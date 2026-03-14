"""SkillExecutor — search, apply, and inject skills into agent context.

The executor bridges skill storage with agent workflows by:
1. Searching for relevant skills given a query
2. Formatting skills as injectable markdown recipes
3. Tracking which skills were applied
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram.skills.schema import Skill
from engram.skills.store import SkillStore

logger = logging.getLogger(__name__)


class SkillExecutor:
    """Searches for and applies skills to agent context."""

    def __init__(self, skill_store: SkillStore):
        self._store = skill_store

    def apply(
        self,
        skill_id: str,
        context: Optional[Dict[str, Any]] = None,
        bindings: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Apply a specific skill by ID.

        If the skill has structure and bindings are provided, renders
        steps with bindings and includes gap analysis.

        Returns a dict with the skill recipe and metadata.
        """
        skill = self._store.get(skill_id)
        if skill is None:
            return {"error": f"Skill not found: {skill_id}", "injected": False}

        # Increment use count
        skill.use_count += 1
        skill.last_used_at = datetime.now(timezone.utc).isoformat()
        skill.updated_at = skill.last_used_at
        self._store.save(skill)

        # Structural apply path
        structure = skill.get_structure()
        if structure and bindings:
            recipe = self._build_structural_recipe(skill, structure, bindings)
            gap_analysis = self._analyze_gaps(skill, structure, bindings)
            return {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "recipe": recipe,
                "confidence": round(skill.confidence, 4),
                "injected": True,
                "source": skill.source,
                "structural": True,
                "gap_analysis": gap_analysis,
            }

        recipe = self._build_recipe(skill, context)
        return {
            "skill_id": skill.id,
            "skill_name": skill.name,
            "recipe": recipe,
            "confidence": round(skill.confidence, 4),
            "injected": True,
            "source": skill.source,
        }

    def search_and_apply(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        min_confidence: float = 0.3,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Find the best matching skill and apply it.

        Returns the skill recipe if found, or an empty result.
        """
        skills = self._store.search(
            query=query,
            limit=1,
            tags=tags,
            min_confidence=min_confidence,
        )

        if not skills:
            return {
                "injected": False,
                "message": "No matching skill found",
                "query": query,
            }

        best = skills[0]
        return self.apply(best.id, context)

    def search(
        self,
        query: str,
        limit: int = 5,
        tags: Optional[List[str]] = None,
        min_confidence: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Search for skills without applying them."""
        skills = self._store.search(
            query=query,
            limit=limit,
            tags=tags,
            min_confidence=min_confidence,
        )
        return [
            {
                "skill_id": s.id,
                "name": s.name,
                "description": s.description,
                "confidence": round(s.confidence, 4),
                "tags": s.tags,
                "use_count": s.use_count,
                "source": s.source,
            }
            for s in skills
        ]

    def _build_recipe(
        self,
        skill: Skill,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Format a skill as injectable markdown for agent context."""
        lines = [
            f"## Skill: {skill.name}",
            f"**Confidence:** {skill.confidence:.0%}  ",
            f"**Source:** {skill.source}  ",
            f"**Used:** {skill.use_count} times",
            "",
        ]

        if skill.description:
            lines.extend([skill.description, ""])

        if skill.preconditions:
            lines.append("### Preconditions")
            for p in skill.preconditions:
                lines.append(f"- {p}")
            lines.append("")

        if skill.steps:
            lines.append("### Steps")
            for i, step in enumerate(skill.steps, 1):
                lines.append(f"{i}. {step}")
            lines.append("")

        if skill.body_markdown:
            lines.extend(["### Details", skill.body_markdown, ""])

        if skill.tags:
            lines.append(f"**Tags:** {', '.join(skill.tags)}")

        return "\n".join(lines)

    def search_structural(
        self,
        query_steps: List[str],
        limit: int = 5,
        min_similarity: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """Search for skills by structural similarity to given steps.

        Decomposes query_steps into templates, then compares against
        all cached skills that have structure.
        """
        from engram.skills.structure import (
            extract_slots_heuristic,
            structural_similarity,
        )

        _, query_structured = extract_slots_heuristic(query_steps)

        results = []
        for skill in self._store.list_all():
            structure = skill.get_structure()
            if structure is None:
                continue

            sim = structural_similarity(query_structured, structure.structured_steps)
            if sim >= min_similarity:
                results.append({
                    "skill_id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "confidence": round(skill.confidence, 4),
                    "structural_similarity": round(sim, 4),
                    "tags": skill.tags,
                })

        results.sort(key=lambda r: r["structural_similarity"], reverse=True)
        return results[:limit]

    def _build_structural_recipe(
        self,
        skill: Skill,
        structure: "SkillStructure",
        bindings: Dict[str, str],
    ) -> str:
        """Format a structured skill as injectable markdown with slot bindings."""
        from engram.skills.structure import SkillStructure

        lines = [
            f"## Skill: {skill.name} (Structural)",
            f"**Confidence:** {skill.confidence:.0%}  ",
            f"**Source:** {skill.source}  ",
            f"**Used:** {skill.use_count} times",
            "",
        ]

        if skill.description:
            lines.extend([skill.description, ""])

        # Slot bindings table
        if structure.slots:
            lines.append("### Slot Bindings")
            lines.append("| Slot | Value | Status |")
            lines.append("|------|-------|--------|")
            for slot in structure.slots:
                value = bindings.get(slot.name, "—")
                known = structure.known_bindings.get(slot.name, [])
                if value == "—":
                    status = "UNBOUND"
                elif value.lower() in [v.lower() for v in known]:
                    status = "proven"
                else:
                    status = "UNTESTED"
                lines.append(f"| {slot.name} | {value} | {status} |")
            lines.append("")

        # Rendered steps with role markers
        rendered = structure.render_steps(bindings)
        lines.append("### Steps")
        for i, (step_text, sstep) in enumerate(zip(rendered, structure.structured_steps), 1):
            role_marker = "[S]" if sstep.role == "structural" else "[V]"
            lines.append(f"{i}. {role_marker} {step_text}")
        lines.append("")

        return "\n".join(lines)

    def _analyze_gaps(
        self,
        skill: Skill,
        structure: "SkillStructure",
        bindings: Dict[str, str],
    ) -> Dict[str, Any]:
        """Run gap analysis for a structural apply."""
        from engram.skills.structure import analyze_gaps
        report = analyze_gaps(structure, bindings, skill.confidence)
        return report.to_dict()
