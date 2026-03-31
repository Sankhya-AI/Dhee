"""IntentionStore — Prospective memory with confidence-scored triggers.

Extracted from Buddhi to be an independent state primitive.
Manages future intentions ("remember to X when Y") with automatic
detection from natural language and trigger evaluation.

Zero LLM calls. Pure pattern matching + keyword triggers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intention dataclass
# ---------------------------------------------------------------------------

@dataclass
class Intention:
    """A stored future trigger — prospective memory.

    "Remember to run tests after modifying the auth module"
    "Deploy when the PR is approved"
    """
    id: str
    user_id: str
    description: str
    trigger_keywords: List[str]     # matched against queries/content
    trigger_after: Optional[str]    # ISO timestamp deadline
    action_type: str                # "remind" | "suggest" | "warn"
    action_payload: str             # what to surface when triggered
    status: str                     # "active" | "triggered" | "expired"
    created_at: str
    triggered_at: Optional[str]

    # Outcome tracking
    outcome_score: Optional[float] = None
    was_useful: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "action_type": self.action_type,
            "action_payload": self.action_payload,
            "status": self.status,
            "trigger_keywords": self.trigger_keywords,
            "trigger_after": self.trigger_after,
            "outcome_score": self.outcome_score,
            "was_useful": self.was_useful,
        }


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

_INTENTION_PATTERNS = [
    # "remember to X when/after/before Y"
    re.compile(
        r"(?:remember|remind|don't forget|make sure)\s+(?:to\s+)?(.+?)"
        r"\s+(?:when|after|before|if|once)\s+(.+)",
        re.IGNORECASE,
    ),
    # "I need to X after Y"
    re.compile(
        r"(?:I|we)\s+(?:need|want|should|must|have)\s+to\s+(.+?)"
        r"\s+(?:after|before|when|once)\s+(.+)",
        re.IGNORECASE,
    ),
    # "todo: X" / "TODO X"
    re.compile(r"(?:todo|TODO|fixme|FIXME|hack|HACK)[:;]?\s+(.+)", re.IGNORECASE),
]

_STOP_WORDS = {"the", "this", "that", "when", "after", "before"}


# ---------------------------------------------------------------------------
# IntentionStore
# ---------------------------------------------------------------------------

class IntentionStore:
    """Manages prospective memory — future triggers and intentions.

    Parallels EpisodeStore, TaskStateStore, BeliefStore, PolicyStore
    with the same constructor pattern: IntentionStore(data_dir).
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._data_dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "intentions"
        )
        os.makedirs(self._data_dir, exist_ok=True)
        self._intentions: Dict[str, Intention] = {}
        self._load()

    def store(
        self,
        user_id: str,
        description: str,
        trigger_keywords: Optional[List[str]] = None,
        trigger_after: Optional[str] = None,
        action_type: str = "remind",
        action_payload: Optional[str] = None,
    ) -> Intention:
        """Store a future intention — prospective memory."""
        intention = Intention(
            id=str(uuid.uuid4()),
            user_id=user_id,
            description=description,
            trigger_keywords=trigger_keywords or [],
            trigger_after=trigger_after,
            action_type=action_type,
            action_payload=action_payload or description,
            status="active",
            created_at=datetime.now(timezone.utc).isoformat(),
            triggered_at=None,
        )
        self._intentions[intention.id] = intention
        self._save()
        return intention

    def detect_in_text(
        self, text: str, user_id: str
    ) -> Optional[Intention]:
        """Auto-detect intentions in natural language and store them."""
        for pattern in _INTENTION_PATTERNS:
            match = pattern.search(text)
            if match:
                groups = match.groups()
                if len(groups) >= 2:
                    action = groups[0].strip()
                    trigger = groups[1].strip()
                    keywords = [
                        w for w in trigger.lower().split()
                        if len(w) > 3 and w not in _STOP_WORDS
                    ]
                    return self.store(
                        user_id=user_id,
                        description=f"{action} (trigger: {trigger})",
                        trigger_keywords=keywords,
                        action_payload=action,
                    )
                elif len(groups) == 1:
                    # TODO-style, no trigger
                    return self.store(
                        user_id=user_id,
                        description=groups[0].strip(),
                        action_payload=groups[0].strip(),
                    )
        return None

    def check_triggers(
        self, user_id: str, context: Optional[str]
    ) -> List[Intention]:
        """Check for triggered intentions using confidence-scored trigger system."""
        from dhee.core.trigger import TriggerManager, TriggerContext

        triggered = []
        now = datetime.now(timezone.utc)
        trigger_ctx = TriggerContext(
            text=context or "",
            timestamp=time.time(),
        )

        for intention in list(self._intentions.values()):
            if intention.user_id != user_id or intention.status != "active":
                continue

            triggers = TriggerManager.from_intention_keywords(
                keywords=intention.trigger_keywords,
                trigger_after=intention.trigger_after,
            )

            if not triggers:
                continue

            results = TriggerManager.evaluate_triggers(triggers, trigger_ctx)
            if results:
                intention.status = "triggered"
                intention.triggered_at = now.isoformat()
                triggered.append(intention)

        if triggered:
            self._save()

        return triggered

    def get_active(self, user_id: str) -> List[Intention]:
        """Get all active intentions for a user."""
        return [
            i for i in self._intentions.values()
            if i.user_id == user_id and i.status == "active"
        ]

    def record_outcome(
        self,
        intention_id: str,
        useful: bool,
        outcome_score: Optional[float] = None,
    ) -> None:
        """Record whether a triggered intention was useful."""
        intention = self._intentions.get(intention_id)
        if not intention:
            return
        intention.was_useful = useful
        intention.outcome_score = outcome_score
        self._save()

    def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Stats for health checks."""
        intentions = list(self._intentions.values())
        if user_id:
            intentions = [i for i in intentions if i.user_id == user_id]
        active = sum(1 for i in intentions if i.status == "active")
        triggered = sum(1 for i in intentions if i.status == "triggered")
        return {"total": len(intentions), "active": active, "triggered": triggered}

    def flush(self) -> None:
        """Persist all state to disk."""
        self._save()

    # ── Persistence ──────────────────────────────────────────────────

    def _save(self) -> None:
        path = os.path.join(self._data_dir, "intentions.jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                for intention in self._intentions.values():
                    row = {
                        "id": intention.id,
                        "user_id": intention.user_id,
                        "description": intention.description,
                        "trigger_keywords": intention.trigger_keywords,
                        "trigger_after": intention.trigger_after,
                        "action_type": intention.action_type,
                        "action_payload": intention.action_payload,
                        "status": intention.status,
                        "created_at": intention.created_at,
                        "triggered_at": intention.triggered_at,
                        "outcome_score": intention.outcome_score,
                        "was_useful": intention.was_useful,
                    }
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to save intentions: %s", e)

    def _load(self) -> None:
        path = os.path.join(self._data_dir, "intentions.jsonl")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    intention = Intention(
                        id=row["id"],
                        user_id=row["user_id"],
                        description=row["description"],
                        trigger_keywords=row.get("trigger_keywords", []),
                        trigger_after=row.get("trigger_after"),
                        action_type=row.get("action_type", "remind"),
                        action_payload=row.get("action_payload", ""),
                        status=row.get("status", "active"),
                        created_at=row.get("created_at", ""),
                        triggered_at=row.get("triggered_at"),
                        outcome_score=row.get("outcome_score"),
                        was_useful=row.get("was_useful"),
                    )
                    self._intentions[intention.id] = intention
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed to load intentions: %s", e)
