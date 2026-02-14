"""Codex agent adapter — uses the `codex` CLI via subprocess."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator

from engram_bridge.agents.base import AgentMessage, BaseAgent
from engram_bridge.config import AgentConfig


class CodexAgent(BaseAgent):
    """Runs OpenAI Codex CLI via subprocess, reading NDJSON output."""

    def __init__(self, config: AgentConfig):
        self._model = config.model or "gpt-5-codex"
        self._session_id: str | None = None
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def name(self) -> str:
        return "codex"

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def send(
        self, message: str, cwd: str, session_id: str | None = None
    ) -> AsyncIterator[AgentMessage]:
        sid = session_id or self._session_id or uuid.uuid4().hex[:12]
        self._session_id = sid

        if session_id:
            cmd = ["codex", "exec", "resume", session_id, "--json"]
        else:
            cmd = ["codex", "exec", message, "--json", "-C", cwd]
            if self._model:
                cmd += ["--model", self._model]

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
                "Codex CLI not found. Install it: npm install -g @openai/codex",
                sid, {},
            )
            return

        async for line in self._proc.stdout:
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                yield AgentMessage("text", line, sid, {})
                continue

            msg = self._parse_event(event, sid)
            if msg:
                yield msg

        await self._proc.wait()

        if self._proc.returncode and self._proc.returncode != 0:
            stderr = await self._proc.stderr.read()
            err = stderr.decode("utf-8", errors="replace").strip()
            if "rate" in err.lower() or "429" in err:
                yield AgentMessage("rate_limited", err, sid, {})
            else:
                yield AgentMessage("error", err or f"Exit code {self._proc.returncode}", sid, {})

        self._proc = None

    def _parse_event(self, event: dict, sid: str) -> AgentMessage | None:
        """Parse an NDJSON event from Codex CLI."""
        etype = event.get("type", "")

        if etype in ("message", "result", "text"):
            content = event.get("content", "") or event.get("result", "") or event.get("text", "")
            new_sid = event.get("session_id", sid)
            if new_sid:
                self._session_id = new_sid
            if content:
                return AgentMessage("text", content, self._session_id or sid, {})

        elif etype in ("tool_use", "function_call"):
            tool = event.get("name", event.get("function", "unknown"))
            return AgentMessage("tool_use", f"Using {tool}...", self._session_id or sid, {"tool": tool})

        elif etype == "error":
            msg = event.get("message", str(event))
            if "rate" in msg.lower() or "429" in msg:
                return AgentMessage("rate_limited", msg, self._session_id or sid, {})
            return AgentMessage("error", msg, self._session_id or sid, {})

        # Fallback: if there's a content field, emit as text
        if "content" in event and event["content"]:
            return AgentMessage("text", str(event["content"]), self._session_id or sid, {})

        return None

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
