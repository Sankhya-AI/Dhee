"""engram-skills — Shareable tool/skill registry for AI agents.

Agents register skills (tools/functions) as memories. Other agents discover
and invoke skills via semantic search.

Usage::

    from engram.memory.main import Memory
    from engram_skills import SkillRegistry, SkillConfig

    memory = Memory(config=...)
    skills = SkillRegistry(memory)
    skills.register(name="run_tests", description="Run pytest on the project")
"""

from engram_skills.config import SkillConfig
from engram_skills.registry import SkillRegistry

__all__ = ["SkillRegistry", "SkillConfig"]
