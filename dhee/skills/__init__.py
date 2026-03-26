"""engram.skills — skill-learning agent memory system.

Skills are SKILL.md files (YAML frontmatter + markdown body) stored on
filesystem and indexed for semantic discovery. Confidence scores update
on success/failure. The Skill Miner compiles successful trajectories
into new skills via LLM extraction.
"""
