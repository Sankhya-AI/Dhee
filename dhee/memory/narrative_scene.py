from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback kept harmless.
    ZoneInfo = None  # type: ignore

from dhee.schemas.narrative import NarrativePriorModel, NarrativeRollupModel
from dhee.schemas.scene_card import SceneCardModel

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{2,}")
_STOP_WORDS = {
    "about", "after", "agent", "also", "and", "are", "because", "before",
    "being", "build", "can", "context", "dhee", "for", "from", "has",
    "have", "into", "memory", "more", "not", "now", "only", "repo",
    "scene", "should", "that", "the", "their", "then", "this", "use",
    "user", "was", "when", "with", "work", "will", "you", "your",
}

DEFAULT_SERIES_ID = "series_default_cto_arc"
DEFAULT_HERO_ID = "character_user_default"
DEFAULT_TIMEZONE = "Asia/Kolkata"
CHOTU_SCENE_CONSOLIDATION_SCHEMA_VERSION = "chotu.dhee_scene_consolidation_input.v1"
ROLLUP_PROMPT_VERSION = "dhee.narrative_rollup.v1"
DEFAULT_ROLLUP_MODEL = "google/gemma-4-31b-it"
SERIES_ESCALATION_ROLLUP_MODEL = "moonshotai/kimi-k2.6"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_id(prefix: str, payload: Any, length: int = 16) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:length]}"


def _tokens(text: str) -> List[str]:
    out = []
    for match in _TOKEN_RE.findall(text or ""):
        token = match.lower()
        if token not in _STOP_WORDS:
            out.append(token)
    return out


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _normalize_categories(categories: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for item in categories or []:
        value = str(item or "").strip().lower().replace("-", "_")
        if value and value not in out:
            out.append(value)
    return out


def _as_text_list(value: Any, limit: int = 12, item_limit: int = 320) -> List[str]:
    items = value if isinstance(value, list) else []
    out: List[str] = []
    for item in items:
        text = _clip(item, item_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _as_dict_list(value: Any, limit: int = 12) -> List[Dict[str, Any]]:
    items = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            out.append(dict(item))
        if len(out) >= limit:
            break
    return out


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cosine_similarity(left: Any, right: Any) -> float:
    try:
        left_values = [float(item) for item in left or []]
        right_values = [float(item) for item in right or []]
    except (TypeError, ValueError):
        return 0.0
    if not left_values or not right_values or len(left_values) != len(right_values):
        return 0.0
    dot = sum(a * b for a, b in zip(left_values, right_values))
    left_norm = math.sqrt(sum(a * a for a in left_values))
    right_norm = math.sqrt(sum(b * b for b in right_values))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _append_unique(items: Any, additions: List[Any], limit: int = 40) -> List[Any]:
    out = list(items or [])
    for item in additions:
        if item is None or item == "":
            continue
        if item not in out:
            out.append(item)
    return out[-limit:]


def _default_series_id(user_id: str) -> str:
    if user_id in {"", "default"}:
        return DEFAULT_SERIES_ID
    return _stable_id("series_cto_arc", {"user_id": user_id}, 18)


def _default_hero_id(user_id: str) -> str:
    if user_id in {"", "default"}:
        return DEFAULT_HERO_ID
    return _stable_id("character_user", {"user_id": user_id}, 18)


def _short_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested_dict(value: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _evidence_item_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else {}


def _evidence_summary(item: Dict[str, Any], limit: int = 700) -> str:
    for key in ("summary", "content", "title"):
        if item.get(key):
            return _clip(item.get(key), limit)
    payload = _evidence_item_payload(item)
    for key in ("summary", "outcome", "status"):
        if payload.get(key):
            return _clip(payload.get(key), limit)
    worker = _nested_dict(payload, "final_outcome", "worker_result")
    if worker.get("summary"):
        return _clip(worker.get("summary"), limit)
    return _clip(f"{item.get('kind') or item.get('source') or 'scene evidence'}:{_short_hash(item)}", limit)


def _payload_event_summary(payload: Dict[str, Any], event_type: str) -> str:
    for key in ("summary", "content", "title"):
        if payload.get(key):
            return _clip(payload.get(key), 700)
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    for key in ("summary", "content", "title", "status", "phase"):
        if nested.get(key):
            return _clip(nested.get(key), 700)
    return _clip(f"{event_type or 'scene_event'} payload {_short_hash(payload)}", 700)


def _evidence_ref(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = _evidence_item_payload(item)
    ref = (
        item.get("evidence_ref")
        or item.get("ref")
        or item.get("path")
        or item.get("id")
        or payload.get("id")
        or f"evidence:{_short_hash(item)}"
    )
    return {
        "kind": item.get("kind") or item.get("source") or "scene_end_evidence",
        "ref": str(ref),
        "label": _evidence_summary(item, limit=140),
    }


def _consolidation_payloads(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    for item in evidence:
        payload = _evidence_item_payload(item)
        if (
            item.get("kind") == "chotu_scene_consolidation_input"
            or payload.get("schema_version") == CHOTU_SCENE_CONSOLIDATION_SCHEMA_VERSION
        ):
            payloads.append(payload)
    return payloads


def _quality_gate_passed(payload: Dict[str, Any]) -> bool:
    gate = _nested_dict(payload, "final_outcome", "latest_quality_gate")
    return bool(gate.get("passed") or gate.get("status") in {"passed", "ready", "completed"})


def _local_date(timezone_name: str) -> str:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).date().isoformat()


class NarrativeSceneService:
    """SQLite-backed scene intelligence service used by Chotu-facing MCP tools."""

    def __init__(
        self,
        db: Any,
        default_timezone: str = DEFAULT_TIMEZONE,
        embedder: Optional[Any] = None,
        reranker: Optional[Any] = None,
        create_default_reranker: bool = False,
        rollup_llm: Optional[Any] = None,
        create_default_rollup_llm: bool = False,
    ) -> None:
        self.db = db
        self.default_timezone = default_timezone
        self.embedder = embedder
        self._reranker = reranker
        self._create_default_reranker = create_default_reranker
        self._rollup_llm = rollup_llm
        self._create_default_rollup_llm = create_default_rollup_llm
        self._rollup_llms: Dict[str, Any] = {}

    def ensure_default_series(
        self,
        user_id: str = "default",
        namespace: str = "personal",
    ) -> Dict[str, Any]:
        series_id = _default_series_id(user_id)
        existing = self.db.get_series(series_id)
        if existing:
            return existing
        return self.db.upsert_series(
            {
                "id": series_id,
                "user_id": user_id,
                "namespace": "personal",
                "title": "Successful CTO arc",
                "theme": "Become a successful CTO",
                "ultimate_goal": (
                    "Become a successful CTO or founder-architect who can build "
                    "and lead ambitious cognitive systems."
                ),
                "hero_identity": "builder learning to become a CTO-level architect",
                "purpose": (
                    "Build technical, product, leadership, and execution depth "
                    "toward CTO/founder-architect capability."
                ),
                "desired_identity": "successful_cto",
                "core_values": [
                    "technical_depth",
                    "taste",
                    "reliability",
                    "execution",
                    "leadership",
                ],
                "long_term_conflicts": [
                    "turning scattered agent experiments into durable systems",
                    "countering LLM unpredictability with structured context and verification",
                    "moving from builder mode into CTO-level architecture and leadership",
                ],
                "status": "active",
                "confidence": 0.9,
            }
        )

    def ensure_active_season(
        self,
        series_id: str,
        user_id: str,
        namespace: str,
        local_date: str,
    ) -> Dict[str, Any]:
        existing = self.db.get_active_season(series_id, user_id=user_id, namespace=namespace)
        if existing:
            return existing
        year = (local_date or datetime.now(timezone.utc).date().isoformat())[:4]
        season_id = _stable_id("season", {"series_id": series_id, "namespace": namespace, "year": year}, 14)
        season = self.db.upsert_season(
            {
                "id": season_id,
                "series_id": series_id,
                "user_id": user_id,
                "namespace": namespace,
                "title": "Chotu and Dhee native runtime season",
                "theme": "Turn memory and coding agents into one native execution loop.",
                "major_goal": (
                    "Make Chotu a strong coding-agent runtime that uses Dhee as a "
                    "scene-first narrative memory substrate."
                ),
                "dominant_struggle": (
                    "Avoid prompt sludge and weak stateless agent behavior while "
                    "preserving proof gates for real code changes."
                ),
                "transformation_expected": (
                    "Move from memory as recall into memory as a predictive story-state world model."
                ),
                "open_threads": [
                    "Implement normalized SceneCard retrieval",
                    "Attach Chotu runtime calls to scene_context",
                    "Keep code mutation gated by proof bundles",
                ],
                "arc_summary": "Move from tools and memories toward a scene-first agent operating system.",
                "period_start": local_date,
                "status": "active",
                "confidence": 0.86,
            }
        )
        series = self.db.get_series(series_id)
        if series:
            series["current_active_season"] = season["id"]
            self.db.upsert_series(series)
        return season

    def ensure_hero(
        self,
        user_id: str,
        namespace: str = "personal",
        hero_character_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if hero_character_id:
            existing = self.db.get_story_character(hero_character_id)
            if existing:
                return existing
        hero_id = _default_hero_id(user_id)
        existing = self.db.get_story_character(hero_id)
        if existing:
            return existing
        return self.db.upsert_story_character(
            {
                "id": hero_id,
                "user_id": user_id,
                "namespace": "personal",
                "name": "user",
                "character_type": "person",
                "stable_identity_ref": user_id,
                "description": "The hero whose long-term CTO/founder-architect arc Dhee is helping.",
                "skills": ["architecture", "product_judgment", "coding", "leadership"],
                "influence": 1.0,
                "trust_level": 0.9,
            }
        )

    def ensure_today_episode(
        self,
        *,
        series: Dict[str, Any],
        season: Dict[str, Any],
        hero: Dict[str, Any],
        user_id: str,
        namespace: str,
        timezone_name: Optional[str] = None,
        agent_id: Optional[str] = None,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        query: str = "",
    ) -> Dict[str, Any]:
        tz = timezone_name or self.default_timezone
        today = _local_date(tz)
        return self.db.upsert_episode(
            {
                "id": _stable_id(
                    "episode",
                    {"user_id": user_id, "namespace": namespace, "date": today, "tz": tz},
                    18,
                ),
                "series_id": series["id"],
                "season_id": season["id"],
                "user_id": user_id,
                "namespace": namespace,
                "local_date": today,
                "timezone": tz,
                "title": title or f"{today} agent episode",
                "summary": summary or "Daily collection of Dhee scene intelligence.",
                "primary_hero_id": hero["id"],
                "goal": query or title or "Advance the active agent task.",
                "conflict": "Current LLM behavior can drift without stable story-state memory.",
                "agent_ids": [agent_id] if agent_id else [],
                "status": "open",
            }
        )

    def scene_start(
        self,
        *,
        user_id: str = "default",
        agent_id: str = "agent",
        agent_category: str = "agent",
        source_app: str = "dhee",
        namespace: str = "default",
        series_id: Optional[str] = None,
        season_id: Optional[str] = None,
        hero_character_id: Optional[str] = None,
        timezone_name: Optional[str] = None,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        query: str = "",
        intent_type: str = "question_answer",
        action_lane: str = "answer",
        categories: Optional[List[str]] = None,
        markers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tz = timezone_name or self.default_timezone
        today = _local_date(tz)
        series = self.db.get_series(series_id) if series_id else None
        if not series:
            series = self.ensure_default_series(user_id=user_id)
        hero = self.ensure_hero(user_id=user_id, hero_character_id=hero_character_id)
        season = self.db.get_season(season_id) if season_id else None
        if not season:
            season = self.ensure_active_season(series["id"], user_id, namespace, today)

        episode = self.ensure_today_episode(
            series=series,
            season=season,
            hero=hero,
            user_id=user_id,
            namespace=namespace,
            timezone_name=tz,
            agent_id=agent_id,
            title=title,
            summary=summary,
            query=query,
        )

        scene_id = _stable_id(
            "scene",
            {"episode": episode["id"], "agent": agent_id, "query": query, "time": _now_iso()},
            18,
        )
        now = _now_iso()
        scene_id = self.db.add_scene(
            {
                "id": scene_id,
                "user_id": user_id,
                "title": title or _clip(query, 90) or "Dhee scene",
                "summary": summary or _clip(query, 360) or "Scene started.",
                "topic": _clip(" ".join(_tokens(" ".join([query, title or ""]))[:6]), 120),
                "participants": [agent_id, hero["id"]],
                "memory_ids": [],
                "start_time": now,
                "namespace": namespace,
            }
        )
        categories_norm = _normalize_categories(categories)
        markers_norm = dict(markers or {})
        markers_norm.setdefault("intent_type", intent_type)
        markers_norm.setdefault("action_lane", action_lane)
        self.db.update_scene(
            scene_id,
            {
                "episode_id": episode["id"],
                "agent_id": agent_id,
                "agent_category": agent_category,
                "source_app": source_app,
                "hero_character_id": hero["id"],
                "hero_focus": "User as CTO/founder-architect advancing the active arc.",
                "intent_type": intent_type,
                "action_lane": action_lane,
                "action": query or title or "",
                "obstacle": "LLM actions need stable context and proof gates.",
                "outcome_status": "partial",
                "importance": 0.6,
                "confidence": 0.75,
                "visibility_scope": "category",
                "privacy_class": "user_private",
                "created_at": now,
                "updated_at": now,
            },
        )
        self.db.replace_scene_categories(scene_id, categories_norm, source="scene_start")
        self.db.replace_scene_markers(scene_id, markers_norm, source="scene_start")
        self.db.upsert_episode_character(
            {
                "episode_id": episode["id"],
                "character_id": hero["id"],
                "role": "hero",
                "relationship_to_hero": "self",
                "salience": 1.0,
            }
        )
        self.db.upsert_scene_character(
            {
                "scene_id": scene_id,
                "character_id": hero["id"],
                "role": "hero",
                "contribution": "Primary arc owner for this scene.",
                "salience": 1.0,
            }
        )
        return {
            "format": "dhee_scene_start.v1",
            "series": series,
            "season": season,
            "episode": episode,
            "hero": hero,
            "scene": self.db.get_scene(scene_id),
        }

    def scene_event(
        self,
        *,
        scene_id: str,
        event_type: str = "observation",
        summary: str = "",
        evidence_ref: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not scene_id:
            return {"error": "scene_id is required"}
        event_payload = payload if isinstance(payload, dict) else {}
        if not summary and event_payload:
            summary = _payload_event_summary(event_payload, event_type)
        if not summary and not evidence_ref:
            return {"error": "summary, evidence_ref, or payload is required"}
        event_metadata = dict(metadata or {})
        if event_payload:
            event_metadata["payload"] = event_payload
            event_metadata["raw_transcript_included"] = False
        event = self.db.add_scene_event(
            {
                "scene_id": scene_id,
                "event_type": event_type,
                "summary": _clip(summary or evidence_ref, 700),
                "evidence_ref": evidence_ref,
                "metadata": event_metadata,
            }
        )
        self.db.update_scene(scene_id, {"updated_at": _now_iso()})
        return {"format": "dhee_scene_event.v1", "event": event}

    def scene_end(
        self,
        *,
        scene_id: str,
        outcome: str = "",
        outcome_status: str = "success",
        story_progress_delta: str = "",
        skip_reason: Optional[str] = None,
        durable_facts: Optional[List[str]] = None,
        decisions: Optional[List[str]] = None,
        procedures: Optional[List[str]] = None,
        success_patterns: Optional[List[str]] = None,
        failure_patterns: Optional[List[str]] = None,
        open_loops: Optional[List[str]] = None,
        entities: Optional[List[Dict[str, Any]]] = None,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        importance: Optional[float] = None,
        confidence: Optional[float] = None,
        reuse_policy: Optional[str] = None,
        visibility_scope: Optional[str] = None,
        privacy_class: Optional[str] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        promote_durable_facts: bool = False,
    ) -> Dict[str, Any]:
        scene = self.db.get_scene(scene_id)
        if not scene:
            return {"error": "scene not found"}
        now = _now_iso()
        if skip_reason:
            self.db.update_scene(
                scene_id,
                {
                    "end_time": now,
                    "result": skip_reason,
                    "outcome": outcome or "skipped",
                    "outcome_status": "partial",
                    "story_progress_delta": story_progress_delta,
                    "updated_at": now,
                },
            )
            return {"format": "dhee_scene_end.v1", "scene_id": scene_id, "skip_reason": skip_reason}

        events = self.db.get_scene_events(scene_id)
        categories = self.db.get_scene_categories(scene_id)
        markers = self.db.get_scene_markers(scene_id)
        extra_evidence = _as_dict_list(evidence, limit=20)
        consolidation_payloads = _consolidation_payloads(extra_evidence)
        event_summaries = [event["summary"] for event in events if event.get("summary")]
        evidence_summaries = [_evidence_summary(item) for item in extra_evidence if _evidence_summary(item)]
        summary = _clip(
            " ".join(event_summaries + evidence_summaries)
            or scene.get("summary")
            or scene.get("title")
            or "Scene completed.",
            1200,
        )
        retrieval_tags = _normalize_categories(
            categories + _tokens(" ".join([summary, scene.get("title") or "", scene.get("action") or ""]))[:12]
        )
        explicit_durable_facts = _as_text_list(durable_facts, limit=10, item_limit=500)
        evidence_refs = [
            {
                "kind": event.get("event_type", "event"),
                "ref": event.get("evidence_ref") or event.get("id"),
                "label": _clip(event.get("summary"), 120),
            }
            for event in events[:8]
        ]
        evidence_refs.extend(_evidence_ref(item) for item in extra_evidence[:12])
        for payload in consolidation_payloads:
            for ref in payload.get("evidence_refs") or []:
                if isinstance(ref, dict):
                    evidence_refs.append(
                        {
                            "kind": ref.get("kind") or "consolidation_ref",
                            "ref": ref.get("path") or ref.get("id") or ref.get("ref") or f"ref:{_short_hash(ref)}",
                            "label": ref.get("label") or ref.get("kind") or "scene consolidation evidence",
                        }
                    )
        explicit_decisions = _as_text_list(decisions, limit=10, item_limit=320)
        if outcome:
            explicit_decisions.insert(0, _clip(outcome, 240))
        explicit_procedures = _as_text_list(procedures, limit=10, item_limit=360)
        if markers.get("action_lane", [""])[0] == "code_mutation":
            explicit_procedures.append(
                "Use SceneCards as priors only; require task contract and proof bundle for mutation."
            )
        for payload in consolidation_payloads:
            truth = payload.get("truth_model") if isinstance(payload.get("truth_model"), dict) else {}
            prompt = payload.get("prompt_causality") if isinstance(payload.get("prompt_causality"), dict) else {}
            if truth.get("thinking_events_are_provisional"):
                explicit_procedures.append(
                    "Treat Kimi thinking and tool calls as provisional evidence until scene-end consolidation."
                )
            if prompt.get("enabled") or prompt.get("prompt_sha256"):
                explicit_procedures.append(
                    "Correlate prompt section hashes with final outcome before changing Kimi prompts."
                )
        explicit_success_patterns = _as_text_list(success_patterns, limit=10, item_limit=360)
        if not explicit_success_patterns:
            explicit_success_patterns = ["Retrieve compact SceneCards before raw evidence."]
        for payload in consolidation_payloads:
            if _quality_gate_passed(payload):
                explicit_success_patterns.append("Verification and final outcome won over provisional reasoning.")
        explicit_failure_patterns = _as_text_list(failure_patterns, limit=10, item_limit=360)
        if not explicit_failure_patterns:
            explicit_failure_patterns = ["Raw transcript reuse can inject stale or low-confidence context."]
        for payload in consolidation_payloads:
            trace = _nested_dict(payload, "provisional_evidence", "kimi_trace")
            if _float_or(trace.get("thinking_event_count"), 0.0) > 0:
                explicit_failure_patterns.append(
                    "Kimi thinking is useful trace evidence, but not durable truth until verified."
                )
        explicit_open_loops = _as_text_list(open_loops, limit=10, item_limit=320)
        if outcome_status != "success" and not explicit_open_loops:
            explicit_open_loops = ["Resolve incomplete scene outcome."]
        for payload in consolidation_payloads:
            for item in payload.get("promote_candidates") or []:
                if item:
                    explicit_open_loops.append(f"Evaluate promotion candidate: {_clip(item, 220)}")
        explicit_artifacts = _as_dict_list(artifacts, limit=12)
        for payload in consolidation_payloads:
            prompt = payload.get("prompt_causality") if isinstance(payload.get("prompt_causality"), dict) else {}
            if prompt.get("prompt_sha256"):
                explicit_artifacts.append(
                    {
                        "kind": "prompt_causality",
                        "prompt_sha256": prompt.get("prompt_sha256"),
                        "action_packet_id": prompt.get("action_packet_id"),
                        "section_hashes": prompt.get("section_hashes", [])[:16],
                    }
                )
        do_not_use_for = ["secret_recall", "mutation_proof_without_task_contract"]
        for payload in consolidation_payloads:
            do_not_use_for.extend(_as_text_list(payload.get("do_not_promote"), limit=8, item_limit=120))
        card = SceneCardModel(
            id=_stable_id("scard", {"scene": scene_id}, 18),
            scene_id=scene_id,
            episode_id=scene.get("episode_id"),
            user_id=scene.get("user_id") or "default",
            agent_id=scene.get("agent_id"),
            agent_category=scene.get("agent_category"),
            namespace=scene.get("namespace") or "default",
            categories=categories,
            retrieval_tags=retrieval_tags,
            summary=summary,
            durable_facts=explicit_durable_facts,
            decisions=explicit_decisions,
            procedures=explicit_procedures,
            success_patterns=explicit_success_patterns,
            failure_patterns=explicit_failure_patterns,
            open_loops=explicit_open_loops,
            entities=_as_dict_list(entities, limit=12),
            artifacts=explicit_artifacts[:12],
            importance=max(_float_or(importance if importance is not None else scene.get("importance"), 0.5), 0.6),
            confidence=max(_float_or(confidence if confidence is not None else scene.get("confidence"), 0.5), 0.75),
            reuse_policy=reuse_policy or "category_shareable",
            visibility_scope=visibility_scope or scene.get("visibility_scope") or "category",
            privacy_class=privacy_class or scene.get("privacy_class") or "user_private",
            evidence_refs=evidence_refs[:24],
            do_not_use_for=list(dict.fromkeys(do_not_use_for))[:16],
        )
        saved = self.db.upsert_scene_card(card.model_dump())
        self.db.add_scene_card_claim(
            {
                "scene_card_id": saved["id"],
                "kind": "decision",
                "claim": "Dhee should retrieve useful SceneCards before raw memories.",
                "confidence": 0.9,
                "evidence_refs": [ref["ref"] for ref in evidence_refs if ref.get("ref")],
            }
        )
        for fact in explicit_durable_facts:
            self.db.add_scene_card_claim(
                {
                    "scene_card_id": saved["id"],
                    "kind": "durable_fact",
                    "claim": fact,
                    "confidence": card.confidence,
                    "evidence_refs": [ref["ref"] for ref in evidence_refs if ref.get("ref")],
                }
            )
        self.db.update_scene(
            scene_id,
            {
                "end_time": now,
                "result": outcome or "SceneCard created.",
                "outcome": outcome or "scene_card_created",
                "outcome_status": outcome_status,
                "story_progress_delta": story_progress_delta,
                "consolidated_card_id": saved["id"],
                "consolidated_card_json": saved,
                "updated_at": now,
            },
        )
        episode = self._rollup_episode_from_card(
            scene=scene,
            card=saved,
            outcome=outcome,
            outcome_status=outcome_status,
            story_progress_delta=story_progress_delta,
        )
        promoted_memory_ids = self._promote_durable_facts(
            card=saved,
            scene=scene,
            facts=explicit_durable_facts,
            promote=promote_durable_facts,
        )
        return {
            "format": "dhee_scene_end.v1",
            "scene": self.db.get_scene(scene_id),
            "episode": episode,
            "card": self._safe_card(saved),
            "promoted_memory_ids": promoted_memory_ids,
        }

    def _rollup_model_for_scope(self, scope_type: str) -> str:
        return SERIES_ESCALATION_ROLLUP_MODEL if scope_type == "series" else DEFAULT_ROLLUP_MODEL

    def _get_rollup_llm(self, model: str) -> Optional[Any]:
        if self._rollup_llm is not None:
            return self._rollup_llm
        if not self._create_default_rollup_llm:
            return None
        if model in self._rollup_llms:
            return self._rollup_llms[model]
        try:
            from dhee.llms.nvidia import NvidiaLLM

            llm = NvidiaLLM(
                {
                    "model": model,
                    "temperature": 0.1,
                    "top_p": 0.7,
                    "max_tokens": 1600,
                    "enable_thinking": False,
                    "timeout": 90,
                }
            )
        except Exception as exc:
            logger.debug("Narrative rollup LLM unavailable for %s: %s", model, exc)
            self._create_default_rollup_llm = False
            return None
        self._rollup_llms[model] = llm
        return llm

    def _rollup_llm_label(self, llm: Any, fallback_model: str) -> str:
        return str(getattr(llm, "model", None) or getattr(llm, "model_name", None) or fallback_model)

    def _rollup_source_cards(self, scope_type: str, scope: Dict[str, Any], limit: int = 24) -> List[Dict[str, Any]]:
        cards = self.db.list_scene_cards(
            user_id=scope.get("user_id") or "default",
            namespace=None if scope_type == "series" else scope.get("namespace"),
            limit=200,
        )
        out: List[Dict[str, Any]] = []
        for card in cards:
            if card.get("privacy_class") in {"secret", "redacted"}:
                continue
            if scope_type == "episode" and card.get("episode_id") != scope.get("id"):
                continue
            if scope_type == "season" and card.get("season_id") != scope.get("id"):
                continue
            if scope_type == "series" and card.get("series_id") != scope.get("id"):
                continue
            out.append(card)
            if len(out) >= limit:
                break
        return out

    def _rollup_card_payload(self, card: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": card.get("id"),
            "scene_id": card.get("scene_id"),
            "episode_id": card.get("episode_id"),
            "categories": list(card.get("categories") or [])[:8],
            "summary": _clip(card.get("summary"), 420),
            "durable_facts": [_clip(item, 240) for item in (card.get("durable_facts") or [])[:6]],
            "decisions": [_clip(item, 220) for item in (card.get("decisions") or [])[:6]],
            "success_patterns": [_clip(item, 220) for item in (card.get("success_patterns") or [])[:4]],
            "failure_patterns": [_clip(item, 220) for item in (card.get("failure_patterns") or [])[:4]],
            "open_loops": [_clip(item, 220) for item in (card.get("open_loops") or [])[:6]],
            "outcome_status": card.get("scene_outcome_status"),
            "importance": card.get("importance"),
            "confidence": card.get("confidence"),
            "evidence_refs": [
                {
                    "kind": ref.get("kind"),
                    "ref": ref.get("ref"),
                    "label": _clip(ref.get("label"), 120),
                }
                for ref in (card.get("evidence_refs") or [])[:6]
                if isinstance(ref, dict)
            ],
        }

    def _deterministic_rollup_payload(
        self,
        *,
        scope_type: str,
        scope: Dict[str, Any],
        deterministic: Dict[str, Any],
        cards: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "schema_version": ROLLUP_PROMPT_VERSION,
            "scope_type": scope_type,
            "scope_id": scope.get("id"),
            "deterministic_rollup": deterministic,
            "source_scene_card_ids": [card.get("id") for card in cards if card.get("id")],
            "source_scene_cards": [self._rollup_card_payload(card) for card in cards],
        }

    def _distill_narrative_rollup(
        self,
        *,
        scope_type: str,
        scope: Dict[str, Any],
        deterministic: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        cards = self._rollup_source_cards(scope_type, scope)
        payload = self._deterministic_rollup_payload(
            scope_type=scope_type,
            scope=scope,
            deterministic=deterministic,
            cards=cards,
        )
        input_hash = _json_hash(payload)
        if scope.get("rollup_input_hash") == input_hash and scope.get("llm_rollup"):
            return None
        model = self._rollup_model_for_scope(scope_type)
        llm = self._get_rollup_llm(model)
        if not llm:
            return {
                "deterministic_rollup": deterministic,
                "rollup_prompt_version": ROLLUP_PROMPT_VERSION,
                "rollup_source_scene_card_ids": payload["source_scene_card_ids"],
                "rollup_input_hash": input_hash,
            }
        if hasattr(llm, "set_purpose"):
            try:
                llm.set_purpose("narrative_rollup")
            except Exception:
                pass
        prompt = (
            "You are Dhee's narrative memory distiller. Summarize only the provided "
            "SceneCard evidence. Do not invent new facts, do not include raw transcripts, "
            "and keep the result advisory.\n\n"
            "Return one strict JSON object with keys: arc_summary, active_tensions, "
            "latest_signal, open_threads, resolved_threads, likely_next_steps, "
            "contradictions, evidence_card_ids, confidence. Strings must be compact. "
            "Lists should contain at most 8 items. evidence_card_ids must come only "
            "from source_scene_card_ids.\n\n"
            f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"
        )
        try:
            raw = llm.generate(prompt)
            parsed = _extract_json_object(raw)
            rollup = NarrativeRollupModel(
                scope_type=scope_type,
                scope_id=str(scope.get("id") or ""),
                arc_summary=_clip(parsed.get("arc_summary") or deterministic.get("arc_summary") or "", 900),
                active_tensions=_as_text_list(parsed.get("active_tensions"), limit=8, item_limit=240),
                latest_signal=_clip(parsed.get("latest_signal") or deterministic.get("latest_signal") or "", 360) or None,
                open_threads=_as_text_list(parsed.get("open_threads"), limit=8, item_limit=240),
                resolved_threads=_as_text_list(parsed.get("resolved_threads"), limit=8, item_limit=240),
                likely_next_steps=_as_text_list(parsed.get("likely_next_steps"), limit=8, item_limit=240),
                contradictions=_as_text_list(parsed.get("contradictions"), limit=8, item_limit=240),
                evidence_card_ids=[
                    card_id
                    for card_id in _as_text_list(parsed.get("evidence_card_ids"), limit=12, item_limit=120)
                    if card_id in set(payload["source_scene_card_ids"])
                ],
                confidence=max(0.0, min(1.0, _float_or(parsed.get("confidence"), 0.65))),
            ).model_dump()
        except Exception as exc:
            logger.debug("Narrative LLM rollup skipped for %s %s: %s", scope_type, scope.get("id"), exc)
            return {
                "deterministic_rollup": deterministic,
                "rollup_prompt_version": ROLLUP_PROMPT_VERSION,
                "rollup_source_scene_card_ids": payload["source_scene_card_ids"],
                "rollup_input_hash": input_hash,
            }
        return {
            "deterministic_rollup": deterministic,
            "llm_rollup": rollup,
            "rollup_model": self._rollup_llm_label(llm, model),
            "rollup_prompt_version": ROLLUP_PROMPT_VERSION,
            "rollup_source_scene_card_ids": payload["source_scene_card_ids"],
            "rollup_input_hash": input_hash,
        }

    def _rollup_episode_from_card(
        self,
        *,
        scene: Dict[str, Any],
        card: Dict[str, Any],
        outcome: str,
        outcome_status: str,
        story_progress_delta: str,
    ) -> Optional[Dict[str, Any]]:
        episode_id = card.get("episode_id") or scene.get("episode_id")
        if not episode_id:
            return None
        episode = self.db.get_episode(episode_id)
        if not episode:
            return None
        summary = _clip(card.get("summary") or scene.get("summary") or "", 260)
        decisions = _append_unique(
            episode.get("key_decisions"),
            [_clip(item, 240) for item in card.get("decisions") or []],
            limit=40,
        )
        open_loops = _append_unique(
            episode.get("open_loops"),
            [_clip(item, 240) for item in card.get("open_loops") or []],
            limit=40,
        )
        unresolved_threads = _append_unique(
            episode.get("unresolved_threads"),
            [_clip(item, 240) for item in card.get("open_loops") or []],
            limit=40,
        )
        category_summaries = dict(episode.get("category_summaries") or {})
        for category in card.get("categories") or []:
            category_summaries[str(category)] = summary
        scene_ids = _append_unique(episode.get("scene_ids"), [card.get("scene_id")], limit=80)
        agent_ids = _append_unique(
            episode.get("agent_ids"),
            [card.get("agent_id") or scene.get("agent_id")],
            limit=30,
        )
        story_parts = [
            part for part in [
                episode.get("story_progress"),
                _clip(story_progress_delta, 320),
                f"Scene {card.get('scene_id')} ended as {outcome_status}: {_clip(outcome or summary, 240)}",
            ] if part
        ]
        story_progress = " | ".join(story_parts[-4:])
        deterministic_rollup = {
            "scope_type": "episode",
            "arc_summary": story_progress,
            "latest_signal": f"Scene {card.get('scene_id')} ended as {outcome_status}.",
            "open_threads": open_loops,
            "unresolved_threads": unresolved_threads,
            "key_decisions": decisions[-8:],
            "category_summaries": category_summaries,
            "source": "deterministic_scene_card_rollup",
        }
        updates = {
            "scene_ids": scene_ids,
            "agent_ids": agent_ids,
            "key_decisions": decisions,
            "open_loops": open_loops,
            "unresolved_threads": unresolved_threads,
            "category_summaries": category_summaries,
            "story_progress": story_progress,
            "deterministic_rollup": deterministic_rollup,
            "outcome": outcome or episode.get("outcome"),
            "lesson": (card.get("success_patterns") or card.get("failure_patterns") or [None])[0],
            "status": "open",
        }
        self.db.update_episode(episode_id, updates)
        updated = self.db.get_episode(episode_id)
        if updated:
            rollup_updates = self._distill_narrative_rollup(
                scope_type="episode",
                scope=updated,
                deterministic=deterministic_rollup,
            )
            if rollup_updates:
                llm_rollup = rollup_updates.get("llm_rollup") or {}
                if llm_rollup.get("arc_summary"):
                    rollup_updates["story_progress"] = llm_rollup["arc_summary"]
                if llm_rollup.get("open_threads"):
                    rollup_updates["open_loops"] = _append_unique(open_loops, llm_rollup["open_threads"], limit=40)
                    rollup_updates["unresolved_threads"] = _append_unique(
                        unresolved_threads,
                        llm_rollup.get("active_tensions") or llm_rollup["open_threads"],
                        limit=40,
                    )
                if llm_rollup.get("latest_signal"):
                    rollup_updates["lesson"] = llm_rollup["latest_signal"]
                self.db.update_episode(episode_id, rollup_updates)
                updated = self.db.get_episode(episode_id)
        if updated:
            self._rollup_season_from_episode(episode=updated, card=card)
        return updated

    def _rollup_season_from_episode(
        self,
        *,
        episode: Dict[str, Any],
        card: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        season_id = episode.get("season_id")
        if not season_id:
            return None
        season = self.db.get_season(season_id)
        if not season:
            return None
        open_threads = _append_unique(
            season.get("open_threads"),
            [item for item in episode.get("open_loops") or []],
            limit=60,
        )
        for category, summary in (episode.get("category_summaries") or {}).items():
            if summary:
                open_threads = _append_unique(
                    open_threads,
                    [f"{category}: {_clip(summary, 180)}"],
                    limit=60,
                )
        arc_parts = [
            part for part in [
                season.get("arc_summary"),
                _clip(episode.get("story_progress"), 420),
                f"Latest episode {episode.get('local_date')}: {_clip(card.get('summary'), 260)}",
            ] if part
        ]
        arc_summary = " | ".join(arc_parts[-5:])
        deterministic_rollup = {
            "scope_type": "season",
            "arc_summary": arc_summary,
            "latest_signal": f"Latest episode {episode.get('local_date')}: {_clip(card.get('summary'), 240)}",
            "open_threads": open_threads,
            "episode_story_progress": episode.get("story_progress"),
            "source": "deterministic_episode_rollup",
        }
        updates = {
            "open_threads": open_threads,
            "arc_summary": arc_summary,
            "deterministic_rollup": deterministic_rollup,
            "confidence": min(0.95, max(_float_or(season.get("confidence"), 0.5), _float_or(card.get("confidence"), 0.75))),
        }
        self.db.update_season(season_id, updates)
        updated = self.db.get_season(season_id)
        if updated:
            rollup_updates = self._distill_narrative_rollup(
                scope_type="season",
                scope=updated,
                deterministic=deterministic_rollup,
            )
            if rollup_updates:
                llm_rollup = rollup_updates.get("llm_rollup") or {}
                if llm_rollup.get("arc_summary"):
                    rollup_updates["arc_summary"] = llm_rollup["arc_summary"]
                if llm_rollup.get("open_threads") or llm_rollup.get("active_tensions"):
                    rollup_updates["open_threads"] = _append_unique(
                        open_threads,
                        list(llm_rollup.get("open_threads") or []) + list(llm_rollup.get("active_tensions") or []),
                        limit=60,
                    )
                if llm_rollup.get("confidence"):
                    rollup_updates["confidence"] = min(
                        0.95,
                        max(_float_or(updated.get("confidence"), 0.5), _float_or(llm_rollup.get("confidence"), 0.65)),
                    )
                self.db.update_season(season_id, rollup_updates)
                updated = self.db.get_season(season_id)
        if updated:
            self._rollup_series_from_season(season=updated, episode=episode, card=card)
        return updated

    def _rollup_series_from_season(
        self,
        *,
        season: Dict[str, Any],
        episode: Dict[str, Any],
        card: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        series_id = season.get("series_id")
        if not series_id:
            return None
        series = self.db.get_series(series_id)
        if not series:
            return None
        active_tensions = _append_unique(
            series.get("active_tensions"),
            [item for item in season.get("open_threads") or []],
            limit=80,
        )
        signal = _clip(season.get("arc_summary") or season.get("theme") or "", 520)
        arc_parts = [
            part for part in [
                series.get("arc_summary"),
                f"Active season {season.get('id')}: {signal}",
                f"Latest episode {episode.get('local_date')}: {_clip(card.get('summary'), 240)}",
            ] if part
        ]
        arc_summary = " | ".join(arc_parts[-5:])
        deterministic_rollup = {
            "scope_type": "series",
            "arc_summary": arc_summary,
            "latest_signal": signal,
            "active_tensions": active_tensions,
            "current_active_season": season.get("id"),
            "source": "deterministic_season_rollup",
        }
        updates = {
            "current_active_season": season.get("id"),
            "arc_summary": arc_summary,
            "active_tensions": active_tensions,
            "latest_season_signal": signal,
            "deterministic_rollup": deterministic_rollup,
            "confidence": min(0.95, max(_float_or(series.get("confidence"), 0.5), _float_or(season.get("confidence"), 0.75))),
        }
        self.db.update_series(series_id, updates)
        updated = self.db.get_series(series_id)
        if updated:
            rollup_updates = self._distill_narrative_rollup(
                scope_type="series",
                scope=updated,
                deterministic=deterministic_rollup,
            )
            if rollup_updates:
                llm_rollup = rollup_updates.get("llm_rollup") or {}
                if llm_rollup.get("arc_summary"):
                    rollup_updates["arc_summary"] = llm_rollup["arc_summary"]
                if llm_rollup.get("active_tensions") or llm_rollup.get("open_threads"):
                    rollup_updates["active_tensions"] = _append_unique(
                        active_tensions,
                        list(llm_rollup.get("active_tensions") or []) + list(llm_rollup.get("open_threads") or []),
                        limit=80,
                    )
                if llm_rollup.get("latest_signal"):
                    rollup_updates["latest_season_signal"] = llm_rollup["latest_signal"]
                if llm_rollup.get("confidence"):
                    rollup_updates["confidence"] = min(
                        0.95,
                        max(_float_or(updated.get("confidence"), 0.5), _float_or(llm_rollup.get("confidence"), 0.65)),
                    )
                self.db.update_series(series_id, rollup_updates)
        return self.db.get_series(series_id)

    def _promote_durable_facts(
        self,
        *,
        card: Dict[str, Any],
        scene: Dict[str, Any],
        facts: List[str],
        promote: bool,
    ) -> List[str]:
        if not promote or not facts:
            return []
        if card.get("privacy_class") in {"secret", "redacted"}:
            return []
        promoted: List[str] = []
        categories = list(card.get("categories") or [])
        for fact in facts:
            content_hash = hashlib.sha256(
                json.dumps(
                    {
                        "user_id": card.get("user_id"),
                        "namespace": card.get("namespace"),
                        "fact": fact,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            existing = None
            if hasattr(self.db, "get_memory_by_content_hash"):
                existing = self.db.get_memory_by_content_hash(
                    content_hash,
                    user_id=card.get("user_id") or "default",
                )
            if existing:
                promoted.append(existing["id"])
                continue
            memory_id = _stable_id(
                "memitem",
                {"scene_card": card.get("id"), "fact": fact},
                18,
            )
            promoted.append(
                self.db.add_memory(
                    {
                        "id": memory_id,
                        "memory": fact,
                        "user_id": card.get("user_id") or "default",
                        "agent_id": card.get("agent_id") or scene.get("agent_id"),
                        "categories": categories,
                        "source_type": "scene_card",
                        "source_app": "dhee_scene",
                        "source_event_id": card.get("id"),
                        "namespace": card.get("namespace") or scene.get("namespace") or "default",
                        "confidentiality_scope": "personal",
                        "sensitivity": "normal",
                        "memory_type": "semantic",
                        "layer": "lml",
                        "importance": min(1.0, max(0.5, _float_or(card.get("importance"), 0.6))),
                        "metadata": {
                            "kind": "scene_card_durable_fact",
                            "scene_id": card.get("scene_id"),
                            "scene_card_id": card.get("id"),
                            "episode_id": card.get("episode_id"),
                            "promotion_policy": "explicit_scene_end_durable_fact",
                        },
                        "content_hash": content_hash,
                    }
                )
            )
        return promoted

    def _get_reranker(self) -> Optional[Any]:
        if self._reranker is not None:
            return self._reranker
        if not self._create_default_reranker:
            return None
        try:
            from dhee.memory.reranker import create_reranker
            from dhee.provider_defaults import DEFAULT_NVIDIA_RERANK_MODEL

            config = {
                "provider": "nvidia",
                "model": DEFAULT_NVIDIA_RERANK_MODEL,
                "api_key_env": "NVIDIA_API_KEY",
                "strict_schema": False,
            }
            try:
                from dhee.cli_config import get_api_key

                api_key = get_api_key("nvidia")
                if api_key:
                    config["api_key"] = api_key
            except Exception:
                pass
            self._reranker = create_reranker(config)
        except Exception as exc:
            logger.debug("SceneCard default reranker unavailable: %s", exc)
            self._create_default_reranker = False
            return None
        return self._reranker

    def _backend_label(self, backend: Optional[Any]) -> Optional[str]:
        if backend is None:
            return None
        model = getattr(backend, "model", None) or getattr(backend, "model_name", None)
        if model:
            return str(model)
        return backend.__class__.__name__

    def _call_embed(self, text: str, memory_action: Optional[str]) -> List[float]:
        embedder = self.embedder
        if not embedder:
            return []
        try:
            return list(embedder.embed(text, memory_action=memory_action))
        except TypeError:
            return list(embedder.embed(text))

    def _call_embed_batch(self, texts: List[str], memory_action: Optional[str]) -> List[List[float]]:
        embedder = self.embedder
        if not embedder:
            return []
        if hasattr(embedder, "embed_batch"):
            try:
                return [list(vec) for vec in embedder.embed_batch(texts, memory_action=memory_action)]
            except TypeError:
                return [list(vec) for vec in embedder.embed_batch(texts)]
        return [self._call_embed(text, memory_action=memory_action) for text in texts]

    def _scene_card_retrieval_text(self, card: Dict[str, Any]) -> str:
        marker_text = []
        for key, values in (card.get("markers") or {}).items():
            marker_text.append(str(key))
            marker_text.extend(str(value) for value in values)
        return _clip(
            " ".join(
                [
                    card.get("summary") or "",
                    " ".join(card.get("retrieval_tags") or []),
                    " ".join(card.get("categories") or []),
                    " ".join(card.get("durable_facts") or []),
                    " ".join(card.get("decisions") or []),
                    " ".join(card.get("procedures") or []),
                    " ".join(card.get("success_patterns") or []),
                    " ".join(card.get("failure_patterns") or []),
                    " ".join(card.get("open_loops") or []),
                    " ".join(marker_text),
                    card.get("story_progress_delta") or "",
                ]
            ),
            12000,
        )

    def _query_retrieval_text(
        self,
        query: str,
        categories: List[str],
        markers: Optional[Dict[str, Any]],
    ) -> str:
        marker_text = []
        for key, value in (markers or {}).items():
            marker_text.append(str(key))
            marker_values = value if isinstance(value, list) else [value]
            marker_text.extend(str(item) for item in marker_values)
        return _clip(" ".join([query or "", " ".join(categories), " ".join(marker_text)]), 4000)

    def _semantic_scores(
        self,
        *,
        query_text: str,
        candidates: List[Dict[str, Any]],
        card_text_by_id: Dict[str, str],
    ) -> Dict[str, float]:
        if not self.embedder or not query_text.strip() or not candidates:
            return {}
        try:
            query_vec = self._call_embed(query_text, memory_action="search")
            passage_texts = [card_text_by_id.get(item["card"]["id"], "") for item in candidates]
            passage_vecs = self._call_embed_batch(passage_texts, memory_action=None)
        except Exception as exc:
            logger.debug("SceneCard embedding similarity skipped: %s", exc)
            return {}
        scores: Dict[str, float] = {}
        for item, vec in zip(candidates, passage_vecs):
            card_id = item["card"]["id"]
            score = _cosine_similarity(query_vec, vec)
            if score > 0:
                scores[card_id] = score
        return scores

    def _rerank_scores(
        self,
        *,
        query_text: str,
        candidates: List[Dict[str, Any]],
        card_text_by_id: Dict[str, str],
    ) -> tuple[Dict[str, float], Dict[str, float]]:
        reranker = self._get_reranker()
        if not reranker or not query_text.strip() or not candidates:
            return {}, {}
        passages = [_clip(card_text_by_id.get(item["card"]["id"], ""), 6000) for item in candidates]
        try:
            raw = reranker.rerank(query=query_text, passages=passages, top_n=0)
        except Exception as exc:
            logger.debug("SceneCard rerank skipped: %s", exc)
            return {}, {}
        logits_by_index: Dict[int, float] = {}
        for row in raw or []:
            if not isinstance(row, dict):
                continue
            try:
                index = int(row.get("index"))
                logit = float(
                    row.get("logit")
                    if row.get("logit") is not None
                    else row.get("score")
                    if row.get("score") is not None
                    else row.get("relevance_score")
                )
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(candidates):
                logits_by_index[index] = logit
        if not logits_by_index:
            return {}, {}
        values = list(logits_by_index.values())
        min_logit = min(values)
        max_logit = max(values)
        normalized: Dict[str, float] = {}
        raw_logits: Dict[str, float] = {}
        for index, logit in logits_by_index.items():
            card_id = candidates[index]["card"]["id"]
            raw_logits[card_id] = logit
            if len(values) == 1:
                normalized[card_id] = 1.0
            elif max_logit == min_logit:
                normalized[card_id] = 0.5
            else:
                normalized[card_id] = (logit - min_logit) / (max_logit - min_logit)
        return normalized, raw_logits

    def scene_context(
        self,
        *,
        query: str,
        user_id: str = "default",
        agent_id: str = "agent",
        agent_category: str = "agent",
        namespace: Optional[str] = None,
        series_id: Optional[str] = None,
        season_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
        markers: Optional[Dict[str, Any]] = None,
        action_lane: str = "answer",
        limit: int = 8,
        has_task_contract: bool = False,
        has_proof_bundle: bool = False,
    ) -> Dict[str, Any]:
        categories_norm = _normalize_categories(categories)
        query_terms = set(_tokens(" ".join([query, " ".join(categories_norm)])))
        marker_terms = set()
        for key, value in (markers or {}).items():
            marker_terms.update(_tokens(str(key)))
            marker_values = value if isinstance(value, list) else [value]
            for item in marker_values:
                marker_terms.update(_tokens(str(item)))
        current_episode_id = None
        if namespace:
            episode = self.db.get_episode_for_day(
                user_id=user_id,
                namespace=namespace,
                local_date=_local_date(self.default_timezone),
                timezone=self.default_timezone,
            )
            current_episode_id = episode["id"] if episode else None
        cards = self.db.list_scene_cards(user_id=user_id, namespace=namespace, limit=200)
        included: List[Dict[str, Any]] = []
        rejected: List[Dict[str, str]] = []
        proof_gate_blocked = action_lane == "code_mutation" and not (has_task_contract and has_proof_bundle)

        query_text = self._query_retrieval_text(query, categories_norm, markers)
        candidates: List[Dict[str, Any]] = []
        for recency_index, card in enumerate(cards):
            reason = self._reject_reason(card, agent_id, agent_category, namespace)
            if reason:
                rejected.append({"scene_id": card.get("scene_id", card["id"]), "reason": reason})
                continue
            marker_values = set()
            for key, values in (card.get("markers") or {}).items():
                marker_values.update(_tokens(str(key)))
                for value in values:
                    marker_values.update(_tokens(str(value)))
            if self.db.scene_has_blocking_edge(card["scene_id"]):
                rejected.append({"scene_id": card["scene_id"], "reason": "superseded_or_contradicted"})
                continue
            haystack = self._scene_card_retrieval_text(card)
            terms = set(_tokens(haystack))
            overlap = len(query_terms & terms)
            category_overlap = len(set(categories_norm) & set(card.get("categories") or []))
            marker_overlap = len(marker_terms & marker_values)
            candidates.append(
                {
                    "recency_index": recency_index,
                    "card": card,
                    "haystack": haystack,
                    "overlap": overlap,
                    "category_overlap": category_overlap,
                    "marker_overlap": marker_overlap,
                }
            )

        card_text_by_id = {
            item["card"]["id"]: item["haystack"]
            for item in candidates
        }
        semantic_scores = self._semantic_scores(
            query_text=query_text,
            candidates=candidates,
            card_text_by_id=card_text_by_id,
        )
        rerank_candidates = sorted(
            candidates,
            key=lambda item: (
                semantic_scores.get(item["card"]["id"], 0.0),
                item["overlap"],
                item["category_overlap"],
                -int(item["recency_index"]),
            ),
            reverse=True,
        )[:50]
        rerank_scores, rerank_logits = self._rerank_scores(
            query_text=query_text,
            candidates=rerank_candidates,
            card_text_by_id=card_text_by_id,
        )
        semantic_active = bool(semantic_scores)
        rerank_active = bool(rerank_scores)

        ranked: List[tuple[float, Dict[str, Any], List[str], Dict[str, float]]] = []
        for item in candidates:
            recency_index = int(item["recency_index"])
            card = item["card"]
            overlap = int(item["overlap"])
            category_overlap = int(item["category_overlap"])
            marker_overlap = int(item["marker_overlap"])
            semantic_score = semantic_scores.get(card["id"], 0.0)
            rerank_score = rerank_scores.get(card["id"], 0.0)
            if (
                query_terms
                and overlap == 0
                and category_overlap == 0
                and marker_overlap == 0
                and semantic_score < 0.18
                and rerank_score < 0.55
            ):
                rejected.append({"scene_id": card["scene_id"], "reason": "low_query_or_category_overlap"})
                continue
            series_match = bool(series_id and series_id == card.get("series_id"))
            season_match = bool(season_id and season_id == card.get("season_id"))
            success_match = str(card.get("scene_outcome_status") or "").lower() == "success"
            recency_bonus = max(0.0, (200 - recency_index) / 200) * 0.03
            reasons = []
            if overlap:
                reasons.append(f"token_overlap:{overlap}")
            if semantic_score:
                reasons.append(f"embedding_similarity:{semantic_score:.3f}")
            if rerank_score:
                reasons.append(f"rerank_score:{rerank_score:.3f}")
            if category_overlap:
                reasons.append(f"category_overlap:{category_overlap}")
            if marker_overlap:
                reasons.append(f"marker_overlap:{marker_overlap}")
            if series_match:
                reasons.append("series_match")
            if season_match:
                reasons.append("season_match")
            if namespace and namespace == card.get("namespace"):
                reasons.append("namespace_match")
            if success_match:
                reasons.append("successful_outcome")
            if agent_category == card.get("agent_category"):
                reasons.append("agent_category_match")
            if recency_bonus:
                reasons.append("recency")
            token_score = min(1.0, overlap / max(4.0, float(len(query_terms) or 1)))
            category_score = min(1.0, category_overlap / max(1.0, float(len(categories_norm) or 1)))
            marker_score = min(1.0, marker_overlap / max(1.0, float(len(marker_terms) or 1)))
            heuristic_score = (
                token_score * 0.18
                + category_score * 0.12
                + marker_score * 0.06
                + (0.12 if series_match else 0.0)
                + (0.08 if season_match else 0.0)
                + (0.08 if namespace and namespace == card.get("namespace") else 0.0)
                + (0.06 if success_match else 0.0)
                + float(card.get("importance") or 0.0) * 0.06
                + (0.03 if agent_category == card.get("agent_category") else 0.0)
                + recency_bonus
            )
            semantic_weight = 0.42 if semantic_active else 0.0
            rerank_weight = 0.58 if rerank_active else 0.0
            score = heuristic_score + (semantic_score * semantic_weight) + (rerank_score * rerank_weight)
            ranked.append(
                (
                    score,
                    card,
                    reasons,
                    {
                        "embedding_similarity": semantic_score,
                        "rerank_score": rerank_score,
                        "rerank_logit": rerank_logits.get(card["id"], 0.0),
                        "heuristic_score": heuristic_score,
                    },
                )
            )
        ranked.sort(key=lambda item: item[0], reverse=True)
        for score, card, reasons, rank_details in ranked[: max(0, int(limit))]:
            safe = self._safe_card(card)
            safe["retrieval_score"] = round(score, 4)
            if rank_details.get("embedding_similarity"):
                safe["embedding_similarity"] = round(rank_details["embedding_similarity"], 4)
            if rank_details.get("rerank_score"):
                safe["rerank_score"] = round(rank_details["rerank_score"], 4)
                safe["rerank_logit"] = round(rank_details["rerank_logit"], 4)
            safe["heuristic_score"] = round(rank_details["heuristic_score"], 4)
            if proof_gate_blocked:
                reasons = list(reasons) + ["advisory_only_proof_gate_blocked"]
                safe["use_policy"] = "advisory_prior_only_not_mutation_proof"
                safe["proof_gate"] = "blocked"
            safe["included_reasons"] = reasons
            included.append(safe)
        failure_patterns = ["Code mutation lanes require task contract and proof bundle."] if proof_gate_blocked else []
        success_patterns = []
        open_loops = []
        for card in included:
            failure_patterns.extend(card.get("failure_patterns") or [])
            success_patterns.extend(card.get("success_patterns") or [])
            open_loops.extend(card.get("open_loops") or [])
        same_episode_scenes = [
            card for card in included if current_episode_id and card.get("episode_id") == current_episode_id
        ]
        similar_past_scenes = [
            card for card in included if not current_episode_id or card.get("episode_id") != current_episode_id
        ]
        compact = " ".join(card["summary"] for card in included[:3])
        if proof_gate_blocked and compact:
            compact = "Advisory only; code mutation still needs task contract and proof bundle. " + compact
        return {
            "schema_version": "dhee.scene_context.v1",
            "current_episode_id": current_episode_id,
            "same_episode_scenes": same_episode_scenes,
            "similar_past_scenes": similar_past_scenes,
            "cross_agent_scenes": [
                card for card in included if card.get("agent_id") and card.get("agent_id") != agent_id
            ],
            "included_cards": included,
            "failure_patterns": list(dict.fromkeys(failure_patterns))[:8],
            "success_patterns": list(dict.fromkeys(success_patterns))[:8],
            "open_loops": list(dict.fromkeys(open_loops))[:8],
            "rejected": rejected[: max(8, int(limit))],
            "compact_context": _clip(compact, 1600),
            "retrieval_policy": {
                "raw_transcripts_included": False,
                "proof_gate": "blocked"
                if proof_gate_blocked
                else "passed"
                if action_lane != "code_mutation"
                else "task_contract_and_proof_bundle",
                "card_use": "advisory_prior_only" if proof_gate_blocked else "normal_context_prior",
                "ranking_features": [
                    "embedding_similarity",
                    "neural_rerank",
                    "token_overlap",
                    "category_overlap",
                    "marker_overlap",
                    "series_alignment",
                    "season_alignment",
                    "namespace_match",
                    "successful_outcome",
                    "importance",
                    "recency",
                    "agent_compatibility",
                ],
                "semantic_backend": {
                    "embedder": self._backend_label(self.embedder),
                    "reranker": self._backend_label(self._reranker),
                    "embedding_similarity_active": semantic_active,
                    "rerank_active": rerank_active,
                    "fallback": "rule_category_token" if not (semantic_active or rerank_active) else None,
                },
            },
        }

    def narrative_prior(
        self,
        *,
        query: str = "",
        user_id: str = "default",
        agent_id: str = "agent",
        agent_category: str = "agent",
        namespace: str = "default",
        series_id: Optional[str] = None,
        season_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
        markers: Optional[Dict[str, Any]] = None,
        action_lane: str = "answer",
        limit: int = 5,
        has_task_contract: bool = False,
        has_proof_bundle: bool = False,
    ) -> Dict[str, Any]:
        series = self.db.get_series(series_id) if series_id else None
        if not series:
            series = self.ensure_default_series(user_id=user_id)
        season = self.db.get_season(season_id) if season_id else None
        if not season:
            season = self.ensure_active_season(
                series["id"],
                user_id,
                namespace,
                _local_date(self.default_timezone),
            )
        hero = self.ensure_hero(user_id=user_id)
        episode = self.ensure_today_episode(
            series=series,
            season=season,
            hero=hero,
            user_id=user_id,
            namespace=namespace,
            agent_id=agent_id,
            query=query,
        )
        context = self.scene_context(
            query=query or season.get("theme") or series.get("theme") or "",
            user_id=user_id,
            agent_id=agent_id,
            agent_category=agent_category,
            namespace=namespace,
            series_id=series["id"],
            season_id=season["id"],
            categories=categories,
            markers=markers,
            action_lane=action_lane,
            limit=limit,
            has_task_contract=has_task_contract,
            has_proof_bundle=has_proof_bundle,
        )
        season = self.db.get_season(season["id"]) or season
        series = self.db.get_series(series["id"]) or series
        cards = context.get("included_cards") or []
        series_active_tensions = list(series.get("active_tensions") or [])
        season_open_threads = list(season.get("open_threads") or [])
        episode_open_loops = list(episode.get("open_loops") or [])
        episode_decisions = list(episode.get("key_decisions") or [])
        open_loops = list(dict.fromkeys(series_active_tensions[:3] + season_open_threads[:5] + episode_open_loops + list(context.get("open_loops") or [])))
        success_patterns = list(dict.fromkeys(context.get("success_patterns") or []))
        failure_patterns = list(dict.fromkeys(context.get("failure_patterns") or []))

        likely_next_beats = []
        if series.get("arc_summary"):
            likely_next_beats.append(f"Aim series arc: {_clip(series.get('arc_summary'), 220)}")
        if season.get("arc_summary"):
            likely_next_beats.append(f"Advance season arc: {_clip(season.get('arc_summary'), 220)}")
        if episode.get("story_progress"):
            likely_next_beats.append(f"Continue episode arc: {_clip(episode.get('story_progress'), 220)}")
        for loop in open_loops[:3]:
            likely_next_beats.append(f"Close open loop: {_clip(loop, 180)}")
        for pattern in success_patterns[:3]:
            likely_next_beats.append(f"Reuse proven pattern: {_clip(pattern, 180)}")
        if not likely_next_beats:
            likely_next_beats = [
                "Retrieve the most relevant SceneCards before acting.",
                "Name the intended action and proof gate before code mutation.",
                "End the scene with one compact SceneCard when the task resolves.",
            ]

        likely_failure_modes = failure_patterns[:5]
        if action_lane == "code_mutation" and not (has_task_contract and has_proof_bundle):
            likely_failure_modes.insert(0, "Code mutation without task contract and proof bundle.")
        likely_failure_modes.extend(
            [
                "Narrative assumptions override explicit user intent.",
                "Raw transcript or secret context leaks into prompt state.",
            ]
        )
        likely_failure_modes = list(dict.fromkeys(likely_failure_modes))[:8]

        proof_gate_status = "not_required"
        if action_lane == "code_mutation":
            proof_gate_status = "passed" if has_task_contract and has_proof_bundle else "blocked"

        if proof_gate_status == "blocked":
            best_next_action = (
                "Do not mutate code yet; first establish the task contract and proof bundle, "
                "then retrieve SceneCards as advisory priors."
            )
        elif cards:
            best_next_action = (
                "Use the top SceneCard patterns, state the intended action, execute the task, "
                "and record a new SceneCard with evidence refs."
            )
        else:
            best_next_action = (
                "Proceed from explicit user intent, create a fresh scene, gather evidence refs, "
                "and avoid treating the narrative prior as proof."
            )

        evidence_scene_cards = [
            {
                "id": card.get("id"),
                "scene_id": card.get("scene_id"),
                "summary": card.get("summary"),
                "retrieval_score": card.get("retrieval_score"),
                "included_reasons": card.get("included_reasons", []),
                "evidence_refs": card.get("evidence_refs", []),
            }
            for card in cards[: max(0, int(limit))]
        ]
        anticipation_trace = [
            {
                "source": "series",
                "id": series.get("id"),
                "signal": series.get("arc_summary") or series.get("theme") or series.get("title"),
            },
            {
                "source": "season",
                "id": season.get("id"),
                "signal": season.get("arc_summary") or season.get("theme") or season.get("title"),
            },
            {
                "source": "episode",
                "id": episode.get("id"),
                "signal": episode.get("story_progress") or episode.get("goal") or episode.get("title"),
            },
        ]
        for card in evidence_scene_cards:
            anticipation_trace.append(
                {
                    "source": "scene_card",
                    "id": card.get("id"),
                    "signal": card.get("summary"),
                    "reasons": card.get("included_reasons", []),
                }
            )

        guardrails = [
            "Advisory prior only; explicit user intent, facts, privacy, and proof gates win.",
            "Never use SceneCards as mutation proof.",
            "Do not include raw transcripts or secret context in prompts.",
        ]
        if proof_gate_status == "blocked":
            guardrails.insert(0, "Code mutation is blocked until task contract and proof bundle are present.")

        confidence = min(
            0.95,
            0.45
            + (0.08 * min(len(cards), 4))
            + (0.08 if context.get("same_episode_scenes") else 0.0)
            + (0.05 if success_patterns else 0.0),
        )
        prior = NarrativePriorModel(
            series_id=series.get("id"),
            season_id=season.get("id"),
            episode_id=episode.get("id"),
            series_theme=series.get("theme") or series.get("title") or "",
            season_theme=season.get("theme") or season.get("title") or "",
            episode_goal=episode.get("goal") or query or "Choose the next useful action for the active scene.",
            scene_tension={
                "hero_wants": "A reliable agent runtime that anticipates instead of merely reacting.",
                "obstacle": "LLMs can drift without durable story state, memory, and proof gates.",
                "current_action": query or "Use narrative memory as predictive prior.",
                "active_open_loops": open_loops[:5],
                "series_arc_summary": series.get("arc_summary"),
                "series_active_tensions": series_active_tensions[-5:],
                "season_arc_summary": season.get("arc_summary"),
                "season_open_threads": season_open_threads[-5:],
                "episode_story_progress": episode.get("story_progress"),
                "episode_key_decisions": episode_decisions[-5:],
                "proof_gate_status": proof_gate_status,
            },
            likely_next_beats=likely_next_beats[:8],
            likely_failure_modes=likely_failure_modes,
            best_next_action=best_next_action,
            evidence_scene_cards=evidence_scene_cards,
            anticipation_trace=anticipation_trace,
            guardrails=guardrails,
            proof_gate_status=proof_gate_status,
            confidence=round(confidence, 3),
        )
        return prior.model_dump()

    def _reject_reason(
        self,
        card: Dict[str, Any],
        agent_id: str,
        agent_category: str,
        namespace: Optional[str],
    ) -> Optional[str]:
        if card.get("privacy_class") in {"secret", "redacted"}:
            return "privacy_class_blocked"
        if namespace and card.get("namespace") != namespace:
            return "different_namespace"
        visibility = card.get("visibility_scope") or "private"
        if visibility == "private" and card.get("agent_id") != agent_id:
            return "private_to_origin_agent"
        if visibility == "agent" and card.get("agent_id") != agent_id:
            return "agent_scope_mismatch"
        return None

    def _safe_card(self, card: Dict[str, Any]) -> Dict[str, Any]:
        refs = []
        for ref in card.get("evidence_refs") or []:
            if isinstance(ref, dict):
                refs.append(
                    {
                        "kind": ref.get("kind"),
                        "ref": ref.get("ref"),
                        "label": ref.get("label"),
                    }
                )
            else:
                refs.append({"ref": str(ref)})
        return {
            "id": card.get("id"),
            "scene_id": card.get("scene_id"),
            "episode_id": card.get("episode_id"),
            "series_id": card.get("series_id"),
            "season_id": card.get("season_id"),
            "agent_id": card.get("agent_id"),
            "agent_category": card.get("agent_category"),
            "namespace": card.get("namespace"),
            "summary": _clip(card.get("summary") or "", 900),
            "categories": list(card.get("categories") or [])[:12],
            "retrieval_tags": list(card.get("retrieval_tags") or [])[:12],
            "durable_facts": list(card.get("durable_facts") or [])[:8],
            "decisions": list(card.get("decisions") or [])[:8],
            "procedures": list(card.get("procedures") or [])[:8],
            "success_patterns": list(card.get("success_patterns") or [])[:8],
            "failure_patterns": list(card.get("failure_patterns") or [])[:8],
            "open_loops": list(card.get("open_loops") or [])[:8],
            "importance": card.get("importance"),
            "confidence": card.get("confidence"),
            "reuse_policy": card.get("reuse_policy"),
            "visibility_scope": card.get("visibility_scope"),
            "privacy_class": card.get("privacy_class"),
            "evidence_refs": refs[:8],
        }
