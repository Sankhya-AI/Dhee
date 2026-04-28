#!/bin/sh
# Dhee Developer Brain installer — one command, local memory + repo context.
#
#   curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
#
# What it does:
#   1. Creates ~/.dhee with a hidden Python venv
#   2. Installs the dhee package
#   3. Symlinks `dhee` and `dhee-mcp` into ~/.local/bin
#   4. Wires Claude Code (hooks + MCP + router) if available
#   5. Runs `dhee onboard` — provider picker, API key paste,
#      and optional git repo linking
#
# Non-interactive: pass DHEE_PROVIDER=openai DHEE_API_KEY=sk-... to skip
# the prompts entirely (CI-friendly).
#
# Requires: Python 3.9+  (Claude Code CLI optional)
set -e

DHEE_HOME="$HOME/.dhee"
VENV_DIR="$DHEE_HOME/.venv"
BIN_DIR="$HOME/.local/bin"
MIN_PYTHON="3.9"
PACKAGE="dhee>=6.1.0"

# --- Colors ---
if [ -t 1 ]; then
    BOLD="\033[1m" GREEN="\033[32m" YELLOW="\033[33m" RED="\033[31m" DIM="\033[2m" RESET="\033[0m"
else
    BOLD="" GREEN="" YELLOW="" RED="" DIM="" RESET=""
fi

info()  { printf "${GREEN}>${RESET} %s\n" "$1"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$1"; }
error() { printf "${RED}x${RESET} %s\n" "$1" >&2; exit 1; }
done_() { printf "${GREEN}✓${RESET} %s\n" "$1"; }

# --- OS check ---
OS="$(uname -s)"
case "$OS" in
    Darwin|Linux) ;;
    *) error "Unsupported OS: $OS. Dhee supports macOS and Linux." ;;
esac

# --- Find Python 3.9+ ---
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver="$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)"
        if [ -n "$ver" ]; then
            major="$(echo "$ver" | cut -d. -f1)"
            minor="$(echo "$ver" | cut -d. -f2)"
            if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
                PYTHON="$cmd"
                break
            fi
        fi
    fi
done

[ -z "$PYTHON" ] && error "Python $MIN_PYTHON+ required. Install: brew install python3 (macOS) or apt install python3 python3-venv (Linux)"

# --- Create/update venv ---
if [ -d "$VENV_DIR" ]; then
    info "Updating existing install"
    FRESH_INSTALL=0
else
    info "Installing Dhee"
    mkdir -p "$DHEE_HOME"
    "$PYTHON" -m venv "$VENV_DIR"
    FRESH_INSTALL=1
fi

# --- Install package ---
"$VENV_DIR/bin/pip" install --upgrade pip -q 2>/dev/null
"$VENV_DIR/bin/pip" install --upgrade --no-cache-dir "$PACKAGE" -q
done_ "Installed dhee"

# --- Symlink binaries ---
mkdir -p "$BIN_DIR"
for bin_name in dhee dhee-mcp; do
    src="$VENV_DIR/bin/$bin_name"
    dst="$BIN_DIR/$bin_name"
    [ -f "$src" ] && ln -sf "$src" "$dst"
done

# --- Add to PATH if needed ---
SHELL_NAME="$(basename "$SHELL" 2>/dev/null || echo "sh")"
case "$SHELL_NAME" in
    zsh)  PROFILE="$HOME/.zshrc" ;;
    bash) PROFILE="$HOME/.bashrc" ;;
    fish) PROFILE="$HOME/.config/fish/config.fish" ;;
    *)    PROFILE="$HOME/.profile" ;;
esac

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        if [ "$SHELL_NAME" = "fish" ]; then
            PATH_LINE="fish_add_path $BIN_DIR"
        else
            PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""
        fi
        if ! grep -qF "$BIN_DIR" "$PROFILE" 2>/dev/null; then
            printf "\n# dhee\n%s\n" "$PATH_LINE" >> "$PROFILE"
        fi
        export PATH="$BIN_DIR:$PATH"
        ;;
esac

# --- Full Claude Code bootstrap (hooks + MCP + router) ---
if command -v claude >/dev/null 2>&1 || [ -f "$HOME/.claude/settings.json" ]; then
    if "$VENV_DIR/bin/dhee" install >/dev/null 2>&1; then
        done_ "Claude Code wired: hooks + MCP + router"
    else
        warn "Claude Code bootstrap failed — run 'dhee install' manually for details"
    fi
else
    warn "Claude Code not found — run 'dhee install' after installing Claude Code"
fi

# --- Onboarding (interactive provider + key) ---
# If the caller set DHEE_PROVIDER + DHEE_API_KEY we stash the key
# non-interactively and skip the prompt.
NONINTERACTIVE_DONE=0
if [ -n "${DHEE_PROVIDER:-}" ] && [ -n "${DHEE_API_KEY:-}" ]; then
    info "Non-interactive onboarding for provider: ${DHEE_PROVIDER}"
    if "$VENV_DIR/bin/python" -c "
import os, sys
from dhee.cli_onboard import _save_provider_in_config
from dhee.secret_store import store_api_key
try:
    provider = os.environ['DHEE_PROVIDER']
    _save_provider_in_config(provider)
    store_api_key(provider, os.environ['DHEE_API_KEY'], label='installer')
except Exception as e:
    print(e, file=sys.stderr); sys.exit(1)
" >/dev/null 2>&1; then
        done_ "Provider configured and API key stored for ${DHEE_PROVIDER}"
        NONINTERACTIVE_DONE=1
    else
        warn "Non-interactive key storage failed — falling back to prompt"
    fi
fi

ONBOARD_STATUS=0
if [ "$NONINTERACTIVE_DONE" = "1" ]; then
    info "Skipping interactive onboarding"
else
    # Interactive: onboard reads from /dev/tty so this works under curl | sh.
    if [ -r /dev/tty ]; then
        "$VENV_DIR/bin/dhee" onboard < /dev/tty || ONBOARD_STATUS=$?
    else
        warn "No TTY detected — skipping interactive onboarding."
        warn "Run 'dhee onboard' manually to pick a provider and paste your API key."
        ONBOARD_STATUS=0
    fi
fi

# --- Done ---
printf "\n${BOLD}${GREEN}Dhee is ready.${RESET}\n"
printf "  Link a repo:   ${BOLD}dhee link /path/to/repo${RESET}\n"
printf "  Update later:  ${BOLD}dhee update${RESET}\n\n"
printf "${DIM}  Inspect:   dhee links | dhee context check${RESET}\n"
printf "${DIM}  Memory:    dhee recall \"what changed?\" | dhee handoff${RESET}\n"
printf "${DIM}  Remove:    dhee uninstall-hooks && rm -rf ~/.dhee${RESET}\n\n"
