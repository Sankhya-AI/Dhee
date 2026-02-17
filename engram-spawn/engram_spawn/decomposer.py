"""LLM-based task decomposition."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

STRATEGIES = {
    "auto": "LLM decides how to break down the task",
    "sequential": "Sub-tasks must be done in order (each blocks the next)",
    "parallel": "All sub-tasks can be done concurrently",
    "phased": "Group into phases — each phase parallel, phases sequential",
}

_DECOMPOSE_PROMPT = """Break this task into {max_subtasks} or fewer sub-tasks.

Task: {title}
{description}

Strategy: {strategy}

Return a JSON array of sub-task objects with these fields:
- "title": short title (imperative form)
- "description": what needs to be done
- "tags": relevant skill tags
- "phase": phase number (1-based, for sequential/phased ordering)

Return ONLY valid JSON, no markdown or explanation.
"""


def decompose_with_llm(llm: Any, task: dict, *,
                       strategy: str = "auto",
                       max_subtasks: int = 5) -> list[dict]:
    """Use an LLM to decompose a task into sub-tasks.

    Args:
        llm: An LLM instance with a generate(prompt) method.
        task: Task dict with title, description, tags.
        strategy: Decomposition strategy (auto, sequential, parallel, phased).
        max_subtasks: Maximum number of sub-tasks.

    Returns:
        List of sub-task dicts with title, description, tags, phase.
    """
    title = task.get("title", "")
    description = task.get("description", "")

    if strategy not in STRATEGIES:
        strategy = "auto"

    strategy_desc = STRATEGIES[strategy]
    prompt = _DECOMPOSE_PROMPT.format(
        max_subtasks=max_subtasks,
        title=title,
        description=f"Description: {description}" if description else "",
        strategy=f"{strategy} — {strategy_desc}",
    )

    try:
        response = llm.generate(prompt)
        # Parse JSON from response
        subtasks = _parse_subtasks(response, max_subtasks)
        return subtasks
    except Exception as e:
        logger.error("LLM decomposition failed: %s", e)
        # Fallback: single sub-task = the original task
        return [{"title": title, "description": description, "tags": [], "phase": 1}]


def _parse_subtasks(response: str, max_subtasks: int) -> list[dict]:
    """Parse LLM response into sub-task list."""
    # Try to find JSON array in response
    text = response.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data[:max_subtasks]
        if isinstance(data, dict) and "subtasks" in data:
            return data["subtasks"][:max_subtasks]
    except json.JSONDecodeError:
        pass

    # Try to extract JSON array from text using raw_decode to ignore trailing text
    start = text.find("[")
    if start != -1:
        try:
            data, _ = json.JSONDecoder().raw_decode(text, start)
            if isinstance(data, list):
                return data[:max_subtasks]
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse subtasks from LLM response")
    return []
