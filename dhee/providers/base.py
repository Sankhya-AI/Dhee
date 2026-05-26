"""Shared helpers for native Dhee provider integrations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from dhee.agent_runtime import Client


class ProviderMemoryRuntime:
    """Thin holder for one provider run backed by Dhee memory."""

    def __init__(
        self,
        user_id: str,
        app_id: str,
        task: str,
        channel: str = "generic",
        run_id: Optional[str] = None,
        data_dir: Optional[Union[str, Path]] = None,
        in_memory: bool = False,
        offline: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.user_id = user_id
        self.app_id = app_id
        self.task = task
        self.channel = channel
        self.client = Client(
            user_id=user_id,
            app_id=app_id,
            data_dir=data_dir,
            in_memory=in_memory,
            offline=offline,
        )
        self.run = self.client.run(
            task=task,
            run_id=run_id,
            metadata=metadata or {},
        )
        self.patch = None

    def start(self, input: Optional[str] = None, budget_tokens: int = 900):
        self.patch = self.run.before(
            input=input,
            budget_tokens=budget_tokens,
            channel=self.channel,
        )
        return self.patch

    def dynamic_variables(self) -> dict[str, Any]:
        patch = self.patch or self.start()
        return dict(patch.dynamic_variables)

    def tool(
        self,
        action: str,
        query: Optional[str] = None,
        content: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.run.tool(
            action=action,
            query=query,
            content=content,
            summary=summary,
            metadata=metadata,
        ).model_dump()

    def event(
        self,
        event_type: str,
        content: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.run.event(event_type, content=content, metadata=metadata)

    def finish(
        self,
        outcome: str = "completed",
        summary: Optional[str] = None,
        outcome_score: Optional[float] = None,
        what_worked: Optional[str] = None,
        what_failed: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.run.finish(
            outcome=outcome,
            summary=summary,
            outcome_score=outcome_score,
            what_worked=what_worked,
            what_failed=what_failed,
            metadata=metadata,
        )
