"""Python SDK entry point for Dhee's universal agent runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from dhee.agent_runtime.run import Run
from dhee.plugin import DheePlugin


class Client:
    """Small framework-neutral runtime client.

    This is intentionally not an ElevenLabs, Gemini, or OpenAI client. It only
    translates agent lifecycle events into Dhee's existing cognition plugin.
    """

    def __init__(
        self,
        user_id: str = "default",
        app_id: str = "default-agent",
        data_dir: Optional[Union[str, Path]] = None,
        provider: Optional[str] = None,
        in_memory: bool = False,
        offline: bool = False,
    ):
        self.user_id = user_id
        self.app_id = app_id
        self.plugin = DheePlugin(
            user_id=user_id,
            data_dir=data_dir,
            provider=provider,
            in_memory=in_memory,
            offline=offline,
        )

    def run(
        self,
        task: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Run:
        return Run(
            plugin=self.plugin,
            user_id=self.user_id,
            app_id=self.app_id,
            task=task,
            run_id=run_id,
            metadata=metadata or {},
        )
