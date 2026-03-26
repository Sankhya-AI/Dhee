"""Proposition-based context builder for memory QA.

Instead of feeding 30K chars of raw conversation to an LLM, this module
builds ~3-8K chars of focused, structured facts with source citations
from episodic events and retrieval results.

This improves answer accuracy for freeform questions by reducing noise
and letting even weak models answer correctly with clean input.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence


def build_proposition_context(
    *,
    events: Sequence[Dict[str, Any]],
    results: Sequence[Dict[str, Any]],
    question: str,
    max_chars: int = 8000,
) -> str:
    """Build LLM context from propositions + evidence snippets.

    Instead of 30K chars of raw conversation, produces ~3-8K chars of
    focused, structured facts with source citations.

    Args:
        events: Episodic events matched to the query.
        results: Search results from the retrieval pipeline.
        question: The user's question (used for relevance hints).
        max_chars: Maximum output length.

    Returns:
        A compact context string ready for LLM consumption.
    """
    lines: List[str] = []
    remaining = max(1, int(max_chars))

    # Section 1: Structured facts from episodic events.
    if events:
        lines.append("Structured Facts:")
        remaining -= len(lines[-1]) + 1
        seen_keys: set = set()
        for event in events:
            value = str(event.get("value_text") or "").strip()
            if not value:
                continue
            # Deduplicate by canonical key.
            ckey = str(event.get("canonical_key") or "").strip().lower()
            if ckey and ckey in seen_keys:
                continue
            if ckey:
                seen_keys.add(ckey)

            session_id = str(event.get("session_id") or "").strip()
            event_time = str(event.get("event_time") or "").strip()
            event_type = str(event.get("event_type") or "fact").strip()
            actor = str(event.get("actor_role") or event.get("actor_id") or "").strip()

            source_parts: List[str] = []
            if session_id:
                source_parts.append(f"Session {session_id}")
            if event_time:
                # Show only date portion for readability.
                date_part = event_time[:10] if len(event_time) >= 10 else event_time
                source_parts.append(date_part)
            source = " | ".join(source_parts) if source_parts else "unknown"

            fact_parts: List[str] = []
            if actor:
                fact_parts.append(actor)
            fact_parts.append(f"({event_type})")
            fact_parts.append(value[:200])
            fact = " ".join(fact_parts)

            line = f"- [{source}] {fact}"
            if len(line) + 1 > remaining:
                break
            lines.append(line)
            remaining -= len(line) + 1

    # Section 2: Evidence snippets from retrieval results.
    evidence_results = list(results)[:5]
    if evidence_results and remaining > 100:
        lines.append("")
        lines.append("Evidence Snippets:")
        remaining -= 20  # header overhead

        for result in evidence_results:
            evidence = str(
                result.get("evidence_text") or result.get("memory") or ""
            ).strip()
            if not evidence:
                continue
            metadata = result.get("metadata") or {}
            session_id = str(metadata.get("session_id") or "").strip()
            session_date = str(metadata.get("session_date") or "").strip()

            header_parts: List[str] = []
            if session_id:
                header_parts.append(f"Session {session_id}")
            if session_date:
                header_parts.append(session_date)
            header = " | ".join(header_parts) if header_parts else "unknown"

            # Truncate evidence to fit budget.
            budget = min(1500, remaining - 20)
            if budget <= 50:
                break
            snippet = evidence[:budget]

            block = f"\n[{header}]\n{snippet}"
            if len(block) + 1 > remaining:
                break
            lines.append(block)
            remaining -= len(block) + 1

    context = "\n".join(lines).strip()
    return context[:max_chars]


def build_proposition_answer_prompt(
    question: str,
    prop_context: str,
    question_date: str = "",
) -> str:
    """Build a simplified answer prompt for proposition-based context.

    When the LLM receives clean, structured facts instead of 30K chars
    of raw conversation, the prompt can be dramatically simpler.
    """
    date_str = question_date or "Not specified"
    return (
        "Answer this question using ONLY the facts below.\n\n"
        f"Question: {question}\n"
        f"Date: {date_str}\n\n"
        f"Facts:\n{prop_context}\n\n"
        "Answer concisely using the exact values from the facts above. "
        "If the answer requires counting items, count the distinct items "
        "listed in the facts. If the facts do not contain enough "
        "information, say so."
    )
