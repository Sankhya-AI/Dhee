"""Buddhi — Proactive cognition layer that turns any agent into a HyperAgent.

Inspired by Meta's DGM-Hyperagents (Zhang et al., 2026): agents that emergently
develop persistent memory and performance tracking achieve self-accelerating
improvement that transfers across domains.

DGM-H agents had to DISCOVER they needed these capabilities. Dhee provides them
as infrastructure — so any agent (Claude, GPT, Gemini, custom) gets HyperAgent
capabilities from day one.

Mapping from DGM-H → Dhee:
  PerformanceTracker → Samskara (12 signal types) + Viveka (5-kosha assessment)
  Persistent Memory  → Engram extraction + insight synthesis + skill archive
  Archive            → Skill store (L1 procedures, grounded in trajectories)
  Meta-agent         → Buddhi (this module) — proactive, not reactive

The key API contract:
  Agent calls `hyper_context(task_description)` at session start →
  Dhee returns EVERYTHING the agent needs to be a HyperAgent:
    - Performance history for this task type
    - Synthesized insights from prior runs (not raw memories)
    - Relevant skills/strategies with confidence scores
    - Proactive warnings (known pitfalls, regressions)
    - Pending intentions (stored future triggers)

Zero LLM calls for the hot path. Pure pattern matching + statistics.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dhee.core.intention import Intention  # re-export for backward compat

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Insight:
    """A synthesized insight from past performance — NOT a raw memory.

    DGM-H's key finding: agents that store causal hypotheses and
    forward-looking plans (not just scores) transfer across domains.
    """
    id: str
    user_id: str
    content: str                    # "Strict criteria + balanced scoring works best"
    insight_type: str               # "causal" | "warning" | "strategy" | "pattern"
    source_task_types: List[str]    # task types this was derived from
    confidence: float               # 0-1, updated by outcomes
    created_at: str
    last_validated: str             # when this insight last proved useful
    validation_count: int           # how many times validated
    invalidation_count: int         # how many times contradicted
    tags: List[str]

    def strength(self) -> float:
        """Net strength: validated - invalidated, normalized."""
        total = self.validation_count + self.invalidation_count
        if total == 0:
            return self.confidence
        return self.confidence * (self.validation_count / total)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "type": self.insight_type,
            "confidence": round(self.confidence, 3),
            "strength": round(self.strength(), 3),
            "source_task_types": self.source_task_types,
            "validations": self.validation_count,
            "tags": self.tags,
        }


@dataclass
class PerformanceSnapshot:
    """Performance record for a task type — the PerformanceTracker from DGM-H."""
    task_type: str
    scores: List[float]
    timestamps: List[str]
    trend: float                    # positive = improving, negative = regressing
    best_score: float
    worst_score: float
    avg_score: float
    total_attempts: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_type": self.task_type,
            "trend": round(self.trend, 4),
            "best_score": round(self.best_score, 3),
            "avg_score": round(self.avg_score, 3),
            "total_attempts": self.total_attempts,
            "recent_scores": [round(s, 3) for s in self.scores[-5:]],
            "improving": self.trend > 0,
        }


@dataclass
class HyperContext:
    """Everything an agent needs to be a HyperAgent.

    Returned by buddhi.get_hyper_context() — the single entry point
    that replaces the passive engram_context tool.
    """
    # Who
    user_id: str
    session_id: Optional[str]

    # Last session state
    last_session: Optional[Dict[str, Any]]

    # Performance context (DGM-H's PerformanceTracker)
    performance: List[PerformanceSnapshot]

    # Synthesized insights (DGM-H's persistent memory)
    insights: List[Insight]

    # Relevant skills/strategies from the archive
    skills: List[Dict[str, Any]]

    # Pending intentions (prospective memory)
    intentions: List[Intention]

    # Proactive warnings
    warnings: List[str]

    # Top relevant memories (context)
    memories: List[Dict[str, Any]]

    # Phase 2: contrastive pairs + heuristics
    contrasts: List[Dict[str, Any]] = field(default_factory=list)
    heuristics: List[Dict[str, Any]] = field(default_factory=list)

    # Phase 3: first-class cognitive state objects
    episodes: List[Dict[str, Any]] = field(default_factory=list)
    task_states: List[Dict[str, Any]] = field(default_factory=list)
    policies: List[Dict[str, Any]] = field(default_factory=list)
    beliefs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "last_session": self.last_session,
            "performance": [p.to_dict() for p in self.performance],
            "insights": [i.to_dict() for i in self.insights],
            "skills": self.skills[:5],
            "intentions": [i.to_dict() for i in self.intentions],
            "warnings": self.warnings,
            "contrasts": self.contrasts[:5],
            "heuristics": self.heuristics[:5],
            "episodes": self.episodes[:5],
            "task_states": self.task_states[:5],
            "policies": self.policies[:5],
            "beliefs": self.beliefs[:10],
            "memories": [
                {"id": m.get("id"), "memory": m.get("memory", "")[:500],
                 "strength": m.get("strength", 1.0)}
                for m in self.memories[:10]
            ],
            "meta": {
                "n_insights": len(self.insights),
                "n_active_intentions": len(self.intentions),
                "n_warnings": len(self.warnings),
                "n_contrasts": len(self.contrasts),
                "n_heuristics": len(self.heuristics),
                "n_episodes": len(self.episodes),
                "n_task_states": len(self.task_states),
                "n_policies": len(self.policies),
                "n_beliefs": len(self.beliefs),
                "performance_tracked": len(self.performance) > 0,
            },
        }


# ---------------------------------------------------------------------------
# Buddhi — the proactive cognition layer
# ---------------------------------------------------------------------------

class Buddhi:
    """Proactive cognition that turns any agent into a HyperAgent.

    Not a gateway or filter. A parallel intelligence that:
    1. Observes the memory pipeline (store/search signals)
    2. Extracts insights from outcomes (not just stores data)
    3. Tracks performance trends per task type
    4. Stores and triggers intentions (prospective memory)
    5. Pushes context proactively (hyper_context)

    Zero LLM calls on the hot path. Fast and cheap.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        kernel: Optional[Any] = None,
    ):
        self._data_dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "buddhi"
        )
        os.makedirs(self._data_dir, exist_ok=True)

        # CognitionKernel owns all state primitives
        if kernel is not None:
            self._kernel = kernel
        else:
            from dhee.core.cognition_kernel import CognitionKernel
            self._kernel = CognitionKernel(data_dir=self._data_dir)

        # Buddhi's own state: insights + performance (NOT state primitives)
        self._insights: Dict[str, Insight] = {}
        self._performance: Dict[str, List[Dict[str, Any]]] = {}  # task_type -> records
        self._query_sequences: Dict[str, List[str]] = {}  # user_id -> recent queries

        # Phase 2 subsystems (lazy-initialized, stay in Buddhi)
        self._contrastive = None
        self._heuristic_distiller = None
        self._meta_buddhi = None

        self._load_state()

    def _get_contrastive(self):
        if self._contrastive is None:
            from dhee.core.contrastive import ContrastiveStore
            self._contrastive = ContrastiveStore(
                data_dir=os.path.join(self._data_dir, "contrastive")
            )
        return self._contrastive

    def _get_heuristic_distiller(self):
        if self._heuristic_distiller is None:
            from dhee.core.heuristic import HeuristicDistiller
            self._heuristic_distiller = HeuristicDistiller(
                data_dir=os.path.join(self._data_dir, "heuristics")
            )
        return self._heuristic_distiller

    def _get_meta_buddhi(self):
        if self._meta_buddhi is None:
            from dhee.core.meta_buddhi import MetaBuddhi
            self._meta_buddhi = MetaBuddhi(
                data_dir=os.path.join(self._data_dir, "meta_buddhi")
            )
        return self._meta_buddhi

    # Deprecated forwarders — use self._kernel.* directly.
    # Kept for backward compat with test_cognition_v3.py.

    def _get_episode_store(self):
        return self._kernel.episodes

    def _get_task_state_store(self):
        return self._kernel.tasks

    def _get_policy_store(self):
        return self._kernel.policies

    def _get_belief_store(self):
        return self._kernel.beliefs

    # ------------------------------------------------------------------
    # Core API: The HyperAgent entry point
    # ------------------------------------------------------------------

    def get_hyper_context(
        self,
        user_id: str = "default",
        task_description: Optional[str] = None,
        memory=None,
    ) -> HyperContext:
        """The single call that turns any agent into a HyperAgent.

        Called at session start or when context is needed. Returns
        everything: performance, insights, skills, intentions, warnings.
        """
        # 1. Last session (via kernel handoff, not memory object)
        last_session = None
        try:
            from dhee.core.kernel import get_last_session
            last_session = get_last_session()
        except Exception:
            pass

        # 2. Performance snapshots for relevant task types
        performance = self._get_performance_snapshots(user_id, task_description)

        # 3. Synthesized insights (filtered by relevance if task given)
        insights = self._get_relevant_insights(user_id, task_description)

        # 4. Relevant skills
        skills = []
        if memory and task_description and hasattr(memory, "skill_store"):
            try:
                store = memory.skill_store
                if store:
                    results = store.search(task_description, limit=5)
                    skills = [
                        {
                            "name": r.get("name", ""),
                            "description": r.get("description", ""),
                            "confidence": r.get("confidence", 0.5),
                            "used_count": r.get("used_count", 0),
                        }
                        for r in (results if isinstance(results, list) else [])
                    ]
            except Exception:
                pass

        # 5. Check pending intentions (via kernel)
        triggered = self._kernel.intentions.check_triggers(user_id, task_description)

        # 6. Generate proactive warnings
        warnings = self._generate_warnings(performance, insights)

        # 7. Top memories
        memories = []
        if memory:
            try:
                if task_description:
                    result = memory.search(
                        query=task_description, user_id=user_id, limit=10
                    )
                    memories = result.get("results", [])
                else:
                    result = memory.get_all(user_id=user_id, limit=10)
                    memories = result.get("results", [])
            except Exception:
                pass

        # 8. Track query sequence (for future pattern prediction)
        if task_description:
            seq = self._query_sequences.setdefault(user_id, [])
            seq.append(task_description[:200])
            if len(seq) > 50:
                self._query_sequences[user_id] = seq[-50:]

        # 9. Contrastive pairs (Phase 2: ReasoningBank pattern)
        contrasts = []
        try:
            store = self._get_contrastive()
            pairs = store.retrieve_contrasts(
                task_description or "", user_id=user_id, limit=5,
            )
            contrasts = [p.to_compact() for p in pairs]
        except Exception:
            pass

        # 10. Heuristics (Phase 2: ERL pattern)
        heuristics = []
        try:
            distiller = self._get_heuristic_distiller()
            relevant = distiller.retrieve_relevant(
                task_description or "", user_id=user_id, limit=5,
            )
            heuristics = [h.to_compact() for h in relevant]
        except Exception:
            pass

        # 11-14. Cognitive state from kernel (episodes, tasks, policies, beliefs)
        cog_state = self._kernel.get_cognitive_state(user_id, task_description)
        episodes = cog_state.get("episodes", [])
        task_states = cog_state.get("task_states", [])
        policies = cog_state.get("policies", [])
        beliefs = cog_state.get("beliefs", [])
        warnings.extend(cog_state.get("belief_warnings", []))

        return HyperContext(
            user_id=user_id,
            session_id=str(uuid.uuid4()),
            last_session=last_session,
            performance=performance,
            insights=insights,
            skills=skills,
            intentions=triggered,
            warnings=warnings,
            memories=memories,
            contrasts=contrasts,
            heuristics=heuristics,
            episodes=episodes,
            task_states=task_states,
            policies=policies,
            beliefs=beliefs,
        )

    # ------------------------------------------------------------------
    # Performance tracking (DGM-H's PerformanceTracker)
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        user_id: str,
        task_type: str,
        score: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Insight]:
        """Record a task outcome and check for emergent insights.

        This is the core feedback signal. Every time an agent completes
        a task, it reports the outcome. Buddhi:
        1. Records the score
        2. Checks for performance trends
        3. Auto-generates insights when patterns emerge
        """
        key = f"{user_id}:{task_type}"
        records = self._performance.setdefault(key, [])
        records.append({
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        })

        # Keep bounded
        if len(records) > 200:
            self._performance[key] = records[-200:]

        self._save_performance()

        # Check for emergent insight
        return self._check_for_insight(user_id, task_type, records)

    def _get_performance_snapshots(
        self, user_id: str, task_description: Optional[str]
    ) -> List[PerformanceSnapshot]:
        """Build performance snapshots for relevant task types."""
        snapshots = []
        prefix = f"{user_id}:"

        for key, records in self._performance.items():
            if not key.startswith(prefix):
                continue
            task_type = key[len(prefix):]
            if not records:
                continue

            scores = [r["score"] for r in records]
            timestamps = [r["timestamp"] for r in records]

            # Compute trend (moving average delta)
            trend = 0.0
            if len(scores) >= 4:
                window = min(5, len(scores) // 2)
                recent = sum(scores[-window:]) / window
                older = sum(scores[-window * 2:-window]) / window
                trend = recent - older

            snapshots.append(PerformanceSnapshot(
                task_type=task_type,
                scores=scores,
                timestamps=timestamps,
                trend=trend,
                best_score=max(scores),
                worst_score=min(scores),
                avg_score=sum(scores) / len(scores),
                total_attempts=len(scores),
            ))

        # Sort by relevance to task_description (keyword overlap)
        if task_description:
            task_words = set(task_description.lower().split())
            snapshots.sort(
                key=lambda s: len(task_words & set(s.task_type.lower().split())),
                reverse=True,
            )

        return snapshots

    def _check_for_insight(
        self, user_id: str, task_type: str, records: List[Dict]
    ) -> Optional[Insight]:
        """Auto-generate insights when patterns emerge in performance data.

        This is what DGM-H agents emergently learned to do — synthesize
        causal hypotheses from performance history. We do it automatically.
        """
        if len(records) < 3:
            return None

        scores = [r["score"] for r in records]
        latest = scores[-1]
        prev = scores[-2]

        # Detect regression
        if len(scores) >= 3 and latest < prev and latest < scores[-3]:
            # Two consecutive drops = regression warning
            insight = Insight(
                id=str(uuid.uuid4()),
                user_id=user_id,
                content=(
                    f"Regression detected in '{task_type}': "
                    f"scores dropped {scores[-3]:.2f} → {prev:.2f} → {latest:.2f}. "
                    f"Recent changes may have caused degradation."
                ),
                insight_type="warning",
                source_task_types=[task_type],
                confidence=0.7,
                created_at=datetime.now(timezone.utc).isoformat(),
                last_validated=datetime.now(timezone.utc).isoformat(),
                validation_count=1,
                invalidation_count=0,
                tags=["regression", "auto-detected", task_type],
            )
            self._insights[insight.id] = insight
            self._save_insights()
            return insight

        # Detect breakthrough (new best after plateau)
        if len(scores) >= 5:
            recent_best = max(scores[-3:])
            prior_best = max(scores[:-3])
            if recent_best > prior_best * 1.1:  # 10% improvement
                insight = Insight(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    content=(
                        f"Breakthrough in '{task_type}': "
                        f"score {recent_best:.2f} exceeds prior best {prior_best:.2f}. "
                        f"Current approach is working — continue this direction."
                    ),
                    insight_type="pattern",
                    source_task_types=[task_type],
                    confidence=0.8,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    last_validated=datetime.now(timezone.utc).isoformat(),
                    validation_count=1,
                    invalidation_count=0,
                    tags=["breakthrough", "auto-detected", task_type],
                )
                self._insights[insight.id] = insight
                self._save_insights()
                return insight

        return None

    # ------------------------------------------------------------------
    # Insight management
    # ------------------------------------------------------------------

    def add_insight(
        self,
        user_id: str,
        content: str,
        insight_type: str = "strategy",
        source_task_types: Optional[List[str]] = None,
        confidence: float = 0.7,
        tags: Optional[List[str]] = None,
    ) -> Insight:
        """Explicitly add an insight (from agent reflection or user feedback)."""
        insight = Insight(
            id=str(uuid.uuid4()),
            user_id=user_id,
            content=content,
            insight_type=insight_type,
            source_task_types=source_task_types or [],
            confidence=confidence,
            created_at=datetime.now(timezone.utc).isoformat(),
            last_validated=datetime.now(timezone.utc).isoformat(),
            validation_count=0,
            invalidation_count=0,
            tags=tags or [],
        )
        self._insights[insight.id] = insight
        self._save_insights()
        return insight

    def validate_insight(self, insight_id: str, validated: bool = True) -> None:
        """Mark an insight as validated (useful) or invalidated (wrong)."""
        insight = self._insights.get(insight_id)
        if not insight:
            return
        if validated:
            insight.validation_count += 1
            insight.last_validated = datetime.now(timezone.utc).isoformat()
            insight.confidence = min(1.0, insight.confidence + 0.05)
        else:
            insight.invalidation_count += 1
            insight.confidence = max(0.0, insight.confidence - 0.1)
        self._save_insights()

    def _get_relevant_insights(
        self, user_id: str, task_description: Optional[str]
    ) -> List[Insight]:
        """Get insights relevant to a task, sorted by strength."""
        user_insights = [
            i for i in self._insights.values()
            if i.user_id == user_id and i.strength() > 0.1
        ]

        if task_description:
            task_words = set(task_description.lower().split())
            # Score by tag/content overlap
            scored = []
            for insight in user_insights:
                text_words = set(insight.content.lower().split())
                tag_words = set(w.lower() for t in insight.tags for w in t.split())
                overlap = len(task_words & (text_words | tag_words))
                scored.append((insight, overlap + insight.strength()))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [i for i, _ in scored[:10]]

        # No task context — return strongest
        user_insights.sort(key=lambda i: i.strength(), reverse=True)
        return user_insights[:10]

    # ------------------------------------------------------------------
    # Intention management (prospective memory)
    # ------------------------------------------------------------------

    def store_intention(
        self,
        user_id: str,
        description: str,
        trigger_keywords: Optional[List[str]] = None,
        trigger_after: Optional[str] = None,
        action_type: str = "remind",
        action_payload: Optional[str] = None,
    ) -> "Intention":
        """Store a future intention — delegates to kernel IntentionStore."""
        return self._kernel.intentions.store(
            user_id=user_id,
            description=description,
            trigger_keywords=trigger_keywords,
            trigger_after=trigger_after,
            action_type=action_type,
            action_payload=action_payload,
        )

    def detect_intention_in_text(
        self, text: str, user_id: str
    ) -> Optional["Intention"]:
        """Auto-detect intentions in natural language — delegates to kernel."""
        return self._kernel.intentions.detect_in_text(text, user_id)

    def _check_intentions(
        self, user_id: str, context: Optional[str]
    ) -> List["Intention"]:
        """Check for triggered intentions — delegates to kernel."""
        return self._kernel.intentions.check_triggers(user_id, context)

    # ------------------------------------------------------------------
    # Proactive warnings
    # ------------------------------------------------------------------

    def _generate_warnings(
        self,
        performance: List[PerformanceSnapshot],
        insights: List[Insight],
    ) -> List[str]:
        """Generate proactive warnings from performance trends and insights."""
        warnings = []

        for snap in performance:
            if snap.trend < -0.05 and snap.total_attempts >= 3:
                warnings.append(
                    f"Performance on '{snap.task_type}' is declining "
                    f"(trend: {snap.trend:+.3f}). Recent scores: "
                    f"{[round(s, 2) for s in snap.scores[-3:]]}"
                )
            if snap.total_attempts >= 5 and snap.avg_score < 0.3:
                warnings.append(
                    f"Low average score ({snap.avg_score:.2f}) on '{snap.task_type}'. "
                    f"Consider changing approach."
                )

        for insight in insights:
            if insight.insight_type == "warning" and insight.strength() > 0.3:
                warnings.append(insight.content)

        return warnings[:5]  # Cap at 5 warnings

    # ------------------------------------------------------------------
    # On-write hook: observe memories being stored
    # ------------------------------------------------------------------

    def on_memory_stored(
        self,
        content: str,
        user_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        memory_id: Optional[str] = None,
    ) -> Optional[Intention]:
        """Called when a memory is stored.

        Triggers:
          1. Intention detection ("remember to X when Y")
          2. Episode event recording
          3. Belief creation for factual claims
        """
        # 1. Intention detection
        intention = self.detect_intention_in_text(content, user_id)

        # 2. Episode event recording (via kernel)
        try:
            self._kernel.episodes.record_event(
                user_id=user_id,
                event_type="memory_add",
                content=content[:500],
                memory_id=memory_id,
            )
        except Exception:
            pass

        # 3. Belief creation for factual statements (via kernel)
        try:
            self._maybe_create_belief(content, user_id, memory_id)
        except Exception:
            pass

        return intention

    def _maybe_create_belief(
        self, content: str, user_id: str, memory_id: Optional[str] = None,
    ) -> None:
        """Detect factual claims and create/update beliefs.

        Simple heuristic: statements with assertion patterns are factual claims.
        """
        assertion_patterns = [
            r"\b(?:is|are|was|were|has|have|does|do)\b",
            r"\b(?:always|never|every|all|none)\b",
            r"\b(?:prefers?|likes?|wants?|needs?|requires?|supports?)\b",
            r"\b(?:works?|runs?|uses?|depends?)\b",
        ]
        content_lower = content.lower()

        # Only create beliefs for assertive content (not questions, not commands)
        if content.strip().endswith("?") or content.strip().startswith(("do ", "how ", "what ", "where ", "when ", "why ")):
            return
        if len(content.split()) < 4:
            return

        # Check if it matches assertion patterns
        is_assertion = any(
            re.search(pattern, content_lower)
            for pattern in assertion_patterns
        )
        if not is_assertion:
            return

        # Determine domain from content
        domain = "general"
        domain_keywords = {
            "programming": ["code", "function", "class", "api", "python", "javascript", "bug", "test"],
            "user_preference": ["prefer", "like", "want", "favorite", "style", "choice"],
            "system_state": ["server", "database", "deploy", "config", "version", "running"],
        }
        for d, keywords in domain_keywords.items():
            if any(kw in content_lower for kw in keywords):
                domain = d
                break

        self._kernel.beliefs.add_belief(
            user_id=user_id,
            claim=content[:500],
            domain=domain,
            confidence=0.5,
            source="memory",
            memory_id=memory_id,
        )

    # ------------------------------------------------------------------
    # On-search hook: piggyback proactive signals
    # ------------------------------------------------------------------

    def on_search(
        self,
        query: str,
        results: List[Dict[str, Any]],
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """Called after search. Returns proactive signals to attach."""
        signals: Dict[str, Any] = {}

        # Check intentions (via kernel)
        triggered = self._kernel.intentions.check_triggers(user_id, query)
        if triggered:
            signals["triggered_intentions"] = [i.to_dict() for i in triggered]

        # Check relevant insights
        insights = self._get_relevant_insights(user_id, query)
        if insights:
            signals["relevant_insights"] = [
                i.to_dict() for i in insights[:3]
            ]

        return signals

    # ------------------------------------------------------------------
    # Reflect: synthesize insights from recent trajectory
    # ------------------------------------------------------------------

    def reflect(
        self,
        user_id: str,
        task_type: str,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        key_decision: Optional[str] = None,
        outcome_score: Optional[float] = None,
    ) -> List[Insight]:
        """Agent-triggered reflection. Synthesizes insights from experience.

        Called when an agent completes a task or wants to record learnings.
        This is the explicit version of DGM-H's persistent memory —
        the agent tells Dhee what it learned, and Dhee stores it as
        transferable insight.

        If outcome_score is provided, policy utility is updated using the
        performance delta between the moving-average baseline and actual score.
        """
        new_insights = []

        if what_worked:
            insight = self.add_insight(
                user_id=user_id,
                content=f"What worked for '{task_type}': {what_worked}",
                insight_type="strategy",
                source_task_types=[task_type],
                confidence=0.75,
                tags=["what_worked", task_type],
            )
            new_insights.append(insight)

        if what_failed:
            insight = self.add_insight(
                user_id=user_id,
                content=f"What failed for '{task_type}': {what_failed}",
                insight_type="warning",
                source_task_types=[task_type],
                confidence=0.7,
                tags=["what_failed", task_type],
            )
            new_insights.append(insight)

        if key_decision:
            insight = self.add_insight(
                user_id=user_id,
                content=f"Key decision for '{task_type}': {key_decision}",
                insight_type="causal",
                source_task_types=[task_type],
                confidence=0.65,
                tags=["decision", task_type],
            )
            new_insights.append(insight)

        # Phase 2: Auto-create contrastive pair when both sides provided
        if what_worked and what_failed:
            try:
                store = self._get_contrastive()
                store.add_pair(
                    task_description=f"{task_type} task",
                    success_approach=what_worked,
                    failure_approach=what_failed,
                    task_type=task_type,
                    user_id=user_id,
                )
            except Exception:
                pass

        # Phase 2: Distill heuristic from what_worked
        if what_worked:
            try:
                distiller = self._get_heuristic_distiller()
                h = distiller.distill_from_trajectory(
                    task_description=f"{task_type} task",
                    task_type=task_type,
                    what_worked=what_worked,
                    what_failed=what_failed,
                    user_id=user_id,
                )
                # Close the heuristic validation loop: validate any previously
                # retrieved heuristics that were used for this task type
                self._validate_used_heuristics(user_id, task_type, what_worked is not None)
            except Exception:
                pass

        # Phase 3: Extract policy from task outcomes, with utility deltas
        # Compute baseline from moving average for utility scoring (D2Skill)
        baseline_score = None
        if outcome_score is not None:
            try:
                key = f"{user_id}:{task_type}"
                records = self._performance.get(key, [])
                if len(records) >= 2:
                    recent = records[-min(10, len(records)):]
                    baseline_score = sum(r["score"] for r in recent) / len(recent)
            except Exception:
                pass

        if what_worked:
            try:
                matched = self._kernel.policies.match_policies(
                    user_id, task_type, f"{task_type} task",
                )
                for policy in matched:
                    self._kernel.policies.record_outcome(
                        policy.id,
                        success=True,
                        baseline_score=baseline_score,
                        actual_score=outcome_score,
                    )

                completed = self._kernel.tasks.get_tasks_by_type(
                    user_id, task_type, limit=10,
                )
                if len(completed) >= 3:
                    task_dicts = [t.to_dict() for t in completed]
                    self._kernel.policies.extract_from_tasks(
                        user_id, task_dicts, task_type,
                    )
            except Exception:
                pass

        if what_failed:
            try:
                matched = self._kernel.policies.match_policies(
                    user_id, task_type, f"{task_type} task",
                )
                for policy in matched:
                    self._kernel.policies.record_outcome(
                        policy.id,
                        success=False,
                        baseline_score=baseline_score,
                        actual_score=outcome_score,
                    )
            except Exception:
                pass

        # Update beliefs based on outcomes (via kernel)
        if what_worked:
            try:
                relevant = self._kernel.beliefs.get_relevant_beliefs(
                    user_id, what_worked, limit=3,
                )
                for belief in relevant:
                    self._kernel.beliefs.reinforce_belief(
                        belief.id, what_worked, source="outcome",
                    )
            except Exception:
                pass

        if what_failed:
            try:
                relevant = self._kernel.beliefs.get_relevant_beliefs(
                    user_id, what_failed, limit=3,
                )
                for belief in relevant:
                    self._kernel.beliefs.challenge_belief(
                        belief.id, what_failed, source="outcome",
                    )
            except Exception:
                pass

        return new_insights

    def _validate_used_heuristics(
        self, user_id: str, task_type: str, success: bool,
    ) -> None:
        """Close the heuristic validation loop.

        When a task completes, validate heuristics that were retrieved for
        this task type. This is the missing feedback loop that turns
        scaffolding into real self-improvement.
        """
        try:
            distiller = self._get_heuristic_distiller()
            relevant = distiller.retrieve_relevant(
                task_description=f"{task_type} task",
                user_id=user_id,
                limit=5,
            )
            for h in relevant:
                distiller.validate(h.id, validated=success)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_insights(self) -> None:
        path = os.path.join(self._data_dir, "insights.jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                for insight in self._insights.values():
                    row = {
                        "id": insight.id,
                        "user_id": insight.user_id,
                        "content": insight.content,
                        "insight_type": insight.insight_type,
                        "source_task_types": insight.source_task_types,
                        "confidence": insight.confidence,
                        "created_at": insight.created_at,
                        "last_validated": insight.last_validated,
                        "validation_count": insight.validation_count,
                        "invalidation_count": insight.invalidation_count,
                        "tags": insight.tags,
                    }
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to save insights: %s", e)

    def _save_intentions(self) -> None:
        """Deprecated: intentions now managed by kernel IntentionStore."""
        self._kernel.intentions.flush()

    def _save_performance(self) -> None:
        path = os.path.join(self._data_dir, "performance.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._performance, f, ensure_ascii=False)
        except OSError as e:
            logger.debug("Failed to save performance: %s", e)

    def _load_state(self) -> None:
        """Load all persisted state from disk."""
        # Insights
        insights_path = os.path.join(self._data_dir, "insights.jsonl")
        if os.path.exists(insights_path):
            try:
                with open(insights_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        insight = Insight(
                            id=row["id"],
                            user_id=row["user_id"],
                            content=row["content"],
                            insight_type=row.get("insight_type", "strategy"),
                            source_task_types=row.get("source_task_types", []),
                            confidence=row.get("confidence", 0.5),
                            created_at=row.get("created_at", ""),
                            last_validated=row.get("last_validated", ""),
                            validation_count=row.get("validation_count", 0),
                            invalidation_count=row.get("invalidation_count", 0),
                            tags=row.get("tags", []),
                        )
                        self._insights[insight.id] = insight
            except (OSError, json.JSONDecodeError) as e:
                logger.debug("Failed to load insights: %s", e)

        # Intentions: now managed by kernel IntentionStore (loaded in kernel init)

        # Performance
        perf_path = os.path.join(self._data_dir, "performance.json")
        if os.path.exists(perf_path):
            try:
                with open(perf_path, "r", encoding="utf-8") as f:
                    self._performance = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.debug("Failed to load performance: %s", e)

    def flush(self) -> None:
        """Persist all state. Call on shutdown."""
        self._save_insights()
        self._save_performance()

        # Flush kernel (all state stores)
        self._kernel.flush()

        # Flush Phase 2 subsystems if initialized
        for store in [
            self._contrastive, self._heuristic_distiller,
        ]:
            if store and hasattr(store, "flush"):
                try:
                    store.flush()
                except Exception:
                    pass

    def get_stats(self) -> Dict[str, Any]:
        """Get buddhi status for health checks."""
        intention_stats = self._kernel.intentions.get_stats()
        stats = {
            "insights": len(self._insights),
            "active_intentions": intention_stats.get("active", 0),
            "triggered_intentions": intention_stats.get("triggered", 0),
            "task_types_tracked": len(self._performance),
            "total_performance_records": sum(
                len(v) for v in self._performance.values()
            ),
        }

        # Kernel state store stats
        kernel_stats = self._kernel.get_stats()
        stats.update(kernel_stats)

        # Phase 2 stats (only if initialized)
        for name, store in [
            ("contrastive", self._contrastive),
            ("heuristics", self._heuristic_distiller),
            ("contrastive", self._contrastive),
            ("heuristics", self._heuristic_distiller),
        ]:
            if store and hasattr(store, "get_stats"):
                try:
                    stats[name] = store.get_stats()
                except Exception:
                    pass

        return stats
