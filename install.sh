#!/bin/sh
# Dhee installer - one command for local world memory + repo context.
#
#   curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
#
# What it does:
#   1. Creates ~/.dhee with a hidden Python venv
#   2. Installs the dhee package
#   3. Symlinks `dhee` and `dhee-mcp` into ~/.local/bin
#   4. Wires Claude Code (hooks + MCP + router) if available
#   5. Runs `dhee onboard` — provider picker + API key paste
#   6. Shows `dhee ui` so the developer can inspect the local brain
#
# Non-interactive: pass DHEE_PROVIDER=openai DHEE_API_KEY=sk-... to skip
# the prompts entirely (CI-friendly). Set DHEE_INIT_REPO to a repo path,
# folder path, or git URL to run `dhee init` non-interactively after install.
# Set DHEE_INIT_SKIP_INGEST=1 for CI smoke tests that should link the
# workspace without calling an embedding provider.
#
# Requires: Python 3.9+  (Claude Code CLI optional)
set -e

DHEE_HOME="$HOME/.dhee"
VENV_DIR="$DHEE_HOME/.venv"
BIN_DIR="$HOME/.local/bin"
MIN_PYTHON="3.9"
DEFAULT_PACKAGE="dhee>=7.2.1"
PACKAGE="${DHEE_INSTALL_PACKAGE:-$DEFAULT_PACKAGE}"
FALLBACK_PACKAGE="${DHEE_FALLBACK_PACKAGE:-git+https://github.com/Sankhya-AI/Dhee.git@main}"

# --- Colors ---
if [ -t 1 ]; then
    BOLD="\033[1m" AMBER="\033[38;5;208m" RED="\033[31m" DIM="\033[2m" RESET="\033[0m"
else
    BOLD="" AMBER="" RED="" DIM="" RESET=""
fi

info()  { printf "> %s\n" "$1"; }
warn()  { printf "${AMBER}!${RESET} %s\n" "$1"; }
error() { printf "${RED}x${RESET} %s\n" "$1" >&2; exit 1; }
done_() { printf "${BOLD}✓${RESET} %s\n" "$1"; }

pip_install_package() {
    "$VENV_DIR/bin/pip" install --upgrade --force-reinstall --no-cache-dir "$1" -q
}

verify_handoff_bus() {
    "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
from dhee.core.kernel import _get_bus

bus = _get_bus()
bus.close()
PY
}

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
if pip_install_package "$PACKAGE"; then
    done_ "Installed dhee"
else
    if [ -n "${DHEE_INSTALL_PACKAGE:-}" ]; then
        error "Could not install Dhee from DHEE_INSTALL_PACKAGE=$DHEE_INSTALL_PACKAGE"
    fi
    warn "PyPI install failed — trying the current GitHub release path"
    command -v git >/dev/null 2>&1 || error "Git is required for the fallback installer. Install git, then rerun the command."
    pip_install_package "$FALLBACK_PACKAGE" || error "Could not install Dhee from PyPI or GitHub fallback"
    done_ "Installed dhee from GitHub fallback"
fi

# --- Verify bundled handoff bus ---
if verify_handoff_bus; then
    done_ "Cross-agent handoff bus ready"
else
    if [ -n "${DHEE_INSTALL_PACKAGE:-}" ]; then
        error "Dhee installed, but the bundled handoff bus failed to import from DHEE_INSTALL_PACKAGE=$DHEE_INSTALL_PACKAGE"
    fi
    warn "PyPI package failed handoff-bus verification — repairing from GitHub"
    command -v git >/dev/null 2>&1 || error "Git is required for the repair installer. Install git, then rerun the command."
    pip_install_package "$FALLBACK_PACKAGE" || error "Dhee installed, but GitHub repair failed. Please report this installer output."
    verify_handoff_bus || error "Dhee installed, but the bundled handoff bus still failed to import after repair."
    done_ "Cross-agent handoff bus ready"
fi

# --- Symlink binaries ---
mkdir -p "$BIN_DIR"
for bin_name in dhee dhee-mcp engram-bus; do
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
    # Interactive: `dhee onboard` opens /dev/tty itself when available and
    # returns a friendly nonzero status when no terminal is attached.
    "$VENV_DIR/bin/dhee" onboard || ONBOARD_STATUS=$?
    if [ "$ONBOARD_STATUS" -ne 0 ]; then
        warn "Interactive onboarding skipped — run 'dhee onboard' from a terminal to pick a provider and paste your API key."
        ONBOARD_STATUS=0
    fi
fi

if [ -n "${DHEE_INIT_REPO:-}" ]; then
    info "Wiring repo/folder: ${DHEE_INIT_REPO}"
    INIT_FLAGS=""
    [ "${DHEE_INIT_SKIP_INGEST:-}" = "1" ] && INIT_FLAGS="$INIT_FLAGS --skip-ingest"
    [ "${DHEE_INIT_SKIP_FIRST_LIGHT:-}" = "1" ] && INIT_FLAGS="$INIT_FLAGS --skip-first-light"
    # shellcheck disable=SC2086 # intentional flag splitting for the small managed flag set above.
    if "$VENV_DIR/bin/dhee" init "$DHEE_INIT_REPO" $INIT_FLAGS >/dev/null 2>&1; then
        done_ "Repo wired into Dhee"
    else
        warn "Repo/folder wire-up failed — run 'dhee init ${DHEE_INIT_REPO}' manually for details"
    fi
fi

# --- Done ---
printf "\n${BOLD}Dhee is ready.${RESET}\n"
printf "  Wire up context: ${BOLD}cd /path/to/repo-or-folder && dhee init${RESET}\n"
printf "                   ${BOLD}dhee init /path/to/folder${RESET} or ${BOLD}dhee init <git-url>${RESET}\n"
printf "  Open the UI:     ${BOLD}dhee ui${RESET}   ${DIM}(local command center, folders canvas, firewall)${RESET}\n"
printf "  Update later:    ${BOLD}dhee update${RESET}\n\n"
printf "${DIM}  Status:    dhee status            (savings + brain health)${RESET}\n"
printf "${DIM}  Recall:    dhee recall \"<query>\"   (your personal cross-repo brain)${RESET}\n"
printf "${DIM}  Inbox:     dhee inbox             (live broadcasts from your other agents)${RESET}\n"
printf "${DIM}  Remove:    dhee uninstall --yes       (stops daemon, removes managed venv + shell hooks)${RESET}\n\n"
