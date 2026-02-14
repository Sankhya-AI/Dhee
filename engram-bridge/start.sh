#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
#  start.sh — Launch engram-bridge backend + frontend
# ─────────────────────────────────────────────────────
#  Usage:
#    ./start.sh              # production (serves built frontend)
#    ./start.sh --dev        # development (vite dev server + backend)
#    ./start.sh --build      # build frontend then start production
# ─────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEBUI_DIR="$SCRIPT_DIR/engram_bridge/channels/web-ui"
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

cleanup() {
  echo ""
  echo "Shutting down..."
  # Kill background jobs (no -r flag — not available on macOS)
  jobs -p 2>/dev/null | xargs kill 2>/dev/null || true
  wait 2>/dev/null || true
  echo "Done."
}
trap cleanup EXIT INT TERM

# ── Ensure config exists ──
ensure_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "No config found at $CONFIG_FILE — creating default..."
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" <<'EOF'
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
EOF
    echo "Created $CONFIG_FILE"
  fi
}

# ── Install deps if needed ──
ensure_deps() {
  # Python: check engram is importable
  if ! python3 -c "import engram" 2>/dev/null; then
    echo "Installing Engram..."
    pip3 install --break-system-packages -e "$SCRIPT_DIR/../" --quiet 2>/dev/null || \
      pip3 install --break-system-packages -e "$SCRIPT_DIR/../"
  fi
  if ! python3 -c "import engram_bridge" 2>/dev/null; then
    echo "Installing engram-bridge..."
    pip3 install --break-system-packages -e "$SCRIPT_DIR" --quiet 2>/dev/null || \
      pip3 install --break-system-packages -e "$SCRIPT_DIR"
  fi

  # Node: check node_modules
  if [[ ! -d "$WEBUI_DIR/node_modules" ]]; then
    echo "Installing frontend dependencies..."
    (cd "$WEBUI_DIR" && npm install)
  fi
}

# ── Build frontend ──
build_frontend() {
  echo "Building frontend..."
  (cd "$WEBUI_DIR" && npm run build)
  echo "Frontend built → $WEBUI_DIR/dist/"
}

# ── Start backend ──
start_backend() {
  echo "Starting engram-bridge (port $BACKEND_PORT)..."
  engram-bridge --channel web --log-level "$LOG_LEVEL" --config "$CONFIG_FILE" &
  BACKEND_PID=$!
  echo "Backend PID: $BACKEND_PID"
}

# ── Start vite dev server ──
start_vite() {
  echo "Starting Vite dev server (port $VITE_PORT)..."
  (cd "$WEBUI_DIR" && npm run dev -- --port "$VITE_PORT") &
  VITE_PID=$!
  echo "Vite PID: $VITE_PID"
}

# ── Wait for backend to be ready ──
wait_for_backend() {
  local max_wait=30
  local waited=0
  while ! curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; do
    sleep 1
    waited=$((waited + 1))
    if [[ $waited -ge $max_wait ]]; then
      echo "Backend failed to start after ${max_wait}s"
      exit 1
    fi
  done
  echo "Backend ready."
}

# ══════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════

echo "═══════════════════════════════════════"
echo "  engram-bridge launcher  (mode: $MODE)"
echo "═══════════════════════════════════════"
echo ""

ensure_config
ensure_deps

case "$MODE" in
  build)
    build_frontend
    start_backend
    wait_for_backend
    echo ""
    echo "Open http://127.0.0.1:$BACKEND_PORT"
    echo "Press Ctrl+C to stop."
    wait
    ;;

  dev)
    start_backend
    wait_for_backend
    start_vite
    echo ""
    echo "Backend:  http://127.0.0.1:$BACKEND_PORT  (API + WebSocket)"
    echo "Frontend: http://localhost:$VITE_PORT      (Vite HMR)"
    echo ""
    echo "Use the Vite URL for development."
    echo "Press Ctrl+C to stop both."
    wait
    ;;

  production)
    # Check if frontend is built
    if [[ ! -f "$WEBUI_DIR/dist/index.html" ]]; then
      echo "Frontend not built yet — building now..."
      build_frontend
    fi
    start_backend
    wait_for_backend
    echo ""
    echo "Open http://127.0.0.1:$BACKEND_PORT"
    echo "Press Ctrl+C to stop."
    wait
    ;;
esac
