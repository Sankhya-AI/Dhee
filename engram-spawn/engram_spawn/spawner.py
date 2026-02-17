"""Spawner — decompose, delegate, and track sub-tasks."""

from __future__ import annotations

import logging
from typing import Any

from engram_spawn.decomposer import decompose_with_llm
from engram_spawn.tracker import ProgressTracker

logger = logging.getLogger(__name__)


class Spawner:
    """Task decomposition and delegation without a central agent.

    An agent breaks a complex task into sub-tasks, stores them in memory,
    and other agents pick them up through the router.
    """

    def __init__(self, memory: Any, router: Any = None,
                 llm: Any = None) -> None:
        self._memory = memory
        self._router = router
        self._llm = llm or getattr(memory, "llm", None)

        from engram.memory.tasks import TaskManager
        self._tm = TaskManager(memory)
        self._tracker = ProgressTracker(self._tm)

    def decompose(self, task_id: str, *, strategy: str = "auto",
                  max_subtasks: int = 5) -> list[dict]:
        """Break a task into sub-tasks using LLM.

        Stores them with parent_task_id. Does NOT create them as tasks yet —
        call spawn() to actually create them.
        """
        task = self._tm.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found")

        if not self._llm:
            raise ValueError("No LLM available for decomposition. Pass llm= to Spawner.")

        subtasks = decompose_with_llm(
            self._llm, task,
            strategy=strategy,
            max_subtasks=max_subtasks,
        )

        # Annotate with parent task info
        for i, st in enumerate(subtasks):
            st["parent_task_id"] = task_id
            st["phase"] = st.get("phase", i + 1)

        return subtasks

    def spawn(self, parent_task_id: str, subtasks: list[dict]) -> list[dict]:
        """Create sub-tasks and optionally route them to agents.

        Args:
            parent_task_id: The parent task that these are sub-tasks of.
            subtasks: List of sub-task dicts from decompose().

        Returns:
            List of created task dicts.
        """
        created = []
        prev_task_id = None

        # Sort by phase
        subtasks = sorted(subtasks, key=lambda s: s.get("phase", 1))

        for st in subtasks:
            task = self._tm.create_task(
                title=st.get("title", "Sub-task"),
                description=st.get("description", ""),
                tags=st.get("tags", []),
                parent_task_id=parent_task_id,
                extra_metadata={
                    "phase": st.get("phase", 1),
                    "spawn_strategy": st.get("strategy", "auto"),
                },
            )
            created.append(task)

            # Route if router is available
            if self._router and task.get("id"):
                try:
                    self._router.route(task["id"])
                except Exception as e:
                    logger.warning("Auto-route failed for subtask: %s", e)

            prev_task_id = task.get("id")

        logger.info(
            "Spawned %d sub-tasks for parent '%s'",
            len(created), parent_task_id,
        )
        return created

    def track(self, parent_task_id: str) -> dict:
        """Get progress: how many sub-tasks done, active, blocked."""
        return self._tracker.track(parent_task_id)

    def aggregate(self, parent_task_id: str) -> dict:
        """Collect results from completed sub-tasks into parent."""
        return self._tracker.aggregate(parent_task_id)

    def is_complete(self, parent_task_id: str) -> bool:
        """Check if all sub-tasks are done."""
        return self._tracker.is_complete(parent_task_id)

    def cancel(self, parent_task_id: str) -> int:
        """Cancel all incomplete sub-tasks. Returns count cancelled."""
        return self._tracker.cancel(parent_task_id)
