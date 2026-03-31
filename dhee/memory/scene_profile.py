"""Scene and profile delegation methods.

Extracted from memory/main.py — thin delegation layer for scene/profile
processor operations. These are not core to the 4-tool wedge but are
useful subsystems.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SceneProfileMixin:
    """Mixin that adds scene/profile methods to FullMemory.

    Delegates all work to self.scene_processor, self.profile_processor,
    and self.db. This is a mixin (not delegation) since the methods are
    thin wrappers that need direct access to FullMemory's attributes.
    """

    def _assign_to_scene(
        self,
        memory_id: str,
        content: str,
        embedding: Optional[List[float]],
        user_id: Optional[str],
        timestamp: str,
    ) -> None:
        """Assign a memory to an existing or new scene."""
        if not self.scene_processor or not user_id:
            return

        self.scene_processor.auto_close_stale(user_id)

        current_scene = self.db.get_open_scene(user_id)
        memory_row = self.db.get_memory(memory_id) or {}
        namespace = str(memory_row.get("namespace", "default") or "default").strip() or "default"
        if (
            current_scene
            and str(current_scene.get("namespace", "default") or "default").strip() != namespace
        ):
            detection = self.scene_processor.detect_boundary(
                content=content,
                timestamp=timestamp,
                current_scene=None,
                embedding=embedding,
            )
        else:
            detection = self.scene_processor.detect_boundary(
                content=content,
                timestamp=timestamp,
                current_scene=current_scene,
                embedding=embedding,
            )

        if detection.is_new_scene:
            if current_scene:
                self.scene_processor.close_scene(current_scene["id"], timestamp)

            topic = content[:60].strip()
            location = detection.detected_location

            self.scene_processor.create_scene(
                first_memory_id=memory_id,
                user_id=user_id,
                timestamp=timestamp,
                topic=topic,
                location=location,
                embedding=embedding,
                namespace=namespace,
            )
        else:
            if current_scene:
                self.scene_processor.add_memory_to_scene(
                    scene_id=current_scene["id"],
                    memory_id=memory_id,
                    embedding=embedding,
                    timestamp=timestamp,
                    namespace=namespace,
                )

    def _update_profiles(
        self,
        memory_id: str,
        content: str,
        metadata: Dict[str, Any],
        user_id: Optional[str],
    ) -> None:
        """Extract and apply profile updates from memory content."""
        if not self.profile_processor or not user_id:
            return

        updates: List[Any] = []
        if hasattr(self.profile_processor, "extract_profile_mentions_from_speakers"):
            try:
                updates.extend(
                    self.profile_processor.extract_profile_mentions_from_speakers(
                        content=content,
                        metadata=metadata,
                    )
                )
            except Exception as e:
                logger.debug("Speaker-based profile extraction failed: %s", e)

        updates.extend(
            self.profile_processor.extract_profile_mentions(
                content=content,
                metadata=metadata,
                user_id=user_id,
            )
        )

        # Merge duplicate profile updates before applying to reduce churn.
        merged_updates: Dict[Tuple[str, str], Any] = {}
        for update in updates:
            key = (str(update.profile_name or "").strip(), str(update.profile_type or "").strip())
            existing = merged_updates.get(key)
            if existing is None:
                merged_updates[key] = update
                continue
            for fact in list(getattr(update, "new_facts", []) or []):
                if fact not in existing.new_facts:
                    existing.new_facts.append(fact)
            for pref in list(getattr(update, "new_preferences", []) or []):
                if pref not in existing.new_preferences:
                    existing.new_preferences.append(pref)
            for rel in list(getattr(update, "new_relationships", []) or []):
                if rel not in existing.new_relationships:
                    existing.new_relationships.append(rel)

        for update in merged_updates.values():
            self.profile_processor.apply_update(
                profile_update=update,
                memory_id=memory_id,
                user_id=user_id,
            )

    # =========================================================================
    # Scene Queries
    # =========================================================================

    def get_scene(self, scene_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific scene by ID."""
        return self.db.get_scene(scene_id)

    def get_scenes(
        self,
        user_id: Optional[str] = None,
        topic: Optional[str] = None,
        start_after: Optional[str] = None,
        start_before: Optional[str] = None,
        namespace: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List scenes chronologically."""
        return self.db.get_scenes(
            user_id=user_id,
            topic=topic,
            start_after=start_after,
            start_before=start_before,
            namespace=namespace,
            limit=limit,
        )

    def search_scenes(self, query: str, user_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Semantic search over scene summaries."""
        if not self.scene_processor:
            return []
        return self.scene_processor.search_scenes(query=query, user_id=user_id, limit=limit)

    def get_scene_timeline(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get scenes in chronological order."""
        if not self.scene_processor:
            return []
        return self.scene_processor.get_scene_timeline(user_id=user_id, limit=limit)

    def get_scene_memories(self, scene_id: str) -> List[Dict[str, Any]]:
        """Get all memories in a scene."""
        return self.db.get_scene_memories(scene_id)

    # =========================================================================
    # Profile Queries
    # =========================================================================

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Get a character profile by ID."""
        return self.db.get_profile(profile_id)

    def get_all_profiles(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all profiles for a user."""
        return self.db.get_all_profiles(user_id=user_id)

    def get_self_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the self-profile for a user."""
        return self.db.get_profile_by_name("self", user_id=user_id)

    def search_profiles(self, query: str, user_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Search profiles by name or description."""
        if not self.profile_processor:
            return []
        return self.profile_processor.search_profiles(query=query, user_id=user_id, limit=limit)

    def update_profile(self, profile_id: str, updates: Dict[str, Any]) -> bool:
        """Update a profile."""
        return self.db.update_profile(profile_id, updates)

    def get_profile_memories(self, profile_id: str) -> List[Dict[str, Any]]:
        """Get memories linked to a profile."""
        return self.db.get_profile_memories(profile_id)

    # =========================================================================
    # Dashboard / Visualization
    # =========================================================================

    def get_constellation_data(self, user_id: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        """Get graph nodes + edges for the constellation force layout."""
        return self.db.get_constellation_data(user_id=user_id, limit=limit)

    def get_decay_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent decay history for dashboard sparkline."""
        return self.db.get_decay_log_entries(limit=limit)
