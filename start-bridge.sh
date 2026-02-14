#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  start-bridge.sh — One-command setup & launch for Engram Bridge
# ═══════════════════════════════════════════════════════════
#
#  Run from the Engram repo root:
#
#    ./start-bridge.sh              # production (build frontend, serve from backend)
#    ./start-bridge.sh --dev        # development (vite HMR + backend in parallel)
#    ./start-bridge.sh --build      # force rebuild frontend, then production
#
#  Environment variables (all optional):
#    ENGRAM_PORT=8200               # backend port
#    VITE_PORT=5173                 # vite dev server port (--dev only)
#    ENGRAM_BRIDGE_CONFIG=~/.engram/bridge.json
#    ENGRAM_LOG_LEVEL=INFO
#
# ═══════════════════════════════════════════════════════════

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_DIR="$ROOT_DIR/engram-bridge"
WEBUI_DIR="$BRIDGE_DIR/engram_bridge/channels/web-ui"
CONFIG_FILE="${ENGRAM_BRIDGE_CONFIG:-$HOME/.engram/bridge.json}"
LOG_LEVEL="${ENGRAM_LOG_LEVEL:-INFO}"
BACKEND_PORT="${ENGRAM_PORT:-8200}"
VITE_PORT="${VITE_PORT:-5173}"

MODE="production"
if [[ "${1:-}" == "--dev" ]]; then
  MODE="dev"
elif [[ "${1:-}" == "--build" ]]; then
  MODE="build"
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[engram]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*"; }

# ── Cleanup on exit ──
cleanup() {
  echo ""
  log "Shutting down..."
  jobs -p 2>/dev/null | xargs kill 2>/dev/null || true
  wait 2>/dev/null || true
  log "Done."
}
trap cleanup EXIT INT TERM

# ══════════════════════════════════════════════════════════
#  1. Prerequisites
# ══════════════════════════════════════════════════════════

check_prerequisites() {
  log "Checking prerequisites..."

  # Python 3
  if ! command -v python3 &>/dev/null; then
    err "python3 not found. Install Python 3.10+ first."
    exit 1
  fi
  ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2)"

  # pip
  if ! python3 -m pip --version &>/dev/null; then
    err "pip not found. Install pip first."
    exit 1
  fi
  ok "pip available"

  # Node.js
  if ! command -v node &>/dev/null; then
    err "node not found. Install Node.js 18+ first."
    exit 1
  fi
  ok "node $(node --version)"

  # npm
  if ! command -v npm &>/dev/null; then
    err "npm not found. Install npm first."
    exit 1
  fi
  ok "npm $(npm --version)"
}

# ══════════════════════════════════════════════════════════
#  2. Install Python packages
# ══════════════════════════════════════════════════════════

install_python() {
  log "Setting up Python packages..."

  # Install Engram core (editable)
  if python3 -c "import engram" 2>/dev/null; then
    ok "engram already installed"
  else
    log "Installing engram..."
    pip3 install -e "$ROOT_DIR" --quiet 2>/dev/null || pip3 install -e "$ROOT_DIR"
    ok "engram installed"
  fi

  # Install engram-bridge (editable, with web extras)
  if python3 -c "import engram_bridge" 2>/dev/null; then
    ok "engram-bridge already installed"
  else
    log "Installing engram-bridge[web]..."
    pip3 install -e "$BRIDGE_DIR[web]" --quiet 2>/dev/null || pip3 install -e "$BRIDGE_DIR[web]"
    ok "engram-bridge installed"
  fi
}

# ══════════════════════════════════════════════════════════
#  3. Install frontend dependencies
# ══════════════════════════════════════════════════════════

install_frontend() {
  log "Setting up frontend..."

  if [[ -d "$WEBUI_DIR/node_modules" ]]; then
    ok "node_modules exists"
  else
    log "Running npm install..."
    (cd "$WEBUI_DIR" && npm install --loglevel=warn)
    ok "frontend dependencies installed"
  fi
}

# ══════════════════════════════════════════════════════════
#  4. Build frontend
# ══════════════════════════════════════════════════════════

build_frontend() {
  log "Building frontend..."
  (cd "$WEBUI_DIR" && npm run build)
  ok "frontend built → dist/"
}

# ══════════════════════════════════════════════════════════
#  5. Create default config if missing
# ══════════════════════════════════════════════════════════

ensure_config() {
  if [[ -f "$CONFIG_FILE" ]]; then
    ok "config: $CONFIG_FILE"
    return
  fi

  log "Creating default config at $CONFIG_FILE ..."
  mkdir -p "$(dirname "$CONFIG_FILE")"
  cat > "$CONFIG_FILE" <<'CONF'
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
    }
  },
  "memory": {
    "provider": "simple",
    "auto_store": true
  }
}
CONF
  ok "config created: $CONFIG_FILE"
}

# ══════════════════════════════════════════════════════════
#  6. Start servers
# ══════════════════════════════════════════════════════════

start_backend() {
  log "Starting backend on port $BACKEND_PORT ..."
  engram-bridge --channel web --log-level "$LOG_LEVEL" --config "$CONFIG_FILE" &
  BACKEND_PID=$!
}

start_vite() {
  log "Starting Vite dev server on port $VITE_PORT ..."
  (cd "$WEBUI_DIR" && npm run dev -- --port "$VITE_PORT") &
  VITE_PID=$!
}

wait_for_backend() {
  local max=30
  local i=0
  while ! curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; do
    sleep 1
    i=$((i + 1))
    if [[ $i -ge $max ]]; then
      err "Backend failed to start after ${max}s"
      exit 1
    fi
  done
  ok "backend ready"
}

# ══════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${BOLD}  Engram Bridge — setup & launch  ${CYAN}($MODE)${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""

check_prerequisites
echo ""
install_python
echo ""
install_frontend
echo ""
ensure_config
echo ""

case "$MODE" in
  build)
    build_frontend
    echo ""
    start_backend
    wait_for_backend
    echo ""
    echo -e "${GREEN}${BOLD}  Ready!${NC}  Open ${CYAN}http://127.0.0.1:$BACKEND_PORT${NC}"
    echo -e "  Your Engram memory at ${CYAN}~/.engram/${NC} is connected."
    echo -e "  Press ${BOLD}Ctrl+C${NC} to stop."
    echo ""
    wait
    ;;

  dev)
    start_backend
    wait_for_backend
    start_vite
    echo ""
    echo -e "${GREEN}${BOLD}  Ready!${NC}"
    echo -e "  Backend:  ${CYAN}http://127.0.0.1:$BACKEND_PORT${NC}  (API + WebSocket)"
    echo -e "  Frontend: ${CYAN}http://localhost:$VITE_PORT${NC}      (Vite HMR)"
    echo ""
    echo -e "  Open the ${BOLD}Vite URL${NC} for development."
    echo -e "  Your Engram memory at ${CYAN}~/.engram/${NC} is connected."
    echo -e "  Press ${BOLD}Ctrl+C${NC} to stop both."
    echo ""
    wait
    ;;

  production)
    if [[ ! -f "$WEBUI_DIR/dist/index.html" ]]; then
      warn "Frontend not built yet — building now..."
      build_frontend
      echo ""
    fi
    start_backend
    wait_for_backend
    echo ""
    echo -e "${GREEN}${BOLD}  Ready!${NC}  Open ${CYAN}http://127.0.0.1:$BACKEND_PORT${NC}"
    echo -e "  Your Engram memory at ${CYAN}~/.engram/${NC} is connected."
    echo -e "  Press ${BOLD}Ctrl+C${NC} to stop."
    echo ""
    wait
    ;;
esac
