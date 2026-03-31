"""Memory write pipeline: processing, enrichment, indexing.

Extracted from memory/main.py — centralizes the full write path:
_process_single_memory, _process_single_memory_lite, and supporting
helpers (resolve_memory_metadata, encode_memory, extract_memories,
classify_memory_type, select_primary_text).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from dhee.core.conflict import resolve_conflict
from dhee.core.echo import EchoDepth, EchoResult
from dhee.core.traces import initialize_traces
from dhee.memory.cost import estimate_token_count, estimate_output_tokens
from dhee.memory.episodic import index_episodic_events_for_memory as _index_episodic
from dhee.memory.retrieval_helpers import (
    attach_bitemporal_metadata,
    normalize_bitemporal_value,
)
from dhee.memory.utils import (
    normalize_categories,
    parse_messages,
    strip_code_fences,
)
from dhee.memory.vectors import build_index_vectors
from dhee.utils.prompts import AGENT_MEMORY_EXTRACTION_PROMPT, MEMORY_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)


class MemoryWritePipeline:
    """Handles the full memory write path: processing, enrichment, indexing.

    Receives all dependencies via constructor so the class carries no hidden
    coupling to FullMemory internals.  Each ``self.*_fn`` callback is a thin
    reference to the corresponding method on the owning FullMemory instance.
    """

    def __init__(
        self,
        *,
        db,
        embedder,
        llm,
        config,
        vector_store=None,
        echo_processor_fn: Optional[Callable] = None,
        category_processor_fn: Optional[Callable] = None,
        graph_fn: Optional[Callable] = None,
        scene_processor_fn: Optional[Callable] = None,
        profile_processor_fn: Optional[Callable] = None,
        unified_enrichment_fn: Optional[Callable] = None,
        engram_extractor_fn: Optional[Callable] = None,
        context_resolver_fn: Optional[Callable] = None,
        evolution_layer_fn: Optional[Callable] = None,
        buddhi_layer_fn: Optional[Callable] = None,
        scope_resolver=None,
        executor=None,
        record_cost_fn: Optional[Callable] = None,
        forget_by_query_fn: Optional[Callable] = None,
        demote_existing_fn: Optional[Callable] = None,
        nearest_memory_fn: Optional[Callable] = None,
        assign_to_scene_fn: Optional[Callable] = None,
        update_profiles_fn: Optional[Callable] = None,
        store_prospective_scenes_fn: Optional[Callable] = None,
    ):
        self._db = db
        self._embedder = embedder
        self._llm = llm
        self._config = config
        self._vector_store = vector_store

        # Lazy-property callables — call to get the current processor instance.
        self._echo_processor_fn = echo_processor_fn
        self._category_processor_fn = category_processor_fn
        self._graph_fn = graph_fn
        self._scene_processor_fn = scene_processor_fn
        self._profile_processor_fn = profile_processor_fn
        self._unified_enrichment_fn = unified_enrichment_fn
        self._engram_extractor_fn = engram_extractor_fn
        self._context_resolver_fn = context_resolver_fn
        self._evolution_layer_fn = evolution_layer_fn
        self._buddhi_layer_fn = buddhi_layer_fn

        self._scope_resolver = scope_resolver
        self._executor = executor

        # Callback hooks into owning FullMemory.
        self._record_cost_fn = record_cost_fn
        self._forget_by_query_fn = forget_by_query_fn
        self._demote_existing_fn = demote_existing_fn
        self._nearest_memory_fn = nearest_memory_fn
        self._assign_to_scene_fn = assign_to_scene_fn
        self._update_profiles_fn = update_profiles_fn
        self._store_prospective_scenes_fn = store_prospective_scenes_fn

    # ------------------------------------------------------------------
    # Convenience accessors for lazy processors
    # ------------------------------------------------------------------

    @property
    def _echo_processor(self):
        return self._echo_processor_fn() if self._echo_processor_fn else None

    @property
    def _category_processor(self):
        return self._category_processor_fn() if self._category_processor_fn else None

    @property
    def _graph(self):
        return self._graph_fn() if self._graph_fn else None

    @property
    def _scene_processor(self):
        return self._scene_processor_fn() if self._scene_processor_fn else None

    @property
    def _profile_processor(self):
        return self._profile_processor_fn() if self._profile_processor_fn else None

    @property
    def _unified_enrichment(self):
        return self._unified_enrichment_fn() if self._unified_enrichment_fn else None

    @property
    def _engram_extractor(self):
        return self._engram_extractor_fn() if self._engram_extractor_fn else None

    @property
    def _context_resolver(self):
        return self._context_resolver_fn() if self._context_resolver_fn else None

    @property
    def _evolution_layer(self):
        return self._evolution_layer_fn() if self._evolution_layer_fn else None

    @property
    def _buddhi_layer(self):
        return self._buddhi_layer_fn() if self._buddhi_layer_fn else None

    # ------------------------------------------------------------------
    # Config sub-sections (read-through to self._config)
    # ------------------------------------------------------------------

    @property
    def _fade_config(self):
        return self._config.fade

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
    def _distillation_config(self):
        return getattr(self._config, "distillation", None)

    @property
    def _parallel_config(self):
        return getattr(self._config, "parallel", None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_cost(self, **kwargs) -> None:
        if self._record_cost_fn:
            self._record_cost_fn(**kwargs)

    def _normalize_agent_category(self, category):
        return self._scope_resolver.normalize_agent_category(category) if self._scope_resolver else category

    def _normalize_connector_id(self, connector_id):
        return self._scope_resolver.normalize_connector_id(connector_id) if self._scope_resolver else connector_id

    def _infer_scope(self, **kwargs):
        return self._scope_resolver.infer_scope(**kwargs) if self._scope_resolver else "agent"

    # ------------------------------------------------------------------
    # Extracted public methods
    # ------------------------------------------------------------------

    def resolve_memory_metadata(
        self,
        *,
        content: str,
        mem_metadata: Dict[str, Any],
        explicit_remember: bool,
        agent_id: Optional[str],
        run_id: Optional[str],
        app_id: Optional[str],
        effective_filters: Dict[str, Any],
        agent_category: Optional[str],
        connector_id: Optional[str],
        scope: Optional[str],
        source_app: Optional[str],
    ) -> tuple:
        """Resolve store identifiers, scope, and metadata for a single memory."""
        store_agent_id = agent_id
        store_run_id = run_id
        store_app_id = app_id
        store_filters = dict(effective_filters)
        if "user_id" in store_filters or "agent_id" in store_filters:
            store_filters.pop("run_id", None)

        if explicit_remember:
            store_agent_id = None
            store_run_id = None
            store_app_id = None
            store_filters.pop("agent_id", None)
            store_filters.pop("run_id", None)
            store_filters.pop("app_id", None)
            mem_metadata.pop("agent_id", None)
            mem_metadata.pop("run_id", None)
            mem_metadata.pop("app_id", None)
            mem_metadata["policy_scope"] = "user"
        else:
            mem_metadata["policy_scope"] = "agent"

        mem_metadata["policy_explicit"] = explicit_remember
        resolved_agent_category = self._normalize_agent_category(
            agent_category or mem_metadata.get("agent_category")
        )
        resolved_connector_id = self._normalize_connector_id(
            connector_id or mem_metadata.get("connector_id")
        )
        resolved_scope = self._infer_scope(
            scope=scope or mem_metadata.get("scope"),
            connector_id=resolved_connector_id,
            agent_category=resolved_agent_category,
            policy_explicit=explicit_remember,
            agent_id=store_agent_id,
        )
        mem_metadata["scope"] = resolved_scope
        if resolved_agent_category:
            mem_metadata["agent_category"] = resolved_agent_category
        if resolved_connector_id:
            mem_metadata["connector_id"] = resolved_connector_id
        if source_app or mem_metadata.get("source_app"):
            mem_metadata["source_app"] = source_app or mem_metadata.get("source_app")

        return store_agent_id, store_run_id, store_app_id, store_filters

    def encode_memory(
        self,
        content: str,
        echo_depth: Optional[str],
        mem_categories: List[str],
        mem_metadata: Dict[str, Any],
        initial_strength: float,
    ) -> tuple:
        """Run echo encoding + embedding.

        Returns ``(echo_result, effective_strength, mem_categories, embedding)``.
        """
        echo_result = None
        effective_strength = initial_strength
        echo_proc = self._echo_processor
        if echo_proc and self._echo_config.enable_echo:
            depth_override = EchoDepth(echo_depth) if echo_depth else None
            echo_result = echo_proc.process(content, depth=depth_override)
            effective_strength = initial_strength * echo_result.strength_multiplier
            mem_metadata.update(echo_result.to_metadata())
            if not mem_categories and echo_result.category:
                mem_categories = [echo_result.category]

        primary_text = self.select_primary_text(content, echo_result)
        embedding = self._embedder.embed(primary_text, memory_action="add")
        return echo_result, effective_strength, mem_categories, embedding

    def process_single_memory(
        self,
        *,
        mem: Dict[str, Any],
        processed_metadata: Dict[str, Any],
        effective_filters: Dict[str, Any],
        categories: Optional[List[str]],
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        app_id: Optional[str],
        agent_category: Optional[str],
        connector_id: Optional[str],
        scope: Optional[str],
        source_app: Optional[str],
        immutable: bool,
        expiration_date: Optional[str],
        initial_layer: str,
        initial_strength: float,
        echo_depth: Optional[str],
        memory_id: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process and store a single memory item. Returns result dict or None if skipped."""
        # Late import to avoid circular dep — these are module-level functions in main.py.
        from dhee.memory.main import detect_explicit_intent, detect_sensitive_categories, is_ephemeral, looks_high_confidence

        content = mem.get("content", "").strip()
        if not content:
            return None

        write_llm_calls = 0.0
        write_embed_calls = 0.0
        write_input_tokens = 0.0
        write_output_tokens = 0.0

        def _add_llm_cost(input_tokens: float) -> None:
            nonlocal write_llm_calls, write_input_tokens, write_output_tokens
            tokens = max(0.0, float(input_tokens or 0.0))
            write_llm_calls += 1.0
            write_input_tokens += tokens
            write_output_tokens += estimate_output_tokens(tokens)

        mem_categories = normalize_categories(categories or mem.get("categories"))
        mem_metadata = dict(processed_metadata)
        mem_metadata.update(mem.get("metadata", {}))
        if app_id:
            mem_metadata["app_id"] = app_id

        role = mem_metadata.get("role", "user")
        explicit_intent = detect_explicit_intent(content) if role == "user" else None
        explicit_action = explicit_intent.action if explicit_intent else None
        explicit_remember = bool(mem_metadata.get("explicit_remember")) or explicit_action == "remember"
        explicit_forget = bool(mem_metadata.get("explicit_forget")) or explicit_action == "forget"

        if explicit_forget:
            query = explicit_intent.content if explicit_intent else ""
            forget_filters = {"user_id": user_id} if user_id else dict(effective_filters)
            forget_result = self._forget_by_query_fn(query, forget_filters)
            return {
                "event": "FORGET",
                "query": query,
                "deleted_count": forget_result.get("deleted_count", 0),
                "deleted_ids": forget_result.get("deleted_ids", []),
            }

        if explicit_remember and explicit_intent and explicit_intent.content:
            content = explicit_intent.content

        blocked = detect_sensitive_categories(content)
        allow_sensitive = bool(mem_metadata.get("allow_sensitive"))
        if blocked and not allow_sensitive:
            return {
                "event": "BLOCKED",
                "reason": "sensitive",
                "blocked_categories": blocked,
                "memory": content,
            }

        is_task_or_note = (mem_metadata or {}).get("memory_type") in ("task", "note")
        if not explicit_remember and not is_task_or_note and is_ephemeral(content):
            return {
                "event": "SKIP",
                "reason": "ephemeral",
                "memory": content,
            }

        # --- Deferred enrichment: lite path (0 LLM calls) ---
        enrichment_config = getattr(self._config, "enrichment", None)
        if enrichment_config and enrichment_config.defer_enrichment:
            return self.process_single_memory_lite(
                content=content,
                mem_metadata=mem_metadata,
                mem_categories=mem_categories,
                context_messages=context_messages,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                app_id=app_id,
                effective_filters=effective_filters,
                agent_category=agent_category,
                connector_id=connector_id,
                scope=scope,
                source_app=source_app,
                immutable=immutable,
                expiration_date=expiration_date,
                initial_layer=initial_layer,
                initial_strength=initial_strength,
                explicit_remember=explicit_remember,
                memory_id=memory_id,
            )

        # Resolve store identifiers and scope metadata.
        store_agent_id, store_run_id, store_app_id, store_filters = self.resolve_memory_metadata(
            content=content,
            mem_metadata=mem_metadata,
            explicit_remember=explicit_remember,
            agent_id=agent_id,
            run_id=run_id,
            app_id=app_id,
            effective_filters=effective_filters,
            agent_category=agent_category,
            connector_id=connector_id,
            scope=scope,
            source_app=source_app,
        )

        high_confidence = explicit_remember or looks_high_confidence(content, mem_metadata)
        policy_repeated = False
        low_confidence = False

        # Determine if we should auto-categorize
        cat_proc = self._category_processor
        _should_categorize = (
            cat_proc
            and self._category_config.auto_categorize
            and not mem_categories
        )

        # Pre-extracted data from unified enrichment
        _unified_entities = None
        _unified_profiles = None
        _unified_facts = None

        # Determine echo depth for unified path check
        echo_proc = self._echo_processor
        _depth_for_echo = EchoDepth(echo_depth) if echo_depth else None
        if _depth_for_echo is None and echo_proc and hasattr(echo_proc, '_assess_depth'):
            try:
                _depth_for_echo = echo_proc._assess_depth(content)
            except Exception:
                _depth_for_echo = EchoDepth.MEDIUM

        # Site 0: Unified enrichment (single LLM call for echo+category+entities+profiles)
        unified = self._unified_enrichment
        _use_unified = (
            unified is not None
            and self._echo_config.enable_echo
            and _depth_for_echo != EchoDepth.SHALLOW
        )

        if _use_unified:
            enrichment_config = getattr(self._config, "enrichment", None)
            existing_cats = None
            if cat_proc:
                cats = cat_proc.get_all_categories()
                if cats:
                    existing_cats = "\n".join(
                        f"- {c['id']}: {c['name']} — {c.get('description', '')}"
                        for c in cats[:30]
                    )

            unified_input_tokens = estimate_token_count(content) + estimate_token_count(existing_cats)
            _add_llm_cost(unified_input_tokens)

            enrichment = unified.enrich(
                content=content,
                depth=_depth_for_echo or EchoDepth.MEDIUM,
                existing_categories=existing_cats,
                include_entities=enrichment_config.include_entities if enrichment_config else True,
                include_profiles=enrichment_config.include_profiles if enrichment_config else True,
            )

            # Apply echo result
            echo_result = enrichment.echo_result
            if echo_result:
                effective_strength = initial_strength * echo_result.strength_multiplier
                mem_metadata.update(echo_result.to_metadata())
                if not mem_categories and echo_result.category:
                    mem_categories = [echo_result.category]
            else:
                effective_strength = initial_strength

            # Apply category result
            if enrichment.category_match and not mem_categories:
                mem_categories = [enrichment.category_match.category_id]
                mem_metadata["category_confidence"] = enrichment.category_match.confidence
                mem_metadata["category_auto"] = True

            # Stash entities + profiles + facts for post-store hooks
            _unified_entities = enrichment.entities
            _unified_profiles = enrichment.profile_updates
            _unified_facts = enrichment.facts

            # Generate embedding
            primary_text = self.select_primary_text(content, echo_result)
            embedding = self._embedder.embed(primary_text, memory_action="add")
            write_embed_calls += 1.0

        else:
            # Site 1: Parallel echo encoding + category detection
            _use_parallel = (
                self._executor is not None
                and self._parallel_config
                and self._parallel_config.parallel_add
                and _should_categorize
                and echo_proc
                and self._echo_config.enable_echo
            )

            if _use_parallel:
                depth_for_parallel = EchoDepth(echo_depth) if echo_depth else (_depth_for_echo or EchoDepth(self._echo_config.default_depth))
                if self._echo_config.enable_echo and depth_for_parallel != EchoDepth.SHALLOW:
                    _add_llm_cost(estimate_token_count(content))
                if _should_categorize and self._category_config.use_llm_categorization:
                    _add_llm_cost(estimate_token_count(content))

                def _do_echo():
                    depth_override = EchoDepth(echo_depth) if echo_depth else None
                    return echo_proc.process(content, depth=depth_override)

                def _do_category():
                    return cat_proc.detect_category(
                        content,
                        metadata=mem_metadata,
                        use_llm=self._category_config.use_llm_categorization,
                    )

                echo_result_p, category_match = self._executor.run_parallel([
                    (_do_echo, ()),
                    (_do_category, ()),
                ])

                # Apply echo result
                effective_strength = initial_strength * echo_result_p.strength_multiplier
                mem_metadata.update(echo_result_p.to_metadata())
                if not mem_categories and echo_result_p.category:
                    mem_categories = [echo_result_p.category]

                # Apply category result
                mem_categories = [category_match.category_id]
                mem_metadata["category_confidence"] = category_match.confidence
                mem_metadata["category_auto"] = True

                # Generate embedding (depends on echo result, must be serial)
                primary_text = self.select_primary_text(content, echo_result_p)
                embedding = self._embedder.embed(primary_text, memory_action="add")
                write_embed_calls += 1.0
                echo_result = echo_result_p
            else:
                # Sequential path (original behavior)
                if _should_categorize:
                    if self._category_config.use_llm_categorization:
                        _add_llm_cost(estimate_token_count(content))
                    category_match = cat_proc.detect_category(
                        content,
                        metadata=mem_metadata,
                        use_llm=self._category_config.use_llm_categorization,
                    )
                    mem_categories = [category_match.category_id]
                    mem_metadata["category_confidence"] = category_match.confidence
                    mem_metadata["category_auto"] = True

                # Encode memory (echo + embedding).
                depth_for_encode = EchoDepth(echo_depth) if echo_depth else (_depth_for_echo or EchoDepth(self._echo_config.default_depth))
                if self._echo_config.enable_echo and depth_for_encode != EchoDepth.SHALLOW:
                    _add_llm_cost(estimate_token_count(content))
                echo_result, effective_strength, mem_categories, embedding = self.encode_memory(
                    content, echo_depth, mem_categories, mem_metadata, initial_strength,
                )
                write_embed_calls += 1.0

        nearest, similarity = self._nearest_memory_fn(embedding, store_filters)
        repeated_threshold = max(self._fade_config.conflict_similarity_threshold - 0.05, 0.7)
        if similarity >= repeated_threshold:
            policy_repeated = True
            high_confidence = True

        if not explicit_remember and not high_confidence:
            low_confidence = True

        # Conflict resolution against nearest memory in scope.
        event = "ADD"
        existing = None
        resolution = None
        if nearest and similarity >= self._fade_config.conflict_similarity_threshold:
            existing = nearest

        if existing and self._fade_config.enable_forgetting:
            conflict_input_tokens = estimate_token_count(existing.get("memory", "")) + estimate_token_count(content)
            _add_llm_cost(conflict_input_tokens)
            resolution = resolve_conflict(existing, content, self._llm, self._config.custom_conflict_prompt)

            if resolution.classification == "CONTRADICTORY":
                self._demote_existing_fn(existing, reason="CONTRADICTORY")
                event = "UPDATE"
            elif resolution.classification == "SUBSUMES":
                content = resolution.merged_content or content
                self._demote_existing_fn(existing, reason="SUBSUMES")
                event = "UPDATE"
            elif resolution.classification == "SUBSUMED":
                boosted_strength = min(1.0, float(existing.get("strength", 1.0)) + 0.05)
                self._db.update_memory(existing["id"], {"strength": boosted_strength})
                self._db.increment_access(existing["id"])
                self._record_cost(
                    phase="write",
                    user_id=user_id,
                    llm_calls=write_llm_calls,
                    input_tokens=write_input_tokens,
                    output_tokens=write_output_tokens,
                    embed_calls=write_embed_calls,
                )
                return {
                    "id": existing["id"],
                    "memory": existing.get("memory", ""),
                    "event": "NOOP",
                    "layer": existing.get("layer", "sml"),
                    "strength": boosted_strength,
                }

        if existing and event == "UPDATE" and resolution and resolution.classification == "SUBSUMES":
            # Re-encode merged content.
            depth_for_encode = EchoDepth(echo_depth) if echo_depth else (_depth_for_echo or EchoDepth(self._echo_config.default_depth))
            if self._echo_config.enable_echo and depth_for_encode != EchoDepth.SHALLOW:
                _add_llm_cost(estimate_token_count(content))
            echo_result, _, mem_categories, embedding = self.encode_memory(
                content, echo_depth, mem_categories, mem_metadata, initial_strength,
            )
            write_embed_calls += 1.0

        if policy_repeated:
            mem_metadata["policy_repeated"] = True
        if low_confidence:
            mem_metadata["policy_low_confidence"] = True
            effective_strength = min(effective_strength, 0.4)

        layer = initial_layer
        if layer == "auto":
            layer = "sml"
        if low_confidence:
            layer = "sml"

        confidentiality_scope = str(
            mem_metadata.get("confidentiality_scope")
            or mem_metadata.get("privacy_scope")
            or "work"
        ).lower()
        source_type = (
            mem_metadata.get("source_type")
            or ("cli" if (source_app or "").lower() == "cli" else "mcp")
        )
        namespace_value = str(mem_metadata.get("namespace", "default") or "default").strip() or "default"

        # Gap 1: Classify memory type (episodic vs semantic)
        memory_type = self.classify_memory_type(mem_metadata, role)

        # Gap 4: Initialize multi-trace strength
        s_fast_val = None
        s_mid_val = None
        s_slow_val = None
        distillation_config = self._distillation_config
        if distillation_config and distillation_config.enable_multi_trace:
            s_fast_val, s_mid_val, s_slow_val = initialize_traces(effective_strength, is_new=True)

        # Metamemory: compute confidence score if enabled
        if self._config.metamemory.enable_confidence:
            try:
                from engram_metamemory.confidence import compute_confidence as _mm_confidence
                mem_metadata["mm_confidence"] = _mm_confidence(
                    metadata=mem_metadata,
                    strength=effective_strength,
                    access_count=0,
                    created_at=None,
                )
            except ImportError:
                pass

        effective_memory_id = memory_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        mem_metadata = attach_bitemporal_metadata(mem_metadata, observed_time=now)
        memory_data = {
            "id": effective_memory_id,
            "memory": content,
            "user_id": user_id,
            "agent_id": store_agent_id,
            "run_id": store_run_id,
            "app_id": store_app_id,
            "metadata": mem_metadata,
            "categories": mem_categories,
            "immutable": immutable,
            "expiration_date": expiration_date,
            "created_at": now,
            "updated_at": now,
            "layer": layer,
            "strength": effective_strength,
            "access_count": 0,
            "last_accessed": now,
            "embedding": embedding,
            "confidentiality_scope": confidentiality_scope,
            "source_type": source_type,
            "source_app": source_app or mem_metadata.get("source_app"),
            "source_event_id": mem_metadata.get("source_event_id"),
            "decay_lambda": self._fade_config.sml_decay_rate,
            "status": "active",
            "importance": mem_metadata.get("importance", 0.5),
            "sensitivity": mem_metadata.get("sensitivity", "normal"),
            "namespace": namespace_value,
            "memory_type": memory_type,
            "s_fast": s_fast_val,
            "s_mid": s_mid_val,
            "s_slow": s_slow_val,
        }

        vectors, payloads, vector_ids = build_index_vectors(
            memory_id=effective_memory_id,
            content=content,
            primary_text=self.select_primary_text(content, echo_result),
            embedding=embedding,
            echo_result=echo_result,
            metadata=mem_metadata,
            categories=mem_categories,
            user_id=user_id,
            agent_id=store_agent_id,
            run_id=store_run_id,
            app_id=store_app_id,
            embedder=self._embedder,
        )

        self._db.add_memory(memory_data)
        if vectors:
            try:
                self._vector_store.insert(vectors=vectors, payloads=payloads, ids=vector_ids)
            except Exception as e:
                logger.error(
                    "Vector insert failed for memory %s, rolling back DB record: %s",
                    effective_memory_id, e,
                )
                try:
                    self._db.delete_memory(effective_memory_id, use_tombstone=False)
                except Exception as rollback_err:
                    logger.critical(
                        "CRITICAL: DB rollback also failed for memory %s — manual cleanup required: %s",
                        effective_memory_id, rollback_err,
                    )
                raise

        # Fact decomposition
        if _unified_facts:
            valid_facts = []
            for i, fact_text in enumerate(_unified_facts[:8]):
                fact_text = fact_text.strip()
                if fact_text and len(fact_text) >= 10:
                    valid_facts.append((i, fact_text))

            if valid_facts:
                try:
                    fact_texts = [ft for _, ft in valid_facts]
                    fact_embeddings = self._embedder.embed_batch(fact_texts, memory_action="add")
                    write_embed_calls += 1.0
                    fact_vectors = []
                    fact_payloads = []
                    fact_ids = []
                    for (i, fact_text), fact_embedding in zip(valid_facts, fact_embeddings):
                        fact_id = f"{effective_memory_id}__fact_{i}"
                        fact_vectors.append(fact_embedding)
                        fact_payloads.append({
                            "memory_id": effective_memory_id,
                            "is_fact": True,
                            "fact_index": i,
                            "fact_text": fact_text,
                            "user_id": user_id,
                            "agent_id": store_agent_id,
                        })
                        fact_ids.append(fact_id)
                    if fact_vectors:
                        self._vector_store.insert(vectors=fact_vectors, payloads=fact_payloads, ids=fact_ids)
                except Exception as e:
                    logger.warning("Fact embedding/insert failed for %s: %s", effective_memory_id, e)

        # Post-store hooks.
        if cat_proc and mem_categories:
            for cat_id in mem_categories:
                cat_proc.update_category_stats(
                    cat_id, effective_strength, is_addition=True
                )

        knowledge_graph = self._graph
        if knowledge_graph:
            if _unified_entities is not None:
                for entity in _unified_entities:
                    existing_ent = knowledge_graph._get_or_create_entity(
                        entity.name, entity.entity_type,
                    )
                    existing_ent.memory_ids.add(effective_memory_id)
                knowledge_graph.memory_entities[effective_memory_id] = {
                    e.name for e in _unified_entities
                }
            else:
                if self._graph_config.use_llm_extraction:
                    _add_llm_cost(estimate_token_count(content))
                knowledge_graph.extract_entities(
                    content=content,
                    memory_id=effective_memory_id,
                    use_llm=self._graph_config.use_llm_extraction,
                )
            if self._graph_config.auto_link_entities:
                knowledge_graph.link_by_shared_entities(effective_memory_id)

        if self._scene_processor:
            try:
                self._assign_to_scene_fn(effective_memory_id, content, embedding, user_id, now)
            except Exception as e:
                logger.warning("Scene assignment failed for %s: %s", effective_memory_id, e)

        if self._profile_processor:
            try:
                if _unified_profiles is not None and _unified_profiles:
                    profile_proc = self._profile_processor
                    for profile_update in _unified_profiles:
                        profile_proc.apply_update(
                            profile_update=profile_update,
                            memory_id=effective_memory_id,
                            user_id=user_id or "default",
                        )
                else:
                    if self._config.profile.use_llm_extraction:
                        _add_llm_cost(estimate_token_count(content))
                    self._update_profiles_fn(effective_memory_id, content, mem_metadata, user_id)
            except Exception as e:
                logger.warning("Profile update failed for %s: %s", effective_memory_id, e)

        _index_episodic(
            db=self._db,
            config=self._config,
            memory_id=effective_memory_id,
            user_id=user_id,
            content=content,
            metadata=mem_metadata,
        )

        # Dhee: Universal Engram extraction
        engram_extractor = self._engram_extractor
        if engram_extractor:
            try:
                session_ctx = None
                if context_messages:
                    session_ctx = {"recent_messages": context_messages[-5:]}
                engram = engram_extractor.extract(
                    content=content,
                    session_context=session_ctx,
                    existing_metadata=mem_metadata,
                    user_id=user_id or "default",
                )
                context_resolver = self._context_resolver
                if context_resolver:
                    context_resolver.store_engram(engram, effective_memory_id)
                if engram.prospective_scenes and self._config.prospective_scene.enable_prospective_scenes:
                    self._store_prospective_scenes_fn(
                        engram.prospective_scenes,
                        effective_memory_id,
                        user_id or "default",
                    )
            except Exception as e:
                logger.warning("Engram extraction failed for %s: %s", effective_memory_id, e)

        # Dhee: Self-evolution — record extraction quality signal
        evolution_layer = self._evolution_layer
        if evolution_layer:
            try:
                engram_facts = None
                engram_context = None
                if engram_extractor and 'engram' in dir() and engram:  # noqa: F821
                    engram_facts = [f.to_dict() if hasattr(f, 'to_dict') else f for f in getattr(engram, 'facts', [])]
                    engram_context = getattr(engram, 'context', None)
                    if engram_context and hasattr(engram_context, '__dict__'):
                        engram_context = engram_context.__dict__
                evolution_layer.on_memory_stored(
                    memory_id=effective_memory_id,
                    content=content,
                    facts=engram_facts,
                    context=engram_context,
                    user_id=user_id or "default",
                )
            except Exception as e:
                logger.debug("Evolution write hook skipped: %s", e)

        # Buddhi write hook: detect intentions in stored content
        buddhi_layer = self._buddhi_layer
        if buddhi_layer:
            try:
                buddhi_layer.on_memory_stored(
                    content=content,
                    user_id=user_id or "default",
                )
            except Exception as e:
                logger.debug("Buddhi write hook skipped: %s", e)

        self._record_cost(
            phase="write",
            user_id=user_id,
            llm_calls=write_llm_calls,
            input_tokens=write_input_tokens,
            output_tokens=write_output_tokens,
            embed_calls=write_embed_calls,
        )

        return {
            "id": effective_memory_id,
            "memory": content,
            "event": event,
            "layer": layer,
            "strength": effective_strength,
            "echo_depth": echo_result.echo_depth.value if echo_result else None,
            "categories": mem_categories,
            "namespace": namespace_value,
            "vector_nodes": len(vectors),
            "memory_type": memory_type,
        }

    def process_single_memory_lite(
        self,
        *,
        content: str,
        mem_metadata: Dict[str, Any],
        mem_categories: List[str],
        context_messages: Optional[List[Dict[str, str]]],
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        app_id: Optional[str],
        effective_filters: Dict[str, Any],
        agent_category: Optional[str],
        connector_id: Optional[str],
        scope: Optional[str],
        source_app: Optional[str],
        immutable: bool,
        expiration_date: Optional[str],
        initial_layer: str,
        initial_strength: float,
        explicit_remember: bool,
        memory_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Lite processing path for deferred enrichment -- 0 LLM calls.

        Stores the memory with regex-extracted keywords, context-enriched
        embedding, and enrichment_status='pending'.  All heavy LLM processing
        (echo, category, conflict, entities, profiles) is deferred to
        enrich_pending().
        """
        from dhee.memory.main import (
            looks_high_confidence,
            _NAME_HINT_RE,
            _PREFERENCE_HINT_RE,
            _ROUTINE_HINT_RE,
            _GOAL_HINT_RE,
        )

        # Resolve store identifiers and scope metadata.
        store_agent_id, store_run_id, store_app_id, store_filters = self.resolve_memory_metadata(
            content=content,
            mem_metadata=mem_metadata,
            explicit_remember=explicit_remember,
            agent_id=agent_id,
            run_id=run_id,
            app_id=app_id,
            effective_filters=effective_filters,
            agent_category=agent_category,
            connector_id=connector_id,
            scope=scope,
            source_app=source_app,
        )

        high_confidence = explicit_remember or looks_high_confidence(content, mem_metadata)

        # --- Regex keyword extraction (0 LLM calls) ---
        extracted_keywords: List[str] = []
        content_lower = content.lower()

        for regex, tag in [
            (_PREFERENCE_HINT_RE, "preference"),
            (_ROUTINE_HINT_RE, "routine"),
            (_GOAL_HINT_RE, "goal"),
        ]:
            if regex.search(content):
                extracted_keywords.append(tag)

        _STOPWORDS = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "can", "shall", "to", "of", "in", "for",
            "on", "with", "at", "by", "from", "as", "into", "through", "during",
            "before", "after", "above", "below", "between", "and", "but", "or",
            "nor", "not", "so", "yet", "both", "either", "neither", "each",
            "every", "all", "any", "few", "more", "most", "other", "some", "such",
            "no", "only", "own", "same", "than", "too", "very", "just", "i", "me",
            "my", "we", "our", "you", "your", "he", "she", "it", "they", "them",
            "this", "that", "these", "those", "am", "his", "her", "its",
        }
        words = re.findall(r"\b[a-z][a-z0-9_-]{2,}\b", content_lower)
        word_freq: Dict[str, int] = {}
        for w in words:
            if w not in _STOPWORDS:
                word_freq[w] = word_freq.get(w, 0) + 1
        top_words = sorted(word_freq, key=lambda w: word_freq[w], reverse=True)[:15]
        extracted_keywords.extend(top_words)

        name_match = _NAME_HINT_RE.search(content)
        if name_match:
            extracted_keywords.append(f"name:{name_match.group(1).strip()}")

        mem_metadata["echo_keywords"] = extracted_keywords
        mem_metadata["enrichment_status"] = "pending"

        # --- Build rich embedding text (content + context summary) ---
        context_window = getattr(self._config.enrichment, "context_window_turns", 10)
        context_summary = ""
        if context_messages:
            recent = context_messages[-context_window:]
            context_lines = [
                f"{m.get('role', 'user')}: {str(m.get('content', ''))[:200]}"
                for m in recent
            ]
            context_summary = " | ".join(context_lines)

        embed_text = content
        if context_summary:
            embed_text += f" [Context: {context_summary[:500]}]"

        # --- Generate embedding (1 API call, NOT an LLM call) ---
        embedding = self._embedder.embed(embed_text, memory_action="add")

        # --- Confidence and layer ---
        effective_strength = initial_strength
        if not explicit_remember and not high_confidence:
            mem_metadata["policy_low_confidence"] = True
            effective_strength = min(effective_strength, 0.4)

        layer = initial_layer
        if layer == "auto":
            layer = "sml"

        # --- Metadata ---
        confidentiality_scope = str(
            mem_metadata.get("confidentiality_scope")
            or mem_metadata.get("privacy_scope")
            or "work"
        ).lower()
        source_type = (
            mem_metadata.get("source_type")
            or ("cli" if (source_app or "").lower() == "cli" else "mcp")
        )
        namespace_value = str(mem_metadata.get("namespace", "default") or "default").strip() or "default"
        memory_type = self.classify_memory_type(mem_metadata, mem_metadata.get("role", "user"))

        # Multi-trace strength
        s_fast_val = s_mid_val = s_slow_val = None
        distillation_config = self._distillation_config
        if distillation_config and distillation_config.enable_multi_trace:
            s_fast_val, s_mid_val, s_slow_val = initialize_traces(effective_strength, is_new=True)

        # Content hash for dedup
        from dhee.memory.core import _content_hash
        ch = _content_hash(content)
        existing = self._db.get_memory_by_content_hash(ch, user_id) if hasattr(self._db, 'get_memory_by_content_hash') else None
        if existing:
            self._db.increment_access(existing["id"])
            return {
                "id": existing["id"],
                "memory": existing.get("memory", ""),
                "event": "DEDUPLICATED",
                "layer": existing.get("layer", "sml"),
                "strength": existing.get("strength", 1.0),
            }

        effective_memory_id = memory_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        mem_metadata = attach_bitemporal_metadata(mem_metadata, observed_time=now)

        # Serialize conversation context
        context_json = None
        if context_messages:
            recent = context_messages[-context_window:]
            context_json = json.dumps(recent)

        memory_data = {
            "id": effective_memory_id,
            "memory": content,
            "user_id": user_id,
            "agent_id": store_agent_id,
            "run_id": store_run_id,
            "app_id": store_app_id,
            "metadata": mem_metadata,
            "categories": mem_categories,
            "immutable": immutable,
            "expiration_date": expiration_date,
            "created_at": now,
            "updated_at": now,
            "layer": layer,
            "strength": effective_strength,
            "access_count": 0,
            "last_accessed": now,
            "embedding": embedding,
            "confidentiality_scope": confidentiality_scope,
            "source_type": source_type,
            "source_app": source_app or mem_metadata.get("source_app"),
            "source_event_id": mem_metadata.get("source_event_id"),
            "decay_lambda": self._fade_config.sml_decay_rate,
            "status": "active",
            "importance": mem_metadata.get("importance", 0.5),
            "sensitivity": mem_metadata.get("sensitivity", "normal"),
            "namespace": namespace_value,
            "memory_type": memory_type,
            "s_fast": s_fast_val,
            "s_mid": s_mid_val,
            "s_slow": s_slow_val,
            "content_hash": ch,
            "conversation_context": context_json,
            "enrichment_status": "pending",
        }

        # Build vector index (single primary vector, no echo nodes)
        base_payload = {
            "memory_id": effective_memory_id,
            "user_id": user_id,
            "agent_id": store_agent_id,
            "run_id": store_run_id,
            "app_id": store_app_id,
            "categories": mem_categories,
            "text": embed_text,
            "type": "primary",
            "memory": content,
        }
        vectors = [embedding]
        payloads = [base_payload]
        vector_ids = [effective_memory_id]

        self._db.add_memory(memory_data)
        try:
            self._vector_store.insert(vectors=vectors, payloads=payloads, ids=vector_ids)
        except Exception as e:
            logger.error("Vector insert failed for memory %s (lite), rolling back: %s", effective_memory_id, e)
            try:
                self._db.delete_memory(effective_memory_id, use_tombstone=False)
            except Exception as rollback_err:
                logger.critical("DB rollback also failed for %s: %s", effective_memory_id, rollback_err)
            raise

        # Scene assignment still works (embedding-based, no LLM)
        if self._scene_processor:
            try:
                self._assign_to_scene_fn(effective_memory_id, content, embedding, user_id, now)
            except Exception as e:
                logger.warning("Scene assignment failed for %s (lite): %s", effective_memory_id, e)

        _index_episodic(
            db=self._db,
            config=self._config,
            memory_id=effective_memory_id,
            user_id=user_id,
            content=content,
            metadata=mem_metadata,
        )
        self._record_cost(
            phase="write",
            user_id=user_id,
            llm_calls=0.0,
            input_tokens=0.0,
            output_tokens=0.0,
            embed_calls=1.0,
        )

        return {
            "id": effective_memory_id,
            "memory": content,
            "event": "ADD",
            "layer": layer,
            "strength": effective_strength,
            "echo_depth": None,
            "categories": mem_categories,
            "namespace": namespace_value,
            "vector_nodes": 1,
            "memory_type": memory_type,
            "enrichment_status": "pending",
        }

    def extract_memories(
        self,
        messages: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        prompt: Optional[str] = None,
        includes: Optional[str] = None,
        excludes: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Extract structured memories from a conversation using LLM."""
        conversation = parse_messages(messages)
        existing = self._db.get_all_memories(
            user_id=metadata.get("user_id"),
            agent_id=metadata.get("agent_id"),
            run_id=metadata.get("run_id"),
            app_id=metadata.get("app_id"),
        )
        existing_text = "\n".join([m.get("memory", "") for m in existing])

        if prompt or self._config.custom_fact_extraction_prompt:
            extraction_prompt = prompt or self._config.custom_fact_extraction_prompt
        else:
            if self._should_use_agent_memory_extraction(messages, metadata):
                extraction_prompt = AGENT_MEMORY_EXTRACTION_PROMPT
            else:
                extraction_prompt = MEMORY_EXTRACTION_PROMPT
        prompt_text = extraction_prompt.format(conversation=conversation, existing_memories=existing_text)

        try:
            response = self._llm.generate(prompt_text)
            data = strip_code_fences(response)
            if not data:
                return []
            parsed = json.loads(data)
            memories = parsed.get("memories", [])
            extracted = [
                {
                    "content": m.get("content", ""),
                    "categories": [m.get("category")] if m.get("category") else [],
                    "metadata": {"importance": m.get("importance"), "confidence": m.get("confidence")},
                }
                for m in memories
                if isinstance(m, dict)
            ]
            if includes:
                extracted = [m for m in extracted if includes.lower() in m.get("content", "").lower()]
            if excludes:
                extracted = [m for m in extracted if excludes.lower() not in m.get("content", "").lower()]
            return extracted
        except Exception as exc:
            logger.warning("Memory extraction failed (LLM or JSON error): %s", exc)
            return []

    @staticmethod
    def _should_use_agent_memory_extraction(messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> bool:
        has_agent_id = metadata.get("agent_id") is not None
        has_assistant_messages = any(msg.get("role") == "assistant" for msg in messages)
        return has_agent_id and has_assistant_messages

    def classify_memory_type(self, metadata: Dict[str, Any], role: str) -> str:
        """Classify a memory as 'episodic' or 'semantic' (Gap 1).

        When enable_memory_types is False, everything stays 'semantic' (backward compat).
        """
        distillation_config = self._distillation_config
        if not distillation_config or not distillation_config.enable_memory_types:
            return distillation_config.default_memory_type if distillation_config else "semantic"

        explicit = metadata.get("memory_type")
        if explicit in ("episodic", "semantic", "task", "note", "procedural",
                       "project", "project_status", "project_tag",
                       "warroom", "warroom_message"):
            return explicit

        if metadata.get("is_distilled"):
            return "semantic"

        if role in ("user", "assistant"):
            return "episodic"

        if metadata.get("source_type") == "active_signal":
            return "semantic"

        return "semantic"

    def select_primary_text(self, content: str, echo_result: Optional[EchoResult]) -> str:
        """Select the best text for embedding given optional echo enrichment."""
        if not echo_result:
            return content

        if self._echo_config.use_echo_augmented_embedding:
            parts = [content[:1500]]
            if echo_result.question_form:
                parts.append(echo_result.question_form)
            if echo_result.keywords:
                parts.append("Keywords: " + ", ".join(echo_result.keywords[:10]))
            if echo_result.paraphrases:
                parts.append(echo_result.paraphrases[0])
            return "\n".join(parts)

        if self._echo_config.use_question_embedding and echo_result.question_form:
            return echo_result.question_form
        return content
