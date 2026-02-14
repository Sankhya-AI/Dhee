"""Interactive setup wizard for `engram setup`."""

import getpass
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


PACKAGES = [
    ("engram-memory", "core memory layer with decay, encoding, scenes"),
    ("engram-bus", "cross-agent coordination and handoff"),
]

PROVIDERS = [
    ("gemini", "Google AI (recommended, free tier)"),
    ("openai", "GPT models"),
    ("nvidia", "Llama / Kimi, cloud hosted"),
    ("ollama", "Local models, no API key needed"),
]


def _prompt_choice(label: str, options: list, default: int = 1) -> int:
    """Prompt user to pick from numbered options. Returns 1-based index."""
    print(f"\n{label}")
    for i, (name, desc) in enumerate(options, 1):
        print(f"  {i}. {name:16s} — {desc}")
    while True:
        raw = input(f"Enter number [{default}]: ").strip()
        if not raw:
            return default
        try:
            n = int(raw)
            if 1 <= n <= len(options):
                return n
        except ValueError:
            pass
        print(f"  Please enter 1-{len(options)}")


def _prompt_multi(label: str, options: list, default: str = "1") -> list:
    """Prompt user to pick multiple (comma-separated). Returns list of 1-based indices."""
    print(f"\n{label}")
    for i, (name, desc) in enumerate(options, 1):
        print(f"  {i}. {name:16s} — {desc}")
    while True:
        raw = input(f"Enter numbers [{default}]: ").strip()
        if not raw:
            raw = default
        try:
            nums = [int(x.strip()) for x in raw.split(",")]
            if all(1 <= n <= len(options) for n in nums):
                return nums
        except ValueError:
            pass
        print(f"  Enter comma-separated numbers, e.g. 1,2")


def _prompt_api_key(provider: str) -> str:
    """Prompt for API key."""
    defaults = PROVIDER_DEFAULTS[provider]
    env_var = defaults["env_var"]
    existing = os.environ.get(env_var, "")
    for alt in defaults.get("alt_env_vars", []):
        existing = existing or os.environ.get(alt, "")

    if existing:
        masked = existing[:4] + "..." + existing[-4:] if len(existing) > 8 else "****"
        print(f"\n  Found {env_var}={masked} in environment.")
        use = input("  Use this key? [Y/n]: ").strip().lower()
        if use in ("", "y", "yes"):
            return existing

    print(f"\n  Enter your {env_var} (input hidden):")
    key = getpass.getpass(f"  {env_var}: ")
    if key:
        print(f"\n  To persist, add to your shell profile:")
        print(f"    export {env_var}={key[:4]}...{key[-4:]}")
        os.environ[env_var] = key
    return key


def run_setup() -> None:
    """Run the interactive setup wizard."""
    print("=" * 50)
    print("  engram setup")
    print("=" * 50)

    config = load_config()

    # 1. Package selection
    pkg_indices = _prompt_multi("Which packages?", PACKAGES, default="1")
    config["packages"] = [PACKAGES[i - 1][0] for i in pkg_indices]

    # 2. Provider selection
    provider_idx = _prompt_choice("Which LLM provider?", PROVIDERS, default=1)
    provider = PROVIDERS[provider_idx - 1][0]
    config["provider"] = provider

    # 3. API key (skip for ollama)
    if provider != "ollama":
        key = _prompt_api_key(provider)
        if not key:
            print("\n  Warning: No API key set. Memory operations will fail without it.")
    else:
        print("\n  Ollama selected — no API key needed.")
        print("  Make sure Ollama is running: ollama serve")

    # Save config
    save_config(config)
    print(f"\n  Config saved to {os.path.join(CONFIG_DIR, 'config.json')}")

    # 4. Auto-configure MCP servers
    agents = detect_agents()
    if agents:
        print(f"\n  Detected agents: {', '.join(agents)}")
        print("  Configuring MCP servers...")
        results = configure_mcp_servers(config)
        for agent, status in results.items():
            print(f"    {agent}: {status}")
    else:
        print("\n  No agents detected. MCP will be configured when you install one.")

    # Done
    print("\n" + "=" * 50)
    print("  Setup complete!")
    print()
    print("  Try:")
    print('    engram add "User prefers dark mode"')
    print('    engram search "preferences"')
    print("    engram status")
    print("=" * 50)
