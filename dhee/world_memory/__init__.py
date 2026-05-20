"""World-memory primitives for screen-aware desktop memory.

This subsystem keeps transition-oriented screen/app memory separate from
the main Dhee text-memory tables while mirroring compact summaries back
into Dhee for durable retrieval and cognition.
"""

from .capture_store import CaptureStore
from .encoder import (
    ContentAwareFrameEncoder,
    DeterministicFrameEncoder,
    NvidiaVLFrameEncoder,
    create_default_encoder,
)
from .predictor import ActionConditionedPredictor, compute_surprise
from .causal_graph import CausalGraphProjection
from .gem_extractor import (
    GEM_SCHEMA_VERSION,
    MemoryGem,
    extract_memory_gems,
    score_memory_gem,
    submit_gem_learning_candidates,
    submit_projected_gem_learning_candidate,
    summarize_gems,
    write_gem_raw_events,
)
from .schema import (
    CAUSAL_PROJECTION_VERSION,
    CAUSAL_SCHEMA_VERSION,
    ActionTransition,
    CausalEdge,
    CaptureAction,
    CaptureEvent,
    CaptureLink,
    CapturePolicy,
    CaptureSession,
    CapturedArtifact,
    CapturedObservation,
    CapturedSurface,
    CheckpointReport,
    EvidenceChunk,
    EventFrame,
    RawEvent,
    RetrievalTrace,
    TransitionMatch,
    WorldState,
)
from .session_graph import SessionGraphStore
from .service import MemoryOSService
from .store import WorldMemoryStore

__all__ = [
    "ActionConditionedPredictor",
    "ActionTransition",
    "CAUSAL_PROJECTION_VERSION",
    "CAUSAL_SCHEMA_VERSION",
    "CausalEdge",
    "CausalGraphProjection",
    "GEM_SCHEMA_VERSION",
    "CaptureAction",
    "CaptureEvent",
    "CaptureLink",
    "CapturePolicy",
    "CaptureSession",
    "CaptureStore",
    "CapturedArtifact",
    "CapturedObservation",
    "CapturedSurface",
    "CheckpointReport",
    "ContentAwareFrameEncoder",
    "DeterministicFrameEncoder",
    "EvidenceChunk",
    "EventFrame",
    "MemoryGem",
    "MemoryOSService",
    "NvidiaVLFrameEncoder",
    "SessionGraphStore",
    "RawEvent",
    "RetrievalTrace",
    "TransitionMatch",
    "WorldMemoryStore",
    "WorldState",
    "compute_surprise",
    "create_default_encoder",
    "extract_memory_gems",
    "score_memory_gem",
    "submit_gem_learning_candidates",
    "submit_projected_gem_learning_candidate",
    "summarize_gems",
    "write_gem_raw_events",
]
