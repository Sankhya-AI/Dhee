#!/bin/sh
# engram installer — run with: curl -fsSL https://raw.githubusercontent.com/Ashish-dwi99/Engram/main/install.sh | sh
set -e

ENGRAM_HOME="$HOME/.engram"
VENV_DIR="$ENGRAM_HOME/venv"
BIN_DIR="$HOME/.local/bin"
MIN_PYTHON="3.9"
PACKAGE="engram-memory[all]"

# --- Colors (if terminal supports them) ---
if [ -t 1 ]; then
    BOLD="\033[1m"
    GREEN="\033[32m"
    YELLOW="\033[33m"
    RED="\033[31m"
    RESET="\033[0m"
else
    BOLD="" GREEN="" YELLOW="" RED="" RESET=""
fi

info()  { printf "${GREEN}=>${RESET} %s\n" "$1"; }
warn()  { printf "${YELLOW}warning:${RESET} %s\n" "$1"; }
error() { printf "${RED}error:${RESET} %s\n" "$1" >&2; exit 1; }

# --- Detect OS ---
OS="$(uname -s)"
case "$OS" in
    Darwin) OS_NAME="macOS" ;;
    Linux)  OS_NAME="Linux" ;;
    *)      error "Unsupported OS: $OS. engram supports macOS and Linux." ;;
esac
info "Detected $OS_NAME"

# --- Detect shell ---
SHELL_NAME="$(basename "$SHELL" 2>/dev/null || echo "sh")"
case "$SHELL_NAME" in
    zsh)  PROFILE="$HOME/.zshrc" ;;
    bash) PROFILE="$HOME/.bashrc" ;;
    fish) PROFILE="$HOME/.config/fish/config.fish" ;;
    *)    PROFILE="$HOME/.profile" ;;
esac

# --- Check Python ---
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

if [ -z "$PYTHON" ]; then
    error "Python $MIN_PYTHON+ is required but not found.

  Install Python:
    macOS:  brew install python3
    Ubuntu: sudo apt install python3 python3-venv
    Fedora: sudo dnf install python3"
fi

PYTHON_VER="$("$PYTHON" --version 2>&1)"
info "Using $PYTHON_VER"

# --- Create venv ---
if [ -d "$VENV_DIR" ]; then
    info "Updating existing venv at $VENV_DIR"
else
    info "Creating venv at $VENV_DIR"
    mkdir -p "$ENGRAM_HOME"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# --- Install package ---
info "Installing $PACKAGE ..."
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null 2>&1
"$VENV_DIR/bin/pip" install "$PACKAGE"

# --- Symlink binaries ---
mkdir -p "$BIN_DIR"
for bin_name in engram engram-mcp engram-bus; do
    src="$VENV_DIR/bin/$bin_name"
    dst="$BIN_DIR/$bin_name"
    if [ -f "$src" ]; then
        ln -sf "$src" "$dst"
        info "Linked $dst -> $src"
    fi
done

# --- Add to PATH if needed ---
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        if [ "$SHELL_NAME" = "fish" ]; then
            PATH_LINE="fish_add_path $BIN_DIR"
        else
            PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""
        fi
        if [ -f "$PROFILE" ] && grep -qF "$BIN_DIR" "$PROFILE" 2>/dev/null; then
            : # already in profile
        else
            printf "\n# engram\n%s\n" "$PATH_LINE" >> "$PROFILE"
            info "Added $BIN_DIR to PATH in $PROFILE"
            warn "Restart your shell or run: source $PROFILE"
        fi
        export PATH="$BIN_DIR:$PATH"
        ;;
esac

# --- Done ---
printf "\n${BOLD}${GREEN}engram installed successfully!${RESET}\n\n"
printf "  Run ${BOLD}engram setup${RESET} to get started.\n"
printf "  Uninstall: ${BOLD}engram uninstall${RESET} or ${BOLD}rm -rf ~/.engram${RESET}\n\n"
