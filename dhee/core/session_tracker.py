"""Passive session observer — makes Dhee learn without ceremony.

Tracks operations (remember/recall/context/checkpoint) and automatically:
1. Bootstraps context on first interaction (auto-context)
2. Checkpoints on session timeout (auto-checkpoint)
3. Infers task_type from query/content patterns (0 LLM)
4. Estimates outcome signals from usage patterns (0 LLM)

The user never has to call context() or checkpoint() explicitly.
Explicit calls still work and override auto-inferred values.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── Memory tiers (Shruti / Smriti / Vasana) ──────────────────────────

TIER_SHRUTI = "shruti"   # Core identity — 0% decay
TIER_SMRITI = "smriti"   # Episodic — normal decay
TIER_VASANA = "vasana"   # Latent echo — compressed, low-priority retrieval

# Patterns that mark a memory as core (shruti)
_SHRUTI_PATTERNS = [
    re.compile(r"\b(?:always|never|must|rule|policy|preference|principle)\b", re.I),
    re.compile(r"\b(?:i am|my name|my role|i work|i prefer)\b", re.I),
    re.compile(r"^(?:system|instruction|config|setting)\s*:", re.I),
]


def classify_tier(content: str) -> str:
    """Classify memory content into a tier. 0 LLM calls."""
    for pat in _SHRUTI_PATTERNS:
        if pat.search(content):
            return TIER_SHRUTI
    return TIER_SMRITI


# ── Task-type inference ──────────────────────────────────────────────

TASK_PATTERNS: Dict[str, List[str]] = {
    "bug_fix": ["fix", "bug", "error", "crash", "broken", "fail", "debug",
                "issue", "traceback", "exception", "stack trace", "segfault"],
    "code_review": ["review", "pr", "pull request", "approve", "nit",
                    "suggestion", "feedback", "lgtm"],
    "feature": ["add", "implement", "create", "build", "new feature",
                "endpoint", "integrate", "support"],
    "refactor": ["refactor", "rename", "extract", "move", "reorganize",
                 "clean", "simplify", "dedup"],
    "deploy": ["deploy", "release", "production", "staging", "ci/cd",
               "pipeline", "rollback", "ship"],
    "research": ["research", "investigate", "explore", "understand",
                 "how does", "why does", "what is", "learn about"],
    "documentation": ["doc", "readme", "changelog", "comment", "explain",
                      "document", "annotate"],
    "testing": ["test", "pytest", "unittest", "coverage", "assertion",
                "mock", "fixture", "spec"],
}


def infer_task_type(texts: List[str]) -> str:
    """Infer task type from a list of text snippets. 0 LLM calls.

    Scores each type by keyword overlap. Returns best match or "general".
    """
    combined = " ".join(texts).lower()
    if not combined.strip():
        return "general"

    best_type = "general"
    best_score = 0

    for task_type, keywords in TASK_PATTERNS.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > best_score:
            best_score = score
            best_type = task_type

    return best_type if best_score >= 2 else "general"


# ── Session Tracker ──────────────────────────────────────────────────

class SessionTracker:
    """Passive session observer. Tracks operations and detects boundaries.

    All methods are pure heuristics — zero LLM calls. The tracker is
    designed to be embedded in Dhee/DheePlugin and called from each
    of the 4 operations.
    """

    # Configurable thresholds
    SESSION_TIMEOUT_SECONDS: float = 1800.0  # 30 min inactivity = boundary
    AUTO_CONTEXT: bool = True
    AUTO_CHECKPOINT: bool = True

    def __init__(
        self,
        session_timeout: Optional[float] = None,
        auto_context: bool = True,
        auto_checkpoint: bool = True,
    ):
        self.SESSION_TIMEOUT_SECONDS = session_timeout or 1800.0
        self.AUTO_CONTEXT = auto_context
        self.AUTO_CHECKPOINT = auto_checkpoint
        self._reset()

    def _reset(self) -> None:
        """Reset all session state."""
        self._session_active = False
        self._session_start_time: float = 0.0
        self._last_activity_time: float = 0.0
        self._op_count: int = 0

        # Content tracking
        self._memories_stored: List[str] = []       # memory IDs
        self._memories_stored_content: List[str] = []  # content snippets
        self._recall_result_ids: List[str] = []     # IDs returned by recall
        self._recall_queries: List[str] = []        # queries
        self._recalled_content: Dict[str, str] = {} # id → content snippet

        # State flags
        self._context_loaded: bool = False
        self._checkpoint_called: bool = False
        self._task_description: Optional[str] = None

    # ── Lifecycle hooks (called by Dhee/DheePlugin) ──────────────

    def on_remember(self, content: str, memory_id: Optional[str] = None) -> Dict[str, Any]:
        """Called after remember(). Returns signals dict.

        Returns:
            {"needs_auto_context": True} if this is the first op and
            auto-context should fire. Empty dict otherwise.
        """
        signals: Dict[str, Any] = {}
        now = time.time()

        # Check for session timeout → auto-checkpoint previous session
        timeout_signals = self._check_timeout(now)
        if timeout_signals:
            signals.update(timeout_signals)

        # Start session if needed
        if not self._session_active:
            self._start_session(now)
            if self.AUTO_CONTEXT and not self._context_loaded:
                signals["needs_auto_context"] = True
                # Infer task from content
                signals["inferred_task"] = content[:200]

        # Track
        self._last_activity_time = now
        self._op_count += 1
        if memory_id:
            self._memories_stored.append(memory_id)
        self._memories_stored_content.append(content[:300])

        return signals

    def on_recall(self, query: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Called after recall(). Results should have 'id' and 'memory' keys."""
        signals: Dict[str, Any] = {}
        now = time.time()

        timeout_signals = self._check_timeout(now)
        if timeout_signals:
            signals.update(timeout_signals)

        if not self._session_active:
            self._start_session(now)
            if self.AUTO_CONTEXT and not self._context_loaded:
                signals["needs_auto_context"] = True
                signals["inferred_task"] = query[:200]

        self._last_activity_time = now
        self._op_count += 1
        self._recall_queries.append(query)
        for r in results:
            rid = r.get("id", "")
            if rid:
                self._recall_result_ids.append(rid)
                self._recalled_content[rid] = r.get("memory", "")[:200]

        return signals

    def on_context(self, task_description: Optional[str] = None) -> None:
        """Called when context() is explicitly invoked."""
        now = time.time()
        if not self._session_active:
            self._start_session(now)
        self._context_loaded = True
        self._last_activity_time = now
        self._op_count += 1
        if task_description:
            self._task_description = task_description

    def on_checkpoint(self) -> None:
        """Called when checkpoint() is explicitly invoked."""
        self._checkpoint_called = True
        self._last_activity_time = time.time()
        self._op_count += 1
        # Don't reset — let the caller decide when to start a new session

    def finalize(self) -> Optional[Dict[str, Any]]:
        """Called on shutdown/atexit. Returns auto-checkpoint args if session active."""
        if self._session_active and not self._checkpoint_called and self._op_count > 0:
            return self._build_auto_checkpoint()
        return None

    # ── Inference (all heuristic, 0 LLM) ─────────────────────────

    def get_inferred_task_type(self) -> str:
        """Infer task type from accumulated session content."""
        texts = self._recall_queries + self._memories_stored_content[:5]
        return infer_task_type(texts)

    def get_outcome_signals(self) -> Dict[str, Any]:
        """Estimate outcome from usage patterns. 0 LLM calls.

        Returns:
            {"outcome_score": float, "what_worked": str|None, "signals": dict}
        """
        signals: Dict[str, Any] = {}

        # Signal 1: Recall utility — were recalled memories referenced later?
        recalled_set = set(self._recall_result_ids)
        stored_set = set(self._memories_stored)
        overlap = recalled_set & stored_set
        if recalled_set:
            recall_utility = len(overlap) / len(recalled_set)
        else:
            recall_utility = 0.5  # neutral if no recalls

        # Signal 2: Session productivity — memories stored per minute
        duration = max(self._last_activity_time - self._session_start_time, 60.0)
        productivity = len(self._memories_stored) / (duration / 60.0)
        # Normalize: 0-1 mems/min = low, 1-5 = good, 5+ = very productive
        prod_score = min(1.0, productivity / 5.0)

        # Signal 3: Session engagement — how many ops total
        engagement = min(1.0, self._op_count / 10.0)

        # Combine signals (weighted average)
        outcome_score = (
            0.4 * recall_utility
            + 0.3 * prod_score
            + 0.3 * engagement
        )
        outcome_score = round(max(0.1, min(1.0, outcome_score)), 2)

        signals["recall_utility"] = round(recall_utility, 2)
        signals["productivity"] = round(prod_score, 2)
        signals["engagement"] = round(engagement, 2)

        # what_worked: the most-recalled memory content
        what_worked = None
        if self._recalled_content:
            # Find the most frequently recalled memory
            from collections import Counter
            id_counts = Counter(self._recall_result_ids)
            if id_counts:
                top_id = id_counts.most_common(1)[0][0]
                what_worked = self._recalled_content.get(top_id)

        return {
            "outcome_score": outcome_score,
            "what_worked": what_worked,
            "signals": signals,
        }

    # ── Internal ─────────────────────────────────────────────────

    def _start_session(self, now: float) -> None:
        self._session_active = True
        self._session_start_time = now
        self._last_activity_time = now

    def _check_timeout(self, now: float) -> Optional[Dict[str, Any]]:
        """Check if previous session timed out. Returns auto-checkpoint args."""
        if not self._session_active:
            return None
        if self._last_activity_time == 0:
            return None

        gap = now - self._last_activity_time
        if gap < self.SESSION_TIMEOUT_SECONDS:
            return None

        # Session timed out — build auto-checkpoint and reset
        auto_cp = None
        if self.AUTO_CHECKPOINT and not self._checkpoint_called and self._op_count > 0:
            auto_cp = self._build_auto_checkpoint()

        self._reset()
        if auto_cp:
            return {"needs_auto_checkpoint": True, "auto_checkpoint_args": auto_cp}
        return None

    def _build_auto_checkpoint(self) -> Dict[str, Any]:
        """Build checkpoint kwargs from inferred session signals."""
        task_type = self.get_inferred_task_type()
        outcome = self.get_outcome_signals()

        # Build summary from queries and stored content
        summary_parts = []
        if self._task_description:
            summary_parts.append(self._task_description)
        elif self._recall_queries:
            summary_parts.append(f"Session focused on: {self._recall_queries[0]}")
        if self._memories_stored_content:
            n = len(self._memories_stored_content)
            summary_parts.append(f"{n} memories stored")
        summary = ". ".join(summary_parts) or "Auto-checkpointed session"

        args: Dict[str, Any] = {
            "summary": summary[:500],
            "task_type": task_type,
            "status": "completed",
        }

        # Only include outcome if we have real signals
        if self._op_count >= 3:
            args["outcome_score"] = outcome["outcome_score"]
        if outcome.get("what_worked"):
            args["what_worked"] = outcome["what_worked"]

        return args

    @property
    def session_active(self) -> bool:
        return self._session_active

    @property
    def op_count(self) -> int:
        return self._op_count

    @property
    def context_loaded(self) -> bool:
        return self._context_loaded
