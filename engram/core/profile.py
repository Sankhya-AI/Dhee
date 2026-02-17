"""
Character Profile Processor for engram.

Tracks people and entities mentioned in memories, building rich profiles:
- Facts, preferences, relationships
- Sentiment tracking
- LLM-generated narrative summaries
- Self-profile auto-creation (for "I prefer...", "my name is...")
- Fuzzy name matching via aliases
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ProfileUpdate:
    """A detected update to apply to a profile."""
    profile_name: str
    profile_type: str = "contact"  # self | contact | entity
    new_facts: List[str] = field(default_factory=list)
    new_preferences: List[str] = field(default_factory=list)
    new_relationships: List[Dict[str, str]] = field(default_factory=list)
    sentiment: Optional[str] = None
    is_new: bool = False


# Patterns for self-referential statements
_SELF_PATTERNS = [
    re.compile(r"\b(?:I|my|me)\s+(?:prefer|like|love|use|want|need|enjoy|hate|dislike)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(?:name|email|job|role|title|team|company|favorite|preferred)\b", re.IGNORECASE),
    re.compile(r"\bI(?:'m| am)\s+(?:a|an|the)\s+", re.IGNORECASE),
    re.compile(r"\bcall me\b", re.IGNORECASE),
]

# Patterns for third-person mentions
_PERSON_PATTERN = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b"  # Two+ capitalized words = likely a name
)

_PREFERENCE_EXTRACT = re.compile(
    r"(?:I|my)\s+(?:prefer|like|love|use|want|enjoy|favorite)\s+(.+?)(?:\.|,|$)",
    re.IGNORECASE,
)

_NAME_EXTRACT = re.compile(
    r"(?:my name is|call me|I'm|I am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    re.IGNORECASE,
)


from engram.utils.math import cosine_similarity as _cosine_similarity


class ProfileProcessor:
    """Manages character profile detection, creation, and updates."""

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
        self.auto_detect = cfg.get("auto_detect_profiles", True)
        self.use_llm_extraction = cfg.get("use_llm_extraction", True)
        self.narrative_regen_threshold = cfg.get("narrative_regenerate_threshold", 10)
        self.self_auto_create = cfg.get("self_profile_auto_create", True)
        self.max_facts = cfg.get("max_facts_per_profile", 100)
        # Track updates since last narrative regeneration
        self._update_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_profile_mentions(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> List[ProfileUpdate]:
        """Extract profile mentions from memory content."""
        updates: List[ProfileUpdate] = []

        # Self-profile updates
        is_self_ref = any(p.search(content) for p in _SELF_PATTERNS)
        if is_self_ref:
            update = ProfileUpdate(
                profile_name="self",
                profile_type="self",
            )
            # Extract preferences
            for match in _PREFERENCE_EXTRACT.finditer(content):
                pref = match.group(1).strip()
                if pref:
                    update.new_preferences.append(pref)

            # Extract name
            name_match = _NAME_EXTRACT.search(content)
            if name_match:
                update.new_facts.append(f"Name: {name_match.group(1)}")

            # General self-facts
            if not update.new_preferences and not update.new_facts:
                update.new_facts.append(content.strip())

            updates.append(update)

        # Third-person mentions
        if self.auto_detect:
            seen_names: Set[str] = set()
            for match in _PERSON_PATTERN.finditer(content):
                name = match.group(1).strip()
                # Filter out common false positives
                if name.lower() in {"the user", "the system", "the app", "the team"}:
                    continue
                if name not in seen_names:
                    seen_names.add(name)
                    update = ProfileUpdate(
                        profile_name=name,
                        profile_type="contact",
                        new_facts=[content.strip()],
                    )
                    updates.append(update)

        # LLM extraction for richer profiles
        if self.use_llm_extraction and self.llm and not updates:
            llm_updates = self._extract_with_llm(content)
            updates.extend(llm_updates)

        return updates

    def _extract_with_llm(self, content: str) -> List[ProfileUpdate]:
        """Use LLM to extract person mentions and facts."""
        prompt = (
            "Extract any people or entities mentioned in the following text. "
            "Return a JSON array of objects with fields: "
            '"name" (string), "type" ("self"|"contact"|"entity"), '
            '"facts" (array of strings), "preferences" (array of strings).\n'
            "If no people are mentioned, return an empty array.\n\n"
            f"Text: {content}\n\nJSON:"
        )
        try:
            response = self.llm.generate(prompt)
            # Use raw_decode to parse the first complete JSON array,
            # ignoring trailing LLM commentary that may contain [] chars.
            arr_start = response.find("[")
            if arr_start >= 0:
                data, _ = json.JSONDecoder().raw_decode(response, arr_start)
                updates = []
                for item in data:
                    name = item.get("name", "").strip()
                    if name:
                        updates.append(ProfileUpdate(
                            profile_name=name,
                            profile_type=item.get("type", "contact"),
                            new_facts=item.get("facts", []),
                            new_preferences=item.get("preferences", []),
                        ))
                return updates
        except Exception as e:
            logger.warning(f"LLM profile extraction failed: {e}")
        return []

    # ------------------------------------------------------------------
    # Profile lifecycle
    # ------------------------------------------------------------------

    def ensure_self_profile(self, user_id: str) -> Dict[str, Any]:
        """Create or return the self-profile for a user."""
        existing = self.db.get_profile_by_name("self", user_id=user_id)
        if existing:
            return existing

        profile_id = str(uuid.uuid4())
        profile_data = {
            "id": profile_id,
            "user_id": user_id,
            "name": "self",
            "profile_type": "self",
            "narrative": "The user's self-profile. Updated automatically from first-person statements.",
            "facts": [],
            "preferences": [],
            "relationships": [],
        }
        self.db.add_profile(profile_data)
        return profile_data

    def apply_update(
        self,
        profile_update: ProfileUpdate,
        memory_id: str,
        user_id: str,
    ) -> str:
        """Apply a ProfileUpdate to an existing or new profile. Returns profile_id."""
        name = profile_update.profile_name

        # Find existing profile
        if name == "self" or profile_update.profile_type == "self":
            profile = self.db.get_profile_by_name("self", user_id=user_id)
            if not profile and self.self_auto_create:
                profile = self.ensure_self_profile(user_id)
        else:
            profile = self._find_profile(name, user_id)

        if profile:
            profile_id = profile["id"]
            self._merge_into_profile(profile, profile_update)
        else:
            # Create new profile
            profile_id = str(uuid.uuid4())
            embedding = None
            if self.embedder:
                embedding = self.embedder.embed(name, memory_action="add")
            profile_data = {
                "id": profile_id,
                "user_id": user_id,
                "name": name,
                "profile_type": profile_update.profile_type,
                "facts": profile_update.new_facts[:self.max_facts],
                "preferences": profile_update.new_preferences,
                "relationships": profile_update.new_relationships,
                "sentiment": profile_update.sentiment,
                "embedding": embedding,
            }
            self.db.add_profile(profile_data)

        # Link memory
        role = "about" if profile_update.profile_type == "self" else "mentioned"
        self.db.add_profile_memory(profile_id, memory_id, role=role)

        # Track updates for narrative regeneration
        count = self._update_counts.get(profile_id, 0) + 1
        self._update_counts[profile_id] = count
        if count >= self.narrative_regen_threshold:
            self._regenerate_narrative(profile_id)
            self._update_counts[profile_id] = 0

        return profile_id

    def _find_profile(self, name: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Find a profile by name or alias, with fuzzy matching."""
        # Fast path: exact or alias match (uses indexed SQL query).
        profile = self.db.get_profile_by_name(name, user_id=user_id)
        if profile:
            return profile

        # Slow path: substring match on name (e.g. "John" matches "John Smith").
        if hasattr(self.db, "find_profile_by_substring"):
            return self.db.find_profile_by_substring(name, user_id=user_id)

        return None

    def _merge_into_profile(
        self, profile: Dict[str, Any], update: ProfileUpdate
    ) -> None:
        """Merge new facts/preferences into an existing profile."""
        changes: Dict[str, Any] = {}

        # Merge facts (deduplicate)
        existing_facts = list(profile.get("facts", []))
        existing_set = {f.lower() for f in existing_facts}
        for fact in update.new_facts:
            if fact.lower() not in existing_set and len(existing_facts) < self.max_facts:
                existing_facts.append(fact)
                existing_set.add(fact.lower())
        if len(existing_facts) != len(profile.get("facts", [])):
            changes["facts"] = existing_facts

        # Merge preferences
        existing_prefs = list(profile.get("preferences", []))
        existing_pref_set = {p.lower() for p in existing_prefs}
        for pref in update.new_preferences:
            if pref.lower() not in existing_pref_set:
                existing_prefs.append(pref)
                existing_pref_set.add(pref.lower())
        if len(existing_prefs) != len(profile.get("preferences", [])):
            changes["preferences"] = existing_prefs

        # Merge relationships
        existing_rels = list(profile.get("relationships", []))
        for rel in update.new_relationships:
            if rel not in existing_rels:
                existing_rels.append(rel)
        if len(existing_rels) != len(profile.get("relationships", [])):
            changes["relationships"] = existing_rels

        # Update sentiment
        if update.sentiment:
            changes["sentiment"] = update.sentiment

        # Add name as alias if different from profile name
        if (
            update.profile_name != profile["name"]
            and update.profile_name.lower() != "self"
        ):
            aliases = list(profile.get("aliases", []))
            if update.profile_name not in aliases:
                aliases.append(update.profile_name)
                changes["aliases"] = aliases

        if changes:
            self.db.update_profile(profile["id"], changes)

    # ------------------------------------------------------------------
    # Narrative
    # ------------------------------------------------------------------

    def _regenerate_narrative(self, profile_id: str) -> None:
        """Regenerate the narrative summary for a profile."""
        if not self.llm:
            return

        profile = self.db.get_profile(profile_id)
        if not profile:
            return

        memories = self.db.get_profile_memories(profile_id)
        narrative = self.generate_narrative(profile, memories)
        if narrative:
            self.db.update_profile(profile_id, {"narrative": narrative})

    def generate_narrative(
        self,
        profile: Dict[str, Any],
        memories: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """Generate a narrative summary for a profile."""
        if not self.llm:
            return None

        name = profile.get("name", "Unknown")
        p_type = profile.get("profile_type", "contact")
        facts = profile.get("facts", [])[:20]
        prefs = profile.get("preferences", [])[:10]

        facts_text = "\n".join(f"- {f}" for f in facts) if facts else "None"
        prefs_text = "\n".join(f"- {p}" for p in prefs) if prefs else "None"

        memory_texts = ""
        if memories:
            texts = [m.get("memory", "") for m in memories[:10] if m.get("memory")]
            memory_texts = "\n".join(f"- {t}" for t in texts)

        if p_type == "self":
            prompt = (
                "Write a concise first-person profile summary (2-3 sentences) based on:\n\n"
                f"Known facts:\n{facts_text}\n\n"
                f"Preferences:\n{prefs_text}\n\n"
                f"Recent memories:\n{memory_texts}\n\n"
                "Summary:"
            )
        else:
            prompt = (
                f"Write a concise profile summary (2-3 sentences) about {name} based on:\n\n"
                f"Known facts:\n{facts_text}\n\n"
                f"Preferences:\n{prefs_text}\n\n"
                f"Recent related memories:\n{memory_texts}\n\n"
                "Summary:"
            )

        try:
            return self.llm.generate(prompt).strip()
        except Exception as e:
            logger.warning(f"Profile narrative generation failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_profiles(
        self,
        query: str,
        user_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search profiles by name, facts, or semantic similarity."""
        all_profiles = self.db.get_all_profiles(user_id=user_id)
        if not all_profiles:
            return []

        query_lower = query.lower()
        query_words = query_lower.split()

        if self.embedder:
            query_embedding = self.embedder.embed(query, memory_action="search")
            scored = []
            for p in all_profiles:
                p_emb = p.get("embedding")
                if p_emb:
                    sim = _cosine_similarity(query_embedding, p_emb)
                    scored.append((p, sim))
                else:
                    text = f"{p.get('name', '')} {' '.join(p.get('facts', []))} {' '.join(p.get('preferences', []))}".lower()
                    kw_score = sum(1 for w in query_words if w in text) * 0.1
                    if kw_score > 0:
                        scored.append((p, kw_score))
            scored.sort(key=lambda x: x[1], reverse=True)
            results = []
            for p, score in scored[:limit]:
                p["search_score"] = round(score, 4)
                results.append(p)
            return results
        else:
            scored = []
            for p in all_profiles:
                text = f"{p.get('name', '')} {' '.join(p.get('facts', []))} {' '.join(p.get('preferences', []))}".lower()
                name_match = query_lower in p.get("name", "").lower()
                score = sum(1 for w in query_words if w in text)
                if score > 0 or name_match:
                    scored.append((p, score + (1 if name_match else 0)))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [p for p, _ in scored[:limit]]
