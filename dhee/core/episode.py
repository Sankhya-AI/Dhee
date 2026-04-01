"""Episode — first-class temporal unit of agent experience.

An Episode is NOT a memory. It is a bounded temporal container that groups
related memories, actions, and outcomes into a coherent unit of experience.
Episodes are the natural unit for:
  - Selective forgetting (forget by utility, not just age)
  - Experience replay (retrieve whole episodes, not isolated fragments)
  - Trajectory segmentation (each episode = one task attempt)
  - Transfer learning (similar episodes across domains)

Lifecycle: open -> active -> closed -> archived | forgotten

Boundary detection uses 3 signals:
  1. Time gap: >30min silence = likely new episode
  2. Topic shift: cosine distance between recent and new content
  3. Explicit markers: session_start/session_end, checkpoint, task change

Forgetting is utility-based (not just recency):
  utility = access_frequency * outcome_value * recency_factor * connection_density
  Episodes below utility threshold get archived (metadata kept, content dropped).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EpisodeStatus(str, Enum):
    OPEN = "open"           # Currently accumulating events
    ACTIVE = "active"       # Closed but frequently accessed
    CLOSED = "closed"       # Done, normal retention
    ARCHIVED = "archived"   # Metadata only, content dropped
    FORGOTTEN = "forgotten" # Marked for deletion


@dataclass
class EpisodeEvent:
    """A single event within an episode."""
    timestamp: float
    event_type: str         # "memory_add" | "memory_recall" | "action" | "outcome" | "reflection"
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> EpisodeEvent:
        return cls(
            timestamp=d["timestamp"],
            event_type=d["event_type"],
            content=d["content"],
            metadata=d.get("metadata", {}),
        )


@dataclass
class Episode:
    """A bounded temporal unit of agent experience."""

    id: str
    user_id: str
    task_description: str
    task_type: str
    status: EpisodeStatus
    started_at: float
    ended_at: Optional[float]

    events: List[EpisodeEvent] = field(default_factory=list)
    memory_ids: List[str] = field(default_factory=list)

    # Outcome tracking
    outcome_score: Optional[float] = None   # 0-1, None if no outcome yet
    outcome_summary: Optional[str] = None

    # Utility signals for selective forgetting
    access_count: int = 0
    last_accessed: Optional[float] = None
    connection_count: int = 0               # links to other episodes / beliefs / policies

    # Content fingerprint for topic detection
    topic_keywords: List[str] = field(default_factory=list)

    def add_event(self, event_type: str, content: str, metadata: Optional[Dict] = None) -> EpisodeEvent:
        """Add an event to this episode."""
        event = EpisodeEvent(
            timestamp=time.time(),
            event_type=event_type,
            content=content,
            metadata=metadata or {},
        )
        self.events.append(event)
        return event

    def close(self, outcome_score: Optional[float] = None, outcome_summary: Optional[str] = None) -> None:
        """Close this episode with optional outcome."""
        self.status = EpisodeStatus.CLOSED
        self.ended_at = time.time()
        if outcome_score is not None:
            self.outcome_score = outcome_score
        if outcome_summary is not None:
            self.outcome_summary = outcome_summary

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return end - self.started_at

    @property
    def event_count(self) -> int:
        return len(self.events)

    def utility_score(self, now: Optional[float] = None) -> float:
        """Compute utility for selective forgetting.

        utility = access_frequency * outcome_value * recency_factor * connection_density

        Higher utility = keep longer. Low utility = candidate for archival.
        """
        now = now or time.time()
        age_hours = max(1.0, (now - self.started_at) / 3600.0)

        # Access frequency: normalized by age
        access_freq = min(1.0, self.access_count / max(1.0, age_hours / 24.0))

        # Outcome value: successful episodes are more valuable
        if self.outcome_score is not None:
            outcome_val = 0.3 + 0.7 * self.outcome_score
        else:
            outcome_val = 0.5  # Unknown outcome = neutral

        # Recency: exponential decay, half-life = 7 days
        half_life_hours = 7 * 24
        recency = math.exp(-0.693 * age_hours / half_life_hours)

        # Connection density: episodes linked to beliefs/policies are more valuable
        conn_density = min(1.0, 0.3 + 0.1 * self.connection_count)

        return access_freq * outcome_val * recency * conn_density

    def mark_accessed(self) -> None:
        """Record an access (retrieval, reference)."""
        self.access_count += 1
        self.last_accessed = time.time()

    def archive(self) -> None:
        """Archive: keep metadata, drop event content."""
        self.status = EpisodeStatus.ARCHIVED
        # Keep the first and last event for context, clear the rest
        if len(self.events) > 2:
            self.events = [self.events[0], self.events[-1]]
        for event in self.events:
            event.content = event.content[:100] + "..." if len(event.content) > 100 else event.content

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "task_description": self.task_description,
            "task_type": self.task_type,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "events": [e.to_dict() for e in self.events],
            "memory_ids": self.memory_ids,
            "outcome_score": self.outcome_score,
            "outcome_summary": self.outcome_summary,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "connection_count": self.connection_count,
            "topic_keywords": self.topic_keywords,
        }

    def to_compact(self) -> Dict[str, Any]:
        """Compact format for HyperContext."""
        return {
            "id": self.id,
            "task": self.task_description[:200],
            "task_type": self.task_type,
            "outcome": self.outcome_score,
            "outcome_summary": (self.outcome_summary or "")[:200],
            "events": self.event_count,
            "duration_min": round(self.duration_seconds / 60, 1),
            "utility": round(self.utility_score(), 3),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Episode:
        return cls(
            id=d["id"],
            user_id=d["user_id"],
            task_description=d["task_description"],
            task_type=d.get("task_type", "general"),
            status=EpisodeStatus(d.get("status", "closed")),
            started_at=d["started_at"],
            ended_at=d.get("ended_at"),
            events=[EpisodeEvent.from_dict(e) for e in d.get("events", [])],
            memory_ids=d.get("memory_ids", []),
            outcome_score=d.get("outcome_score"),
            outcome_summary=d.get("outcome_summary"),
            access_count=d.get("access_count", 0),
            last_accessed=d.get("last_accessed"),
            connection_count=d.get("connection_count", 0),
            topic_keywords=d.get("topic_keywords", []),
        )


class EpisodeStore:
    """Manages episode lifecycle, boundary detection, and selective forgetting.

    Boundary detection signals:
      1. Time gap: >30 min of silence between events
      2. Topic shift: keyword overlap < 20% between last 5 events and new content
      3. Explicit: session_start/session_end/task_change calls

    Selective forgetting:
      - Runs periodically (on checkpoint or explicit call)
      - Computes utility for all closed episodes
      - Archives episodes below threshold
      - Never forgets episodes linked to active beliefs/policies
    """

    TIME_GAP_THRESHOLD = 30 * 60     # 30 minutes
    TOPIC_SHIFT_THRESHOLD = 0.2      # 20% keyword overlap = new episode
    ARCHIVE_UTILITY_THRESHOLD = 0.05 # Below this = archive candidate
    MAX_EPISODES = 500               # Hard cap per user

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "episodes"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._episodes: Dict[str, Episode] = {}
        self._open_episodes: Dict[str, str] = {}  # user_id -> episode_id
        self._load()

    def begin_episode(
        self,
        user_id: str,
        task_description: str,
        task_type: str = "general",
    ) -> Episode:
        """Explicitly start a new episode (e.g., session_start)."""
        # Close any open episode for this user
        self._close_open_episode(user_id)

        episode = Episode(
            id=str(uuid.uuid4()),
            user_id=user_id,
            task_description=task_description,
            task_type=task_type,
            status=EpisodeStatus.OPEN,
            started_at=time.time(),
            ended_at=None,
            topic_keywords=self._extract_keywords(task_description),
        )
        self._episodes[episode.id] = episode
        self._open_episodes[user_id] = episode.id
        self._save_episode(episode)
        return episode

    def end_episode(
        self,
        user_id: str,
        outcome_score: Optional[float] = None,
        outcome_summary: Optional[str] = None,
    ) -> Optional[Episode]:
        """Explicitly end the current episode."""
        ep_id = self._open_episodes.get(user_id)
        if not ep_id:
            return None
        episode = self._episodes.get(ep_id)
        if not episode:
            return None

        episode.close(outcome_score, outcome_summary)
        del self._open_episodes[user_id]
        self._save_episode(episode)
        return episode

    def record_event(
        self,
        user_id: str,
        event_type: str,
        content: str,
        metadata: Optional[Dict] = None,
        memory_id: Optional[str] = None,
    ) -> Episode:
        """Record an event, auto-detecting episode boundaries.

        If no open episode exists, or boundary is detected, starts a new one.
        Returns the episode the event was added to.
        """
        current_ep = self._get_open_episode(user_id)

        # Check if we need a new episode
        if current_ep and self._should_split(current_ep, content):
            current_ep.close()
            self._save_episode(current_ep)
            current_ep = None

        if current_ep is None:
            # Infer task description from content
            task_desc = content[:200] if len(content) <= 200 else content[:200] + "..."
            current_ep = self.begin_episode(user_id, task_desc)

        current_ep.add_event(event_type, content, metadata)
        if memory_id and memory_id not in current_ep.memory_ids:
            current_ep.memory_ids.append(memory_id)

        # Update topic keywords incrementally
        new_words = self._extract_keywords(content)
        existing = set(current_ep.topic_keywords)
        for w in new_words:
            if w not in existing:
                current_ep.topic_keywords.append(w)
                existing.add(w)
        # Keep bounded
        if len(current_ep.topic_keywords) > 50:
            current_ep.topic_keywords = current_ep.topic_keywords[-50:]

        self._save_episode(current_ep)
        return current_ep

    def retrieve_episodes(
        self,
        user_id: str,
        task_description: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 5,
        include_archived: bool = False,
    ) -> List[Episode]:
        """Retrieve relevant episodes for context injection."""
        candidates = []
        for ep in self._episodes.values():
            if ep.user_id != user_id:
                continue
            if ep.status == EpisodeStatus.FORGOTTEN:
                continue
            if ep.status == EpisodeStatus.ARCHIVED and not include_archived:
                continue
            candidates.append(ep)

        # Score by relevance
        if task_description:
            query_words = set(task_description.lower().split())
            scored = []
            for ep in candidates:
                ep_words = set(ep.topic_keywords)
                overlap = len(query_words & ep_words)
                type_match = 1.0 if task_type and ep.task_type == task_type else 0.0
                utility = ep.utility_score()
                score = overlap * 2.0 + type_match * 3.0 + utility * 5.0
                scored.append((ep, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            results = [ep for ep, _ in scored[:limit]]
        else:
            # No query — return most recent
            candidates.sort(key=lambda e: e.started_at, reverse=True)
            results = candidates[:limit]

        # Mark accessed
        for ep in results:
            ep.mark_accessed()

        return results

    def selective_forget(self, user_id: str, protected_episode_ids: Optional[set] = None) -> int:
        """Run utility-based selective forgetting.

        Archives low-utility episodes. Never archives protected episodes
        (those linked to active beliefs, policies, or open tasks).

        Returns number of episodes archived.
        """
        protected = protected_episode_ids or set()
        now = time.time()
        archived = 0

        user_episodes = [
            ep for ep in self._episodes.values()
            if ep.user_id == user_id and ep.status == EpisodeStatus.CLOSED
        ]

        # Sort by utility ascending (worst candidates first)
        user_episodes.sort(key=lambda e: e.utility_score(now))

        for ep in user_episodes:
            if ep.id in protected:
                continue
            if ep.utility_score(now) < self.ARCHIVE_UTILITY_THRESHOLD:
                ep.archive()
                self._save_episode(ep)
                archived += 1

        # Hard cap: if still over limit, archive oldest low-utility
        total_active = sum(
            1 for ep in self._episodes.values()
            if ep.user_id == user_id and ep.status in (EpisodeStatus.CLOSED, EpisodeStatus.ACTIVE)
        )
        if total_active > self.MAX_EPISODES:
            excess = total_active - self.MAX_EPISODES
            for ep in user_episodes[:excess]:
                if ep.id not in protected and ep.status != EpisodeStatus.ARCHIVED:
                    ep.archive()
                    self._save_episode(ep)
                    archived += 1

        return archived

    def get_open_episode(self, user_id: str) -> Optional[Episode]:
        """Get the currently open episode for a user (public access)."""
        ep_id = self._open_episodes.get(user_id)
        if ep_id:
            return self._episodes.get(ep_id)
        return None

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        """Get an episode by its ID (public access)."""
        return self._episodes.get(episode_id)

    def increment_connections(self, user_id: str, count: int = 1) -> None:
        """Increment connection_count on the open episode for cross-primitive links."""
        ep = self.get_open_episode(user_id)
        if ep:
            ep.connection_count += count

    def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get episode store statistics."""
        episodes = list(self._episodes.values())
        if user_id:
            episodes = [e for e in episodes if e.user_id == user_id]

        by_status = {}
        for ep in episodes:
            by_status[ep.status.value] = by_status.get(ep.status.value, 0) + 1

        utilities = [ep.utility_score() for ep in episodes if ep.status == EpisodeStatus.CLOSED]

        return {
            "total": len(episodes),
            "by_status": by_status,
            "open": len(self._open_episodes),
            "avg_utility": sum(utilities) / len(utilities) if utilities else 0.0,
            "avg_events": (
                sum(ep.event_count for ep in episodes) / len(episodes)
                if episodes else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Boundary detection
    # ------------------------------------------------------------------

    def _should_split(self, episode: Episode, new_content: str) -> bool:
        """Detect whether new content should start a new episode."""
        if not episode.events:
            return False

        last_event = episode.events[-1]
        now = time.time()

        # Signal 1: Time gap
        gap = now - last_event.timestamp
        if gap > self.TIME_GAP_THRESHOLD:
            logger.debug("Episode split: time gap %.0fs > threshold", gap)
            return True

        # Signal 2: Topic shift
        new_words = set(self._extract_keywords(new_content))
        if new_words and episode.topic_keywords:
            recent_words = set(episode.topic_keywords[-20:])
            if recent_words:
                overlap = len(new_words & recent_words)
                total = len(new_words | recent_words)
                similarity = overlap / total if total > 0 else 0
                if similarity < self.TOPIC_SHIFT_THRESHOLD and len(episode.events) >= 3:
                    logger.debug("Episode split: topic shift (similarity=%.2f)", similarity)
                    return True

        return False

    def _get_open_episode(self, user_id: str) -> Optional[Episode]:
        """Get the currently open episode for a user."""
        ep_id = self._open_episodes.get(user_id)
        if not ep_id:
            return None
        ep = self._episodes.get(ep_id)
        if ep and ep.status == EpisodeStatus.OPEN:
            return ep
        # Stale reference
        del self._open_episodes[user_id]
        return None

    def _close_open_episode(self, user_id: str) -> None:
        """Close any currently open episode for a user."""
        ep = self._get_open_episode(user_id)
        if ep:
            ep.close()
            self._save_episode(ep)
            if user_id in self._open_episodes:
                del self._open_episodes[user_id]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """Extract significant keywords for topic detection."""
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "can", "shall", "to", "of", "in", "for",
            "on", "with", "at", "by", "from", "as", "into", "through", "during",
            "before", "after", "above", "below", "between", "out", "off", "over",
            "under", "again", "further", "then", "once", "here", "there", "when",
            "where", "why", "how", "all", "each", "every", "both", "few", "more",
            "most", "other", "some", "such", "no", "nor", "not", "only", "own",
            "same", "so", "than", "too", "very", "just", "because", "but", "and",
            "or", "if", "while", "about", "it", "its", "this", "that", "these",
            "those", "i", "me", "my", "we", "our", "you", "your", "he", "him",
            "his", "she", "her", "they", "them", "their", "what", "which", "who",
        }
        words = text.lower().split()
        return [w for w in words if len(w) > 2 and w not in stop_words][:30]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_episode(self, episode: Episode) -> None:
        """Save a single episode to its own JSON file."""
        path = os.path.join(self._dir, f"{episode.id}.json")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(episode.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as e:
            logger.debug("Failed to save episode %s: %s", episode.id, e)

    def _load(self) -> None:
        """Load all episodes from disk."""
        if not os.path.isdir(self._dir):
            return
        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ep = Episode.from_dict(data)
                self._episodes[ep.id] = ep
                if ep.status == EpisodeStatus.OPEN:
                    self._open_episodes[ep.user_id] = ep.id
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.debug("Failed to load episode %s: %s", fname, e)

    def flush(self) -> None:
        """Persist all in-memory state."""
        for ep in self._episodes.values():
            self._save_episode(ep)
