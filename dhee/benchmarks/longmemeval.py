"""LongMemEval runner for Engram

Usage:
    python -m engram.benchmarks.longmemeval --dataset-path ... --output-jsonl ...
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dhee import FullMemory as Memory
from dhee.configs.base import (
    BatchConfig,
    CategoryMemConfig,
    EchoMemConfig,
    EmbedderConfig,
    EngramExtractionConfig,
    EnrichmentConfig,
    KnowledgeGraphConfig,
    LLMConfig,
    MemoryConfig,
    OrchestrationConfig,
    ProfileConfig,
    RerankConfig,
    SceneConfig,
    CostGuardrailConfig,
    VectorStoreConfig,
)
from dhee.core.answer_orchestration import (
    is_low_confidence_answer,
)

logger = logging.getLogger(__name__)

# ---- Training dataset export ----


def _export_training_data(
    memory: Any,
    question_id: str,
    question: str,
    question_type: str,
    question_date: str,
    gold_answer: str,
    hypothesis: str,
    intent: str,
    context: str,
    results: List[Dict[str, Any]],
    orchestration_meta: Dict[str, Any],
    session_items: List[Dict[str, Any]],
    output_dir: str,
    user_id: str,
) -> None:
    """Export structured training tuples BEFORE memory.delete_all() wipes the DB.

    Saves per-task-type JSONL files that become DheeModel fine-tuning data:
      - engram_extraction.jsonl: session text → extracted facts/entities/profiles
      - query_classification.jsonl: question → intent + search params
      - answer_synthesis.jsonl: question + facts + context → gold answer
      - context_anchoring.jsonl: memory → context anchors (era/place/time/activity)
    """
    os.makedirs(output_dir, exist_ok=True)

    # 1. [ENGRAM] extraction pairs: stored memories with their enrichment
    try:
        all_memories = memory.get_all(user_id=user_id, limit=500).get("results", [])
        if all_memories:
            engram_path = os.path.join(output_dir, "engram_extraction.jsonl")
            with open(engram_path, "a", encoding="utf-8") as f:
                for mem in all_memories:
                    row = {
                        "question_id": question_id,
                        "memory_id": mem.get("id", ""),
                        "input_text": mem.get("memory", ""),
                        "metadata": mem.get("metadata", {}),
                        "categories": mem.get("categories", []),
                        "hash": mem.get("hash", ""),
                    }
                    # Extract enrichment data from DB if available
                    mid = mem.get("id", "")
                    if mid and hasattr(memory, "db"):
                        db = memory.db
                        # Facts
                        try:
                            facts = db.conn.execute(
                                "SELECT subject, predicate, value, value_numeric, value_unit, "
                                "time, valid_from, valid_until, qualifier, canonical_key, "
                                "confidence, is_derived FROM engram_facts WHERE memory_id = ?",
                                (mid,),
                            ).fetchall()
                            if facts:
                                row["facts"] = [
                                    dict(zip(
                                        ["subject", "predicate", "value", "value_numeric",
                                         "value_unit", "time", "valid_from", "valid_until",
                                         "qualifier", "canonical_key", "confidence", "is_derived"],
                                        f,
                                    ))
                                    for f in facts
                                ]
                        except Exception:
                            pass
                        # Context anchors
                        try:
                            ctx = db.conn.execute(
                                "SELECT era, place, place_type, place_detail, time_absolute, "
                                "time_markers, time_range_start, time_range_end, time_derivation, "
                                "activity, session_id FROM engram_context WHERE memory_id = ?",
                                (mid,),
                            ).fetchone()
                            if ctx:
                                row["context_anchor"] = dict(zip(
                                    ["era", "place", "place_type", "place_detail",
                                     "time_absolute", "time_markers", "time_range_start",
                                     "time_range_end", "time_derivation", "activity",
                                     "session_id"],
                                    ctx,
                                ))
                        except Exception:
                            pass
                        # Entities
                        try:
                            ents = db.conn.execute(
                                "SELECT name, entity_type, state, relationships "
                                "FROM engram_entities WHERE memory_id = ?",
                                (mid,),
                            ).fetchall()
                            if ents:
                                row["entities"] = [
                                    dict(zip(["name", "entity_type", "state", "relationships"], e))
                                    for e in ents
                                ]
                        except Exception:
                            pass
                        # Profiles
                        try:
                            profiles = db.conn.execute(
                                "SELECT fact_key, fact_value, category, source_memory_id "
                                "FROM profiles WHERE source_memory_id = ?",
                                (mid,),
                            ).fetchall()
                            if profiles:
                                row["profiles"] = [
                                    dict(zip(["fact_key", "fact_value", "category", "source_memory_id"], p))
                                    for p in profiles
                                ]
                        except Exception:
                            pass
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Training data export (engram): %s", e)

    # 2. [QUERY] classification pairs: question → intent + orchestration params
    try:
        query_path = os.path.join(output_dir, "query_classification.jsonl")
        with open(query_path, "a", encoding="utf-8") as f:
            row = {
                "question_id": question_id,
                "question": question,
                "question_type": question_type,
                "question_date": question_date,
                "classified_intent": intent,
                "orchestration": {
                    k: v for k, v in orchestration_meta.items()
                    if k in ("mode", "intent", "search_limit", "context_limit",
                             "map_reduce_used", "rewritten_query", "reason_codes")
                },
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Training data export (query): %s", e)

    # 3. [ANSWER] synthesis pairs: question + structured facts + context → gold answer
    try:
        answer_path = os.path.join(output_dir, "answer_synthesis.jsonl")
        with open(answer_path, "a", encoding="utf-8") as f:
            # Collect all facts from the DB for this question's memories
            all_facts: List[Dict] = []
            if hasattr(memory, "db"):
                try:
                    facts_rows = memory.db.conn.execute(
                        "SELECT subject, predicate, value, canonical_key, time, qualifier "
                        "FROM engram_facts WHERE memory_id IN "
                        "(SELECT id FROM memories WHERE user_id = ?)",
                        (user_id,),
                    ).fetchall()
                    all_facts = [
                        dict(zip(["subject", "predicate", "value", "canonical_key", "time", "qualifier"], r))
                        for r in facts_rows
                    ]
                except Exception:
                    pass
            row = {
                "question_id": question_id,
                "question": question,
                "question_type": question_type,
                "question_date": question_date,
                "gold_answer": gold_answer,
                "system_answer": hypothesis,
                "intent": intent,
                "context_text": context[:8000] if context else "",
                "structured_facts": all_facts[:200],
                "reduced_answer": orchestration_meta.get("reduced_answer"),
                "num_results": len(results),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Training data export (answer): %s", e)

    # 4. [CONTEXT] anchoring pairs: session texts → context metadata
    try:
        context_path = os.path.join(output_dir, "context_anchoring.jsonl")
        with open(context_path, "a", encoding="utf-8") as f:
            for item in session_items:
                row = {
                    "question_id": question_id,
                    "session_id": item.get("metadata", {}).get("session_id", ""),
                    "session_date": item.get("metadata", {}).get("session_date", ""),
                    "session_text": item.get("content", "")[:4000],
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Training data export (context): %s", e)


# ---- Answer grounding helpers ----

_IDK_RE = re.compile(
    r"\b(i\s+don[''\u2019]?t\s+know|no\s+(relevant|specific)\s+(information|mention)"
    r"|not\s+(mentioned|found|stated|provided|discussed)"
    r"|does\s+not\s+(appear|mention|discuss)|cannot\s+determine"
    r"|did\s+not\s+mention"
    r"|insufficient\s+information)\b",
    re.I,
)


def _is_refusal(answer: str) -> bool:
    """Return True if the answer is effectively 'I don't know'."""
    a = str(answer or "").strip()
    if not a:
        return True
    return bool(_IDK_RE.search(a))


def _extract_key_numbers(text: str) -> set:
    """Extract all numbers from text (integers and decimals)."""
    return set(re.findall(r"\b\d+(?:\.\d+)?\b", str(text or "")))


def _answer_grounded_in_context(hypothesis: str, context: str) -> bool:
    """Check if numbers/quantities in the hypothesis exist in the context.

    For factual extraction questions, the answer should reference
    values that are explicitly present in the retrieved context.
    Returns True if grounded or if no numbers to verify.
    """
    # Normalize word numbers to digits in both hypothesis and context
    h_norm = _word_numbers_to_digits(hypothesis)
    c_norm = _word_numbers_to_digits(context)
    h_numbers = _extract_key_numbers(h_norm)
    if not h_numbers:
        return True  # No numbers to check — assume grounded
    c_numbers = _extract_key_numbers(c_norm)
    # At least one number in the answer should appear in context
    return bool(h_numbers & c_numbers)

SESSION_ID_PATTERN = re.compile(r"^Session ID:\s*(?P<session_id>\S+)\s*$", re.MULTILINE)
HISTORY_HEADER = "User Transcript:"
DEFAULT_NVIDIA_LLM_MODEL = "meta/llama-3.1-8b-instruct"
DEFAULT_NVIDIA_EMBEDDER_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"
DEFAULT_NVIDIA_RERANK_MODEL = "nvidia/llama-nemotron-rerank-vl-1b-v2"


def extract_user_only_text(session_turns: Sequence[Dict[str, Any]]) -> str:
    """Convert one LongMemEval session into newline-separated user text."""
    lines = [str(turn.get("content", "")).strip() for turn in session_turns if turn.get("role") == "user"]
    return "\n".join([line for line in lines if line])


def format_session_memory(session_id: str, session_date: str, session_turns: Sequence[Dict[str, Any]], include_all_roles: bool = False) -> str:
    """Create a memory payload that preserves session metadata in plain text.

    When include_all_roles=True, includes both user and assistant turns
    for richer context in deferred enrichment mode.
    """
    if include_all_roles:
        all_text = []
        for turn in session_turns:
            role = turn.get("role", "user")
            content = str(turn.get("content", "")).strip()
            if content:
                all_text.append(f"{role}: {content}")
        full_text = "\n".join(all_text)
    else:
        full_text = extract_user_only_text(session_turns)
    return (
        f"Session ID: {session_id}\n"
        f"Session Date: {session_date}\n"
        f"{HISTORY_HEADER}\n"
        f"{full_text}"
    )


def parse_session_id_from_result(result: Dict[str, Any]) -> Optional[str]:
    """Extract session_id from memory metadata or fallback text header."""
    metadata = result.get("metadata") or {}
    sid = metadata.get("session_id")
    if sid:
        return str(sid)
    memory_text = str(result.get("memory", "") or "")
    match = SESSION_ID_PATTERN.search(memory_text)
    if match:
        return match.group("session_id")
    return None


def dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def compute_session_metrics(retrieved_session_ids: Sequence[str], answer_session_ids: Sequence[str]) -> Dict[str, float]:
    """Compute simple retrieval metrics over session IDs."""
    retrieved = dedupe_preserve_order([str(x) for x in retrieved_session_ids if str(x).strip()])
    gold = {str(x) for x in answer_session_ids if str(x).strip()}

    metrics: Dict[str, float] = {}
    for k in (1, 3, 5, 10):
        top_k = set(retrieved[:k])
        metrics[f"recall_any@{k}"] = 1.0 if gold and bool(top_k & gold) else 0.0
        metrics[f"recall_all@{k}"] = 1.0 if gold and gold.issubset(top_k) else 0.0
    return metrics


def _coerce_finite_float(value: Any) -> Optional[float]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def _top1_session_id_by_score(results: Sequence[Dict[str, Any]], score_key: str) -> Optional[str]:
    best_result: Optional[Dict[str, Any]] = None
    best_score = float("-inf")
    for result in results:
        score = _coerce_finite_float(result.get(score_key))
        if score is None:
            continue
        if score > best_score:
            best_score = score
            best_result = result
    if best_result is None:
        return None
    return parse_session_id_from_result(best_result)


def _rank_gold_by_score(
    results: Sequence[Dict[str, Any]],
    gold_session_ids: Sequence[str],
    score_key: str,
    require_finite_score: bool = False,
) -> Optional[int]:
    gold = {str(x) for x in gold_session_ids if str(x).strip()}
    if not gold:
        return None

    scored_rows: List[Tuple[float, str]] = []
    for result in results:
        session_id = parse_session_id_from_result(result)
        if not session_id:
            continue
        score = _coerce_finite_float(result.get(score_key))
        if score is None:
            if require_finite_score:
                continue
            score = float("-inf")
        scored_rows.append((score, session_id))

    if not scored_rows:
        return None

    scored_rows.sort(key=lambda item: item[0], reverse=True)
    for rank, (_, session_id) in enumerate(scored_rows, start=1):
        if session_id in gold:
            return rank
    return None


def _truncate_text(text: str, max_chars: int) -> str:
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = 3500
    limit = max(1, limit)
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


# ---------------------------------------------------------------------------
# Keyword-context evidence extraction (Option 1: smarter evidence, 0 cost)
# ---------------------------------------------------------------------------

_KEYWORD_STOPWORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during',
    'before', 'after', 'above', 'below', 'between', 'out', 'off', 'over',
    'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when',
    'where', 'why', 'how', 'all', 'each', 'every', 'both', 'few', 'more',
    'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own',
    'same', 'so', 'than', 'too', 'very', 'just', 'because', 'but', 'and',
    'or', 'if', 'while', 'what', 'which', 'who', 'whom', 'this', 'that',
    'these', 'those', 'i', 'me', 'my', 'myself', 'we', 'our', 'ours',
    'you', 'your', 'yours', 'he', 'him', 'his', 'she', 'her', 'hers',
    'it', 'its', 'they', 'them', 'their', 'theirs', 'about', 'up', 'down',
    'many', 'much', 'also', 'any', 'tell', 'know', 'am',
})


def extract_keyword_context(
    text: str,
    query: str,
    context_chars: int = 800,
    max_excerpts: int = 5,
) -> str:
    """Extract text windows around query keyword matches.

    Finds meaningful keywords from the query, locates them in the session text,
    and returns large context windows (before + after) around each match.
    Overlapping windows are merged into contiguous blocks.
    """
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if w not in _KEYWORD_STOPWORDS and len(w) > 2]

    if not keywords:
        return _truncate_text(text, context_chars * 2)

    text_lower = text.lower()
    positions: List[int] = []
    for kw in keywords:
        for match in re.finditer(r'\b' + re.escape(kw) + r'\b', text_lower):
            positions.append(match.start())

    if not positions:
        return _truncate_text(text, context_chars * 2)

    positions.sort()

    # Build context windows and merge overlapping ones
    windows: List[Tuple[int, int]] = []
    for pos in positions:
        start = max(0, pos - context_chars)
        end = min(len(text), pos + context_chars)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))

    # Keep the largest windows, then re-sort by position
    windows = sorted(windows, key=lambda w: w[1] - w[0], reverse=True)[:max_excerpts]
    windows.sort(key=lambda w: w[0])

    excerpts: List[str] = []
    for start, end in windows:
        excerpt = text[start:end].strip()
        if not excerpt:
            continue
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""
        excerpts.append(f"{prefix}{excerpt}{suffix}")

    return "\n---\n".join(excerpts) if excerpts else _truncate_text(text, context_chars * 2)


def _normalize_question_type(question_type: str) -> str:
    return str(question_type or "").strip().lower()


def _question_type_instructions(question_type: str) -> str:
    qtype = _normalize_question_type(question_type)

    if "multi-session" in qtype:
        return (
            "- Multi-session: relevant evidence can be spread across multiple candidates.\n"
            "- Read ALL candidates before finalizing the answer.\n"
            "- For counting/summing: enumerate each unique item as a numbered list (1. item from Session X, 2. item from Session Y, ...). Then count the list. Do NOT count the same item/event twice even if mentioned in multiple sessions.\n"
            "- After listing, state: 'Total unique items: N' where N is the count of your numbered list.\n"
        )

    if "temporal" in qtype:
        return (
            "- Temporal reasoning: use session dates in candidate headers for chronology/date math.\n"
            "- Convert relative time references using the provided timeline evidence.\n"
            "- For day/week/month difference questions, return the computed final duration only.\n"
        )

    if "knowledge-update" in qtype or "update" in qtype or "changing" in qtype:
        return (
            "- Knowledge update: if old and updated values coexist, prioritize the latest updated value.\n"
            "- If both values appear in your answer, the final answer must clearly include the updated value.\n"
        )

    if "preference" in qtype:
        return (
            "- Preference question: the user is asking for a recommendation based on their stated preferences.\n"
            "- DO NOT refuse or say 'you did not mention'. Instead, use what the user HAS told you about their interests, brands, styles, and past choices to describe what they would prefer.\n"
            "- Answer format: describe the user's relevant preferences and what kind of suggestion would suit them (2-3 sentences).\n"
            "- Example: if user mentioned using Sony cameras, and asks for accessory suggestions, answer: 'Based on your Sony camera, you would prefer Sony-compatible accessories.'\n"
        )

    if "single-session-user" in qtype or "single-session-assistant" in qtype:
        return (
            "- Single-session fact: locate the exact supporting detail and answer directly.\n"
            "- Return only the requested fact.\n"
        )

    return "- Find direct supporting evidence and answer precisely.\n"


def build_answer_prompt(
    question: str,
    retrieved_context: str,
    question_date: str = "",
    question_type: str = "",
) -> str:
    qtype = _normalize_question_type(question_type)
    date_str = question_date or "Not specified"
    type_rules = _question_type_instructions(qtype)

    return (
        "You are a question-answering system. Based on the retrieved conversation history below, answer the question.\n\n"
        f"Question: {question}\n"
        f"Question Date: {date_str}\n\n"
        "Retrieved Context:\n"
        f"{retrieved_context}\n\n"
        "Instructions:\n"
        "- Base your answer ONLY on the provided context. Every fact in your answer must be directly traceable to text in the conversations above.\n"
        "- If the answer spans multiple conversations, synthesize information from ALL of them.\n"
        "- For counting/aggregation questions: first list each unique item found across ALL conversations as a numbered list (1., 2., 3., ...), noting which session it came from. Skip duplicates. Then count the numbered list to give the total.\n"
        "- For temporal questions: use session dates to calculate time differences.\n"
        "- For preference/recommendation questions: describe user preferences including specific products/brands mentioned (2-3 sentences).\n"
        "- If evidence conflicts, prefer the most recent dated evidence.\n"
        "- Answer even if the match is indirect (e.g. a store name IS a brand, a mentioned product IS the answer even if not called a 'brand').\n"
        "- If the exact thing asked about is NOT in the context but a closely related topic IS mentioned, say: 'You did not mention this information. You mentioned [related thing] but not [asked thing].'\n"
        "- Only say 'I don't know' if the context contains absolutely zero relevant information about what is asked.\n"
        "- When the user refers to relative time ('last weekend', 'recently'), match it to the closest relevant event mentioned in the context rather than rejecting based on exact date arithmetic.\n"
        f"{type_rules}\n"
        "Think step by step, then provide your answer.\n\n"
        "Reasoning:\n[Identify which conversations contain relevant information and quote the exact supporting text]\n\n"
        "Answer:\n[Your concise final answer — use the exact words/numbers from the context]"
    )


def _parse_answer_section(text: str) -> str:
    """Extract the final answer from a chain-of-thought response.

    Handles multiple reasoning model formats:
    1. <think>...</think> tags (qwq-32b, DeepSeek with thinking mode)
    2. "Answer:" line prefix (standard CoT format)
    3. Inline reasoning starting with "Okay," / "Let me" / "Let's" etc.
    """
    if not text:
        return ""

    # Step 1: Strip reasoning blocks from thinking models (qwq-32b, DeepSeek, etc.)
    import re
    # Case A: Full <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if cleaned and cleaned != text.strip():
        text = cleaned
    # Case B: Missing opening <think> — API strips it but keeps </think>
    elif "</think>" in text:
        after_think = text.split("</think>", 1)[1].strip()
        if after_think:
            text = after_think

    lines = text.splitlines()

    # Step 2: Search backwards for a line starting with "Answer:"
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line.lower().startswith("answer:"):
            content = line[len("answer:"):].strip()
            if i + 1 < len(lines):
                remaining = "\n".join(lines[i + 1:]).strip()
                if remaining:
                    content = content + "\n" + remaining if content else remaining
            return content.strip()

    # Step 3: If text starts with common reasoning prefixes and is long,
    # try to extract the final conclusion (last paragraph after a blank line)
    first_line = lines[0].strip().lower() if lines else ""
    _REASONING_PREFIXES = (
        "okay,", "okay ", "let me", "let's", "first,", "first ", "so,", "so ",
        "to answer", "looking at", "based on", "i need to", "the user",
        "alright,", "alright ", "now,", "now ", "hmm",
    )
    if len(text) > 300 and any(first_line.startswith(p) for p in _REASONING_PREFIXES):
        # Find last non-empty paragraph
        paragraphs = re.split(r"\n\s*\n", text)
        if len(paragraphs) > 1:
            last = paragraphs[-1].strip()
            # Only use last paragraph if it's short enough to be an answer
            if last and len(last) < 500:
                return last

    return text.strip()


def _is_count_style_question(question: str) -> bool:
    """Check if a question needs count refinement (item enumeration).

    Only returns True for questions that COUNT DISTINCT ITEMS.
    Returns False for questions asking for numeric VALUES, durations, or money sums.
    """
    from dhee.core.answer_orchestration import classify_answer_intent, AnswerIntent
    intent = classify_answer_intent(question)
    return intent == AnswerIntent.COUNT


def _normalize_number_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parse_text = text.replace(",", "")
    if parse_text.startswith("$"):
        parse_text = parse_text[1:]
    try:
        num = float(parse_text)
    except (TypeError, ValueError):
        return text
    if not math.isfinite(num):
        return None
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num}".rstrip("0").rstrip(".")


def _extract_number_matches(text: str) -> List[re.Match]:
    matches = list(
        re.finditer(
            r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![A-Za-z0-9])",
            text,
        )
    )
    if matches:
        return matches
    return list(re.finditer(r"(?<![A-Za-z0-9])[-+]?\d*\.?\d+(?![A-Za-z0-9])", text))


def _is_probable_list_index(text: str, match: re.Match) -> bool:
    start, end = match.span()
    next_char = text[end:end + 1]
    if next_char not in {".", ")", ":"}:
        return False
    if next_char == "." and end < len(text) - 1 and text[end + 1].isdigit():
        # Decimal number (e.g. 1.5), not a list index.
        return False
    if end < len(text) - 1 and not text[end + 1].isspace():
        return False
    if start == 0:
        return True
    prev_char = text[start - 1]
    return prev_char in {"\n", " ", "\t", "-", "*"}


def _window_has_total_marker(text_lower: str, start: int, end: int, radius: int = 24) -> bool:
    window = text_lower[max(0, start - radius): min(len(text_lower), end + radius)]
    return any(marker in window for marker in ("total", "in total", "altogether", "overall", "final"))


def _extract_how_many_keywords(question: str) -> List[str]:
    q_lower = str(question or "").lower()
    match = re.search(r"\bhow many\s+(.+)", q_lower)
    if not match:
        return []
    fragment = match.group(1).split("?", 1)[0]
    fragment = re.split(
        r"\b(do|did|does|have|has|had|are|is|was|were|can|could|will|would|should)\b",
        fragment,
        maxsplit=1,
    )[0]
    words = re.findall(r"[a-z]+", fragment)
    stopwords = {
        "the", "a", "an", "my", "our", "your", "their", "his", "her", "its",
        "i", "we", "you", "they", "he", "she", "it", "in", "on", "for", "to",
        "of", "with", "at", "from", "by", "or", "and", "last", "past", "current",
    }
    return [w for w in words if len(w) > 2 and w not in stopwords]


_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_SIMPLE_WORDS = {
    "a dozen": "12", "half a": "0.5", "a half": "0.5",
    "half": "0.5", "hundred": "100",
}


def _word_numbers_to_digits(text: str) -> str:
    """Replace word-form numbers (two, twenty-three...) with digits."""
    result = text
    # Handle simple phrases first (longest first)
    for word, digit in sorted(_SIMPLE_WORDS.items(), key=lambda x: -len(x[0])):
        result = re.sub(rf"\b{re.escape(word)}\b", digit, result, flags=re.I)

    # Handle compound numbers: "twenty-three", "twenty three", "fifty-one"
    tens_pattern = "|".join(_TENS.keys())
    ones_pattern = "|".join(k for k in _ONES if _ONES[k] >= 1 and _ONES[k] <= 9)

    def _compound_replace(m: re.Match) -> str:
        t = m.group(1).lower()
        o = m.group(2).lower()
        return str(_TENS[t] + _ONES[o])

    result = re.sub(
        rf"\b({tens_pattern})[\s-]({ones_pattern})\b",
        _compound_replace, result, flags=re.I,
    )

    # Handle standalone tens and ones (longest first to avoid partial matches)
    all_words = {**{k: str(v) for k, v in _TENS.items()}, **{k: str(v) for k, v in _ONES.items()}}
    for word, digit in sorted(all_words.items(), key=lambda x: -len(x[0])):
        result = re.sub(rf"\b{re.escape(word)}\b", digit, result, flags=re.I)
    return result


def _canonicalize_count_answer(answer: str, question: str) -> str:
    text = str(answer or "").strip()
    if not text or not _is_count_style_question(question):
        return text
    # Convert word numbers to digits before extraction
    text_with_digits = _word_numbers_to_digits(text)
    matches = _extract_number_matches(text_with_digits)
    if not matches:
        return text
    q_lower = str(question or "").lower()
    text_lower = text_with_digits.lower()
    use_last = bool(re.search(r"[=+\-*/]", text)) and len(matches) > 1

    # Strict money detection: only dollar/money keywords, NOT event-type words.
    # "fundraise", "charity", "raised" can appear in counting questions
    # (e.g. "how many charity events") and must NOT trigger $ formatting.
    money_question = bool(
        re.search(
            r"\b(how much money|money|dollars?|usd)\b",
            q_lower,
        )
    )
    # Ambiguous words (charity, fundraise, raised, spent, cost) require a
    # money-verb gate — same two-gate pattern as classify_answer_intent.
    if not money_question and re.search(r"\b(charity|fundrais\w*|raised?|spent?|cost)\b", q_lower):
        money_question = bool(
            re.search(r"\b(how much|total|sum|amount)\b", q_lower)
        )

    duration_question = (
        "how long" in q_lower
        or bool(re.search(r"\b(duration|difference|elapsed|ago)\b", q_lower))
        or bool(re.search(r"\bhow many\s+(days?|weeks?|months?|years?|hours?|minutes?)\b", q_lower))
    )

    non_list_matches = [m for m in matches if not _is_probable_list_index(text_with_digits, m)]
    candidate_matches = non_list_matches or matches
    chosen: Optional[re.Match] = None

    if "how many" in q_lower and not non_list_matches and len(matches) > 1:
        # If the model returned an enumerated list (1., 2., 3., ...), use the max index.
        try:
            chosen = max(matches, key=lambda m: float((m.group(0) or "0").replace(",", "")))
        except ValueError:
            chosen = matches[-1]

    if chosen is None:
        total_matches = []
        for m in candidate_matches:
            s, e = m.span()
            if _window_has_total_marker(text_lower, s, e):
                total_matches.append(m)
        if total_matches:
            chosen = total_matches[-1]

    if chosen is None and duration_question:
        for m in candidate_matches:
            s, e = m.span()
            window = text_lower[max(0, s - 20): min(len(text_lower), e + 20)]
            if re.search(r"\b(days?|weeks?|months?|years?|hours?|minutes?)\b", window):
                chosen = m
                break

    if chosen is None and money_question:
        currency_matches = []
        for m in candidate_matches:
            s, e = m.span()
            around = text[max(0, s - 2): min(len(text), e + 2)]
            if "$" in around:
                currency_matches.append(m)
        if currency_matches:
            chosen = currency_matches[-1] if use_last else currency_matches[0]

    if chosen is None and "how many" in q_lower:
        keywords = _extract_how_many_keywords(question)
        if keywords:
            for m in candidate_matches:
                s, e = m.span()
                window = text_lower[max(0, s - 48): min(len(text_lower), e + 96)]
                if any(kw in window for kw in keywords):
                    chosen = m
                    break

    if chosen is None:
        chosen = candidate_matches[-1] if use_last and len(candidate_matches) > 1 else candidate_matches[0]

    raw_number = chosen.group(0)
    number = _normalize_number_text(raw_number) or raw_number.replace(",", "")

    if money_question and number:
        normalized_money = _normalize_number_text(number) or str(number).replace(",", "")
        if re.fullmatch(r"[-+]?\d+", normalized_money):
            return f"${int(normalized_money):,}"
        return f"${normalized_money}"

    if duration_question:
        for unit in ("days", "day", "weeks", "week", "months", "month", "years", "year", "hours", "hour", "minutes", "minute"):
            if re.search(rf"\b{unit}\b", q_lower):
                return f"{number} {unit}"
        # If question doesn't name a unit (e.g., "How long is my commute?"),
        # infer unit from answer text near the selected number.
        s, e = chosen.span()
        nearby = text_lower[max(0, s - 8): min(len(text_lower), e + 48)]
        inferred = re.search(r"\b(days?|weeks?|months?|years?|hours?|minutes?)\b", nearby)
        if inferred:
            suffix = ""
            if "each way" in nearby:
                suffix = " each way"
            elif "one way" in nearby:
                suffix = " one way"
            elif "round trip" in nearby:
                suffix = " round trip"
            return f"{number} {inferred.group(1)}{suffix}".strip()
        # Do not collapse to bare number for duration questions without clear unit.
        return text
    if "how many" in q_lower:
        return number
    return number


def _build_count_refinement_prompt(
    *,
    question: str,
    question_type: str,
    question_date: str,
    retrieved_context: str,
    draft_answer: str,
) -> str:
    return (
        "You are validating a count-based memory QA answer.\n"
        "Use ONLY the retrieved context.\n\n"
        f"Question: {question}\n"
        f"Question Type: {question_type or 'unknown'}\n"
        f"Question Date: {question_date or 'Not specified'}\n"
        f"Draft Answer: {draft_answer}\n\n"
        "Retrieved Context:\n"
        f"{retrieved_context}\n\n"
        "Task:\n"
        "1) Inspect all relevant candidates.\n"
        "2) List each unique item/event as a numbered list (1., 2., 3., ...). Skip any item that is the same as one already listed, even if mentioned in a different session.\n"
        "3) Count the items in your numbered list to get the final number.\n"
        "4) Return only strict JSON (no markdown, no extra text).\n\n"
        "Required JSON schema:\n"
        "{\"items\":[\"<list each unique item here>\"],\"final_answer\":\"<concise answer>\",\"final_number\":\"<number only>\",\"unit\":\"<unit if applicable>\",\"notes\":\"<reasoning>\"}"
    )


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    # Try each { from the end — thinking-mode LLMs produce long reasoning
    # before the JSON, so searching from the end finds the actual output.
    candidates = [m.start() for m in re.finditer(r"\{", raw)]
    for start_pos in reversed(candidates):
        # Find the matching closing brace
        depth = 0
        end_pos = None
        for i in range(start_pos, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    end_pos = i + 1
                    break
        if end_pos is None:
            continue
        snippet = raw[start_pos:end_pos]
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and any(
            k in parsed for k in ("final_answer", "final_number", "items")
        ):
            return parsed
    # Fallback: greedy match (original behavior)
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _refine_count_hypothesis(
    *,
    llm: Any,
    question: str,
    question_type: str,
    question_date: str,
    retrieved_context: str,
    draft_answer: str,
) -> str:
    prompt = _build_count_refinement_prompt(
        question=question,
        question_type=question_type,
        question_date=question_date,
        retrieved_context=retrieved_context,
        draft_answer=draft_answer,
    )
    response = str(llm.generate(prompt)).strip()
    parsed = _extract_json_object(response)

    # Schema placeholder values that indicate the LLM copied the template literally
    _SCHEMA_PLACEHOLDERS = {
        "string", "item1", "item2", "item3", "item4", "item5",
        "<list each unique item here>", "<concise answer>", "<number only>",
        "<unit if applicable>", "<reasoning>",
    }

    if parsed:
        # Cross-check: if items array is present, its length is more reliable
        # than a number the LLM states (prevents miscounting).
        items = parsed.get("items")
        items_count: Optional[int] = None
        if isinstance(items, list) and items:
            # Filter out empty/placeholder entries
            real_items = [str(it).strip() for it in items
                          if str(it).strip() and str(it).strip().lower() not in _SCHEMA_PLACEHOLDERS]
            if real_items:
                items_count = len(real_items)

        final_answer = str(parsed.get("final_answer", "") or "").strip()
        number = _normalize_number_text(parsed.get("final_number"))

        # Detect schema placeholders — LLM copied the template literally
        if final_answer.lower() in _SCHEMA_PLACEHOLDERS:
            final_answer = ""
        if number and number.lower() in _SCHEMA_PLACEHOLDERS:
            number = None

        # If enumerated items disagree with stated number, prefer items count
        if items_count is not None and number:
            try:
                stated = int(float(number))
            except (TypeError, ValueError):
                stated = None
            if stated is not None and stated != items_count:
                logger.debug(
                    "Count cross-check: items list has %d entries but final_number='%s'; using items count",
                    items_count, number,
                )
                number = str(items_count)
                final_answer = ""  # force using corrected number path
        elif items_count is not None and not number:
            number = str(items_count)

        if final_answer:
            return _canonicalize_count_answer(final_answer, question)
        unit = str(parsed.get("unit", "") or "").strip()
        if unit.lower() in _SCHEMA_PLACEHOLDERS:
            unit = ""
        if number and unit:
            return f"{number} {unit}".strip()
        if number:
            return _canonicalize_count_answer(number, question)

    # Fallback: refinement JSON was garbled or all placeholder — use draft answer
    draft_canonical = _canonicalize_count_answer(draft_answer, question)
    if draft_canonical and draft_canonical != draft_answer:
        return draft_canonical
    # Last resort: try to extract from raw refinement response
    fallback = _canonicalize_count_answer(response, question)
    if fallback and fallback.lower() not in _SCHEMA_PLACEHOLDERS:
        return fallback
    return draft_canonical or draft_answer


# ---------------------------------------------------------------------------
# Session digest generation (Option 4: pre-compute at ingestion, 0 query cost)
# ---------------------------------------------------------------------------

def build_digest_prompt(session_text: str, session_id: str, session_date: str) -> str:
    """Prompt for extracting a structured fact digest from a conversation session."""
    header = f"Session: {session_id}"
    if session_date:
        header += f" | Date: {session_date}"
    return (
        "Extract ALL key facts from this conversation into a concise bullet-point summary.\n"
        "Include:\n"
        "- Names, locations, dates, and specific amounts/numbers mentioned\n"
        "- Preferences expressed (likes, dislikes, favorites)\n"
        "- Events, activities, and experiences described\n"
        "- Any changes or updates to previous information\n"
        "- Recommendations given or requested\n\n"
        "Be specific — include exact numbers, names, and dates. Keep each bullet to one line.\n"
        "If the conversation has no meaningful personal facts, write: NO_FACTS\n\n"
        f"{header}\n"
        f"{session_text}\n\n"
        "Key facts:"
    )


# Session digest cache — keyed by session_id, persists across questions within a run
_DIGEST_CACHE: Dict[str, str] = {}


def generate_session_digest(
    llm: Any,
    session_id: str,
    session_text: str,
    session_date: str,
    max_input_chars: int = 6000,
) -> str:
    """Generate or retrieve cached session digest."""
    if session_id in _DIGEST_CACHE:
        return _DIGEST_CACHE[session_id]

    truncated = session_text[:max_input_chars]
    prompt = build_digest_prompt(truncated, session_id, session_date)

    try:
        digest = str(llm.generate(prompt)).strip()
        if not digest or "NO_FACTS" in digest.upper():
            digest = ""
    except Exception as e:
        logger.warning("Digest generation failed for session %s: %s", session_id, e)
        digest = ""

    _DIGEST_CACHE[session_id] = digest
    return digest


@dataclass
class HFResponder:
    model_name: str
    max_new_tokens: int = 128

    def __post_init__(self) -> None:
        # Lazy heavy import so module-level import stays lightweight.
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise ImportError(
                "HF backend requires transformers (and torch). "
                "Install with: pip install transformers accelerate"
            ) from exc

        self._pipeline = pipeline(
            "text-generation",
            model=self.model_name,
            tokenizer=self.model_name,
            device_map="auto",
            model_kwargs={"torch_dtype": "auto"},
        )

    def generate(self, prompt: str) -> str:
        outputs = self._pipeline(
            prompt,
            max_new_tokens=self.max_new_tokens,
            do_sample=False,
            return_full_text=False,
        )
        if not outputs:
            return ""
        text = outputs[0].get("generated_text", "")
        return str(text).strip()


def build_memory(
    *,
    llm_provider: str,
    embedder_provider: str,
    vector_store_provider: str,
    embedding_dims: int,
    history_db_path: str,
    llm_model: Optional[str] = None,
    llm_timeout: int = 300,
    llm_max_retries: Optional[int] = None,
    embedder_model: Optional[str] = None,
    full_potential: bool = True,
    defer_enrichment: bool = False,
    enable_rerank: bool = False,
    rerank_model: Optional[str] = None,
    rerank_config: Optional[Dict[str, Any]] = None,
    enable_episodic_index: bool = True,
    enable_hierarchical_retrieval: bool = True,
    enable_orchestrated_search: bool = True,
    cost_guardrail_strict: bool = True,
    disable_batch_writes: bool = False,
) -> Memory:
    """Build Engram Memory for LongMemEval. By default uses full potential (echo, categories, graph, scenes, profiles).

    When defer_enrichment=True, ingestion uses 0 LLM calls (store fast), and
    enrichment is done in batch after all sessions are loaded.
    """
    vector_cfg: Dict[str, Any] = {
        "collection_name": "engram_longmemeval",
        "embedding_model_dims": embedding_dims,
    }

    llm_cfg: Dict[str, Any] = {
        "max_tokens": 256 if llm_provider == "dhee" else 16384,
        "timeout": max(1, int(llm_timeout)),
        "model": DEFAULT_NVIDIA_LLM_MODEL,
        "temperature": 0.1 if llm_provider == "dhee" else 0.6,
        "top_p": 0.7,
    }
    if llm_provider == "dhee":
        llm_cfg["n_ctx"] = 16384  # Qwen3.5-2B supports 262K; 16K is safe for Q4_K_M
        llm_cfg["n_threads"] = 8
    else:
        llm_cfg["stream"] = True
    if llm_max_retries is not None:
        llm_cfg["max_retries"] = max(0, int(llm_max_retries))
    if llm_model:
        llm_cfg["model"] = llm_model
    embedder_cfg: Dict[str, Any] = {"embedding_dims": embedding_dims}
    if embedder_model:
        embedder_cfg["model"] = embedder_model
    elif embedder_provider == "nvidia":
        embedder_cfg["model"] = DEFAULT_NVIDIA_EMBEDDER_MODEL

    rerank_kwargs: Dict[str, Any] = {
        "enable_rerank": enable_rerank,
        "config": rerank_config or {},
    }
    if rerank_model:
        rerank_kwargs["model"] = rerank_model
    rerank_cfg = RerankConfig(**rerank_kwargs)

    config = MemoryConfig(
        vector_store=VectorStoreConfig(provider=vector_store_provider, config=vector_cfg),
        llm=LLMConfig(provider=llm_provider, config=llm_cfg),
        embedder=EmbedderConfig(provider=embedder_provider, config=embedder_cfg),
        history_db_path=history_db_path,
        embedding_model_dims=embedding_dims,
        echo=EchoMemConfig(enable_echo=full_potential, default_depth="deep"),
        category=CategoryMemConfig(use_llm_categorization=full_potential, enable_categories=full_potential),
        graph=KnowledgeGraphConfig(enable_graph=full_potential),
        # Keep write-path cost flat: scene summaries default to extractive mode.
        scene=SceneConfig(use_llm_summarization=False, enable_scenes=full_potential),
        profile=ProfileConfig(use_llm_extraction=full_potential, enable_profiles=full_potential),
        orchestration=OrchestrationConfig(
            enable_orchestrated_search=enable_orchestrated_search,
            enable_episodic_index=enable_episodic_index,
            enable_hierarchical_retrieval=enable_hierarchical_retrieval,
            reflection_max_hops=1,
        ),
        cost_guardrail=CostGuardrailConfig(
            strict_write_path_cap=cost_guardrail_strict,
        ),
        enrichment=EnrichmentConfig(
            enable_unified=full_potential,
            max_batch_size=5,
            defer_enrichment=defer_enrichment,
        ),
        batch=BatchConfig(
            enable_batch=(full_potential and not defer_enrichment and not disable_batch_writes),
            max_batch_size=50,
        ),
        rerank=rerank_cfg,
        engram_extraction=EngramExtractionConfig(enable_extraction=True, use_llm_extraction=full_potential),
    )
    mem = Memory(config)
    # FullMemory features (categories, scenes, profiles) need FullSQLiteManager
    if full_potential:
        from dhee.db.sqlite import FullSQLiteManager
        mem.db = FullSQLiteManager(history_db_path)

    # Teacher logging: wrap the LLM to capture (prompt, response) pairs for DheeModel training
    teacher_log_dir = os.environ.get("DHEE_TEACHER_LOG_DIR")
    if teacher_log_dir and hasattr(mem, "llm") and mem.llm is not None:
        from dhee.llms.teacher_logger import TeacherLoggingLLM
        mem.llm = TeacherLoggingLLM(mem.llm, log_dir=teacher_log_dir)
        logger.info("Teacher logging enabled: %s", teacher_log_dir)

    return mem


def build_context_text(
    *,
    results: Sequence[Dict[str, Any]],
    max_chars: int,
    max_results: int,
    per_result_max_chars: int,
    query: str = "",
) -> str:
    """Build reader context from search results using digest summaries + keyword evidence.

    Each candidate block includes:
    - Header with session ID and date
    - Summary section from pre-computed digest (if available)
    - Evidence section with large context windows around query keywords
    """
    chunks: List[str] = []
    total = 0
    for idx, result in enumerate(results[: max(1, int(max_results))], start=1):
        if result.get("masked"):
            continue

        memory_text = str(result.get("memory") or "").strip()
        metadata = result.get("metadata") or {}
        session_id = parse_session_id_from_result(result) or "unknown"
        session_date = str(metadata.get("session_date", "")).strip()
        session_digest = str(metadata.get("session_digest", "")).strip()

        # Build header
        header = f"[Candidate {idx}] Session: {session_id}"
        if session_date:
            header += f" | Date: {session_date}"

        sections: List[str] = [header]

        # Section 1: Digest summary (pre-computed at ingestion)
        if session_digest:
            sections.append(f"Summary:\n{session_digest}")

        # Section 2: Keyword evidence with large context windows
        if query and memory_text:
            keyword_evidence = extract_keyword_context(
                text=memory_text,
                query=query,
                context_chars=800,
                max_excerpts=5,
            )
            if keyword_evidence:
                # Budget remaining after digest
                evidence_budget = per_result_max_chars - len(session_digest)
                if evidence_budget > 200:
                    sections.append(f"Evidence:\n{_truncate_text(keyword_evidence, evidence_budget)}")
        elif memory_text:
            # No query — fall back to full text (legacy behavior)
            sections.append(_truncate_text(memory_text, per_result_max_chars))
        else:
            # No memory text — use evidence_text or details
            evidence = str(result.get("evidence_text") or "").strip()
            if evidence:
                sections.append(_truncate_text(evidence, per_result_max_chars))
            else:
                details = str(result.get("details") or "").strip()
                if details:
                    sections.append(details)

        block = "\n".join(sections)
        if not block.strip() or block.strip() == header:
            continue
        if total + len(block) > max_chars and chunks:
            break
        chunks.append(block)
        total += len(block)

    if not chunks:
        return "No relevant retrieved history."
    return "\n\n---\n\n".join(chunks)


def build_output_row(
    *,
    question_id: str,
    hypothesis: str,
    retrieved_session_ids: Sequence[str],
    retrieval_metrics: Dict[str, float],
    include_debug_fields: bool,
    orchestration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build evaluator-compatible output row with optional debug fields."""
    row: Dict[str, Any] = {
        "question_id": question_id,
        "hypothesis": hypothesis,
    }
    if include_debug_fields:
        row["retrieved_session_ids"] = list(retrieved_session_ids)
        row["retrieval_metrics"] = dict(retrieval_metrics)
        if orchestration:
            row["orchestration"] = dict(orchestration)
    return row


def run_longmemeval(args: argparse.Namespace) -> Dict[str, Any]:
    with open(args.dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    if not isinstance(dataset, list):
        raise ValueError("Dataset file must be a JSON list of instances.")

    # Support resume mode: skip already-completed question_ids
    resume_skip_qids: set = set()
    resume_mode = getattr(args, "resume_mode", False)
    if resume_mode:
        output_path_check = Path(args.output_jsonl)
        if output_path_check.exists():
            with output_path_check.open("r", encoding="utf-8") as rf:
                for line in rf:
                    try:
                        row = json.loads(line.strip())
                        qid = row.get("question_id", "")
                        if qid:
                            resume_skip_qids.add(str(qid))
                    except (json.JSONDecodeError, AttributeError):
                        pass
            logger.info("Resume mode: skipping %d already-completed questions", len(resume_skip_qids))

    selected = dataset[args.start_index : args.end_index if args.end_index > 0 else None]
    if args.max_questions > 0:
        selected = selected[: args.max_questions]
    if args.skip_abstention:
        selected = [entry for entry in selected if "_abs" not in str(entry.get("question_id", ""))]

    use_deferred = getattr(args, "defer_enrichment", False)
    rerank_runtime_config: Optional[Dict[str, Any]] = None
    if getattr(args, "enable_rerank", False):
        rerank_runtime_config = {
            "timeout": args.rerank_timeout,
            "passage_strategy": args.rerank_strategy,
            "max_passage_chars": args.rerank_max_passage_chars,
            "context_lines": args.rerank_context_lines,
            "candidates_multiplier": args.rerank_candidates_multiplier,
            "strict_schema": True,
        }
    memory = build_memory(
        llm_provider=args.llm_provider,
        embedder_provider=args.embedder_provider,
        vector_store_provider=args.vector_store_provider,
        embedding_dims=args.embedding_dims,
        history_db_path=args.history_db_path,
        llm_model=args.llm_model,
        llm_timeout=getattr(args, "llm_timeout", 300),
        llm_max_retries=getattr(args, "llm_max_retries", None),
        embedder_model=args.embedder_model,
        full_potential=args.full_potential,
        defer_enrichment=use_deferred,
        enable_rerank=getattr(args, "enable_rerank", False),
        rerank_model=getattr(args, "rerank_model", None),
        rerank_config=rerank_runtime_config,
        enable_episodic_index=getattr(args, "enable_episodic_index", True),
        enable_hierarchical_retrieval=getattr(args, "enable_hierarchical_retrieval", True),
        enable_orchestrated_search=getattr(args, "enable_orchestrated_search", True),
        cost_guardrail_strict=getattr(args, "cost_guardrail_strict", True),
        disable_batch_writes=getattr(args, "disable_batch_writes", False),
    )

    # Separate answer LLM for reasoning models (DeepSeek, etc.)
    _answer_llm = None
    if getattr(args, "answer_llm_model", None) and args.answer_backend != "hf":
        from dhee.llms.nvidia import NvidiaLLM
        _answer_llm = NvidiaLLM({
            "model": args.answer_llm_model,
            "enable_thinking": getattr(args, "answer_enable_thinking", False),
            "temperature": 0.2,
            "top_p": 0.7,
            "max_tokens": getattr(args, "answer_max_tokens", 4096),
            "timeout": getattr(args, "answer_llm_timeout", 300),
            "max_retries": 2,
        })
        logger.info("Using separate answer LLM: %s (thinking=%s)", args.answer_llm_model, getattr(args, "answer_enable_thinking", False))

    # Separate code-exec LLM (fast, cheap reasoning model for code generation)
    _code_exec_llm = None
    _code_exec_model = getattr(args, "code_exec_model", None)
    if _code_exec_model and args.answer_backend != "hf":
        from dhee.llms.nvidia import NvidiaLLM
        _code_exec_llm = NvidiaLLM({
            "model": _code_exec_model,
            "temperature": 0.6,
            "top_p": 0.7,
            "max_tokens": 4096,
            "timeout": 300,
            "max_retries": 2,
        })
        logger.info("Using separate code-exec LLM: %s", _code_exec_model)

    orchestration_mode = str(getattr(args, "answer_orchestration_mode", "off") or "off").strip().lower()
    if orchestration_mode not in {"off", "hybrid", "strict"}:
        orchestration_mode = "off"

    _orchestrator_llm = None
    if orchestration_mode != "off" and args.answer_backend != "hf":
        orchestrator_model = getattr(args, "answer_orchestrator_llm_model", None)
        if orchestrator_model:
            from dhee.llms.nvidia import NvidiaLLM
            _orchestrator_llm = NvidiaLLM({
                "model": orchestrator_model,
                "temperature": 0.0,
                "top_p": 1.0,
                "max_tokens": 4096,
                "timeout": getattr(args, "answer_orchestrator_llm_timeout", 120),
                "max_retries": 1,
            })
            logger.info("Using dedicated orchestrator LLM: %s", orchestrator_model)
        else:
            _orchestrator_llm = memory.llm
            logger.info("Using memory LLM for orchestration map stage: %s", getattr(memory.llm, "model", "unknown"))

    hf_responder: Optional[HFResponder] = None
    if args.answer_backend == "hf" and not getattr(args, "retrieval_only", False):
        hf_responder = HFResponder(model_name=args.hf_model, max_new_tokens=args.hf_max_new_tokens)

    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    retrieval_path = Path(args.retrieval_jsonl) if args.retrieval_jsonl else None
    if retrieval_path:
        retrieval_path.parent.mkdir(parents=True, exist_ok=True)

    per_question_metrics: List[Dict[str, float]] = []
    rerank_applied_count = 0
    top1_changed_count = 0
    gold_moved_to_top1_count = 0
    gold_rank_pre_values: List[int] = []
    gold_rank_post_values: List[int] = []
    processed = 0

    file_mode = "a" if resume_mode and resume_skip_qids else "w"
    with output_path.open(file_mode, encoding="utf-8") as out_f:
        retrieval_f = retrieval_path.open(file_mode, encoding="utf-8") if retrieval_path else None
        try:
            for entry in selected:
                question_id = str(entry.get("question_id", ""))
                if not question_id:
                    continue
                if question_id in resume_skip_qids:
                    continue

                # Keep each question isolated.
                memory.delete_all(user_id=args.user_id)

                session_ids = entry.get("haystack_session_ids") or []
                session_dates = entry.get("haystack_dates") or []
                sessions = entry.get("haystack_sessions") or []

                # Build batch items for all sessions
                use_digests = getattr(args, "generate_digests", False) and args.answer_backend != "hf"
                batch_items = []
                for sess_id, sess_date, sess_turns in zip(session_ids, session_dates, sessions):
                    payload = format_session_memory(
                        str(sess_id), str(sess_date), sess_turns or [],
                        include_all_roles=True,
                    )
                    # Generate session digest at ingestion (Option 4)
                    sess_digest = ""
                    if use_digests:
                        sess_digest = generate_session_digest(
                            llm=memory.llm,
                            session_id=str(sess_id),
                            session_text=payload,
                            session_date=str(sess_date),
                        )
                    # Build context_messages from session turns for deferred mode
                    ctx_msgs = None
                    if use_deferred and sess_turns:
                        ctx_msgs = [
                            {"role": t.get("role", "user"), "content": str(t.get("content", "")).strip()}
                            for t in sess_turns if str(t.get("content", "")).strip()
                        ]
                    batch_items.append({
                        "content": payload,
                        "metadata": {
                            "session_id": str(sess_id),
                            "session_date": str(sess_date),
                            "question_id": question_id,
                            "session_digest": sess_digest,
                        },
                        "categories": ["longmemeval", "session"],
                        "_context_messages": ctx_msgs,
                    })

                # Use add_batch for fewer LLM calls; fallback to sequential on failure
                if batch_items:
                    if use_deferred:
                        # Deferred mode: sequential add with context_messages
                        for item in batch_items:
                            try:
                                memory.add(
                                    messages=item["content"],
                                    user_id=args.user_id,
                                    metadata=item["metadata"],
                                    categories=item["categories"],
                                    infer=False,
                                    context_messages=item.get("_context_messages"),
                                )
                            except Exception as e2:
                                logger.warning("Skipping session for question %s: %s", question_id, e2)
                    else:
                        try:
                            memory.add_batch(
                                items=batch_items,
                                user_id=args.user_id,
                            )
                        except Exception as e:
                            logger.warning("Batch add failed for question %s, retrying sequentially: %s", question_id, e)
                            for item in batch_items:
                                try:
                                    memory.add(
                                        messages=item["content"],
                                        user_id=args.user_id,
                                        metadata=item["metadata"],
                                        categories=item["categories"],
                                        infer=False,
                                    )
                                except Exception as e2:
                                    logger.warning("Skipping session for question %s: %s", question_id, e2)

                # Batch enrich after all sessions loaded (deferred mode)
                if use_deferred:
                    try:
                        memory.enrich_pending(user_id=args.user_id, batch_size=10, max_batches=50)
                    except Exception as e:
                        logger.warning("Enrichment failed for question %s: %s", question_id, e)

                query = str(entry.get("question", "")).strip()
                question_date = str(entry.get("question_date", "")).strip()
                question_type = str(entry.get("question_type", "")).strip()

                orchestration_payload = memory.search_orchestrated(
                    query=query,
                    user_id=args.user_id,
                    question_type=question_type,
                    question_date=question_date,
                    limit=args.top_k,
                    orchestration_mode=orchestration_mode,
                    base_search_limit=args.top_k,
                    base_context_limit=getattr(args, "answer_context_top_k", 10),
                    search_cap=getattr(args, "answer_orchestrator_search_cap", max(args.top_k, 30)),
                    context_cap=getattr(args, "answer_orchestrator_context_cap", 20),
                    map_max_candidates=getattr(args, "answer_orchestrator_max_candidates", 8),
                    map_max_chars=getattr(args, "answer_orchestrator_max_chars", 1200),
                    keyword_search=True,
                    hybrid_alpha=0.7,
                    include_evidence=True,
                    evidence_strategy=getattr(args, "answer_context_strategy", "full"),
                    evidence_max_chars=getattr(args, "answer_context_max_chars", 4000),
                    evidence_context_lines=getattr(args, "answer_context_lines", 3),
                    max_context_chars=args.max_context_chars,
                    rerank=getattr(args, "enable_rerank", False),
                    orchestrator_llm=_orchestrator_llm if orchestration_mode != "off" else None,
                    reflection_max_hops=1,
                )
                results = orchestration_payload.get("results", [])
                orchestration_meta = orchestration_payload.get("orchestration") or {}
                coverage = orchestration_payload.get("coverage") or {}
                intent_value = str(orchestration_meta.get("intent") or "").strip().lower()
                context_limit = max(
                    1,
                    int(orchestration_meta.get("context_limit") or getattr(args, "answer_context_top_k", 10)),
                )

                orchestration_debug: Dict[str, Any] = {
                    "mode": orchestration_meta.get("mode", orchestration_mode),
                    "intent": intent_value or "freeform",
                    "search_limit": int(orchestration_meta.get("search_limit") or args.top_k),
                    "context_limit": context_limit,
                    "coverage_ratio": coverage.get("coverage_ratio"),
                    "event_hit_count": coverage.get("event_hit_count"),
                    "map_reduce_used": orchestration_meta.get("map_reduce_used", False),
                    "reflection_hops": orchestration_meta.get("reflection_hops", 0),
                    "reduced_answer": orchestration_meta.get("reduced_answer"),
                    "reason_codes": orchestration_meta.get("reason_codes", []),
                    "fact_count": len(orchestration_payload.get("facts") or []),
                }
                if orchestration_meta.get("rewritten_query"):
                    orchestration_debug["rewritten_query"] = orchestration_meta.get("rewritten_query")

                gold_session_ids = [str(x) for x in (entry.get("answer_session_ids") or []) if str(x).strip()]
                rerank_applied = any(_coerce_finite_float(result.get("rerank_logit")) is not None for result in results)
                top1_pre_rerank_session_id = _top1_session_id_by_score(results, "composite_score")
                top1_post_rerank_session_id = parse_session_id_from_result(results[0]) if results else None
                gold_rank_by_similarity = _rank_gold_by_score(results, gold_session_ids, "score")
                gold_rank_by_composite = _rank_gold_by_score(results, gold_session_ids, "composite_score")
                gold_rank_by_rerank = _rank_gold_by_score(
                    results,
                    gold_session_ids,
                    "rerank_logit",
                    require_finite_score=True,
                )

                if getattr(args, "enable_rerank", False) and args.fail_on_rerank_noop and not rerank_applied:
                    raise RuntimeError(
                        f"Rerank appears inactive for question_id={question_id}; "
                        "no finite rerank_logit found in returned results."
                    )

                if rerank_applied:
                    rerank_applied_count += 1
                if top1_pre_rerank_session_id and top1_post_rerank_session_id:
                    if top1_pre_rerank_session_id != top1_post_rerank_session_id:
                        top1_changed_count += 1

                post_rank_effective = (
                    gold_rank_by_rerank
                    if gold_rank_by_rerank is not None
                    else gold_rank_by_composite
                )
                if gold_rank_by_composite is not None:
                    gold_rank_pre_values.append(gold_rank_by_composite)
                if post_rank_effective is not None:
                    gold_rank_post_values.append(post_rank_effective)
                if (
                    gold_rank_by_composite is not None
                    and gold_rank_by_composite > 1
                    and post_rank_effective == 1
                ):
                    gold_moved_to_top1_count += 1

                retrieved_session_ids = dedupe_preserve_order(
                    [
                        sid
                        for sid in [parse_session_id_from_result(result) for result in results]
                        if sid is not None
                    ]
                )
                metrics = compute_session_metrics(
                    retrieved_session_ids=retrieved_session_ids,
                    answer_session_ids=entry.get("answer_session_ids", []),
                )
                per_question_metrics.append(metrics)

                # Defaults when retrieval_only skips the answer-gen block below.
                # Keeps the downstream write/export path unchanged.
                hypothesis = ""
                context = ""

                if not getattr(args, "retrieval_only", False):
                    max_results = context_limit
                    per_result_max = getattr(args, "answer_context_max_chars", 4000)

                    # Prefer the core orchestrated context; fallback to legacy builder.
                    context = str(orchestration_payload.get("context") or "").strip()
                    if not context:
                        context = build_context_text(
                            results=results,
                            max_chars=args.max_context_chars,
                            max_results=max_results,
                            per_result_max_chars=per_result_max,
                            query=query,
                        )
                    reduced_answer: Optional[str] = orchestration_payload.get("reduced_answer")

                    prompt = build_answer_prompt(
                        question=query,
                        retrieved_context=context,
                        question_date=question_date,
                        question_type=question_type,
                    )

                    # NOTE: Phase 1 shortcut disabled — the 8b model's fact
                    # extraction is unreliable (over/under-counts), so the LLM
                    # with full grounding/refinement chain produces better answers.
                    # reduced_answer is still logged for diagnostics.

                    if args.answer_backend == "hf":
                        assert hf_responder is not None
                        answer_llm = hf_responder
                        hypothesis = str(hf_responder.generate(prompt)).strip()
                    else:
                        answer_llm = _answer_llm or memory.llm
                        # For thinking models, strip reasoning instructions — thinking handles that.
                        # Just ask for the direct concise answer.
                        answer_prompt = prompt
                        if _answer_llm and getattr(_answer_llm, "enable_thinking", False):
                            answer_prompt = prompt.replace(
                                "Think step by step, then provide your answer.\n\n"
                                "Reasoning:\n[Identify which conversations contain relevant information and quote the exact supporting text]\n\n"
                                "Answer:\n[Your concise final answer — use the exact words/numbers from the context]",
                                "Give your concise final answer directly — use the exact words/numbers from the context. "
                                "For list questions, list the items. For number questions, give the number. "
                                "Do NOT include evidence or citations — just the answer."
                            )
                        raw_answer = str(answer_llm.generate(answer_prompt)).strip()
                        # Parse Answer: section from CoT output if present
                        hypothesis = _parse_answer_section(raw_answer)

                        # --- Answer Grounding Verification ---
                        # If the hypothesis contains numbers not found in the
                        # context, the LLM likely hallucinated.  Re-generate
                        # with a stricter grounding constraint.
                        # Skip for count-style questions — those numbers are
                        # computed by enumeration, not extracted verbatim.
                        if (
                            not _is_refusal(hypothesis)
                            and not _is_count_style_question(query)
                            and not _answer_grounded_in_context(hypothesis, context)
                        ):
                            logger.info(
                                "Grounding check failed for %s — re-generating with constraint",
                                question_id,
                            )
                            grounded_prompt = (
                                prompt
                                + "\n\nCRITICAL: Your previous answer contained a number or fact "
                                "that does NOT appear in the provided context. Re-read the "
                                "conversations above very carefully and answer using ONLY "
                                "values explicitly written in the text. Quote the exact "
                                "number/fact from the context."
                            )
                            raw_answer2 = str(answer_llm.generate(grounded_prompt)).strip()
                            hypothesis2 = _parse_answer_section(raw_answer2)
                            # Accept the re-generated answer only if it IS grounded
                            if (
                                not _is_refusal(hypothesis2)
                                and _answer_grounded_in_context(hypothesis2, context)
                            ):
                                hypothesis = hypothesis2

                        # --- Count refinement ---
                        # Run for ALL count-style questions including multi-session.
                        # Multi-session counting is the hardest case and benefits most
                        # from explicit enumeration.
                        if _is_count_style_question(query) and not _is_refusal(hypothesis):
                            logger.info(
                                "Running count refinement for %s (draft=%r, multi=%s)",
                                question_id, hypothesis, "multi-session" in question_type.lower(),
                            )

                            # 1) Entity registry lookup (zero LLM cost)
                            registry_answer = None
                            try:
                                if hasattr(memory, "lookup_entity_aggregates"):
                                    registry_answer = memory.lookup_entity_aggregates(
                                        query=query, user_id=args.user_id,
                                    )
                                    if registry_answer:
                                        logger.info(
                                            "Entity registry answer for %s: %r",
                                            question_id, registry_answer,
                                        )
                            except Exception as reg_exc:
                                logger.debug("Entity registry lookup failed for %s: %s", question_id, reg_exc)

                            code_exec_answer = None

                            # 3) Pick best: code_exec > registry > existing refinement
                            if code_exec_answer and not _is_refusal(code_exec_answer):
                                hypothesis = code_exec_answer
                            elif registry_answer and not _is_refusal(registry_answer):
                                hypothesis = registry_answer
                            else:
                                # Fall through to existing _refine_count_hypothesis
                                try:
                                    refinement_llm = _answer_llm or memory.llm
                                    refined = _refine_count_hypothesis(
                                        llm=refinement_llm,
                                        question=query,
                                        question_type=question_type,
                                        question_date=question_date,
                                        retrieved_context=context,
                                        draft_answer=hypothesis,
                                    )
                                    logger.info(
                                        "Count refinement result for %s: refined=%r, is_refusal=%s",
                                        question_id, refined, _is_refusal(refined) if refined else "N/A",
                                    )
                                    if refined and not _is_refusal(refined):
                                        hypothesis = refined
                                except Exception as refine_exc:
                                    logger.warning("Count refinement failed for question %s: %s", question_id, refine_exc)

                            if not _is_refusal(hypothesis):
                                pre_canon = hypothesis
                                hypothesis = _canonicalize_count_answer(hypothesis, query)
                                if hypothesis != pre_canon:
                                    logger.info("Canonicalized %r -> %r for %s", pre_canon, hypothesis, question_id)

                        # Fallback: if reducer produced an answer for LATEST intent
                        # and the LLM answer is low-confidence, prefer the reducer.
                        if (
                            reduced_answer
                            and not _is_refusal(reduced_answer)
                            and intent_value == "latest"
                            and is_low_confidence_answer(hypothesis)
                        ):
                            hypothesis = reduced_answer

                output_row = build_output_row(
                    question_id=question_id,
                    hypothesis=hypothesis,
                    retrieved_session_ids=retrieved_session_ids[: args.top_k],
                    retrieval_metrics=metrics,
                    include_debug_fields=args.include_debug_fields,
                    orchestration=orchestration_debug if orchestration_mode != "off" else None,
                )
                out_f.write(json.dumps(output_row, ensure_ascii=False) + "\n")
                out_f.flush()

                if retrieval_f is not None:
                    retrieval_row = {
                        "question_id": question_id,
                        "answer_session_ids": entry.get("answer_session_ids", []),
                        "retrieved_session_ids": retrieved_session_ids[: args.top_k],
                        "metrics": metrics,
                    }
                    if args.include_debug_fields and orchestration_mode != "off":
                        retrieval_row["orchestration"] = dict(orchestration_debug)
                    if args.include_debug_fields:
                        candidates = []
                        for result in results[: args.top_k]:
                            rerank_logit = _coerce_finite_float(result.get("rerank_logit"))
                            rerank_passage_chars = result.get("rerank_passage_chars")
                            try:
                                rerank_passage_chars = int(rerank_passage_chars) if rerank_passage_chars is not None else None
                            except (TypeError, ValueError):
                                rerank_passage_chars = None
                            candidates.append(
                                {
                                    "session_id": parse_session_id_from_result(result),
                                    "score": _coerce_finite_float(result.get("score")),
                                    "keyword_score": _coerce_finite_float(result.get("keyword_score")),
                                    "composite_score": _coerce_finite_float(result.get("composite_score")),
                                    "rerank_logit": rerank_logit,
                                    "memory_chars": len(str(result.get("memory", "") or "")),
                                    "rerank_passage_chars": rerank_passage_chars,
                                    "evidence_source": result.get("evidence_source"),
                                    "evidence_chars": result.get("evidence_chars"),
                                    "resolver_boost": _coerce_finite_float(result.get("resolver_boost")),
                                    "resolver_boost_applied": bool(result.get("resolver_boost_applied")),
                                    "fact_active_count": result.get("fact_active_count"),
                                    "fact_superseded_count": result.get("fact_superseded_count"),
                                    "resolver_intent": result.get("resolver_intent"),
                                    "resolver_predicate": result.get("resolver_predicate"),
                                }
                            )
                        retrieval_row.update(
                            {
                                "rerank_applied": rerank_applied,
                                "top1_pre_rerank_session_id": top1_pre_rerank_session_id,
                                "top1_post_rerank_session_id": top1_post_rerank_session_id,
                                "gold_rank_by_similarity": gold_rank_by_similarity,
                                "gold_rank_by_composite": gold_rank_by_composite,
                                "gold_rank_by_rerank": gold_rank_by_rerank,
                                "candidates": candidates,
                            }
                        )
                    retrieval_f.write(json.dumps(retrieval_row, ensure_ascii=False) + "\n")
                    retrieval_f.flush()

                processed += 1
                if args.print_every > 0 and processed % args.print_every == 0:
                    # Per-question recall (this question)
                    q_any1 = metrics.get("recall_any@1", 0.0)
                    q_any5 = metrics.get("recall_any@5", 0.0)
                    q_any10 = metrics.get("recall_any@10", 0.0)
                    q_all1 = metrics.get("recall_all@1", 0.0)
                    q_all5 = metrics.get("recall_all@5", 0.0)
                    q_all10 = metrics.get("recall_all@10", 0.0)
                    # Rolling aggregate across all questions so far
                    n = len(per_question_metrics)
                    agg_any1 = sum(m.get("recall_any@1", 0.0) for m in per_question_metrics) / n if n else 0.0
                    agg_any5 = sum(m.get("recall_any@5", 0.0) for m in per_question_metrics) / n if n else 0.0
                    agg_any10 = sum(m.get("recall_any@10", 0.0) for m in per_question_metrics) / n if n else 0.0
                    agg_all1 = sum(m.get("recall_all@1", 0.0) for m in per_question_metrics) / n if n else 0.0
                    agg_all5 = sum(m.get("recall_all@5", 0.0) for m in per_question_metrics) / n if n else 0.0
                    agg_all10 = sum(m.get("recall_all@10", 0.0) for m in per_question_metrics) / n if n else 0.0
                    hit1 = "HIT" if q_any1 >= 1.0 else "MISS"
                    print(
                        f"[LongMemEval] {processed:3d}/{len(selected)}  qid={question_id[:8]}  "
                        f"{hit1}@1  this:any[1/5/10]={q_any1:.0f}/{q_any5:.0f}/{q_any10:.0f} "
                        f"all[1/5/10]={q_all1:.0f}/{q_all5:.0f}/{q_all10:.0f}  "
                        f"running:R@1={agg_any1:.1%} R@5={agg_any5:.1%} R@10={agg_any10:.1%} "
                        f"[all] R@1={agg_all1:.1%} R@5={agg_all5:.1%} R@10={agg_all10:.1%}",
                        flush=True,
                    )

                # Export training data BEFORE next iteration's delete_all() wipes DB
                _training_data_dir = getattr(args, "training_data_dir", None)
                if _training_data_dir:
                    try:
                        _export_training_data(
                            memory=memory,
                            question_id=question_id,
                            question=query,
                            question_type=question_type,
                            question_date=question_date,
                            gold_answer=str(entry.get("answer", "")),
                            hypothesis=hypothesis,
                            intent=intent_value,
                            context=context,
                            results=results,
                            orchestration_meta=orchestration_debug,
                            session_items=batch_items,
                            output_dir=_training_data_dir,
                            user_id=args.user_id,
                        )
                    except Exception as _td_exc:
                        logger.debug("Training data export failed for %s: %s", question_id, _td_exc)
        finally:
            if retrieval_f is not None:
                retrieval_f.close()

    aggregate: Dict[str, float] = {}
    if per_question_metrics:
        for key in sorted(per_question_metrics[0].keys()):
            aggregate[key] = round(mean(metric[key] for metric in per_question_metrics), 4)

    summary = {
        "processed": processed,
        "output_jsonl": str(output_path),
        "retrieval_jsonl": str(retrieval_path) if retrieval_path else None,
        "aggregate_retrieval_metrics": aggregate,
        "rerank_applied_rate": round(rerank_applied_count / processed, 4) if processed > 0 else 0.0,
        "top1_changed_rate": round(top1_changed_count / processed, 4) if processed > 0 else 0.0,
        "gold_moved_to_top1_rate": round(gold_moved_to_top1_count / processed, 4) if processed > 0 else 0.0,
        "avg_gold_rank_pre": round(mean(gold_rank_pre_values), 4) if gold_rank_pre_values else None,
        "avg_gold_rank_post": round(mean(gold_rank_post_values), 4) if gold_rank_post_values else None,
        "answer_backend": args.answer_backend,
        "hf_model": args.hf_model if args.answer_backend == "hf" else None,
    }
    print(json.dumps(summary, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Engram on LongMemEval in Colab or local environments.")
    parser.add_argument("--dataset-path", required=True, help="Path to LongMemEval json file.")
    parser.add_argument("--output-jsonl", required=True, help="Path to write question_id/hypothesis jsonl.")
    parser.add_argument("--retrieval-jsonl", default=None, help="Optional path for retrieval-only log jsonl.")
    parser.add_argument(
        "--include-debug-fields",
        action="store_true",
        help="Include retrieval debug fields in output jsonl rows (official evaluator only needs question_id/hypothesis).",
    )

    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Disable echo, categories, graph, scenes, profiles (faster but lower retrieval quality). Default is full potential.",
    )
    parser.add_argument("--user-id", default="longmemeval", help="User scope used for temporary ingestion.")
    parser.add_argument("--start-index", type=int, default=0, help="Start offset for dataset slicing.")
    parser.add_argument("--end-index", type=int, default=-1, help="End offset for dataset slicing (exclusive).")
    parser.add_argument("--max-questions", type=int, default=-1, help="Cap number of evaluated questions.")
    parser.add_argument("--skip-abstention", action="store_true", help="Skip *_abs questions.")

    parser.add_argument("--top-k", type=int, default=20, help="Number of retrieved memories for context.")
    parser.add_argument("--max-context-chars", type=int, default=30000, help="Maximum context size passed to reader.")
    parser.add_argument(
        "--answer-context-top-k",
        type=int,
        default=10,
        help="How many top retrieved results to include in reader context.",
    )
    parser.add_argument(
        "--answer-context-strategy",
        choices=["vector_or_snippet", "vector_text", "snippet", "full"],
        default="full",
        help="Evidence strategy exposed by memory.search for reader context.",
    )
    parser.add_argument(
        "--answer-context-max-chars",
        type=int,
        default=4000,
        help="Per-result evidence char cap used by memory.search and context builder.",
    )
    parser.add_argument(
        "--answer-context-lines",
        type=int,
        default=3,
        help="Neighbor lines for snippet evidence extraction in memory.search.",
    )
    parser.add_argument("--print-every", type=int, default=25, help="Progress print interval.")

    parser.add_argument(
        "--answer-backend",
        choices=["hf", "engram-llm"],
        default="hf",
        help="Reader backend for hypothesis generation.",
    )
    parser.add_argument("--hf-model", default="Qwen/Qwen2.5-1.5B-Instruct", help="HF model when --answer-backend hf.")
    parser.add_argument("--hf-max-new-tokens", type=int, default=128, help="Generation cap for HF backend.")

    parser.add_argument(
        "--llm-provider",
        choices=["mock", "gemini", "openai", "ollama", "nvidia", "dhee"],
        default="mock",
        help="Engram LLM provider (used for --answer-backend engram-llm).",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help=f"Optional LLM model override (NVIDIA default: {DEFAULT_NVIDIA_LLM_MODEL}).",
    )
    parser.add_argument("--llm-timeout", type=int, default=300, help="LLM request timeout in seconds.")
    parser.add_argument(
        "--llm-max-retries",
        type=int,
        default=None,
        help="LLM client retry count. Use 0 to disable retries.",
    )
    parser.add_argument(
        "--embedder-provider",
        choices=["simple", "gemini", "openai", "ollama", "nvidia", "qwen"],
        default="simple",
        help="Engram embedder provider for retrieval.",
    )
    parser.add_argument(
        "--embedder-model",
        default=None,
        help=f"Optional embedder model override (NVIDIA default: {DEFAULT_NVIDIA_EMBEDDER_MODEL}).",
    )
    parser.add_argument("--embedding-dims", type=int, default=1536, help="Embedding dimensions for simple/memory configs.")
    parser.add_argument("--vector-store-provider", choices=["memory", "sqlite_vec"], default="memory")
    parser.add_argument("--history-db-path", default="/tmp/engram-longmemeval.db", help="SQLite db path.")
    parser.add_argument("--defer-enrichment", action="store_true", default=False, help="Use deferred enrichment (0 LLM calls at ingestion, batch enrich after).")
    parser.add_argument("--generate-digests", action="store_true", default=False, help="Generate session fact digests at ingestion time. Produces structured summaries per session for richer evidence at query time. Cost: 1 LLM call per unique session (cached across questions).")
    parser.add_argument("--answer-llm-model", default=None, help="Separate LLM model for answer generation (e.g. deepseek-ai/deepseek-v3.1). If not set, uses --llm-model.")
    parser.add_argument("--answer-enable-thinking", action="store_true", default=False, help="Enable thinking/reasoning mode for the answer LLM (DeepSeek, Qwen thinking models).")
    parser.add_argument("--answer-llm-timeout", type=int, default=300, help="Timeout for the answer LLM (reasoning models need more time).")
    parser.add_argument("--answer-max-tokens", type=int, default=4096, help="Max output tokens for answer LLM.")
    parser.add_argument(
        "--answer-orchestration-mode",
        choices=["off", "hybrid", "strict"],
        default="hybrid",
        help="Enable answer orchestration (query rewrite + map/reduce).",
    )
    parser.add_argument(
        "--answer-orchestrator-llm-model",
        default=None,
        help="Optional low-cost LLM model for map-stage fact extraction (defaults to memory llm).",
    )
    parser.add_argument(
        "--answer-orchestrator-llm-timeout",
        type=int,
        default=120,
        help="Timeout for orchestrator map-stage LLM calls.",
    )
    parser.add_argument(
        "--answer-orchestrator-max-candidates",
        type=int,
        default=8,
        help="Maximum retrieved candidates passed to map stage.",
    )
    parser.add_argument(
        "--answer-orchestrator-max-chars",
        type=int,
        default=1200,
        help="Per-candidate char cap for map-stage extraction input.",
    )
    parser.add_argument(
        "--answer-orchestrator-search-cap",
        type=int,
        default=30,
        help="Maximum retrieval depth when orchestration expands search.",
    )
    parser.add_argument(
        "--answer-orchestrator-context-cap",
        type=int,
        default=20,
        help="Maximum number of candidates included in answer context when orchestration is enabled.",
    )
    parser.add_argument(
        "--disable-episodic-index",
        action="store_true",
        default=False,
        help="Disable deterministic episodic index (feature flag).",
    )
    parser.add_argument(
        "--disable-hierarchical-retrieval",
        action="store_true",
        default=False,
        help="Disable hierarchical retrieval anchors (feature flag).",
    )
    parser.add_argument(
        "--disable-orchestrated-search",
        action="store_true",
        default=False,
        help="Disable core orchestrated retrieval path (feature flag).",
    )
    parser.add_argument(
        "--cost-guardrail-strict",
        dest="cost_guardrail_strict",
        action="store_true",
        default=True,
        help="Enforce strict write-path cost guardrail.",
    )
    parser.add_argument(
        "--no-cost-guardrail-strict",
        dest="cost_guardrail_strict",
        action="store_false",
        help="Disable strict write-path cost guardrail.",
    )
    parser.add_argument(
        "--training-data-dir",
        default=None,
        help="Directory to export structured training tuples (engram extraction, query classification, "
             "answer synthesis, context anchoring) for DheeModel fine-tuning. "
             "Data is saved per-question BEFORE memory.delete_all() wipes the DB.",
    )
    parser.add_argument("--enable-rerank", action="store_true", default=False, help="Enable neural reranking (cross-encoder second stage on retrieved results).")
    parser.add_argument(
        "--disable-batch-writes",
        action="store_true",
        default=False,
        help="Force per-item writes (batch.enable_batch=False). The batch write "
             "path skips conflict detection + supersede; this flag re-enables them "
             "for every ingested session. Diagnostic for confirming the live "
             "supersede mechanism on LongMemEval without changing substrate code.",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        default=False,
        help="Skip answer generation entirely. Only compute retrieval metrics (R@1-R@10). "
             "Drops HF/LLM answer costs, emits hypothesis=\"\" in output rows. "
             "For publishing recall benchmarks without LLM dependency.",
    )
    parser.add_argument(
        "--rerank-model",
        default=None,
        help=f"Reranker model override (default: {DEFAULT_NVIDIA_RERANK_MODEL}).",
    )
    parser.add_argument(
        "--rerank-strategy",
        choices=["full", "snippet", "vector_text"],
        default="snippet",
        help="Passage strategy for reranker input.",
    )
    parser.add_argument(
        "--rerank-max-passage-chars",
        type=int,
        default=32000,
        help="Maximum characters per rerank passage.",
    )
    parser.add_argument(
        "--rerank-context-lines",
        type=int,
        default=1,
        help="Context lines around term hits when using --rerank-strategy snippet.",
    )
    parser.add_argument(
        "--rerank-candidates-multiplier",
        type=int,
        default=2,
        help="Rerank top (top_k * multiplier) candidates before slicing to top_k.",
    )
    parser.add_argument(
        "--rerank-timeout",
        type=int,
        default=60,
        help="Timeout seconds for reranker API calls.",
    )
    parser.add_argument(
        "--fail-on-rerank-noop",
        action="store_true",
        default=False,
        help="Fail the run if rerank is enabled but no finite rerank logits are returned for a question.",
    )
    args = parser.parse_args()
    args.full_potential = not args.minimal
    args.defer_enrichment = args.defer_enrichment
    args.enable_episodic_index = not args.disable_episodic_index
    args.enable_hierarchical_retrieval = not args.disable_hierarchical_retrieval
    args.enable_orchestrated_search = not args.disable_orchestrated_search
    return args


def main() -> None:
    args = parse_args()
    run_longmemeval(args)


if __name__ == "__main__":
    main()
