import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from engram.configs.active import ActiveMemoryConfig


_VALID_VECTOR_PROVIDERS = {"memory", "sqlite_vec"}
_VALID_LLM_PROVIDERS = {"gemini", "openai", "nvidia", "ollama", "mock"}
_VALID_EMBEDDER_PROVIDERS = {"gemini", "openai", "nvidia", "ollama", "simple"}


class VectorStoreConfig(BaseModel):
    provider: str = Field(default="sqlite_vec")
    config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "path": os.path.join(os.path.expanduser("~"), ".engram", "sqlite_vec.db"),
            "collection_name": "fadem_memories",
        }
    )

    @field_validator("provider")
    @classmethod
    def _valid_provider(cls, v: str) -> str:
        v = str(v).strip().lower()
        if v not in _VALID_VECTOR_PROVIDERS:
            raise ValueError(f"Unknown vector store provider '{v}'. Valid: {sorted(_VALID_VECTOR_PROVIDERS)}")
        return v


class LLMConfig(BaseModel):
    provider: str = Field(default="nvidia")
    config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "model": "meta/llama-3.1-8b-instruct",
            "temperature": 0.2,
            "max_tokens": 1024,
        }
    )

    @field_validator("provider")
    @classmethod
    def _valid_provider(cls, v: str) -> str:
        v = str(v).strip().lower()
        if v not in _VALID_LLM_PROVIDERS:
            raise ValueError(f"Unknown LLM provider '{v}'. Valid: {sorted(_VALID_LLM_PROVIDERS)}")
        return v


class EmbedderConfig(BaseModel):
    provider: str = Field(default="nvidia")
    config: Dict[str, Any] = Field(default_factory=lambda: {"model": "nvidia/nv-embed-v1"})

    @field_validator("provider")
    @classmethod
    def _valid_provider(cls, v: str) -> str:
        v = str(v).strip().lower()
        if v not in _VALID_EMBEDDER_PROVIDERS:
            raise ValueError(f"Unknown embedder provider '{v}'. Valid: {sorted(_VALID_EMBEDDER_PROVIDERS)}")
        return v


class GraphStoreConfig(BaseModel):
    provider: Optional[str] = Field(default=None)
    config: Optional[Dict[str, Any]] = Field(default=None)


class KnowledgeGraphConfig(BaseModel):
    """Configuration for knowledge graph entity extraction and linking."""
    enable_graph: bool = True  # Enable knowledge graph
    use_llm_extraction: bool = False  # Use LLM for entity extraction (slower but more accurate)
    auto_link_entities: bool = True  # Automatically link memories by shared entities
    max_traversal_depth: int = 2  # Maximum depth for graph traversal in search
    graph_boost_weight: float = 0.1  # Boost for graph-related memories in search


class EchoMemConfig(BaseModel):
    """Configuration for EchoMem multi-modal encoding."""
    enable_echo: bool = True
    auto_depth: bool = True  # Auto-detect echo depth based on content importance
    default_depth: str = "medium"  # shallow, medium, deep
    reecho_on_access: bool = False  # Re-process on retrieval (expensive but strengthening)
    reecho_threshold: int = 3  # Re-echo after N accesses
    # Strength multipliers for each depth
    shallow_multiplier: float = 1.0
    medium_multiplier: float = 1.3
    deep_multiplier: float = 1.6
    # Use question_form embedding for primary vector (better query matching)
    use_question_embedding: bool = True

    @field_validator("default_depth")
    @classmethod
    def _valid_depth(cls, v: str) -> str:
        allowed = {"shallow", "medium", "deep"}
        v = str(v).strip().lower()
        if v not in allowed:
            return "medium"
        return v

    @field_validator("shallow_multiplier", "medium_multiplier", "deep_multiplier")
    @classmethod
    def _positive_multiplier(cls, v: float) -> float:
        return max(0.1, float(v))


class CategoryMemConfig(BaseModel):
    """
    Configuration for CategoryMem hierarchical category layer.

    Unlike traditional static approaches, CategoryMem provides:
    - Dynamic auto-discovered categories
    - Hierarchical structure with parent/child relationships
    - Category summaries that evolve with memories
    - Category decay (unused categories merge/fade)
    - Category-aware retrieval boosting
    """
    enable_categories: bool = True  # Enable category layer
    auto_categorize: bool = True  # Automatically categorize new memories
    use_llm_categorization: bool = True  # Use LLM for ambiguous categorization

    # Category decay (bio-inspired, like engram)
    enable_category_decay: bool = True
    category_decay_rate: float = 0.05  # Decay rate per cycle
    merge_weak_categories: bool = True  # Merge weak categories automatically
    weak_category_threshold: float = 0.3  # Strength below this triggers merge consideration

    # Summary generation
    auto_generate_summaries: bool = True  # Generate summaries for categories
    summary_update_threshold: int = 5  # Regenerate summary after N new memories

    # Retrieval boosting
    category_boost_weight: float = 0.15  # Boost for matching category in search
    cross_category_boost: float = 0.05  # Boost for related categories

    # Hierarchy
    max_category_depth: int = 3  # Maximum nesting depth
    auto_create_subcategories: bool = True  # Allow dynamic subcategory creation

    @field_validator(
        "category_decay_rate", "weak_category_threshold",
        "category_boost_weight", "cross_category_boost",
    )
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))

    @field_validator("max_category_depth")
    @classmethod
    def _clamp_depth(cls, v: int) -> int:
        return min(10, max(1, int(v)))


class SceneConfig(BaseModel):
    """Configuration for episodic scene grouping."""
    enable_scenes: bool = True
    scene_time_gap_minutes: int = 30       # gap > this = new scene
    scene_topic_threshold: float = 0.55    # cosine sim below this = topic shift
    auto_close_inactive_minutes: int = 120
    max_scene_memories: int = 50
    use_llm_summarization: bool = True
    summary_regenerate_threshold: int = 5


class ProfileConfig(BaseModel):
    """Configuration for character profile tracking."""
    enable_profiles: bool = True
    auto_detect_profiles: bool = True
    use_llm_extraction: bool = True
    narrative_regenerate_threshold: int = 10
    self_profile_auto_create: bool = True
    max_facts_per_profile: int = 100


class HandoffConfig(BaseModel):
    """Configuration for cross-agent session handoff."""
    enable_handoff: bool = True
    auto_enrich: bool = True          # LLM-enrich digests with linked memories
    max_sessions_per_user: int = 100  # retain last N sessions
    handoff_backend: str = "hosted"   # hosted|local
    strict_handoff_auth: bool = True
    allow_auto_trusted_bootstrap: bool = False
    auto_session_bus: bool = True
    auto_checkpoint_events: List[str] = Field(
        default_factory=lambda: ["tool_complete", "agent_pause", "agent_end"]
    )
    lane_inactivity_minutes: int = 240
    max_lanes_per_user: int = 50
    max_checkpoints_per_lane: int = 200
    resume_statuses: List[str] = Field(default_factory=lambda: ["active", "paused"])
    auto_trusted_agents: List[str] = Field(
        default_factory=lambda: [
            "pm",
            "design",
            "frontend",
            "backend",
            "claude-code",
            "codex",
            "chatgpt",
        ]
    )


class ScopeConfig(BaseModel):
    """Configuration for scope-aware sharing weights."""
    agent_weight: float = 1.0
    connector_weight: float = 0.97
    category_weight: float = 0.94
    global_weight: float = 0.92


class DistillationConfig(BaseModel):
    """Configuration for CLS Distillation Memory (hippocampus-neocortex consolidation)."""

    # Gap 1: Episodic/Semantic separation
    enable_memory_types: bool = True
    default_memory_type: str = "semantic"

    # Gap 2: Replay distillation
    enable_distillation: bool = True
    distillation_batch_size: int = 20
    distillation_min_episodes: int = 5
    distillation_scene_grouping: bool = True
    distillation_time_window_hours: int = 24
    max_semantic_per_batch: int = 5

    # Gap 3: Advanced forgetting
    enable_interference_pruning: bool = True
    enable_redundancy_collapse: bool = True
    enable_homeostasis: bool = True
    homeostasis_budget_per_namespace: int = 5000
    homeostasis_pressure_factor: float = 0.1
    redundancy_collapse_threshold: float = 0.92

    # Gap 4: Multi-trace strength
    enable_multi_trace: bool = True
    s_fast_weight: float = 0.2
    s_mid_weight: float = 0.3
    s_slow_weight: float = 0.5
    s_fast_decay_rate: float = 0.20
    s_mid_decay_rate: float = 0.05
    s_slow_decay_rate: float = 0.005
    cascade_fast_to_mid: float = 0.1
    cascade_mid_to_slow: float = 0.05

    # Gap 5: Intent routing
    enable_intent_routing: bool = True
    episodic_boost: float = 0.15
    semantic_boost: float = 0.15
    intersection_boost: float = 0.1

    @field_validator("default_memory_type")
    @classmethod
    def _valid_memory_type(cls, v: str) -> str:
        allowed = {"episodic", "semantic"}
        v = str(v).strip().lower()
        if v not in allowed:
            return "semantic"
        return v

    @field_validator(
        "homeostasis_pressure_factor", "redundancy_collapse_threshold",
        "s_fast_weight", "s_mid_weight", "s_slow_weight",
        "s_fast_decay_rate", "s_mid_decay_rate", "s_slow_decay_rate",
        "cascade_fast_to_mid", "cascade_mid_to_slow",
        "episodic_boost", "semantic_boost", "intersection_boost",
    )
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))

    @field_validator("homeostasis_budget_per_namespace", "distillation_batch_size",
                     "distillation_min_episodes", "distillation_time_window_hours",
                     "max_semantic_per_batch")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        return max(1, int(v))


class TaskConfig(BaseModel):
    """Configuration for tasks as first-class Engram memories."""
    enable_tasks: bool = True
    task_namespace: str = "tasks"
    default_priority: str = "normal"
    active_task_decay_rate: float = 0.0       # active tasks don't decay
    completed_task_decay_rate: float = 0.30   # done tasks decay 2x faster
    archived_task_decay_rate: float = 0.15    # normal rate
    task_category_prefix: str = "tasks"
    auto_archive_completed_days: int = 7

    @field_validator("default_priority")
    @classmethod
    def _valid_priority(cls, v: str) -> str:
        allowed = {"low", "normal", "high", "urgent"}
        v = str(v).strip().lower()
        if v not in allowed:
            return "normal"
        return v


class BatchConfig(BaseModel):
    """Configuration for batch memory operations."""
    enable_batch: bool = False    # off by default
    max_batch_size: int = 20
    batch_echo: bool = True
    batch_embed: bool = True
    batch_category: bool = True

    @field_validator("max_batch_size")
    @classmethod
    def _clamp_batch_size(cls, v: int) -> int:
        return min(100, max(1, int(v)))


class ParallelConfig(BaseModel):
    """Configuration for parallel I/O execution (ThreadPoolExecutor)."""
    enable_parallel: bool = False   # off by default
    max_workers: int = 4
    parallel_add: bool = True       # echo + category in parallel during add()
    parallel_reecho: bool = True    # parallel re-echo during search()
    parallel_decay: bool = True     # parallel interference + redundancy during apply_decay()

    @field_validator("max_workers")
    @classmethod
    def _clamp_workers(cls, v: int) -> int:
        return min(32, max(1, int(v)))


class FadeMemConfig(BaseModel):
    enable_forgetting: bool = True
    sml_decay_rate: float = 0.15
    lml_decay_rate: float = 0.02
    access_dampening_factor: float = 0.5
    promotion_access_threshold: int = 3
    promotion_strength_threshold: float = 0.7
    forgetting_threshold: float = 0.1
    access_strength_boost: float = 0.02
    conflict_similarity_threshold: float = 0.85
    fusion_similarity_threshold: float = 0.90
    enable_fusion: bool = True
    use_tombstone_deletion: bool = True

    @field_validator(
        "sml_decay_rate", "lml_decay_rate", "access_dampening_factor",
        "promotion_strength_threshold", "forgetting_threshold",
        "access_strength_boost", "conflict_similarity_threshold",
        "fusion_similarity_threshold",
    )
    @classmethod
    def _clamp_unit_float(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))

    @field_validator("promotion_access_threshold")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        return max(1, int(v))


class MemoryConfig(BaseModel):
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    graph_store: GraphStoreConfig = Field(default_factory=GraphStoreConfig)
    history_db_path: str = Field(
        default_factory=lambda: os.path.join(os.path.expanduser("~"), ".engram", "history.db")
    )
    collection_name: str = "fadem_memories"
    embedding_model_dims: int = 4096  # nvidia/nv-embed-v1 default dimensions
    version: str = "v1.4"  # Updated for CLS Distillation Memory
    custom_fact_extraction_prompt: Optional[str] = None
    custom_conflict_prompt: Optional[str] = None
    custom_fusion_prompt: Optional[str] = None
    custom_echo_prompt: Optional[str] = None
    custom_category_prompt: Optional[str] = None
    engram: FadeMemConfig = Field(default_factory=FadeMemConfig)
    echo: EchoMemConfig = Field(default_factory=EchoMemConfig)
    category: CategoryMemConfig = Field(default_factory=CategoryMemConfig)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    graph: KnowledgeGraphConfig = Field(default_factory=KnowledgeGraphConfig)
    scene: SceneConfig = Field(default_factory=SceneConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    handoff: HandoffConfig = Field(default_factory=HandoffConfig)
    active: ActiveMemoryConfig = Field(default_factory=ActiveMemoryConfig)
    distillation: DistillationConfig = Field(default_factory=DistillationConfig)
    parallel: ParallelConfig = Field(default_factory=ParallelConfig)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    task: TaskConfig = Field(default_factory=TaskConfig)

    @field_validator("embedding_model_dims")
    @classmethod
    def _valid_dims(cls, v: int) -> int:
        v = int(v)
        if v < 1 or v > 65536:
            raise ValueError(f"embedding_model_dims must be 1-65536, got {v}")
        return v
