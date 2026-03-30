"""Frozen snapshot generator — renders DheePlugin context as a system prompt.

For agents that don't support tool calling (e.g., simple prompt → completion
workflows, humanoid robot controllers, voice assistants), this module generates
a self-contained system prompt block that includes the full HyperContext.

The "frozen snapshot" pattern (from NousResearch Hermes Agent architecture):
  1. At session start, load HyperContext into the system prompt.
  2. During the session, the system prompt is NEVER mutated — this preserves
     LLM KV-cache / prefix caches for fast inference.
  3. At session end, new knowledge is written to storage for next time.

Usage:
    from dhee import DheePlugin
    from dhee.adapters.system_prompt import generate_snapshot, SnapshotConfig

    plugin = DheePlugin()
    prompt = generate_snapshot(plugin, task="fixing auth bug")

    # Or with custom config:
    config = SnapshotConfig(
        include_memories=True,
        include_heuristics=True,
        max_memories=10,
        include_tool_instructions=True,
    )
    prompt = generate_snapshot(plugin, task="fixing auth bug", config=config)
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SnapshotConfig:
    """Controls what goes into the frozen snapshot."""

    include_performance: bool = True
    include_warnings: bool = True
    include_insights: bool = True
    include_intentions: bool = True
    include_contrasts: bool = True
    include_heuristics: bool = True
    include_memories: bool = True
    include_tool_instructions: bool = False
    include_hive: bool = False

    max_performance: int = 5
    max_warnings: int = 5
    max_insights: int = 5
    max_intentions: int = 5
    max_contrasts: int = 3
    max_heuristics: int = 3
    max_memories: int = 10

    # Prefix/suffix for wrapping the snapshot
    header: str = "## Dhee Cognition Context (Frozen Snapshot)"
    footer: str = ""


# Tool usage instructions (for agents that CAN call tools after loading snapshot)
_TOOL_INSTRUCTIONS = """\
### Available Memory Tools
- **remember(content)** — store a new fact/observation
- **recall(query)** — search memory for relevant facts
- **context(task)** — load full HyperContext (already loaded above)
- **checkpoint(summary, ...)** — save session state and learnings

Use `remember` proactively when you learn new facts. Use `recall` before
answering questions that may depend on stored knowledge. Use `checkpoint`
at natural breakpoints and at session end.
"""


def generate_snapshot(
    plugin: Any,
    task: Optional[str] = None,
    user_id: Optional[str] = None,
    config: Optional[SnapshotConfig] = None,
    hive: Optional[Any] = None,
) -> str:
    """Generate a frozen system prompt snapshot from DheePlugin.

    Args:
        plugin: A DheePlugin instance.
        task: Current task description.
        user_id: User identifier.
        config: Snapshot configuration. Defaults to include everything.
        hive: Optional HiveMemory instance for multi-agent context.

    Returns:
        A complete system prompt block as a string.
    """
    cfg = config or SnapshotConfig()
    ctx = plugin.context(task_description=task, user_id=user_id)

    parts: List[str] = [cfg.header]

    if task:
        parts.append(f"\n**Current task:** {task}")

    # Performance
    if cfg.include_performance:
        perf = ctx.get("performance", [])[:cfg.max_performance]
        if perf:
            parts.append("\n### Performance History")
            for p in perf:
                trend = p.get("trend", 0)
                direction = "improving" if trend > 0 else "declining" if trend < 0 else "stable"
                parts.append(
                    f"- **{p['task_type']}**: avg={p['avg_score']:.2f}, "
                    f"trend={p['trend']:+.3f} ({direction}), "
                    f"attempts={p['total_attempts']}"
                )

    # Warnings
    if cfg.include_warnings:
        warnings = ctx.get("warnings", [])[:cfg.max_warnings]
        if warnings:
            parts.append("\n### Warnings")
            for w in warnings:
                parts.append(f"- {w}")

    # Insights
    if cfg.include_insights:
        insights = ctx.get("insights", [])[:cfg.max_insights]
        if insights:
            parts.append("\n### Insights from Past Work")
            for i in insights:
                parts.append(f"- [{i.get('type', 'general')}] {i['content']}")

    # Intentions (triggered reminders)
    if cfg.include_intentions:
        intentions = ctx.get("intentions", [])[:cfg.max_intentions]
        if intentions:
            parts.append("\n### Triggered Reminders")
            for i in intentions:
                parts.append(f"- {i['description']}")

    # Contrastive evidence
    if cfg.include_contrasts:
        contrasts = ctx.get("contrasts", [])[:cfg.max_contrasts]
        if contrasts:
            parts.append("\n### Contrastive Evidence (Do / Avoid)")
            for c in contrasts:
                do_text = c.get("do", "")[:200]
                avoid_text = c.get("avoid", "")[:200]
                parts.append(f"- **Do:** {do_text}")
                parts.append(f"  **Avoid:** {avoid_text}")
                confidence = c.get("confidence")
                if confidence is not None:
                    parts.append(f"  *confidence: {confidence:.1%}*")

    # Heuristics
    if cfg.include_heuristics:
        heuristics = ctx.get("heuristics", [])[:cfg.max_heuristics]
        if heuristics:
            parts.append("\n### Learned Heuristics")
            for h in heuristics:
                level = h.get("level", "domain")
                text = h.get("heuristic", "")[:250]
                parts.append(f"- [{level}] {text}")

    # Memories
    if cfg.include_memories:
        memories = ctx.get("memories", [])[:cfg.max_memories]
        if memories:
            parts.append("\n### Relevant Memories")
            for m in memories:
                mem_text = m.get("memory", "")[:250]
                score = m.get("score", 0)
                if mem_text:
                    parts.append(f"- {mem_text}")
                    if score > 0:
                        parts.append(f"  *(relevance: {score:.2f})*")

    # Hive context
    if cfg.include_hive and hive:
        try:
            hive_block = hive.get_context_block(limit=3)
            hive_insights = hive_block.get("hive_insights", [])
            hive_heuristics = hive_block.get("hive_heuristics", [])

            if hive_insights or hive_heuristics:
                parts.append("\n### Hive Knowledge (from other agents)")
                for hi in hive_insights:
                    parts.append(
                        f"- [insight from {hi['source']}] "
                        f"{hi['content'].get('content', '')[:150]}"
                    )
                for hh in hive_heuristics:
                    parts.append(
                        f"- [heuristic from {hh['source']}] "
                        f"{hh['content'].get('heuristic', '')[:150]}"
                    )
        except Exception as e:
            logger.debug("Hive context failed: %s", e)

    # Tool instructions
    if cfg.include_tool_instructions:
        parts.append("\n" + _TOOL_INSTRUCTIONS.strip())

    # Meta
    meta = ctx.get("meta", {})
    if meta:
        meta_parts = []
        for key in ["insight_count", "intention_count", "contrast_count", "heuristic_count"]:
            val = meta.get(key, 0)
            if val > 0:
                meta_parts.append(f"{key.replace('_count', '')}s: {val}")
        if meta_parts:
            parts.append(f"\n*Loaded: {', '.join(meta_parts)}*")

    if cfg.footer:
        parts.append(f"\n{cfg.footer}")

    return "\n".join(parts)


def generate_minimal_snapshot(
    plugin: Any,
    task: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """Generate a minimal snapshot — just warnings, intentions, and top memories.

    Suitable for edge/embedded agents with tight context budgets.
    """
    cfg = SnapshotConfig(
        include_performance=False,
        include_insights=False,
        include_contrasts=False,
        include_heuristics=False,
        max_warnings=3,
        max_intentions=3,
        max_memories=3,
        header="## Dhee Context (Minimal)",
    )
    return generate_snapshot(plugin, task=task, user_id=user_id, config=cfg)
