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
from .schema import (
    ActionTransition,
    CaptureAction,
    CaptureEvent,
    CaptureLink,
    CapturePolicy,
    CaptureSession,
    CapturedArtifact,
    CapturedObservation,
    CapturedSurface,
    EvidenceChunk,
    TransitionMatch,
    WorldState,
)
from .session_graph import SessionGraphStore
from .service import MemoryOSService
from .store import WorldMemoryStore

__all__ = [
    "ActionConditionedPredictor",
    "ActionTransition",
    "CaptureAction",
    "CaptureEvent",
    "CaptureLink",
    "CapturePolicy",
    "CaptureSession",
    "CaptureStore",
    "CapturedArtifact",
    "CapturedObservation",
    "CapturedSurface",
    "ContentAwareFrameEncoder",
    "DeterministicFrameEncoder",
    "EvidenceChunk",
    "MemoryOSService",
    "NvidiaVLFrameEncoder",
    "SessionGraphStore",
    "TransitionMatch",
    "WorldMemoryStore",
    "WorldState",
    "compute_surprise",
    "create_default_encoder",
]
