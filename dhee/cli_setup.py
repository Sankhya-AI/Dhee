"""Auto-setup for `dhee setup`. No prompts — detects environment and configures automatically."""

import logging
import os

from dhee.cli_config import (
    CONFIG_DIR,
    PROVIDER_DEFAULTS,
    load_config,
    save_config,
)
from dhee.harness.install import install_harnesses
from dhee.utils.factory import _detect_provider

logger = logging.getLogger(__name__)


def run_setup() -> None:
    """Auto-detect environment and configure. No prompts."""
    print("=" * 50)
    print(" dhee setup (auto-detect)")
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

    # Auto-configure native harnesses
    print("\n  Configuring native harness integrations...")
    results = install_harnesses(harness="all", enable_router=True)
    for harness, result in results.items():
        label = "Claude Code" if harness == "claude_code" else "Codex"
        print(f"    {label}: {result.action}")

    print("\n" + "=" * 50)
    print(" Setup complete!")
    print()
    print(" Try:")
    print("   dhee install --harness all")
    print("   dhee harness status")
    print("   dhee quality-report")
    print("=" * 50)
