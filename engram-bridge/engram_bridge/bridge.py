"""Bridge — the core orchestrator. Routes messages between channels and agents.

No LLM in the loop. Pure routing logic:
  User on Telegram → Bridge → doer agent (Claude Code / Codex) → response back.

Engram provides memory (auto-store exchanges) and the bus provides session
handoffs / pub-sub signals.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path

from engram_bus import Bus

from engram_bridge.agents.base import AgentMessage, BaseAgent
from engram_bridge.agents.claude import ClaudeAgent
from engram_bridge.agents.codex import CodexAgent
from engram_bridge.agents.custom import CustomAgent
from engram_bridge.channels.base import BaseChannel, IncomingMessage
from engram_bridge.config import AgentConfig, BridgeConfig, load_config

logger = logging.getLogger(__name__)


@dataclass
class UserSession:
    """Per-user active session state."""
    agent: BaseAgent
    repo: str
    agent_session_id: str | None = None


class Bridge:
    """Orchestrates channels, agents, bus, and memory. No LLM — pure routing."""

    def __init__(self, config: BridgeConfig):
        self.config = config

        # Bus for session handoffs + pub/sub
        db_path = str(Path("~/.engram/handoff.db").expanduser())
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.bus = Bus(db_path=db_path)

        # Engram memory (lazy init)
        self._memory = None
        self._memory_init = config.auto_store

        # Channel
        self.channel: BaseChannel | None = None

        # Coordination layer (lazy init when enabled)
        self._coordinator = None

        # War room layer (lazy init when enabled)
        self._warroom = None
        self._monitor_role = None
        self._auto_picker = None
        self._auto_failover = None
        self._active_warroom: dict[int, str] = {}  # user_id -> active room_id

        # Per-user sessions
        self._sessions: dict[int, UserSession] = {}

    def _get_memory(self):
        """Lazy-init Engram Memory to avoid import cost when memory is off."""
        if self._memory is None and self._memory_init:
            try:
                from engram.memory.main import Memory
                from engram.configs.base import MemoryConfig, LLMConfig, EmbedderConfig

                # For LLM: use "mock" unless a real provider is specified
                llm_provider = self.config.memory_provider
                if llm_provider == "simple":
                    llm_provider = "mock"
                cfg = MemoryConfig(
                    llm=LLMConfig(provider=llm_provider),
                    embedder=EmbedderConfig(provider=self.config.memory_provider),
                    vector_store={"provider": "memory", "config": {}},
                    history_db_path=str(Path("~/.engram/bridge_memory.db").expanduser()),
                    collection_name="bridge_exchanges",
                )
                # Disable echo for bridge (speed over depth)
                cfg.echo.enable_echo = False
                self._memory = Memory(config=cfg)
            except Exception as e:
                logger.warning("Failed to initialize Engram memory: %s", e)
                self._memory_init = False
        return self._memory

    async def _handle_stats_request(self, user_id: int) -> None:
        """Handle stats request from web UI — sends memory + session info."""
        from engram_bridge.channels.web import WebChannel

        if not isinstance(self.channel, WebChannel):
            return

        data: dict = {}

        # Memory stats
        mem = self._get_memory()
        if mem:
            try:
                data["memory"] = mem.get_stats(user_id=str(user_id))
            except Exception as e:
                logger.warning("Failed to get memory stats: %s", e)

        # Session info
        session = self._sessions.get(user_id)
        if session and session.agent:
            data["session"] = {
                "agent": session.agent.name,
                "repo": session.repo,
                "running": session.agent.is_running,
                "sessionId": session.agent_session_id,
            }

        await self.channel.send_stats(user_id, data)

    def _get_agents_info(self) -> list[dict]:
        """Return agent roster info for the dashboard."""
        agents = []
        # Map active sessions by agent name
        active_agents = {}
        for uid, session in self._sessions.items():
            if session.agent:
                active_agents[session.agent.name] = {
                    "user_id": uid,
                    "repo": session.repo,
                    "running": session.agent.is_running,
                }

        for name, acfg in self.config.agents.items():
            status_info = active_agents.get(name)
            info = {
                "name": name,
                "type": acfg.type,
                "model": acfg.model,
                "status": "active" if status_info and status_info["running"] else
                          "idle" if status_info else "offline",
                "repo": status_info["repo"] if status_info else None,
            }
            # Enrich with coordination registry data
            if self._coordinator:
                reg_info = self._coordinator.registry.get(name)
                if reg_info:
                    info["capabilities"] = reg_info.get("capabilities", [])
                    info["max_concurrent"] = reg_info.get("max_concurrent", 1)
                    info["active_tasks"] = reg_info.get("active_tasks", [])
                    info["coordination_status"] = reg_info.get("status", "")
            agents.append(info)
        return agents

    # ── Agent Factory ──

    def _create_agent(self, name: str) -> BaseAgent:
        """Create an agent instance from config."""
        acfg = self.config.agents.get(name)
        if not acfg:
            raise ValueError(
                f"Unknown agent '{name}'. Available: {list(self.config.agents.keys())}"
            )
        if acfg.type == "claude":
            return ClaudeAgent(acfg)
        elif acfg.type == "codex":
            return CodexAgent(acfg)
        elif acfg.type == "custom":
            return CustomAgent(acfg, agent_name=name)
        else:
            raise ValueError(f"Unknown agent type: {acfg.type}")

    # ── Lifecycle ──

    async def start(self) -> None:
        """Start the bridge — connects channel and begins listening."""
        if self.config.channel == "web":
            from engram_bridge.channels.web import WebChannel

            wc = WebChannel(
                host=self.config.web.host,
                port=self.config.web.port,
                auth_token=self.config.web.auth_token,
                allowed_users=self.config.allowed_users,
            )
            wc._on_stats_request = self._handle_stats_request
            wc._get_agents_info = self._get_agents_info
            # Replace ephemeral TaskStore with persistent EngramTaskStore + wire ProjectManager
            mem = self._get_memory()
            if mem:
                try:
                    from engram_bridge.channels.web import EngramTaskStore
                    wc.tasks = EngramTaskStore(mem, user_id="bridge")
                    wc.tasks._bus = self.bus  # wire bus for coordination events
                    wc.set_memory(mem)
                    logger.info("Using EngramTaskStore (persistent tasks) + ProjectManager")
                except Exception as e:
                    logger.warning("Failed to init EngramTaskStore, using ephemeral: %s", e)
            # Initialize coordination layer if enabled
            if self.config.coordination.enabled and mem:
                try:
                    from engram_bridge.coordination import Coordinator
                    self._coordinator = Coordinator(mem, self.bus, self.config.coordination, default_agent=self.config.default_agent)
                    self._coordinator.register_from_config(
                        self.config.agents,
                        caps_map=self.config.coordination.default_capabilities or None,
                    )
                    self._coordinator.start()
                    wc._coordinator = self._coordinator
                    logger.info("Coordination layer enabled")

                    # Auto-execute: subscribe to bridge.task.execute events
                    if self.config.coordination.auto_execute:
                        self.bus.subscribe("bridge.task.execute", self._on_bus_task_execute)
                        logger.info("Auto-execute enabled: tasks will start automatically after routing")
                except Exception as e:
                    logger.warning("Failed to init coordination layer: %s", e)
                    self._coordinator = None

            # Initialize war room layer if enabled
            if self.config.warroom.enabled and mem:
                try:
                    from engram_warroom import WarRoom, MonitorRole, AutoPicker, AutoFailover
                    self._warroom = WarRoom(mem, self.bus)
                    self._monitor_role = MonitorRole(mem, self.bus, self._warroom)
                    router = self._coordinator.router if self._coordinator else None
                    self._auto_picker = AutoPicker(mem, self.bus, router=router)
                    if self._coordinator:
                        self._auto_failover = AutoFailover(
                            self.bus, self._coordinator.registry,
                        )
                    wc._warroom = self._warroom
                    wc._monitor_role = self._monitor_role
                    logger.info("War room layer enabled")
                except Exception as e:
                    logger.warning("Failed to init war room layer: %s", e)

            wc._bridge_config = self.config
            self.channel = wc
        else:
            from engram_bridge.channels.telegram import TelegramChannel

            self.channel = TelegramChannel(self.config.telegram_token, self.config.allowed_users)

        await self.channel.start(on_message=self.handle_message)
        self.bus.publish("bridge.started", {"channel": self.config.channel})
        logger.info("Bridge started on %s channel.", self.config.channel)

    async def stop(self) -> None:
        """Gracefully stop all sessions and the channel."""
        # Stop all active agents
        for uid, session in list(self._sessions.items()):
            if session.agent:
                self.bus.save_session(
                    agent_id=session.agent.name,
                    repo=session.repo,
                    status="paused",
                    task_summary="Bridge shutting down",
                )
                await session.agent.stop()
        self._sessions.clear()

        if self._coordinator:
            self._coordinator.stop()
            self._coordinator = None

        if self.channel:
            await self.channel.stop()

        mem = self._get_memory()
        if mem:
            mem.close()

        self.bus.close()
        logger.info("Bridge stopped.")

    # ── Message Handling ──

    async def handle_message(self, msg: IncomingMessage) -> None:
        """Main entry point for all incoming messages."""
        if msg.is_command:
            await self._handle_command(msg)
            return

        # Task-scoped messages: route to task handler instead of generic chat
        task_id = (msg.metadata or {}).get("task_id")
        if task_id:
            await self._handle_task_message(msg, task_id)
            return

        # War room messages: route through monitor if active
        if msg.user_id in self._active_warroom and self._warroom:
            await self._handle_warroom_message(msg)
            return

        session = self._sessions.get(msg.user_id)
        if not session or not session.agent:
            await self.channel.send_text(
                msg.chat_id, "No active session. Use /start <agent> <repo>"
            )
            return

        # Publish incoming message event
        self.bus.publish("bridge.message.in", {
            "user_id": msg.user_id, "text": msg.text[:200], "chat_id": msg.chat_id,
        })

        # Send "thinking" placeholder
        placeholder_id = await self.channel.send_text(msg.chat_id, "...")

        # Stream agent response
        full_response: list[str] = []
        last_tool = ""

        async for agent_msg in session.agent.send(
            msg.text, cwd=session.repo, session_id=session.agent_session_id
        ):
            if agent_msg.type == "rate_limited":
                await self._handle_rate_limit(msg, session, agent_msg)
                return

            if agent_msg.type == "error":
                await self.channel.edit_text(
                    msg.chat_id, placeholder_id, f"Error: {agent_msg.content[:3500]}"
                )
                return

            if agent_msg.type == "tool_use":
                tool_display = agent_msg.content
                if tool_display != last_tool:
                    last_tool = tool_display
                    await self.channel.edit_text(
                        msg.chat_id, placeholder_id, f"[{tool_display}]"
                    )

            elif agent_msg.type == "text":
                full_response.append(agent_msg.content)
                session.agent_session_id = agent_msg.session_id

        # Send final response
        response_text = "\n".join(full_response) or "(no response)"
        await self.channel.edit_text(msg.chat_id, placeholder_id, response_text[:4096])

        # If response is longer than one Telegram message, send the rest
        if len(response_text) > 4096:
            await self.channel.send_text(msg.chat_id, response_text[4096:])

        # Auto-store in Engram memory
        mem = self._get_memory()
        if mem:
            try:
                mem.add(
                    f"User: {msg.text}\nAgent: {response_text[:2000]}",
                    user_id=str(msg.user_id),
                    metadata={
                        "source": self.config.channel,
                        "agent": session.agent.name,
                        "repo": session.repo,
                    },
                )
            except Exception as e:
                logger.warning("Failed to store memory: %s", e)

        # Checkpoint to bus
        self.bus.put(
            f"session:{msg.user_id}:last_exchange",
            {"user": msg.text, "agent": response_text[:500], "repo": session.repo},
            agent=session.agent.name,
            ttl=3600,
        )

        # Publish outgoing message event
        self.bus.publish("bridge.message.out", {
            "user_id": msg.user_id, "agent": session.agent.name,
            "text": response_text[:200],
        })

    # ── Task-Scoped Message Handling ──

    async def _handle_task_message(self, msg: IncomingMessage, task_id: str) -> None:
        """Handle a message scoped to a specific task — streams to task conversation."""
        from engram_bridge.channels.web import WebChannel

        agent_name = (msg.metadata or {}).get("agent") or self.config.default_agent
        session = self._get_or_create_task_session(msg.user_id, agent_name)

        # Claim task via coordinator if available
        if self._coordinator:
            self._coordinator.claim(task_id, agent_name)

        # Publish incoming message event
        self.bus.publish("bridge.message.in", {
            "user_id": msg.user_id, "text": msg.text[:200],
            "chat_id": msg.chat_id, "task_id": task_id,
        })

        # Stream agent response, routing events to task conversation
        full_response: list[str] = []
        message_id = None
        last_tool = ""

        async for agent_msg in session.agent.send(
            msg.text, cwd=session.repo, session_id=session.agent_session_id
        ):
            if isinstance(self.channel, WebChannel):
                if agent_msg.type == "rate_limited":
                    await self.channel.send_task_update(msg.chat_id, task_id, "task_error", {
                        "content": "Agent rate limited. Try again later.",
                        "agent": agent_name,
                    })
                    return

                if agent_msg.type == "error":
                    await self.channel.send_task_update(msg.chat_id, task_id, "task_error", {
                        "content": agent_msg.content[:3500],
                        "agent": agent_name,
                    })
                    return

                if agent_msg.type == "tool_use":
                    tool_display = agent_msg.content
                    if tool_display != last_tool:
                        last_tool = tool_display
                        file_path = (agent_msg.metadata or {}).get("file_path") if hasattr(agent_msg, "metadata") else None
                        await self.channel.send_task_update(msg.chat_id, task_id, "task_tool_use", {
                            "content": tool_display,
                            "tool": tool_display,
                            "file_path": file_path,
                            "agent": agent_name,
                        })

                elif agent_msg.type == "text":
                    full_response.append(agent_msg.content)
                    session.agent_session_id = agent_msg.session_id
                    if message_id is None:
                        message_id = id(agent_msg)
                        await self.channel.send_task_update(msg.chat_id, task_id, "task_text", {
                            "content": agent_msg.content,
                            "agent": agent_name,
                            "message_id": message_id,
                            "streaming": True,
                        })
                    else:
                        await self.channel.send_task_update(msg.chat_id, task_id, "task_edit", {
                            "content": "\n".join(full_response),
                            "message_id": message_id,
                            "streaming": True,
                        })

        # Send completion
        if isinstance(self.channel, WebChannel):
            # Final edit with streaming=False
            if message_id is not None:
                await self.channel.send_task_update(msg.chat_id, task_id, "task_edit", {
                    "content": "\n".join(full_response),
                    "message_id": message_id,
                    "streaming": False,
                })
            elif full_response:
                await self.channel.send_task_update(msg.chat_id, task_id, "task_text", {
                    "content": "\n".join(full_response),
                    "agent": agent_name,
                    "streaming": False,
                })
            await self.channel.send_task_update(msg.chat_id, task_id, "task_complete", {
                "agent": agent_name,
            })

        # Auto-store in Engram memory
        response_text = "\n".join(full_response) or "(no response)"
        mem = self._get_memory()
        if mem:
            try:
                mem.add(
                    f"User: {msg.text}\nAgent: {response_text[:2000]}",
                    user_id=str(msg.user_id),
                    metadata={
                        "source": self.config.channel,
                        "agent": agent_name,
                        "repo": session.repo,
                        "task_id": task_id,
                    },
                )
            except Exception as e:
                logger.warning("Failed to store memory: %s", e)

    def _get_or_create_task_session(self, user_id: int, agent_name: str) -> UserSession:
        """Get an existing session for the agent, or create a new one.

        Reuses the user's session if the agent matches. Otherwise creates
        a new session. For auto-execute (no real user), uses a synthetic user_id.
        """
        session = self._sessions.get(user_id)
        if session and session.agent and session.agent.name == agent_name:
            return session

        agent = self._create_agent(agent_name)
        prev = self.bus.get_session(agent_id=agent_name)
        session_id = prev["id"] if prev else None
        new_session = UserSession(
            agent=agent,
            repo=os.getcwd(),
            agent_session_id=session_id,
        )
        self._sessions[user_id] = new_session
        return new_session

    # ── Auto-Execute ──

    def _on_bus_task_execute(self, topic: str, data: Any, agent_id: str | None) -> None:
        """Bus callback for bridge.task.execute — schedule auto-execution on the event loop."""
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self._auto_execute_task(data))
        except Exception as e:
            logger.error("Failed to schedule auto-execute: %s", e)

    async def _auto_execute_task(self, data: dict) -> None:
        """Build a prompt from task data and dispatch it through handle_message()."""
        from engram_bridge.channels.web import WebChannel

        task_id = data.get("task_id", "")
        agent_name = data.get("agent", self.config.default_agent)
        title = data.get("title", "")
        description = data.get("description", "")

        if not task_id:
            return

        # Build the prompt from task title + description
        prompt = title
        if description:
            prompt = f"{title}\n\n{description}"

        # Use a synthetic user_id to avoid session collisions with real users
        auto_user_id = hash(f"auto:{task_id}") & 0x7FFFFFFF

        # Store a system conversation entry to indicate auto-dispatch
        if isinstance(self.channel, WebChannel):
            self.channel.tasks.add_conversation_entry(task_id, {
                "type": "system",
                "content": f"Auto-dispatching to {agent_name}...",
            })
            # Broadcast task_auto_started to all WS clients
            self.channel._broadcast_ws("task_auto_started", {
                "task_id": task_id,
                "agent": agent_name,
                "title": title,
            })

        # Dispatch via handle_message
        incoming = IncomingMessage(
            user_id=auto_user_id,
            chat_id=auto_user_id,
            text=prompt,
            username=f"auto_{agent_name}",
            is_command=False,
            metadata={"task_id": task_id, "agent": agent_name},
        )
        await self.handle_message(incoming)

    # ── Rate Limit Handling ──

    async def _handle_rate_limit(
        self, msg: IncomingMessage, session: UserSession, agent_msg: AgentMessage
    ) -> None:
        """Save session digest and notify user when an agent is rate-limited."""
        self.bus.save_session(
            agent_id=session.agent.name,
            task_summary=f"Rate limited. Last message: {msg.text[:200]}",
            repo=session.repo,
            status="paused",
        )
        self.bus.publish("bridge.agent.rate_limited", {
            "user_id": msg.user_id,
            "agent": session.agent.name,
            "session_id": session.agent_session_id,
        })

        # Auto-failover if enabled
        if self._auto_failover and self.config.warroom.auto_failover:
            task_id = (msg.metadata or {}).get("task_id", "") if hasattr(msg, "metadata") else ""
            failover_agent = self._auto_failover.find_failover_agent(
                exclude=session.agent.name,
            )
            if failover_agent:
                self._auto_failover.execute_failover(
                    from_agent=session.agent.name,
                    to_agent=failover_agent,
                    user_id=str(msg.user_id),
                    task_id=task_id,
                    last_message=msg.text[:200],
                )
                # Create new session with failover agent
                new_agent = self._create_agent(failover_agent)
                self._sessions[msg.user_id] = UserSession(
                    agent=new_agent, repo=session.repo,
                )
                await self.channel.send_text(
                    msg.chat_id,
                    f"[{session.agent.name}] Rate limited. "
                    f"Auto-switching to {failover_agent}...",
                )
                # Re-dispatch the original message
                new_msg = IncomingMessage(
                    user_id=msg.user_id,
                    chat_id=msg.chat_id,
                    text=msg.text,
                    username=msg.username,
                    is_command=False,
                    metadata=getattr(msg, "metadata", None),
                )
                asyncio.create_task(self.handle_message(new_msg))
                return

        await self.channel.send_text(
            msg.chat_id,
            f"[{session.agent.name}] Rate limited. Session saved.\n"
            f"Use /switch <agent> to continue with another agent.",
        )

    # ── Command Handling ──

    async def _handle_command(self, msg: IncomingMessage) -> None:
        """Dispatch bot commands."""
        handler = {
            "start": self._cmd_start,
            "switch": self._cmd_switch,
            "status": self._cmd_status,
            "agents": self._cmd_agents,
            "stop": self._cmd_stop,
            "sessions": self._cmd_sessions,
            "memory": self._cmd_memory,
            "warroom": self._cmd_warroom,
            "monitor": self._cmd_monitor,
            "decide": self._cmd_decide,
        }.get(msg.command)

        if handler:
            await handler(msg)
        else:
            await self.channel.send_text(msg.chat_id, f"Unknown command: /{msg.command}")

    async def _cmd_start(self, msg: IncomingMessage) -> None:
        """/start [agent] [repo] — start an agent session."""
        agent_name = msg.command_args[0] if msg.command_args else self.config.default_agent
        repo = msg.command_args[1] if len(msg.command_args) > 1 else os.getcwd()
        repo = os.path.expanduser(repo)

        if agent_name not in self.config.agents:
            available = ", ".join(self.config.agents.keys())
            await self.channel.send_text(
                msg.chat_id, f"Unknown agent '{agent_name}'. Available: {available}"
            )
            return

        agent = self._create_agent(agent_name)

        # Check for previous session to resume
        prev = self.bus.get_session(agent_id=agent_name)
        session_id = prev["id"] if prev else None

        self._sessions[msg.user_id] = UserSession(
            agent=agent, repo=repo, agent_session_id=session_id
        )

        if self._coordinator:
            self._coordinator.registry.update_status(agent_name, "available")

        self.bus.publish("bridge.agent.started", {
            "user_id": msg.user_id, "agent": agent_name, "repo": repo,
        })

        status = f"Session started: {agent_name} on {repo}"
        if prev:
            summary = prev.get("task_summary", "")[:100]
            status += f"\nResumed previous session: {summary}"
        await self.channel.send_text(msg.chat_id, status)

        # Surface pending tasks on session start
        mem = self._get_memory()
        if mem:
            try:
                from engram.memory.tasks import TaskManager
                tm = TaskManager(mem)
                pending = tm.get_pending_tasks(user_id=str(msg.user_id))
                if pending:
                    lines = "\n".join(
                        f"  [{t['priority']}] {t['title']} ({t['status']})"
                        for t in pending[:5]
                    )
                    await self.channel.send_text(msg.chat_id, f"Pending tasks:\n{lines}")
            except Exception as e:
                logger.debug("Could not surface pending tasks: %s", e)

        # Auto-pick top task if warroom enabled and no explicit repo/agent args
        explicit_args = len(msg.command_args) > 1
        if (
            self.config.warroom.auto_pick
            and self._auto_picker
            and not explicit_args
        ):
            try:
                task = self._auto_picker.pick_and_dispatch(
                    user_id=str(msg.user_id), agent_name=agent_name,
                )
                if task:
                    await self.channel.send_text(
                        msg.chat_id,
                        f"Auto-picked: [{task['priority']}] {task['title']}"
                        f" — dispatching to {task.get('agent') or agent_name}...",
                    )
            except Exception as e:
                logger.debug("Auto-pick failed: %s", e)

    async def _cmd_switch(self, msg: IncomingMessage) -> None:
        """/switch <agent> — switch active agent, saving current session."""
        if not msg.command_args:
            await self.channel.send_text(msg.chat_id, "Usage: /switch <agent-name>")
            return

        new_agent_name = msg.command_args[0]
        if new_agent_name not in self.config.agents:
            available = ", ".join(self.config.agents.keys())
            await self.channel.send_text(
                msg.chat_id, f"Unknown agent '{new_agent_name}'. Available: {available}"
            )
            return

        session = self._sessions.get(msg.user_id)
        old_name = None
        repo = os.getcwd()

        if session and session.agent:
            old_name = session.agent.name
            repo = session.repo
            # Save current session
            self.bus.save_session(
                agent_id=old_name,
                task_summary=f"Switched to {new_agent_name}",
                repo=repo,
                status="paused",
            )
            await session.agent.stop()

        new_agent = self._create_agent(new_agent_name)
        self._sessions[msg.user_id] = UserSession(agent=new_agent, repo=repo)

        self.bus.publish("bridge.agent.switched", {
            "user_id": msg.user_id,
            "from": old_name,
            "to": new_agent_name,
            "repo": repo,
        })

        await self.channel.send_text(msg.chat_id, f"Switched to {new_agent_name} on {repo}")

    async def _cmd_status(self, msg: IncomingMessage) -> None:
        """/status — show active session info."""
        session = self._sessions.get(msg.user_id)
        if session and session.agent:
            await self.channel.send_text(
                msg.chat_id,
                f"Agent: {session.agent.name}\n"
                f"Repo: {session.repo}\n"
                f"Running: {session.agent.is_running}\n"
                f"Session: {session.agent_session_id or '(new)'}",
            )
        else:
            await self.channel.send_text(msg.chat_id, "No active session.")

    async def _cmd_agents(self, msg: IncomingMessage) -> None:
        """/agents — list configured agents."""
        lines = []
        for name, acfg in self.config.agents.items():
            marker = " (default)" if name == self.config.default_agent else ""
            lines.append(f"  - {name} [{acfg.type}]{marker}")
        await self.channel.send_text(
            msg.chat_id, "Available agents:\n" + "\n".join(lines)
        )

    async def _cmd_stop(self, msg: IncomingMessage) -> None:
        """/stop — stop active agent and save session."""
        session = self._sessions.get(msg.user_id)
        if session and session.agent:
            self.bus.save_session(
                agent_id=session.agent.name,
                repo=session.repo,
                status="stopped",
                task_summary="User stopped session",
            )
            if self._coordinator:
                self._coordinator.registry.update_status(session.agent.name, "offline")
            await session.agent.stop()
            del self._sessions[msg.user_id]
            await self.channel.send_text(msg.chat_id, "Session stopped. State saved.")
        else:
            await self.channel.send_text(msg.chat_id, "No active session.")

    async def _cmd_sessions(self, msg: IncomingMessage) -> None:
        """/sessions — list recent handoff sessions."""
        sessions = self.bus.list_sessions()
        if not sessions:
            await self.channel.send_text(msg.chat_id, "No sessions found.")
            return
        lines = []
        for s in sessions[-10:]:
            status = s.get("status", "?")
            agent = s.get("agent_id", "?")
            summary = s.get("task_summary", "")[:80]
            lines.append(f"  [{status}] {agent}: {summary}")
        await self.channel.send_text(
            msg.chat_id, "Recent sessions:\n" + "\n".join(lines)
        )

    async def _cmd_memory(self, msg: IncomingMessage) -> None:
        """/memory [search query] — search Engram memory or show stats."""
        mem = self._get_memory()
        if not mem:
            await self.channel.send_text(msg.chat_id, "Memory not configured.")
            return

        query = " ".join(msg.command_args) if msg.command_args else ""
        if not query:
            try:
                result = mem.get_all(user_id=str(msg.user_id), limit=0)
                count = len(result.get("results", []))
                await self.channel.send_text(
                    msg.chat_id, f"Memory: {count} stored exchanges for your user."
                )
            except Exception as e:
                await self.channel.send_text(msg.chat_id, f"Memory error: {e}")
            return

        try:
            result = mem.search(query, user_id=str(msg.user_id), limit=5)
            results = result.get("results", [])
            if not results:
                await self.channel.send_text(msg.chat_id, "No memories found.")
                return
            lines = []
            for r in results:
                content = r.get("memory", r.get("content", ""))[:100]
                lines.append(f"  - {content}")
            await self.channel.send_text(
                msg.chat_id, "Memories:\n" + "\n".join(lines)
            )
        except Exception as e:
            await self.channel.send_text(msg.chat_id, f"Memory search error: {e}")

    # ── War Room Commands ──

    async def _cmd_warroom(self, msg: IncomingMessage) -> None:
        """/warroom [topic] — create a war room or list active rooms."""
        if not self._warroom:
            await self.channel.send_text(msg.chat_id, "War room not enabled. Set warroom.enabled=true in config.")
            return

        topic = " ".join(msg.command_args) if msg.command_args else ""

        if not topic:
            # List active war rooms
            rooms = self._warroom.list_active()
            if not rooms:
                await self.channel.send_text(msg.chat_id, "No active war rooms.")
                return
            lines = []
            for r in rooms:
                state = r.get("wr_state", "?")
                monitor = r.get("wr_monitor_agent", "none")
                lines.append(f"  [{state}] {r.get('wr_topic', '?')} (monitor: {monitor}, id: {r.get('id', '?')})")
            await self.channel.send_text(msg.chat_id, "Active war rooms:\n" + "\n".join(lines))
            return

        # Create a war room with all agents as participants
        participants = list(self.config.agents.keys())
        monitor = self.config.warroom.monitor_agent or self.config.default_agent

        room = self._warroom.create(
            topic=topic,
            participants=participants,
            monitor_agent=monitor,
            created_by=f"user:{msg.user_id}",
        )
        room_id = room.get("id", "")
        self._active_warroom[msg.user_id] = room_id

        # Transition to discussing
        self._warroom.transition(room_id, "discussing", by=f"user:{msg.user_id}")

        await self.channel.send_text(
            msg.chat_id,
            f"War room created: {topic}\n"
            f"  ID: {room_id}\n"
            f"  Monitor: {monitor}\n"
            f"  Participants: {', '.join(participants)}\n"
            f"\nMessages in this session will be routed through the war room.",
        )

    async def _cmd_monitor(self, msg: IncomingMessage) -> None:
        """/monitor <agent> — assign an agent as monitor for the active war room."""
        if not self._warroom or not self._monitor_role:
            await self.channel.send_text(msg.chat_id, "War room not enabled.")
            return

        if not msg.command_args:
            await self.channel.send_text(msg.chat_id, "Usage: /monitor <agent-name>")
            return

        agent_name = msg.command_args[0]
        if agent_name not in self.config.agents:
            available = ", ".join(self.config.agents.keys())
            await self.channel.send_text(msg.chat_id, f"Unknown agent '{agent_name}'. Available: {available}")
            return

        room_id = self._active_warroom.get(msg.user_id)
        if not room_id:
            await self.channel.send_text(msg.chat_id, "No active war room. Use /warroom <topic> first.")
            return

        result = self._monitor_role.assign(room_id, agent_name)
        if "error" in result:
            await self.channel.send_text(msg.chat_id, result["error"])
        else:
            await self.channel.send_text(
                msg.chat_id,
                f"Monitor changed: {result.get('old_monitor', 'none')} -> {agent_name}",
            )

    async def _cmd_decide(self, msg: IncomingMessage) -> None:
        """/decide — tell the monitor to synthesize a decision."""
        if not self._warroom or not self._monitor_role:
            await self.channel.send_text(msg.chat_id, "War room not enabled.")
            return

        room_id = self._active_warroom.get(msg.user_id)
        if not room_id:
            await self.channel.send_text(msg.chat_id, "No active war room.")
            return

        monitor_name = self._monitor_role.get_monitor(room_id)
        if not monitor_name:
            await self.channel.send_text(msg.chat_id, "No monitor assigned. Use /monitor <agent>.")
            return

        # Transition to deciding
        self._warroom.transition(room_id, "deciding", by=f"user:{msg.user_id}")

        # Build decision prompt and dispatch to monitor
        prompt = self._monitor_role.build_monitor_prompt(
            room_id,
            trigger_message="The user has requested a decision. Please synthesize the discussion "
            "and use @decide(your decision text) to record your final decision.",
        )
        await self._handle_warroom_dispatch(msg, room_id, monitor_name, prompt)

    async def _handle_warroom_message(self, msg: IncomingMessage) -> None:
        """Handle a user message while a war room is active.

        Posts to war room, builds monitor prompt, dispatches to monitor,
        parses directives, executes them.
        """
        room_id = self._active_warroom.get(msg.user_id)
        if not room_id or not self._warroom or not self._monitor_role:
            return

        # Post user message to war room
        self._warroom.post_message(room_id, "user", msg.text)

        # Get monitor and build prompt
        monitor_name = self._monitor_role.get_monitor(room_id)
        if not monitor_name:
            await self.channel.send_text(msg.chat_id, "No monitor assigned. Use /monitor <agent>.")
            return

        prompt = self._monitor_role.build_monitor_prompt(room_id, trigger_message=msg.text)
        await self._handle_warroom_dispatch(msg, room_id, monitor_name, prompt)

    async def _handle_warroom_dispatch(
        self, msg: IncomingMessage, room_id: str, monitor_name: str, prompt: str,
    ) -> None:
        """Send prompt to monitor agent and handle directives in response."""
        session = self._get_or_create_task_session(msg.user_id, monitor_name)

        # Send prompt to monitor and collect response
        placeholder_id = await self.channel.send_text(msg.chat_id, f"[{monitor_name}/monitor] ...")
        full_response: list[str] = []

        async for agent_msg in session.agent.send(
            prompt, cwd=session.repo, session_id=session.agent_session_id,
        ):
            if agent_msg.type == "text":
                full_response.append(agent_msg.content)
                session.agent_session_id = agent_msg.session_id

        response_text = "\n".join(full_response) or "(no response)"
        await self.channel.edit_text(msg.chat_id, placeholder_id, f"[{monitor_name}/monitor] {response_text[:4000]}")

        # Post monitor response to war room
        self._warroom.post_message(room_id, monitor_name, response_text, "message")

        # Parse for directives
        directives = self._monitor_role.parse_monitor_response(response_text)
        for directive in directives:
            dtype = directive.get("type")

            if dtype == "delegate":
                await self._dispatch_to_subagent(
                    msg, room_id,
                    directive["agent"], directive["instruction"],
                )
            elif dtype == "ask":
                await self._dispatch_to_subagent(
                    msg, room_id,
                    directive["agent"], directive["question"],
                )
            elif dtype == "decide":
                self._warroom.set_decision(room_id, directive["text"])
                await self.channel.send_text(
                    msg.chat_id,
                    f"Decision recorded: {directive['text'][:500]}",
                )

    async def _dispatch_to_subagent(
        self, msg: IncomingMessage, room_id: str, agent_name: str, instruction: str,
    ) -> None:
        """Dispatch a delegation/question to a subagent and post result back."""
        if agent_name not in self.config.agents:
            self._warroom.post_message(
                room_id, "system", f"Agent '{agent_name}' not found.", "system",
            )
            return

        prompt = self._monitor_role.build_delegation_prompt(room_id, agent_name, instruction)
        session = self._get_or_create_task_session(msg.user_id, agent_name)

        placeholder_id = await self.channel.send_text(msg.chat_id, f"[{agent_name}] ...")
        full_response: list[str] = []

        async for agent_msg in session.agent.send(
            prompt, cwd=session.repo, session_id=session.agent_session_id,
        ):
            if agent_msg.type == "text":
                full_response.append(agent_msg.content)
                session.agent_session_id = agent_msg.session_id

        response_text = "\n".join(full_response) or "(no response)"
        await self.channel.edit_text(msg.chat_id, placeholder_id, f"[{agent_name}] {response_text[:4000]}")

        # Post subagent response to war room
        self._warroom.post_message(room_id, agent_name, response_text)


# ── Entry Point ──

def main():
    """CLI entry point for engram-bridge."""
    import argparse

    parser = argparse.ArgumentParser(description="Engram Bridge — channel adapter for coding agents")
    parser.add_argument(
        "--config", "-c",
        default="~/.engram/bridge.json",
        help="Path to bridge config file (default: ~/.engram/bridge.json)",
    )
    parser.add_argument(
        "--channel",
        choices=["telegram", "web"],
        default=None,
        help="Channel to use (overrides config file)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(args.config)
    if args.channel:
        config.channel = args.channel
    bridge = Bridge(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run():
        await bridge.start()
        # Keep running until interrupted
        stop_event = asyncio.Event()

        def handle_signal():
            stop_event.set()

        loop.add_signal_handler(signal.SIGINT, handle_signal)
        loop.add_signal_handler(signal.SIGTERM, handle_signal)

        await stop_event.wait()
        await bridge.stop()

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        loop.run_until_complete(bridge.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
