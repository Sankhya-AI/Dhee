from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SceneStartModel(BaseModel):
    schema_version: str = "dhee.scene.v1"
    id: str
    episode_id: str
    user_id: str = "default"
    agent_id: str
    agent_category: str = "agent"
    source_app: str = "dhee"
    namespace: str = "default"
    hero_character_id: Optional[str] = None
    title: str
    summary: str
    intent_type: str = "question_answer"
    action_lane: str = "answer"
    categories: List[str] = Field(default_factory=list)
    markers: Dict[str, Any] = Field(default_factory=dict)


class SceneCardClaimModel(BaseModel):
    kind: str
    claim: str
    confidence: float = 0.5
    valid_until: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    validity: str = "until_superseded"


class SceneCardModel(BaseModel):
    schema_version: str = "dhee.scene_card.v1"
    id: str
    scene_id: str
    episode_id: Optional[str] = None
    user_id: str = "default"
    agent_id: Optional[str] = None
    agent_category: Optional[str] = None
    namespace: str = "default"
    categories: List[str] = Field(default_factory=list)
    retrieval_tags: List[str] = Field(default_factory=list)
    summary: str
    claims: List[SceneCardClaimModel] = Field(default_factory=list)
    durable_facts: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    procedures: List[str] = Field(default_factory=list)
    success_patterns: List[str] = Field(default_factory=list)
    failure_patterns: List[str] = Field(default_factory=list)
    open_loops: List[str] = Field(default_factory=list)
    entities: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    importance: float = 0.5
    confidence: float = 0.5
    reuse_policy: str = "private"
    visibility_scope: str = "private"
    privacy_class: str = "user_private"
    evidence_refs: List[Any] = Field(default_factory=list)
    do_not_use_for: List[str] = Field(default_factory=list)


class SceneContextModel(BaseModel):
    schema_version: str = "dhee.scene_context.v1"
    current_episode_id: Optional[str] = None
    same_episode_scenes: List[Dict[str, Any]] = Field(default_factory=list)
    similar_past_scenes: List[Dict[str, Any]] = Field(default_factory=list)
    cross_agent_scenes: List[Dict[str, Any]] = Field(default_factory=list)
    included_cards: List[Dict[str, Any]] = Field(default_factory=list)
    failure_patterns: List[str] = Field(default_factory=list)
    success_patterns: List[str] = Field(default_factory=list)
    open_loops: List[str] = Field(default_factory=list)
    rejected: List[Dict[str, str]] = Field(default_factory=list)
    compact_context: str = ""
    retrieval_policy: Dict[str, Any] = Field(default_factory=dict)
