"""Generic subprocess agent — wraps any CLI tool as an agent."""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

from engram_bridge.agents.base import AgentMessage, BaseAgent
from engram_bridge.config import AgentConfig


class CustomAgent(BaseAgent):
    """Wraps an arbitrary CLI command as an agent.

    Config example (aider):
        {
            "type": "custom",
            "command": ["aider", "--message", "{prompt}", "--yes"],
            "cwd_flag": "--cwd"
        }

    The placeholder {prompt} in the command list is replaced with the user message.
    If cwd_flag is set, it's appended with the working directory.
    """

    def __init__(self, config: AgentConfig, agent_name: str = "custom"):
        self._command_template = config.command
        self._cwd_flag = config.cwd_flag
        self._agent_name = agent_name
        self._session_id: str | None = None
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def name(self) -> str:
        return self._agent_name

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def send(
        self, message: str, cwd: str, session_id: str | None = None
    ) -> AsyncIterator[AgentMessage]:
        sid = session_id or self._session_id or uuid.uuid4().hex[:12]
        self._session_id = sid

        # Build command by substituting {prompt}
        cmd = [part.replace("{prompt}", message) for part in self._command_template]
        if self._cwd_flag:
            cmd += [self._cwd_flag, cwd]

        if not cmd:
            yield AgentMessage("error", "No command configured for this agent.", sid, {})
            return

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except FileNotFoundError:
            yield AgentMessage(
                "error",
                f"Command not found: {cmd[0]}",
                sid, {},
            )
            return

        # Read stdout line by line
        async for line in self._proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                yield AgentMessage("text", text, sid, {})

        await self._proc.wait()

        if self._proc.returncode and self._proc.returncode != 0:
            stderr = await self._proc.stderr.read()
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                yield AgentMessage("error", err[:1000], sid, {})

        self._proc = None

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
