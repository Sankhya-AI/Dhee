#!/usr/bin/env bash
#
# record_demo.sh — Record the Engram dashboard demo video end-to-end.
#
# Steps:
#   1. Start engram-bridge and seed demo data
#   2. Run Playwright recording script
#   3. Post-process with ffmpeg (mp4, poster, gif)
#   4. Place assets in landing page + docs
#
# Usage:
#   cd Engram && bash scripts/record_demo.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGRAM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LANDING_DIR="$(cd "$ENGRAM_DIR/../engram-landing" && pwd)"
WORK_DIR="$ENGRAM_DIR/_demo_work"

# Output locations
LANDING_DEMO="$LANDING_DIR/public/demo"
DOCS_DEMO="$ENGRAM_DIR/docs/demo"

# Cleanup function
cleanup() {
  echo ""
  echo "Cleaning up..."
  if [ -n "${BRIDGE_PID:-}" ] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
    kill "$BRIDGE_PID" 2>/dev/null || true
    wait "$BRIDGE_PID" 2>/dev/null || true
    echo "  Stopped engram-bridge (PID $BRIDGE_PID)"
  fi
  if [ -d "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR"
    echo "  Removed work directory"
  fi
}
trap cleanup EXIT

echo "═══════════════════════════════════════════"
echo "  Engram Dashboard Demo Recording"
echo "═══════════════════════════════════════════"
echo ""

# ── Pre-flight checks ──
echo "Checking prerequisites..."
command -v engram-bridge >/dev/null 2>&1 || { echo "ERROR: engram-bridge not found"; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ERROR: ffmpeg not found (brew install ffmpeg)"; exit 1; }
command -v npx >/dev/null 2>&1 || { echo "ERROR: npx not found"; exit 1; }
echo "  All prerequisites found."
echo ""

# ── Create work directory ──
mkdir -p "$WORK_DIR"
mkdir -p "$LANDING_DEMO"
mkdir -p "$DOCS_DEMO"

# ── Step 1: Start engram-bridge and seed data ──
echo "Step 1: Starting engram-bridge..."
engram-bridge --channel web > "$WORK_DIR/bridge.log" 2>&1 &
BRIDGE_PID=$!
echo "  PID: $BRIDGE_PID"

echo "  Waiting for health check..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8200/health > /dev/null 2>&1; then
    echo "  Bridge is healthy!"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Bridge did not become healthy in 30s"
    cat "$WORK_DIR/bridge.log"
    exit 1
  fi
  sleep 1
done

echo ""
echo "Step 1b: Seeding demo data..."
python3 "$SCRIPT_DIR/seed_demo.py" --no-start
echo ""

# ── Step 2: Record with Playwright ──
echo "Step 2: Recording with Playwright..."
cd "$ENGRAM_DIR"
npx tsx "$SCRIPT_DIR/record_demo.ts" "$WORK_DIR"
echo ""

if [ ! -f "$WORK_DIR/demo.webm" ]; then
  echo "ERROR: demo.webm was not created"
  exit 1
fi

echo "  Recording size: $(du -h "$WORK_DIR/demo.webm" | cut -f1)"
echo ""

# ── Step 3: Post-process with ffmpeg ──
echo "Step 3: Post-processing with ffmpeg..."

# Convert to mp4 (H.264, web-optimized)
echo "  Converting to mp4..."
ffmpeg -y -i "$WORK_DIR/demo.webm" \
  -c:v libx264 -crf 23 -preset medium \
  -movflags +faststart \
  -an \
  "$WORK_DIR/demo.mp4" 2>/dev/null
echo "  mp4: $(du -h "$WORK_DIR/demo.mp4" | cut -f1)"

# Extract poster frame (first frame)
echo "  Extracting poster frame..."
ffmpeg -y -i "$WORK_DIR/demo.mp4" \
  -vframes 1 -q:v 2 \
  "$WORK_DIR/demo-poster.jpg" 2>/dev/null
echo "  poster: $(du -h "$WORK_DIR/demo-poster.jpg" | cut -f1)"

# Create GIF (720px wide, 12fps, optimized palette)
echo "  Creating GIF..."
ffmpeg -y -i "$WORK_DIR/demo.mp4" \
  -vf "fps=12,scale=720:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
  -loop 0 \
  "$WORK_DIR/demo.gif" 2>/dev/null
echo "  gif: $(du -h "$WORK_DIR/demo.gif" | cut -f1)"
echo ""

# ── Step 4: Place assets ──
echo "Step 4: Placing assets..."

# Landing page
cp "$WORK_DIR/demo.mp4" "$LANDING_DEMO/demo.mp4"
cp "$WORK_DIR/demo.webm" "$LANDING_DEMO/demo.webm"
cp "$WORK_DIR/demo-poster.jpg" "$LANDING_DEMO/demo-poster.jpg"
echo "  Landing: $LANDING_DEMO/"

# README GIF
cp "$WORK_DIR/demo.gif" "$DOCS_DEMO/demo.gif"
echo "  Docs: $DOCS_DEMO/"

echo ""
echo "═══════════════════════════════════════════"
echo "  Done! Assets placed:"
echo "    $LANDING_DEMO/demo.mp4"
echo "    $LANDING_DEMO/demo.webm"
echo "    $LANDING_DEMO/demo-poster.jpg"
echo "    $DOCS_DEMO/demo.gif"
echo "═══════════════════════════════════════════"
