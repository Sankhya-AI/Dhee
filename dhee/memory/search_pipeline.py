"""Memory search pipeline: vector search, boosting, reranking, evidence.

Extracted from memory/main.py — centralizes the full search path so that
FullMemory.search() becomes a thin delegation wrapper.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from dhee.core.intent import QueryIntent, classify_intent
from dhee.core.retrieval import composite_score, tokenize, HybridSearcher
from dhee.memory.retrieval_helpers import (
    ECHO_STOP_WORDS,
    build_rerank_snippet,
    calculate_echo_boost,
    compute_temporal_boost,
    term_overlap_count,
    truncate_rerank_text,
)
from dhee.memory.scoping import SCOPE_VALUES, DEFAULT_SCOPE_WEIGHTS, MemoryScope
from dhee.memory.utils import build_filters_and_metadata, matches_filters
from dhee.memory.vectors import collapse_vector_results, resolve_memory_id

logger = logging.getLogger(__name__)


class SearchPipeline:
    """Handles the full memory search path: vector search, boosting, reranking, evidence."""

    def __init__(
        self,
        *,
        db,
        embedder,
        config,
        vector_store,
        echo_processor_fn: Optional[Callable] = None,
        category_processor_fn: Optional[Callable] = None,
        reranker_fn: Optional[Callable] = None,
        scope_resolver=None,
        context_resolver_fn: Optional[Callable] = None,
        evolution_layer_fn: Optional[Callable] = None,
        buddhi_layer_fn: Optional[Callable] = None,
        knowledge_graph_fn: Optional[Callable] = None,
        executor: Optional[Any] = None,
        record_cost_fn: Optional[Callable] = None,
        check_promotion_fn: Optional[Callable] = None,
        persist_categories_fn: Optional[Callable] = None,
        is_expired_fn: Optional[Callable] = None,
        update_vectors_for_memory_fn: Optional[Callable] = None,
    ):
        self._db = db
        self._embedder = embedder
        self._config = config
        self._vector_store = vector_store
        self._echo_processor_fn = echo_processor_fn or (lambda: None)
        self._category_processor_fn = category_processor_fn or (lambda: None)
        self._reranker_fn = reranker_fn or (lambda: None)
        self._scope_resolver = scope_resolver
        self._context_resolver_fn = context_resolver_fn or (lambda: None)
        self._evolution_layer_fn = evolution_layer_fn or (lambda: None)
        self._buddhi_layer_fn = buddhi_layer_fn or (lambda: None)
        self._knowledge_graph_fn = knowledge_graph_fn or (lambda: None)
        self._executor = executor
        self._record_cost_fn = record_cost_fn
        self._check_promotion_fn = check_promotion_fn
        self._persist_categories_fn = persist_categories_fn
        self._is_expired_fn = is_expired_fn or (lambda m: False)
        self._update_vectors_for_memory_fn = update_vectors_for_memory_fn

    # -- Config shorthand accessors -------------------------------------------

    @property
    def _echo_config(self):
        return self._config.echo

    @property
    def _category_config(self):
        return self._config.category

    @property
    def _graph_config(self):
        return self._config.graph

    @property
    def _fade_config(self):
        return self._config.fade

    @property
    def _distillation_config(self):
        return getattr(self._config, "distillation", None)

    @property
    def _parallel_config(self):
        return getattr(self._config, "parallel", None)

    # -- Main search ----------------------------------------------------------

    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        filters: Dict[str, Any] = None,
        categories: List[str] = None,
        agent_category: Optional[str] = None,
        connector_ids: Optional[List[str]] = None,
        scope_filter: Optional[Union[str, List[str]]] = None,
        limit: int = 100,
        rerank: bool = True,
        keyword_search: bool = False,
        hybrid_alpha: float = 0.7,
        min_strength: float = 0.1,
        boost_on_access: bool = True,
        use_echo_rerank: bool = True,
        use_category_boost: bool = True,
        include_evidence: bool = False,
        evidence_strategy: str = "vector_or_snippet",
        evidence_max_chars: int = 900,
        evidence_context_lines: int = 1,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not query or not query.strip():
            return {"results": [], "context_packet": None}

        # Dhee: Run context resolver for metadata enrichment only.
        # NEVER short-circuit: the resolver can't guarantee complete coverage
        # across all relevant sessions for multi-session queries (count, set,
        # temporal, sum).  Example: "how many tanks did I buy?" needs 3-5
        # sessions but the resolver finds 1 fact and would return only that.
        # Instead, pass resolver hints to the vector pipeline as boosting
        # signals.
        resolver_result = None
        context_resolver = self._context_resolver_fn()
        if context_resolver:
            try:
                resolver_result = context_resolver.resolve(query, user_id=user_id or "default")
            except Exception as e:
                logger.debug("Context resolver skipped: %s", e)

        _, effective_filters = build_filters_and_metadata(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            input_filters=filters,
        )
        if app_id:
            effective_filters["app_id"] = app_id

        if isinstance(connector_ids, str):
            connector_ids = [connector_ids]
        normalized_agent_category = self._scope_resolver.normalize_agent_category(agent_category)
        normalized_connector_ids = [
            cid for cid in (self._scope_resolver.normalize_connector_id(c) for c in (connector_ids or [])) if cid
        ]
        normalized_scope_filter = None
        if scope_filter:
            if isinstance(scope_filter, str):
                scope_filter = [scope_filter]
            normalized_scope_filter = {
                scope_value
                for scope_value in (self._scope_resolver.normalize_scope(s) for s in scope_filter)
                if scope_value
            }

        # Gap 5: Classify query intent for routing
        query_intent = None
        if (
            self._distillation_config
            and self._distillation_config.enable_intent_routing
            and self._distillation_config.enable_memory_types
        ):
            query_intent = classify_intent(query)

        query_embedding = self._embedder.embed(query, memory_action="search")
        vector_results = self._vector_store.search(
            query=query,
            vectors=query_embedding,
            limit=limit * 2,
            filters=effective_filters,
        )

        if agent_id and user_id:
            connector_filters = {
                key: value
                for key, value in effective_filters.items()
                if key not in {"agent_id", "run_id", "app_id"}
            }
            connector_filters["user_id"] = user_id
            connector_results = self._vector_store.search(
                query=query,
                vectors=query_embedding,
                limit=limit * 2,
                filters=connector_filters,
            )

            merged = {result.id: result for result in vector_results}
            for result in connector_results:
                existing = merged.get(result.id)
                if not existing or result.score > existing.score:
                    merged[result.id] = result
            vector_results = list(merged.values())

        vector_results = collapse_vector_results(vector_results)

        # Prepare query terms for echo-based re-ranking (strip punctuation)
        query_lower = query.lower()
        query_terms = set(
            re.sub(r"[^\w\s]", "", query_lower).split()
        )

        # CategoryMem: Detect relevant categories for the query
        category_processor = self._category_processor_fn()
        query_category_id = None
        related_category_ids: Set[str] = set()
        if category_processor and use_category_boost:
            category_match = category_processor.detect_category(
                query, use_llm=False  # Fast match only for search
            )
            if category_match.confidence > 0.4:
                query_category_id = category_match.category_id
                related_category_ids = set(
                    category_processor.find_related_categories(query_category_id)
                )
                # Record access to category
                category_processor.access_category(query_category_id)

        # Phase 2: Bulk-fetch all candidate memories to eliminate N+1 queries.
        candidate_ids = [resolve_memory_id(vr) for vr in vector_results]
        vr_by_id = {resolve_memory_id(vr): vr for vr in vector_results}
        memories_bulk = self._db.get_memories_bulk(candidate_ids)

        results: List[Dict[str, Any]] = []
        access_ids: List[str] = []
        strength_updates: Dict[str, float] = {}
        promotion_ids: List[str] = []
        reecho_ids: List[str] = []
        subscriber_ids: List[str] = []

        # Pre-create HybridSearcher outside the loop to avoid re-allocation per result.
        hybrid_searcher = HybridSearcher(alpha=hybrid_alpha) if keyword_search else None

        echo_processor = self._echo_processor_fn()
        knowledge_graph = self._knowledge_graph_fn()

        for memory_id in candidate_ids:
            memory = memories_bulk.get(memory_id)
            if not memory:
                continue

            # Skip expired memories (cleanup happens in apply_decay, not during search)
            if self._is_expired_fn(memory):
                continue

            if memory.get("strength", 1.0) < min_strength:
                continue
            if categories and not any(c in memory.get("categories", []) for c in categories):
                continue
            if filters and not matches_filters({**memory, **memory.get("metadata", {})}, filters):
                continue

            metadata = memory.get("metadata", {}) or {}
            scope = self._scope_resolver.resolve_scope(memory)
            if normalized_scope_filter and scope not in normalized_scope_filter:
                continue
            if not self._scope_resolver.allows_scope(
                memory,
                user_id=user_id,
                agent_id=agent_id,
                agent_category=normalized_agent_category,
                connector_ids=normalized_connector_ids,
            ):
                continue

            vr = vr_by_id[memory_id]
            similarity = float(vr.score)
            strength = float(memory.get("strength", 1.0))

            # Hybrid search: combine semantic and keyword scores
            keyword_score = 0.0
            if hybrid_searcher:
                scores = hybrid_searcher.score_memory(
                    query_terms=query_terms,
                    semantic_similarity=similarity,
                    memory_content=memory.get("memory", ""),
                    echo_keywords=metadata.get("echo_keywords", []),
                    echo_paraphrases=metadata.get("echo_paraphrases", []),
                    strength=strength,
                )
                combined = scores["composite_score"]
                keyword_score = scores["keyword_score"]
            else:
                combined = composite_score(similarity, strength)

            combined *= self._scope_resolver.get_scope_weight(scope)

            # EchoMem: Apply echo-based re-ranking boost
            echo_boost = 0.0
            if use_echo_rerank and self._echo_config.enable_echo:
                echo_boost = calculate_echo_boost(query_lower, query_terms, metadata)
                combined = combined * (1 + echo_boost)

            # CategoryMem: Apply category-based re-ranking boost
            category_boost = 0.0
            memory_categories = set(memory.get("categories", []))
            if use_category_boost and category_processor and query_category_id:
                if query_category_id in memory_categories:
                    category_boost = self._category_config.category_boost_weight
                elif memory_categories & related_category_ids:
                    category_boost = self._category_config.cross_category_boost
                combined = combined * (1 + category_boost)

            # Gap 5: Intent-based retrieval routing boost
            intent_boost = 0.0
            mem_type = memory.get("memory_type", "semantic")
            if query_intent and self._distillation_config:
                dc = self._distillation_config
                if query_intent == QueryIntent.EPISODIC and mem_type == "episodic":
                    intent_boost = dc.episodic_boost
                elif query_intent == QueryIntent.SEMANTIC and mem_type == "semantic":
                    intent_boost = dc.semantic_boost
                elif query_intent == QueryIntent.MIXED:
                    intent_boost = dc.intersection_boost
                combined = combined * (1 + intent_boost)

            # Bitemporal recency policy: boost/penalize memories using event_time vs query recency signals.
            temporal_boost = compute_temporal_boost(
                query=query,
                metadata=metadata,
                query_intent=query_intent,
            )
            if temporal_boost:
                combined = combined * (1 + temporal_boost)

            # KnowledgeGraph: Boost for memories sharing entities with query terms
            graph_boost = 0.0
            if knowledge_graph:
                memory_entities = knowledge_graph.memory_entities.get(memory["id"], set())
                for entity_name in memory_entities:
                    if entity_name.lower() in query_lower or any(
                        term in entity_name.lower() for term in query_terms
                    ):
                        graph_boost = self._graph_config.graph_boost_weight
                        break
                combined = combined * (1 + graph_boost)

            # Procedural: boost automatic procedures in search results
            proc_boost = 0.0
            if self._config.procedural.automaticity_boost_in_search:
                automaticity = metadata.get("proc_automaticity", 0)
                if isinstance(automaticity, (int, float)) and automaticity >= 0.5:
                    proc_boost = float(automaticity) * self._config.procedural.automaticity_boost_in_search_weight
                    combined = combined * (1 + proc_boost)

            # Salience: boost high-salience memories
            salience_boost = 0.0
            if self._config.salience.enable_salience:
                sal_score = metadata.get("sal_salience_score", 0)
                if isinstance(sal_score, (int, float)) and sal_score > 0:
                    salience_boost = float(sal_score) * self._config.salience.salience_boost_weight
                    combined = combined * (1 + salience_boost)

            if boost_on_access:
                access_ids.append(memory["id"])
                if self._fade_config.access_strength_boost > 0:
                    boosted_strength = min(1.0, strength + self._fade_config.access_strength_boost)
                    if boosted_strength != strength:
                        strength_updates[memory["id"]] = boosted_strength
                        strength = boosted_strength
                promotion_ids.append(memory["id"])
                # EchoMem: Re-echo on frequent access
                if (
                    echo_processor
                    and self._echo_config.reecho_on_access
                    and memory.get("access_count", 0) >= self._echo_config.reecho_threshold
                    and metadata.get("echo_depth") != "deep"
                ):
                    reecho_ids.append(memory["id"])
                if agent_id:
                    subscriber_ids.append(memory["id"])

            results.append(
                {
                    "id": memory["id"],
                    "memory": memory.get("memory", ""),
                    "user_id": memory.get("user_id"),
                    "agent_id": memory.get("agent_id"),
                    "run_id": memory.get("run_id"),
                    "app_id": memory.get("app_id"),
                    "metadata": memory.get("metadata", {}),
                    "categories": memory.get("categories", []),
                    "agent_category": metadata.get("agent_category"),
                    "connector_id": metadata.get("connector_id"),
                    "immutable": memory.get("immutable", False),
                    "created_at": memory.get("created_at"),
                    "updated_at": memory.get("updated_at"),
                    "score": similarity,
                    "keyword_score": keyword_score,
                    "strength": strength,
                    "layer": memory.get("layer", "sml"),
                    "access_count": memory.get("access_count", 0),
                    "last_accessed": memory.get("last_accessed"),
                    "composite_score": combined,
                    "scope": scope,
                    "namespace": memory.get("namespace", "default"),
                    "confidentiality_scope": memory.get("confidentiality_scope", "work"),
                    "source_type": memory.get("source_type"),
                    "source_app": memory.get("source_app"),
                    "source_event_id": memory.get("source_event_id"),
                    "status": memory.get("status", "active"),
                    "importance": memory.get("importance", 0.5),
                    "sensitivity": memory.get("sensitivity", "normal"),
                    "echo_boost": echo_boost,
                    "category_boost": category_boost,
                    "graph_boost": graph_boost,
                    "intent_boost": intent_boost,
                    "proc_boost": proc_boost,
                    "salience_boost": salience_boost,
                    "temporal_boost": temporal_boost,
                    "memory_type": mem_type,
                    "query_intent": query_intent.value if query_intent else None,
                    "confidence": metadata.get("mm_confidence"),
                    "conversation_context": memory.get("conversation_context"),
                    "enrichment_status": memory.get("enrichment_status", "complete"),
                }
            )

        # Phase 2: Batch DB writes instead of per-result round-trips.
        if access_ids:
            self._db.increment_access_bulk(access_ids)
        if strength_updates:
            self._db.update_strength_bulk(strength_updates)
        if self._check_promotion_fn:
            for mid in promotion_ids:
                self._check_promotion_fn(mid)
        # Site 2: Parallel re-echo
        if (
            reecho_ids
            and self._executor is not None
            and self._parallel_config
            and self._parallel_config.parallel_reecho
            and len(reecho_ids) > 1
        ):
            self._executor.run_parallel([
                (self._reecho_memory, (mid,)) for mid in reecho_ids
            ])
        else:
            for mid in reecho_ids:
                self._reecho_memory(mid)
        if agent_id:
            for mid in subscriber_ids:
                self._db.add_memory_subscriber(mid, f"agent:{agent_id}", ref_type="weak")

        # Persist category access updates
        if self._persist_categories_fn and category_processor:
            self._persist_categories_fn()

        results.sort(key=lambda x: x["composite_score"], reverse=True)

        # Neural reranking: cross-encoder second stage on top candidates
        rerank_cfg = getattr(self._config, "rerank", None)
        reranker = self._reranker_fn()
        if rerank and reranker and results:
            try:
                rerank_opts = (rerank_cfg.config if rerank_cfg else {}) or {}
                passage_strategy = str(rerank_opts.get("passage_strategy", "full")).strip().lower()
                if passage_strategy not in {"full", "snippet", "vector_text"}:
                    passage_strategy = "full"
                try:
                    max_passage_chars = int(rerank_opts.get("max_passage_chars", 3500))
                except (TypeError, ValueError):
                    max_passage_chars = 3500
                max_passage_chars = max(1, max_passage_chars)
                try:
                    context_lines = int(rerank_opts.get("context_lines", 1))
                except (TypeError, ValueError):
                    context_lines = 1
                context_lines = max(0, context_lines)
                try:
                    candidates_multiplier = int(rerank_opts.get("candidates_multiplier", 1))
                except (TypeError, ValueError):
                    candidates_multiplier = 1
                candidates_multiplier = max(1, candidates_multiplier)

                try:
                    limit_for_rerank = int(limit)
                except (TypeError, ValueError):
                    limit_for_rerank = 1
                limit_for_rerank = max(1, limit_for_rerank)
                rerank_k = min(len(results), limit_for_rerank * candidates_multiplier)
                rerank_window = results[:rerank_k]
                passages: List[str] = []
                for row in rerank_window:
                    passage = self._build_rerank_passage(
                        result=row,
                        query_terms=query_terms,
                        strategy=passage_strategy,
                        max_chars=max_passage_chars,
                        context_lines=context_lines,
                    )
                    row["rerank_passage_chars"] = len(passage)
                    passages.append(passage)
                reranked = reranker.rerank(
                    query=query,
                    passages=passages,
                    top_n=rerank_cfg.top_n if rerank_cfg and rerank_cfg.top_n > 0 else 0,
                )
                # Re-order results by reranker logits
                idx_to_logit = {r["index"]: r["logit"] for r in reranked}
                for i, result in enumerate(rerank_window):
                    result["rerank_logit"] = idx_to_logit.get(i, float("-inf"))
                results[:rerank_k] = sorted(
                    rerank_window,
                    key=lambda x: x.get("rerank_logit", float("-inf")),
                    reverse=True,
                )
            except Exception as e:
                logger.warning("Reranking failed, using composite_score order: %s", e)

        if include_evidence and results:
            try:
                strategy = str(evidence_strategy or "vector_or_snippet").strip().lower()
                if strategy not in {"vector_or_snippet", "vector_text", "snippet", "full"}:
                    strategy = "vector_or_snippet"
                max_chars = max(1, int(evidence_max_chars))
                context_lines = max(0, int(evidence_context_lines))
                try:
                    evidence_limit = int(limit)
                except (TypeError, ValueError):
                    evidence_limit = len(results)
                if evidence_limit <= 0:
                    evidence_limit = len(results)
                for result in results[: min(len(results), evidence_limit)]:
                    evidence_text, evidence_source = self._build_result_evidence(
                        result=result,
                        query_terms=query_terms,
                        strategy=strategy,
                        max_chars=max_chars,
                        context_lines=context_lines,
                    )
                    result["evidence_text"] = evidence_text
                    result["evidence_source"] = evidence_source
                    result["evidence_chars"] = len(evidence_text)
            except Exception as e:
                logger.debug("Evidence extraction failed: %s", e)

        # Metamemory: auto-log knowledge gap when search returns no results
        if not results and self._config.metamemory.auto_log_gaps:
            try:
                from engram_metamemory.metamemory import Metamemory as _Metamemory
                _mm = _Metamemory(self, user_id=user_id or "default")
                _mm.log_knowledge_gap(query=query, reason="empty_search")
            except ImportError:
                pass
            except Exception as e:
                logger.debug("Auto-gap logging failed: %s", e)

        # Dhee: Self-evolution — record retrieval quality signal
        evolution_layer = self._evolution_layer_fn()
        if evolution_layer and results:
            try:
                evolution_layer.on_search_results(
                    query=query,
                    results=results[:limit],
                    user_id=user_id or "default",
                )
            except Exception as e:
                logger.debug("Evolution search hook skipped: %s", e)

        # Buddhi search hook: piggyback proactive signals (intentions, insights)
        final_results = results[:limit]
        buddhi_layer = self._buddhi_layer_fn()
        if buddhi_layer and final_results:
            try:
                buddhi_signals = buddhi_layer.on_search(
                    query=query,
                    results=final_results,
                    user_id=user_id or "default",
                )
                if buddhi_signals:
                    return {"results": final_results, "buddhi": buddhi_signals}
            except Exception as e:
                logger.debug("Buddhi search hook skipped: %s", e)

        return {"results": final_results}

    # -- Rerank passage builder -----------------------------------------------

    def _build_rerank_passage(
        self,
        *,
        result: Dict[str, Any],
        query_terms: set,
        strategy: str,
        max_chars: int,
        context_lines: int,
    ) -> str:
        memory_text = str(result.get("memory", "") or "")
        strategy = str(strategy or "full").strip().lower()
        if strategy == "vector_text":
            memory_id = str(result.get("id", "") or "")
            vector_text = self._select_vector_text_for_memory(memory_id=memory_id, query_terms=query_terms)
            if vector_text:
                return truncate_rerank_text(vector_text, max_chars)
            return truncate_rerank_text(memory_text, max_chars)
        if strategy == "snippet":
            return build_rerank_snippet(
                memory_text=memory_text,
                query_terms=query_terms,
                max_chars=max_chars,
                context_lines=context_lines,
            )
        return truncate_rerank_text(memory_text, max_chars)

    # -- Evidence builder -----------------------------------------------------

    def _build_result_evidence(
        self,
        *,
        result: Dict[str, Any],
        query_terms: set,
        strategy: str,
        max_chars: int,
        context_lines: int,
    ) -> Tuple[str, str]:
        normalized_strategy = str(strategy or "vector_or_snippet").strip().lower()
        if normalized_strategy not in {"vector_or_snippet", "vector_text", "snippet", "full"}:
            normalized_strategy = "vector_or_snippet"

        memory_text = str(result.get("memory", "") or "")
        memory_id = str(result.get("id", "") or "")

        # Minimum evidence size: if vector_text or snippet is too small relative
        # to the full memory, fall through to a richer strategy to avoid losing context.
        min_evidence_chars = min(300, len(memory_text) // 3) if memory_text else 0

        if normalized_strategy in {"vector_or_snippet", "vector_text"}:
            vector_text = self._select_vector_text_for_memory(memory_id=memory_id, query_terms=query_terms)
            if vector_text and len(vector_text) >= min_evidence_chars:
                return truncate_rerank_text(vector_text, max_chars), "vector_text"
            if normalized_strategy == "vector_text":
                # vector_text too small — fall back to full memory
                return truncate_rerank_text(memory_text, max_chars), "memory"

        if normalized_strategy in {"vector_or_snippet", "snippet"}:
            snippet = build_rerank_snippet(
                memory_text=memory_text,
                query_terms=query_terms,
                max_chars=max_chars,
                context_lines=context_lines,
            )
            if snippet and len(snippet) >= min_evidence_chars:
                return snippet, "snippet"

        return truncate_rerank_text(memory_text, max_chars), "memory"

    # -- Vector text selector -------------------------------------------------

    def _select_vector_text_for_memory(self, memory_id: str, query_terms: set) -> Optional[str]:
        if not memory_id:
            return None
        try:
            vector_nodes = self._vector_store.list(filters={"memory_id": memory_id})
        except Exception as e:
            logger.debug("Unable to list vector nodes for memory %s: %s", memory_id, e)
            return None
        if not vector_nodes:
            return None

        content_terms = {
            term.lower()
            for term in query_terms
            if isinstance(term, str) and len(term) > 3 and term.lower() not in ECHO_STOP_WORDS
        }
        best_fact: Tuple[int, int, str] = (-1, -1, "")
        best_text: Tuple[int, int, str] = (-1, -1, "")

        for node in vector_nodes:
            payload = getattr(node, "payload", None) or {}
            if not isinstance(payload, dict):
                continue

            fact_text = payload.get("fact_text")
            if isinstance(fact_text, str) and fact_text.strip():
                cleaned_fact = fact_text.strip()
                overlap = term_overlap_count(cleaned_fact, content_terms)
                fact_rank = (overlap, len(cleaned_fact), cleaned_fact)
                if fact_rank > best_fact:
                    best_fact = fact_rank

            text_value = payload.get("text")
            if isinstance(text_value, str) and text_value.strip():
                cleaned_text = text_value.strip()
                overlap = term_overlap_count(cleaned_text, content_terms)
                text_rank = (overlap, len(cleaned_text), cleaned_text)
                if text_rank > best_text:
                    best_text = text_rank

        if best_fact[2]:
            return best_fact[2]
        if best_text[2]:
            return best_text[2]
        return None

    # -- Re-echo on access ----------------------------------------------------

    def _reecho_memory(self, memory_id: str) -> None:
        """Re-process a memory through deeper echo to strengthen it."""
        memory = self._db.get_memory(memory_id)
        echo_processor = self._echo_processor_fn()
        if not memory or not echo_processor:
            return

        try:
            echo_result = echo_processor.reecho(memory)
            metadata = memory.get("metadata", {})
            metadata.update(echo_result.to_metadata())

            # Update memory with new echo data and boosted strength
            new_strength = min(1.0, memory.get("strength", 1.0) * 1.1)  # 10% boost
            self._db.update_memory(memory_id, {
                "metadata": metadata,
                "strength": new_strength,
            })
            self._db.log_event(memory_id, "REECHO", old_strength=memory.get("strength"), new_strength=new_strength)
            if self._update_vectors_for_memory_fn:
                self._update_vectors_for_memory_fn(memory_id, metadata)
        except Exception as e:
            logger.warning("Re-echo failed for memory %s: %s", memory_id, e)
