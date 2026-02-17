"""Pure functions for reconsolidation eligibility and update proposal."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PROPOSAL_PROMPT = """You are evaluating whether a memory should be updated based on new context.

Current memory content:
{memory_content}

New context:
{new_context}

Evaluate:
1. Does the new context refine, correct, or elaborate on the memory?
2. What is the proposed updated content?
3. What type of change is this? (refine/correct/elaborate/no_change)
4. How confident are you (0.0-1.0)?

Respond in JSON:
{{"proposed_content": "...", "confidence": 0.8, "reasoning": "...", "change_type": "refine"}}"""


def should_reconsolidate(
    memory: Dict[str, Any],
    current_context: str,
    config: Any,
) -> bool:
    """Check if a memory is eligible for reconsolidation.

    Checks cooldown period, staleness, and basic relevance.
    """
    md = memory.get("metadata", {}) or {}

    # Check cooldown
    last_rc = md.get("rc_last_reconsolidated_at", "")
    if last_rc and config is not None:
        try:
            last_dt = datetime.fromisoformat(last_rc.replace("Z", "+00:00"))
            elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            cooldown = getattr(config, "cooldown_hours", 1.0)
            if elapsed_hours < cooldown:
                return False
        except (ValueError, TypeError):
            pass

    # Skip if no content to compare
    content = memory.get("memory", "")
    if not content or not current_context:
        return False

    # Basic relevance: at least some word overlap
    content_words = set(content.lower().split())
    context_words = set(current_context.lower().split())
    overlap = content_words & context_words
    # Need at least 2 meaningful words in common (skip very short words)
    meaningful_overlap = {w for w in overlap if len(w) > 3}
    if len(meaningful_overlap) < 2:
        return False

    return True


def propose_update(
    memory_content: str,
    new_context: str,
    llm: Any,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """LLM proposes refined content for a memory.

    Returns: {proposed_content, confidence, reasoning, change_type}
    change_type: 'refine', 'correct', 'elaborate', 'no_change'
    """
    prompt_template = prompt or _DEFAULT_PROPOSAL_PROMPT
    formatted = prompt_template.format(
        memory_content=memory_content,
        new_context=new_context,
    )

    try:
        response = llm.generate(formatted)
        text = response if isinstance(response, str) else str(response)
        start = text.find("{")
        if start >= 0:
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
            return {
                "proposed_content": parsed.get("proposed_content", memory_content),
                "confidence": float(parsed.get("confidence", 0.5)),
                "reasoning": parsed.get("reasoning", ""),
                "change_type": parsed.get("change_type", "no_change"),
            }
    except Exception as e:
        logger.warning("Reconsolidation proposal failed: %s", e)

    return {
        "proposed_content": memory_content,
        "confidence": 0.0,
        "reasoning": "LLM evaluation failed",
        "change_type": "no_change",
    }
