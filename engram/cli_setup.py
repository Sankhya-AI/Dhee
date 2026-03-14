"""Auto-setup for `engram setup`. No prompts — detects environment and configures automatically."""

import logging
import os
import sys

from engram.cli_config import (
    CONFIG_DIR,
    PROVIDER_DEFAULTS,
    get_default_config,
    load_config,
    save_config,
)
from engram.cli_mcp import configure_mcp_servers, detect_agents
from engram.utils.factory import _detect_provider

logger = logging.getLogger(__name__)


def run_setup() -> None:
    """Auto-detect environment and configure. No prompts."""
    print("=" * 50)
    print(" engram setup (auto-detect)")
    print("=" * 50)

    config = load_config()

    # Auto-detect provider
    embedder_provider, llm_provider = _detect_provider()
    config["provider"] = embedder_provider
    config["auto_configured"] = True

    if embedder_provider in ("gemini", "openai"):
        defaults = PROVIDER_DEFAULTS.get(embedder_provider, {})
        env_var = defaults.get("env_var", f"{embedder_provider.upper()}_API_KEY")
        key = os.environ.get(env_var, "")
        for alt in defaults.get("alt_env_vars", []):
            key = key or os.environ.get(alt, "")
        if key:
            masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "****"
            print(f"  Provider detected: {embedder_provider}")
            print(f"  API key found: {env_var}={masked}")
        else:
            print(f"  Provider detected: {embedder_provider}")
            print(f"  ! No API key found — set {env_var} for full functionality")
    elif embedder_provider == "ollama":
        print("  Provider detected: ollama (local)")
        print("  Make sure Ollama is running: ollama serve")
    else:
        print("  Provider detected: simple (hash-based embedder)")
        print("  No API key required. In-memory vector store for zero-config.")

    # Save config
    save_config(config)
    print(f"\n  Config saved to {os.path.join(CONFIG_DIR, 'config.json')}")

    # Auto-configure MCP servers
    agents = detect_agents()
    if agents:
        print(f"\n  Detected agents: {', '.join(agents)}")
        print("  Configuring MCP servers...")
        results = configure_mcp_servers(config)
        for agent, status in results.items():
            print(f"    {agent}: {status}")
    else:
        print("\n  No agents detected. MCP will configure when you install one.")

    print("\n" + "=" * 50)
    print(" Setup complete!")
    print()
    print(" Try:")
    print('   engram add "User prefers dark mode"')
    print('   engram search "preferences"')
    print('   engram status')
    print("=" * 50)
