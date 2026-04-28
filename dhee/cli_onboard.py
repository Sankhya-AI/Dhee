"""`dhee onboard` — interactive provider + key wizard.

Invoked at the tail of ``install.sh`` and usable on its own afterwards.
Walks the user through:

  1. Provider selection (openai default, then gemini, nvidia, ollama)
  2. API key paste (masked echo, stored in the encrypted secret store)
  3. Optional git repo linking for shared `.dhee/context/`
  4. Final "run ``dhee link`` / ``dhee handoff``" handoff

The prompts are routed through ``/dev/tty`` so the flow works even when
the caller is piped — the exact shape ``curl ... | sh`` takes. If the
TTY is unavailable (CI, non-interactive shell), the whole thing becomes
a no-op with a friendly hint rather than failing the install.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from typing import Optional, Tuple

from dhee.cli_config import PROVIDER_DEFAULTS, load_config, save_config

# Order matters — this is the order presented to the user. ``openai`` is
# first by design (most users have an OpenAI key already, and it's the
# path of least friction for the product's core memory calls).
PROVIDER_ORDER = ["openai", "gemini", "nvidia", "ollama"]

PROVIDER_HINTS = {
    "openai": "default · https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/app/apikey",
    "nvidia": "https://build.nvidia.com  (NIM/NGC API key)",
    "ollama": "local runtime — no key needed",
}


def _open_tty() -> Tuple[Optional[io.TextIOBase], Optional[io.TextIOBase]]:
    """Return (stdin, stdout) backed by /dev/tty so prompts work under
    ``curl | sh``. Returns ``(None, None)`` when no TTY is attached.
    """
    try:
        tty_in = open("/dev/tty", "r")
        tty_out = open("/dev/tty", "w")
        return tty_in, tty_out
    except OSError:
        # Fall back to stdin/stdout if they are TTYs.
        if sys.stdin.isatty() and sys.stdout.isatty():
            return sys.stdin, sys.stdout
        return None, None


def _print(tty_out: io.TextIOBase, text: str = "") -> None:
    tty_out.write(text + "\n")
    tty_out.flush()


def _ask(tty_in: io.TextIOBase, tty_out: io.TextIOBase, prompt: str) -> str:
    tty_out.write(prompt)
    tty_out.flush()
    line = tty_in.readline()
    if not line:
        return ""
    return line.rstrip("\n").strip()


def _ask_secret(tty_in: io.TextIOBase, tty_out: io.TextIOBase, prompt: str) -> str:
    """Mask the paste if we have a terminal, fall back to plain read."""
    try:
        import getpass

        # getpass wants fds; if tty is a real terminal this works.
        if tty_in.fileno() == sys.stdin.fileno():
            try:
                return getpass.getpass(prompt).strip()
            except Exception:
                pass
    except Exception:
        pass
    return _ask(tty_in, tty_out, prompt)


def _pick_provider(tty_in: io.TextIOBase, tty_out: io.TextIOBase) -> str:
    _print(tty_out, "")
    _print(tty_out, "Which provider should Dhee use for memory + embeddings?")
    _print(tty_out, "")
    for idx, key in enumerate(PROVIDER_ORDER, start=1):
        label = key.capitalize() if key != "nvidia" else "NVIDIA"
        hint = PROVIDER_HINTS.get(key, "")
        star = " (default)" if idx == 1 else ""
        _print(tty_out, f"  [{idx}] {label}{star}  — {hint}")
    _print(tty_out, "")
    while True:
        raw = _ask(tty_in, tty_out, "Select [1-4, default 1]: ").strip().lower()
        if not raw:
            return PROVIDER_ORDER[0]
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(PROVIDER_ORDER):
                return PROVIDER_ORDER[idx - 1]
        # Accept the provider name directly too.
        if raw in PROVIDER_ORDER:
            return raw
        _print(tty_out, "  invalid choice, try again.")


def _save_key(provider: str, api_key: str) -> str:
    from dhee.secret_store import store_api_key

    status = store_api_key(provider, api_key, label=f"{provider} · onboarding")
    return str(status.get("activePreview") or "stored")


def _save_provider_in_config(provider: str) -> None:
    config = load_config()
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    config["provider"] = provider
    if defaults.get("llm_model"):
        config.setdefault("llm_model", defaults["llm_model"])
    if defaults.get("embedder_model"):
        config.setdefault("embedder_model", defaults["embedder_model"])
    if defaults.get("embedding_dims"):
        config.setdefault("embedding_dims", defaults["embedding_dims"])
    save_config(config)


def _link_repo(path: str) -> Tuple[bool, str]:
    """Run ``repo_link.link()`` on *path*. Returns (ok, message)."""
    from dhee import repo_link

    try:
        info = repo_link.link(path)
    except ValueError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"link error: {exc}"
    return True, (
        f"linked {info['repo_root']} (id={str(info.get('repo_id') or '')[:12]}) — "
        f"hooks: {', '.join(info.get('hooks') or []) or 'none'}"
    )


def _link_repos_interactive(
    tty_in: io.TextIOBase, tty_out: io.TextIOBase
) -> int:
    """Prompt the user for git repo paths to link.

    No-op on empty input. Each line links one repo via
    ``repo_link.link()``; non-git paths print a one-line warning and
    move on. Returns the number of successful links.
    """
    _print(tty_out, "")
    _print(
        tty_out,
        "Which git repos do you want to share AI-coding context for?",
    )
    _print(
        tty_out,
        "Paste an absolute path per line. Linking creates `<repo>/.dhee/`",
    )
    _print(
        tty_out,
        "and installs git hooks so context flows through `git push`/`pull`.",
    )
    _print(tty_out, "Press Enter on an empty line to finish (you can run `dhee link` later).")
    _print(tty_out, "")

    linked = 0
    while True:
        raw = _ask(tty_in, tty_out, "repo path (blank to finish): ").strip()
        if not raw:
            break
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(path):
            _print(tty_out, f"  ✗ {path} is not a directory; skipped.")
            continue
        ok, message = _link_repo(path)
        marker = "✓" if ok else "✗"
        _print(tty_out, f"  {marker} {message}")
        if ok:
            linked += 1
    return linked


def run_onboard(
    *,
    provider_default: Optional[str] = None,
    skip_ui_build: bool = False,  # Deprecated no-op; kept for old installers.
    link_paths: Optional[list[str]] = None,
    skip_link_prompt: bool = False,
) -> int:
    tty_in, tty_out = _open_tty()
    if tty_in is None or tty_out is None:
        sys.stderr.write(
            "dhee onboard requires an interactive terminal. Re-run from a "
            "shell (not a pipe) or set keys manually: `dhee key set <provider>`.\n"
        )
        return 1

    try:
        _print(tty_out, "──────────────────────────────────────────")
        _print(tty_out, "  Dhee setup · shared memory for AI agents")
        _print(tty_out, "──────────────────────────────────────────")

        if provider_default in PROVIDER_ORDER:
            provider = provider_default
            _print(tty_out, f"Using provider: {provider} (from flag)")
        else:
            provider = _pick_provider(tty_in, tty_out)

        _save_provider_in_config(provider)

        if provider == "ollama":
            _print(tty_out, "")
            _print(tty_out, "Ollama runs locally — no API key required.")
        else:
            env_var = PROVIDER_DEFAULTS.get(provider, {}).get("env_var")
            _print(tty_out, "")
            _print(
                tty_out,
                f"Paste your {provider.upper()} API key (env var {env_var}; input is hidden).",
            )
            _print(tty_out, "Leave blank to skip — you can set it later with `dhee key set`.")
            key = _ask_secret(tty_in, tty_out, "API key: ")
            if key:
                try:
                    preview = _save_key(provider, key)
                    _print(tty_out, f"✓ Stored securely ({preview}) under ~/.dhee/secret_store.enc.json")
                except Exception as exc:
                    _print(tty_out, f"Failed to store key: {exc}")
            else:
                _print(tty_out, "No key provided; skipping.")

        # ── Repo linking — the "share context across teammates" step ─
        if link_paths:
            _print(tty_out, "")
            for path in link_paths:
                resolved = os.path.abspath(os.path.expanduser(path))
                ok, message = _link_repo(resolved)
                marker = "✓" if ok else "✗"
                _print(tty_out, f"  {marker} {message}")
        elif not skip_link_prompt:
            _link_repos_interactive(tty_in, tty_out)

        _print(tty_out, "")
        _print(tty_out, "Done. Dhee Developer Brain is ready.")
        _print(tty_out, "Link more repos later with:")
        _print(tty_out, "  dhee link <path>")
        _print(tty_out, "Check shared-context conflicts with:")
        _print(tty_out, "  dhee context check")
        _print(tty_out, "Recover compact continuity with:")
        _print(tty_out, "  dhee handoff")
        _print(tty_out, "")
        _print(tty_out, "Update to the latest release:")
        _print(tty_out, "  dhee update")
        _print(tty_out, "")
        return 0
    finally:
        # Only close real file handles. In-memory buffers (StringIO /
        # BytesIO) have a ``.name`` attribute only when backed by a real
        # fd — we guard on that so tests can still read the captured
        # output after this function returns.
        for stream in (tty_in, tty_out):
            if stream is None:
                continue
            if stream is sys.stdin or stream is sys.stdout:
                continue
            if not hasattr(stream, "fileno"):
                continue
            try:
                stream.fileno()  # raises for StringIO
            except Exception:
                continue
            try:
                stream.close()
            except Exception:
                pass


def register(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p = sub.add_parser(
        "onboard",
        help="Interactive provider + API key setup plus optional repo linking",
    )
    p.add_argument(
        "--provider",
        choices=PROVIDER_ORDER,
        help="Skip the provider picker with this provider (non-interactive first step).",
    )
    p.add_argument(
        "--skip-ui-build",
        action="store_true",
        help="Deprecated no-op kept for old installers.",
    )
    p.add_argument(
        "--link",
        action="append",
        metavar="PATH",
        help=(
            "Link this git repo non-interactively (repeatable). "
            "Skips the interactive repo prompt."
        ),
    )
    p.add_argument(
        "--skip-link-prompt",
        action="store_true",
        help="Skip the 'which git repos to link?' step entirely.",
    )
    p.set_defaults(
        func=lambda args: sys.exit(
            run_onboard(
                provider_default=getattr(args, "provider", None),
                skip_ui_build=bool(getattr(args, "skip_ui_build", False)),
                link_paths=list(getattr(args, "link", None) or []) or None,
                skip_link_prompt=bool(getattr(args, "skip_link_prompt", False)),
            )
        )
    )
