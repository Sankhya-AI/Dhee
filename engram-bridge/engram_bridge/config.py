"""Bridge configuration — loads from ~/.engram/bridge.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentConfig:
    type: str                          # "claude", "codex", "custom"
    model: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)  # for custom agents
    cwd_flag: str = ""                 # for custom agents


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8200
    auth_token: str = ""


@dataclass
class BridgeConfig:
    telegram_token: str
    allowed_users: list[int]
    default_agent: str
    agents: dict[str, AgentConfig]
    memory_provider: str
    auto_store: bool
    channel: str = "telegram"
    web: WebConfig = field(default_factory=WebConfig)

    @staticmethod
    def _resolve_env(value: str) -> str:
        """Resolve 'env:VAR_NAME' to the environment variable value."""
        if isinstance(value, str) and value.startswith("env:"):
            var = value[4:]
            resolved = os.environ.get(var, "")
            if not resolved:
                raise ValueError(f"Environment variable {var} is not set")
            return resolved
        return value


def load_config(path: str = "~/.engram/bridge.json") -> BridgeConfig:
    """Load bridge configuration from JSON file."""
    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Bridge config not found at {config_path}. "
            "Create it or pass a custom path."
        )

    with open(config_path) as f:
        raw: dict[str, Any] = json.load(f)

    tg = raw.get("telegram", {})
    token = BridgeConfig._resolve_env(tg.get("token", ""))
    allowed_users = tg.get("allowed_users", [])

    agents: dict[str, AgentConfig] = {}
    for name, acfg in raw.get("agents", {}).items():
        agents[name] = AgentConfig(
            type=acfg.get("type", "custom"),
            model=acfg.get("model", ""),
            allowed_tools=acfg.get("allowed_tools", []),
            command=acfg.get("command", []),
            cwd_flag=acfg.get("cwd_flag", ""),
        )

    mem = raw.get("memory", {})

    # Web channel config
    web_raw = raw.get("web", {})
    web_token = web_raw.get("auth_token", "")
    if web_token:
        web_token = BridgeConfig._resolve_env(web_token)
    web = WebConfig(
        host=web_raw.get("host", "127.0.0.1"),
        port=web_raw.get("port", 8200),
        auth_token=web_token,
    )

    return BridgeConfig(
        telegram_token=token,
        allowed_users=allowed_users,
        default_agent=raw.get("default_agent", "claude-code"),
        agents=agents,
        memory_provider=mem.get("provider", "gemini"),
        auto_store=mem.get("auto_store", True),
        channel=raw.get("channel", "telegram"),
        web=web,
    )
