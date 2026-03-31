"""Orchestration engine: map-reduce, episodic anchoring, hierarchical retrieval.

Extracted from memory/main.py — centralizes the orchestrated-search path so that
FullMemory.search_orchestrated() becomes a thin delegation wrapper.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from dhee.memory.cost import stable_hash_text
from dhee.core.episodic_index import normalize_actor_id
from dhee.core.answer_orchestration import (
    build_map_candidates,
    build_query_plan,
    deterministic_inconsistency_check,
    extract_atomic_facts,
    is_low_confidence_answer,
    reduce_atomic_facts,
    render_fact_context,
)

logger = logging.getLogger(__name__)


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
        self._reducer_cache: Dict[str, Dict[str, Any]] = {}
        self._guardrail_auto_disabled: bool = False

    # -- Reducer cache helpers ------------------------------------------------

    def _build_reducer_cache_key(
        self,
        *,
        user_id: str,
        intent_value: str,
        query: str,
        results: List[Dict[str, Any]],
    ) -> str:
        evidence_fingerprint_parts: List[str] = []
        for row in results[:30]:
            mem_id = str(row.get("id") or "").strip()
            score = float(row.get("composite_score", row.get("score", 0.0)) or 0.0)
            evidence_fingerprint_parts.append(f"{mem_id}:{score:.4f}")
        evidence_fingerprint = "|".join(evidence_fingerprint_parts)
        base = "|".join(
            [
                str(user_id or ""),
                str(intent_value or ""),
                stable_hash_text(query),
                stable_hash_text(evidence_fingerprint),
            ]
        )
        return stable_hash_text(base)

    def _get_reducer_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        orch_cfg = getattr(self._config, "orchestration", None)
        ttl_seconds = int(getattr(orch_cfg, "reducer_cache_ttl_seconds", 900) or 900)
        record = self._reducer_cache.get(cache_key)
        if not record:
            return None
        ts = float(record.get("ts", 0.0) or 0.0)
        if ts <= 0.0:
            return None
        if (time.time() - ts) > max(1, ttl_seconds):
            self._reducer_cache.pop(cache_key, None)
            return None
        return record

    def _put_reducer_cache(
        self,
        *,
        cache_key: str,
        reduced_answer: Optional[str],
        facts: List[Dict[str, Any]],
    ) -> None:
        orch_cfg = getattr(self._config, "orchestration", None)
        max_entries = int(getattr(orch_cfg, "reducer_cache_max_entries", 2048) or 2048)
        self._reducer_cache[cache_key] = {
            "ts": time.time(),
            "reduced_answer": reduced_answer,
            "facts": list(facts or []),
        }
        # Keep insertion-order bounded cache.
        while len(self._reducer_cache) > max(1, max_entries):
            oldest_key = next(iter(self._reducer_cache))
            self._reducer_cache.pop(oldest_key, None)

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

        hierarchical_anchors: List[str] = []
        if orch_cfg.enable_hierarchical_retrieval:
            hierarchical_anchors = self._build_hierarchical_anchors(
                query=query,
                user_id=user_id,
                limit=3,
            )

        (
            reduced_answer,
            facts,
            map_reduce_used,
            reflection_hops,
            llm_calls_used,
            cache_hit,
            orchestration_reasons,
            results,
        ) = self._execute_map_reduce(
            query_plan=query_plan,
            orchestrator_llm=orchestrator_llm,
            results=results,
            event_hits=event_hits,
            coverage=coverage,
            query=query,
            question_type=question_type,
            question_date=question_date,
            mode=mode,
            search_cap_value=search_cap_value,
            map_max_candidates_value=map_max_candidates_value,
            map_max_chars_value=map_max_chars_value,
            reflection_max_hops=reflection_max_hops,
            search_query=search_query,
            search_limit=search_limit,
            rerank=rerank,
            keyword_search=keyword_search,
            hybrid_alpha=hybrid_alpha,
            include_evidence=include_evidence,
            evidence_strategy=evidence_strategy,
            evidence_max_chars=evidence_max_chars,
            evidence_context_lines=evidence_context_lines,
            user_id=user_id,
            filters=filters,
            categories=categories,
            agent_id=agent_id,
            run_id=run_id,
            app_id=app_id,
        )
        reason_codes.extend(orchestration_reasons)

        # Always use full retrieval context — proposition context (Phase 3)
        # is deferred until episodic event coverage is proven reliable.
        context = self._build_orchestrated_context(
            results=results,
            event_hits=event_hits,
            hierarchical_anchors=hierarchical_anchors,
            max_results=context_limit,
            max_chars=max_context_chars,
            per_result_max_chars=evidence_max_chars,
        )
        if facts:
            fact_context = render_fact_context(facts, max_facts=20)
            if fact_context:
                if mode == "strict":
                    context = "Canonical Facts:\n" + fact_context
                else:
                    context = "Canonical Facts:\n" + fact_context + "\n\nRetrieved Context:\n" + context

        self._record_cost_fn(
            phase="query",
            user_id=user_id,
            llm_calls=llm_calls_used,
            input_tokens=0.0,
            output_tokens=0.0,
            embed_calls=0.0,
        )

        intent_coverage = float(coverage.get("intent_coverage", coverage.get("coverage_ratio", 0.0)) or 0.0)

        # Dhee: Self-evolution — record answer generation signal
        evolution_layer = self._evolution_layer_fn()
        if evolution_layer and reduced_answer:
            try:
                source_ids = [r.get("id", "") for r in results[:context_limit] if r.get("id")]
                source_texts = [r.get("memory", "") for r in results[:context_limit] if r.get("memory")]
                evolution_layer.on_answer_generated(
                    query=query,
                    answer=str(reduced_answer),
                    source_memory_ids=source_ids,
                    source_texts=source_texts,
                    user_id=user_id or "default",
                )
            except Exception as e:
                logger.debug("Evolution answer hook skipped: %s", e)

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

    # -- Map-reduce execution -------------------------------------------------

    def _execute_map_reduce(
        self,
        *,
        query_plan: Any,
        orchestrator_llm: Optional[Any],
        results: List[Dict[str, Any]],
        event_hits: Optional[List[Dict[str, Any]]] = None,
        coverage: Optional[Dict[str, Any]],
        query: str,
        question_type: str,
        question_date: str,
        mode: str,
        search_cap_value: int,
        map_max_candidates_value: int,
        map_max_chars_value: int,
        reflection_max_hops: Optional[int],
        search_query: str,
        search_limit: int,
        rerank: bool,
        keyword_search: bool,
        hybrid_alpha: float,
        include_evidence: bool,
        evidence_strategy: str,
        evidence_max_chars: int,
        evidence_context_lines: int,
        user_id: str,
        filters: Optional[Dict[str, Any]],
        categories: Optional[List[str]],
        agent_id: Optional[str],
        run_id: Optional[str],
        app_id: Optional[str],
    ) -> Tuple[Optional[str], List[Dict[str, Any]], bool, int, float, bool, List[str], List[Dict[str, Any]]]:
        """Execute map-reduce orchestration with optional reflection.

        Tries event-first reduction (zero LLM cost) before falling back
        to LLM-based atomic fact extraction.

        Returns:
            (
                reduced_answer,
                facts,
                map_reduce_used,
                reflection_hops,
                llm_calls_used,
                cache_hit,
                reason_codes,
                updated_results,
            )
        """
        reduced_answer: Optional[str] = None
        facts: List[Dict[str, Any]] = []
        map_reduce_used = False
        reflection_hops = 0
        llm_calls_used = 0.0
        cache_hit = False
        reason_codes: List[str] = []
        active_orchestrator_llm = orchestrator_llm or self._llm_fn()
        orch_cfg = getattr(self._config, "orchestration", None)
        max_query_llm_calls = int(getattr(orch_cfg, "max_query_llm_calls", 2) or 2)

        coverage_sufficient = bool((coverage or {}).get("sufficient"))
        if coverage_sufficient:
            reason_codes.append("coverage_sufficient")
        else:
            reason_codes.append("coverage_insufficient")

        inconsistency = deterministic_inconsistency_check(
            question=query,
            intent=query_plan.intent,
            results=results,
            coverage=coverage,
        )
        inconsistency_detected = bool(inconsistency.get("inconsistent"))
        if inconsistency_detected:
            reason_codes.extend(list(inconsistency.get("reasons") or []))

        # NOTE: Event-first reduction (Phase 2) disabled — episodic events
        # alone lack sufficient coverage for accurate multi-session counting.
        # The LLM-based map-reduce path below is more reliable.

        should_run_map_reduce = bool(
            query_plan.should_map_reduce
            and active_orchestrator_llm is not None
            and results
            and (mode in ("strict", "hybrid") or not coverage_sufficient or inconsistency_detected)
        )
        if query_plan.should_map_reduce and active_orchestrator_llm is None:
            reason_codes.append("no_orchestrator_llm")
        if should_run_map_reduce and max_query_llm_calls <= 0:
            reason_codes.append("query_llm_budget_exhausted")
            should_run_map_reduce = False

        if should_run_map_reduce:
            cache_key = self._build_reducer_cache_key(
                user_id=user_id,
                intent_value=query_plan.intent.value,
                query=query,
                results=results,
            )
            cached = self._get_reducer_cache(cache_key)
            if cached and str(cached.get("reduced_answer") or "").strip():
                cached_answer = str(cached.get("reduced_answer") or "").strip()
                if not is_low_confidence_answer(cached_answer):
                    reduced_answer = cached_answer
                    facts = list(cached.get("facts") or [])
                    cache_hit = True
                    reason_codes.append("reducer_cache_hit")

            if not cache_hit:
                map_candidates = build_map_candidates(
                    results,
                    max_candidates=map_max_candidates_value,
                    per_candidate_max_chars=map_max_chars_value,
                )
                if llm_calls_used < float(max_query_llm_calls):
                    facts = extract_atomic_facts(
                        llm=active_orchestrator_llm,
                        question=query,
                        question_type=question_type,
                        question_date=question_date,
                        candidates=map_candidates,
                    )
                    reduced_answer, _ = reduce_atomic_facts(
                        question=query,
                        intent=query_plan.intent,
                        facts=facts,
                    )
                    llm_calls_used += 1.0
                    map_reduce_used = True
                    reason_codes.append("map_reduce_executed")
                    if reduced_answer or facts:
                        self._put_reducer_cache(
                            cache_key=cache_key,
                            reduced_answer=reduced_answer,
                            facts=facts,
                        )
                else:
                    reason_codes.append("query_llm_budget_exhausted")

            max_hops = int(
                reflection_max_hops
                if reflection_max_hops is not None
                else getattr(self._config.orchestration, "reflection_max_hops", 1)
            )
            if (
                max_hops > 0
                and (not reduced_answer or is_low_confidence_answer(reduced_answer))
                and search_limit < search_cap_value
                and llm_calls_used < float(max_query_llm_calls)
            ):
                reflection_hops = 1
                reason_codes.append("reflection_executed")
                expanded_limit = min(search_cap_value, max(search_limit + 8, search_limit * 2))
                reflection_payload = self._search_fn(
                    query=search_query,
                    user_id=user_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    app_id=app_id,
                    filters=filters,
                    categories=categories,
                    limit=expanded_limit,
                    rerank=rerank,
                    keyword_search=keyword_search,
                    hybrid_alpha=hybrid_alpha,
                    include_evidence=include_evidence,
                    evidence_strategy=evidence_strategy,
                    evidence_max_chars=evidence_max_chars,
                    evidence_context_lines=evidence_context_lines,
                )
                reflected_results = list(reflection_payload.get("results", []))
                merged: Dict[str, Dict[str, Any]] = {}
                for row in results + reflected_results:
                    memory_id = str(row.get("id") or "")
                    existing = merged.get(memory_id)
                    if not existing or float(row.get("composite_score", row.get("score", 0.0))) > float(
                        existing.get("composite_score", existing.get("score", 0.0))
                    ):
                        merged[memory_id] = row
                results = sorted(
                    merged.values(),
                    key=lambda row: float(row.get("composite_score", row.get("score", 0.0))),
                    reverse=True,
                )
                map_candidates = build_map_candidates(
                    results,
                    max_candidates=map_max_candidates_value,
                    per_candidate_max_chars=map_max_chars_value,
                )
                if llm_calls_used < float(max_query_llm_calls):
                    facts = extract_atomic_facts(
                        llm=active_orchestrator_llm,
                        question=query,
                        question_type=question_type,
                        question_date=question_date,
                        candidates=map_candidates,
                    )
                    reduced_answer, _ = reduce_atomic_facts(
                        question=query,
                        intent=query_plan.intent,
                        facts=facts,
                    )
                    llm_calls_used += 1.0
                    map_reduce_used = True
                    if reduced_answer or facts:
                        self._put_reducer_cache(
                            cache_key=self._build_reducer_cache_key(
                                user_id=user_id,
                                intent_value=query_plan.intent.value,
                                query=query,
                                results=results,
                            ),
                            reduced_answer=reduced_answer,
                            facts=facts,
                        )
                else:
                    reason_codes.append("query_llm_budget_exhausted")
            elif (
                max_hops > 0
                and (not reduced_answer or is_low_confidence_answer(reduced_answer))
                and search_limit < search_cap_value
            ):
                reason_codes.append("reflection_skipped_budget")

        return (
            reduced_answer,
            facts,
            map_reduce_used,
            reflection_hops,
            llm_calls_used,
            cache_hit,
            list(dict.fromkeys(reason_codes)),
            results,
        )
