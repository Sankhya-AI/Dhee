# engram-bridge

Channel adapters for Engram — talk to your coding agents from Telegram or a browser without opening a terminal.

**Architecture**: User on Telegram/Web → thin Python bridge (NO LLM) → doer agent (Claude Code/Codex) directly. 1x token cost, not 2x.

## Quick Start

### Web Channel (Browser)

```bash
# Install with web dependencies
pip install -e "./Engram/engram-bridge[web]"

# Create config
mkdir -p ~/.engram
cat > ~/.engram/bridge.json << 'EOF'
{
  "channel": "web",
  "web": {
    "host": "127.0.0.1",
    "port": 8200,
    "auth_token": ""
  },
  "default_agent": "claude-code",
  "agents": {
    "claude-code": {
      "type": "claude",
      "model": "claude-opus-4-6",
      "allowed_tools": ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
    },
    "codex": {
      "type": "codex",
      "model": "gpt-5-codex"
    }
  },
  "memory": {
    "provider": "gemini",
    "auto_store": true
  }
}
EOF

# Run
engram-bridge --channel web

# Open browser
open http://127.0.0.1:8200
```

### Telegram Channel

```bash
# Install with Telegram dependencies
pip install -e "./Engram/engram-bridge[telegram]"

# Set token (get one from @BotFather on Telegram)
export TELEGRAM_BOT_TOKEN="your-token"

# Create config (use "channel": "telegram" or omit — it's the default)
mkdir -p ~/.engram
cat > ~/.engram/bridge.json << 'EOF'
{
  "telegram": {
    "token": "env:TELEGRAM_BOT_TOKEN",
    "allowed_users": []
  },
  "default_agent": "claude-code",
  "agents": {
    "claude-code": {
      "type": "claude",
      "model": "claude-opus-4-6"
    }
  },
  "memory": {
    "provider": "gemini",
    "auto_store": true
  }
}
EOF

# Run
engram-bridge
```

## Commands

Available in both Telegram and Web channels:

| Command | Description |
|---------|-------------|
| `/start [agent] [repo]` | Start agent session on a repo |
| `/switch <agent>` | Switch active agent (saves session) |
| `/status` | Show active agent, repo, session info |
| `/agents` | List available agents |
| `/stop` | Stop active agent and save session |
| `/sessions` | List recent handoff sessions |
| `/memory [query]` | Search Engram memory or show stats |

## Web Channel

The web channel serves a React chat UI over WebSocket at `http://127.0.0.1:8200` (default).

- Real-time streaming of tool-use updates and agent responses
- Command button bar for quick access to `/start`, `/switch`, `/status`, etc.
- Markdown rendering with code block support
- Auto-reconnect on disconnect
- Optional token auth: set `web.auth_token` in config, pass `?token=...` in the URL

### Web Config Options

```json
{
  "channel": "web",
  "web": {
    "host": "127.0.0.1",
    "port": 8200,
    "auth_token": "env:BRIDGE_WEB_TOKEN"
  }
}
```

## Agent Types

### Claude Code (`type: "claude"`)
Uses the `claude` CLI. Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed.

### Codex (`type: "codex"`)
Uses the `codex` CLI. Requires [OpenAI Codex](https://github.com/openai/codex) installed.

### Custom (`type: "custom"`)
Wraps any CLI tool. Use `{prompt}` placeholder in the command template:
```json
{
  "aider": {
    "type": "custom",
    "command": ["aider", "--message", "{prompt}", "--yes"],
    "cwd_flag": "--cwd"
  }
}
```

## How It Works

1. User sends message (on Telegram or Web)
2. Bridge routes to the active agent (no LLM orchestrator in between)
3. Agent processes the message (Claude Code, Codex, etc.)
4. Bridge streams tool-use updates and final response back to the channel
5. Exchange is auto-stored in Engram memory
6. Session state is checkpointed to engram-bus

### Rate Limit Handling

When an agent hits a rate limit, the bridge:
1. Saves the session digest to engram-bus
2. Notifies you on your channel
3. You can `/switch` to another agent to continue immediately

## Configuration

Config lives at `~/.engram/bridge.json`. Token values support `env:VAR_NAME` syntax to read from environment variables.

The `--channel` CLI flag overrides the `channel` field in the config file.

### Security

- **Telegram**: `allowed_users` restricts access by Telegram user ID. Empty list = allow all.
- **Web**: `auth_token` protects the WebSocket endpoint. Empty = no auth (local dev only).
- Agents run with whatever permissions the CLI tool has on the host machine.
- Claude Code supports `--permission-mode` for sandboxing.

## Dependencies

- `engram-bus` — session handoffs, pub/sub
- `engram-memory` — conversation memory (FadeMem + EchoMem)
- `python-telegram-bot` — Telegram bot API (optional, install with `[telegram]`)
- `fastapi` + `uvicorn` — Web channel server (optional, install with `[web]`)
