"""Answer-time orchestration utilities for memory-heavy QA.

This module is benchmark-agnostic and can be reused by runtime APIs.
It provides:
- lightweight query-intent routing
- optional query rewriting for retrieval
- map stage (atomic fact extraction)
- deterministic reducers for high-leverage question types
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^Session ID:\s*(?P<session_id>\S+)\s*$", re.MULTILINE)
_RECENT_QUERY_RE = re.compile(r"\b(latest|most recent(?:ly)?|currently|current|recent(?:ly)?|as of|last)\b", re.I)
# Superlative patterns: "the most", "the least", "the first", "the last"
# These require ARGMAX/ARGMIN — not just listing set members.
_SUPERLATIVE_RE = re.compile(
    r"\b(?:the\s+)?(?:most|least|fewest|highest|lowest|biggest|smallest|first|last"
    r"|(?:fly|flew|visit|use|eat|watch|play|buy|read|drive|travel)\w*\s+(?:the\s+)?most)\b",
    re.I,
)
_LOW_CONFIDENCE_RE = re.compile(
    r"\b(i\s+don['’]?t\s+know|not\s+enough\s+information|insufficient\s+information|unknown|cannot\s+determine)\b",
    re.I,
)
_MONEY_RE = re.compile(r"[-+]?\$?\s*(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d+))?")
_DURATION_RE = re.compile(
    r"([-+]?\d+(?:\.\d+)?)\s*(years?|months?|weeks?|days?|hours?|minutes?)",
    re.I,
)


class AnswerIntent(str, Enum):
    COUNT = "count"
    MONEY_SUM = "money_sum"
    DURATION = "duration"
    LATEST = "latest"
    SET_MEMBERS = "set_members"
    FREEFORM = "freeform"


_NUMERIC_INTENTS = {AnswerIntent.COUNT, AnswerIntent.MONEY_SUM, AnswerIntent.DURATION}


@dataclass
class QueryPlan:
    intent: AnswerIntent
    rewritten_query: str
    search_limit: int
    context_limit: int
    should_map_reduce: bool


def classify_answer_intent(question: str, question_type: str = "") -> AnswerIntent:
    q = str(question or "").strip().lower()
    qtype = str(question_type or "").strip().lower()

    if not q:
        return AnswerIntent.FREEFORM

    # DURATION must be checked BEFORE money — "how much time did I spend" is duration, not money.
    if re.search(r"\b(how long|duration|elapsed|time spent|total years?|total months?)\b", q):
        return AnswerIntent.DURATION
    if re.search(r"\bhow much time\b", q):
        return AnswerIntent.DURATION

    # "How many days/months/weeks ago" or "how many days between X and Y"
    # are temporal-duration questions, not counting questions.
    if re.search(r"\bhow many\s+(days?|weeks?|months?|years?|hours?|minutes?)\b", q):
        return AnswerIntent.DURATION

    # Money: strict signals only. "spend/spent" + time words is DURATION (caught above).
    # Exclude "days/hours spent" — that's DURATION, not money.
    money_signals = bool(
        re.search(r"\b(money|dollars?|usd|spent|spend|cost|price)\b", q)
    )
    if money_signals and re.search(r"\b(how much|total|sum|spent|cost)\b", q):
        # "total number of days spent" is DURATION, not money
        if re.search(r"\b(days?|weeks?|months?|years?|hours?|minutes?)\s+(spent|in)\b", q):
            return AnswerIntent.DURATION
        # "what percentage" is FREEFORM, not money
        if re.search(r"\bpercentage\b", q):
            return AnswerIntent.FREEFORM
        return AnswerIntent.MONEY_SUM

    # "How many [quantity-unit]" asks for a numeric VALUE, not a COUNT of distinct items.
    # COUNT = enumerate distinct items (cities, books, sports, doctors)
    # FREEFORM = read/compute a numeric value (points, pages, followers, views, copies)
    # Allow up to 3 modifier words between "how many" and the unit:
    #   "how many Instagram followers" / "how many rare items" / "how many completed videos"
    _QUANTITY_UNITS = (
        r"points?|dollars?|credits?|tokens?|calories?|miles?|steps?|pounds?"
        r"|kilograms?|grams?|liters?|gallons?|servings?|reps?|sets?"
        r"|pages?|episodes?|copies?|followers?|views?|comments?|stars?"
        r"|likes?|shares?|subscribers?|downloads?|posts?"
        r"|people|persons?|viewers?"
        r"|videos?|items?|photos?|images?|songs?|tracks?|chapters?"
        r"|members?|participants?|attendees?|guests?|tickets?"
    )
    _QUANTITY_UNITS_RE = re.compile(
        r"\bhow many\s+(?:\w+\s+){0,3}(" + _QUANTITY_UNITS + r")\b"
    )
    if _QUANTITY_UNITS_RE.search(q):
        return AnswerIntent.FREEFORM

    # "[noun] count" pattern: "page count", "word count", "calorie count", "step count"
    if re.search(r"\b(page|word|calorie|step|follower|subscriber|view|video|item)\s+count\b", q):
        return AnswerIntent.FREEFORM

    # "What is the total number of [quantity]" needs arithmetic (sum), not item counting.
    _TOTAL_QUANTITY_RE = re.compile(
        r"\btotal\s+number\s+of\s+(?:\w+\s+){0,2}(" + _QUANTITY_UNITS + r")\b"
    )
    if _TOTAL_QUANTITY_RE.search(q):
        return AnswerIntent.FREEFORM

    # Knowledge-update questions need LATEST intent — must check BEFORE "how many"
    # to prevent "How many times did X change?" from being routed to COUNT
    if "knowledge-update" in qtype:
        return AnswerIntent.LATEST

    # "How much" alone (without money signals) is a value question, not a count.
    # "How much is the painting worth?" → FREEFORM
    # "How much will I save?" → FREEFORM
    if re.search(r"\bhow much\b", q):
        return AnswerIntent.FREEFORM

    if re.search(r"\b(how many|number of|count|total number)\b", q):
        return AnswerIntent.COUNT

    # Superlative questions FIRST: "which X the most/least/first/last"
    # Must come before generic LATEST check — "the most last month" is COUNT, not LATEST.
    if _SUPERLATIVE_RE.search(q):
        # Frequency superlatives → COUNT (need argmax)
        if re.search(r"\b(the most|most often|most frequent)\b", q, re.I):
            return AnswerIntent.COUNT
        # Temporal superlatives → LATEST (ordering by date)
        if re.search(r"\b(most recent|first|earliest|latest|newest|oldest)\b", q, re.I):
            return AnswerIntent.LATEST

    if _RECENT_QUERY_RE.search(q):
        return AnswerIntent.LATEST

    if re.search(r"\b(which|what are|list|name all)\b", q):
        return AnswerIntent.SET_MEMBERS

    return AnswerIntent.FREEFORM


def rewrite_query_for_intent(question: str, intent: AnswerIntent) -> str:
    q = str(question or "").strip()
    if not q:
        return q

    if intent == AnswerIntent.COUNT:
        # If the original question asks "which X the most", the count is for argmax
        if _SUPERLATIVE_RE.search(q):
            return (
                f"{q}\nList each occurrence of each distinct item across ALL sessions. "
                f"Count how many times each item appears. "
                f"Return the item that appears MOST frequently as the answer."
            )
        return f"{q}\nList each distinct relevant item and return the final total count."
    if intent == AnswerIntent.MONEY_SUM:
        return f"{q}\nExtract every relevant monetary amount and compute one final total."
    if intent == AnswerIntent.DURATION:
        return f"{q}\nExtract each relevant duration and compute one final total duration."
    if intent == AnswerIntent.LATEST:
        return (
            f"{q}\nExtract the date or time each item was started/mentioned/occurred. "
            f"Order by date/time and return ONLY the most recent one as the answer."
        )
    if intent == AnswerIntent.SET_MEMBERS:
        return f"{q}\nList all distinct relevant items with deduplication."
    # FREEFORM: add derivation instruction for computation questions
    if re.search(r"\bwhat time\b", q, re.I):
        return (
            f"{q}\nIf the exact answer is not stated directly, COMPUTE it from the "
            f"available facts (e.g., if wake-up is 7:00 AM and 15 minutes earlier "
            f"on certain days, the answer is 6:45 AM). Always give the final computed value."
        )
    return q


def build_query_plan(
    question: str,
    question_type: str,
    *,
    base_search_limit: int,
    base_context_limit: int,
    search_cap: int = 30,
    context_cap: int = 20,
) -> QueryPlan:
    intent = classify_answer_intent(question, question_type)
    rewritten = rewrite_query_for_intent(question, intent)

    search_limit = max(1, int(base_search_limit))
    context_limit = max(1, int(base_context_limit))

    should_expand = intent in {
        AnswerIntent.COUNT,
        AnswerIntent.MONEY_SUM,
        AnswerIntent.DURATION,
        AnswerIntent.LATEST,
        AnswerIntent.SET_MEMBERS,
    }
    # Also expand FREEFORM questions that need multi-fact derivation.
    # "how many" questions may be classified FREEFORM (e.g., "how many items"
    # hits the quantity-unit filter) but still need map-reduce to aggregate
    # across multiple sessions.  Same for multi-session aggregation patterns.
    if not should_expand and intent == AnswerIntent.FREEFORM:
        q_lower = question.lower()
        if re.search(
            r"\b(what time|what day|what date|at what age|how many|how much"
            r"|total number|in total|all the|list all|what are all)\b",
            q_lower,
        ):
            should_expand = True

    if should_expand:
        search_limit = max(search_limit, min(max(search_limit, int(search_cap)), search_limit + 10))
        context_limit = max(context_limit, min(max(context_limit, int(context_cap)), context_limit + 6))
        search_limit = max(search_limit, context_limit)

    return QueryPlan(
        intent=intent,
        rewritten_query=rewritten,
        search_limit=search_limit,
        context_limit=context_limit,
        should_map_reduce=should_expand,
    )


def build_map_candidates(
    results: Sequence[Dict[str, Any]],
    *,
    max_candidates: int,
    per_candidate_max_chars: int,
) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in list(results)[: max(1, int(max_candidates))]:
        metadata = row.get("metadata") or {}
        session_id = str(metadata.get("session_id") or "").strip()
        memory_text = str(row.get("memory") or "")
        if not session_id and memory_text:
            match = _SESSION_ID_RE.search(memory_text)
            if match:
                session_id = match.group("session_id")
        session_date = str(
            metadata.get("event_time")
            or metadata.get("session_date")
            or metadata.get("event_date")
            or ""
        ).strip()
        evidence = str(row.get("evidence_text") or "").strip() or memory_text
        if not evidence.strip():
            continue

        out.append(
            {
                "session_id": session_id or "unknown",
                "session_date": session_date,
                "text": _truncate_text(evidence, per_candidate_max_chars),
            }
        )
    return out


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _extract_json_payload(raw: str) -> Optional[Any]:
    if not raw:
        return None
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    obj_match = re.search(r"\{[\s\S]*\}", raw)
    if obj_match:
        candidate = obj_match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    arr_match = re.search(r"\[[\s\S]*\]", raw)
    if arr_match:
        candidate = arr_match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return None


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    if text.startswith("$"):
        text = text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def _parse_event_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
            if not date_match:
                return None
            try:
                d = date.fromisoformat(date_match.group(1))
            except ValueError:
                return None
            dt = datetime.combine(d, datetime.min.time())

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def extract_atomic_facts(
    *,
    llm: Any,
    question: str,
    question_type: str,
    question_date: str,
    candidates: Sequence[Dict[str, str]],
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    candidate_blocks = []
    for idx, c in enumerate(candidates, start=1):
        candidate_blocks.append(
            "\n".join(
                [
                    f"[Candidate {idx}] session_id={c.get('session_id', 'unknown')} date={c.get('session_date', '')}",
                    c.get("text", ""),
                ]
            )
        )

    prompt = (
        "You are a fact extraction engine for memory QA.\n"
        "Extract only facts relevant to answering the question.\n"
        "IMPORTANT: Deduplicate facts. If the same item/event appears in "
        "multiple sessions, emit it ONCE with the canonical_key set.\n"
        "canonical_key = a short lowercase identifier for the unique item "
        "(e.g. 'boots_zara', 'blazer_dry_cleaning', 'project_alpha'). "
        "Same real-world item across sessions MUST share the same canonical_key.\n"
        "Return STRICT JSON only, no markdown.\n\n"
        f"Question: {question}\n"
        f"Question Type: {question_type or 'unknown'}\n"
        f"Question Date: {question_date or 'unknown'}\n\n"
        "Candidate Context:\n"
        + "\n\n".join(candidate_blocks)
        + "\n\n"
        "Required JSON schema:\n"
        "{\"facts\":["
        "{"
        "\"session_id\":\"string\","
        "\"event_date\":\"YYYY-MM-DD or empty\","
        "\"subject\":\"string\","
        "\"predicate\":\"string\","
        "\"value\":\"string\","
        "\"numeric_value\":0,"
        "\"unit\":\"string\","
        "\"currency\":\"string\","
        "\"canonical_key\":\"unique_item_id (REQUIRED, lowercase, e.g. boots_zara)\","
        "\"relevant\":true"
        "}"
        "]}\n"
        "Return an empty list if nothing relevant: {\"facts\":[]}"
    )

    try:
        raw = str(llm.generate(prompt)).strip()
    except Exception as exc:
        logger.warning("Map-stage fact extraction failed: %s", exc)
        return []

    logger.info("Map-stage raw LLM response (first 800 chars): %s", raw[:800])
    payload = _extract_json_payload(raw)
    if payload is None:
        logger.warning("Map-stage payload parse failed. Raw response (first 500 chars): %s", raw[:500])
        return []

    if isinstance(payload, list):
        facts_raw = payload
    elif isinstance(payload, dict):
        facts_raw = payload.get("facts")
    else:
        facts_raw = None

    if not isinstance(facts_raw, list):
        logger.warning("Map-stage facts_raw is not a list: %s", type(facts_raw))
        return []

    logger.info("Map-stage extracted %d raw facts from LLM", len(facts_raw))
    facts: List[Dict[str, Any]] = []
    for row in facts_raw:
        if not isinstance(row, dict):
            continue
        value = str(row.get("value") or "").strip()
        subject = str(row.get("subject") or "").strip()
        predicate = str(row.get("predicate") or "").strip()
        if not value and not subject and not predicate:
            continue

        facts.append(
            {
                "session_id": str(row.get("session_id") or "").strip(),
                "event_date": str(row.get("event_date") or "").strip(),
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "numeric_value": _to_float(row.get("numeric_value")),
                "unit": str(row.get("unit") or "").strip().lower(),
                "currency": str(row.get("currency") or "").strip().upper(),
                "canonical_key": str(row.get("canonical_key") or "").strip(),
                "relevant": _normalize_bool(row.get("relevant", True)),
            }
        )

    logger.info("Map-stage final facts: %d (relevant=%d)", len(facts), sum(1 for f in facts if _normalize_bool(f.get("relevant", True))))
    if facts:
        for i, f in enumerate(facts[:5]):
            logger.info("  fact[%d]: subject=%s predicate=%s value=%s canonical_key=%s relevant=%s",
                        i, f.get("subject", ""), f.get("predicate", ""), f.get("value", ""), f.get("canonical_key", ""), f.get("relevant"))
    return facts


def _extract_money_value(text: str) -> Optional[float]:
    if not text:
        return None
    m = _MONEY_RE.search(text)
    if not m:
        return None
    whole = m.group(1).replace(",", "")
    frac = m.group(2)
    try:
        if frac:
            return float(f"{whole}.{frac}")
        return float(whole)
    except ValueError:
        return None


def _extract_duration_value(text: str) -> Optional[Tuple[float, str]]:
    if not text:
        return None
    m = _DURATION_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1)), m.group(2).lower()
    except ValueError:
        return None


def _normalize_unit(unit: str) -> str:
    unit = str(unit or "").strip().lower()
    if unit.endswith("s"):
        unit = unit[:-1]
    aliases = {
        "yr": "year",
        "yrs": "year",
        "hr": "hour",
        "hrs": "hour",
        "min": "minute",
        "mins": "minute",
    }
    return aliases.get(unit, unit)


def _duration_target_unit(question: str) -> str:
    q = str(question or "").lower()
    for unit in ("year", "month", "week", "day", "hour", "minute"):
        if re.search(rf"\b{unit}s?\b", q):
            return unit
    return "day"


def _convert_duration(value: float, src_unit: str, dst_unit: str) -> Optional[float]:
    src = _normalize_unit(src_unit)
    dst = _normalize_unit(dst_unit)
    to_days = {
        "year": 365.0,
        "month": 30.0,
        "week": 7.0,
        "day": 1.0,
        "hour": 1.0 / 24.0,
        "minute": 1.0 / 1440.0,
    }
    if src not in to_days or dst not in to_days:
        return None
    return (value * to_days[src]) / to_days[dst]


def reduce_atomic_facts(
    *,
    question: str,
    intent: AnswerIntent,
    facts: Sequence[Dict[str, Any]],
) -> Tuple[Optional[str], Dict[str, Any]]:
    relevant = [f for f in facts if _normalize_bool(f.get("relevant", True))]
    meta: Dict[str, Any] = {
        "fact_count": len(facts),
        "relevant_fact_count": len(relevant),
        "intent": intent.value,
    }
    if not relevant:
        return None, meta

    if intent == AnswerIntent.COUNT:
        # Superlative COUNT (argmax): "which X the most" → return the most frequent VALUE
        is_argmax = bool(_SUPERLATIVE_RE.search(question))
        if is_argmax:
            # Count occurrences of each distinct value
            value_counts: Dict[str, int] = {}
            value_display: Dict[str, str] = {}  # lowercase → original case
            for f in relevant:
                val = str(f.get("value") or "").strip()
                if not val:
                    continue
                key = val.lower()
                value_counts[key] = value_counts.get(key, 0) + 1
                if key not in value_display:
                    value_display[key] = val
            if not value_counts:
                return None, meta
            # Find the value with highest count
            best_key = max(value_counts, key=value_counts.get)
            best_count = value_counts[best_key]
            meta["argmax_value"] = value_display[best_key]
            meta["argmax_count"] = best_count
            meta["value_distribution"] = {
                value_display[k]: c for k, c in value_counts.items()
            }
            return value_display[best_key], meta

        # Standard COUNT: count unique items
        keys = set()
        for f in relevant:
            key = str(f.get("canonical_key") or "").strip().lower()
            if not key:
                # Build key from predicate + normalized value.
                # Include predicate so "return boots" != "pick up boots".
                val = str(f.get("value") or "").strip().lower()
                pred = str(f.get("predicate") or "").strip().lower()
                # Strip common prefixes that don't change identity
                for prefix in ("new ", "a ", "an ", "the ", "my ", "pair of ", "some "):
                    if val.startswith(prefix):
                        val = val[len(prefix):]
                parts = [p for p in (pred, val) if p]
                key = " | ".join(parts) if parts else ""
            if key:
                keys.add(key)
        if not keys:
            return None, meta
        meta["reduced_unique_keys"] = len(keys)
        return str(len(keys)), meta

    if intent == AnswerIntent.MONEY_SUM:
        values: List[float] = []
        for f in relevant:
            amount = _to_float(f.get("numeric_value"))
            if amount is None:
                amount = _extract_money_value(str(f.get("value") or ""))
            if amount is None:
                continue
            values.append(amount)
        if not values:
            return None, meta
        total = sum(values)
        meta["money_terms"] = len(values)
        if abs(total - round(total)) < 1e-9:
            return f"${int(round(total)):,}", meta
        return f"${total:,.2f}", meta

    if intent == AnswerIntent.DURATION:
        target = _duration_target_unit(question)
        values: List[float] = []
        for f in relevant:
            numeric = _to_float(f.get("numeric_value"))
            unit = _normalize_unit(str(f.get("unit") or ""))
            if numeric is None or not unit:
                parsed = _extract_duration_value(str(f.get("value") or ""))
                if parsed:
                    numeric, unit = parsed
                    unit = _normalize_unit(unit)
            if numeric is None or not unit:
                continue
            converted = _convert_duration(float(numeric), unit, target)
            if converted is None:
                continue
            values.append(converted)
        if not values:
            return None, meta
        total = sum(values)
        meta["duration_terms"] = len(values)
        rounded = round(total, 2)
        if abs(rounded - round(rounded)) < 1e-9:
            rounded = int(round(rounded))
        unit_out = target if rounded == 1 else f"{target}s"
        return f"{rounded} {unit_out}", meta

    if intent == AnswerIntent.LATEST:
        dated: List[Tuple[datetime, Dict[str, Any]]] = []
        for f in relevant:
            dt = _parse_event_datetime(f.get("event_date"))
            if dt is not None:
                dated.append((dt, f))
        if dated:
            dated.sort(key=lambda x: x[0], reverse=True)
            best = dated[0][1]
            answer = str(best.get("value") or "").strip()
            if answer:
                return answer, meta
        # fallback: first relevant value
        for f in relevant:
            answer = str(f.get("value") or "").strip()
            if answer:
                return answer, meta
        return None, meta

    if intent == AnswerIntent.SET_MEMBERS:
        values = []
        seen = set()
        for f in relevant:
            val = str(f.get("value") or "").strip()
            if not val:
                continue
            key = val.lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(val)
        if values:
            return ", ".join(values), meta
        return None, meta

    return None, meta


def _extract_numeric_mentions(text: str) -> List[float]:
    values: List[float] = []
    if not text:
        return values
    for match in _MONEY_RE.finditer(str(text)):
        whole = (match.group(1) or "").replace(",", "")
        frac = match.group(2)
        if not whole:
            continue
        try:
            number = float(f"{whole}.{frac}") if frac else float(whole)
        except ValueError:
            continue
        values.append(number)
    return values


def deterministic_inconsistency_check(
    *,
    question: str,
    intent: AnswerIntent,
    results: Sequence[Dict[str, Any]],
    coverage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Detect deterministic evidence inconsistencies before map/reduce.

    This is intentionally cheap and LLM-free.
    """
    reasons: List[str] = []
    coverage_payload = dict(coverage or {})
    coverage_sufficient = bool(coverage_payload.get("sufficient"))
    if not coverage_sufficient:
        reasons.append("coverage_insufficient")

    if not results:
        reasons.append("no_results")
        return {"inconsistent": True, "reasons": reasons}

    intent_value = intent.value if isinstance(intent, AnswerIntent) else str(intent or "")
    top_rows = list(results)[:12]

    if intent_value in {"count", "set_members"}:
        numeric_candidates = set()
        for row in top_rows:
            text = str(row.get("evidence_text") or row.get("memory") or "")
            for value in _extract_numeric_mentions(text):
                if value >= 0:
                    numeric_candidates.add(round(float(value), 3))
        if len(numeric_candidates) >= 2:
            reasons.append("count_numeric_conflict")

    if intent_value == "money_sum":
        amounts = set()
        for row in top_rows:
            text = str(row.get("evidence_text") or row.get("memory") or "")
            for value in _extract_numeric_mentions(text):
                amounts.add(round(float(value), 2))
        if len(amounts) >= 2:
            reasons.append("money_terms_multiple")

    if intent_value == "duration":
        units = set()
        for row in top_rows:
            text = str(row.get("evidence_text") or row.get("memory") or "")
            parsed = _extract_duration_value(text)
            if parsed:
                units.add(_normalize_unit(parsed[1]))
        if len(units) >= 2:
            reasons.append("duration_unit_mixed")

    if intent_value == "latest":
        dated_hits = int(coverage_payload.get("dated_fact_count", 0) or 0)
        if dated_hits <= 0:
            # fallback scan for explicit dates in evidence
            has_date_like = False
            for row in top_rows:
                text = str(row.get("evidence_text") or row.get("memory") or "")
                if re.search(r"\b\d{4}-\d{2}-\d{2}\b", text):
                    has_date_like = True
                    break
            if not has_date_like:
                reasons.append("latest_missing_dated_evidence")

    return {"inconsistent": bool(reasons), "reasons": reasons}


def render_fact_context(facts: Sequence[Dict[str, Any]], max_facts: int = 20) -> str:
    lines: List[str] = []
    for f in list(facts)[: max(1, int(max_facts))]:
        if not _normalize_bool(f.get("relevant", True)):
            continue
        parts = []
        if f.get("event_date"):
            parts.append(f"date={f['event_date']}")
        if f.get("session_id"):
            parts.append(f"session={f['session_id']}")
        label = " ".join(parts)
        value = str(f.get("value") or "").strip()
        subj = str(f.get("subject") or "").strip()
        pred = str(f.get("predicate") or "").strip()
        body = " | ".join(x for x in [subj, pred, value] if x)
        if not body:
            continue
        if label:
            lines.append(f"- [{label}] {body}")
        else:
            lines.append(f"- {body}")
    return "\n".join(lines)


def is_low_confidence_answer(answer: str) -> bool:
    return bool(_LOW_CONFIDENCE_RE.search(str(answer or "").strip()))


def should_override_with_reducer(intent: AnswerIntent) -> bool:
    return intent in _NUMERIC_INTENTS or intent == AnswerIntent.LATEST


# ---------------------------------------------------------------------------
# Event-first reducer — zero LLM cost
# ---------------------------------------------------------------------------


def reduce_from_episodic_events(
    *,
    question: str,
    intent: AnswerIntent,
    events: Sequence[Dict[str, Any]],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Reduce episodic events into an answer — zero LLM cost.

    Works directly from event dicts produced by episodic_index rather than
    LLM-extracted atomic facts.  Uses the same deterministic logic as
    reduce_atomic_facts() but adapted for the event schema.
    """
    meta: Dict[str, Any] = {
        "event_count": len(events),
        "intent": intent.value,
        "source": "episodic_events",
    }
    if not events:
        return None, meta

    if intent == AnswerIntent.COUNT:
        keys: set = set()
        for ev in events:
            key = str(ev.get("canonical_key") or "").strip().lower()
            if not key:
                value = str(ev.get("value_text") or "").strip().lower()
                if value:
                    key = value
            if key:
                keys.add(key)
        if not keys:
            return None, meta
        meta["reduced_unique_keys"] = len(keys)
        return str(len(keys)), meta

    if intent == AnswerIntent.MONEY_SUM:
        values: List[float] = []
        for ev in events:
            if str(ev.get("event_type") or "").lower() != "money":
                continue
            amount = _to_float(ev.get("value_num"))
            if amount is None:
                amount = _extract_money_value(str(ev.get("value_text") or ""))
            if amount is not None:
                values.append(amount)
        if not values:
            return None, meta
        total = sum(values)
        meta["money_terms"] = len(values)
        if abs(total - round(total)) < 1e-9:
            return f"${int(round(total)):,}", meta
        return f"${total:,.2f}", meta

    if intent == AnswerIntent.DURATION:
        target = _duration_target_unit(question)
        values = []
        for ev in events:
            if str(ev.get("event_type") or "").lower() != "duration":
                continue
            numeric = _to_float(ev.get("value_num"))
            unit = _normalize_unit(str(ev.get("value_unit") or ""))
            if numeric is None or not unit:
                parsed = _extract_duration_value(str(ev.get("value_text") or ""))
                if parsed:
                    numeric, unit = parsed
                    unit = _normalize_unit(unit)
            if numeric is None or not unit:
                continue
            converted = _convert_duration(float(numeric), unit, target)
            if converted is not None:
                values.append(converted)
        if not values:
            return None, meta
        total = sum(values)
        meta["duration_terms"] = len(values)
        rounded = round(total, 2)
        if abs(rounded - round(rounded)) < 1e-9:
            rounded = int(round(rounded))
        unit_out = target if rounded == 1 else f"{target}s"
        return f"{rounded} {unit_out}", meta

    if intent == AnswerIntent.LATEST:
        dated: List[Tuple[datetime, Dict[str, Any]]] = []
        for ev in events:
            dt = _parse_event_datetime(
                ev.get("normalized_time_start") or ev.get("event_time")
            )
            if dt is not None:
                dated.append((dt, ev))
        if dated:
            dated.sort(key=lambda x: x[0], reverse=True)
            best = dated[0][1]
            answer = str(best.get("value_text") or "").strip()
            if answer:
                return answer, meta
        # fallback: first event value
        for ev in events:
            answer = str(ev.get("value_text") or "").strip()
            if answer:
                return answer, meta
        return None, meta

    if intent == AnswerIntent.SET_MEMBERS:
        values_list: List[str] = []
        seen: set = set()
        for ev in events:
            val = str(ev.get("value_text") or "").strip()
            if not val:
                continue
            key = val.lower()
            if key in seen:
                continue
            seen.add(key)
            values_list.append(val)
        if values_list:
            return ", ".join(values_list), meta
        return None, meta

    return None, meta
