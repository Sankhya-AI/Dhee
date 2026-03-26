"""Deterministic episodic event extraction for memory indexing.

This module avoids LLM calls and derives structured events from memory text.
It is used to power low-cost event-first retrieval for counting, sums, and
recency-style questions.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence


_SESSION_ID_RE = re.compile(r"^Session ID:\s*(?P<session_id>\S+)\s*$", re.MULTILINE)
_SESSION_DATE_RE = re.compile(r"^Session Date:\s*(?P<session_date>.+?)\s*$", re.MULTILINE)
_TURN_RE = re.compile(r"^\s*(?P<speaker>[A-Za-z0-9_ .'-]{1,64}):\s*(?P<text>.+?)\s*$")
_MONEY_RE = re.compile(r"(?P<currency>\$)?\s*(?P<whole>\d{1,3}(?:,\d{3})*|\d+)(?:\.(?P<frac>\d+))?")
_MONEY_CONTEXT_RE = re.compile(
    r"\b(usd|dollars?|spent|spend|cost|price|paid|payment|bought|purchase|salary|income|budget|amount)\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>years?|months?|weeks?|days?|hours?|minutes?)",
    re.IGNORECASE,
)
_TRANSCRIPT_MARKER_RE = re.compile(r"^\s*User Transcript:\s*$", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")
_ENTITY_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b")

# ITEM events: Extract objects/things mentioned alongside action verbs.
# Pattern: "picked up [the] ITEM", "bought [a] ITEM", etc.
_ITEM_VERB_RE = re.compile(
    r"\b(picked up|bought|returned|grabbed|got|ordered|received|"
    r"exchanged|tried on|wore|cooked|made|played|watched|read|"
    r"visited|went to|started|finished|completed)\b\s+"
    r"(?:the |a |an |my |some )?(.+?)(?:\.|,|$|\band\b)",
    re.IGNORECASE,
)

# ACTION events: What the user/speaker did (intransitive verbs).
_ACTION_RE = re.compile(
    r"\b(I|we|user)\s+(went|traveled|visited|moved|started|"
    r"quit|changed|switched|joined|left|signed up|enrolled|"
    r"graduated|retired|married|divorced)\b",
    re.IGNORECASE,
)
_REL_YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)
_REL_TODAY_RE = re.compile(r"\btoday\b", re.IGNORECASE)
_REL_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_REL_LAST_WEEK_RE = re.compile(r"\b(last week|week before|the week before)\b", re.IGNORECASE)
_REL_NEXT_WEEK_RE = re.compile(r"\b(next week|coming week)\b", re.IGNORECASE)
_REL_LAST_MONTH_RE = re.compile(r"\b(last month|month before)\b", re.IGNORECASE)
_REL_NEXT_MONTH_RE = re.compile(r"\b(next month|coming month)\b", re.IGNORECASE)
_REL_LAST_YEAR_RE = re.compile(r"\b(last year|year before)\b", re.IGNORECASE)
_REL_NEXT_YEAR_RE = re.compile(r"\b(next year|coming year)\b", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "he",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "she",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "with",
        "you",
        "your",
    }
)


def _normalize_text(text: str) -> str:
    lowered = _NON_ALNUM_RE.sub(" ", str(text or "").lower())
    lowered = _WS_RE.sub(" ", lowered).strip()
    return lowered


def _canonical_phrase(text: str, *, max_terms: int = 12) -> str:
    normalized = _normalize_text(text)
    terms = [t for t in normalized.split() if t and t not in _STOPWORDS]
    if not terms:
        return ""
    return " ".join(terms[: max(1, int(max_terms))])


def normalize_actor_id(name: str) -> str:
    actor = _normalize_text(name)
    actor = actor.replace(" ", "_")
    actor = actor.strip("_")
    return actor or "unknown"


def _parse_event_time(value: Any) -> Optional[str]:
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
    return dt.isoformat()


def _parse_event_datetime(value: Any) -> Optional[datetime]:
    normalized = _parse_event_time(value)
    if not normalized:
        return None
    text = str(normalized)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _dt_iso(value: datetime) -> str:
    dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _day_bounds(dt: datetime) -> tuple[datetime, datetime]:
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = dt.replace(hour=23, minute=59, second=59, microsecond=0)
    return start, end


def _resolve_relative_time_range(text: str, event_time: Optional[str]) -> tuple[Optional[str], Optional[str], str]:
    base_dt = _parse_event_datetime(event_time)
    if base_dt is None:
        return (None, None, "unknown")

    lowered = str(text or "").lower()
    if _REL_YESTERDAY_RE.search(lowered):
        start_dt, end_dt = _day_bounds(base_dt - timedelta(days=1))
        return (_dt_iso(start_dt), _dt_iso(end_dt), "day")
    if _REL_TODAY_RE.search(lowered):
        start_dt, end_dt = _day_bounds(base_dt)
        return (_dt_iso(start_dt), _dt_iso(end_dt), "day")
    if _REL_TOMORROW_RE.search(lowered):
        start_dt, end_dt = _day_bounds(base_dt + timedelta(days=1))
        return (_dt_iso(start_dt), _dt_iso(end_dt), "day")
    if _REL_LAST_WEEK_RE.search(lowered):
        end_dt = base_dt
        start_dt = base_dt - timedelta(days=7)
        return (_dt_iso(start_dt), _dt_iso(end_dt), "week")
    if _REL_NEXT_WEEK_RE.search(lowered):
        start_dt = base_dt + timedelta(days=7)
        end_dt = base_dt + timedelta(days=14)
        return (_dt_iso(start_dt), _dt_iso(end_dt), "week")
    if _REL_LAST_MONTH_RE.search(lowered):
        end_dt = base_dt
        start_dt = base_dt - timedelta(days=30)
        return (_dt_iso(start_dt), _dt_iso(end_dt), "month")
    if _REL_NEXT_MONTH_RE.search(lowered):
        start_dt = base_dt + timedelta(days=30)
        end_dt = base_dt + timedelta(days=60)
        return (_dt_iso(start_dt), _dt_iso(end_dt), "month")
    if _REL_LAST_YEAR_RE.search(lowered):
        end_dt = base_dt
        start_dt = base_dt - timedelta(days=365)
        return (_dt_iso(start_dt), _dt_iso(end_dt), "year")
    if _REL_NEXT_YEAR_RE.search(lowered):
        start_dt = base_dt + timedelta(days=365)
        end_dt = base_dt + timedelta(days=730)
        return (_dt_iso(start_dt), _dt_iso(end_dt), "year")
    return (event_time, event_time, "instant")


def _derive_entity_key(actor_id: str, text: str) -> str:
    match = _ENTITY_NAME_RE.search(str(text or ""))
    if match:
        candidate = normalize_actor_id(match.group(1))
        if candidate and candidate != "unknown":
            return candidate
    actor = normalize_actor_id(actor_id)
    if actor and actor != "unknown":
        return actor
    phrase = _canonical_phrase(text, max_terms=4)
    return phrase or "unknown"


def _normalize_event_value(event: Dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "").strip().lower()
    value_text = str(event.get("value_text") or "").strip()
    if event_type == "money":
        value_num = event.get("value_num")
        if value_num is not None:
            try:
                amount = float(value_num)
                currency = str(event.get("currency") or "").strip().upper() or "USD"
                return f"{amount:.2f} {currency}"
            except (TypeError, ValueError):
                pass
    if event_type == "duration":
        value_num = event.get("value_num")
        unit = _duration_unit(str(event.get("value_unit") or ""))
        if value_num is not None and unit:
            try:
                return f"{float(value_num):g} {unit}"
            except (TypeError, ValueError):
                pass
    if value_text:
        phrase = _canonical_phrase(value_text, max_terms=16)
        if phrase:
            return phrase
        return value_text.lower()
    return ""


def _enrich_event_metadata(
    *,
    event: Dict[str, Any],
    actor_id: str,
    text: str,
    event_time: Optional[str],
) -> None:
    norm_start, norm_end, granularity = _resolve_relative_time_range(text=text, event_time=event_time)
    event["normalized_time_start"] = norm_start or event_time
    event["normalized_time_end"] = norm_end or event_time
    event["time_granularity"] = granularity
    event["entity_key"] = _derive_entity_key(actor_id=actor_id, text=text)
    event["value_norm"] = _normalize_event_value(event)


def _stable_event_id(
    *,
    memory_id: str,
    turn_id: int,
    event_type: str,
    canonical_key: str,
    value_text: str,
) -> str:
    raw = f"{memory_id}|{turn_id}|{event_type}|{canonical_key}|{value_text}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"{memory_id[:8]}_{digest[:24]}"


def _session_meta_from_content(content: str, metadata: Dict[str, Any]) -> Dict[str, str]:
    session_id = str(metadata.get("session_id") or "").strip()
    if not session_id:
        match = _SESSION_ID_RE.search(content)
        if match:
            session_id = match.group("session_id").strip()

    session_date = (
        str(metadata.get("event_time") or metadata.get("session_date") or metadata.get("event_date") or "").strip()
    )
    if not session_date:
        match = _SESSION_DATE_RE.search(content)
        if match:
            session_date = match.group("session_date").strip()

    return {"session_id": session_id or "unknown", "session_date": session_date}


def _speaker_lines(content: str) -> List[Dict[str, str]]:
    lines = [ln.rstrip() for ln in str(content or "").splitlines()]
    start_idx = 0
    for idx, line in enumerate(lines):
        if _TRANSCRIPT_MARKER_RE.match(line):
            start_idx = idx + 1
            break

    out: List[Dict[str, str]] = []
    for line in lines[start_idx:]:
        line = line.strip()
        if not line:
            continue
        match = _TURN_RE.match(line)
        if not match:
            continue
        speaker = match.group("speaker").strip()
        text = match.group("text").strip()
        if not text:
            continue
        out.append({"speaker": speaker or "unknown", "text": text})
    return out


def _duration_unit(unit: str) -> str:
    u = str(unit or "").strip().lower()
    if u.endswith("s"):
        u = u[:-1]
    aliases = {
        "yr": "year",
        "yrs": "year",
        "hr": "hour",
        "hrs": "hour",
        "min": "minute",
        "mins": "minute",
    }
    return aliases.get(u, u)


def _emit_utterance_event(
    *,
    memory_id: str,
    user_id: str,
    conversation_id: str,
    session_id: str,
    turn_id: int,
    actor_id: str,
    actor_role: str,
    event_time: Optional[str],
    text: str,
) -> Optional[Dict[str, Any]]:
    phrase = _canonical_phrase(text, max_terms=14)
    if not phrase:
        return None
    canonical_key = f"utterance:{actor_id}:{phrase}"
    return {
        "id": _stable_event_id(
            memory_id=memory_id,
            turn_id=turn_id,
            event_type="utterance",
            canonical_key=canonical_key,
            value_text=text,
        ),
        "memory_id": memory_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "event_time": event_time,
        "event_type": "utterance",
        "canonical_key": canonical_key,
        "value_text": text,
        "value_num": None,
        "value_unit": None,
        "currency": None,
        "confidence": 0.55,
        "superseded_by": None,
    }


def _emit_money_events(
    *,
    memory_id: str,
    user_id: str,
    conversation_id: str,
    session_id: str,
    turn_id: int,
    actor_id: str,
    actor_role: str,
    event_time: Optional[str],
    text: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for match in _MONEY_RE.finditer(text):
        start, end = match.span()
        currency_symbol = match.group("currency")
        context_window = text[max(0, start - 24): min(len(text), end + 24)]
        has_money_context = bool(currency_symbol == "$" or _MONEY_CONTEXT_RE.search(context_window))
        if not has_money_context:
            continue

        raw_whole = match.group("whole")
        if not raw_whole:
            continue
        try:
            whole = raw_whole.replace(",", "")
            frac = match.group("frac")
            value_num = float(f"{whole}.{frac}") if frac else float(whole)
        except ValueError:
            continue
        currency = "USD" if (currency_symbol == "$" or re.search(r"\b(usd|dollars?)\b", context_window, re.I)) else None
        value_text = match.group(0).strip()
        canonical_key = f"money:{actor_id}:{value_num:.2f}:{currency or ''}"
        out.append(
            {
                "id": _stable_event_id(
                    memory_id=memory_id,
                    turn_id=turn_id,
                    event_type="money",
                    canonical_key=canonical_key,
                    value_text=value_text,
                ),
                "memory_id": memory_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "event_time": event_time,
                "event_type": "money",
                "canonical_key": canonical_key,
                "value_text": value_text,
                "value_num": value_num,
                "value_unit": None,
                "currency": currency,
                "confidence": 0.9,
                "superseded_by": None,
            }
        )
    return out


def _emit_duration_events(
    *,
    memory_id: str,
    user_id: str,
    conversation_id: str,
    session_id: str,
    turn_id: int,
    actor_id: str,
    actor_role: str,
    event_time: Optional[str],
    text: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for match in _DURATION_RE.finditer(text):
        value_raw = match.group("value")
        unit_raw = match.group("unit")
        if not value_raw or not unit_raw:
            continue
        try:
            value_num = float(value_raw)
        except ValueError:
            continue
        unit = _duration_unit(unit_raw)
        value_text = match.group(0).strip()
        canonical_key = f"duration:{actor_id}:{value_num}:{unit}"
        out.append(
            {
                "id": _stable_event_id(
                    memory_id=memory_id,
                    turn_id=turn_id,
                    event_type="duration",
                    canonical_key=canonical_key,
                    value_text=value_text,
                ),
                "memory_id": memory_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "event_time": event_time,
                "event_type": "duration",
                "canonical_key": canonical_key,
                "value_text": value_text,
                "value_num": value_num,
                "value_unit": unit,
                "currency": None,
                "confidence": 0.9,
                "superseded_by": None,
            }
        )
    return out


def _emit_item_events(
    *,
    memory_id: str,
    user_id: str,
    conversation_id: str,
    session_id: str,
    turn_id: int,
    actor_id: str,
    actor_role: str,
    event_time: Optional[str],
    text: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_phrases: set = set()

    def _add_item(raw_value: str, confidence: float = 0.65) -> None:
        phrase = _canonical_phrase(raw_value.strip(), max_terms=10)
        if not phrase or phrase in seen_phrases:
            return
        seen_phrases.add(phrase)
        canonical_key = f"item:{actor_id}:{phrase}"
        out.append(
            {
                "id": _stable_event_id(
                    memory_id=memory_id,
                    turn_id=turn_id,
                    event_type="item",
                    canonical_key=canonical_key,
                    value_text=raw_value.strip(),
                ),
                "memory_id": memory_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "event_time": event_time,
                "event_type": "item",
                "canonical_key": canonical_key,
                "value_text": raw_value.strip(),
                "value_num": None,
                "value_unit": None,
                "currency": None,
                "confidence": confidence,
                "superseded_by": None,
            }
        )

    # Strategy 1: Comma-separated list items (existing behavior).
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        parts = [p for p in parts if p and len(p) <= 80]
        if len(parts) >= 2:
            for part in parts[:12]:
                _add_item(part, confidence=0.65)

    # Strategy 2: Verb-object patterns ("picked up a dress", "bought shoes").
    for match in _ITEM_VERB_RE.finditer(text):
        obj = match.group(2).strip()
        if obj and 1 < len(obj) <= 80:
            _add_item(obj, confidence=0.75)

    return out


def _emit_action_events(
    *,
    memory_id: str,
    user_id: str,
    conversation_id: str,
    session_id: str,
    turn_id: int,
    actor_id: str,
    actor_role: str,
    event_time: Optional[str],
    text: str,
) -> List[Dict[str, Any]]:
    """Extract action events — what the user/speaker did."""
    out: List[Dict[str, Any]] = []
    for match in _ACTION_RE.finditer(text):
        action_verb = match.group(2).strip().lower()
        # Use a wider context window after the verb for the action object.
        end_pos = match.end()
        remaining = text[end_pos:end_pos + 80].strip()
        # Take up to the first sentence boundary.
        obj_match = re.match(r"\s*(.+?)(?:\.|,|$)", remaining)
        action_object = obj_match.group(1).strip() if obj_match else ""
        value_text = f"{action_verb} {action_object}".strip() if action_object else action_verb
        phrase = _canonical_phrase(value_text, max_terms=10)
        if not phrase:
            continue
        canonical_key = f"action:{actor_id}:{phrase}"
        out.append(
            {
                "id": _stable_event_id(
                    memory_id=memory_id,
                    turn_id=turn_id,
                    event_type="action",
                    canonical_key=canonical_key,
                    value_text=value_text,
                ),
                "memory_id": memory_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "event_time": event_time,
                "event_type": "action",
                "canonical_key": canonical_key,
                "value_text": value_text,
                "value_num": None,
                "value_unit": None,
                "currency": None,
                "confidence": 0.70,
                "superseded_by": None,
            }
        )
    return out


def extract_episodic_events(
    *,
    memory_id: str,
    user_id: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    md = dict(metadata or {})
    session_meta = _session_meta_from_content(content, md)
    session_id = session_meta["session_id"]
    event_time = _parse_event_time(session_meta["session_date"])
    conversation_id = str(md.get("conversation_id") or md.get("sample_id") or user_id or "default")

    speaker_rows = _speaker_lines(content)
    if not speaker_rows:
        fallback_actor = normalize_actor_id(str(md.get("actor_id") or md.get("speaker") or "unknown"))
        speaker_rows = [{"speaker": fallback_actor, "text": str(content or "").strip()}]

    events: List[Dict[str, Any]] = []
    seen_keys = set()

    for idx, row in enumerate(speaker_rows, start=1):
        speaker = row.get("speaker", "unknown")
        text = row.get("text", "")
        actor_id = normalize_actor_id(speaker)
        actor_role = str(speaker or "unknown")
        if not text:
            continue

        utter = _emit_utterance_event(
            memory_id=memory_id,
            user_id=user_id,
            conversation_id=conversation_id,
            session_id=session_id,
            turn_id=idx,
            actor_id=actor_id,
            actor_role=actor_role,
            event_time=event_time,
            text=text,
        )
        if utter:
            _enrich_event_metadata(
                event=utter,
                actor_id=actor_id,
                text=text,
                event_time=event_time,
            )
            key = (
                utter["event_type"],
                utter["canonical_key"],
                utter.get("value_text"),
                utter.get("turn_id"),
            )
            if key not in seen_keys:
                seen_keys.add(key)
                events.append(utter)

        emitted = []
        emitted.extend(
            _emit_money_events(
                memory_id=memory_id,
                user_id=user_id,
                conversation_id=conversation_id,
                session_id=session_id,
                turn_id=idx,
                actor_id=actor_id,
                actor_role=actor_role,
                event_time=event_time,
                text=text,
            )
        )
        emitted.extend(
            _emit_duration_events(
                memory_id=memory_id,
                user_id=user_id,
                conversation_id=conversation_id,
                session_id=session_id,
                turn_id=idx,
                actor_id=actor_id,
                actor_role=actor_role,
                event_time=event_time,
                text=text,
            )
        )
        emitted.extend(
            _emit_item_events(
                memory_id=memory_id,
                user_id=user_id,
                conversation_id=conversation_id,
                session_id=session_id,
                turn_id=idx,
                actor_id=actor_id,
                actor_role=actor_role,
                event_time=event_time,
                text=text,
            )
        )
        emitted.extend(
            _emit_action_events(
                memory_id=memory_id,
                user_id=user_id,
                conversation_id=conversation_id,
                session_id=session_id,
                turn_id=idx,
                actor_id=actor_id,
                actor_role=actor_role,
                event_time=event_time,
                text=text,
            )
        )

        for event in emitted:
            event_value_text = str(event.get("value_text") or text)
            _enrich_event_metadata(
                event=event,
                actor_id=actor_id,
                text=event_value_text,
                event_time=event_time,
            )
            key = (
                event["event_type"],
                event["canonical_key"],
                event.get("value_text"),
                event.get("turn_id"),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(event)

    return events


def _coarse_entity_key(event: Dict[str, Any]) -> str:
    """Normalize event into a coarse entity key for aggregation.

    Strips actor prefix from canonical_key, extracts the activity noun,
    lowercases, and removes stopwords.
    """
    canonical = str(event.get("canonical_key") or "").strip()
    # Strip the type:actor: prefix  (e.g. "duration:user:3.0:day" -> "3.0:day")
    parts = canonical.split(":", 2)
    if len(parts) >= 3:
        suffix = parts[2]  # everything after type:actor:
    elif len(parts) == 2:
        suffix = parts[1]
    else:
        suffix = canonical

    # For duration/money events, try to derive the activity from surrounding text
    event_type = str(event.get("event_type") or "").lower()
    value_text = str(event.get("value_text") or "").lower()
    entity_key = str(event.get("entity_key") or "").lower()

    if event_type in ("duration", "money"):
        # Use entity_key if it looks meaningful (not just "unknown" or an actor name)
        if entity_key and entity_key not in ("unknown", "user", "assistant"):
            key = entity_key.replace(" ", "_")
        else:
            # Try to extract the activity noun from the surrounding context
            # Look at the full canonical_key for context
            key = suffix.replace(":", "_")
        return _normalize_text(key).replace(" ", "_").strip("_") or "unknown"

    if event_type == "item":
        # For items, group by the action verb context
        if entity_key and entity_key not in ("unknown", "user", "assistant"):
            return _normalize_text(entity_key).replace(" ", "_").strip("_") or "items"
        return "items"

    if event_type == "action":
        # For actions, use the canonical phrase
        phrase = _canonical_phrase(value_text, max_terms=4)
        return phrase.replace(" ", "_") if phrase else "actions"

    return _normalize_text(entity_key or "unknown").replace(" ", "_").strip("_") or "unknown"


def extract_entity_aggregates(
    events: List[Dict[str, Any]],
    session_id: str,
    memory_id: str,
) -> List[Dict[str, Any]]:
    """Extract entity aggregates from episodic events for write-time accumulation.

    Reuses the events already extracted by extract_episodic_events() (no extra cost).
    Returns a list of aggregate update dicts, each with:
        entity_key, agg_type, value_delta, value_unit, session_id, memory_id
        (plus item_value for item_set type)
    """
    aggregates: List[Dict[str, Any]] = []

    # Group duration events by activity noun -> duration_sum
    duration_accum: Dict[str, Dict[str, Any]] = {}
    # Group money events by context entity -> money_sum
    money_accum: Dict[str, Dict[str, Any]] = {}
    # Group item events -> item_set (unique items)
    item_accum: Dict[str, List[str]] = {}
    # Group action events -> count
    action_accum: Dict[str, int] = {}

    for event in events:
        event_type = str(event.get("event_type") or "").lower()
        coarse_key = _coarse_entity_key(event)

        if event_type == "duration":
            value_num = event.get("value_num")
            if value_num is None:
                continue
            try:
                val = float(value_num)
            except (TypeError, ValueError):
                continue
            unit = _duration_unit(str(event.get("value_unit") or ""))
            if coarse_key not in duration_accum:
                duration_accum[coarse_key] = {"total": 0.0, "unit": unit}
            duration_accum[coarse_key]["total"] += val
            # Keep most specific unit
            if unit and not duration_accum[coarse_key]["unit"]:
                duration_accum[coarse_key]["unit"] = unit

        elif event_type == "money":
            value_num = event.get("value_num")
            if value_num is None:
                continue
            try:
                val = float(value_num)
            except (TypeError, ValueError):
                continue
            currency = str(event.get("currency") or "USD")
            if coarse_key not in money_accum:
                money_accum[coarse_key] = {"total": 0.0, "currency": currency}
            money_accum[coarse_key]["total"] += val

        elif event_type == "item":
            value_text = str(event.get("value_text") or "").strip()
            if not value_text:
                continue
            normalized_item = _canonical_phrase(value_text, max_terms=10)
            if not normalized_item:
                continue
            if coarse_key not in item_accum:
                item_accum[coarse_key] = []
            if normalized_item not in item_accum[coarse_key]:
                item_accum[coarse_key].append(normalized_item)

        elif event_type == "action":
            if coarse_key not in action_accum:
                action_accum[coarse_key] = 0
            action_accum[coarse_key] += 1

    # Emit duration aggregates
    for key, data in duration_accum.items():
        aggregates.append({
            "entity_key": key,
            "agg_type": "duration_sum",
            "value_delta": data["total"],
            "value_unit": data["unit"] or None,
            "session_id": session_id,
            "memory_id": memory_id,
        })

    # Emit money aggregates
    for key, data in money_accum.items():
        aggregates.append({
            "entity_key": key,
            "agg_type": "money_sum",
            "value_delta": data["total"],
            "value_unit": data["currency"],
            "session_id": session_id,
            "memory_id": memory_id,
        })

    # Emit item_set aggregates (one per unique item)
    for key, items in item_accum.items():
        for item in items:
            aggregates.append({
                "entity_key": key,
                "agg_type": "item_set",
                "item_value": item,
                "value_delta": 1,
                "value_unit": None,
                "session_id": session_id,
                "memory_id": memory_id,
            })

    # Emit action count aggregates
    for key, count in action_accum.items():
        aggregates.append({
            "entity_key": key,
            "agg_type": "count",
            "value_delta": count,
            "value_unit": None,
            "session_id": session_id,
            "memory_id": memory_id,
        })

    return aggregates


def tokenize_query_terms(query: str) -> List[str]:
    normalized = _normalize_text(query)
    terms = [term for term in normalized.split() if len(term) > 2 and term not in _STOPWORDS]
    # Preserve order while de-duplicating.
    seen = set()
    out: List[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def score_event_match(event: Dict[str, Any], query_terms: Sequence[str]) -> float:
    if not query_terms:
        return 1.0
    haystack = " ".join(
        [
            str(event.get("event_type") or ""),
            str(event.get("canonical_key") or ""),
            str(event.get("value_text") or ""),
            str(event.get("actor_id") or ""),
            str(event.get("actor_role") or ""),
        ]
    ).lower()
    score = 0.0
    for term in query_terms:
        if term and term in haystack:
            score += 1.0
    return score


def intent_event_types(intent_value: str) -> Optional[Iterable[str]]:
    intent = str(intent_value or "").strip().lower()
    if intent == "money_sum":
        return ("money",)
    if intent == "duration":
        return ("duration",)
    if intent == "latest":
        return None
    if intent in {"count", "set_members"}:
        return ("item", "action", "utterance")
    return None
