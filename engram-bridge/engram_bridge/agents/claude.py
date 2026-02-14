"""Claude Code agent adapter — uses the `claude` CLI with JSON streaming."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator

from engram_bridge.agents.base import AgentMessage, BaseAgent
from engram_bridge.config import AgentConfig


class ClaudeAgent(BaseAgent):
    """Runs Claude Code via subprocess, reading NDJSON streaming output."""

    def __init__(self, config: AgentConfig):
        self._model = config.model or "claude-opus-4-6"
        self._allowed_tools = config.allowed_tools
        self._session_id: str | None = None
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def name(self) -> str:
        return "claude-code"

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def send(
        self, message: str, cwd: str, session_id: str | None = None
    ) -> AsyncIterator[AgentMessage]:
        sid = session_id or self._session_id or uuid.uuid4().hex[:12]
        self._session_id = sid

        cmd = [
            "claude", "--json",
            "--model", self._model,
            "--print",          # non-interactive mode
            "--output-format", "stream-json",
        ]
        if self._allowed_tools:
            cmd += ["--allowedTools", ",".join(self._allowed_tools)]
        if session_id:
            cmd += ["--resume", session_id]
        cmd += ["-p", message]

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
                "Claude CLI not found. Install it: npm install -g @anthropic-ai/claude-code",
                sid, {},
            )
            return

        collected_text: list[str] = []

        async for line in self._proc.stdout:
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Plain text fallback
                collected_text.append(line)
                continue

            msg = self._parse_event(event, sid)
            if msg:
                if msg.type == "text":
                    collected_text.append(msg.content)
                yield msg

        await self._proc.wait()

        # If process failed, read stderr
        if self._proc.returncode and self._proc.returncode != 0:
            stderr = await self._proc.stderr.read()
            err = stderr.decode("utf-8", errors="replace").strip()
            if "rate" in err.lower() or "429" in err:
                yield AgentMessage("rate_limited", err, sid, {})
            else:
                yield AgentMessage("error", err or f"Exit code {self._proc.returncode}", sid, {})

        # Emit final combined text if we got plain output
        if collected_text and not any(True for _ in []):
            # The text messages were already yielded above
            pass

        self._proc = None

    def _parse_event(self, event: dict, sid: str) -> AgentMessage | None:
        """Parse a JSON streaming event from Claude CLI."""
        etype = event.get("type", "")

        if etype == "assistant" or etype == "result":
            # Final or intermediate text
            content = event.get("result", "") or event.get("content", "")
            if isinstance(content, list):
                # content blocks
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                content = "\n".join(texts)
            new_sid = event.get("session_id", sid)
            if new_sid:
                self._session_id = new_sid
            if content:
                return AgentMessage("text", content, self._session_id or sid, {})

        elif etype == "tool_use":
            tool = event.get("name", event.get("tool", "unknown"))
            inp = event.get("input", {})
            display = f"Using {tool}..."
            if "file_path" in inp:
                display = f"{tool}: {inp['file_path']}"
            elif "command" in inp:
                cmd_str = inp["command"]
                if len(cmd_str) > 80:
                    cmd_str = cmd_str[:77] + "..."
                display = f"{tool}: {cmd_str}"
            return AgentMessage("tool_use", display, self._session_id or sid, {"tool": tool, "input": inp})

        elif etype == "tool_result":
            content = event.get("content", "")
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
            return AgentMessage("tool_result", content[:500], self._session_id or sid, {})

        elif etype == "error":
            msg = event.get("error", {}).get("message", str(event))
            if "rate" in msg.lower() or "429" in msg:
                return AgentMessage("rate_limited", msg, self._session_id or sid, {})
            return AgentMessage("error", msg, self._session_id or sid, {})

        return None

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
