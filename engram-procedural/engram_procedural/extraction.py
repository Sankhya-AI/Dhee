"""Pure functions for procedure extraction and automaticity computation."""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_EXTRACTION_PROMPT = """Analyze these episode memories and extract a reusable step-by-step procedure.

Episodes:
{episodes}

Extract:
1. A clear procedure name
2. Ordered steps (as a JSON list of strings)
3. The domain this procedure applies to
4. Your confidence (0.0-1.0) that this is a valid, reusable procedure

Respond in JSON:
{{"name": "...", "steps": ["step 1", "step 2", ...], "domain": "...", "confidence": 0.8}}"""

_DEFAULT_ABSTRACTION_PROMPT = """Given this domain-specific procedure, create an abstract version that could apply across domains.

Procedure: {name}
Domain: {domain}
Steps:
{steps}

Strip domain-specific details but keep the transferable pattern.
Respond in JSON:
{{"name": "...", "steps": ["step 1", "step 2", ...], "domain": "general"}}"""


def extract_procedure(
    episodes: List[str],
    llm: Any,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """LLM call: find common steps across episode texts.

    Returns: {name, steps: List[str], domain, confidence}
    """
    prompt_template = prompt or _DEFAULT_EXTRACTION_PROMPT
    formatted = prompt_template.format(
        episodes="\n---\n".join(f"Episode {i+1}: {ep}" for i, ep in enumerate(episodes))
    )

    try:
        response = llm.generate(formatted)
        text = response if isinstance(response, str) else str(response)
        # Try to parse JSON from response
        start = text.find("{")
        if start >= 0:
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
            return {
                "name": parsed.get("name", "unnamed_procedure"),
                "steps": parsed.get("steps", []),
                "domain": parsed.get("domain", ""),
                "confidence": float(parsed.get("confidence", 0.5)),
            }
    except Exception as e:
        logger.warning("Procedure extraction failed: %s", e)

    return {"name": "unnamed_procedure", "steps": [], "domain": "", "confidence": 0.0}


def abstract_procedure(
    procedure: Dict[str, Any],
    llm: Any,
) -> Dict[str, Any]:
    """Strip domain-specific details, keep transferable pattern.

    'Debug Python import error' -> 'Debug dependency resolution error'
    """
    steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(procedure.get("steps", [])))
    formatted = _DEFAULT_ABSTRACTION_PROMPT.format(
        name=procedure.get("name", ""),
        domain=procedure.get("domain", ""),
        steps=steps_text,
    )

    try:
        response = llm.generate(formatted)
        text = response if isinstance(response, str) else str(response)
        start = text.find("{")
        if start >= 0:
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
            return {
                "name": parsed.get("name", procedure.get("name", "")),
                "steps": parsed.get("steps", procedure.get("steps", [])),
                "domain": parsed.get("domain", "general"),
            }
    except Exception as e:
        logger.warning("Procedure abstraction failed: %s", e)

    return {
        "name": procedure.get("name", ""),
        "steps": procedure.get("steps", []),
        "domain": "general",
    }


def compute_automaticity(use_count: int, success_rate: float, threshold: int) -> float:
    """Compute automaticity score (0.0-1.0).

    Logarithmic with use_count, weighted by success_rate.
    Formula: min(1.0, log(1 + use_count) / log(1 + threshold)) * success_rate
    """
    if threshold <= 0 or use_count <= 0:
        return 0.0
    raw = math.log(1 + use_count) / math.log(1 + threshold)
    return min(1.0, raw) * min(1.0, max(0.0, success_rate))
