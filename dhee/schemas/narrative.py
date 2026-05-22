from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SeriesModel(BaseModel):
    schema_version: str = "dhee.series.v1"
    id: str
    user_id: str = "default"
    namespace: str = "personal"
    title: str
    theme: str
    purpose: str
    ultimate_goal: Optional[str] = None
    hero_identity: Optional[str] = None
    desired_identity: Optional[str] = None
    core_values: List[str] = Field(default_factory=list)
    long_term_conflicts: List[str] = Field(default_factory=list)
    current_active_season: Optional[str] = None
    arc_summary: str = ""
    active_tensions: List[str] = Field(default_factory=list)
    latest_season_signal: Optional[str] = None
    deterministic_rollup: Dict[str, Any] = Field(default_factory=dict)
    llm_rollup: Dict[str, Any] = Field(default_factory=dict)
    rollup_model: Optional[str] = None
    rollup_prompt_version: Optional[str] = None
    rollup_source_scene_card_ids: List[str] = Field(default_factory=list)
    rollup_input_hash: Optional[str] = None
    status: str = "active"
    confidence: float = 0.5


class SeasonModel(BaseModel):
    schema_version: str = "dhee.season.v1"
    id: str
    series_id: str
    user_id: str = "default"
    namespace: str = "default"
    title: str
    theme: str
    arc_summary: str
    major_goal: Optional[str] = None
    dominant_struggle: Optional[str] = None
    transformation_expected: Optional[str] = None
    open_threads: List[str] = Field(default_factory=list)
    deterministic_rollup: Dict[str, Any] = Field(default_factory=dict)
    llm_rollup: Dict[str, Any] = Field(default_factory=dict)
    rollup_model: Optional[str] = None
    rollup_prompt_version: Optional[str] = None
    rollup_source_scene_card_ids: List[str] = Field(default_factory=list)
    rollup_input_hash: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    status: str = "active"
    confidence: float = 0.5


class StoryCharacterModel(BaseModel):
    schema_version: str = "dhee.character.v1"
    id: str
    user_id: str = "default"
    namespace: str = "personal"
    name: str
    character_type: str
    description: str
    stable_identity_ref: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    influence: float = 0.5
    trust_level: float = 0.5
    lessons_learned: List[str] = Field(default_factory=list)


class EpisodeModel(BaseModel):
    schema_version: str = "dhee.episode.v1"
    id: str
    user_id: str = "default"
    namespace: str = "default"
    local_date: str
    timezone: str
    title: str
    summary: str
    series_id: Optional[str] = None
    season_id: Optional[str] = None
    primary_hero_id: Optional[str] = None
    goal: Optional[str] = None
    conflict: Optional[str] = None
    key_decisions: List[str] = Field(default_factory=list)
    unresolved_threads: List[str] = Field(default_factory=list)
    story_progress: Optional[str] = None
    category_summaries: Dict[str, str] = Field(default_factory=dict)
    deterministic_rollup: Dict[str, Any] = Field(default_factory=dict)
    llm_rollup: Dict[str, Any] = Field(default_factory=dict)
    rollup_model: Optional[str] = None
    rollup_prompt_version: Optional[str] = None
    rollup_source_scene_card_ids: List[str] = Field(default_factory=list)
    rollup_input_hash: Optional[str] = None
    agent_ids: List[str] = Field(default_factory=list)
    scene_ids: List[str] = Field(default_factory=list)
    open_loops: List[str] = Field(default_factory=list)
    status: str = "open"


class NarrativeRollupModel(BaseModel):
    schema_version: str = "dhee.narrative_rollup.v1"
    scope_type: str
    scope_id: str
    arc_summary: str
    active_tensions: List[str] = Field(default_factory=list)
    latest_signal: Optional[str] = None
    open_threads: List[str] = Field(default_factory=list)
    resolved_threads: List[str] = Field(default_factory=list)
    likely_next_steps: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    evidence_card_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.5
    advisory_only: bool = True


class NarrativePriorModel(BaseModel):
    schema_version: str = "dhee.narrative_prior.v1"
    series_id: Optional[str] = None
    season_id: Optional[str] = None
    episode_id: Optional[str] = None
    series_theme: str
    season_theme: str
    episode_goal: str
    scene_tension: Dict[str, Any] = Field(default_factory=dict)
    likely_next_beats: List[str] = Field(default_factory=list)
    likely_failure_modes: List[str] = Field(default_factory=list)
    best_next_action: str
    evidence_scene_cards: List[Dict[str, Any]] = Field(default_factory=list)
    anticipation_trace: List[Dict[str, Any]] = Field(default_factory=list)
    guardrails: List[str] = Field(default_factory=list)
    proof_gate_status: str = "not_required"
    confidence: float = 0.5
    soft_prior: bool = True
    advisory_only: bool = True
