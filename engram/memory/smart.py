"""SmartMemory — bio-inspired memory: decay + echo + categories + knowledge graph.

Extends CoreMemory with LLM-powered features: echo encoding for stronger
retention, dynamic category organization, and knowledge graph entity linking.
Requires an LLM provider (Gemini, OpenAI, Ollama) for full functionality.

Processors are lazily initialized — only created on first use.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from engram.configs.base import MemoryConfig
from engram.memory.core import CoreMemory, _content_hash
from engram.utils.factory import LLMFactory

logger = logging.getLogger(__name__)


class SmartMemory(CoreMemory):
    """Bio-inspired memory: decay + echo + categories + knowledge graph.

    Usage:
        m = SmartMemory(preset="smart")
        m.add("I like Python", echo_depth="medium")
        results = m.search("programming preferences")
    """

    def __init__(
        self,
        config: Optional[MemoryConfig] = None,
        preset: Optional[str] = None,
    ):
        if config is None and preset is None:
            config = MemoryConfig.smart()
        super().__init__(config=config, preset=preset)

        self.echo_config = self.config.echo
        self.category_config = self.config.category
        self.graph_config = self.config.graph
        self.scope_config = getattr(self.config, "scope", None)

        # LLM — created eagerly since echo/category need it
        self.llm = LLMFactory.create(self.config.llm.provider, self.config.llm.config)

        self.skill_config = getattr(self.config, "skill", None)

        # Lazy-init processors (only created on first use)
        self._echo_processor = None
        self._category_processor = None
        self._knowledge_graph = None
        self._skill_store = None
        self._skill_executor = None

    @property
    def echo_processor(self):
        if self._echo_processor is None and self.echo_config.enable_echo:
            from engram.core.echo import EchoProcessor
            self._echo_processor = EchoProcessor(
                self.llm,
                config={
                    "auto_depth": self.echo_config.auto_depth,
                    "default_depth": self.echo_config.default_depth,
                },
            )
        return self._echo_processor

    @property
    def category_processor(self):
        if self._category_processor is None and self.category_config.enable_categories:
            from engram.core.category import CategoryProcessor
            self._category_processor = CategoryProcessor(
                llm=self.llm,
                embedder=self.embedder,
                config={
                    "use_llm": self.category_config.use_llm_categorization,
                    "auto_subcategories": self.category_config.auto_create_subcategories,
                    "max_depth": self.category_config.max_category_depth,
                },
            )
            # Load existing categories from DB
            existing = self.db.get_all_categories()
            if existing:
                self._category_processor.load_categories(existing)
        return self._category_processor

    @property
    def knowledge_graph(self):
        if self._knowledge_graph is None and self.graph_config.enable_graph:
            from engram.core.graph import KnowledgeGraph
            self._knowledge_graph = KnowledgeGraph(
                llm=self.llm if self.graph_config.use_llm_extraction else None
            )
        return self._knowledge_graph

    @property
    def skill_store(self):
        if self._skill_store is None and self.skill_config and self.skill_config.enable_skills:
            from engram.skills.discovery import discover_skill_dirs
            from engram.skills.store import SkillStore
            skill_dirs = discover_skill_dirs()
            self._skill_store = SkillStore(
                skill_dirs=skill_dirs,
                embedder=self.embedder,
                vector_store=None,  # Skills use text search in SmartMemory (no separate collection)
                collection_name=self.skill_config.skill_collection_name,
            )
            self._skill_store.sync_from_filesystem()
        return self._skill_store

    @property
    def skill_executor(self):
        if self._skill_executor is None and self.skill_store is not None:
            from engram.skills.executor import SkillExecutor
            self._skill_executor = SkillExecutor(self.skill_store)
        return self._skill_executor

    def search_skills(
        self,
        query: str,
        limit: int = 5,
        tags: Optional[List[str]] = None,
        min_confidence: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Search for skills by semantic query."""
        if self.skill_executor is None:
            return []
        return self.skill_executor.search(
            query=query, limit=limit, tags=tags, min_confidence=min_confidence,
        )

    def apply_skill(
        self,
        skill_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Apply a skill by ID, returning the recipe for injection."""
        if self.skill_executor is None:
            return {"error": "Skills not enabled", "injected": False}
        return self.skill_executor.apply(skill_id, context)

    def log_skill_outcome(
        self,
        skill_id: str,
        success: bool,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Log success/failure for a skill and update its confidence."""
        if self.skill_store is None:
            return {"error": "Skills not enabled"}
        from engram.skills.outcomes import OutcomeTracker
        tracker = OutcomeTracker(self.skill_store)
        return tracker.log_outcome(skill_id, success, notes)

    def add(
        self,
        content: str,
        user_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
        source_app: Optional[str] = None,
        echo_depth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add with echo encoding and category detection."""
        content = str(content).strip()
        if not content:
            return {"results": []}

        user_id = user_id or "default"
        metadata = dict(metadata or {})
        categories = list(categories or [])

        # Content-hash dedup (inherited from CoreMemory logic)
        ch = _content_hash(content)
        existing = self.db.get_memory_by_content_hash(ch, user_id)
        if existing:
            from engram.core.traces import boost_fast_trace
            self.db.increment_access(existing["id"])
            if self.distillation_config and self.distillation_config.enable_multi_trace:
                s_fast = existing.get("s_fast") or 0.0
                boosted = boost_fast_trace(s_fast, self.fadem_config.access_strength_boost)
                self.db.update_memory(existing["id"], {"s_fast": boosted})
            return {
                "results": [{
                    "id": existing["id"],
                    "memory": existing.get("memory", ""),
                    "event": "DEDUPLICATED",
                    "layer": existing.get("layer", "sml"),
                    "strength": existing.get("strength", 1.0),
                }]
            }

        # Echo encoding
        echo_result = None
        initial_strength = 1.0
        effective_strength = initial_strength
        if self.echo_processor and self.echo_config.enable_echo:
            try:
                from engram.core.echo import EchoDepth
                depth_override = EchoDepth(echo_depth) if echo_depth else None
                echo_result = self.echo_processor.process(content, depth=depth_override)
                effective_strength = initial_strength * echo_result.strength_multiplier
                metadata.update(echo_result.to_metadata())
                if not categories and echo_result.category:
                    categories = [echo_result.category]
            except Exception as e:
                logger.warning("Echo encoding failed: %s", e)

        # Category detection
        if self.category_processor and self.category_config.auto_categorize and not categories:
            try:
                cat_match = self.category_processor.detect_category(
                    content,
                    metadata=metadata,
                    use_llm=self.category_config.use_llm_categorization,
                )
                categories = [cat_match.category_id]
                metadata["category_confidence"] = cat_match.confidence
                metadata["category_auto"] = True
            except Exception as e:
                logger.warning("Category detection failed: %s", e)

        # Use echo's question_form for embedding if available
        primary_text = content
        if (
            echo_result
            and self.echo_config.use_question_embedding
            and hasattr(echo_result, "question_form")
            and echo_result.question_form
        ):
            primary_text = echo_result.question_form

        embedding = self.embedder.embed(primary_text, memory_action="add")

        # Knowledge graph entity extraction
        if self.knowledge_graph:
            try:
                self.knowledge_graph.extract_entities(content, metadata=metadata)
            except Exception as e:
                logger.warning("Entity extraction failed: %s", e)

        # Store via parent's DB logic, but with our enhanced data
        from engram.core.traces import initialize_traces
        import uuid
        from datetime import datetime, timezone

        memory_type = metadata.get("memory_type", "semantic")
        s_fast_val = s_mid_val = s_slow_val = None
        if self.distillation_config and self.distillation_config.enable_multi_trace:
            s_fast_val, s_mid_val, s_slow_val = initialize_traces(effective_strength, is_new=True)

        now = datetime.now(timezone.utc).isoformat()
        memory_id = str(uuid.uuid4())
        namespace = str(metadata.get("namespace", "default") or "default").strip() or "default"

        memory_data = {
            "id": memory_id,
            "memory": content,
            "user_id": user_id,
            "agent_id": agent_id,
            "metadata": metadata,
            "categories": categories,
            "created_at": now,
            "updated_at": now,
            "layer": "sml",
            "strength": effective_strength,
            "access_count": 0,
            "last_accessed": now,
            "embedding": embedding,
            "confidentiality_scope": metadata.get("confidentiality_scope", "work"),
            "source_type": "mcp",
            "source_app": source_app,
            "decay_lambda": self.fadem_config.sml_decay_rate,
            "status": "active",
            "importance": metadata.get("importance", 0.5),
            "sensitivity": metadata.get("sensitivity", "normal"),
            "namespace": namespace,
            "memory_type": memory_type,
            "s_fast": s_fast_val,
            "s_mid": s_mid_val,
            "s_slow": s_slow_val,
            "content_hash": ch,
        }

        self.db.add_memory(memory_data)

        # Vector store
        payload = {"memory_id": memory_id, "user_id": user_id, "memory": content}
        if agent_id:
            payload["agent_id"] = agent_id
        try:
            self.vector_store.insert(
                vectors=[embedding], payloads=[payload], ids=[memory_id]
            )
        except Exception as e:
            logger.warning("Vector insert failed: %s", e)

        # Persist categories
        if self.category_processor and categories:
            try:
                for cat_id in categories:
                    self.category_processor.update_category_stats(
                        cat_id, effective_strength, is_addition=True
                    )
                self._persist_categories()
            except Exception as e:
                logger.warning("Category persistence failed: %s", e)

        return {
            "results": [{
                "id": memory_id,
                "memory": content,
                "event": "ADD",
                "layer": "sml",
                "strength": effective_strength,
                "categories": categories,
                "namespace": namespace,
                "memory_type": memory_type,
                "echo_depth": echo_result.echo_depth.value if echo_result else None,
            }]
        }

    def search(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 10,
        agent_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
        use_echo_boost: bool = True,
        use_category_boost: bool = True,
    ) -> Dict[str, Any]:
        """Search with echo reranking and category boosting."""
        # Get base results from CoreMemory
        result = super().search(
            query=query,
            user_id=user_id,
            limit=limit * 2 if (use_echo_boost or use_category_boost) else limit,
            agent_id=agent_id,
            categories=categories,
        )

        if not use_echo_boost and not use_category_boost:
            return result

        memories = result.get("results", [])

        # Apply echo boost
        if use_echo_boost and self.echo_config.enable_echo:
            for mem in memories:
                full_mem = self.db.get_memory(mem["id"])
                if not full_mem:
                    continue
                md = full_mem.get("metadata", {})
                if isinstance(md, str):
                    import json
                    try:
                        md = json.loads(md)
                    except (json.JSONDecodeError, TypeError):
                        md = {}
                echo_depth = md.get("echo_depth")
                if echo_depth:
                    multiplier = {
                        "shallow": self.echo_config.shallow_multiplier,
                        "medium": self.echo_config.medium_multiplier,
                        "deep": self.echo_config.deep_multiplier,
                    }.get(echo_depth, 1.0)
                    mem["composite_score"] = mem.get("composite_score", mem.get("score", 0)) * (0.9 + 0.1 * multiplier)
                    mem["score"] = mem["composite_score"]

        # Apply category boost
        if use_category_boost and self.category_processor and categories:
            for mem in memories:
                mem_cats = mem.get("categories", [])
                if isinstance(mem_cats, str):
                    import json
                    try:
                        mem_cats = json.loads(mem_cats)
                    except (json.JSONDecodeError, TypeError):
                        mem_cats = []
                if any(c in mem_cats for c in categories):
                    mem["composite_score"] = mem.get("composite_score", mem.get("score", 0)) * (1.0 + self.category_config.category_boost_weight)
                    mem["score"] = mem["composite_score"]

        # Re-rank
        memories.sort(key=lambda r: r.get("composite_score", r.get("score", 0)), reverse=True)
        return {"results": memories[:limit]}

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all categories."""
        if self.category_processor:
            return self.category_processor.get_all_categories()
        return []

    def _persist_categories(self):
        """Persist category state to DB."""
        if not self.category_processor:
            return
        try:
            categories = self.category_processor.export_categories()
            for cat in categories:
                self.db.upsert_category(cat)
        except Exception as e:
            logger.warning("Failed to persist categories: %s", e)
