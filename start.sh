#!/usr/bin/env bash
# Wi-Vi Sentinel — start Flask API + dashboard
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

cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$VITE_PID" ] && kill $VITE_PID 2>/dev/null
    [ -n "$FLASK_PID" ] && kill $FLASK_PID 2>/dev/null
    wait 2>/dev/null
    exit 0
}
trap cleanup INT TERM

if [ "$MODE" = "dev" ]; then
    # ── Dev mode: Flask API + Vite dev server with hot-reload ──
    echo "[Flask]  Starting API server on :${FLASK_PORT}"
    python3 server.py &
    FLASK_PID=$!

    echo "[Vite]   Starting dashboard on :${VITE_PORT}"
    npx vite --host &
    VITE_PID=$!

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Wi-Vi Sentinel running (dev mode)"
    echo "  Dashboard:  http://localhost:${VITE_PORT}"
    echo "  API:        http://localhost:${FLASK_PORT}"
    echo "  Press Ctrl+C to stop"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    # ── Production mode: Flask serves everything ──
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
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Wi-Vi Sentinel running (production)"
    echo "  Dashboard + API:  http://localhost:${FLASK_PORT}"
    echo "  Press Ctrl+C to stop"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

echo ""
wait
