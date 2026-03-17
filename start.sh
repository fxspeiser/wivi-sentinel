#!/usr/bin/env bash
# Wi-Vi Sentinel — start Flask API + dashboard + RuView Docker
#
# Usage:
#   ./start.sh          Auto-detects: Vite dev server if Node >=18, else Flask serves dist/
#   ./start.sh dev      Force Vite dev mode (hot-reload, requires Node >=18)
#   ./start.sh prod     Force production mode (Flask serves pre-built dist/)
#   ./start.sh build    Build the dashboard (run on Mac, then deploy dist/ to Pi)

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Load .env if present
if [ -f .env ]; then
    set -a; source .env; set +a
fi

FLASK_PORT="${FLASK_PORT:-5555}"
VITE_PORT="${VITE_PORT:-3000}"
RUVIEW_PORT="${RUVIEW_PORT:-3100}"
RUVIEW_ENABLED="${RUVIEW_ENABLED:-true}"
RUVIEW_CONTAINER="wivi-ruview"
MODE="${1:-auto}"

# ── Activate Python venv if present ──
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

# ── Build mode: just build and exit ──
if [ "$MODE" = "build" ]; then
    echo "[Build]  Building dashboard into dist/..."
    npm run build
    echo "[Build]  Done. Deploy dist/ to your Pi."
    exit 0
fi

# ── Auto-detect: can we run Vite? ──
if [ "$MODE" = "auto" ]; then
    NODE_VER=$(node --version 2>/dev/null | sed 's/v//' | cut -d. -f1)
    if [ -n "$NODE_VER" ] && [ "$NODE_VER" -ge 18 ] 2>/dev/null; then
        MODE="dev"
    else
        MODE="prod"
    fi
fi

# ── RuView Docker ─────────────────────────────────────────────────────────────
RUVIEW_PID=""

start_ruview() {
    if [ "$RUVIEW_ENABLED" != "true" ]; then
        echo "[RuView] Disabled (RUVIEW_ENABLED=false)"
        return
    fi

    if ! command -v docker &>/dev/null; then
        echo "[RuView] Docker not found — skipping RuView"
        echo "[RuView] Install Docker and rerun to enable pose estimation"
        return
    fi

    # Stop any stale container with the same name
    if docker ps -a --format '{{.Names}}' | grep -q "^${RUVIEW_CONTAINER}$"; then
        echo "[RuView] Removing stale container '${RUVIEW_CONTAINER}'..."
        docker rm -f "$RUVIEW_CONTAINER" &>/dev/null || true
    fi

    # Determine the CSI source for RuView — run simulated alongside esp32/nexmon
    RUVIEW_CSI="${RUVIEW_CSI_SOURCE:-simulated}"

    echo "[RuView] Starting Docker container on :${RUVIEW_PORT} (CSI_SOURCE=${RUVIEW_CSI})..."
    docker run -d \
        --name "$RUVIEW_CONTAINER" \
        -p "${RUVIEW_PORT}:3000" \
        -p "$((RUVIEW_PORT + 1)):3001" \
        -p "5005:5005/udp" \
        -e "CSI_SOURCE=${RUVIEW_CSI}" \
        ruvnet/wifi-densepose:latest \
        &>/dev/null

    # Give it a moment to start, then verify
    sleep 2
    if docker ps --format '{{.Names}}' | grep -q "^${RUVIEW_CONTAINER}$"; then
        echo "[RuView] Running → http://localhost:${RUVIEW_PORT}/ui/observatory.html"
    else
        echo "[RuView] Container failed to start — check: docker logs ${RUVIEW_CONTAINER}"
    fi
}

start_ruview

# ── Cleanup ───────────────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$VITE_PID" ]  && kill "$VITE_PID"  2>/dev/null
    [ -n "$FLASK_PID" ] && kill "$FLASK_PID" 2>/dev/null
    if docker ps --format '{{.Names}}' | grep -q "^${RUVIEW_CONTAINER}$" 2>/dev/null; then
        echo "[RuView] Stopping container..."
        docker stop "$RUVIEW_CONTAINER" &>/dev/null || true
        docker rm   "$RUVIEW_CONTAINER" &>/dev/null || true
    fi
    wait 2>/dev/null
    exit 0
}
trap cleanup INT TERM

# ── Start servers ─────────────────────────────────────────────────────────────
if [ "$MODE" = "dev" ]; then
    echo "[Flask]  Starting API server on :${FLASK_PORT}"
    python3 server.py &
    FLASK_PID=$!

    echo "[Vite]   Starting dashboard on :${VITE_PORT}"
    npx vite --host &
    VITE_PID=$!

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Wi-Vi Sentinel running (dev mode)"
    echo "  Dashboard:   http://localhost:${VITE_PORT}"
    echo "  API:         http://localhost:${FLASK_PORT}"
    [ "$RUVIEW_ENABLED" = "true" ] && \
    echo "  RuView:      http://localhost:${RUVIEW_PORT}/ui/observatory.html"
    echo "  Press Ctrl+C to stop all services"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    if [ ! -f dist/index.html ]; then
        echo "[WARN]   dist/index.html not found."
        echo "         Run './start.sh build' on a machine with Node >=18,"
        echo "         then copy the dist/ folder here."
        exit 1
    fi

    echo "[Flask]  Starting server on :${FLASK_PORT} (serving pre-built dashboard)"
    python3 server.py &
    FLASK_PID=$!

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Wi-Vi Sentinel running (production)"
    echo "  Dashboard + API:  http://localhost:${FLASK_PORT}"
    [ "$RUVIEW_ENABLED" = "true" ] && \
    echo "  RuView:           http://localhost:${RUVIEW_PORT}/ui/observatory.html"
    echo "  Press Ctrl+C to stop all services"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

echo ""
wait
