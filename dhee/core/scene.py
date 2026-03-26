"""
Episodic Scene Processor for engram.

Groups memories into coherent episodic scenes based on:
1. Time gaps - large temporal gaps signal new scenes
2. Topic shifts - cosine similarity drops signal topic changes
3. Location changes - detected location mentions changing

Scenes get LLM-generated summaries and are searchable by semantic similarity.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SceneDetectionResult:
    """Result of scene boundary detection."""
    is_new_scene: bool
    reason: Optional[str] = None  # "time_gap", "topic_shift", "location_change"
    detected_location: Optional[str] = None
    topic_similarity: Optional[float] = None


# Common location prepositions/patterns
_LOCATION_PATTERN = re.compile(
    r'(?:at|in|from|near|visiting|located in|based in|went to|going to|arrived at)\s+'
    r'([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)',
    re.IGNORECASE,
)


from dhee.utils.math import cosine_similarity as _cosine_similarity


def _detect_location(content: str) -> Optional[str]:
    """Extract a location mention from text."""
    match = _LOCATION_PATTERN.search(content)
    if match:
        loc = match.group(1).strip()
        if len(loc) > 2:
            return loc
    return None


class SceneProcessor:
    """Manages episodic scene detection, creation, and summarization."""

    def __init__(
        self,
        db,
        embedder=None,
        llm=None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.db = db
        self.embedder = embedder
        self.llm = llm
        cfg = config or {}
        self.time_gap_minutes = cfg.get("scene_time_gap_minutes", 30)
        self.topic_threshold = cfg.get("scene_topic_threshold", 0.55)
        self.auto_close_minutes = cfg.get("auto_close_inactive_minutes", 120)
        self.max_scene_memories = cfg.get("max_scene_memories", 50)
        self.use_llm_summarization = cfg.get("use_llm_summarization", True)
        self.summary_regen_threshold = cfg.get("summary_regenerate_threshold", 5)

    # ------------------------------------------------------------------
    # Boundary detection
    # ------------------------------------------------------------------

    def detect_boundary(
        self,
        content: str,
        timestamp: str,
        current_scene: Optional[Dict[str, Any]],
        embedding: Optional[List[float]] = None,
    ) -> SceneDetectionResult:
        """Decide whether this memory starts a new scene or continues the current one."""

        if current_scene is None:
            return SceneDetectionResult(is_new_scene=True, reason="no_scene")

        # 1. Time gap
        scene_end = current_scene.get("end_time") or current_scene.get("start_time")
        if scene_end and timestamp:
            try:
                last_dt = datetime.fromisoformat(scene_end)
                new_dt = datetime.fromisoformat(timestamp)
                gap = (new_dt - last_dt).total_seconds() / 60.0
                if gap > self.time_gap_minutes:
                    return SceneDetectionResult(is_new_scene=True, reason="time_gap")
            except (ValueError, TypeError):
                pass

        # 2. Max memories
        memory_ids = current_scene.get("memory_ids", [])
        if len(memory_ids) >= self.max_scene_memories:
            return SceneDetectionResult(is_new_scene=True, reason="max_memories")

        # 3. Topic shift (cosine similarity)
        scene_embedding = current_scene.get("embedding")
        topic_sim: Optional[float] = None
        if embedding and scene_embedding:
            topic_sim = _cosine_similarity(embedding, scene_embedding)
            if topic_sim < self.topic_threshold:
                return SceneDetectionResult(
                    is_new_scene=True,
                    reason="topic_shift",
                    topic_similarity=topic_sim,
                )

        # 4. Location change
        scene_location = current_scene.get("location")
        detected_location = _detect_location(content)
        if (
            scene_location
            and detected_location
            and scene_location.lower() != detected_location.lower()
        ):
            return SceneDetectionResult(
                is_new_scene=True,
                reason="location_change",
                detected_location=detected_location,
            )

        return SceneDetectionResult(
            is_new_scene=False,
            detected_location=detected_location,
            topic_similarity=topic_sim,
        )

    # ------------------------------------------------------------------
    # Scene lifecycle
    # ------------------------------------------------------------------

    def create_scene(
        self,
        first_memory_id: str,
        user_id: str,
        timestamp: str,
        topic: Optional[str] = None,
        location: Optional[str] = None,
        participants: Optional[List[str]] = None,
        embedding: Optional[List[float]] = None,
        namespace: str = "default",
    ) -> Dict[str, Any]:
        """Create a new scene and add the first memory to it."""
        scene_id = str(uuid.uuid4())
        scene_data = {
            "id": scene_id,
            "user_id": user_id,
            "title": topic or "Untitled scene",
            "topic": topic,
            "location": location,
            "participants": participants or [],
            "memory_ids": [first_memory_id],
            "start_time": timestamp,
            "end_time": None,
            "embedding": embedding,
            "strength": 1.0,
            "namespace": namespace,
        }
        self.db.add_scene(scene_data)
        self.db.add_scene_memory(scene_id, first_memory_id, position=0)
        try:
            self.db.update_memory(first_memory_id, {"scene_id": scene_id})
        except Exception:
            pass  # scene_id column may not exist in very old DBs
        return scene_data

    def add_memory_to_scene(
        self,
        scene_id: str,
        memory_id: str,
        embedding: Optional[List[float]] = None,
        timestamp: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> None:
        """Append a memory to an existing scene."""
        scene = self.db.get_scene(scene_id)
        if not scene:
            return

        memory_ids = scene.get("memory_ids", [])
        position = len(memory_ids)
        memory_ids.append(memory_id)

        updates: Dict[str, Any] = {"memory_ids": memory_ids}
        if timestamp:
            updates["end_time"] = timestamp
        if namespace:
            updates["namespace"] = namespace

        # Running average of embeddings (incremental centroid).
        if embedding and scene.get("embedding"):
            old_emb = scene["embedding"]
            if len(old_emb) == len(embedding):
                n = max(position, 1)
                inv = 1.0 / (n + 1)
                updates["embedding"] = [
                    old_emb[i] * (n * inv) + embedding[i] * inv
                    for i in range(len(embedding))
                ]

        self.db.update_scene(scene_id, updates)
        self.db.add_scene_memory(scene_id, memory_id, position=position)
        try:
            self.db.update_memory(memory_id, {"scene_id": scene_id})
        except Exception:
            pass

    def close_scene(self, scene_id: str, timestamp: Optional[str] = None) -> None:
        """Close a scene: set end_time and generate summary."""
        scene = self.db.get_scene(scene_id)
        if not scene:
            return

        updates: Dict[str, Any] = {}
        if not scene.get("end_time"):
            updates["end_time"] = timestamp or datetime.now(timezone.utc).isoformat()

        # Generate summary (LLM when enabled, otherwise deterministic extractive fallback).
        memories = self.db.get_scene_memories(scene_id)
        summary = None
        if self.use_llm_summarization and self.llm:
            summary = self._summarize_scene(scene, memories)
        if not summary:
            summary = self._extractive_scene_summary(memories)
        if summary:
            updates["summary"] = summary
            # Derive title from summary
            title = summary.split(".")[0][:120]
            updates["title"] = title

        if updates:
            self.db.update_scene(scene_id, updates)

    def auto_close_stale(self, user_id: str) -> List[str]:
        """Close scenes that have been inactive beyond the auto-close threshold."""
        open_scene = self.db.get_open_scene(user_id)
        if not open_scene:
            return []

        end_time = open_scene.get("end_time") or open_scene.get("start_time")
        if not end_time:
            return []

        try:
            last_dt = datetime.fromisoformat(end_time)
            now = datetime.now(timezone.utc)
            # Make last_dt offset-aware if naive.
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt > timedelta(minutes=self.auto_close_minutes):
                self.close_scene(open_scene["id"])
                return [open_scene["id"]]
        except (ValueError, TypeError):
            pass
        return []

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def _summarize_scene(
        self, scene: Dict[str, Any], memories: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Generate an LLM summary of a scene's memories."""
        if not self.llm or not memories:
            return None

        memory_texts = [m.get("memory", "") for m in memories if m.get("memory")]
        if not memory_texts:
            return None

        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(memory_texts[:20]))
        prompt = (
            "Summarize the following sequence of memories into a concise episodic narrative "
            "(2-4 sentences). Focus on what happened, who was involved, and key outcomes.\n\n"
            f"Topic: {scene.get('topic', 'unknown')}\n"
            f"Location: {scene.get('location', 'unknown')}\n\n"
            f"Memories:\n{numbered}\n\n"
            "Summary:"
        )

        try:
            return self.llm.generate(prompt).strip()
        except Exception as e:
            logger.warning(f"Scene summarization failed: {e}")
            return None

    @staticmethod
    def _extractive_scene_summary(memories: List[Dict[str, Any]], max_lines: int = 4) -> Optional[str]:
        """Low-cost extractive summary used when LLM summarization is disabled."""
        snippets: List[str] = []
        for memory in memories:
            text = str(memory.get("memory", "")).strip()
            if not text:
                continue
            # Prefer salient transcript lines over headers.
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            for line in lines:
                lowered = line.lower()
                if lowered.startswith("session id:") or lowered.startswith("session date:") or lowered.startswith("user transcript:"):
                    continue
                snippets.append(line[:180])
                break
            if len(snippets) >= max(1, int(max_lines)):
                break
        if not snippets:
            return None
        return " | ".join(snippets)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_scenes(
        self,
        query: str,
        user_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search scenes by matching query against summaries and topics."""
        # Fetch a bounded candidate set (3x limit is sufficient for re-ranking).
        candidate_limit = min(limit * 3, 150)
        all_scenes = self.db.get_scenes(user_id=user_id, limit=candidate_limit)
        if not all_scenes:
            return []

        query_lower = query.lower()
        query_words = query_lower.split()

        if not self.embedder:
            scored = []
            for s in all_scenes:
                text = f"{s.get('title', '')} {s.get('summary', '')} {s.get('topic', '')}".lower()
                score = sum(1 for w in query_words if w in text)
                if score > 0:
                    scored.append((s, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in scored[:limit]]

        query_embedding = self.embedder.embed(query, memory_action="search")
        scored = []
        for s in all_scenes:
            scene_emb = s.get("embedding")
            if scene_emb:
                sim = _cosine_similarity(query_embedding, scene_emb)
                scored.append((s, sim))
            else:
                text = f"{s.get('title', '')} {s.get('summary', '')} {s.get('topic', '')}".lower()
                keyword_score = sum(1 for w in query_words if w in text) * 0.1
                if keyword_score > 0:
                    scored.append((s, keyword_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for s, score in scored[:limit]:
            s["search_score"] = round(score, 4)
            results.append(s)
        return results

    def get_scene_timeline(
        self,
        user_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get scenes in chronological order for timeline view."""
        scenes = self.db.get_scenes(user_id=user_id, limit=limit)
        # Reverse to chronological (oldest first)
        scenes.reverse()
        return scenes
