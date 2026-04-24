from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WorldState:
    id: str
    frame_ref: str
    latent: List[float]
    user_id: str = "default"
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionTransition:
    id: str
    ptr: str
    source_state_id: str
    target_state_id: str
    action_type: str
    action_payload: Dict[str, Any] = field(default_factory=dict)
    instruction_context: str = ""
    action_trace: List[str] = field(default_factory=list)
    predicted_next_latent: List[float] = field(default_factory=list)
    surprise: float = 0.0
    user_id: str = "default"
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceChunk:
    id: str
    state_id: str
    chunk_type: str
    role: str
    label: str
    text: str
    selector_hint: str
    position: int
    embedding: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionMatch:
    transition: ActionTransition
    source_state: WorldState
    target_state: WorldState
    score: float
    latent_score: float
    instruction_score: float
    action_score: float
    surprise_score: float
    evidence_score: float = 0.0
    evidence_chunks: List[EvidenceChunk] = field(default_factory=list)


@dataclass
class CaptureSession:
    id: str
    user_id: str = "default"
    source_app: str = ""
    namespace: str = ""
    status: str = "active"
    started_at: str = ""
    ended_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptureEvent:
    id: str
    session_id: str
    user_id: str = "default"
    source_app: str = ""
    namespace: str = ""
    event_type: str = ""
    created_at: str = ""
    text_payload: str = ""
    structured_payload: Dict[str, Any] = field(default_factory=dict)
    window_title: str = ""
    url: str = ""
    action_type: str = ""
    action_payload: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    source_kind: str = ""
    world_ptr: Optional[str] = None
    memory_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptureAction:
    id: str
    session_id: str
    user_id: str = "default"
    source_app: str = ""
    namespace: str = ""
    created_at: str = ""
    action_type: str = ""
    target: Dict[str, Any] = field(default_factory=dict)
    surface_id: str = ""
    capture_mode: str = "dom"
    confidence: float = 0.0
    world_ptr: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CapturedSurface:
    id: str
    session_id: str
    user_id: str = "default"
    source_app: str = ""
    namespace: str = ""
    surface_type: str = "page"
    title: str = ""
    url: str = ""
    app_path: str = ""
    content_hash: str = ""
    path_hint: List[str] = field(default_factory=list)
    parent_surface_id: Optional[str] = None
    first_seen_at: str = ""
    last_seen_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CapturedObservation:
    id: str
    session_id: str
    surface_id: str
    user_id: str = "default"
    source_app: str = ""
    namespace: str = ""
    created_at: str = ""
    action_id: Optional[str] = None
    source_kind: str = "dom"
    kind: str = "capture"
    text: str = ""
    structured: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    memory_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CapturedArtifact:
    id: str
    session_id: str
    surface_id: str
    user_id: str = "default"
    source_app: str = ""
    created_at: str = ""
    action_id: Optional[str] = None
    artifact_type: str = "screenshot"
    path: str = ""
    mime_type: str = ""
    sha256: str = ""
    ttl_hours: int = 48
    expires_at: str = ""
    retention: str = "temporary"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptureLink:
    id: str
    session_id: str
    user_id: str = "default"
    source_app: str = ""
    created_at: str = ""
    from_id: str = ""
    to_id: str = ""
    relation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CapturePolicy:
    source_app: str
    enabled: bool = True
    mode: str = "sampled"
    metadata: Dict[str, Any] = field(default_factory=dict)
