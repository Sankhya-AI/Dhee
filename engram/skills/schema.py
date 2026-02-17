"""Skill and Trajectory data models.

Skills are SKILL.md files (YAML frontmatter + markdown body).
Trajectories are recorded agent action sequences used for skill mining.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from engram.skills.hashing import content_hash, skill_signature_hash, trajectory_hash


@dataclass
class Skill:
    """A reusable agent skill stored as a SKILL.md file."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    body_markdown: str = ""
    confidence: float = 0.5
    success_count: int = 0
    fail_count: int = 0
    use_count: int = 0
    source: str = "authored"  # "authored" | "mined" | "imported"
    source_trajectory_ids: List[str] = field(default_factory=list)
    signature_hash: str = ""
    content_hash_val: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_used_at: Optional[str] = None

    def __post_init__(self):
        if not self.signature_hash:
            self.signature_hash = skill_signature_hash(
                self.preconditions, self.steps, self.tags
            )
        if not self.content_hash_val and self.body_markdown:
            self.content_hash_val = content_hash(self.body_markdown)

    def to_skill_md(self) -> str:
        """Serialize to YAML frontmatter + markdown body."""
        frontmatter = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "preconditions": self.preconditions,
            "steps": self.steps,
            "confidence": round(self.confidence, 4),
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "use_count": self.use_count,
            "source": self.source,
            "source_trajectory_ids": self.source_trajectory_ids,
            "signature_hash": self.signature_hash,
            "content_hash": self.content_hash_val,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
        }
        yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
        body = self.body_markdown or self._generate_body()
        return f"---\n{yaml_str}---\n\n{body}\n"

    @classmethod
    def from_skill_md(cls, content: str) -> "Skill":
        """Parse a SKILL.md file into a Skill object."""
        content = content.strip()
        if not content.startswith("---"):
            # No frontmatter — treat entire content as body
            return cls(body_markdown=content)

        # Split frontmatter from body
        parts = content.split("---", 2)
        if len(parts) < 3:
            return cls(body_markdown=content)

        frontmatter_str = parts[1].strip()
        body = parts[2].strip()

        try:
            fm = yaml.safe_load(frontmatter_str) or {}
        except yaml.YAMLError:
            return cls(body_markdown=content)

        return cls(
            id=fm.get("id", str(uuid.uuid4())),
            name=fm.get("name", ""),
            description=fm.get("description", ""),
            tags=fm.get("tags", []),
            preconditions=fm.get("preconditions", []),
            steps=fm.get("steps", []),
            body_markdown=body,
            confidence=float(fm.get("confidence", 0.5)),
            success_count=int(fm.get("success_count", 0)),
            fail_count=int(fm.get("fail_count", 0)),
            use_count=int(fm.get("use_count", 0)),
            source=fm.get("source", "authored"),
            source_trajectory_ids=fm.get("source_trajectory_ids", []),
            signature_hash=fm.get("signature_hash", ""),
            content_hash_val=fm.get("content_hash", ""),
            created_at=fm.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=fm.get("updated_at", datetime.now(timezone.utc).isoformat()),
            last_used_at=fm.get("last_used_at"),
        )

    def _generate_body(self) -> str:
        """Generate markdown body from structured fields."""
        lines = [f"# {self.name}", ""]
        if self.description:
            lines.extend([self.description, ""])
        if self.preconditions:
            lines.append("## Preconditions")
            for p in self.preconditions:
                lines.append(f"- {p}")
            lines.append("")
        if self.steps:
            lines.append("## Steps")
            for i, s in enumerate(self.steps, 1):
                lines.append(f"{i}. {s}")
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "preconditions": self.preconditions,
            "steps": self.steps,
            "confidence": self.confidence,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "use_count": self.use_count,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
        }


@dataclass
class TrajectoryStep:
    """A single step in an agent's action sequence."""

    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    action: str = ""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    error: Optional[str] = None
    state_snapshot: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "tool": self.tool,
            "args": self.args,
            "result_summary": self.result_summary,
            "error": self.error,
            "state_snapshot": self.state_snapshot,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Trajectory:
    """A recorded sequence of agent actions for a task."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = "default"
    agent_id: str = "default"
    task_description: str = ""
    steps: List[TrajectoryStep] = field(default_factory=list)
    success: bool = False
    outcome_summary: str = ""
    trajectory_hash_val: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    mined_skill_ids: List[str] = field(default_factory=list)

    def compute_hash(self) -> str:
        """Compute trajectory hash from steps."""
        step_dicts = [s.to_dict() for s in self.steps]
        self.trajectory_hash_val = trajectory_hash(step_dicts)
        return self.trajectory_hash_val

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "task_description": self.task_description,
            "steps": [s.to_dict() for s in self.steps],
            "success": self.success,
            "outcome_summary": self.outcome_summary,
            "trajectory_hash": self.trajectory_hash_val,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "mined_skill_ids": self.mined_skill_ids,
        }
