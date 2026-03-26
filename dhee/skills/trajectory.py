"""Trajectory recording and persistence for skill mining.

TrajectoryRecorder — one per task/episode, records agent actions.
TrajectoryStore — persists trajectories as memories for later mining.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dhee.skills.schema import Trajectory, TrajectoryStep

logger = logging.getLogger(__name__)


class TrajectoryRecorder:
    """Records agent actions for a single task/episode."""

    def __init__(
        self,
        task_description: str,
        user_id: str = "default",
        agent_id: str = "default",
    ):
        self.id = str(uuid.uuid4())
        self.task_description = task_description
        self.user_id = user_id
        self.agent_id = agent_id
        self.steps: List[TrajectoryStep] = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._step_start_time: Optional[float] = None

    def record_step(
        self,
        action: str,
        tool: str = "",
        args: Optional[Dict[str, Any]] = None,
        result_summary: str = "",
        error: Optional[str] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> TrajectoryStep:
        """Append a step to this trajectory."""
        duration_ms = None
        if self._step_start_time is not None:
            duration_ms = int((time.time() - self._step_start_time) * 1000)
        self._step_start_time = time.time()

        step = TrajectoryStep(
            action=action,
            tool=tool,
            args=args or {},
            result_summary=result_summary,
            error=error,
            state_snapshot=state_snapshot,
            duration_ms=duration_ms,
        )
        self.steps.append(step)
        return step

    def finalize(
        self,
        success: bool,
        outcome_summary: str = "",
    ) -> Trajectory:
        """Finalize the recording and return a Trajectory."""
        trajectory = Trajectory(
            id=self.id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            task_description=self.task_description,
            steps=self.steps,
            success=success,
            outcome_summary=outcome_summary,
            started_at=self.started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        trajectory.compute_hash()
        return trajectory


class TrajectoryStore:
    """Persists trajectories as memories for later retrieval and mining."""

    def __init__(self, db: Any, embedder: Any = None, vector_store: Any = None):
        self._db = db
        self._embedder = embedder
        self._vector_store = vector_store

    def save(self, trajectory: Trajectory) -> str:
        """Save trajectory as a memory record."""
        content = f"[trajectory] {trajectory.task_description}"
        metadata = {
            "memory_type": "trajectory",
            "trajectory_id": trajectory.id,
            "trajectory_hash": trajectory.trajectory_hash_val,
            "success": trajectory.success,
            "outcome_summary": trajectory.outcome_summary,
            "step_count": len(trajectory.steps),
            "started_at": trajectory.started_at,
            "completed_at": trajectory.completed_at,
            "agent_id": trajectory.agent_id,
            "steps_json": json.dumps([s.to_dict() for s in trajectory.steps], default=str),
            "mined_skill_ids": json.dumps(trajectory.mined_skill_ids),
        }

        memory_data = {
            "id": trajectory.id,
            "memory": content,
            "user_id": trajectory.user_id,
            "agent_id": trajectory.agent_id,
            "metadata": metadata,
            "categories": ["trajectories"],
            "created_at": trajectory.started_at,
            "updated_at": trajectory.completed_at or trajectory.started_at,
            "layer": "sml",
            "strength": 1.0 if trajectory.success else 0.5,
            "access_count": 0,
            "last_accessed": trajectory.completed_at or trajectory.started_at,
            "status": "active",
            "namespace": "trajectories",
            "memory_type": "trajectory",
            "content_hash": trajectory.trajectory_hash_val,
        }

        try:
            self._db.add_memory(memory_data)
        except Exception as e:
            logger.warning("Failed to save trajectory %s: %s", trajectory.id, e)

        # Index in vector store for semantic search
        if self._embedder and self._vector_store:
            try:
                embedding = self._embedder.embed(content, memory_action="add")
                self._vector_store.insert(
                    vectors=[embedding],
                    payloads=[{
                        "trajectory_id": trajectory.id,
                        "user_id": trajectory.user_id,
                        "memory": content,
                        "success": trajectory.success,
                    }],
                    ids=[trajectory.id],
                )
            except Exception as e:
                logger.warning("Failed to index trajectory %s: %s", trajectory.id, e)

        return trajectory.id

    def get(self, trajectory_id: str) -> Optional[Trajectory]:
        """Retrieve a trajectory by ID."""
        mem = self._db.get_memory(trajectory_id)
        if not mem:
            return None
        return self._mem_to_trajectory(mem)

    def find_successful(
        self,
        task_query: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Trajectory]:
        """Find successful trajectories, optionally filtered by task query."""
        memories = self._db.get_all_memories(
            user_id=user_id,
            limit=limit * 3,
        )

        trajectories = []
        for mem in memories:
            md = mem.get("metadata", {})
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except (json.JSONDecodeError, TypeError):
                    continue

            if md.get("memory_type") != "trajectory":
                continue
            if not md.get("success"):
                continue
            if task_query:
                content = mem.get("memory", "")
                if task_query.lower() not in content.lower():
                    continue

            t = self._mem_to_trajectory(mem)
            if t:
                trajectories.append(t)
            if len(trajectories) >= limit:
                break

        return trajectories

    def find_by_hash(self, trajectory_hash: str) -> Optional[Trajectory]:
        """Find trajectory by its hash."""
        memories = self._db.get_all_memories(limit=500)
        for mem in memories:
            md = mem.get("metadata", {})
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except (json.JSONDecodeError, TypeError):
                    continue
            if md.get("trajectory_hash") == trajectory_hash:
                return self._mem_to_trajectory(mem)
        return None

    def _mem_to_trajectory(self, mem: Dict[str, Any]) -> Optional[Trajectory]:
        """Convert a memory record back to a Trajectory."""
        md = mem.get("metadata", {})
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except (json.JSONDecodeError, TypeError):
                return None

        steps_json = md.get("steps_json", "[]")
        try:
            steps_data = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
        except (json.JSONDecodeError, TypeError):
            steps_data = []

        steps = [
            TrajectoryStep(
                timestamp=s.get("timestamp", ""),
                action=s.get("action", ""),
                tool=s.get("tool", ""),
                args=s.get("args", {}),
                result_summary=s.get("result_summary", ""),
                error=s.get("error"),
                state_snapshot=s.get("state_snapshot"),
                duration_ms=s.get("duration_ms"),
            )
            for s in steps_data
        ]

        mined_ids_raw = md.get("mined_skill_ids", "[]")
        try:
            mined_ids = json.loads(mined_ids_raw) if isinstance(mined_ids_raw, str) else mined_ids_raw
        except (json.JSONDecodeError, TypeError):
            mined_ids = []

        return Trajectory(
            id=mem.get("id", md.get("trajectory_id", "")),
            user_id=mem.get("user_id", "default"),
            agent_id=mem.get("agent_id", md.get("agent_id", "default")),
            task_description=mem.get("memory", "").replace("[trajectory] ", ""),
            steps=steps,
            success=md.get("success", False),
            outcome_summary=md.get("outcome_summary", ""),
            trajectory_hash_val=md.get("trajectory_hash", ""),
            started_at=md.get("started_at", mem.get("created_at", "")),
            completed_at=md.get("completed_at"),
            mined_skill_ids=mined_ids,
        )
