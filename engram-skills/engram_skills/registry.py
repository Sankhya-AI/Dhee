"""SkillRegistry — register, search, and invoke skills via memory."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from engram_skills.skill import Skill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Registry of shareable skills/tools backed by Engram Memory.

    Skills are stored as memories with memory_type="skill" for semantic
    discovery. Local callables can be invoked directly.
    """

    def __init__(self, memory: Any, user_id: str = "system") -> None:
        self._memory = memory
        self._user_id = user_id
        self._local_skills: dict[str, Callable] = {}

    # ── Helpers ──

    def _find_skill_memory(self, skill_name: str) -> dict | None:
        """Find existing skill memory by name."""
        results = self._memory.get_all(
            user_id=self._user_id,
            filters={"memory_type": "skill", "skill_name": skill_name},
            limit=1,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return items[0] if items else None

    def _format_skill(self, mem: dict) -> dict:
        """Format a raw memory into a skill dict."""
        md = mem.get("metadata", {})
        return {
            "id": mem.get("id", ""),
            "name": md.get("skill_name", ""),
            "description": md.get("skill_description", ""),
            "parameters": md.get("skill_parameters", {}),
            "examples": md.get("skill_examples", []),
            "agent_id": md.get("skill_agent_id", ""),
            "tags": md.get("skill_tags", []),
            "created_at": md.get("skill_created_at", ""),
            "invocable": md.get("skill_name", "") in self._local_skills,
        }

    def _build_content(self, name: str, description: str,
                       tags: list[str] | None = None) -> str:
        """Build searchable content for semantic matching."""
        parts = [f"{name}: {description}"]
        if tags:
            parts.append(f"Tags: {', '.join(tags)}")
        return " ".join(parts)

    # ── Public API ──

    def register(self, *, name: str, description: str,
                 parameters: dict | None = None,
                 examples: list[str] | None = None,
                 agent_id: str | None = None,
                 tags: list[str] | None = None,
                 callable: Callable | None = None) -> dict:
        """Register a skill. Stored as a memory for discovery."""
        now = datetime.now(timezone.utc).isoformat()
        content = self._build_content(name, description, tags)

        metadata = {
            "memory_type": "skill",
            "skill_name": name,
            "skill_description": description,
            "skill_parameters": parameters or {},
            "skill_examples": examples or [],
            "skill_agent_id": agent_id or "",
            "skill_tags": tags or [],
            "skill_created_at": now,
        }

        # Store callable locally if provided
        if callable is not None:
            self._local_skills[name] = callable

        existing = self._find_skill_memory(name)
        if existing:
            self._memory.update(existing["id"], {
                "content": content,
                "metadata": {**existing.get("metadata", {}), **metadata},
            })
            updated = self._memory.get(existing["id"])
            return self._format_skill(updated) if updated else self._format_skill(existing)

        result = self._memory.add(
            content,
            user_id=self._user_id,
            metadata=metadata,
            categories=["skills"],
            infer=False,
        )
        items = result.get("results", [])
        if items:
            return self._format_skill(items[0])
        return {"name": name, "description": description}

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search over registered skills."""
        results = self._memory.search(
            query,
            user_id=self._user_id,
            filters={"memory_type": "skill"},
            limit=limit,
            use_echo_rerank=False,
        )
        items = results.get("results", [])
        skills = []
        for item in items:
            skill = self._format_skill(item)
            skill["similarity"] = item.get("score", item.get("similarity", 0.0))
            skills.append(skill)
        return skills

    def get(self, skill_name: str) -> dict | None:
        """Get a skill by exact name."""
        mem = self._find_skill_memory(skill_name)
        if mem:
            return self._format_skill(mem)
        return None

    def invoke(self, skill_name: str, **params: Any) -> Any:
        """Invoke a locally-registered skill."""
        fn = self._local_skills.get(skill_name)
        if not fn:
            raise ValueError(f"Skill '{skill_name}' not found locally. Only local callables can be invoked.")
        return fn(**params)

    def list(self, agent_id: str | None = None) -> list[dict]:
        """List all registered skills."""
        filters: dict[str, Any] = {"memory_type": "skill"}
        if agent_id:
            filters["skill_agent_id"] = agent_id
        results = self._memory.get_all(
            user_id=self._user_id,
            filters=filters,
            limit=500,
        )
        items = results.get("results", []) if isinstance(results, dict) else results
        return [self._format_skill(m) for m in items]

    def remove(self, skill_id: str) -> bool:
        """Unregister a skill."""
        try:
            # Also remove local callable if name matches
            mem = self._memory.get(skill_id)
            if mem:
                name = mem.get("metadata", {}).get("skill_name", "")
                self._local_skills.pop(name, None)
            self._memory.delete(skill_id)
            return True
        except Exception:
            return False

    def load_module(self, module_path: str) -> int:
        """Load skills from a Python module. Returns count loaded."""
        from engram_skills.loader import load_skills_from_module

        skills = load_skills_from_module(module_path)
        count = 0
        for s in skills:
            self.register(
                name=s.name,
                description=s.description,
                parameters=s.parameters,
                examples=s.examples,
                tags=s.tags,
                callable=s.callable,
            )
            count += 1
        return count
