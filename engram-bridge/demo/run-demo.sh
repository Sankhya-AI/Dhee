#!/bin/bash
# One-command demo setup: enable auto_execute, start bridge, seed data, open browser.
#
# Usage:
#   cd Engram/engram-bridge
#   bash demo/run-demo.sh

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/.."

echo ""
echo "=== engram-bridge Auto-Execute Demo ==="
echo ""

# 1. Enable auto_execute
python3 demo/seed.py --enable 2>/dev/null || true

# 2. Start bridge in background
echo "Starting bridge..."
engram-bridge --channel web &
BRIDGE_PID=$!
sleep 3

# 3. Seed demo data
echo "Seeding demo data..."
python3 demo/seed.py

# 4. Open browser
if command -v open &>/dev/null; then
    open "http://127.0.0.1:8200"
elif command -v xdg-open &>/dev/null; then
    xdg-open "http://127.0.0.1:8200"
fi

echo ""
echo "Bridge running (PID $BRIDGE_PID). Press Ctrl+C to stop."
echo ""

# Wait for bridge
wait $BRIDGE_PID
