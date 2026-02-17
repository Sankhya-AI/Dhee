import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from engram.utils.prompts import FUSION_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class FusedMemory:
    content: str
    strength: float
    access_count: int
    source_ids: List[str]
    layer: str = "lml"


def fuse_memories(memories: List[Dict[str, Any]], llm, custom_prompt: Optional[str] = None) -> Optional[FusedMemory]:
    if not memories:
        return None

    memories_text = "\n\n".join(
        [
            f"Memory {i + 1} (strength={m.get('strength', 1.0):.2f}, accessed={m.get('access_count', 0)}x, created_at={m.get('created_at', '')}):\n{m.get('memory', '')}"
            for i, m in enumerate(memories)
        ]
    )

    prompt = (custom_prompt or FUSION_PROMPT).format(memories_list=memories_text)

    try:
        response = llm.generate(prompt)
        json_start = response.find("{")
        if json_start < 0:
            raise json.JSONDecodeError("No JSON object found", response, 0)
        data, _ = json.JSONDecoder().raw_decode(response, json_start)
        fused_content = data.get("consolidated_memory", "")
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Fusion LLM parsing failed: %s", e)
        fused_content = " | ".join([m.get("memory", "") for m in memories])
    except Exception as e:
        logger.warning("Fusion LLM call failed: %s", e)
        fused_content = " | ".join([m.get("memory", "") for m in memories])

    def _safe_float(val, default: float) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def _safe_int(val, default: int) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    avg_strength = sum(_safe_float(m.get("strength", 1.0), 1.0) for m in memories) / len(memories)
    total_access = sum(_safe_int(m.get("access_count", 0), 0) for m in memories)

    return FusedMemory(
        content=fused_content,
        strength=min(1.0, avg_strength * 1.2),
        access_count=total_access,
        source_ids=[m.get("id", "") for m in memories],
        layer="lml",
    )
