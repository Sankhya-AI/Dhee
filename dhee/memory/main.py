from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, date, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from dhee.configs.base import MemoryConfig
from dhee.core.decay import calculate_decayed_strength, should_forget, should_promote
from dhee.core.conflict import resolve_conflict
from dhee.core.distillation import ReplayDistiller
from dhee.core.echo import EchoProcessor, EchoDepth, EchoResult
from dhee.core.forgetting import HomeostaticNormalizer, InterferencePruner, RedundancyCollapser
from dhee.core.fusion import fuse_memories
from dhee.core.intent import QueryIntent, classify_intent
from dhee.core.retrieval import composite_score, tokenize, HybridSearcher
from dhee.core.traces import (
    boost_fast_trace,
    cascade_traces,
    compute_effective_strength,
    decay_traces,
    initialize_traces,
)
from dhee.core.category import CategoryProcessor, CategoryMatch
from dhee.core.graph import KnowledgeGraph
from dhee.core.scene import SceneProcessor
from dhee.core.profile import ProfileProcessor
from dhee.core.answer_orchestration import (
    AnswerIntent,
    build_map_candidates,
    build_query_plan,
    deterministic_inconsistency_check,
    extract_atomic_facts,
    is_low_confidence_answer,
    reduce_atomic_facts,
    render_fact_context,
    should_override_with_reducer,
)
from dhee.core.episodic_index import (
    extract_entity_aggregates,
    extract_episodic_events,
    intent_event_types,
    normalize_actor_id,
    score_event_match,
    tokenize_query_terms,
)
from dhee.db.sqlite import SQLiteManager
from dhee.exceptions import FadeMemValidationError
from dhee.memory.base import MemoryBase
from dhee.memory.utils import (
    build_filters_and_metadata,
    matches_filters,
    normalize_categories,
    normalize_messages,
    parse_messages,
    strip_code_fences,
)
from dhee.memory.parallel import ParallelExecutor
from dhee.memory.smart import SmartMemory
from dhee.observability import metrics
from dhee.utils.factory import EmbedderFactory, LLMFactory, VectorStoreFactory
from dhee.utils.prompts import AGENT_MEMORY_EXTRACTION_PROMPT, MEMORY_EXTRACTION_PROMPT

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
    r"\b(bank account|account number|routing number|iban|swift code|credit card|debit card|cvv|salary|income|mortgage|loan amount|tax id|tax return)\b",
    re.IGNORECASE,
)
_EPHEMERAL_HINT_RE = re.compile(
    r"\b(remind me|pick up|todo|to-do)\b"
    r"|\b(today|tomorrow|tonight)\s+(?:i\s|we\s|I\s)"
    r"|(?:this|next)\s+(?:morning|afternoon|evening|week)\b"
    r"|\bin\s+\d+\s*(?:minutes|hours|days)\b",
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
_TEMPORAL_RECENT_QUERY_RE = re.compile(
    r"\b(latest|most recent|currently|current|as of|recent|newest|last)\b",
    re.IGNORECASE,
)
_TEMPORAL_RANGE_QUERY_RE = re.compile(
    r"\b(past|in the past|within the last|last month|last week|last year)\b",
    re.IGNORECASE,
)
_TEMPORAL_TRANSACTIONAL_QUERY_RE = re.compile(
    r"\b(spent|spend|cost|price|payment|paid|bought|purchase|transaction|grocery|amount|money|dollars?|usd)\b",
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
    # Content explicitly provided by user (infer=False) is always high confidence.
    if metadata.get("user_provided"):
        return True
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


class FullMemory(SmartMemory):
    """Full-featured engram Memory class with scenes, profiles, tasks, projects.

    Extends SmartMemory with additional FullMemory-specific features:
    - SceneProcessor for episodic memory grouping
    - ProfileProcessor for character/entity profiles
    - Task and project management (future)

    All base features (echo encoding, categories, knowledge graph) are inherited
    from SmartMemory with lazy initialization via @property.
    """

    def __init__(self, config: Optional[MemoryConfig] = None, preset: Optional[str] = None):
        # Use default full() config if neither config nor preset provided
        if config is None and preset is None:
            config = MemoryConfig.full()
        # Initialize parent SmartMemory (handles db, llm, embedder, etc.)
        super().__init__(config=config, preset=preset)
        # Only FullMemory-specific lazy init
        self._scene_processor: Optional[SceneProcessor] = None
        self._profile_processor: Optional[ProfileProcessor] = None
        self._task_manager: Optional[Any] = None
        self._project_manager: Optional[Any] = None
        # Neural reranker (lazy init)
        self._reranker: Optional[Any] = None
        # Trajectory recording and skill mining
        self._trajectory_store: Optional[Any] = None
        self._skill_miner: Optional[Any] = None
        self._active_recorders: Dict[str, Any] = {}
        self._guardrail_auto_disabled = False
        self._reducer_cache: Dict[str, Dict[str, Any]] = {}
        # Parallel executor (lazy: created only when config enables it)
        self._executor: Optional[ParallelExecutor] = None
        if self.config.parallel.enable_parallel:
            self._executor = ParallelExecutor(max_workers=self.config.parallel.max_workers)
        # Dhee: Universal Engram extraction + context-first resolver + cognition
        self._engram_extractor: Optional[Any] = None
        self._context_resolver: Optional[Any] = None
        self._cognition_engine: Optional[Any] = None
        # Dhee: Self-evolution layer (samskara + viveka + alaya + nididhyasana)
        self._evolution_layer: Optional[Any] = None
        # Dhee: Buddhi — proactive cognition (HyperAgent layer)
        self._buddhi_layer: Optional[Any] = None

    @property
    def scene_processor(self) -> Optional[SceneProcessor]:
        """Lazy-initialized SceneProcessor (only if scenes enabled in config)."""
        if self._scene_processor is None and self.config.scene.enable_scenes:
            self._scene_processor = SceneProcessor(
                db=self.db,
                embedder=self.embedder,
                llm=self.llm,
                config={
                    "scene_time_gap_minutes": self.config.scene.scene_time_gap_minutes,
                    "scene_topic_threshold": self.config.scene.scene_topic_threshold,
                    "auto_close_inactive_minutes": self.config.scene.auto_close_inactive_minutes,
                    "max_scene_memories": self.config.scene.max_scene_memories,
                    "use_llm_summarization": self.config.scene.use_llm_summarization,
                    "summary_regenerate_threshold": self.config.scene.summary_regenerate_threshold,
                },
            )
        return self._scene_processor

    @property
    def profile_processor(self) -> Optional[ProfileProcessor]:
        """Lazy-initialized ProfileProcessor (only if profiles enabled in config)."""
        if self._profile_processor is None and self.config.profile.enable_profiles:
            self._profile_processor = ProfileProcessor(
                db=self.db,
                embedder=self.embedder,
                llm=self.llm,
                config={
                    "auto_detect_profiles": self.config.profile.auto_detect_profiles,
                    "use_llm_extraction": self.config.profile.use_llm_extraction,
                    "narrative_regenerate_threshold": self.config.profile.narrative_regenerate_threshold,
                    "self_profile_auto_create": self.config.profile.self_profile_auto_create,
                    "max_facts_per_profile": self.config.profile.max_facts_per_profile,
                },
            )
        return self._profile_processor

    @property
    def engram_extractor(self):
        """Lazy-initialized EngramExtractor for structured memory extraction."""
        if self._engram_extractor is None and self.config.engram_extraction.enable_extraction:
            from dhee.core.engram_extractor import EngramExtractor
            llm = None
            if self.config.engram_extraction.use_llm_extraction:
                # Create a SEPARATE LLM instance for extraction with shorter timeout.
                # The main LLM (120s timeout, 3 retries) blocks too long when rate-limited.
                # Extraction must fail fast and fall back to rule-based extraction.
                from dhee.utils.factory import LLMFactory
                extraction_llm_config = dict(self.config.llm.config)
                extraction_llm_config["timeout"] = 30          # 30s vs 120s default
                extraction_llm_config["app_retries"] = 1       # 1 retry vs 3 default
                extraction_llm_config["max_retries"] = 0       # no OpenAI client retries
                extraction_llm_config["max_tokens"] = 2048     # extraction output is smaller
                try:
                    llm = LLMFactory.create(self.config.llm.provider, extraction_llm_config)
                    logger.info("EngramExtractor: created dedicated LLM (timeout=30s, retries=1)")
                except Exception as e:
                    logger.warning("EngramExtractor: failed to create dedicated LLM, using main: %s", e)
                    llm = self.llm
            self._engram_extractor = EngramExtractor(llm=llm)
        return self._engram_extractor

    @property
    def context_resolver(self):
        """Lazy-initialized ContextResolver for deterministic fact resolution."""
        if self._context_resolver is None:
            from dhee.core.resolvers import ContextResolver
            self._context_resolver = ContextResolver(db=self.db)
        return self._context_resolver

    @property
    def cognition_engine(self):
        """Lazy-initialized CognitionEngine for memory-grounded reasoning."""
        if self._cognition_engine is None and self.config.cognition.enable_cognition:
            from dhee.core.cognition import CognitionEngine
            self._cognition_engine = CognitionEngine(
                memory=self,
                external_llm=self.llm,
                max_depth=self.config.cognition.max_depth,
                max_sub_questions=self.config.cognition.max_sub_questions,
                store_solutions=self.config.cognition.store_solutions,
            )
        return self._cognition_engine

    @property
    def evolution_layer(self):
        """Lazy-initialized self-evolution layer (samskara + viveka + alaya)."""
        if self._evolution_layer is None:
            try:
                from dhee.core.evolution import EvolutionLayer
                self._evolution_layer = EvolutionLayer()
            except Exception as e:
                logger.debug("Evolution layer init skipped: %s", e)
        return self._evolution_layer

    @property
    def buddhi_layer(self):
        """Lazy-initialized Buddhi — proactive cognition layer (HyperAgent)."""
        if self._buddhi_layer is None:
            try:
                from dhee.core.buddhi import Buddhi
                self._buddhi_layer = Buddhi()
            except Exception as e:
                logger.debug("Buddhi layer init skipped: %s", e)
        return self._buddhi_layer

    @property
    def trajectory_store(self):
        """Lazy-initialized TrajectoryStore for persisting agent trajectories."""
        if self._trajectory_store is None:
            from dhee.skills.trajectory import TrajectoryStore
            self._trajectory_store = TrajectoryStore(
                db=self.db,
                embedder=self.embedder,
                vector_store=self.vector_store,
            )
        return self._trajectory_store

    @property
    def skill_miner(self):
        """Lazy-initialized SkillMiner for extracting skills from trajectories."""
        skill_cfg = getattr(self.config, "skill", None)
        if self._skill_miner is None and skill_cfg and skill_cfg.enable_mining:
            from dhee.skills.miner import SkillMiner
            self._skill_miner = SkillMiner(
                trajectory_store=self.trajectory_store,
                skill_store=self.skill_store,
                llm=self.llm,
                embedder=self.embedder,
                mutation_rate=skill_cfg.mutation_rate,
            )
        return self._skill_miner

    @property
    def reranker(self):
        """Lazy-initialized neural reranker (only if enabled in config)."""
        rerank_cfg = getattr(self.config, "rerank", None)
        if self._reranker is None and rerank_cfg and rerank_cfg.enable_rerank:
            from dhee.retrieval.reranker import create_reranker
            self._reranker = create_reranker({
                "provider": rerank_cfg.provider,
                "model": rerank_cfg.model,
                "api_key_env": rerank_cfg.api_key_env,
                **rerank_cfg.config,
            })
        return self._reranker

    def start_trajectory(
        self,
        task_description: str,
        user_id: str = "default",
        agent_id: str = "default",
    ) -> str:
        """Start recording a new trajectory for the given task.

        Returns the recorder ID to be used with record_trajectory_step()
        and complete_trajectory().
        """
        from dhee.skills.trajectory import TrajectoryRecorder
        recorder = TrajectoryRecorder(
            task_description=task_description,
            user_id=user_id,
            agent_id=agent_id,
        )
        self._active_recorders[recorder.id] = recorder
        return recorder.id

    def record_trajectory_step(
        self,
        recorder_id: str,
        action: str,
        tool: str = "",
        args: Optional[Dict[str, Any]] = None,
        result_summary: str = "",
        error: Optional[str] = None,
        slot_values: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Record a step in an active trajectory.

        If slot_values are provided, they are stored in state_snapshot
        for later structural mining.
        """
        recorder = self._active_recorders.get(recorder_id)
        if recorder is None:
            return {"error": f"No active recorder: {recorder_id}"}

        state_snapshot = None
        if slot_values:
            state_snapshot = {"slot_values": slot_values}

        step = recorder.record_step(
            action=action,
            tool=tool,
            args=args,
            result_summary=result_summary,
            error=error,
            state_snapshot=state_snapshot,
        )
        return {
            "recorder_id": recorder_id,
            "step_count": len(recorder.steps),
            "action": action,
            "tool": tool,
        }

    def complete_trajectory(
        self,
        recorder_id: str,
        success: bool,
        outcome_summary: str = "",
    ) -> Dict[str, Any]:
        """Finalize a trajectory recording and persist it.

        Returns the trajectory data.
        """
        recorder = self._active_recorders.pop(recorder_id, None)
        if recorder is None:
            return {"error": f"No active recorder: {recorder_id}"}

        trajectory = recorder.finalize(
            success=success,
            outcome_summary=outcome_summary,
        )
        self.trajectory_store.save(trajectory)

        return {
            "trajectory_id": trajectory.id,
            "task_description": trajectory.task_description,
            "step_count": len(trajectory.steps),
            "success": success,
            "outcome_summary": outcome_summary,
            "trajectory_hash": trajectory.trajectory_hash_val,
        }

    def mine_skills(
        self,
        task_query: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a skill mining cycle.

        Analyzes successful trajectories and extracts reusable skills.
        Returns info about mined skills.
        """
        if self.skill_miner is None:
            return {"error": "Skill mining not enabled", "skills_mined": 0}

        mined = self.skill_miner.mine(
            task_query=task_query,
            user_id=user_id,
        )
        return {
            "skills_mined": len(mined),
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "confidence": s.confidence,
                    "source": s.source,
                    "tags": s.tags,
                }
                for s in mined
            ],
        }

    def get_skill_stats(self) -> Dict[str, Any]:
        """Get statistics about skills and trajectories."""
        skills = self.skill_store.list_all() if self.skill_store else []
        trajectories = self.trajectory_store.find_successful(limit=1000) if self._trajectory_store else []

        total_skills = len(skills)
        authored = sum(1 for s in skills if s.source == "authored")
        mined = sum(1 for s in skills if s.source == "mined")
        imported = sum(1 for s in skills if s.source == "imported")
        avg_confidence = sum(s.confidence for s in skills) / max(1, total_skills)

        return {
            "total_skills": total_skills,
            "authored_skills": authored,
            "mined_skills": mined,
            "imported_skills": imported,
            "avg_confidence": round(avg_confidence, 4),
            "total_successful_trajectories": len(trajectories),
            "active_recorders": len(self._active_recorders),
        }

    def close(self) -> None:
        """Release all resources held by the Memory instance."""
        # Flush self-evolution state before shutdown
        if self._evolution_layer is not None:
            try:
                self._evolution_layer.flush()
            except Exception:
                pass
        # Shutdown parallel executor if it was created
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None
        # Release vector store
        if self.vector_store is not None:
            self.vector_store.close()
        # Release database
        if self.db is not None:
            self.db.close()

    def __repr__(self) -> str:
        return f"FullMemory(db={self.db!r}, echo={self.config.echo.enable_echo}, scenes={self.config.scene.enable_scenes})"

    # _cached_embed inherited from SmartMemory

    # from_config inherited from SmartMemory

    def _record_cost_counter(
        self,
        *,
        phase: str,
        user_id: Optional[str],
        llm_calls: float = 0.0,
        input_tokens: float = 0.0,
        output_tokens: float = 0.0,
        embed_calls: float = 0.0,
    ) -> None:
        cost_cfg = getattr(self.config, "cost_guardrail", None)
        if not cost_cfg or not cost_cfg.enable_cost_counters:
            return
        try:
            self.db.record_cost_counter(
                phase=phase,
                user_id=user_id,
                llm_calls=llm_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                embed_calls=embed_calls,
            )
            if str(phase) == "write":
                self._enforce_write_cost_guardrail(user_id=user_id)
        except Exception as e:
            logger.debug("Cost counter record failed: %s", e)

    @staticmethod
    def _estimate_token_count(value: Any) -> float:
        """Lightweight token estimate for guardrail telemetry."""
        if value is None:
            return 0.0
        if not isinstance(value, str):
            try:
                value = json.dumps(value, default=str)
            except Exception:
                value = str(value)
        text = str(value or "").strip()
        if not text:
            return 0.0
        # Rough English token estimate; good enough for trend guardrails.
        return float(max(1, math.ceil(len(text) / 4.0)))

    @staticmethod
    def _estimate_output_tokens(input_tokens: float) -> float:
        base = max(0.0, float(input_tokens or 0.0))
        return float(max(8, math.ceil(base * 0.3)))

    def _intent_coverage_threshold(self, intent_value: str, fallback: float) -> float:
        orch_cfg = getattr(self.config, "orchestration", None)
        thresholds = getattr(orch_cfg, "intent_coverage_thresholds", {}) or {}
        key = str(intent_value or "freeform").strip().lower()
        value = thresholds.get(key, fallback)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return max(0.0, min(1.0, float(fallback)))

    @staticmethod
    def _stable_hash_text(text: str) -> str:
        return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()

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
                self._stable_hash_text(query),
                self._stable_hash_text(evidence_fingerprint),
            ]
        )
        return self._stable_hash_text(base)

    def _get_reducer_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        orch_cfg = getattr(self.config, "orchestration", None)
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
        orch_cfg = getattr(self.config, "orchestration", None)
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

    def _enforce_write_cost_guardrail(self, *, user_id: Optional[str]) -> None:
        cost_cfg = getattr(self.config, "cost_guardrail", None)
        orch_cfg = getattr(self.config, "orchestration", None)
        if not cost_cfg or not cost_cfg.strict_write_path_cap or not orch_cfg:
            return

        # Baseline values default to 0.0; treat that as "not configured" to avoid
        # accidental auto-disable on fresh installs.
        base_calls = float(getattr(cost_cfg, "baseline_write_llm_calls_per_memory", 0.0) or 0.0)
        base_tokens = float(getattr(cost_cfg, "baseline_write_tokens_per_memory", 0.0) or 0.0)
        if base_calls <= 0.0 and base_tokens <= 0.0:
            return

        summary = self.db.aggregate_cost_counters(phase="write", user_id=user_id)
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

    def _infer_actor_id_from_query(self, *, query: str, user_id: str) -> Optional[str]:
        """Infer actor from query using profile names/aliases for speaker-anchored retrieval."""
        text = str(query or "").strip().lower()
        if not text or not user_id:
            return None
        try:
            profiles = self.db.get_all_profiles(user_id=user_id)
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
        # Tier 2a: scene summaries (episodic compression).
        if self.scene_processor:
            try:
                for scene in self.scene_processor.search_scenes(query=query, user_id=user_id, limit=max(1, int(limit))):
                    scene_id = str(scene.get("id") or "")[:8]
                    summary = str(scene.get("summary") or scene.get("title") or "").strip()
                    if summary:
                        anchors.append(f"scene[{scene_id}] {summary[:220]}")
            except Exception as e:
                logger.debug("Scene anchor retrieval failed: %s", e)
        # Tier 2b: profile anchors (entity continuity).
        if self.profile_processor:
            try:
                for profile in self.profile_processor.search_profiles(query=query, user_id=user_id, limit=max(1, int(limit))):
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

    def _index_episodic_events_for_memory(
        self,
        *,
        memory_id: str,
        user_id: Optional[str],
        content: str,
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        orch_cfg = getattr(self.config, "orchestration", None)
        if not orch_cfg or not orch_cfg.enable_episodic_index:
            return 0
        if not user_id:
            return 0
        if not content:
            return 0
        try:
            events = extract_episodic_events(
                memory_id=memory_id,
                user_id=user_id,
                content=content,
                metadata=metadata or {},
            )
            # Re-index memory deterministically on updates/duplicate writes.
            self.db.delete_episodic_events_for_memory(memory_id)
            count = self.db.add_episodic_events(events)

            # Accumulate entity aggregates from extracted events.
            if events and hasattr(self.db, "upsert_entity_aggregate"):
                session_id = (metadata or {}).get("session_id", "")
                aggregates = extract_entity_aggregates(events, session_id, memory_id)
                for agg in aggregates:
                    try:
                        if agg["agg_type"] == "item_set":
                            self.db.upsert_entity_set_member(
                                user_id=user_id,
                                entity_key=agg["entity_key"],
                                item_value=agg.get("item_value", ""),
                                session_id=agg.get("session_id"),
                                memory_id=agg.get("memory_id"),
                            )
                        else:
                            self.db.upsert_entity_aggregate(
                                user_id=user_id,
                                entity_key=agg["entity_key"],
                                agg_type=agg["agg_type"],
                                value_delta=agg.get("value_delta", 0),
                                value_unit=agg.get("value_unit"),
                                session_id=agg.get("session_id"),
                                memory_id=agg.get("memory_id"),
                            )
                    except Exception as agg_exc:
                        logger.debug("Entity aggregate upsert failed: %s", agg_exc)

            return count
        except Exception as e:
            logger.debug("Episodic indexing failed for %s: %s", memory_id, e)
            return 0

    def search_episodes(
        self,
        *,
        query: str,
        user_id: str,
        intent: Optional[AnswerIntent] = None,
        actor_id: Optional[str] = None,
        time_anchor: Optional[str] = None,
        entity_hints: Optional[List[str]] = None,
        min_coverage: Optional[float] = None,
        limit: int = 80,
    ) -> Dict[str, Any]:
        orch_cfg = getattr(self.config, "orchestration", None)
        if not orch_cfg or not orch_cfg.enable_episodic_index:
            return {"results": [], "coverage": {"event_hit_count": 0, "unique_canonical_keys": 0, "sufficient": False}}

        intent_value = (intent.value if isinstance(intent, AnswerIntent) else str(intent or "")).strip().lower()
        event_types = intent_event_types(intent_value)
        if event_types is not None:
            event_types = list(event_types)

        normalized_hints = [str(h).strip().lower() for h in (entity_hints or []) if str(h).strip()]
        anchor_dt = self._parse_bitemporal_datetime(time_anchor) if time_anchor else None

        # Pull a broader window and score in Python to stay deterministic across intents.
        events = self.db.get_episodic_events(
            user_id=user_id,
            actor_id=actor_id,
            event_types=event_types,
            time_anchor=time_anchor,
            entity_hints=normalized_hints,
            limit=max(50, int(limit) * 6),
        )
        query_terms = tokenize_query_terms(query)
        if normalized_hints:
            query_terms = list(dict.fromkeys(query_terms + normalized_hints))

        scored_events: List[Dict[str, Any]] = []
        for event in events:
            score = score_event_match(event, query_terms)
            if normalized_hints:
                event_entity = str(event.get("entity_key") or event.get("actor_id") or event.get("actor_role") or "").lower()
                if any(h in event_entity for h in normalized_hints):
                    score += 1.0
            # For typed intents (money/duration/latest/etc.), keep events even when
            # lexical overlap is sparse; intent filtering already constrained types.
            if query_terms and score <= 0 and event_types is None:
                continue
            if score <= 0 and event_types is not None:
                score = 0.25
            # Prefer recency for latest-style questions.
            if intent_value == "latest":
                dt = self._parse_bitemporal_datetime(event.get("event_time"))
                if dt is not None:
                    age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
                    score += max(0.0, 2.0 - (age_days / 30.0))
            # Anchor-aware boost: favor evidence near the caller-provided time anchor.
            if anchor_dt is not None:
                ev_dt = self._parse_bitemporal_datetime(
                    event.get("normalized_time_start") or event.get("event_time")
                )
                if ev_dt is not None:
                    dist_days = abs((anchor_dt - ev_dt).total_seconds()) / 86400.0
                    score += max(0.0, 0.75 - (dist_days / 45.0))
            event_copy = dict(event)
            event_copy["match_score"] = float(score)
            scored_events.append(event_copy)

        scored_events.sort(
            key=lambda row: (
                float(row.get("match_score", 0.0)),
                str(row.get("event_time") or ""),
                int(row.get("turn_id", 0) or 0),
            ),
            reverse=True,
        )
        top_events = scored_events[: max(1, int(limit))]
        unique_keys = {str(row.get("canonical_key") or "") for row in top_events if row.get("canonical_key")}
        unique_entities = {
            str(row.get("entity_key") or row.get("actor_id") or "").strip().lower()
            for row in top_events
            if str(row.get("entity_key") or row.get("actor_id") or "").strip()
        }
        numeric_fact_count = sum(1 for row in top_events if row.get("value_num") is not None)
        dated_fact_count = sum(
            1
            for row in top_events
            if str(row.get("normalized_time_start") or row.get("event_time") or "").strip()
        )

        context_cap = max(1, int(getattr(orch_cfg, "context_cap", 20)))
        coverage_ratio = min(1.0, len(unique_keys) / float(context_cap)) if unique_keys else 0.0
        intent_coverage = coverage_ratio
        if intent_value in {"count", "set_members"}:
            intent_coverage = min(1.0, len(unique_entities) / float(max(1, min(context_cap, 8))))
        elif intent_value in {"money_sum", "duration"}:
            intent_coverage = min(1.0, numeric_fact_count / float(max(1, min(context_cap, 8))))
        elif intent_value == "latest":
            intent_coverage = min(1.0, dated_fact_count / float(max(1, min(context_cap, 6))))

        default_threshold = float(getattr(orch_cfg, "map_reduce_coverage_threshold", 0.6))
        threshold = self._intent_coverage_threshold(intent_value, default_threshold)
        if min_coverage is not None:
            try:
                threshold = max(0.0, min(1.0, float(min_coverage)))
            except (TypeError, ValueError):
                threshold = self._intent_coverage_threshold(intent_value, default_threshold)

        coverage = {
            "event_hit_count": len(top_events),
            "unique_canonical_keys": len(unique_keys),
            "unique_entities": len(unique_entities),
            "numeric_fact_count": int(numeric_fact_count),
            "dated_fact_count": int(dated_fact_count),
            "coverage_ratio": round(coverage_ratio, 4),
            "intent_coverage": round(float(intent_coverage), 4),
            "threshold": round(float(threshold), 4),
            "sufficient": bool(intent_coverage >= threshold and len(top_events) > 0),
        }
        return {"results": top_events, "coverage": coverage}

    def lookup_entity_aggregates(
        self,
        query: str,
        user_id: str,
        intent: Optional[str] = None,
    ) -> Optional[str]:
        """Look up pre-computed entity aggregates that match a query.

        Returns a formatted answer string (e.g. "8 days", "$140", "3") or None.
        """
        if not hasattr(self.db, "get_entity_aggregates"):
            return None

        # Extract entity keywords from query
        keywords = tokenize_query_terms(query)
        if not keywords:
            return None

        # Determine which agg_type to look for based on intent / query phrasing
        q_lower = query.lower()
        agg_types: List[str] = []
        if intent:
            intent_lower = intent.lower()
            if intent_lower in ("duration", "duration_sum"):
                agg_types = ["duration_sum"]
            elif intent_lower in ("money", "money_sum"):
                agg_types = ["money_sum"]
            elif intent_lower in ("count", "set_members"):
                agg_types = ["count", "item_set"]

        if not agg_types:
            # Infer from question phrasing
            if any(w in q_lower for w in ("how long", "how many days", "how many hours",
                                           "how many weeks", "how many months", "duration")):
                agg_types = ["duration_sum"]
            elif any(w in q_lower for w in ("how much", "cost", "spent", "price", "money")):
                agg_types = ["money_sum"]
            else:
                agg_types = ["count", "item_set", "duration_sum"]

        best_match = None
        best_score = 0.0

        for agg_type in agg_types:
            rows = self.db.get_entity_aggregates(
                user_id=user_id,
                agg_type=agg_type,
                entity_hints=keywords,
            )
            for row in rows:
                # Score how well this aggregate matches the query
                entity_key = str(row.get("entity_key") or "").lower()
                score = sum(1.0 for kw in keywords if kw in entity_key)
                # Bonus for having multiple contributing sessions (multi-session aggregation)
                sessions = row.get("contributing_sessions")
                if sessions:
                    try:
                        n_sessions = len(json.loads(sessions)) if isinstance(sessions, str) else len(sessions)
                    except Exception:
                        n_sessions = 0
                    if n_sessions > 1:
                        score += 0.5  # prefer multi-session aggregates

                if score > best_score:
                    best_score = score
                    best_match = row

        if not best_match or best_score < 1.0:
            return None

        # Format the answer
        agg_type = best_match.get("agg_type", "")
        value_num = best_match.get("value_num")
        value_unit = best_match.get("value_unit")
        item_set = best_match.get("item_set")

        if agg_type == "item_set" and item_set:
            try:
                items = json.loads(item_set) if isinstance(item_set, str) else item_set
                return str(len(items))
            except Exception:
                pass

        if value_num is not None:
            try:
                num = float(value_num)
                if abs(num - round(num)) < 1e-9:
                    formatted = str(int(round(num)))
                else:
                    formatted = f"{num:g}"
                if value_unit:
                    if agg_type == "money_sum":
                        return f"${formatted}" if value_unit == "USD" else f"{formatted} {value_unit}"
                    return f"{formatted} {value_unit}{'s' if num != 1 else ''}"
                return formatted
            except (TypeError, ValueError):
                pass

        return None

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
        orch_cfg = getattr(self.config, "orchestration", None)
        enabled = bool(orch_cfg and orch_cfg.enable_orchestrated_search and mode in {"hybrid", "strict"})

        if not enabled:
            base = self.search(
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
        event_payload = self.search_episodes(
            query=query,
            user_id=user_id,
            intent=query_plan.intent,
            actor_id=actor_id,
            time_anchor=question_date or None,
            entity_hints=entity_hints,
            min_coverage=self._intent_coverage_threshold(
                query_plan.intent.value,
                float(getattr(orch_cfg, "map_reduce_coverage_threshold", 0.6)),
            ),
            limit=max(20, context_limit * 2),
        )
        event_hits = event_payload.get("results", [])
        coverage = event_payload.get("coverage", {}) or {}
        reason_codes: List[str] = []

        search_payload = self.search(
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
                        hydrated = self.db.get_memories_bulk(missing_ids, include_tombstoned=False)
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

        self._record_cost_counter(
            phase="query",
            user_id=user_id,
            llm_calls=llm_calls_used,
            input_tokens=0.0,
            output_tokens=0.0,
            embed_calls=0.0,
        )

        intent_coverage = float(coverage.get("intent_coverage", coverage.get("coverage_ratio", 0.0)) or 0.0)

        # Dhee: Self-evolution — record answer generation signal
        if self.evolution_layer and reduced_answer:
            try:
                source_ids = [r.get("id", "") for r in results[:context_limit] if r.get("id")]
                source_texts = [r.get("memory", "") for r in results[:context_limit] if r.get("memory")]
                self.evolution_layer.on_answer_generated(
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
        active_orchestrator_llm = orchestrator_llm or self.llm
        orch_cfg = getattr(self.config, "orchestration", None)
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
                else getattr(self.config.orchestration, "reflection_max_hops", 1)
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
                reflection_payload = self.search(
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
        memory_id: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None,
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
                # When infer=False, the caller explicitly provides content to store.
                # Treat as high confidence to avoid the low_confidence strength cap.
                mem_meta["user_provided"] = True
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
                memory_id=memory_id,
                context_messages=context_messages,
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

        # Write-path telemetry aggregates for this batch (later normalized per memory).
        batch_llm_calls_total = 0.0
        batch_embed_calls_total = 0.0
        batch_input_tokens_total = 0.0
        batch_output_tokens_total = 0.0

        # 0. Try unified enrichment (single LLM call for echo+category+entities+profiles)
        echo_results = [None] * len(contents)
        category_results = [None] * len(contents)
        enrichment_results = [None] * len(contents)  # stash for post-store hooks

        enrichment_config = getattr(self.config, "enrichment", None)
        _use_unified = (
            self.unified_enrichment is not None
            and self.echo_config.enable_echo
            and batch_config.batch_echo
        )

        if _use_unified:
            try:
                depth_override = EchoDepth(echo_depth) if echo_depth else EchoDepth(self.echo_config.default_depth)
                existing_cats = None
                if self.category_processor:
                    cats = self.category_processor.get_all_categories()
                    if cats:
                        existing_cats = "\n".join(
                            f"- {c['id']}: {c['name']} — {c.get('description', '')}"
                            for c in cats[:30]
                        )

                # Process in sub-batches of enrichment_config.max_batch_size
                enrich_batch_size = enrichment_config.max_batch_size if enrichment_config else 10
                for start in range(0, len(contents), enrich_batch_size):
                    end = min(start + enrich_batch_size, len(contents))
                    sub_contents = contents[start:end]
                    sub_results = self.unified_enrichment.enrich_batch(
                        sub_contents,
                        depth=depth_override,
                        existing_categories=existing_cats,
                        include_entities=enrichment_config.include_entities if enrichment_config else True,
                        include_profiles=enrichment_config.include_profiles if enrichment_config else True,
                    )
                    sub_input_tokens = sum(self._estimate_token_count(c) for c in sub_contents)
                    sub_input_tokens += self._estimate_token_count(existing_cats)
                    batch_llm_calls_total += 1.0
                    batch_input_tokens_total += sub_input_tokens
                    batch_output_tokens_total += self._estimate_output_tokens(sub_input_tokens)
                    for j, enrichment in enumerate(sub_results):
                        idx = start + j
                        if enrichment.echo_result:
                            echo_results[idx] = enrichment.echo_result
                        if enrichment.category_match:
                            category_results[idx] = enrichment.category_match
                        enrichment_results[idx] = enrichment

                logger.info("Unified batch enrichment completed for %d memories", len(contents))
            except Exception as e:
                logger.warning("Unified batch enrichment failed, falling back to separate: %s", e)
                # Reset — let the fallback below handle it
                echo_results = [None] * len(contents)
                category_results = [None] * len(contents)
                enrichment_results = [None] * len(contents)
                _use_unified = False

        # 1. Batch echo encoding (fallback if unified was not used or failed)
        if not _use_unified:
            if self.echo_processor and self.echo_config.enable_echo and batch_config.batch_echo:
                depth_override = EchoDepth(echo_depth) if echo_depth else EchoDepth(self.echo_config.default_depth)
                if depth_override != EchoDepth.SHALLOW:
                    echo_input_tokens = sum(self._estimate_token_count(c) for c in contents if c)
                    non_empty_count = sum(1 for c in contents if c)
                    batch_llm_calls_total += float(non_empty_count)
                    batch_input_tokens_total += echo_input_tokens
                    batch_output_tokens_total += self._estimate_output_tokens(echo_input_tokens)
                try:
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
            if (
                self.category_processor
                and self.category_config.auto_categorize
                and batch_config.batch_category
            ):
                if self.category_config.use_llm_categorization:
                    cat_input_tokens = sum(self._estimate_token_count(c) for c in contents if c)
                    non_empty_count = sum(1 for c in contents if c)
                    batch_llm_calls_total += float(non_empty_count)
                    batch_input_tokens_total += cat_input_tokens
                    batch_output_tokens_total += self._estimate_output_tokens(cat_input_tokens)
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
                # Sub-batch to stay within API limits (~50 per call)
                embeddings: List[List[float]] = []
                for start in range(0, len(primary_texts), 50):
                    sub = primary_texts[start:start + 50]
                    embeddings.extend(self.embedder.embed_batch(sub, memory_action="add"))
                    batch_embed_calls_total += 1.0
            except Exception as e:
                logger.warning("Batch embed failed, falling back to sequential: %s", e)
                embeddings = [
                    self.embedder.embed(t, memory_action="add") for t in primary_texts
                ]
                batch_embed_calls_total += float(len(primary_texts))
        else:
            embeddings = [
                self.embedder.embed(t, memory_action="add") for t in primary_texts
            ]
            batch_embed_calls_total += float(len(primary_texts))

        # 3b. Pre-embed all echo node texts (paraphrases, questions, content variants)
        # so _build_index_vectors can use the cache instead of individual embed() calls.
        echo_node_texts = []
        for i, content in enumerate(contents):
            echo_result = echo_results[i]
            pt = primary_texts[i]
            if pt != content:
                cleaned = content.strip()
                if cleaned:
                    echo_node_texts.append(cleaned)
            if echo_result:
                for p in echo_result.paraphrases:
                    cleaned = str(p).strip()
                    if cleaned:
                        echo_node_texts.append(cleaned)
                for q in echo_result.questions:
                    cleaned = str(q).strip()
                    if cleaned:
                        echo_node_texts.append(cleaned)

        embedding_cache: Dict[str, List[float]] = {}
        if echo_node_texts:
            # Deduplicate while preserving order for batch embedding
            unique_texts = list(dict.fromkeys(echo_node_texts))
            try:
                # Sub-batch to stay within NVIDIA API limits (~50 per call)
                all_echo_embeddings: List[List[float]] = []
                for start in range(0, len(unique_texts), 50):
                    sub = unique_texts[start:start + 50]
                    sub_embs = self.embedder.embed_batch(sub, memory_action="add")
                    all_echo_embeddings.extend(sub_embs)
                    batch_embed_calls_total += 1.0
                for text, emb in zip(unique_texts, all_echo_embeddings):
                    embedding_cache[text] = emb
                logger.info("Batch-embedded %d echo node texts in %d API calls",
                            len(unique_texts), (len(unique_texts) + 49) // 50)
            except Exception as e:
                logger.warning("Batch echo node embedding failed, will embed individually: %s", e)

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
        episodic_rows: List[Tuple[str, Optional[str], str, Dict[str, Any]]] = []
        vector_batch = []  # (vectors, payloads, ids)
        results = []

        for i, content in enumerate(contents):
            if not content:
                continue

            memory_id = str(uuid.uuid4())
            mem_metadata = dict(processed_metadata_base)
            mem_metadata.update(item_metadata_list[i])
            mem_metadata = self._attach_bitemporal_metadata(mem_metadata, observed_time=now)

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
            episodic_rows.append(
                (
                    memory_id,
                    items[i].get("user_id") or user_id,
                    content,
                    mem_metadata,
                )
            )

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
                embedding_cache=embedding_cache if embedding_cache else None,
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

        # Deterministic episodic index.
        for memory_id, owner_user_id, content, mem_metadata in episodic_rows:
            self._index_episodic_events_for_memory(
                memory_id=memory_id,
                user_id=owner_user_id,
                content=content,
                metadata=mem_metadata,
            )

        # Post-store hooks: category stats
        for i, record in enumerate(memory_records):
            if self.category_processor and record.get("categories"):
                for cat_id in record["categories"]:
                    self.category_processor.update_category_stats(
                        cat_id, record["strength"], is_addition=True
                    )

        # Post-store hooks: fact decomposition (batch embed + insert)
        all_fact_texts = []
        all_fact_meta = []  # (memory_id, fact_index)
        for i, record in enumerate(memory_records):
            enrichment = enrichment_results[i] if i < len(enrichment_results) else None
            if enrichment and enrichment.facts:
                for fi, fact_text in enumerate(enrichment.facts[:8]):
                    fact_text = fact_text.strip()
                    if fact_text and len(fact_text) >= 10:
                        all_fact_texts.append(fact_text)
                        all_fact_meta.append((record["id"], fi))

        if all_fact_texts:
            try:
                # Sub-batch fact embeddings to stay within API limits
                fact_embeddings: List[List[float]] = []
                for fs in range(0, len(all_fact_texts), 50):
                    sub = all_fact_texts[fs:fs + 50]
                    fact_embeddings.extend(self.embedder.embed_batch(sub, memory_action="add"))
                    batch_embed_calls_total += 1.0
                fact_vectors = []
                fact_payloads = []
                fact_ids = []
                for (memory_id, fi), fact_text, fact_emb in zip(all_fact_meta, all_fact_texts, fact_embeddings):
                    fact_id = f"{memory_id}__fact_{fi}"
                    fact_vectors.append(fact_emb)
                    fact_payloads.append({
                        "memory_id": memory_id,
                        "is_fact": True,
                        "fact_index": fi,
                        "fact_text": fact_text,
                        "user_id": user_id,
                        "agent_id": agent_id,
                    })
                    fact_ids.append(fact_id)
                if fact_vectors:
                    self.vector_store.insert(vectors=fact_vectors, payloads=fact_payloads, ids=fact_ids)
            except Exception as e:
                logger.warning("Batch fact embedding/insert failed: %s", e)

        # Post-store hooks: entity linking and profile updates
        for i, record in enumerate(memory_records):
            enrichment = enrichment_results[i] if i < len(enrichment_results) else None
            if not enrichment:
                continue
            memory_id = record["id"]
            content = record.get("memory", "")

            if self.knowledge_graph and enrichment.entities:
                try:
                    for entity in enrichment.entities:
                        existing_ent = self.knowledge_graph._get_or_create_entity(
                            entity.name, entity.entity_type,
                        )
                        existing_ent.memory_ids.add(memory_id)
                    self.knowledge_graph.memory_entities[memory_id] = {
                        e.name for e in enrichment.entities
                    }
                    if self.graph_config.auto_link_entities:
                        self.knowledge_graph.link_by_shared_entities(memory_id)
                except Exception as e:
                    logger.warning("Entity linking failed for %s: %s", memory_id, e)

            if self.profile_processor and enrichment.profile_updates:
                try:
                    for profile_update in enrichment.profile_updates:
                        self.profile_processor.apply_update(
                            profile_update=profile_update,
                            memory_id=memory_id,
                            user_id=record.get("user_id") or user_id or "default",
                        )
                except Exception as e:
                    logger.warning("Profile update failed for %s: %s", memory_id, e)

        # Post-store hooks: Universal Engram extraction (structured facts + context anchors)
        if self.engram_extractor:
            for i, record in enumerate(memory_records):
                memory_id = record["id"]
                content = record.get("memory", "")
                try:
                    engram = self.engram_extractor.extract(
                        content=content,
                        session_context=None,
                        existing_metadata=record.get("metadata"),
                        user_id=record.get("user_id") or user_id or "default",
                    )
                    if self.context_resolver and engram:
                        self.context_resolver.store_engram(engram, memory_id)
                except Exception as e:
                    logger.warning("Engram extraction failed for %s: %s", memory_id, e)

        if episodic_rows:
            sample_count = float(len(episodic_rows))
            llm_calls_per_memory = batch_llm_calls_total / sample_count
            input_tokens_per_memory = batch_input_tokens_total / sample_count
            output_tokens_per_memory = batch_output_tokens_total / sample_count
            embed_calls_per_memory = batch_embed_calls_total / sample_count
            for _, owner_user_id, _, _ in episodic_rows:
                self._record_cost_counter(
                    phase="write",
                    user_id=owner_user_id,
                    llm_calls=llm_calls_per_memory,
                    input_tokens=input_tokens_per_memory,
                    output_tokens=output_tokens_per_memory,
                    embed_calls=embed_calls_per_memory,
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
        memory_id: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process and store a single memory item. Returns result dict or None if skipped."""
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
            write_output_tokens += self._estimate_output_tokens(tokens)

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

        # --- Deferred enrichment: lite path (0 LLM calls) ---
        enrichment_config = getattr(self.config, "enrichment", None)
        if enrichment_config and enrichment_config.defer_enrichment:
            return self._process_single_memory_lite(
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

        # Pre-extracted data from unified enrichment (used to skip redundant post-store calls)
        _unified_entities = None   # List[Entity] or None
        _unified_profiles = None   # List[ProfileUpdate] or None
        _unified_facts = None      # List[str] or None

        # Determine echo depth for unified path check
        _depth_for_echo = EchoDepth(echo_depth) if echo_depth else None
        if _depth_for_echo is None and self.echo_processor and hasattr(self.echo_processor, '_assess_depth'):
            try:
                _depth_for_echo = self.echo_processor._assess_depth(content)
            except Exception:
                _depth_for_echo = EchoDepth.MEDIUM

        # Site 0: Unified enrichment (single LLM call for echo+category+entities+profiles)
        _use_unified = (
            self.unified_enrichment is not None
            and self.echo_config.enable_echo
            and _depth_for_echo != EchoDepth.SHALLOW  # shallow is LLM-free
        )

        if _use_unified:
            enrichment_config = getattr(self.config, "enrichment", None)
            existing_cats = None
            if self.category_processor:
                cats = self.category_processor.get_all_categories()
                if cats:
                    existing_cats = "\n".join(
                        f"- {c['id']}: {c['name']} — {c.get('description', '')}"
                        for c in cats[:30]
                    )

            unified_input_tokens = self._estimate_token_count(content) + self._estimate_token_count(existing_cats)
            _add_llm_cost(unified_input_tokens)

            enrichment = self.unified_enrichment.enrich(
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
            primary_text = self._select_primary_text(content, echo_result)
            embedding = self.embedder.embed(primary_text, memory_action="add")
            write_embed_calls += 1.0

        else:
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
                depth_for_parallel = EchoDepth(echo_depth) if echo_depth else (_depth_for_echo or EchoDepth(self.echo_config.default_depth))
                if self.echo_config.enable_echo and depth_for_parallel != EchoDepth.SHALLOW:
                    _add_llm_cost(self._estimate_token_count(content))
                if _should_categorize and self.category_config.use_llm_categorization:
                    _add_llm_cost(self._estimate_token_count(content))

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
                write_embed_calls += 1.0
                echo_result = echo_result_p
            else:
                # Sequential path (original behavior)
                if _should_categorize:
                    if self.category_config.use_llm_categorization:
                        _add_llm_cost(self._estimate_token_count(content))
                    category_match = self.category_processor.detect_category(
                        content,
                        metadata=mem_metadata,
                        use_llm=self.category_config.use_llm_categorization,
                    )
                    mem_categories = [category_match.category_id]
                    mem_metadata["category_confidence"] = category_match.confidence
                    mem_metadata["category_auto"] = True

                # Encode memory (echo + embedding).
                depth_for_encode = EchoDepth(echo_depth) if echo_depth else (_depth_for_echo or EchoDepth(self.echo_config.default_depth))
                if self.echo_config.enable_echo and depth_for_encode != EchoDepth.SHALLOW:
                    _add_llm_cost(self._estimate_token_count(content))
                echo_result, effective_strength, mem_categories, embedding = self._encode_memory(
                    content, echo_depth, mem_categories, mem_metadata, initial_strength,
                )
                write_embed_calls += 1.0

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
            conflict_input_tokens = self._estimate_token_count(existing.get("memory", "")) + self._estimate_token_count(content)
            _add_llm_cost(conflict_input_tokens)
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
                self._record_cost_counter(
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
            depth_for_encode = EchoDepth(echo_depth) if echo_depth else (_depth_for_echo or EchoDepth(self.echo_config.default_depth))
            if self.echo_config.enable_echo and depth_for_encode != EchoDepth.SHALLOW:
                _add_llm_cost(self._estimate_token_count(content))
            echo_result, _, mem_categories, embedding = self._encode_memory(
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
        memory_type = self._classify_memory_type(mem_metadata, role)

        # Gap 4: Initialize multi-trace strength
        s_fast_val = None
        s_mid_val = None
        s_slow_val = None
        if self.distillation_config and self.distillation_config.enable_multi_trace:
            s_fast_val, s_mid_val, s_slow_val = initialize_traces(effective_strength, is_new=True)

        # Metamemory: compute confidence score if enabled
        if self.config.metamemory.enable_confidence:
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
        mem_metadata = self._attach_bitemporal_metadata(mem_metadata, observed_time=now)
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
            memory_id=effective_memory_id,
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
                    effective_memory_id, e,
                )
                try:
                    self.db.delete_memory(effective_memory_id, use_tombstone=False)
                except Exception as rollback_err:
                    logger.critical(
                        "CRITICAL: DB rollback also failed for memory %s — manual cleanup required: %s",
                        effective_memory_id, rollback_err,
                    )
                raise

        # Fact decomposition: store each extracted fact as a sub-vector for direct retrieval.
        # Each fact gets its own embedding, linked back to the parent memory.
        # Uses batch embedding (single API call) for efficiency.
        if _unified_facts:
            valid_facts = []
            for i, fact_text in enumerate(_unified_facts[:8]):  # Cap at 8 facts
                fact_text = fact_text.strip()
                if fact_text and len(fact_text) >= 10:
                    valid_facts.append((i, fact_text))

            if valid_facts:
                try:
                    fact_texts = [ft for _, ft in valid_facts]
                    fact_embeddings = self.embedder.embed_batch(fact_texts, memory_action="add")
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
                        self.vector_store.insert(vectors=fact_vectors, payloads=fact_payloads, ids=fact_ids)
                except Exception as e:
                    logger.warning("Fact embedding/insert failed for %s: %s", effective_memory_id, e)

        # Post-store hooks.
        if self.category_processor and mem_categories:
            for cat_id in mem_categories:
                self.category_processor.update_category_stats(
                    cat_id, effective_strength, is_addition=True
                )

        if self.knowledge_graph:
            if _unified_entities is not None:
                # Use pre-extracted entities from unified enrichment
                for entity in _unified_entities:
                    existing = self.knowledge_graph._get_or_create_entity(
                        entity.name, entity.entity_type,
                    )
                    existing.memory_ids.add(effective_memory_id)
                self.knowledge_graph.memory_entities[effective_memory_id] = {
                    e.name for e in _unified_entities
                }
            else:
                if self.graph_config.use_llm_extraction:
                    _add_llm_cost(self._estimate_token_count(content))
                self.knowledge_graph.extract_entities(
                    content=content,
                    memory_id=effective_memory_id,
                    use_llm=self.graph_config.use_llm_extraction,
                )
            if self.graph_config.auto_link_entities:
                self.knowledge_graph.link_by_shared_entities(effective_memory_id)

        if self.scene_processor:
            try:
                self._assign_to_scene(effective_memory_id, content, embedding, user_id, now)
            except Exception as e:
                logger.warning("Scene assignment failed for %s: %s", effective_memory_id, e)

        if self.profile_processor:
            try:
                if _unified_profiles is not None and _unified_profiles:
                    # Use pre-extracted profiles from unified enrichment
                    for profile_update in _unified_profiles:
                        self.profile_processor.apply_update(
                            profile_update=profile_update,
                            memory_id=effective_memory_id,
                            user_id=user_id or "default",
                        )
                else:
                    if self.profile_config.use_llm_extraction:
                        _add_llm_cost(self._estimate_token_count(content))
                    self._update_profiles(effective_memory_id, content, mem_metadata, user_id)
            except Exception as e:
                logger.warning("Profile update failed for %s: %s", effective_memory_id, e)

        self._index_episodic_events_for_memory(
            memory_id=effective_memory_id,
            user_id=user_id,
            content=content,
            metadata=mem_metadata,
        )

        # Dhee: Universal Engram extraction — structured facts + context anchors + prospective scenes.
        # Runs AFTER existing enrichment pipeline to avoid duplication.
        if self.engram_extractor:
            try:
                session_ctx = None
                if context_messages:
                    session_ctx = {"recent_messages": context_messages[-5:]}
                engram = self.engram_extractor.extract(
                    content=content,
                    session_context=session_ctx,
                    existing_metadata=mem_metadata,
                    user_id=user_id or "default",
                )
                # Store structured engram data into v3 tables
                if self.context_resolver:
                    self.context_resolver.store_engram(engram, effective_memory_id)
                # Store prospective scenes (predicted future events)
                if engram.prospective_scenes and self.config.prospective_scene.enable_prospective_scenes:
                    self._store_prospective_scenes(
                        engram.prospective_scenes,
                        effective_memory_id,
                        user_id or "default",
                    )
            except Exception as e:
                logger.warning("Engram extraction failed for %s: %s", effective_memory_id, e)

        # Dhee: Self-evolution — record extraction quality signal
        if self.evolution_layer:
            try:
                engram_facts = None
                engram_context = None
                if self.engram_extractor and 'engram' in dir() and engram:
                    engram_facts = [f.to_dict() if hasattr(f, 'to_dict') else f for f in getattr(engram, 'facts', [])]
                    engram_context = getattr(engram, 'context', None)
                    if engram_context and hasattr(engram_context, '__dict__'):
                        engram_context = engram_context.__dict__
                self.evolution_layer.on_memory_stored(
                    memory_id=effective_memory_id,
                    content=content,
                    facts=engram_facts,
                    context=engram_context,
                    user_id=user_id or "default",
                )
            except Exception as e:
                logger.debug("Evolution write hook skipped: %s", e)

        # Buddhi write hook: detect intentions in stored content
        if self.buddhi_layer:
            try:
                self.buddhi_layer.on_memory_stored(
                    content=content,
                    user_id=user_id or "default",
                )
            except Exception as e:
                logger.debug("Buddhi write hook skipped: %s", e)

        self._record_cost_counter(
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

    def _process_single_memory_lite(
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
        """Lite processing path for deferred enrichment — 0 LLM calls.

        Stores the memory with regex-extracted keywords, context-enriched
        embedding, and enrichment_status='pending'. All heavy LLM processing
        (echo, category, conflict, entities, profiles) is deferred to
        enrich_pending().
        """
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

        # --- Regex keyword extraction (0 LLM calls) ---
        extracted_keywords: List[str] = []
        content_lower = content.lower()

        # Extract preference/routine/goal hints
        for regex, tag in [
            (_PREFERENCE_HINT_RE, "preference"),
            (_ROUTINE_HINT_RE, "routine"),
            (_GOAL_HINT_RE, "goal"),
        ]:
            if regex.search(content):
                extracted_keywords.append(tag)

        # Simple word tokenization for top keywords (skip stopwords)
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

        # Regex entity extraction (names, dates)
        name_match = _NAME_HINT_RE.search(content)
        if name_match:
            extracted_keywords.append(f"name:{name_match.group(1).strip()}")

        mem_metadata["echo_keywords"] = extracted_keywords
        mem_metadata["enrichment_status"] = "pending"

        # --- Build rich embedding text (content + context summary) ---
        context_window = getattr(self.config.enrichment, "context_window_turns", 10)
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
        embedding = self.embedder.embed(embed_text, memory_action="add")

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
        memory_type = self._classify_memory_type(mem_metadata, mem_metadata.get("role", "user"))

        # Multi-trace strength
        s_fast_val = s_mid_val = s_slow_val = None
        if self.distillation_config and self.distillation_config.enable_multi_trace:
            s_fast_val, s_mid_val, s_slow_val = initialize_traces(effective_strength, is_new=True)

        # Content hash for dedup
        from dhee.memory.core import _content_hash
        ch = _content_hash(content)
        existing = self.db.get_memory_by_content_hash(ch, user_id) if hasattr(self.db, 'get_memory_by_content_hash') else None
        if existing:
            self.db.increment_access(existing["id"])
            return {
                "id": existing["id"],
                "memory": existing.get("memory", ""),
                "event": "DEDUPLICATED",
                "layer": existing.get("layer", "sml"),
                "strength": existing.get("strength", 1.0),
            }

        effective_memory_id = memory_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        mem_metadata = self._attach_bitemporal_metadata(mem_metadata, observed_time=now)

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
            "decay_lambda": self.fadem_config.sml_decay_rate,
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

        self.db.add_memory(memory_data)
        try:
            self.vector_store.insert(vectors=vectors, payloads=payloads, ids=vector_ids)
        except Exception as e:
            logger.error("Vector insert failed for memory %s (lite), rolling back: %s", effective_memory_id, e)
            try:
                self.db.delete_memory(effective_memory_id, use_tombstone=False)
            except Exception as rollback_err:
                logger.critical("DB rollback also failed for %s: %s", effective_memory_id, rollback_err)
            raise

        # Scene assignment still works (embedding-based, no LLM)
        if self.scene_processor:
            try:
                self._assign_to_scene(effective_memory_id, content, embedding, user_id, now)
            except Exception as e:
                logger.warning("Scene assignment failed for %s (lite): %s", effective_memory_id, e)

        self._index_episodic_events_for_memory(
            memory_id=effective_memory_id,
            user_id=user_id,
            content=content,
            metadata=mem_metadata,
        )
        self._record_cost_counter(
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

    def enrich_pending(
        self,
        user_id: str = "default",
        batch_size: int = 10,
        max_batches: int = 5,
    ) -> Dict[str, Any]:
        """Batch-enrich memories that were stored with deferred enrichment.

        Uses unified enrichment: 1 LLM call per batch_size memories.
        Returns {enriched_count, batches, remaining}.
        """
        limit = batch_size * max_batches
        pending = self.db.get_pending_enrichment(user_id=user_id, limit=limit)
        if not pending:
            return {"enriched_count": 0, "batches": 0, "remaining": 0}

        enriched_count = 0
        batches_processed = 0

        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            contents = [m.get("memory", "") for m in batch]

            # Try unified enrichment (single LLM call for the batch)
            enrichment_results = None
            if self.unified_enrichment is not None:
                try:
                    existing_cats = None
                    if self.category_processor:
                        cats = self.category_processor.get_all_categories()
                        if cats:
                            existing_cats = "\n".join(
                                f"- {c['id']}: {c['name']} — {c.get('description', '')}"
                                for c in cats[:30]
                            )

                    enrichment_results = self.unified_enrichment.enrich_batch(
                        contents,
                        depth=EchoDepth.MEDIUM,
                        existing_categories=existing_cats,
                        include_entities=True,
                        include_profiles=True,
                    )
                except Exception as e:
                    logger.warning("Unified batch enrichment failed in enrich_pending: %s", e)
                    enrichment_results = None

            # Fallback: individual enrichment per memory
            if enrichment_results is None:
                enrichment_results = []
                for c in contents:
                    if self.unified_enrichment is not None:
                        try:
                            enrichment_results.append(
                                self.unified_enrichment.enrich(c, depth=EchoDepth.MEDIUM)
                            )
                        except Exception:
                            enrichment_results.append(None)
                    else:
                        enrichment_results.append(None)

            # Apply enrichment results and update DB
            db_updates: List[Dict[str, Any]] = []
            for mem, enrichment in zip(batch, enrichment_results):
                mem_id = mem["id"]
                mem_meta = mem.get("metadata", {}) or {}
                mem_cats = mem.get("categories", []) or []

                if enrichment:
                    # Apply echo result
                    if enrichment.echo_result:
                        mem_meta.update(enrichment.echo_result.to_metadata())
                        if not mem_cats and enrichment.echo_result.category:
                            mem_cats = [enrichment.echo_result.category]

                    # Apply category result
                    if enrichment.category_match and not mem_cats:
                        mem_cats = [enrichment.category_match.category_id]
                        mem_meta["category_confidence"] = enrichment.category_match.confidence
                        mem_meta["category_auto"] = True

                    # Apply extracted facts to metadata
                    if enrichment.facts:
                        mem_meta["enrichment_facts"] = enrichment.facts[:8]

                    # Post-store hooks: entities
                    if self.knowledge_graph and enrichment.entities:
                        for entity in enrichment.entities:
                            existing_ent = self.knowledge_graph._get_or_create_entity(
                                entity.name, entity.entity_type,
                            )
                            existing_ent.memory_ids.add(mem_id)
                        self.knowledge_graph.memory_entities[mem_id] = {
                            e.name for e in enrichment.entities
                        }

                    # Post-store hooks: profiles
                    if self.profile_processor and enrichment.profile_updates:
                        for profile_update in enrichment.profile_updates:
                            try:
                                self.profile_processor.apply_update(
                                    profile_update=profile_update,
                                    memory_id=mem_id,
                                    user_id=user_id,
                                )
                            except Exception as e:
                                logger.warning("Profile update failed during enrichment for %s: %s", mem_id, e)

                    # Generate fact decomposition vectors
                    if enrichment.facts:
                        valid_facts = [
                            (i, f.strip()) for i, f in enumerate(enrichment.facts[:8])
                            if f.strip() and len(f.strip()) >= 10
                        ]
                        if valid_facts:
                            try:
                                fact_texts = [ft for _, ft in valid_facts]
                                fact_embeddings = self.embedder.embed_batch(fact_texts, memory_action="add")
                                fact_vectors, fact_payloads, fact_ids = [], [], []
                                for (i, fact_text), fact_emb in zip(valid_facts, fact_embeddings):
                                    fact_id = f"{mem_id}__fact_{i}"
                                    fact_vectors.append(fact_emb)
                                    fact_payloads.append({
                                        "memory_id": mem_id,
                                        "is_fact": True,
                                        "fact_index": i,
                                        "fact_text": fact_text,
                                        "user_id": user_id,
                                    })
                                    fact_ids.append(fact_id)
                                if fact_vectors:
                                    self.vector_store.insert(
                                        vectors=fact_vectors,
                                        payloads=fact_payloads,
                                        ids=fact_ids,
                                    )
                            except Exception as e:
                                logger.warning("Fact embedding failed during enrichment for %s: %s", mem_id, e)

                mem_meta["enrichment_status"] = "complete"
                db_updates.append({
                    "id": mem_id,
                    "metadata": mem_meta,
                    "categories": mem_cats,
                    "enrichment_status": "complete",
                })
                enriched_count += 1

            # Batch DB update
            self.db.update_enrichment_bulk(db_updates)
            batches_processed += 1

        # Check remaining
        remaining_count = len(self.db.get_pending_enrichment(user_id=user_id, limit=1))

        return {
            "enriched_count": enriched_count,
            "batches": batches_processed,
            "remaining": remaining_count,
        }

    @staticmethod
    def _normalize_bitemporal_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        return text or None

    @classmethod
    def _parse_bitemporal_datetime(cls, value: Any) -> Optional[datetime]:
        normalized = cls._normalize_bitemporal_value(value)
        if not normalized:
            return None
        text = normalized
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            # Allow plain YYYY-MM-DD values.
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

    @classmethod
    def _attach_bitemporal_metadata(
        cls,
        metadata: Optional[Dict[str, Any]],
        observed_time: str,
    ) -> Dict[str, Any]:
        md = dict(metadata or {})

        observed_norm = cls._normalize_bitemporal_value(md.get("observed_time")) or observed_time
        md["observed_time"] = observed_norm

        event_candidate = (
            md.get("event_time")
            or md.get("session_date")
            or md.get("event_date")
            or md.get("timestamp")
            or md.get("date")
        )
        event_norm = cls._normalize_bitemporal_value(event_candidate)
        if event_norm:
            md["event_time"] = event_norm
        return md

    @staticmethod
    def _query_prefers_recency(query: str) -> bool:
        q = str(query or "")
        return bool(_TEMPORAL_RECENT_QUERY_RE.search(q) or _TEMPORAL_RANGE_QUERY_RE.search(q))

    @staticmethod
    def _query_is_transactional(query: str) -> bool:
        return bool(_TEMPORAL_TRANSACTIONAL_QUERY_RE.search(str(query or "")))

    def _compute_temporal_boost(
        self,
        *,
        query: str,
        metadata: Dict[str, Any],
        query_intent: Optional[QueryIntent],
    ) -> float:
        if not metadata:
            return 0.0
        if not self._query_prefers_recency(query) and query_intent not in {QueryIntent.EPISODIC}:
            return 0.0

        event_time = metadata.get("event_time") or metadata.get("session_date")
        event_dt = self._parse_bitemporal_datetime(event_time)
        if event_dt is None:
            return 0.0

        now = datetime.now(timezone.utc)
        age_days = max(0.0, (now - event_dt).total_seconds() / 86400.0)

        # Transaction-like facts should decay faster than profile-like facts.
        decay_days = 30.0 if self._query_is_transactional(query) else 180.0
        recency = math.exp(-age_days / decay_days)
        boost = 0.20 * recency

        # If query explicitly limits a recent window, penalize very old memories.
        if _TEMPORAL_RANGE_QUERY_RE.search(str(query or "")) and age_days > 45.0:
            penalty = min(0.20, (age_days - 45.0) / 365.0)
            boost -= penalty

        if boost > 0.25:
            return 0.25
        if boost < -0.25:
            return -0.25
        return boost

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
        if self.context_resolver:
            try:
                resolver_result = self.context_resolver.resolve(query, user_id=user_id or "default")
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

        # Prepare query terms for echo-based re-ranking (strip punctuation)
        query_lower = query.lower()
        query_terms = set(
            re.sub(r"[^\w\s]", "", query_lower).split()
        )

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

            # Bitemporal recency policy: boost/penalize memories using event_time vs query recency signals.
            temporal_boost = self._compute_temporal_boost(
                query=query,
                metadata=metadata,
                query_intent=query_intent,
            )
            if temporal_boost:
                combined = combined * (1 + temporal_boost)

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

            # Procedural: boost automatic procedures in search results
            proc_boost = 0.0
            if self.config.procedural.automaticity_boost_in_search:
                automaticity = metadata.get("proc_automaticity", 0)
                if isinstance(automaticity, (int, float)) and automaticity >= 0.5:
                    proc_boost = float(automaticity) * self.config.procedural.automaticity_boost_in_search_weight
                    combined = combined * (1 + proc_boost)

            # Salience: boost high-salience memories
            salience_boost = 0.0
            if self.config.salience.enable_salience:
                sal_score = metadata.get("sal_salience_score", 0)
                if isinstance(sal_score, (int, float)) and sal_score > 0:
                    salience_boost = float(sal_score) * self.config.salience.salience_boost_weight
                    combined = combined * (1 + salience_boost)

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

        # Neural reranking: cross-encoder second stage on top candidates
        rerank_cfg = getattr(self.config, "rerank", None)
        if rerank and self.reranker and results:
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
                reranked = self.reranker.rerank(
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
        if not results and self.config.metamemory.auto_log_gaps:
            try:
                from engram_metamemory.metamemory import Metamemory as _Metamemory
                _mm = _Metamemory(self, user_id=user_id or "default")
                _mm.log_knowledge_gap(query=query, reason="empty_search")
            except ImportError:
                pass
            except Exception as e:
                logger.debug("Auto-gap logging failed: %s", e)

        # Dhee: Self-evolution — record retrieval quality signal
        if self.evolution_layer and results:
            try:
                self.evolution_layer.on_search_results(
                    query=query,
                    results=results[:limit],
                    user_id=user_id or "default",
                )
            except Exception as e:
                logger.debug("Evolution search hook skipped: %s", e)

        # Buddhi search hook: piggyback proactive signals (intentions, insights)
        final_results = results[:limit]
        if self.buddhi_layer and final_results:
            try:
                buddhi_signals = self.buddhi_layer.on_search(
                    query=query,
                    results=final_results,
                    user_id=user_id or "default",
                )
                if buddhi_signals:
                    return {"results": final_results, "buddhi": buddhi_signals}
            except Exception as e:
                logger.debug("Buddhi search hook skipped: %s", e)

        return {"results": final_results}

    # Stop words to exclude from echo boost term matching
    _ECHO_STOP_WORDS = frozenset({
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "has", "have", "had", "i", "me", "my", "we",
        "our", "you", "your", "he", "she", "it", "they", "them", "their",
        "what", "which", "who", "whom", "this", "that", "these", "those",
        "am", "will", "would", "shall", "should", "can", "could", "may",
        "might", "must", "to", "of", "in", "for", "on", "with", "at", "by",
        "from", "about", "as", "into", "through", "during", "before", "after",
        "and", "but", "or", "nor", "not", "so", "if", "then", "than", "too",
        "very", "just", "how", "when", "where", "why", "all", "each", "some",
        "any", "no", "yes",
    })

    def _calculate_echo_boost(
        self, query_lower: str, query_terms: set, metadata: Dict[str, Any]
    ) -> float:
        """Calculate re-ranking boost based on echo metadata matches."""
        boost = 0.0
        content_query_terms = query_terms - self._ECHO_STOP_WORDS

        # Keyword match boost
        keywords = metadata.get("echo_keywords", [])
        if keywords:
            keyword_matches = 0
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower in query_lower:
                    keyword_matches += 1
                elif content_query_terms and any(
                    term in kw_lower or kw_lower in term
                    for term in content_query_terms
                    if len(term) > 3
                ):
                    keyword_matches += 1
            boost += keyword_matches * 0.06
            # Coverage bonus: high fraction of query content matched = strong signal
            if content_query_terms and keyword_matches > 0:
                coverage = keyword_matches / len(content_query_terms)
                boost += coverage * 0.15

        # Question form similarity boost (if query is similar to question_form)
        question_form = metadata.get("echo_question_form", "")
        if question_form and content_query_terms:
            q_terms = set(question_form.lower().split()) - self._ECHO_STOP_WORDS
            overlap = len(content_query_terms & q_terms)
            if overlap > 0:
                boost += min(0.15, overlap * 0.05)

        # Implication match boost
        implications = metadata.get("echo_implications", [])
        if implications and content_query_terms:
            for impl in implications:
                impl_terms = set(impl.lower().split()) - self._ECHO_STOP_WORDS
                if content_query_terms & impl_terms:
                    boost += 0.03

        # Cap boost at 0.3 (30% max increase)
        return min(0.3, boost)

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
                return self._truncate_rerank_text(vector_text, max_chars)
            return self._truncate_rerank_text(memory_text, max_chars)
        if strategy == "snippet":
            return self._build_rerank_snippet(
                memory_text=memory_text,
                query_terms=query_terms,
                max_chars=max_chars,
                context_lines=context_lines,
            )
        return self._truncate_rerank_text(memory_text, max_chars)

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
                return self._truncate_rerank_text(vector_text, max_chars), "vector_text"
            if normalized_strategy == "vector_text":
                # vector_text too small — fall back to full memory
                return self._truncate_rerank_text(memory_text, max_chars), "memory"

        if normalized_strategy in {"vector_or_snippet", "snippet"}:
            snippet = self._build_rerank_snippet(
                memory_text=memory_text,
                query_terms=query_terms,
                max_chars=max_chars,
                context_lines=context_lines,
            )
            if snippet and len(snippet) >= min_evidence_chars:
                return snippet, "snippet"

        return self._truncate_rerank_text(memory_text, max_chars), "memory"

    def _select_vector_text_for_memory(self, memory_id: str, query_terms: set) -> Optional[str]:
        if not memory_id:
            return None
        try:
            vector_nodes = self.vector_store.list(filters={"memory_id": memory_id})
        except Exception as e:
            logger.debug("Unable to list vector nodes for memory %s: %s", memory_id, e)
            return None
        if not vector_nodes:
            return None

        content_terms = {
            term.lower()
            for term in query_terms
            if isinstance(term, str) and len(term) > 3 and term.lower() not in self._ECHO_STOP_WORDS
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
                overlap = self._term_overlap_count(cleaned_fact, content_terms)
                fact_rank = (overlap, len(cleaned_fact), cleaned_fact)
                if fact_rank > best_fact:
                    best_fact = fact_rank

            text_value = payload.get("text")
            if isinstance(text_value, str) and text_value.strip():
                cleaned_text = text_value.strip()
                overlap = self._term_overlap_count(cleaned_text, content_terms)
                text_rank = (overlap, len(cleaned_text), cleaned_text)
                if text_rank > best_text:
                    best_text = text_rank

        if best_fact[2]:
            return best_fact[2]
        if best_text[2]:
            return best_text[2]
        return None

    @classmethod
    def _build_rerank_snippet(
        cls,
        *,
        memory_text: str,
        query_terms: set,
        max_chars: int,
        context_lines: int,
    ) -> str:
        normalized_text = str(memory_text or "")
        if not normalized_text.strip():
            return ""

        lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
        if not lines:
            return cls._truncate_rerank_text(normalized_text, max_chars)

        header_prefixes = ("session date:", "user transcript:")
        selected_indices = set()
        for idx, line in enumerate(lines):
            lowered = line.lower()
            if lowered.startswith(header_prefixes):
                selected_indices.add(idx)

        content_terms = {
            str(term).lower()
            for term in query_terms
            if isinstance(term, str) and len(term) > 3 and str(term).lower() not in cls._ECHO_STOP_WORDS
        }

        # Use at least 3 context lines around hits for better evidence quality
        effective_context = max(context_lines, 3)

        hit_found = False
        if content_terms:
            for idx, line in enumerate(lines):
                lowered = line.lower()
                if any(term in lowered for term in content_terms):
                    hit_found = True
                    start = max(0, idx - effective_context)
                    end = min(len(lines), idx + effective_context + 1)
                    selected_indices.update(range(start, end))

        if not hit_found:
            # No keyword hits — include broader coverage to avoid missing facts
            if len(lines) <= 30:
                # Short session: include everything
                selected_indices.update(range(len(lines)))
            else:
                selected_indices.update(range(0, min(len(lines), 15)))
                # Include middle section where conversational facts often appear
                mid = len(lines) // 2
                mid_start = max(0, mid - 5)
                mid_end = min(len(lines), mid + 5)
                selected_indices.update(range(mid_start, mid_end))
                tail_start = max(0, len(lines) - 10)
                selected_indices.update(range(tail_start, len(lines)))

        ordered_lines = [lines[idx] for idx in sorted(selected_indices)]
        snippet = "\n".join(ordered_lines)
        return cls._truncate_rerank_text(snippet, max_chars)

    @staticmethod
    def _truncate_rerank_text(text: str, max_chars: int) -> str:
        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            limit = 3500
        limit = max(1, limit)
        normalized = str(text or "").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip()

    @staticmethod
    def _term_overlap_count(text: str, terms: set) -> int:
        if not terms:
            return 0
        lowered = str(text or "").lower()
        return sum(1 for term in terms if term and term in lowered)

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

        # When metadata filters are present, fetch extra rows to account
        # for post-hoc filtering (same pattern as TaskManager uses limit*3).
        fetch_limit = limit
        if filters:
            fetch_limit = max(limit * 5, 200)

        memories = self.db.get_all_memories(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            app_id=app_id,
            layer=layer,
            min_strength=min_strength,
            limit=fetch_limit,
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

        if success:
            self._index_episodic_events_for_memory(
                memory_id=memory_id,
                user_id=memory.get("user_id"),
                content=content,
                metadata=metadata,
            )
            self._record_cost_counter(
                phase="write",
                user_id=memory.get("user_id"),
                llm_calls=0.0,
                input_tokens=0.0,
                output_tokens=0.0,
                embed_calls=1.0 if content_changed else 0.0,
            )

        return {"id": memory_id, "memory": content, "event": "UPDATE" if success else "ERROR"}

    def delete(self, memory_id: str) -> Dict[str, Any]:
        logger.info("Deleting memory %s (tombstone=%s)", memory_id, self.fadem_config.use_tombstone_deletion)
        memory = self.db.get_memory(memory_id)
        self.db.delete_memory(memory_id, use_tombstone=self.fadem_config.use_tombstone_deletion)
        self._delete_vectors_for_memory(memory_id)
        self._record_cost_counter(
            phase="write",
            user_id=(memory or {}).get("user_id"),
            llm_calls=0.0,
            input_tokens=0.0,
            output_tokens=0.0,
            embed_calls=0.0,
        )
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

        # Clean up entity aggregates for benchmark isolation.
        if hasattr(self.db, "delete_entity_aggregates_for_user") and user_id:
            self.db.delete_entity_aggregates_for_user(user_id)

        # Cascade-clean v3 structured tables.
        # delete() handles per-memory cleanup, but tombstoned memories leave
        # orphan rows.  Belt-and-suspenders: purge by user_id via JOIN.
        if user_id:
            try:
                with self.db._get_connection() as conn:
                    for table in ("engram_facts", "engram_context", "engram_scenes",
                                  "engram_entities", "engram_links"):
                        col = "source_memory_id" if table == "engram_links" else "memory_id"
                        try:
                            conn.execute(
                                f"DELETE FROM {table} WHERE {col} IN "
                                f"(SELECT id FROM memories WHERE user_id = ?)",
                                (user_id,),
                            )
                        except Exception:
                            pass  # Table may not exist yet
            except Exception as e:
                logger.debug("v3 table cleanup skipped: %s", e)

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

        # Distillation: episodic → semantic consolidation (the "sleep cycle")
        # This is the most impactful fusion operation per FadeMem ablation (53.7% F1 drop without it).
        # Runs after decay so it works on surviving memories only.
        distillation_stats = {}
        if (
            user_id
            and self.distillation_config
            and self.distillation_config.enable_distillation
            and self.llm
        ):
            try:
                distiller = ReplayDistiller(
                    db=self.db,
                    llm=self.llm,
                    config=self.distillation_config,
                )
                distillation_stats = distiller.run(
                    user_id=user_id,
                    memory_add_fn=self.add,
                )
                logger.info("Distillation: %s", distillation_stats)
            except Exception as e:
                logger.warning("Distillation failed: %s", e)
                distillation_stats = {"error": str(e)}

        self.db.log_decay(decayed, forgotten, promoted)
        return {
            "decayed": decayed,
            "forgotten": forgotten,
            "promoted": promoted,
            "stale_refs_removed": stale_refs_removed,
            "interference": interference_stats,
            "redundancy": redundancy_stats,
            "homeostasis": homeostasis_stats,
            "distillation": distillation_stats,
        }

    def sleep_cycle(self, user_id: str = "default") -> Dict[str, Any]:
        """Run a full sleep cycle: decay + distillation + fusion.

        Models the hippocampus-to-neocortex transfer in Complementary Learning
        Systems theory. Call this periodically (e.g. daily) to:
        1. Decay weak memories (Ebbinghaus forgetting curve)
        2. Fuse redundant memories (compression without information loss)
        3. Distill episodic → semantic knowledge (consolidation)
        4. Enforce memory budgets (homeostasis)
        """
        return self.apply_decay(scope={"user_id": user_id})

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

    # ── Dhee: Cognition + Prospective Scenes ──

    def think(
        self,
        question: str,
        user_id: str = "default",
        max_depth: Optional[int] = None,
        ask_user_fn=None,
    ):
        """Cognitive decomposition loop — memory-grounded reasoning.

        Decomposes the question, searches memory for each sub-question,
        grounds facts, and synthesizes an answer from verified facts.
        """
        if not self.cognition_engine:
            # Direct search if cognition engine not configured
            return self.search(query=question, user_id=user_id)
        return self.cognition_engine.think(
            question=question,
            user_id=user_id,
            max_depth=max_depth,
            ask_user_fn=ask_user_fn,
        )

    def get_prospective_scenes(
        self,
        user_id: str = "default",
        now_iso: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get predicted scenes that should be triggered now.

        Returns upcoming events with relevant past context — the memory
        engine proactively surfaces what you'll need before you ask.
        """
        if now_iso is None:
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

        try:
            with self.db._get_connection() as conn:
                rows = conn.execute(
                    """SELECT * FROM engram_prospective_scenes
                    WHERE user_id = ? AND status = 'predicted'
                    ORDER BY predicted_time ASC""",
                    (user_id,),
                ).fetchall()

                due_scenes = []
                for row in rows:
                    predicted_time = row["predicted_time"]
                    trigger_hours = row["trigger_window_hours"] or 24
                    if predicted_time:
                        try:
                            from datetime import datetime as dt
                            predicted = dt.fromisoformat(predicted_time.replace("Z", "+00:00"))
                            now = dt.fromisoformat(now_iso.replace("Z", "+00:00"))
                            delta_hours = (predicted - now).total_seconds() / 3600
                            if 0 <= delta_hours <= trigger_hours:
                                scene_data = dict(row)
                                # Parse JSON fields
                                import json
                                for json_field in ["participants", "predicted_needs", "relevant_past_scene_ids"]:
                                    val = scene_data.get(json_field, "[]")
                                    if isinstance(val, str):
                                        try:
                                            scene_data[json_field] = json.loads(val)
                                        except (json.JSONDecodeError, TypeError):
                                            scene_data[json_field] = []

                                # Enrich with relevant past memories
                                past_ids = scene_data.get("relevant_past_scene_ids", [])
                                if past_ids:
                                    past_memories = self.db.get_memories_bulk(past_ids)
                                    scene_data["past_context"] = [
                                        {"id": mid, "memory": m.get("memory", "")}
                                        for mid, m in past_memories.items()
                                    ]

                                due_scenes.append(scene_data)
                                # Mark as triggered
                                conn.execute(
                                    "UPDATE engram_prospective_scenes SET status = 'triggered' WHERE id = ?",
                                    (row["id"],),
                                )
                        except (ValueError, TypeError):
                            continue

                return due_scenes
        except Exception as e:
            logger.debug("Prospective scene check failed: %s", e)
            return []

    def _store_prospective_scenes(
        self,
        scenes,
        memory_id: str,
        user_id: str,
    ) -> None:
        """Store prospective scenes (predicted future events) in DB."""
        import json
        import uuid
        try:
            with self.db._get_connection() as conn:
                for scene in scenes:
                    scene_id = str(uuid.uuid4())

                    # Find relevant past scenes by searching for similar activities/participants
                    relevant_past_ids = []
                    if scene.participants or scene.event_type:
                        search_query = " ".join(scene.participants or [])
                        if scene.event_type:
                            search_query += f" {scene.event_type}"
                        if search_query.strip():
                            try:
                                past_results = self.search(
                                    query=search_query, user_id=user_id, limit=5,
                                )
                                relevant_past_ids = [
                                    r.get("id", "") for r in past_results.get("results", [])
                                    if r.get("id")
                                ]
                            except Exception:
                                pass
                    scene.relevant_past_scene_ids = relevant_past_ids

                    conn.execute(
                        """INSERT INTO engram_prospective_scenes
                        (id, memory_id, user_id, predicted_time, trigger_window_hours,
                         event_type, participants, predicted_setting, predicted_needs,
                         relevant_past_scene_ids, status, prediction_basis)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            scene_id,
                            memory_id,
                            user_id,
                            scene.predicted_time,
                            scene.trigger_window_hours,
                            scene.event_type,
                            json.dumps(scene.participants),
                            scene.predicted_setting,
                            json.dumps(scene.predicted_needs),
                            json.dumps(scene.relevant_past_scene_ids),
                            scene.status,
                            scene.prediction_basis,
                        ),
                    )
        except Exception as e:
            logger.debug("Failed to store prospective scene: %s", e)

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

        write_cost = self.db.aggregate_cost_counters(phase="write", user_id=user_id)
        query_cost = self.db.aggregate_cost_counters(phase="query", user_id=user_id)

        return {
            "total": len(memories),
            "sml_count": sml_count,
            "lml_count": lml_count,
            "avg_strength": round(avg_strength, 3),
            "echo_stats": echo_stats,
            "echo_enabled": self.echo_config.enable_echo if self.echo_config else False,
            "cost_counters": {
                "write": write_cost,
                "query": query_cost,
            },
        }

    def snapshot_cost_baseline(self, *, output_path: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Persist write/query counter aggregates for guardrail baselining."""
        write_cost = self.db.aggregate_cost_counters(phase="write", user_id=user_id)
        query_cost = self.db.aggregate_cost_counters(phase="query", user_id=user_id)

        write_samples = max(1, int(write_cost.get("samples", 0) or 0))
        write_llm_calls_per_memory = float(write_cost.get("llm_calls", 0.0) or 0.0) / float(write_samples)
        write_tokens_per_memory = (
            float(write_cost.get("input_tokens", 0.0) or 0.0)
            + float(write_cost.get("output_tokens", 0.0) or 0.0)
        ) / float(write_samples)

        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "write": write_cost,
            "query": query_cost,
            "write_llm_calls_per_memory": round(write_llm_calls_per_memory, 6),
            "write_tokens_per_memory": round(write_tokens_per_memory, 6),
        }

        out_path = str(output_path or "").strip()
        if out_path:
            out_dir = os.path.dirname(out_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        return payload

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
                       "project", "project_status", "project_tag",
                       "warroom", "warroom_message"):
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
        if not echo_result:
            return content

        # Echo-augmented embedding: compose content + echo data for richer vectors.
        # Multiple retrieval paths in one embedding — like the brain's multi-path access.
        if self.echo_config.use_echo_augmented_embedding:
            parts = [content[:1500]]  # Keep original content (capped to leave room)
            if echo_result.question_form:
                parts.append(echo_result.question_form)
            if echo_result.keywords:
                parts.append("Keywords: " + ", ".join(echo_result.keywords[:10]))
            if echo_result.paraphrases:
                parts.append(echo_result.paraphrases[0])
            return "\n".join(parts)

        # Legacy: replace content with question_form only
        if self.echo_config.use_question_embedding and echo_result.question_form:
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
        embedding_cache: Optional[Dict[str, List[float]]] = None,
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

            if vector is not None:
                emb = vector
            elif embedding_cache is not None and cleaned in embedding_cache:
                emb = embedding_cache[cleaned]
            else:
                emb = self.embedder.embed(cleaned, memory_action="add")
            vectors.append(emb)
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

        updates: List[Any] = []
        if hasattr(self.profile_processor, "extract_profile_mentions_from_speakers"):
            try:
                updates.extend(
                    self.profile_processor.extract_profile_mentions_from_speakers(
                        content=content,
                        metadata=metadata,
                    )
                )
            except Exception as e:
                logger.debug("Speaker-based profile extraction failed: %s", e)

        updates.extend(
            self.profile_processor.extract_profile_mentions(
            content=content,
            metadata=metadata,
            user_id=user_id,
            )
        )

        # Merge duplicate profile updates before applying to reduce churn.
        merged_updates: Dict[Tuple[str, str], Any] = {}
        for update in updates:
            key = (str(update.profile_name or "").strip(), str(update.profile_type or "").strip())
            existing = merged_updates.get(key)
            if existing is None:
                merged_updates[key] = update
                continue
            for fact in list(getattr(update, "new_facts", []) or []):
                if fact not in existing.new_facts:
                    existing.new_facts.append(fact)
            for pref in list(getattr(update, "new_preferences", []) or []):
                if pref not in existing.new_preferences:
                    existing.new_preferences.append(pref)
            for rel in list(getattr(update, "new_relationships", []) or []):
                if rel not in existing.new_relationships:
                    existing.new_relationships.append(rel)

        for update in merged_updates.values():
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


# Backward-compatible alias — existing code that imports Memory still works.
Memory = FullMemory
