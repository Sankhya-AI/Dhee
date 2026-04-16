#!/bin/sh
# Dhee installer — one command to add cognition to Claude Code.
#
#   curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/dhee/main/install.sh | sh
#
# What it does:
#   1. Creates ~/.dhee with a hidden Python venv
#   2. Installs the dhee package
#   3. Configures Claude Code hooks (automatic cognition every session)
#
# Requires: Python 3.9+, Claude Code CLI
set -e

DHEE_HOME="$HOME/.dhee"
VENV_DIR="$DHEE_HOME/.venv"
BIN_DIR="$HOME/.local/bin"
MIN_PYTHON="3.9"
PACKAGE="dhee[all]"

# --- Colors ---
if [ -t 1 ]; then
    BOLD="\033[1m" GREEN="\033[32m" YELLOW="\033[33m" RED="\033[31m" RESET="\033[0m"
else
    BOLD="" GREEN="" YELLOW="" RED="" RESET=""
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
else
    info "Installing Dhee"
    mkdir -p "$DHEE_HOME"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# --- Install package ---
"$VENV_DIR/bin/pip" install --upgrade pip -q 2>/dev/null
"$VENV_DIR/bin/pip" install --upgrade "$PACKAGE" -q
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

# --- Install Claude Code hooks ---
if command -v claude >/dev/null 2>&1 || [ -f "$HOME/.claude/settings.json" ]; then
    "$VENV_DIR/bin/dhee" install >/dev/null 2>&1 && done_ "Claude Code hooks configured" || warn "Hook install failed — run 'dhee install' manually"
else
    warn "Claude Code not found — run 'dhee install' after installing Claude Code"
fi

# --- Done ---
printf "\n${BOLD}${GREEN}Dhee is ready.${RESET}\n"
printf "  Open Claude Code in any project — cognition is automatic.\n\n"
printf "  Commands:  dhee status | dhee install | dhee uninstall\n"
printf "  Update:    re-run this script\n"
printf "  Remove:    rm -rf ~/.dhee && dhee uninstall-hooks\n\n"
