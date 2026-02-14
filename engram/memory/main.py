from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, date, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from engram.configs.base import MemoryConfig
from engram.core.decay import calculate_decayed_strength, should_forget, should_promote
from engram.core.conflict import resolve_conflict
from engram.core.distillation import ReplayDistiller
from engram.core.echo import EchoProcessor, EchoDepth, EchoResult
from engram.core.forgetting import HomeostaticNormalizer, InterferencePruner, RedundancyCollapser
from engram.core.fusion import fuse_memories
from engram.core.intent import QueryIntent, classify_intent
from engram.core.retrieval import composite_score, tokenize, HybridSearcher
from engram.core.traces import (
    boost_fast_trace,
    cascade_traces,
    compute_effective_strength,
    decay_traces,
    initialize_traces,
)
from engram.core.category import CategoryProcessor, CategoryMatch
from engram.core.graph import KnowledgeGraph
from engram.core.scene import SceneProcessor
from engram.core.profile import ProfileProcessor
from engram.db.sqlite import SQLiteManager
from engram.exceptions import FadeMemValidationError
from engram.memory.base import MemoryBase
from engram.memory.utils import (
    build_filters_and_metadata,
    matches_filters,
    normalize_categories,
    normalize_messages,
    parse_messages,
    strip_code_fences,
)
from engram.memory.parallel import ParallelExecutor
from engram.observability import metrics
from engram.utils.factory import EmbedderFactory, LLMFactory, VectorStoreFactory
from engram.utils.prompts import AGENT_MEMORY_EXTRACTION_PROMPT, MEMORY_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline helpers (formerly in deleted core/acceptance and core/policy modules)
# ---------------------------------------------------------------------------

@dataclass
class ExplicitIntent:
    action: Optional[str]
    content: str


_REMEMBER_PATTERNS = [
    r"^\s*(?:please\s+)?remember\b(?: that)?\s*[:,-]?\s*(.+)$",
    r"\b(?:don't|do not)\s+forget\b(?: to)?\s*[:,-]?\s*(.+)$",
    r"\bmake sure to remember\b(?: that)?\s*[:,-]?\s*(.+)$",
]

_FORGET_PATTERNS = [
    r"^\s*(?:forget|delete|remove|erase)\b(?: about| that)?\s*[:,-]?\s*(.+)$",
    r"^\s*(?:don't|do not)\s+remember\b(?: that)?\s*[:,-]?\s*(.+)$",
]


def detect_explicit_intent(text: str) -> ExplicitIntent:
    cleaned = text.strip()
    for pattern in _REMEMBER_PATTERNS:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            content = match.group(1).strip()
            return ExplicitIntent(action="remember", content=content or cleaned)
    for pattern in _FORGET_PATTERNS:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            content = match.group(1).strip()
            return ExplicitIntent(action="forget", content=content or "")
    return ExplicitIntent(action=None, content=cleaned)


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{3}\)?[\s-]?)\d{3}[\s-]?\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+\w+(?:\s+\w+){0,4}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct)\b",
    re.IGNORECASE,
)
_NAME_HINT_RE = re.compile(
    r"\b(?:my name is|call me|i am|i'm)\s+([A-Za-z][A-Za-z'\\-]+(?:\s+[A-Za-z][A-Za-z'\\-]+)?)\b",
    re.IGNORECASE,
)
_ID_HINT_RE = re.compile(
    r"\b(passport|driver'?s license|license number|id number|social security|ssn)\b",
    re.IGNORECASE,
)
_HEALTH_HINT_RE = re.compile(
    r"\b(diagnosed|diagnosis|medication|prescription|doctor|clinic|therapy|symptom|allergy|allergic|sick|illness|disease|mental health|depression|anxiety|adhd|diabetes|asthma|blood pressure|migraine)\b",
    re.IGNORECASE,
)
_FINANCE_HINT_RE = re.compile(
    r"\b(bank|account number|routing|iban|swift|credit card|debit card|cvv|salary|income|mortgage|loan|tax|tax id|payment|billing|invoice)\b",
    re.IGNORECASE,
)
_EPHEMERAL_HINT_RE = re.compile(
    r"\b(today|tomorrow|tonight|this morning|this afternoon|this evening|this week|next week|later|in \d+\s*(?:minutes|hours|days)|remind me|schedule|book|call|email|send|buy|pick up|meeting|appointment|todo|to-do|task|for now|currently|at the moment)\b",
    re.IGNORECASE,
)
_PREFERENCE_HINT_RE = re.compile(
    r"\b(prefer|favorite|always|never|like to|love|hate|avoid|must|can't|cannot)\b",
    re.IGNORECASE,
)
_ROUTINE_HINT_RE = re.compile(
    r"\b(every day|every morning|every night|every week|weekly|monthly|on weekends|each week|every weekday)\b",
    re.IGNORECASE,
)
_GOAL_HINT_RE = re.compile(
    r"\b(my goal is|i want to|i plan to|i'm working on|i am working on|long[- ]term)\b",
    re.IGNORECASE,
)


def detect_sensitive_categories(text: str) -> List[str]:
    reasons: List[str] = []
    if _EMAIL_RE.search(text):
        reasons.append("email")
    if _PHONE_RE.search(text):
        reasons.append("phone")
    if _SSN_RE.search(text):
        reasons.append("ssn")
    if _ADDRESS_RE.search(text):
        reasons.append("address")
    if _ID_HINT_RE.search(text):
        reasons.append("id")
    name_match = _NAME_HINT_RE.search(text)
    if name_match:
        candidate = name_match.group(1).strip()
        if candidate and candidate[0].isupper():
            reasons.append("name")
    if _HEALTH_HINT_RE.search(text):
        reasons.append("health")
    if _FINANCE_HINT_RE.search(text):
        reasons.append("finance")
    return sorted(set(reasons))


def is_ephemeral(text: str) -> bool:
    return _EPHEMERAL_HINT_RE.search(text) is not None


def looks_high_confidence(content: str, metadata: Optional[Dict[str, object]] = None) -> bool:
    metadata = metadata or {}
    confidence = _coerce_float(metadata.get("confidence"))
    importance = _coerce_float(metadata.get("importance"))
    if confidence is not None and confidence >= 0.7:
        return True
    if importance is not None and importance >= 0.7:
        return True
    if metadata.get("confirmed") or metadata.get("user_confirmed"):
        return True
    if _PREFERENCE_HINT_RE.search(content):
        return True
    if _ROUTINE_HINT_RE.search(content):
        return True
    if _GOAL_HINT_RE.search(content):
        return True
    return False


def _coerce_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def feature_enabled(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------

SHAREABLE_CATEGORY_IDS = {
    "preferences",
    "procedures",
    "corrections",
}

SHAREABLE_CATEGORY_HINTS = (
    "preference",
    "workflow",
    "procedure",
    "coding",
    "code",
    "style",
    "tooling",
    "editor",
)

SCOPE_VALUES = {"agent", "connector", "category", "global"}
DEFAULT_SCOPE_WEIGHTS = {
    "agent": 1.0,
    "connector": 0.97,
    "category": 0.94,
    "global": 0.92,
}


class MemoryScope(str, Enum):
    AGENT = "agent"
    CONNECTOR = "connector"
    CATEGORY = "category"
    GLOBAL = "global"


class Memory(MemoryBase):
    """engram Memory class - biologically-inspired memory for AI agents."""

    def __init__(self, config: Optional[MemoryConfig] = None):
        self.config = config or MemoryConfig()

        # Ensure vector store config has dims/collection if missing
        self.config.vector_store.config.setdefault("collection_name", self.config.collection_name)
        self.config.vector_store.config.setdefault("embedding_model_dims", self.config.embedding_model_dims)

        self.db = SQLiteManager(self.config.history_db_path)
        self.llm = LLMFactory.create(self.config.llm.provider, self.config.llm.config)
        self.embedder = EmbedderFactory.create(self.config.embedder.provider, self.config.embedder.config)
        self.vector_store = VectorStoreFactory.create(self.config.vector_store.provider, self.config.vector_store.config)
        self.fadem_config = self.config.engram
        self.echo_config = self.config.echo
        self.scope_config = getattr(self.config, "scope", None)
        self.distillation_config = getattr(self.config, "distillation", None)

        # Initialize EchoMem processor
        if self.echo_config.enable_echo:
            self.echo_processor = EchoProcessor(
                self.llm,
                config={
                    "auto_depth": self.echo_config.auto_depth,
                    "default_depth": self.echo_config.default_depth,
                }
            )
        else:
            self.echo_processor = None

        # Initialize CategoryMem processor
        self.category_config = self.config.category
        if self.category_config.enable_categories:
            self.category_processor = CategoryProcessor(
                llm=self.llm,
                embedder=self.embedder,
                config={
                    "use_llm": self.category_config.use_llm_categorization,
                    "auto_subcategories": self.category_config.auto_create_subcategories,
                    "max_depth": self.category_config.max_category_depth,
                },
            )
            # Load existing categories from DB
            existing_categories = self.db.get_all_categories()
            if existing_categories:
                self.category_processor.load_categories(existing_categories)
        else:
            self.category_processor = None

        # Initialize Knowledge Graph
        self.graph_config = self.config.graph
        if self.graph_config.enable_graph:
            self.knowledge_graph = KnowledgeGraph(
                llm=self.llm if self.graph_config.use_llm_extraction else None
            )
        else:
            self.knowledge_graph = None

        # Initialize SceneProcessor
        self.scene_config = self.config.scene
        if self.scene_config.enable_scenes:
            self.scene_processor = SceneProcessor(
                db=self.db,
                embedder=self.embedder,
                llm=self.llm,
                config={
                    "scene_time_gap_minutes": self.scene_config.scene_time_gap_minutes,
                    "scene_topic_threshold": self.scene_config.scene_topic_threshold,
                    "auto_close_inactive_minutes": self.scene_config.auto_close_inactive_minutes,
                    "max_scene_memories": self.scene_config.max_scene_memories,
                    "use_llm_summarization": self.scene_config.use_llm_summarization,
                    "summary_regenerate_threshold": self.scene_config.summary_regenerate_threshold,
                },
            )
        else:
            self.scene_processor = None

        # Initialize ProfileProcessor
        self.profile_config = self.config.profile
        if self.profile_config.enable_profiles:
            self.profile_processor = ProfileProcessor(
                db=self.db,
                embedder=self.embedder,
                llm=self.llm,
                config={
                    "auto_detect_profiles": self.profile_config.auto_detect_profiles,
                    "use_llm_extraction": self.profile_config.use_llm_extraction,
                    "narrative_regenerate_threshold": self.profile_config.narrative_regenerate_threshold,
                    "self_profile_auto_create": self.profile_config.self_profile_auto_create,
                    "max_facts_per_profile": self.profile_config.max_facts_per_profile,
                },
            )
        else:
            self.profile_processor = None

        # Parallel executor for I/O-bound LLM/embedding calls
        self.parallel_config = getattr(self.config, "parallel", None)
        self._executor: Optional[ParallelExecutor] = None
        if self.parallel_config and self.parallel_config.enable_parallel:
            self._executor = ParallelExecutor(
                max_workers=self.parallel_config.max_workers
            )

    def close(self) -> None:
        """Release all resources held by the Memory instance."""
        if hasattr(self, '_executor') and self._executor is not None:
            self._executor.shutdown()
            self._executor = None
        if hasattr(self, 'vector_store') and self.vector_store is not None:
            self.vector_store.close()
        if hasattr(self, 'db') and self.db is not None:
            self.db.close()

    def __repr__(self) -> str:
        return f"Memory(db={self.db!r}, echo={self.echo_config.enable_echo}, scenes={self.scene_config.enable_scenes})"

    @classmethod
    def from_config(cls, config_dict: Dict[str, Any]):
        return cls(MemoryConfig(**config_dict))

    def add(
        self,
        messages: Union[str, List[Dict[str, str]]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        metadata: Dict[str, Any] = None,
        filters: Dict[str, Any] = None,
        categories: List[str] = None,
        immutable: bool = False,
        expiration_date: Optional[str] = None,
        infer: bool = True,
        prompt: Optional[str] = None,
        includes: Optional[str] = None,
        excludes: Optional[str] = None,
        initial_layer: str = "auto",
        initial_strength: float = 1.0,
        echo_depth: Optional[str] = None,  # EchoMem: override echo depth (shallow/medium/deep)
        agent_category: Optional[str] = None,
        connector_id: Optional[str] = None,
        scope: Optional[str] = None,
        source_app: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        processed_metadata, effective_filters = build_filters_and_metadata(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            input_metadata=metadata,
            input_filters=filters,
        )

        messages_list = normalize_messages(messages)

        if infer:
            memories_to_add = self._extract_memories(
                messages_list,
                processed_metadata,
                prompt=prompt,
                includes=includes,
                excludes=excludes,
            )
        else:
            memories_to_add = []
            for msg in messages_list:
                role = msg.get("role")
                if role == "system":
                    continue
                content = msg.get("content")
                if not content:
                    continue
                mem_meta = dict(processed_metadata)
                mem_meta["role"] = role
                if msg.get("name"):
                    mem_meta["actor_id"] = msg.get("name")
                memories_to_add.append({"content": content, "metadata": mem_meta})

        results: List[Dict[str, Any]] = []
        for mem in memories_to_add:
            result = self._process_single_memory(
                mem=mem,
                processed_metadata=processed_metadata,
                effective_filters=effective_filters,
                categories=categories,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                app_id=app_id,
                agent_category=agent_category,
                connector_id=connector_id,
                scope=scope,
                source_app=source_app,
                immutable=immutable,
                expiration_date=expiration_date,
                initial_layer=initial_layer,
                initial_strength=initial_strength,
                echo_depth=echo_depth,
            )
            if result is not None:
                results.append(result)

        # Persist categories after batch
        if self.category_processor:
            self._persist_categories()

        return {"results": results}

    def add_batch(
        self,
        items: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        metadata: Dict[str, Any] = None,
        filters: Dict[str, Any] = None,
        initial_strength: float = 1.0,
        echo_depth: Optional[str] = None,
        **common_kwargs: Any,
    ) -> Dict[str, Any]:
        """Add multiple memories in a batch, minimizing LLM/embedding/DB calls.

        Each item in *items* is a dict with at least a ``content`` key (or
        ``messages``). Items may also carry per-item ``user_id``, ``metadata``,
        ``categories``, etc.

        Batch optimization is only used when ``config.batch.enable_batch`` is
        True. Otherwise this is equivalent to calling ``add()`` in a loop.

        Returns ``{"results": [...]}``.
        """
        batch_config = getattr(self.config, "batch", None)
        use_batch = batch_config and batch_config.enable_batch

        if not use_batch or not items:
            # Fallback: sequential add per item
            all_results = []
            for item in items:
                content = item.get("content") or item.get("messages", "")
                item_meta = dict(metadata or {})
                item_meta.update(item.get("metadata") or {})
                result = self.add(
                    messages=content,
                    user_id=item.get("user_id") or user_id,
                    agent_id=item.get("agent_id") or agent_id,
                    run_id=item.get("run_id") or run_id,
                    app_id=item.get("app_id") or app_id,
                    metadata=item_meta,
                    filters=filters,
                    categories=item.get("categories"),
                    initial_strength=initial_strength,
                    echo_depth=echo_depth,
                    infer=False,
                    **common_kwargs,
                )
                all_results.extend(result.get("results", []))
            return {"results": all_results}

        max_batch = batch_config.max_batch_size

        # Split into sub-batches if needed
        all_results: List[Dict[str, Any]] = []
        for start in range(0, len(items), max_batch):
            chunk = items[start : start + max_batch]
            chunk_results = self._process_memory_batch(
                chunk,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                app_id=app_id,
                metadata=metadata,
                filters=filters,
                initial_strength=initial_strength,
                echo_depth=echo_depth,
                batch_config=batch_config,
                **common_kwargs,
            )
            all_results.extend(chunk_results)

        # Persist categories after full batch
        if self.category_processor:
            self._persist_categories()

        return {"results": all_results}

    def _process_memory_batch(
        self,
        items: List[Dict[str, Any]],
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        app_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
        filters: Optional[Dict[str, Any]],
        initial_strength: float,
        echo_depth: Optional[str],
        batch_config,
        **common_kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Process a batch of memory items with batched echo/embed/DB."""

        # Extract contents
        contents = []
        item_metadata_list = []
        for item in items:
            content = item.get("content") or item.get("messages", "")
            if isinstance(content, list):
                # Flatten message list to string
                content = " ".join(
                    m.get("content", "") for m in content if isinstance(m, dict)
                )
            contents.append(str(content).strip())
            item_meta = dict(metadata or {})
            item_meta.update(item.get("metadata") or {})
            item_metadata_list.append(item_meta)

        # 1. Batch echo encoding
        echo_results = [None] * len(contents)
        if self.echo_processor and self.echo_config.enable_echo and batch_config.batch_echo:
            try:
                depth_override = EchoDepth(echo_depth) if echo_depth else None
                echo_results = self.echo_processor.process_batch(
                    contents, depth=depth_override
                )
            except Exception as e:
                logger.warning("Batch echo failed, processing individually: %s", e)
                for i, c in enumerate(contents):
                    if c:
                        try:
                            depth_override = EchoDepth(echo_depth) if echo_depth else None
                            echo_results[i] = self.echo_processor.process(c, depth=depth_override)
                        except Exception:
                            pass

        # 2. Batch category detection
        category_results = [None] * len(contents)
        if (
            self.category_processor
            and self.category_config.auto_categorize
            and batch_config.batch_category
        ):
            try:
                category_results = self.category_processor.detect_categories_batch(
                    contents,
                    use_llm=self.category_config.use_llm_categorization,
                )
            except Exception as e:
                logger.warning("Batch category failed: %s", e)

        # 3. Batch embeddings
        primary_texts = []
        for i, content in enumerate(contents):
            echo_result = echo_results[i]
            primary_texts.append(self._select_primary_text(content, echo_result))

        if batch_config.batch_embed:
            try:
                embeddings = self.embedder.embed_batch(primary_texts, memory_action="add")
            except Exception as e:
                logger.warning("Batch embed failed, falling back to sequential: %s", e)
                embeddings = [
                    self.embedder.embed(t, memory_action="add") for t in primary_texts
                ]
        else:
            embeddings = [
                self.embedder.embed(t, memory_action="add") for t in primary_texts
            ]

        # 4. Build memory records and batch-insert into DB
        processed_metadata_base, effective_filters = build_filters_and_metadata(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            input_metadata=metadata,
            input_filters=filters,
        )
        if app_id:
            processed_metadata_base["app_id"] = app_id

        now = datetime.now(timezone.utc).isoformat()
        memory_records = []
        vector_batch = []  # (vectors, payloads, ids)
        results = []

        for i, content in enumerate(contents):
            if not content:
                continue

            memory_id = str(uuid.uuid4())
            mem_metadata = dict(processed_metadata_base)
            mem_metadata.update(item_metadata_list[i])

            echo_result = echo_results[i]
            effective_strength = initial_strength
            mem_categories = list(items[i].get("categories") or [])

            if echo_result:
                effective_strength = initial_strength * echo_result.strength_multiplier
                mem_metadata.update(echo_result.to_metadata())
                if not mem_categories and echo_result.category:
                    mem_categories = [echo_result.category]

            cat_match = category_results[i]
            if cat_match and not mem_categories:
                mem_categories = [cat_match.category_id]
                mem_metadata["category_confidence"] = cat_match.confidence
                mem_metadata["category_auto"] = True

            embedding = embeddings[i]
            namespace_value = str(mem_metadata.get("namespace", "default") or "default").strip() or "default"

            memory_type = self._classify_memory_type(mem_metadata, mem_metadata.get("role", "user"))

            s_fast_val = s_mid_val = s_slow_val = None
            if self.distillation_config and self.distillation_config.enable_multi_trace:
                s_fast_val, s_mid_val, s_slow_val = initialize_traces(effective_strength, is_new=True)

            memory_data = {
                "id": memory_id,
                "memory": content,
                "user_id": items[i].get("user_id") or user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "app_id": app_id,
                "metadata": mem_metadata,
                "categories": mem_categories,
                "immutable": items[i].get("immutable", False),
                "expiration_date": items[i].get("expiration_date"),
                "created_at": now,
                "updated_at": now,
                "layer": "sml",
                "strength": effective_strength,
                "access_count": 0,
                "last_accessed": now,
                "embedding": embedding,
                "confidentiality_scope": "work",
                "source_type": "mcp",
                "source_app": items[i].get("source_app"),
                "source_event_id": mem_metadata.get("source_event_id"),
                "decay_lambda": self.fadem_config.sml_decay_rate,
                "status": "active",
                "importance": mem_metadata.get("importance", 0.5),
                "sensitivity": mem_metadata.get("sensitivity", "normal"),
                "namespace": namespace_value,
                "memory_type": memory_type,
                "s_fast": s_fast_val,
                "s_mid": s_mid_val,
                "s_slow": s_slow_val,
            }
            memory_records.append(memory_data)

            # Build vector index entries
            vectors, payloads, vector_ids = self._build_index_vectors(
                memory_id=memory_id,
                content=content,
                primary_text=primary_texts[i],
                embedding=embedding,
                echo_result=echo_result,
                metadata=mem_metadata,
                categories=mem_categories,
                user_id=items[i].get("user_id") or user_id,
                agent_id=agent_id,
                run_id=run_id,
                app_id=app_id,
            )
            if vectors:
                vector_batch.append((vectors, payloads, vector_ids))

            results.append({
                "id": memory_id,
                "memory": content,
                "event": "ADD",
                "layer": "sml",
                "strength": effective_strength,
                "echo_depth": echo_result.echo_depth.value if echo_result else None,
                "categories": mem_categories,
                "namespace": namespace_value,
                "memory_type": memory_type,
            })

        # 4a. Batch DB insert
        if memory_records:
            try:
                self.db.add_memories_batch(memory_records)
            except Exception as e:
                logger.error("Batch DB insert failed, falling back to sequential: %s", e)
                for record in memory_records:
                    self.db.add_memory(record)

        # 4b. Batch vector insert
        for vectors, payloads, vector_ids in vector_batch:
            try:
                self.vector_store.insert(vectors=vectors, payloads=payloads, ids=vector_ids)
            except Exception as e:
                logger.error("Vector insert failed in batch: %s", e)

        # Post-store hooks
        for i, record in enumerate(memory_records):
            if self.category_processor and record.get("categories"):
                for cat_id in record["categories"]:
                    self.category_processor.update_category_stats(
                        cat_id, record["strength"], is_addition=True
                    )

        return results

    def _resolve_memory_metadata(
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

    def _encode_memory(
        self,
        content: str,
        echo_depth: Optional[str],
        mem_categories: List[str],
        mem_metadata: Dict[str, Any],
        initial_strength: float,
    ) -> tuple:
        """Run echo encoding + embedding. Returns (echo_result, effective_strength, mem_categories, embedding)."""
        echo_result = None
        effective_strength = initial_strength
        if self.echo_processor and self.echo_config.enable_echo:
            depth_override = EchoDepth(echo_depth) if echo_depth else None
            echo_result = self.echo_processor.process(content, depth=depth_override)
            effective_strength = initial_strength * echo_result.strength_multiplier
            mem_metadata.update(echo_result.to_metadata())
            if not mem_categories and echo_result.category:
                mem_categories = [echo_result.category]

        primary_text = self._select_primary_text(content, echo_result)
        embedding = self.embedder.embed(primary_text, memory_action="add")
        return echo_result, effective_strength, mem_categories, embedding

    def _process_single_memory(
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
    ) -> Optional[Dict[str, Any]]:
        """Process and store a single memory item. Returns result dict or None if skipped."""
        content = mem.get("content", "").strip()
        if not content:
            return None

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
            forget_result = self._forget_by_query(query, forget_filters)
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

        # Resolve store identifiers and scope metadata.
        store_agent_id, store_run_id, store_app_id, store_filters = self._resolve_memory_metadata(
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
        _should_categorize = (
            self.category_processor
            and self.category_config.auto_categorize
            and not mem_categories
        )

        # Site 1: Parallel echo encoding + category detection
        _use_parallel = (
            self._executor is not None
            and self.parallel_config
            and self.parallel_config.parallel_add
            and _should_categorize
            and self.echo_processor
            and self.echo_config.enable_echo
        )

        if _use_parallel:
            # Run echo and category detection in parallel (both only read content)
            def _do_echo():
                depth_override = EchoDepth(echo_depth) if echo_depth else None
                return self.echo_processor.process(content, depth=depth_override)

            def _do_category():
                return self.category_processor.detect_category(
                    content,
                    metadata=mem_metadata,
                    use_llm=self.category_config.use_llm_categorization,
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
            primary_text = self._select_primary_text(content, echo_result_p)
            embedding = self.embedder.embed(primary_text, memory_action="add")
            echo_result = echo_result_p
        else:
            # Sequential path (original behavior)
            if _should_categorize:
                category_match = self.category_processor.detect_category(
                    content,
                    metadata=mem_metadata,
                    use_llm=self.category_config.use_llm_categorization,
                )
                mem_categories = [category_match.category_id]
                mem_metadata["category_confidence"] = category_match.confidence
                mem_metadata["category_auto"] = True

            # Encode memory (echo + embedding).
            echo_result, effective_strength, mem_categories, embedding = self._encode_memory(
                content, echo_depth, mem_categories, mem_metadata, initial_strength,
            )

        nearest, similarity = self._nearest_memory(embedding, store_filters)
        repeated_threshold = max(self.fadem_config.conflict_similarity_threshold - 0.05, 0.7)
        if similarity >= repeated_threshold:
            policy_repeated = True
            high_confidence = True

        if not explicit_remember and not high_confidence:
            low_confidence = True

        # Conflict resolution against nearest memory in scope.
        event = "ADD"
        existing = None
        resolution = None
        if nearest and similarity >= self.fadem_config.conflict_similarity_threshold:
            existing = nearest

        if existing and self.fadem_config.enable_forgetting:
            resolution = resolve_conflict(existing, content, self.llm, self.config.custom_conflict_prompt)

            if resolution.classification == "CONTRADICTORY":
                self._demote_existing(existing, reason="CONTRADICTORY")
                event = "UPDATE"
            elif resolution.classification == "SUBSUMES":
                content = resolution.merged_content or content
                self._demote_existing(existing, reason="SUBSUMES")
                event = "UPDATE"
            elif resolution.classification == "SUBSUMED":
                boosted_strength = min(1.0, float(existing.get("strength", 1.0)) + 0.05)
                self.db.update_memory(existing["id"], {"strength": boosted_strength})
                self.db.increment_access(existing["id"])
                return {
                    "id": existing["id"],
                    "memory": existing.get("memory", ""),
                    "event": "NOOP",
                    "layer": existing.get("layer", "sml"),
                    "strength": boosted_strength,
                }

        if existing and event == "UPDATE" and resolution and resolution.classification == "SUBSUMES":
            # Re-encode merged content.
            echo_result, _, mem_categories, embedding = self._encode_memory(
                content, echo_depth, mem_categories, mem_metadata, initial_strength,
            )

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
        memory_type = self._classify_memory_type(mem_metadata, role)

        # Gap 4: Initialize multi-trace strength
        s_fast_val = None
        s_mid_val = None
        s_slow_val = None
        if self.distillation_config and self.distillation_config.enable_multi_trace:
            s_fast_val, s_mid_val, s_slow_val = initialize_traces(effective_strength, is_new=True)

        memory_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        memory_data = {
            "id": memory_id,
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
            "decay_lambda": self.fadem_config.sml_decay_rate,
            "status": "active",
            "importance": mem_metadata.get("importance", 0.5),
            "sensitivity": mem_metadata.get("sensitivity", "normal"),
            "namespace": namespace_value,
            "memory_type": memory_type,
            "s_fast": s_fast_val,
            "s_mid": s_mid_val,
            "s_slow": s_slow_val,
        }

        vectors, payloads, vector_ids = self._build_index_vectors(
            memory_id=memory_id,
            content=content,
            primary_text=self._select_primary_text(content, echo_result),
            embedding=embedding,
            echo_result=echo_result,
            metadata=mem_metadata,
            categories=mem_categories,
            user_id=user_id,
            agent_id=store_agent_id,
            run_id=store_run_id,
            app_id=store_app_id,
        )

        self.db.add_memory(memory_data)
        if vectors:
            try:
                self.vector_store.insert(vectors=vectors, payloads=payloads, ids=vector_ids)
            except Exception as e:
                # Vector insert failed — roll back the DB record to prevent desync.
                logger.error(
                    "Vector insert failed for memory %s, rolling back DB record: %s",
                    memory_id, e,
                )
                try:
                    self.db.delete_memory(memory_id, use_tombstone=False)
                except Exception as rollback_err:
                    logger.critical(
                        "CRITICAL: DB rollback also failed for memory %s — manual cleanup required: %s",
                        memory_id, rollback_err,
                    )
                raise

        # Post-store hooks.
        if self.category_processor and mem_categories:
            for cat_id in mem_categories:
                self.category_processor.update_category_stats(
                    cat_id, effective_strength, is_addition=True
                )

        if self.knowledge_graph:
            self.knowledge_graph.extract_entities(
                content=content,
                memory_id=memory_id,
                use_llm=self.graph_config.use_llm_extraction,
            )
            if self.graph_config.auto_link_entities:
                self.knowledge_graph.link_by_shared_entities(memory_id)

        if self.scene_processor:
            try:
                self._assign_to_scene(memory_id, content, embedding, user_id, now)
            except Exception as e:
                logger.warning("Scene assignment failed for %s: %s", memory_id, e)

        if self.profile_processor:
            try:
                self._update_profiles(memory_id, content, mem_metadata, user_id)
            except Exception as e:
                logger.warning("Profile update failed for %s: %s", memory_id, e)

        return {
            "id": memory_id,
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
        hybrid_alpha: float = 0.7,  # Weight for semantic vs keyword (0.7 = 70% semantic)
        min_strength: float = 0.1,
        boost_on_access: bool = True,
        use_echo_rerank: bool = True,  # EchoMem: use echo metadata for re-ranking
        use_category_boost: bool = True,  # CategoryMem: boost by category relevance
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not query or not query.strip():
            return {"results": [], "context_packet": None}

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
        normalized_agent_category = self._normalize_agent_category(agent_category)
        normalized_connector_ids = [
            cid for cid in (self._normalize_connector_id(c) for c in (connector_ids or [])) if cid
        ]
        normalized_scope_filter = None
        if scope_filter:
            if isinstance(scope_filter, str):
                scope_filter = [scope_filter]
            normalized_scope_filter = {
                scope_value
                for scope_value in (self._normalize_scope(s) for s in scope_filter)
                if scope_value
            }

        # Gap 5: Classify query intent for routing
        query_intent = None
        if (
            self.distillation_config
            and self.distillation_config.enable_intent_routing
            and self.distillation_config.enable_memory_types
        ):
            query_intent = classify_intent(query)

        query_embedding = self.embedder.embed(query, memory_action="search")
        vector_results = self.vector_store.search(
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
            connector_results = self.vector_store.search(
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

        vector_results = self._collapse_vector_results(vector_results)

        # Prepare query terms for echo-based re-ranking
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        # CategoryMem: Detect relevant categories for the query
        query_category_id = None
        related_category_ids = set()
        if self.category_processor and use_category_boost:
            category_match = self.category_processor.detect_category(
                query, use_llm=False  # Fast match only for search
            )
            if category_match.confidence > 0.4:
                query_category_id = category_match.category_id
                related_category_ids = set(
                    self.category_processor.find_related_categories(query_category_id)
                )
                # Record access to category
                self.category_processor.access_category(query_category_id)

        # Phase 2: Bulk-fetch all candidate memories to eliminate N+1 queries.
        candidate_ids = [self._resolve_memory_id(vr) for vr in vector_results]
        vr_by_id = {self._resolve_memory_id(vr): vr for vr in vector_results}
        memories_bulk = self.db.get_memories_bulk(candidate_ids)

        results: List[Dict[str, Any]] = []
        access_ids: List[str] = []
        strength_updates: Dict[str, float] = {}
        promotion_ids: List[str] = []
        reecho_ids: List[str] = []
        subscriber_ids: List[str] = []

        # Pre-create HybridSearcher outside the loop to avoid re-allocation per result.
        hybrid_searcher = HybridSearcher(alpha=hybrid_alpha) if keyword_search else None

        for memory_id in candidate_ids:
            memory = memories_bulk.get(memory_id)
            if not memory:
                continue

            # Skip expired memories (cleanup happens in apply_decay, not during search)
            if self._is_expired(memory):
                continue

            if memory.get("strength", 1.0) < min_strength:
                continue
            if categories and not any(c in memory.get("categories", []) for c in categories):
                continue
            if filters and not matches_filters({**memory, **memory.get("metadata", {})}, filters):
                continue

            metadata = memory.get("metadata", {}) or {}
            scope = self._resolve_scope(memory)
            if normalized_scope_filter and scope not in normalized_scope_filter:
                continue
            if not self._allows_scope(
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

            combined *= self._get_scope_weight(scope)

            # EchoMem: Apply echo-based re-ranking boost
            echo_boost = 0.0
            if use_echo_rerank and self.echo_config.enable_echo:
                echo_boost = self._calculate_echo_boost(query_lower, query_terms, metadata)
                combined = combined * (1 + echo_boost)

            # CategoryMem: Apply category-based re-ranking boost
            category_boost = 0.0
            memory_categories = set(memory.get("categories", []))
            if use_category_boost and self.category_processor and query_category_id:
                if query_category_id in memory_categories:
                    category_boost = self.category_config.category_boost_weight
                elif memory_categories & related_category_ids:
                    category_boost = self.category_config.cross_category_boost
                combined = combined * (1 + category_boost)

            # Gap 5: Intent-based retrieval routing boost
            intent_boost = 0.0
            mem_type = memory.get("memory_type", "semantic")
            if query_intent and self.distillation_config:
                dc = self.distillation_config
                if query_intent == QueryIntent.EPISODIC and mem_type == "episodic":
                    intent_boost = dc.episodic_boost
                elif query_intent == QueryIntent.SEMANTIC and mem_type == "semantic":
                    intent_boost = dc.semantic_boost
                elif query_intent == QueryIntent.MIXED:
                    intent_boost = dc.intersection_boost
                combined = combined * (1 + intent_boost)

            # KnowledgeGraph: Boost for memories sharing entities with query terms
            graph_boost = 0.0
            if self.knowledge_graph:
                memory_entities = self.knowledge_graph.memory_entities.get(memory["id"], set())
                for entity_name in memory_entities:
                    if entity_name.lower() in query_lower or any(
                        term in entity_name.lower() for term in query_terms
                    ):
                        graph_boost = self.graph_config.graph_boost_weight
                        break
                combined = combined * (1 + graph_boost)

            if boost_on_access:
                access_ids.append(memory["id"])
                if self.fadem_config.access_strength_boost > 0:
                    boosted_strength = min(1.0, strength + self.fadem_config.access_strength_boost)
                    if boosted_strength != strength:
                        strength_updates[memory["id"]] = boosted_strength
                        strength = boosted_strength
                promotion_ids.append(memory["id"])
                # EchoMem: Re-echo on frequent access
                if (
                    self.echo_processor
                    and self.echo_config.reecho_on_access
                    and memory.get("access_count", 0) >= self.echo_config.reecho_threshold
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
                    "memory_type": mem_type,
                    "query_intent": query_intent.value if query_intent else None,
                }
            )

        # Phase 2: Batch DB writes instead of per-result round-trips.
        if access_ids:
            self.db.increment_access_bulk(access_ids)
        if strength_updates:
            self.db.update_strength_bulk(strength_updates)
        for mid in promotion_ids:
            self._check_promotion(mid)
        # Site 2: Parallel re-echo
        if (
            reecho_ids
            and self._executor is not None
            and self.parallel_config
            and self.parallel_config.parallel_reecho
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
                self.db.add_memory_subscriber(mid, f"agent:{agent_id}", ref_type="weak")

        # Persist category access updates
        if self.category_processor:
            self._persist_categories()

        results.sort(key=lambda x: x["composite_score"], reverse=True)
        return {"results": results[:limit]}

    def _calculate_echo_boost(
        self, query_lower: str, query_terms: set, metadata: Dict[str, Any]
    ) -> float:
        """Calculate re-ranking boost based on echo metadata matches."""
        boost = 0.0

        # Keyword match boost (each matching keyword adds 0.05)
        keywords = metadata.get("echo_keywords", [])
        if keywords:
            keyword_matches = sum(1 for kw in keywords if kw.lower() in query_lower)
            boost += keyword_matches * 0.05

        # Question form similarity boost (if query is similar to question_form)
        question_form = metadata.get("echo_question_form", "")
        if question_form:
            q_terms = set(question_form.lower().split())
            overlap = len(query_terms & q_terms)
            if overlap > 0:
                boost += min(0.15, overlap * 0.05)

        # Implication match boost
        implications = metadata.get("echo_implications", [])
        if implications:
            for impl in implications:
                impl_terms = set(impl.lower().split())
                if query_terms & impl_terms:
                    boost += 0.03

        # Cap boost at 0.3 (30% max increase)
        return min(0.3, boost)

    def _reecho_memory(self, memory_id: str) -> None:
        """Re-process a memory through deeper echo to strengthen it."""
        memory = self.db.get_memory(memory_id)
        if not memory or not self.echo_processor:
            return

        try:
            echo_result = self.echo_processor.reecho(memory)
            metadata = memory.get("metadata", {})
            metadata.update(echo_result.to_metadata())

            # Update memory with new echo data and boosted strength
            new_strength = min(1.0, memory.get("strength", 1.0) * 1.1)  # 10% boost
            self.db.update_memory(memory_id, {
                "metadata": metadata,
                "strength": new_strength,
            })
            self.db.log_event(memory_id, "REECHO", old_strength=memory.get("strength"), new_strength=new_strength)
            self._update_vectors_for_memory(memory_id, metadata)
        except Exception as e:
            logger.warning("Re-echo failed for memory %s: %s", memory_id, e)

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        memory = self.db.get_memory(memory_id)
        if memory:
            self.db.increment_access(memory_id)
        return memory

    # Hard cap to prevent unbounded result sets even if callers pass a huge limit.
    _GET_ALL_MAX_LIMIT = 10_000

    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        filters: Dict[str, Any] = None,
        categories: List[str] = None,
        limit: int = 100,
        layer: Optional[str] = None,
        min_strength: float = 0.0,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # Clamp limit to a sensible maximum to avoid unbounded result sets.
        limit = max(1, min(limit, self._GET_ALL_MAX_LIMIT))

        _, effective_filters = build_filters_and_metadata(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            input_filters=filters,
        )
        if app_id:
            effective_filters["app_id"] = app_id

        memories = self.db.get_all_memories(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            app_id=app_id,
            layer=layer,
            min_strength=min_strength,
            limit=limit,
        )

        if categories:
            memories = [m for m in memories if any(c in m.get("categories", []) for c in categories)]

        if filters:
            memories = [m for m in memories if matches_filters({**m, **m.get("metadata", {})}, filters)]

        memories = [m for m in memories if not self._is_expired(m)]
        return {"results": memories[:limit]}

    def update(self, memory_id: str, data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        memory = self.db.get_memory(memory_id)
        if not memory:
            missing_memory = data.get("content") if isinstance(data, dict) else data
            return {"id": memory_id, "memory": missing_memory, "event": "ERROR"}

        content: Optional[str]
        metadata_updates: Optional[Dict[str, Any]] = None
        categories_updates: Optional[List[str]] = None

        if isinstance(data, dict):
            content = data.get("content") or data.get("memory")
            metadata_updates = data.get("metadata")
            if "categories" in data:
                categories_updates = normalize_categories(data.get("categories"))
        else:
            content = data

        if content is None and metadata_updates is None and categories_updates is None:
            return {"id": memory_id, "memory": memory.get("memory", ""), "event": "ERROR"}

        metadata = dict(memory.get("metadata", {}) or {})
        categories = list(memory.get("categories", []) or [])
        existing_content = memory.get("memory", "")
        echo_result = None

        content_changed = content is not None and content != existing_content
        if content is None:
            content = existing_content

        if content_changed and self.echo_processor and self.echo_config.enable_echo:
            depth_override = None
            current_depth = metadata.get("echo_depth")
            if current_depth:
                try:
                    depth_override = EchoDepth(current_depth)
                except ValueError:
                    depth_override = None
            echo_result = self.echo_processor.process(content, depth=depth_override)
            metadata.update(echo_result.to_metadata())
            if not categories and echo_result.category:
                categories = [echo_result.category]

        if metadata_updates:
            metadata.update(metadata_updates)
        if categories_updates is not None:
            categories = categories_updates

        if content_changed:
            primary_text = self._select_primary_text(content, echo_result)
            new_embedding = self.embedder.embed(primary_text, memory_action="update")
            success = self.db.update_memory(
                memory_id,
                {"memory": content, "embedding": new_embedding, "metadata": metadata, "categories": categories},
            )
            if success:
                self._delete_vectors_for_memory(memory_id)
                vectors, payloads, vector_ids = self._build_index_vectors(
                    memory_id=memory_id,
                    content=content,
                    primary_text=primary_text,
                    embedding=new_embedding,
                    echo_result=echo_result,
                    metadata=metadata,
                    categories=categories,
                    user_id=memory.get("user_id"),
                    agent_id=memory.get("agent_id"),
                    run_id=memory.get("run_id"),
                    app_id=memory.get("app_id"),
                )
                try:
                    self.vector_store.insert(vectors=vectors, payloads=payloads, ids=vector_ids)
                except Exception as e:
                    logger.error(
                        "Vector re-insert failed during update for memory %s: %s. "
                        "DB was updated but vector index is stale — will be rebuilt on next update.",
                        memory_id, e,
                    )
        else:
            success = self.db.update_memory(
                memory_id,
                {"metadata": metadata, "categories": categories},
            )
            if success:
                payload_updates = dict(metadata)
                payload_updates["categories"] = categories
                try:
                    self._update_vectors_for_memory(memory_id, payload_updates)
                except Exception as e:
                    logger.error(
                        "Vector payload update failed for memory %s: %s. "
                        "DB is authoritative — vector metadata may be stale.",
                        memory_id, e,
                    )

        return {"id": memory_id, "memory": content, "event": "UPDATE" if success else "ERROR"}

    def delete(self, memory_id: str) -> Dict[str, Any]:
        logger.info("Deleting memory %s (tombstone=%s)", memory_id, self.fadem_config.use_tombstone_deletion)
        self.db.delete_memory(memory_id, use_tombstone=self.fadem_config.use_tombstone_deletion)
        self._delete_vectors_for_memory(memory_id)
        return {"id": memory_id, "deleted": True}

    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        app_id: Optional[str] = None,
        filters: Dict[str, Any] = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not any([user_id, agent_id, run_id, app_id, filters]):
            raise FadeMemValidationError(
                "At least one filter is required to delete all memories. Use reset() to clear everything.",
                error_code="VALIDATION_004",
            )
        memories = self.db.get_all_memories(user_id=user_id, agent_id=agent_id, run_id=run_id, app_id=app_id)
        if filters:
            memories = [m for m in memories if matches_filters({**m, **m.get("metadata", {})}, filters)]

        if dry_run:
            return {"deleted_count": 0, "would_delete": len(memories), "dry_run": True}

        logger.warning(
            "delete_all: deleting %d memories (user_id=%s, agent_id=%s, filters=%s)",
            len(memories), user_id, agent_id, filters,
        )
        count = 0
        for memory in memories:
            self.delete(memory["id"])
            count += 1
        return {"deleted_count": count}

    def history(self, memory_id: str) -> List[Dict[str, Any]]:
        return self.db.get_history(memory_id)

    def reset(self) -> None:
        """Delete ALL memories including tombstoned. This is IRREVERSIBLE."""
        memories = self.db.get_all_memories(include_tombstoned=True)
        logger.warning("reset: permanently deleting ALL %d memories", len(memories))
        for mem in memories:
            self.delete(mem["id"])
        if hasattr(self.vector_store, "reset"):
            self.vector_store.reset()

    # FadeMem-specific methods
    def apply_decay(self, scope: Dict[str, Any] = None) -> Dict[str, Any]:
        if not self.fadem_config.enable_forgetting:
            return {"decayed": 0, "forgotten": 0, "promoted": 0}

        stale_refs_removed = 0

        memories = self.db.get_all_memories(
            user_id=scope.get("user_id") if scope else None,
            agent_id=scope.get("agent_id") if scope else None,
            run_id=scope.get("run_id") if scope else None,
            app_id=scope.get("app_id") if scope else None,
        )

        decayed = 0
        forgotten = 0
        promoted = 0

        for memory in memories:
            if memory.get("immutable"):
                continue

            # Task-aware decay: active tasks don't decay
            if memory.get("memory_type") == "task":
                _md = memory.get("metadata") or {}
                if isinstance(_md, str):
                    import json as _json
                    try:
                        _md = _json.loads(_md)
                    except (ValueError, TypeError):
                        _md = {}
                _ts = _md.get("task_status", "inbox")
                if _ts in ("inbox", "assigned", "active", "review", "blocked"):
                    continue  # skip decay for active tasks

            ref_aware = feature_enabled("ENGRAM_V2_REF_AWARE_DECAY", default=False)
            ref_state = {"strong": 0, "weak": 0}
            if ref_aware:
                ref_state = self.db.get_memory_refcount(memory["id"])
                if int(ref_state.get("strong", 0)) > 0:
                    # Strong references pause decay/deletion.
                    metrics.record_ref_protected_skip(1)
                    continue

            # Gap 4: Multi-trace decay (if enabled and traces are initialized)
            use_multi_trace = (
                self.distillation_config
                and self.distillation_config.enable_multi_trace
                and memory.get("s_fast") is not None
            )

            if use_multi_trace:
                s_f, s_m, s_s = decay_traces(
                    s_fast=float(memory.get("s_fast", 0.0)),
                    s_mid=float(memory.get("s_mid", 0.0)),
                    s_slow=float(memory.get("s_slow", 0.0)),
                    last_accessed=memory.get("last_accessed", datetime.now(timezone.utc).isoformat()),
                    access_count=memory.get("access_count", 0),
                    config=self.distillation_config,
                )
                new_strength = compute_effective_strength(s_f, s_m, s_s, self.distillation_config)
            else:
                new_strength = calculate_decayed_strength(
                    current_strength=memory.get("strength", 1.0),
                    last_accessed=memory.get("last_accessed", datetime.now(timezone.utc).isoformat()),
                    access_count=memory.get("access_count", 0),
                    layer=memory.get("layer", "sml"),
                    config=self.fadem_config,
                )

            if ref_aware and int(ref_state.get("weak", 0)) > 0:
                weak = min(int(ref_state.get("weak", 0)), 10)
                dampening = 1.0 + weak * 0.15
                retained_floor = memory.get("strength", 1.0) * (1.0 - 0.03 / dampening)
                new_strength = max(new_strength, retained_floor)

            forget_threshold = self.fadem_config.forgetting_threshold
            if ref_aware and int(ref_state.get("weak", 0)) > 0:
                weak = min(int(ref_state.get("weak", 0)), 10)
                forget_threshold = forget_threshold / (1.0 + weak * 0.25)

            if new_strength < forget_threshold:
                self.delete(memory["id"])
                forgotten += 1
                continue

            if new_strength != memory.get("strength"):
                if use_multi_trace:
                    self.db.update_multi_trace(memory["id"], s_f, s_m, s_s, new_strength)
                else:
                    self.db.update_memory(memory["id"], {"strength": new_strength})
                self.db.log_event(memory["id"], "DECAY", old_strength=memory.get("strength"), new_strength=new_strength)
                decayed += 1

            if should_promote(
                memory.get("layer", "sml"),
                memory.get("access_count", 0),
                new_strength,
                self.fadem_config,
            ):
                self.db.update_memory(memory["id"], {"layer": "lml"})
                self.db.log_event(memory["id"], "PROMOTE", old_layer="sml", new_layer="lml")
                promoted += 1

        if self.fadem_config.use_tombstone_deletion:
            self.db.purge_tombstoned()

        # Gap 3: Advanced forgetting mechanisms
        interference_stats = {"checked": 0, "demoted": 0}
        redundancy_stats = {"groups_fused": 0, "memories_fused": 0}
        homeostasis_stats = {"namespaces_over_budget": 0, "pressured": 0, "forgotten": 0}

        if self.distillation_config:
            user_id = scope.get("user_id") if scope else None

            _do_interference = self.distillation_config.enable_interference_pruning
            _do_redundancy = self.distillation_config.enable_redundancy_collapse

            # Site 3: Parallel interference + redundancy during apply_decay
            _use_parallel_decay = (
                self._executor is not None
                and self.parallel_config
                and self.parallel_config.parallel_decay
                and _do_interference
                and _do_redundancy
            )

            if _use_parallel_decay:
                pruner = InterferencePruner(
                    db=self.db,
                    config=self.distillation_config,
                    fadem_config=self.fadem_config,
                    resolve_conflict_fn=resolve_conflict,
                    search_fn=self.vector_store.search,
                    llm=self.llm,
                )
                collapser = RedundancyCollapser(
                    db=self.db,
                    config=self.distillation_config,
                    fuse_fn=self.fuse_memories,
                    search_fn=self.vector_store.search,
                )
                def _run_pruner():
                    return pruner.run(memories, user_id=user_id)

                def _run_collapser():
                    return collapser.run(memories, user_id=user_id)

                interference_stats, redundancy_stats = self._executor.run_parallel([
                    (_run_pruner, ()),
                    (_run_collapser, ()),
                ])
            else:
                if _do_interference:
                    pruner = InterferencePruner(
                        db=self.db,
                        config=self.distillation_config,
                        fadem_config=self.fadem_config,
                        resolve_conflict_fn=resolve_conflict,
                        search_fn=self.vector_store.search,
                        llm=self.llm,
                    )
                    interference_stats = pruner.run(memories, user_id=user_id)

                if _do_redundancy:
                    collapser = RedundancyCollapser(
                        db=self.db,
                        config=self.distillation_config,
                        fuse_fn=self.fuse_memories,
                        search_fn=self.vector_store.search,
                    )
                    redundancy_stats = collapser.run(memories, user_id=user_id)

            if self.distillation_config.enable_homeostasis and user_id:
                normalizer = HomeostaticNormalizer(
                    db=self.db,
                    config=self.distillation_config,
                    fadem_config=self.fadem_config,
                    delete_fn=self.delete,
                )
                homeostasis_stats = normalizer.run(user_id)

        self.db.log_decay(decayed, forgotten, promoted)
        return {
            "decayed": decayed,
            "forgotten": forgotten,
            "promoted": promoted,
            "stale_refs_removed": stale_refs_removed,
            "interference": interference_stats,
            "redundancy": redundancy_stats,
            "homeostasis": homeostasis_stats,
        }

    def fuse_memories(self, memory_ids: List[str], user_id: Optional[str] = None) -> Dict[str, Any]:
        memories = [self.db.get_memory(mid) for mid in memory_ids]
        memories = [m for m in memories if m]
        if len(memories) < 2:
            return {"error": "Need at least 2 memories to fuse"}

        fused = fuse_memories(memories, self.llm, self.config.custom_fusion_prompt)
        result = self.add(
            fused.content,
            user_id=user_id or memories[0].get("user_id"),
            agent_id=memories[0].get("agent_id"),
            run_id=memories[0].get("run_id"),
            app_id=memories[0].get("app_id"),
            initial_layer=fused.layer,
            initial_strength=fused.strength,
            infer=False,
        )

        for mid in memory_ids:
            self.delete(mid)

        fused_id = result.get("results", [{}])[0].get("id") if result.get("results") else None
        return {"fused_id": fused_id, "source_ids": memory_ids, "fused_memory": fused.content}

    def get_stats(self, user_id: Optional[str] = None, agent_id: Optional[str] = None) -> Dict[str, Any]:
        memories = self.db.get_all_memories(user_id=user_id, agent_id=agent_id)
        sml_count = sum(1 for m in memories if m.get("layer") == "sml")
        lml_count = sum(1 for m in memories if m.get("layer") == "lml")
        strengths = [m.get("strength", 1.0) for m in memories]
        avg_strength = sum(strengths) / len(strengths) if strengths else 0.0

        # EchoMem stats
        echo_stats = {"shallow": 0, "medium": 0, "deep": 0, "none": 0}
        for m in memories:
            metadata = m.get("metadata", {})
            depth = metadata.get("echo_depth", "none")
            if depth in echo_stats:
                echo_stats[depth] += 1
            else:
                echo_stats["none"] += 1

        return {
            "total": len(memories),
            "sml_count": sml_count,
            "lml_count": lml_count,
            "avg_strength": round(avg_strength, 3),
            "echo_stats": echo_stats,
            "echo_enabled": self.echo_config.enable_echo if self.echo_config else False,
        }

    def promote(self, memory_id: str) -> Dict[str, Any]:
        return {"success": self.db.update_memory(memory_id, {"layer": "lml"})}

    def demote(self, memory_id: str) -> Dict[str, Any]:
        return {"success": self.db.update_memory(memory_id, {"layer": "sml"})}

    # Internal helpers
    def _extract_memories(
        self,
        messages: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        prompt: Optional[str] = None,
        includes: Optional[str] = None,
        excludes: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conversation = parse_messages(messages)
        existing = self.db.get_all_memories(
            user_id=metadata.get("user_id"),
            agent_id=metadata.get("agent_id"),
            run_id=metadata.get("run_id"),
            app_id=metadata.get("app_id"),
        )
        existing_text = "\n".join([m.get("memory", "") for m in existing])

        if prompt or self.config.custom_fact_extraction_prompt:
            extraction_prompt = prompt or self.config.custom_fact_extraction_prompt
        else:
            if self._should_use_agent_memory_extraction(messages, metadata):
                extraction_prompt = AGENT_MEMORY_EXTRACTION_PROMPT
            else:
                extraction_prompt = MEMORY_EXTRACTION_PROMPT
        prompt_text = extraction_prompt.format(conversation=conversation, existing_memories=existing_text)

        try:
            response = self.llm.generate(prompt_text)
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

    def _should_use_agent_memory_extraction(self, messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> bool:
        has_agent_id = metadata.get("agent_id") is not None
        has_assistant_messages = any(msg.get("role") == "assistant" for msg in messages)
        return has_agent_id and has_assistant_messages

    def _classify_memory_type(self, metadata: Dict[str, Any], role: str) -> str:
        """Classify a memory as 'episodic' or 'semantic' (Gap 1).

        When enable_memory_types is False, everything stays 'semantic' (backward compat).
        """
        if not self.distillation_config or not self.distillation_config.enable_memory_types:
            return self.distillation_config.default_memory_type if self.distillation_config else "semantic"

        # Explicit override from metadata
        explicit = metadata.get("memory_type")
        if explicit in ("episodic", "semantic", "task", "note", "procedural",
                       "project", "project_status", "project_tag"):
            return explicit

        # Distilled content is always semantic
        if metadata.get("is_distilled"):
            return "semantic"

        # Conversation messages (user/assistant) are episodic
        if role in ("user", "assistant"):
            return "episodic"

        # Active memory signals are semantic
        if metadata.get("source_type") == "active_signal":
            return "semantic"

        return "semantic"

    def _select_primary_text(self, content: str, echo_result: Optional[EchoResult]) -> str:
        if self.echo_config.use_question_embedding and echo_result and echo_result.question_form:
            return echo_result.question_form
        return content

    def _resolve_memory_id(self, vector_result: Any) -> str:
        payload = getattr(vector_result, "payload", None) or {}
        return str(payload.get("memory_id") or vector_result.id)

    def _collapse_vector_results(self, vector_results: List[Any]) -> List[Any]:
        collapsed: Dict[str, Any] = {}
        for result in vector_results:
            memory_id = self._resolve_memory_id(result)
            existing = collapsed.get(memory_id)
            if not existing or float(result.score) > float(existing.score):
                collapsed[memory_id] = result
        return list(collapsed.values())

    def _normalize_scope(self, scope: Optional[str]) -> Optional[str]:
        if scope is None:
            return None
        value = str(scope).strip().lower()
        return value if value in SCOPE_VALUES else None

    def _normalize_agent_category(self, category: Optional[str]) -> Optional[str]:
        if category is None:
            return None
        value = str(category).strip().lower()
        return value or None

    def _normalize_connector_id(self, connector_id: Optional[str]) -> Optional[str]:
        if connector_id is None:
            return None
        value = str(connector_id).strip().lower()
        return value or None

    def _infer_scope(
        self,
        *,
        scope: Optional[str],
        connector_id: Optional[str],
        agent_category: Optional[str],
        policy_explicit: bool,
        agent_id: Optional[str],
    ) -> str:
        normalized_scope = self._normalize_scope(scope)
        normalized_connector_id = self._normalize_connector_id(connector_id)
        normalized_agent_category = self._normalize_agent_category(agent_category)

        if normalized_scope:
            if normalized_scope == MemoryScope.CONNECTOR.value and not normalized_connector_id:
                return MemoryScope.CATEGORY.value if normalized_agent_category else MemoryScope.GLOBAL.value
            if normalized_scope == MemoryScope.CATEGORY.value and not normalized_agent_category:
                return MemoryScope.GLOBAL.value
            if normalized_scope == MemoryScope.AGENT.value and not agent_id:
                return MemoryScope.GLOBAL.value
            return normalized_scope

        if normalized_connector_id:
            return MemoryScope.CONNECTOR.value
        if policy_explicit:
            return MemoryScope.CATEGORY.value if normalized_agent_category else MemoryScope.GLOBAL.value
        if agent_id:
            return MemoryScope.AGENT.value
        return MemoryScope.GLOBAL.value

    def _resolve_scope(self, memory: Dict[str, Any]) -> str:
        metadata = memory.get("metadata", {}) or {}
        scope = self._normalize_scope(metadata.get("scope"))
        if scope:
            return scope

        return self._infer_scope(
            scope=None,
            connector_id=metadata.get("connector_id"),
            agent_category=metadata.get("agent_category"),
            policy_explicit=bool(metadata.get("policy_explicit")),
            agent_id=memory.get("agent_id"),
        )

    def _get_scope_weight(self, scope: str) -> float:
        if self.scope_config:
            weight_map = {
                MemoryScope.AGENT.value: getattr(self.scope_config, "agent_weight", DEFAULT_SCOPE_WEIGHTS["agent"]),
                MemoryScope.CONNECTOR.value: getattr(self.scope_config, "connector_weight", DEFAULT_SCOPE_WEIGHTS["connector"]),
                MemoryScope.CATEGORY.value: getattr(self.scope_config, "category_weight", DEFAULT_SCOPE_WEIGHTS["category"]),
                MemoryScope.GLOBAL.value: getattr(self.scope_config, "global_weight", DEFAULT_SCOPE_WEIGHTS["global"]),
            }
        else:
            weight_map = DEFAULT_SCOPE_WEIGHTS
        return float(weight_map.get(scope, 1.0))

    def _allows_scope(
        self,
        memory: Dict[str, Any],
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
        agent_category: Optional[str],
        connector_ids: Optional[List[str]],
    ) -> bool:
        metadata = memory.get("metadata", {}) or {}
        stored_scope = self._normalize_scope(metadata.get("scope"))
        memory_agent_id = memory.get("agent_id")

        if stored_scope is None and not agent_category:
            if agent_id and memory_agent_id not in (None, agent_id):
                return self._is_shareable_memory(memory)
            return True

        scope = stored_scope or self._resolve_scope(memory)

        if scope == MemoryScope.GLOBAL.value:
            return True
        if scope == MemoryScope.AGENT.value:
            return bool(agent_id) and memory_agent_id == agent_id
        if scope == MemoryScope.CATEGORY.value:
            if not agent_category:
                return False
            mem_category = self._normalize_agent_category(metadata.get("agent_category"))
            return mem_category == self._normalize_agent_category(agent_category)
        if scope == MemoryScope.CONNECTOR.value:
            if not connector_ids:
                return False
            mem_connector = self._normalize_connector_id(metadata.get("connector_id"))
            if not mem_connector:
                return False
            normalized_ids = {
                cid
                for cid in (self._normalize_connector_id(c) for c in connector_ids)
                if cid
            }
            if mem_connector not in normalized_ids:
                return False
            request_category = self._normalize_agent_category(agent_category)
            mem_category = self._normalize_agent_category(metadata.get("agent_category"))
            if request_category and mem_category and request_category != mem_category:
                return False
            return True

        return True

    def _build_index_vectors(
        self,
        *,
        memory_id: str,
        content: str,
        primary_text: str,
        embedding: List[float],
        echo_result: Optional[EchoResult],
        metadata: Dict[str, Any],
        categories: List[str],
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        app_id: Optional[str],
    ) -> tuple[List[List[float]], List[Dict[str, Any]], List[str]]:
        base_payload = dict(metadata)
        base_payload.update(
            {
                "memory_id": memory_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "app_id": app_id,
                "categories": categories,
            }
        )

        vectors: List[List[float]] = []
        payloads: List[Dict[str, Any]] = []
        vector_ids: List[str] = []
        seen: set[str] = set()

        def add_node(
            text: str,
            node_type: str,
            subtype: Optional[str] = None,
            vector: Optional[List[float]] = None,
            node_id: Optional[str] = None,
        ) -> None:
            if not text:
                return
            cleaned = str(text).strip()
            if not cleaned:
                return
            key = cleaned.lower()
            if key in seen:
                return
            seen.add(key)

            payload = base_payload.copy()
            payload.update(
                {
                    "text": cleaned,
                    "type": node_type,
                }
            )
            if subtype:
                payload["subtype"] = subtype
            if node_type == "primary":
                payload["memory"] = content
            if echo_result and echo_result.category:
                payload["category"] = echo_result.category

            vectors.append(vector if vector is not None else self.embedder.embed(cleaned, memory_action="add"))
            payloads.append(payload)
            vector_ids.append(node_id or str(uuid.uuid4()))

        primary_subtype = "question_form" if primary_text != content else None
        add_node(primary_text, "primary", subtype=primary_subtype, vector=embedding, node_id=memory_id)

        if primary_text != content:
            add_node(content, "echo_node", subtype="content")

        if echo_result:
            for paraphrase in echo_result.paraphrases:
                add_node(paraphrase, "echo_node", subtype="paraphrase")
            for question in echo_result.questions:
                add_node(question, "echo_node", subtype="question")

        return vectors, payloads, vector_ids

    def _delete_vectors_for_memory(self, memory_id: str) -> None:
        try:
            vectors = self.vector_store.list(filters={"memory_id": memory_id})
            if not vectors:
                self.vector_store.delete(memory_id)
                return
            for vec in vectors:
                self.vector_store.delete(vec.id)
        except Exception as e:
            logger.error(
                "Failed to delete vectors for memory %s: %s. "
                "Orphaned vector entries may exist.",
                memory_id, e,
            )

    def _update_vectors_for_memory(self, memory_id: str, payload_updates: Dict[str, Any]) -> None:
        try:
            vectors = self.vector_store.list(filters={"memory_id": memory_id})
        except Exception as e:
            logger.error("Failed to list vectors for memory %s: %s", memory_id, e)
            return
        if not vectors:
            try:
                existing = self.vector_store.get(memory_id)
                if existing:
                    payload = existing.payload or {}
                    payload.update(payload_updates)
                    self.vector_store.update(memory_id, payload=payload)
            except Exception as e:
                logger.error("Failed to update vector payload for memory %s: %s", memory_id, e)
            return
        for vec in vectors:
            payload = vec.payload or {}
            payload.update(payload_updates)
            try:
                self.vector_store.update(vec.id, payload=payload)
            except Exception as e:
                logger.error("Failed to update vector %s for memory %s: %s", vec.id, memory_id, e)

    def _nearest_memory(self, embedding: List[float], filters: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], float]:
        results = self.vector_store.search(query=None, vectors=embedding, limit=1, filters=filters)
        if not results:
            return None, 0.0
        memory_id = self._resolve_memory_id(results[0])
        memory = self.db.get_memory(memory_id)
        if not memory:
            return None, 0.0
        return memory, float(results[0].score)

    def _is_shareable_memory(self, memory: Dict[str, Any]) -> bool:
        if memory.get("agent_id") is None:
            return True

        categories = [str(c).lower() for c in memory.get("categories", [])]
        if any(c in SHAREABLE_CATEGORY_IDS for c in categories):
            return True
        if any(any(hint in c for hint in SHAREABLE_CATEGORY_HINTS) for c in categories):
            return True

        metadata = memory.get("metadata", {}) or {}
        echo_category = str(metadata.get("echo_category") or "").lower()
        if echo_category and any(hint in echo_category for hint in SHAREABLE_CATEGORY_HINTS):
            return True

        keywords = metadata.get("echo_keywords") or []
        for kw in keywords:
            kw_lower = str(kw).lower()
            if any(hint in kw_lower for hint in SHAREABLE_CATEGORY_HINTS):
                return True

        if metadata.get("policy_explicit"):
            return True

        return False

    def _demote_existing(self, memory: Dict[str, Any], reason: str) -> None:
        if not memory:
            return
        old_strength = float(memory.get("strength", 1.0))
        old_layer = memory.get("layer", "sml")
        new_strength = min(old_strength, 0.05)
        metadata = dict(memory.get("metadata", {}))
        metadata["superseded"] = True
        metadata["superseded_reason"] = reason
        metadata["superseded_at"] = datetime.now(timezone.utc).isoformat()

        self.db.update_memory(
            memory["id"],
            {
                "strength": new_strength,
                "layer": "sml",
                "metadata": metadata,
            },
        )

        self._update_vectors_for_memory(memory["id"], metadata)

        self.db.log_event(
            memory["id"],
            "DEMOTE",
            old_strength=old_strength,
            new_strength=new_strength,
            old_layer=old_layer,
            new_layer="sml",
        )

    def _forget_by_query(self, query: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = (query or "").strip()
        if not cleaned:
            return {"deleted_count": 0, "deleted_ids": []}

        threshold = max(self.fadem_config.conflict_similarity_threshold, 0.85)
        query_embedding = self.embedder.embed(cleaned, memory_action="forget")
        results = self.vector_store.search(query=None, vectors=query_embedding, limit=20, filters=filters)

        deleted_ids: List[str] = []
        candidates: Dict[str, float] = {}
        for result in results:
            if float(result.score) < threshold:
                continue
            memory_id = self._resolve_memory_id(result)
            best = candidates.get(memory_id)
            if best is None or float(result.score) > best:
                candidates[memory_id] = float(result.score)

        for memory_id in candidates:
            memory = self.db.get_memory(memory_id)
            if not memory:
                continue
            self.delete(memory_id)
            deleted_ids.append(memory_id)

        return {"deleted_count": len(deleted_ids), "deleted_ids": deleted_ids}

    def _find_similar(self, embedding: List[float], filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        memory, similarity = self._nearest_memory(embedding, filters)
        if memory and similarity >= self.fadem_config.conflict_similarity_threshold:
            return memory
        return None

    def _check_promotion(self, memory_id: str) -> None:
        memory = self.db.get_memory(memory_id)
        if memory and should_promote(
            memory.get("layer", "sml"),
            memory.get("access_count", 0),
            memory.get("strength", 1.0),
            self.fadem_config,
        ):
            self.db.update_memory(memory_id, {"layer": "lml"})
            self.db.log_event(memory_id, "PROMOTE", old_layer="sml", new_layer="lml")

    def _is_expired(self, memory: Dict[str, Any]) -> bool:
        expiration = memory.get("expiration_date")
        if not expiration:
            return False
        try:
            exp_date = date.fromisoformat(expiration)
        except Exception:
            return False
        return date.today() > exp_date

    # CategoryMem methods
    def _persist_categories(self) -> None:
        """Persist category state to database."""
        if not self.category_processor:
            return
        categories = self.category_processor.get_all_categories()
        self.db.save_all_categories(categories)

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all categories."""
        if not self.category_processor:
            return []
        return self.category_processor.get_all_categories()

    def get_category(self, category_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific category by ID."""
        if not self.category_processor:
            return None
        cat = self.category_processor.get_category(category_id)
        return cat.to_dict() if cat else None

    def get_category_summary(self, category_id: str, regenerate: bool = False) -> str:
        """
        Get or generate summary for a category.

        Args:
            category_id: Category ID
            regenerate: Force regenerate even if cached

        Returns:
            Summary text
        """
        if not self.category_processor:
            return ""

        cat = self.category_processor.get_category(category_id)
        if not cat:
            return "Category not found."

        # Return cached if available and not forcing regenerate
        if cat.summary and not regenerate:
            return cat.summary

        # Get memories in this category
        memories = self.db.get_memories_by_category(category_id, limit=20)

        return self.category_processor.generate_summary(category_id, memories)

    def get_all_summaries(self) -> Dict[str, str]:
        """
        Get summaries for all categories with memories.

        Returns category-level summaries with dynamic,
        evolving content based on stored memories.

        Returns:
            Dict mapping category name to summary
        """
        if not self.category_processor:
            return {}

        summaries = {}
        for cat in self.category_processor.categories.values():
            if cat.memory_count > 0:
                if not cat.summary:
                    memories = self.db.get_memories_by_category(cat.id, limit=20)
                    self.category_processor.generate_summary(cat.id, memories)
                summaries[cat.name] = cat.summary or f"{cat.memory_count} memories"

        self._persist_categories()
        return summaries

    def get_category_tree(self) -> List[Dict[str, Any]]:
        """
        Get hierarchical category tree.

        Returns:
            List of root categories with nested children
        """
        if not self.category_processor:
            return []

        def node_to_dict(node) -> Dict[str, Any]:
            return {
                "id": node.category.id,
                "name": node.category.name,
                "description": node.category.description,
                "memory_count": node.category.memory_count,
                "strength": node.category.strength,
                "depth": node.depth,
                "children": [node_to_dict(child) for child in node.children],
            }

        tree_nodes = self.category_processor.get_category_tree()
        return [node_to_dict(node) for node in tree_nodes]

    def apply_category_decay(self) -> Dict[str, Any]:
        """
        Apply decay to categories

        Unused categories weaken and may merge with similar ones.

        Returns:
            Stats about decayed/merged/deleted categories
        """
        if not self.category_processor or not self.category_config.enable_category_decay:
            return {"decayed": 0, "merged": 0, "deleted": 0}

        result = self.category_processor.apply_category_decay(
            decay_rate=self.category_config.category_decay_rate
        )

        self._persist_categories()
        return result

    def get_category_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the category layer.

        Returns:
            Category statistics
        """
        if not self.category_processor:
            return {"enabled": False}

        stats = self.category_processor.get_category_stats()
        stats["enabled"] = True
        stats["config"] = {
            "auto_categorize": self.category_config.auto_categorize,
            "enable_decay": self.category_config.enable_category_decay,
            "boost_weight": self.category_config.category_boost_weight,
        }
        return stats

    def search_by_category(
        self,
        category_id: str,
        limit: int = 50,
        min_strength: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Get memories in a specific category.

        Args:
            category_id: Category ID
            limit: Maximum results
            min_strength: Minimum memory strength

        Returns:
            Dict with results list
        """
        if not self.category_processor:
            return {"results": [], "category": None}

        cat = self.category_processor.get_category(category_id)
        if not cat:
            return {"results": [], "category": None, "error": "Category not found"}

        # Record access
        self.category_processor.access_category(category_id)

        memories = self.db.get_memories_by_category(
            category_id, limit=limit, min_strength=min_strength
        )

        self._persist_categories()

        return {
            "results": memories,
            "category": cat.to_dict(),
            "total": len(memories),
        }

    # =========================================================================
    # Knowledge Graph Methods
    # =========================================================================

    def get_related_memories(
        self,
        memory_id: str,
        max_depth: int = 2,
    ) -> Dict[str, Any]:
        """
        Get memories related to a given memory via the knowledge graph.

        Args:
            memory_id: Starting memory ID
            max_depth: Maximum graph traversal depth

        Returns:
            Dict with related memories and relationship paths
        """
        if not self.knowledge_graph:
            return {"results": [], "graph_enabled": False}

        related = self.knowledge_graph.get_related_memories(
            memory_id=memory_id,
            max_depth=max_depth,
        )

        results = []
        for other_id, depth, path in related:
            memory = self.db.get_memory(other_id)
            if memory:
                results.append({
                    "id": other_id,
                    "memory": memory.get("memory", ""),
                    "depth": depth,
                    "path": [
                        {
                            "type": r.relation_type.value,
                            "entity": r.entity,
                            "weight": r.weight,
                        }
                        for r in path
                    ],
                })

        return {"results": results, "total": len(results)}

    def get_memory_entities(self, memory_id: str) -> Dict[str, Any]:
        """
        Get entities extracted from a specific memory.

        Args:
            memory_id: Memory ID

        Returns:
            Dict with entity information
        """
        if not self.knowledge_graph:
            return {"entities": [], "graph_enabled": False}

        entity_names = self.knowledge_graph.memory_entities.get(memory_id, set())
        entities = []
        for name in entity_names:
            entity = self.knowledge_graph.entities.get(name)
            if entity:
                entities.append(entity.to_dict())

        return {"entities": entities, "total": len(entities)}

    def get_entity_memories(self, entity_name: str) -> Dict[str, Any]:
        """
        Get all memories containing a specific entity.

        Args:
            entity_name: Entity name to search for

        Returns:
            Dict with memories containing the entity
        """
        if not self.knowledge_graph:
            return {"results": [], "graph_enabled": False}

        memory_ids = self.knowledge_graph.get_entity_memories(entity_name)
        results = []
        for memory_id in memory_ids:
            memory = self.db.get_memory(memory_id)
            if memory:
                results.append({
                    "id": memory_id,
                    "memory": memory.get("memory", ""),
                    "strength": memory.get("strength", 1.0),
                    "layer": memory.get("layer", "sml"),
                })

        return {"results": results, "entity": entity_name, "total": len(results)}

    def get_memory_graph(self, memory_id: str) -> Dict[str, Any]:
        """
        Get graph visualization data centered on a memory.

        Args:
            memory_id: Center memory ID

        Returns:
            Dict with nodes and edges for visualization
        """
        if not self.knowledge_graph:
            return {"nodes": [], "edges": [], "graph_enabled": False}

        return self.knowledge_graph.get_memory_graph(memory_id)

    def get_graph_stats(self) -> Dict[str, Any]:
        """
        Get knowledge graph statistics.

        Returns:
            Dict with graph statistics
        """
        if not self.knowledge_graph:
            return {"enabled": False}

        stats = self.knowledge_graph.stats()
        stats["enabled"] = True
        return stats

    # =========================================================================
    # Scene Methods
    # =========================================================================

    def _assign_to_scene(
        self,
        memory_id: str,
        content: str,
        embedding: Optional[List[float]],
        user_id: Optional[str],
        timestamp: str,
    ) -> None:
        """Assign a memory to an existing or new scene."""
        if not self.scene_processor or not user_id:
            return

        # Auto-close stale scenes first
        self.scene_processor.auto_close_stale(user_id)

        current_scene = self.db.get_open_scene(user_id)
        memory_row = self.db.get_memory(memory_id) or {}
        namespace = str(memory_row.get("namespace", "default") or "default").strip() or "default"
        if (
            current_scene
            and str(current_scene.get("namespace", "default") or "default").strip() != namespace
        ):
            detection = self.scene_processor.detect_boundary(
                content=content,
                timestamp=timestamp,
                current_scene=None,
                embedding=embedding,
            )
        else:
            detection = self.scene_processor.detect_boundary(
                content=content,
                timestamp=timestamp,
                current_scene=current_scene,
                embedding=embedding,
            )

        if detection.is_new_scene:
            # Close old scene if open
            if current_scene:
                self.scene_processor.close_scene(current_scene["id"], timestamp)

            # Detect topic from content (first 60 chars as fallback)
            topic = content[:60].strip()
            location = detection.detected_location

            self.scene_processor.create_scene(
                first_memory_id=memory_id,
                user_id=user_id,
                timestamp=timestamp,
                topic=topic,
                location=location,
                embedding=embedding,
                namespace=namespace,
            )
        else:
            if current_scene:
                self.scene_processor.add_memory_to_scene(
                    scene_id=current_scene["id"],
                    memory_id=memory_id,
                    embedding=embedding,
                    timestamp=timestamp,
                    namespace=namespace,
                )

    def _update_profiles(
        self,
        memory_id: str,
        content: str,
        metadata: Dict[str, Any],
        user_id: Optional[str],
    ) -> None:
        """Extract and apply profile updates from memory content."""
        if not self.profile_processor or not user_id:
            return

        updates = self.profile_processor.extract_profile_mentions(
            content=content,
            metadata=metadata,
            user_id=user_id,
        )

        for update in updates:
            self.profile_processor.apply_update(
                profile_update=update,
                memory_id=memory_id,
                user_id=user_id,
            )

    def get_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific scene by ID."""
        return self.db.get_scene(scene_id)

    def get_scenes(
        self,
        user_id: Optional[str] = None,
        topic: Optional[str] = None,
        start_after: Optional[str] = None,
        start_before: Optional[str] = None,
        namespace: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List scenes chronologically."""
        return self.db.get_scenes(
            user_id=user_id,
            topic=topic,
            start_after=start_after,
            start_before=start_before,
            namespace=namespace,
            limit=limit,
        )

    def search_scenes(self, query: str, user_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Semantic search over scene summaries."""
        if not self.scene_processor:
            return []
        return self.scene_processor.search_scenes(query=query, user_id=user_id, limit=limit)

    def get_scene_timeline(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get scenes in chronological order."""
        if not self.scene_processor:
            return []
        return self.scene_processor.get_scene_timeline(user_id=user_id, limit=limit)

    def get_scene_memories(self, scene_id: str) -> List[Dict[str, Any]]:
        """Get all memories in a scene."""
        return self.db.get_scene_memories(scene_id)

    # =========================================================================
    # Profile Methods
    # =========================================================================

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Get a character profile by ID."""
        return self.db.get_profile(profile_id)

    def get_all_profiles(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all profiles for a user."""
        return self.db.get_all_profiles(user_id=user_id)

    def get_self_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the self-profile for a user."""
        return self.db.get_profile_by_name("self", user_id=user_id)

    def search_profiles(self, query: str, user_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Search profiles by name or description."""
        if not self.profile_processor:
            return []
        return self.profile_processor.search_profiles(query=query, user_id=user_id, limit=limit)

    def update_profile(self, profile_id: str, updates: Dict[str, Any]) -> bool:
        """Update a profile."""
        return self.db.update_profile(profile_id, updates)

    def get_profile_memories(self, profile_id: str) -> List[Dict[str, Any]]:
        """Get memories linked to a profile."""
        return self.db.get_profile_memories(profile_id)

    # =========================================================================
    # Dashboard / Visualization Methods
    # =========================================================================

    def get_constellation_data(self, user_id: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        """Get graph nodes + edges for the constellation force layout."""
        return self.db.get_constellation_data(user_id=user_id, limit=limit)

    def get_decay_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent decay history for dashboard sparkline."""
        return self.db.get_decay_log_entries(limit=limit)
