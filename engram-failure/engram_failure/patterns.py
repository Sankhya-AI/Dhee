"""Pure functions for failure pattern extraction."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_ANTIPATTERN_PROMPT = """Analyze these failure episodes and extract a reusable anti-pattern (something NOT to do).

Failures:
{failures}

Extract:
1. A clear anti-pattern name (what to avoid)
2. Description of why this fails
3. Warning signs to watch for
4. Suggested alternative approach
5. Confidence (0.0-1.0)

Respond in JSON:
{{"name": "...", "description": "...", "warning_signs": ["..."], "alternative": "...", "confidence": 0.8}}"""

_DEFAULT_RECOVERY_PROMPT = """Analyze this failure and its resolution to extract a recovery strategy.

Failure: {failure}
Resolution: {resolution}

Extract a reusable recovery strategy in JSON:
{{"name": "...", "steps": ["step 1", "step 2", ...], "applicable_when": "...", "confidence": 0.8}}"""


def extract_antipattern(
    failures: List[str],
    llm: Any,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract an anti-pattern from similar failures."""
    prompt_template = prompt or _DEFAULT_ANTIPATTERN_PROMPT
    formatted = prompt_template.format(
        failures="\n---\n".join(f"Failure {i+1}: {f}" for i, f in enumerate(failures))
    )

    try:
        response = llm.generate(formatted)
        text = response if isinstance(response, str) else str(response)
        start = text.find("{")
        if start >= 0:
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
            return {
                "name": parsed.get("name", "unnamed_antipattern"),
                "description": parsed.get("description", ""),
                "warning_signs": parsed.get("warning_signs", []),
                "alternative": parsed.get("alternative", ""),
                "confidence": float(parsed.get("confidence", 0.5)),
            }
    except Exception as e:
        logger.warning("Anti-pattern extraction failed: %s", e)

    return {
        "name": "unnamed_antipattern",
        "description": "",
        "warning_signs": [],
        "alternative": "",
        "confidence": 0.0,
    }


def extract_recovery_strategy(
    failure: str,
    resolution: str,
    llm: Any,
) -> Dict[str, Any]:
    """Extract a recovery strategy from a failure+resolution pair."""
    formatted = _DEFAULT_RECOVERY_PROMPT.format(
        failure=failure, resolution=resolution
    )

    try:
        response = llm.generate(formatted)
        text = response if isinstance(response, str) else str(response)
        start = text.find("{")
        if start >= 0:
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
            return {
                "name": parsed.get("name", "unnamed_recovery"),
                "steps": parsed.get("steps", []),
                "applicable_when": parsed.get("applicable_when", ""),
                "confidence": float(parsed.get("confidence", 0.5)),
            }
    except Exception as e:
        logger.warning("Recovery strategy extraction failed: %s", e)

    return {
        "name": "unnamed_recovery",
        "steps": [],
        "applicable_when": "",
        "confidence": 0.0,
    }
