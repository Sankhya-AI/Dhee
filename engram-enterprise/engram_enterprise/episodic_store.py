"""CAST-inspired episodic storage and retrieval."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")


def _cosine_similarity(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EpisodicStore:
    def __init__(self, db, embedder, *, time_window_minutes: int = 30, topic_threshold: float = 0.7):
        self.db = db
        self.embedder = embedder
        self.time_window_minutes = time_window_minutes
        self.topic_threshold = topic_threshold

    def ingest_memory_as_view(
        self,
        *,
        user_id: str,
        agent_id: Optional[str],
        memory_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        metadata = metadata or {}
        timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        namespace = str(metadata.get("namespace", "default") or "default").strip() or "default"
        place_type, place_value = self._extract_place(metadata)
        topic_label = self._extract_topic(content)
        topic_embedding = self.embedder.embed(topic_label, memory_action="search") if topic_label else None
        characters = self._extract_characters(content=content, metadata=metadata, agent_id=agent_id)

        target_scene = self._find_scene_for_view(
            user_id=user_id,
            view_time=timestamp,
            place_value=place_value,
            topic_embedding=topic_embedding,
            namespace=namespace,
        )

        if target_scene:
            scene_id = target_scene["id"]
            self._attach_to_scene(
                scene=target_scene,
                memory_id=memory_id,
                view_time=timestamp,
                place_value=place_value,
                topic_label=topic_label,
                topic_embedding=topic_embedding,
                characters=characters,
                namespace=namespace,
            )
        else:
            scene_id = self.db.add_scene(
                {
                    "user_id": user_id,
                    "title": topic_label,
                    "summary": topic_label,
                    "topic": topic_label,
                    "location": place_value,
                    "participants": [c["entity_id"] for c in characters],
                    "memory_ids": [memory_id],
                    "start_time": timestamp,
                    "end_time": timestamp,
                    "embedding": topic_embedding,
                    "layer": "sml",
                    "scene_strength": 1.0,
                    "namespace": namespace,
                }
            )
            self.db.add_scene_memory(scene_id, memory_id, position=0)
            self.db.update_memory(memory_id, {"scene_id": scene_id})

        view_id = self.db.add_view(
            {
                "user_id": user_id,
                "agent_id": agent_id,
                "timestamp": timestamp,
                "place_type": place_type,
                "place_value": place_value,
                "topic_label": topic_label,
                "topic_embedding_ref": memory_id,
                "characters": characters,
                "raw_text": content,
                "signals": {
                    "importance": metadata.get("importance", 0.5),
                    "sentiment": metadata.get("sentiment", "neutral"),
                },
                "scene_id": scene_id,
            }
        )
        return {
            "view_id": view_id,
            "scene_id": scene_id,
        }

    def search_scenes(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 10,
        entities: Optional[List[str]] = None,
        place_hint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        scenes = self.db.get_scenes(user_id=user_id, limit=max(limit * 5, 20))
        if not scenes:
            return []

        query_embedding = self.embedder.embed(query, memory_action="search")
        query_terms = set(query.lower().split())
        entities_lower = {e.lower() for e in (entities or [])}

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for scene in scenes:
            score = 0.0
            scene_emb = scene.get("embedding")
            score += _cosine_similarity(query_embedding, scene_emb)

            text = f"{scene.get('summary', '')} {scene.get('topic', '')} {scene.get('title', '')}".lower()
            keyword_hits = sum(1 for t in query_terms if t in text)
            score += keyword_hits * 0.05

            if place_hint and scene.get("location"):
                if place_hint.lower() in str(scene.get("location", "")).lower():
                    score += 0.1
                else:
                    continue

            participants = {str(p).lower() for p in scene.get("participants", [])}
            if entities_lower and not (participants & entities_lower):
                continue

            scene["search_score"] = round(score, 4)
            scored.append((score, scene))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [scene for _, scene in scored[:limit]]

    def _extract_place(self, metadata: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        place = metadata.get("place") or metadata.get("location")
        if isinstance(place, dict):
            return str(place.get("type") or "digital"), place.get("value")
        if isinstance(place, str):
            return "digital", place
        repo = metadata.get("repo") or metadata.get("workspace")
        if repo:
            return "digital", str(repo)
        return "digital", None

    def _extract_topic(self, content: str) -> str:
        terms = (content or "").strip().split()
        return " ".join(terms[:10]) if terms else "untitled"

    def _extract_characters(self, *, content: str, metadata: Dict[str, Any], agent_id: Optional[str]) -> List[Dict[str, str]]:
        chars: List[Dict[str, str]] = []
        primary = metadata.get("actor_id") or metadata.get("speaker") or agent_id or "char_self"
        chars.append({"entity_id": str(primary), "role": "MC"})

        for match in _NAME_RE.findall(content or ""):
            name = match.strip()
            if name.lower() in {"i", "we", "the", "this", "that"}:
                continue
            if name == primary:
                continue
            chars.append({"entity_id": name, "role": "SC"})

        seen = set()
        unique = []
        for c in chars:
            key = (c["entity_id"], c["role"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
        return unique

    def _find_scene_for_view(
        self,
        *,
        user_id: str,
        view_time: str,
        place_value: Optional[str],
        topic_embedding: Optional[List[float]],
        namespace: str,
    ) -> Optional[Dict[str, Any]]:
        candidates = self.db.get_scenes(user_id=user_id, limit=25)
        if not candidates:
            return None

        view_dt = self._safe_parse_time(view_time)
        best_score = -1.0
        best_scene = None

        for scene in candidates:
            scene_namespace = str(scene.get("namespace", "default") or "default").strip() or "default"
            if scene_namespace != namespace:
                continue
            cond_count = 0
            score = 0.0

            scene_time = scene.get("end_time") or scene.get("start_time")
            scene_dt = self._safe_parse_time(scene_time)
            if view_dt and scene_dt and abs((view_dt - scene_dt).total_seconds()) <= self.time_window_minutes * 60:
                cond_count += 1
                score += 0.4

            scene_place = scene.get("location")
            if place_value and scene_place and str(place_value).lower() == str(scene_place).lower():
                cond_count += 1
                score += 0.3

            sim = _cosine_similarity(topic_embedding, scene.get("embedding"))
            if sim >= self.topic_threshold:
                cond_count += 1
                score += min(0.3, sim * 0.3)

            if cond_count >= 2 and score > best_score:
                best_score = score
                best_scene = scene

        return best_scene

    def _attach_to_scene(
        self,
        *,
        scene: Dict[str, Any],
        memory_id: str,
        view_time: str,
        place_value: Optional[str],
        topic_label: str,
        topic_embedding: Optional[List[float]],
        characters: List[Dict[str, str]],
        namespace: str,
    ) -> None:
        scene_id = scene["id"]
        memory_ids = list(scene.get("memory_ids", []))
        if memory_id not in memory_ids:
            position = len(memory_ids)
            memory_ids.append(memory_id)
            self.db.add_scene_memory(scene_id, memory_id, position=position)
        participants = set(scene.get("participants", []))
        participants.update(c["entity_id"] for c in characters)

        updates: Dict[str, Any] = {
            "memory_ids": memory_ids,
            "participants": sorted(participants),
            "end_time": view_time,
            "location": place_value or scene.get("location"),
            "summary": scene.get("summary") or topic_label,
            "topic": scene.get("topic") or topic_label,
            "namespace": namespace,
        }
        if topic_embedding and scene.get("embedding"):
            old = scene.get("embedding")
            n = max(len(memory_ids) - 1, 1)
            updates["embedding"] = [
                (old[i] * n + topic_embedding[i]) / (n + 1)
                for i in range(len(topic_embedding))
            ]
        elif topic_embedding:
            updates["embedding"] = topic_embedding

        self.db.update_scene(scene_id, updates)
        self.db.update_memory(memory_id, {"scene_id": scene_id})

    @staticmethod
    def _safe_parse_time(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
