"""Universal Engram — the atomic unit of structured memory in Dhee.

Two layers:
- Context Layer: HOW you find this memory (era -> place -> time -> activity)
- Content Layer: WHAT it is (facts, entities, associative links)

Mirrors human episodic memory: context narrows first, then content surfaces.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ContextAnchor:
    """HOW you find this memory — hierarchical context filtering.
    Mirrors human recall: era -> place -> time -> activity -> memory."""
    era: Optional[str] = None              # life phase: "ghazipur_school", "bengaluru_work"
    place: Optional[str] = None            # spatial anchor: "Ghazipur", "Bengaluru"
    place_type: Optional[str] = None       # home|office|travel|school|city
    place_detail: Optional[str] = None     # specific: "grandparents' home", "office 3rd floor"
    time_absolute: Optional[str] = None    # ISO datetime (resolved or derived)
    time_markers: List[str] = field(default_factory=list)  # original refs: ["last Thursday", "class 9"]
    time_range_start: Optional[str] = None # era/phase start
    time_range_end: Optional[str] = None   # era/phase end (None = ongoing)
    time_derivation: Optional[str] = None  # HOW the time was derived
    activity: Optional[str] = None         # activity type: coding|meeting|travel|movie|exam
    session_id: Optional[str] = None
    session_position: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "era": self.era,
            "place": self.place,
            "place_type": self.place_type,
            "place_detail": self.place_detail,
            "time_absolute": self.time_absolute,
            "time_markers": self.time_markers,
            "time_range_start": self.time_range_start,
            "time_range_end": self.time_range_end,
            "time_derivation": self.time_derivation,
            "activity": self.activity,
            "session_id": self.session_id,
            "session_position": self.session_position,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextAnchor":
        if not data:
            return cls()
        return cls(
            era=data.get("era"),
            place=data.get("place"),
            place_type=data.get("place_type"),
            place_detail=data.get("place_detail"),
            time_absolute=data.get("time_absolute"),
            time_markers=data.get("time_markers", []),
            time_range_start=data.get("time_range_start"),
            time_range_end=data.get("time_range_end"),
            time_derivation=data.get("time_derivation"),
            activity=data.get("activity"),
            session_id=data.get("session_id"),
            session_position=data.get("session_position", 0),
        )

    def has_context(self) -> bool:
        """Return True if any context field is populated."""
        return bool(
            self.era or self.place or self.time_absolute
            or self.activity or self.time_markers
        )


@dataclass
class SceneSnapshot:
    """VISUAL scene representation — who/where/what/state at that moment.
    Humans recall memories as SCENES, not facts."""
    setting: Optional[str] = None          # "grandparents' home in Varanasi"
    people_present: List[str] = field(default_factory=list)
    self_state: Optional[str] = None       # "recovering from appendix operation"
    emotional_tone: Optional[str] = None   # "relaxed", "anxious", "celebratory"
    sensory_cues: List[str] = field(default_factory=list)  # triggers recall

    def to_dict(self) -> Dict[str, Any]:
        return {
            "setting": self.setting,
            "people_present": self.people_present,
            "self_state": self.self_state,
            "emotional_tone": self.emotional_tone,
            "sensory_cues": self.sensory_cues,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneSnapshot":
        if not data:
            return cls()
        return cls(
            setting=data.get("setting"),
            people_present=data.get("people_present", []),
            self_state=data.get("self_state"),
            emotional_tone=data.get("emotional_tone"),
            sensory_cues=data.get("sensory_cues", []),
        )


@dataclass
class AssociativeLink:
    """Connections between memories — causal, temporal, emotional chains.
    These chains enable DERIVED reasoning (dates, sequences, causality)."""
    target_memory_id: Optional[str] = None
    target_canonical_key: str = ""
    link_type: str = "co_occurring"        # causal|temporal_sequence|co_occurring|emotional|elaborates
    direction: str = "forward"             # forward|backward
    qualifier: Optional[str] = None

    _VALID_LINK_TYPES = frozenset({
        "causal", "temporal_sequence", "co_occurring", "emotional", "elaborates",
    })
    _VALID_DIRECTIONS = frozenset({"forward", "backward"})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_memory_id": self.target_memory_id,
            "target_canonical_key": self.target_canonical_key,
            "link_type": self.link_type,
            "direction": self.direction,
            "qualifier": self.qualifier,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssociativeLink":
        if not data:
            return cls()
        link_type = data.get("link_type", "co_occurring")
        if link_type not in cls._VALID_LINK_TYPES:
            link_type = "co_occurring"
        direction = data.get("direction", "forward")
        if direction not in cls._VALID_DIRECTIONS:
            direction = "forward"
        return cls(
            target_memory_id=data.get("target_memory_id"),
            target_canonical_key=data.get("target_canonical_key", ""),
            link_type=link_type,
            direction=direction,
            qualifier=data.get("qualifier"),
        )


@dataclass
class Fact:
    """Atomic unit of knowledge. Deterministic queries run over these."""
    subject: str = ""
    predicate: str = ""
    value: str = ""
    value_numeric: Optional[float] = None
    value_unit: Optional[str] = None
    time: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None       # None = still true
    qualifier: Optional[str] = None
    canonical_key: str = ""                 # "user|visited|tokyo" — dedup key
    confidence: float = 1.0
    is_derived: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "value_numeric": self.value_numeric,
            "value_unit": self.value_unit,
            "time": self.time,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "qualifier": self.qualifier,
            "canonical_key": self.canonical_key,
            "confidence": self.confidence,
            "is_derived": self.is_derived,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Fact":
        if not data:
            return cls()
        return cls(
            subject=data.get("subject", ""),
            predicate=data.get("predicate", ""),
            value=data.get("value", ""),
            value_numeric=data.get("value_numeric"),
            value_unit=data.get("value_unit"),
            time=data.get("time"),
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            qualifier=data.get("qualifier"),
            canonical_key=data.get("canonical_key", ""),
            confidence=data.get("confidence", 1.0),
            is_derived=data.get("is_derived", False),
        )

    def make_canonical_key(self) -> str:
        """Generate canonical_key from subject|predicate|value."""
        parts = [
            (self.subject or "").strip().lower().replace(" ", "_"),
            (self.predicate or "").strip().lower().replace(" ", "_"),
            (self.value or "").strip().lower().replace(" ", "_"),
        ]
        return "|".join(p for p in parts if p)


@dataclass
class EntityRef:
    """Entity mention with state and relationships."""
    name: str = ""
    entity_type: str = "unknown"           # person|org|technology|location|project|tool
    state: Optional[str] = None            # "current", "former", "planned"
    relationships: List[Dict[str, str]] = field(default_factory=list)

    _VALID_ENTITY_TYPES = frozenset({
        "person", "org", "technology", "location", "project", "tool", "unknown",
    })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entity_type": self.entity_type,
            "state": self.state,
            "relationships": self.relationships,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EntityRef":
        if not data:
            return cls()
        entity_type = data.get("entity_type", "unknown")
        if entity_type not in cls._VALID_ENTITY_TYPES:
            entity_type = "unknown"
        return cls(
            name=data.get("name", ""),
            entity_type=entity_type,
            state=data.get("state"),
            relationships=data.get("relationships", []),
        )


@dataclass
class ProspectiveScene:
    """A predicted future scene — memory-driven anticipation.

    When a user says "we'll play tennis next Saturday with Ankit",
    the memory engine doesn't create a todo. It creates a PREDICTED SCENE:
    - Links to past similar scenes (prior tennis, prior Ankit interactions)
    - Predicts what you'll need based on past patterns
    - When the date approaches, proactively surfaces relevant context

    This mirrors how human memory works: you don't set a reminder to "bring
    racket" — you VISUALIZE the upcoming scene, and your brain auto-surfaces
    what you'll need from similar past scenes.
    """
    predicted_time: Optional[str] = None   # ISO datetime of predicted event
    trigger_window_hours: int = 24         # Surface this N hours before
    event_type: Optional[str] = None       # meeting|activity|travel|deadline|social
    participants: List[str] = field(default_factory=list)
    predicted_setting: Optional[str] = None  # where (from past patterns)
    predicted_needs: List[str] = field(default_factory=list)  # from past similar events
    relevant_past_scene_ids: List[str] = field(default_factory=list)
    status: str = "predicted"              # predicted|triggered|occurred|cancelled
    source_memory_id: str = ""
    prediction_basis: Optional[str] = None  # HOW this was predicted

    _VALID_STATUSES = frozenset({"predicted", "triggered", "occurred", "cancelled"})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predicted_time": self.predicted_time,
            "trigger_window_hours": self.trigger_window_hours,
            "event_type": self.event_type,
            "participants": self.participants,
            "predicted_setting": self.predicted_setting,
            "predicted_needs": self.predicted_needs,
            "relevant_past_scene_ids": self.relevant_past_scene_ids,
            "status": self.status,
            "source_memory_id": self.source_memory_id,
            "prediction_basis": self.prediction_basis,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProspectiveScene":
        if not data:
            return cls()
        status = data.get("status", "predicted")
        if status not in cls._VALID_STATUSES:
            status = "predicted"
        return cls(
            predicted_time=data.get("predicted_time"),
            trigger_window_hours=data.get("trigger_window_hours", 24),
            event_type=data.get("event_type"),
            participants=data.get("participants", []),
            predicted_setting=data.get("predicted_setting"),
            predicted_needs=data.get("predicted_needs", []),
            relevant_past_scene_ids=data.get("relevant_past_scene_ids", []),
            status=status,
            source_memory_id=data.get("source_memory_id", ""),
            prediction_basis=data.get("prediction_basis"),
        )

    def is_due(self, now_iso: str) -> bool:
        """Check if this scene should be triggered (within trigger window)."""
        if not self.predicted_time or self.status != "predicted":
            return False
        try:
            predicted = datetime.fromisoformat(self.predicted_time.replace("Z", "+00:00"))
            now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            delta_hours = (predicted - now).total_seconds() / 3600
            return 0 <= delta_hours <= self.trigger_window_hours
        except (ValueError, TypeError):
            return False


@dataclass
class UniversalEngram:
    """The atomic unit of structured memory in Dhee.
    Four layers: Context (how you find it), Scene (what you see),
    Content (what you know), Prospective (what you anticipate)."""
    id: str = ""
    raw_content: str = ""                  # original text (backward compat + vector search)

    # Context Layer (hierarchical retrieval path: era -> place -> time -> activity)
    context: ContextAnchor = field(default_factory=ContextAnchor)

    # Scene Layer (visual reconstruction)
    scene: SceneSnapshot = field(default_factory=SceneSnapshot)

    # Content Layer (structured knowledge)
    facts: List[Fact] = field(default_factory=list)
    entities: List[EntityRef] = field(default_factory=list)
    links: List[AssociativeLink] = field(default_factory=list)
    echo: List[str] = field(default_factory=list)  # semantic variants

    # Prospective Layer (predicted future scenes)
    prospective_scenes: List[ProspectiveScene] = field(default_factory=list)

    # Existing Engram powers (preserved)
    salience: Dict[str, float] = field(default_factory=dict)
    scene_id: Optional[str] = None
    memory_type: str = "episodic"          # episodic|semantic
    strength: float = 1.0
    traces: Dict[str, float] = field(default_factory=lambda: {
        "s_fast": 1.0, "s_mid": 0.5, "s_slow": 0.2,
    })

    # Metadata
    content_hash: str = ""
    created_at: str = ""
    user_id: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.content_hash and self.raw_content:
            self.content_hash = hashlib.sha256(self.raw_content.encode()).hexdigest()
        # Auto-generate canonical keys for facts that lack them
        for fact in self.facts:
            if not fact.canonical_key:
                fact.canonical_key = fact.make_canonical_key()

    def to_dict(self, *, sparse: bool = False) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "raw_content": self.raw_content,
            "context": self.context.to_dict(),
            "scene": self.scene.to_dict(),
            "facts": [f.to_dict() for f in self.facts],
            "entities": [e.to_dict() for e in self.entities],
            "links": [l.to_dict() for l in self.links],
            "echo": self.echo,
            "prospective_scenes": [p.to_dict() for p in self.prospective_scenes],
            "salience": self.salience,
            "scene_id": self.scene_id,
            "memory_type": self.memory_type,
            "strength": self.strength,
            "traces": self.traces,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "user_id": self.user_id,
            "metadata": self.metadata,
        }
        if sparse:
            d = {
                k: v for k, v in d.items()
                if v is not None and v != "" and v != [] and v != {}
            }
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UniversalEngram":
        if not data:
            return cls()
        return cls(
            id=data.get("id", ""),
            raw_content=data.get("raw_content", ""),
            context=ContextAnchor.from_dict(data.get("context", {})),
            scene=SceneSnapshot.from_dict(data.get("scene", {})),
            facts=[Fact.from_dict(f) for f in data.get("facts", [])],
            entities=[EntityRef.from_dict(e) for e in data.get("entities", [])],
            links=[AssociativeLink.from_dict(l) for l in data.get("links", [])],
            echo=data.get("echo", []),
            prospective_scenes=[
                ProspectiveScene.from_dict(p) for p in data.get("prospective_scenes", [])
            ],
            salience=data.get("salience", {}),
            scene_id=data.get("scene_id"),
            memory_type=data.get("memory_type", "episodic"),
            strength=data.get("strength", 1.0),
            traces=data.get("traces", {"s_fast": 1.0, "s_mid": 0.5, "s_slow": 0.2}),
            content_hash=data.get("content_hash", ""),
            created_at=data.get("created_at", ""),
            user_id=data.get("user_id", "default"),
            metadata=data.get("metadata", {}),
        )

    def get_due_prospective_scenes(self, now_iso: str) -> List[ProspectiveScene]:
        """Get prospective scenes that should be triggered now."""
        return [p for p in self.prospective_scenes if p.is_due(now_iso)]

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_json(cls, json_str: str) -> "UniversalEngram":
        return cls.from_dict(json.loads(json_str))
