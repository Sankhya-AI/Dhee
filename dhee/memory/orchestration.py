"""Orchestration engine: episodic anchoring, hierarchical retrieval, context assembly.

Dhee's job: retrieve well, assemble context, return it. No answer synthesis.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from dhee.core.episodic_index import normalize_actor_id
from dhee.core.answer_orchestration import (
    build_query_plan,
)

logger = logging.getLogger(__name__)

_QUERY_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "before", "blocker",
    "did", "does", "for", "from", "had", "has", "have", "how", "into",
    "latest", "now", "the", "this", "what", "when", "where", "which",
    "with", "work",
}
_REPO_CONTINUITY_HINTS = {
    "blocker", "blocked", "branch", "commit", "diff", "file", "files",
    "handoff", "hero", "latest", "pypi", "push", "readme", "release",
    "session", "tag", "todo", "todos", "touched", "upload",
}


def _query_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"\b[a-z0-9][a-z0-9_-]{2,}\b", str(text or "").lower())
        if token not in _QUERY_STOPWORDS
    }


def _load_repo_handoff(repo: Optional[str], user_id: str, agent_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not repo:
        return None
    try:
        from dhee.core.kernel import get_last_session

        requester = (
            agent_id
            or os.environ.get("DHEE_REQUESTER_AGENT_ID")
            or os.environ.get("DHEE_AGENT_ID")
            or "codex"
        )
        return get_last_session(
            agent_id=requester,
            requester_agent_id=requester,
            repo=repo,
            user_id=user_id,
            fallback_log_recovery=True,
        )
    except Exception as exc:
        logger.debug("Repo handoff lookup skipped: %s", exc)
        return None


def _load_repo_handoff_candidates(
    repo: Optional[str],
    user_id: str,
    agent_id: Optional[str],
    *,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    if not repo:
        return []
    try:
        from dhee.core.kernel import list_sessions

        requester = (
            agent_id
            or os.environ.get("DHEE_REQUESTER_AGENT_ID")
            or os.environ.get("DHEE_AGENT_ID")
            or "codex"
        )
        return list_sessions(
            agent_id=requester,
            requester_agent_id=requester,
            repo=repo,
            user_id=user_id,
            limit=limit,
        )
    except Exception as exc:
        logger.debug("Repo handoff candidate lookup skipped: %s", exc)
        return []


class OrchestrationEngine:
    """Handles orchestrated search: map-reduce, episodic index, hierarchical anchors.

    Uses dependency injection (same pattern as SearchPipeline) so that it can
    call back into FullMemory without a circular import.
    """

    def __init__(
        self,
        *,
        config,
        db,
        search_fn: Callable,
        search_episodes_fn: Callable,
        lookup_aggregates_fn: Callable,
        intent_coverage_threshold_fn: Callable,
        record_cost_fn: Callable,
        scene_processor_fn: Callable,
        profile_processor_fn: Callable,
        evolution_layer_fn: Callable,
        llm_fn: Callable,
    ):
        self._config = config
        self._db = db
        self._search_fn = search_fn
        self._search_episodes_fn = search_episodes_fn
        self._lookup_aggregates_fn = lookup_aggregates_fn
        self._intent_coverage_threshold_fn = intent_coverage_threshold_fn
        self._record_cost_fn = record_cost_fn
        self._scene_processor_fn = scene_processor_fn
        self._profile_processor_fn = profile_processor_fn
        self._evolution_layer_fn = evolution_layer_fn
        self._llm_fn = llm_fn
        # Internal state
        self._guardrail_auto_disabled: bool = False

    # -- Cost guardrail -------------------------------------------------------

    def _enforce_write_cost_guardrail(self, *, user_id: Optional[str]) -> None:
        cost_cfg = getattr(self._config, "cost_guardrail", None)
        orch_cfg = getattr(self._config, "orchestration", None)
        if not cost_cfg or not cost_cfg.strict_write_path_cap or not orch_cfg:
            return

        # Baseline values default to 0.0; treat that as "not configured" to avoid
        # accidental auto-disable on fresh installs.
        base_calls = float(getattr(cost_cfg, "baseline_write_llm_calls_per_memory", 0.0) or 0.0)
        base_tokens = float(getattr(cost_cfg, "baseline_write_tokens_per_memory", 0.0) or 0.0)
        if base_calls <= 0.0 and base_tokens <= 0.0:
            return

        summary = self._db.aggregate_cost_counters(phase="write", user_id=user_id)
        samples = max(1, int(summary.get("samples", 0) or 0))
        avg_calls = float(summary.get("llm_calls", 0.0) or 0.0) / float(samples)
        avg_tokens = (
            float(summary.get("input_tokens", 0.0) or 0.0)
            + float(summary.get("output_tokens", 0.0) or 0.0)
        ) / float(samples)

        violates_calls = base_calls > 0.0 and avg_calls > base_calls
        violates_tokens = base_tokens > 0.0 and avg_tokens > base_tokens
        if not (violates_calls or violates_tokens):
            return

        if getattr(cost_cfg, "auto_disable_on_violation", False):
            if not self._guardrail_auto_disabled:
                orch_cfg.enable_episodic_index = False
                orch_cfg.enable_hierarchical_retrieval = False
                orch_cfg.enable_orchestrated_search = False
                self._guardrail_auto_disabled = True
                logger.warning(
                    "Write-cost guardrail violated (avg_calls=%.4f avg_tokens=%.2f). "
                    "Auto-disabled orchestration features.",
                    avg_calls,
                    avg_tokens,
                )
        else:
            logger.warning(
                "Write-cost guardrail violated (avg_calls=%.4f avg_tokens=%.2f), "
                "strict mode active and auto-disable disabled.",
                avg_calls,
                avg_tokens,
            )

    # -- Actor / anchor helpers -----------------------------------------------

    def _infer_actor_id_from_query(self, *, query: str, user_id: str) -> Optional[str]:
        """Infer actor from query using profile names/aliases for speaker-anchored retrieval."""
        text = str(query or "").strip().lower()
        if not text or not user_id:
            return None
        try:
            profiles = self._db.get_all_profiles(user_id=user_id)
        except Exception:
            return None
        for profile in profiles:
            name = str(profile.get("name") or "").strip()
            aliases = list(profile.get("aliases") or [])
            candidates = [name] + [str(a).strip() for a in aliases if str(a).strip()]
            for candidate in candidates:
                lowered = candidate.lower()
                if not lowered:
                    continue
                if lowered in {"self", "me", "myself"} and re.search(r"\b(i|my|me)\b", text):
                    return normalize_actor_id(candidate)
                if re.search(rf"\b{re.escape(lowered)}\b", text):
                    return normalize_actor_id(candidate)
        return None

    def _build_hierarchical_anchors(
        self,
        *,
        query: str,
        user_id: str,
        limit: int = 3,
    ) -> List[str]:
        anchors: List[str] = []
        if not user_id:
            return anchors
        scene_processor = self._scene_processor_fn()
        profile_processor = self._profile_processor_fn()
        # Tier 2a: scene summaries (episodic compression).
        if scene_processor:
            try:
                for scene in scene_processor.search_scenes(query=query, user_id=user_id, limit=max(1, int(limit))):
                    scene_id = str(scene.get("id") or "")[:8]
                    summary = str(scene.get("summary") or scene.get("title") or "").strip()
                    if summary:
                        anchors.append(f"scene[{scene_id}] {summary[:220]}")
            except Exception as e:
                logger.debug("Scene anchor retrieval failed: %s", e)
        # Tier 2b: profile anchors (entity continuity).
        if profile_processor:
            try:
                for profile in profile_processor.search_profiles(query=query, user_id=user_id, limit=max(1, int(limit))):
                    name = str(profile.get("name") or "unknown").strip()
                    narrative = str(profile.get("narrative") or "").strip()
                    if narrative:
                        anchors.append(f"profile[{name}] {narrative[:220]}")
                    else:
                        facts = profile.get("facts") or []
                        if facts:
                            anchors.append(f"profile[{name}] {str(facts[0])[:220]}")
            except Exception as e:
                logger.debug("Profile anchor retrieval failed: %s", e)
        return anchors[: max(0, int(limit) * 2)]

    @staticmethod
    def _session_text(session: Dict[str, Any]) -> str:
        parts: List[str] = []
        for key in ("task_summary", "summary", "status", "repo", "updated"):
            value = session.get(key)
            if value:
                parts.append(str(value))
        for key in ("decisions", "files_touched", "todos", "blockers", "key_commands", "test_results"):
            value = session.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value if str(item).strip())
            elif value:
                parts.append(str(value))
        metadata = session.get("metadata")
        if isinstance(metadata, dict):
            for key in ("blockers", "key_commands", "test_results"):
                value = metadata.get(key)
                if isinstance(value, list):
                    parts.extend(str(item) for item in value if str(item).strip())
                elif value:
                    parts.append(str(value))
        return "\n".join(parts)

    @classmethod
    def _build_repo_handoff_result(
        cls,
        *,
        query: str,
        repo: Optional[str],
        user_id: str,
        agent_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        query_terms = _query_tokens(query)
        candidates = _load_repo_handoff_candidates(repo, user_id, agent_id, limit=50)
        scored_candidates: List[Tuple[float, Dict[str, Any], List[str]]] = []
        for idx, candidate in enumerate(candidates):
            text = cls._session_text(candidate)
            session_terms = _query_tokens(text)
            overlap = sorted(query_terms & session_terms)
            if not overlap and not (query_terms & _REPO_CONTINUITY_HINTS):
                continue
            specificity = len(overlap)
            hint_overlap = len((query_terms & _REPO_CONTINUITY_HINTS) & session_terms)
            recency = max(0.0, 1.0 - (idx * 0.01))
            scored_candidates.append((specificity + hint_overlap + recency, candidate, overlap))
        session = None
        selected_overlap: List[str] = []
        if scored_candidates:
            _score, session, selected_overlap = max(scored_candidates, key=lambda item: item[0])
        if session is None:
            session = _load_repo_handoff(repo, user_id, agent_id)
            selected_overlap = []
        if not session:
            return None
        text = cls._session_text(session)
        session_terms = _query_tokens(text)
        overlap = selected_overlap or sorted(query_terms & session_terms)
        continuity_query = bool(query_terms & _REPO_CONTINUITY_HINTS)
        if not overlap and not continuity_query:
            return None
        overlap_score = len(overlap) / max(4.0, float(len(query_terms) or 1))
        score = min(3.0, 1.15 + overlap_score + (0.35 if continuity_query else 0.0))
        session_id = str(session.get("id") or "latest")
        summary = str(session.get("task_summary") or session.get("summary") or "").strip()
        decisions = session.get("decisions") if isinstance(session.get("decisions"), list) else []
        files = session.get("files_touched") if isinstance(session.get("files_touched"), list) else []
        todos = session.get("todos") if isinstance(session.get("todos"), list) else []
        memory_text = "\n".join(
            part
            for part in (
                f"Latest repo handoff for {repo or 'workspace'}:",
                f"Summary: {summary}" if summary else "",
                "Decisions:\n- " + "\n- ".join(str(item) for item in decisions[:8]) if decisions else "",
                "Files touched: " + ", ".join(str(item) for item in files[:12]) if files else "",
                "Todos/blockers:\n- " + "\n- ".join(str(item) for item in todos[:8]) if todos else "",
            )
            if part
        )
        return {
            "id": f"session:{session_id}",
            "memory": memory_text,
            "user_id": user_id,
            "agent_id": session.get("agent_id"),
            "metadata": {
                "dhee_memory_class": "repo_continuity",
                "canonical_kind": "handoff",
                "repo": repo,
                "session_id": session_id,
                "source": session.get("source") or "handoff",
                "updated_at": session.get("updated"),
            },
            "categories": ["context"],
            "score": score,
            "keyword_score": len(overlap),
            "strength": 1.0,
            "layer": "lml",
            "composite_score": score,
            "namespace": "repo_context",
            "source_type": "handoff",
            "source_app": "dhee",
            "status": session.get("status", "active"),
            "importance": 0.95,
            "memory_type": "handoff",
            "memory_class": "repo_continuity",
            "canonical_kind": "handoff",
            "quality_boost": 0.0,
            "recall_explanation": {
                "matched_memory_id": f"session:{session_id}",
                "overlap_terms": overlap[:8],
                "memory_class": "repo_continuity",
                "memory_kind": "handoff",
                "confidence": round(min(1.0, 0.72 + overlap_score), 3),
                "why_now": "latest repo handoff matched the repo-continuity request",
                "decision_risk": "low",
            },
            "evidence_text": memory_text,
            "evidence_source": "handoff",
            "evidence_chars": len(memory_text),
        }

    # -- Orchestrated context builder -----------------------------------------

    @staticmethod
    def _build_orchestrated_context(
        *,
        results: List[Dict[str, Any]],
        event_hits: List[Dict[str, Any]],
        hierarchical_anchors: Optional[List[str]],
        max_results: int,
        max_chars: int,
        per_result_max_chars: int,
    ) -> str:
        lines: List[str] = []
        remaining = max(1, int(max_chars))

        if hierarchical_anchors:
            lines.append("Hierarchical Anchors:")
            remaining -= len(lines[-1]) + 1
            for anchor in hierarchical_anchors[:10]:
                row = f"- {str(anchor).strip()}"
                if len(row) + 1 > remaining:
                    break
                lines.append(row)
                remaining -= len(row) + 1
            if remaining > 20:
                lines.append("")
                remaining -= 1

        if event_hits:
            lines.append("Episodic Events:")
            remaining -= len(lines[-1]) + 1
            for idx, event in enumerate(event_hits[:20], start=1):
                value = str(event.get("value_text") or "").strip()
                if not value:
                    continue
                actor = str(event.get("actor_role") or event.get("actor_id") or "unknown")
                etype = str(event.get("event_type") or "event")
                stamp = str(event.get("event_time") or "")
                row = f"- [{idx}] type={etype} actor={actor} time={stamp} value={value[:200]}"
                if len(row) + 1 > remaining:
                    break
                lines.append(row)
                remaining -= len(row) + 1
            if remaining > 20:
                lines.append("")
                remaining -= 1

        lines.append("Retrieved Memories:")
        remaining -= len(lines[-1]) + 1
        for idx, row in enumerate(results[: max(1, int(max_results))], start=1):
            evidence = str(row.get("evidence_text") or row.get("memory") or "").strip()
            if not evidence:
                continue
            snippet = evidence[: max(1, int(per_result_max_chars))]
            mem_id = str(row.get("id") or "")
            meta = row.get("metadata") or {}
            session_date = str(
                meta.get("event_time")
                or meta.get("session_date")
                or meta.get("event_date")
                or ""
            ).strip()
            date_tag = f" date={session_date}" if session_date else ""
            session_id = str(meta.get("session_id") or "").strip()
            sid_tag = f" session={session_id}" if session_id else ""
            block = f"[Memory {idx}] id={mem_id}{sid_tag}{date_tag}\n{snippet}"
            if len(block) + 2 > remaining:
                break
            lines.append(block)
            lines.append("")
            remaining -= len(block) + 2

        text = "\n".join(lines).strip()
        return text[: max(1, int(max_chars))]

    # -- Main orchestrated search entry point ---------------------------------

    def search_orchestrated(
        self,
        *,
        query: str,
        user_id: str,
        question_type: str = "",
        question_date: str = "",
        repo: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
        limit: int = 10,
        orchestration_mode: str = "hybrid",
        base_search_limit: Optional[int] = None,
        base_context_limit: int = 10,
        search_cap: Optional[int] = None,
        context_cap: Optional[int] = None,
        map_max_candidates: Optional[int] = None,
        map_max_chars: Optional[int] = None,
        keyword_search: bool = True,
        hybrid_alpha: float = 0.7,
        include_evidence: bool = True,
        evidence_strategy: str = "full",
        evidence_max_chars: int = 3500,
        evidence_context_lines: int = 1,
        max_context_chars: int = 28000,
        rerank: bool = True,
        orchestrator_llm: Optional[Any] = None,
        reflection_max_hops: Optional[int] = None,
    ) -> Dict[str, Any]:
        mode = str(orchestration_mode or "off").strip().lower()
        orch_cfg = getattr(self._config, "orchestration", None)
        enabled = bool(orch_cfg and orch_cfg.enable_orchestrated_search and mode in {"hybrid", "strict"})

        if not enabled:
            base = self._search_fn(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                app_id=app_id,
                filters=filters,
                categories=categories,
                limit=limit,
                rerank=rerank,
                keyword_search=keyword_search,
                hybrid_alpha=hybrid_alpha,
                include_evidence=include_evidence,
                evidence_strategy=evidence_strategy,
                evidence_max_chars=evidence_max_chars,
                evidence_context_lines=evidence_context_lines,
            )
            return {
                "results": base.get("results", []),
                "event_hits": [],
                "coverage": {
                    "event_hit_count": 0,
                    "unique_canonical_keys": 0,
                    "unique_entities": 0,
                    "numeric_fact_count": 0,
                    "dated_fact_count": 0,
                    "coverage_ratio": 0.0,
                    "intent_coverage": 0.0,
                    "threshold": 0.0,
                    "sufficient": False,
                },
                "orchestration": {
                    "mode": "off",
                    "intent": "freeform",
                    "map_reduce_used": False,
                    "reflection_hops": 0,
                    "reason_codes": ["orchestration_disabled"],
                    "cache_hit": False,
                    "intent_coverage": 0.0,
                },
                "reason_codes": ["orchestration_disabled"],
                "cache_hit": False,
                "intent_coverage": 0.0,
                "context": self._build_orchestrated_context(
                    results=base.get("results", []),
                    event_hits=[],
                    hierarchical_anchors=None,
                    max_results=max(1, int(base_context_limit)),
                    max_chars=max_context_chars,
                    per_result_max_chars=evidence_max_chars,
                ),
                "reduced_answer": None,
                "facts": [],
            }

        search_cap_value = int(search_cap or getattr(orch_cfg, "search_cap", 30))
        context_cap_value = int(context_cap or getattr(orch_cfg, "context_cap", 20))
        query_plan = build_query_plan(
            query,
            question_type,
            base_search_limit=int(base_search_limit or max(limit, 10)),
            base_context_limit=int(base_context_limit),
            search_cap=search_cap_value,
            context_cap=context_cap_value,
        )
        search_query = query_plan.rewritten_query or query
        search_limit = max(1, int(query_plan.search_limit))
        context_limit = max(1, int(query_plan.context_limit))
        map_max_candidates_value = int(map_max_candidates or getattr(orch_cfg, "map_max_candidates", 8))
        map_max_chars_value = int(map_max_chars or getattr(orch_cfg, "map_candidate_max_chars", 1200))

        actor_id = self._infer_actor_id_from_query(query=query, user_id=user_id)
        entity_hints: List[str] = []
        if actor_id:
            entity_hints.append(actor_id.replace("_", " "))
        event_payload = self._search_episodes_fn(
            query=query,
            user_id=user_id,
            intent=query_plan.intent,
            actor_id=actor_id,
            time_anchor=question_date or None,
            entity_hints=entity_hints,
            min_coverage=self._intent_coverage_threshold_fn(
                query_plan.intent.value,
                float(getattr(orch_cfg, "map_reduce_coverage_threshold", 0.6)),
            ),
            limit=max(20, context_limit * 2),
        )
        event_hits = event_payload.get("results", [])
        coverage = event_payload.get("coverage", {}) or {}
        reason_codes: List[str] = []

        search_payload = self._search_fn(
            query=search_query,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            app_id=app_id,
            filters=filters,
            categories=categories,
            limit=max(limit, search_limit),
            rerank=rerank,
            keyword_search=keyword_search,
            hybrid_alpha=hybrid_alpha,
            include_evidence=include_evidence,
            evidence_strategy=evidence_strategy,
            evidence_max_chars=evidence_max_chars,
            evidence_context_lines=evidence_context_lines,
        )
        results = list(search_payload.get("results", []))

        handoff_result = self._build_repo_handoff_result(
            query=query,
            repo=os.path.abspath(os.path.expanduser(repo)) if repo else None,
            user_id=user_id,
            agent_id=agent_id,
        )
        if handoff_result:
            existing_ids = {str(row.get("id")) for row in results}
            if str(handoff_result.get("id")) not in existing_ids:
                results.insert(0, handoff_result)
                reason_codes.append("repo_handoff_included")

        if event_hits and orch_cfg.enable_hierarchical_retrieval:
            ordered_ids: List[str] = []
            for event in event_hits:
                memory_id = str(event.get("memory_id") or "").strip()
                if memory_id and memory_id not in ordered_ids:
                    ordered_ids.append(memory_id)
            if ordered_ids:
                ranked = {str(row.get("id")): row for row in results}
                missing_ids = [mid for mid in ordered_ids if mid not in ranked]
                if missing_ids:
                    try:
                        hydrated = self._db.get_memories_bulk(missing_ids, include_tombstoned=False)
                    except Exception as e:
                        logger.debug("Event-hit hydration failed: %s", e)
                        hydrated = {}
                    for memory_id in missing_ids:
                        memory = hydrated.get(memory_id)
                        if not memory:
                            continue
                        memory_text = str(memory.get("memory") or "").strip()
                        evidence_text = memory_text[: max(1, int(evidence_max_chars))]
                        ranked[memory_id] = {
                            "id": memory_id,
                            "memory": memory_text,
                            "score": 0.0,
                            "keyword_score": 0.0,
                            "composite_score": 0.0,
                            "metadata": memory.get("metadata") or {},
                            "categories": memory.get("categories") or [],
                            "layer": memory.get("layer"),
                            "strength": memory.get("strength"),
                            "evidence_text": evidence_text,
                            "evidence_source": "event_hydration",
                            "evidence_chars": len(evidence_text),
                        }
                head = [ranked[mid] for mid in ordered_ids if mid in ranked]
                tail = [row for row in results if str(row.get("id")) not in ordered_ids]
                results = head + tail
                reason_codes.append("event_first_reorder")

        if handoff_result:
            handoff_id = str(handoff_result.get("id"))
            results = [handoff_result] + [row for row in results if str(row.get("id")) != handoff_id]

        hierarchical_anchors: List[str] = []
        if orch_cfg.enable_hierarchical_retrieval:
            hierarchical_anchors = self._build_hierarchical_anchors(
                query=query,
                user_id=user_id,
                limit=3,
            )

        # Dhee's job: retrieve and assemble context. Agent answers.
        # No map-reduce, no triple extraction, no LLM calls at query time.
        reduced_answer: Optional[str] = None
        facts: List[Dict[str, Any]] = []
        map_reduce_used = False
        reflection_hops = 0
        llm_calls_used = 0.0
        cache_hit = False

        context = self._build_orchestrated_context(
            results=results,
            event_hits=event_hits,
            hierarchical_anchors=hierarchical_anchors,
            max_results=context_limit,
            max_chars=max_context_chars,
            per_result_max_chars=evidence_max_chars,
        )

        self._record_cost_fn(
            phase="query",
            user_id=user_id,
            llm_calls=llm_calls_used,
            input_tokens=0.0,
            output_tokens=0.0,
            embed_calls=0.0,
        )

        intent_coverage = float(coverage.get("intent_coverage", coverage.get("coverage_ratio", 0.0)) or 0.0)

        return {
            "results": results[: max(1, int(limit))],
            "event_hits": event_hits,
            "coverage": coverage,
            "orchestration": {
                "mode": mode,
                "intent": query_plan.intent.value,
                "rewritten_query": search_query if search_query != query else None,
                "search_limit": search_limit,
                "context_limit": context_limit,
                "map_reduce_used": map_reduce_used,
                "reflection_hops": reflection_hops,
                "reduced_answer": reduced_answer,
                "reason_codes": list(dict.fromkeys(reason_codes)),
                "cache_hit": bool(cache_hit),
                "intent_coverage": round(intent_coverage, 4),
            },
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "cache_hit": bool(cache_hit),
            "intent_coverage": round(intent_coverage, 4),
            "context": context,
            "reduced_answer": reduced_answer,
            "facts": facts,
        }
