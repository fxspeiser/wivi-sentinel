#!/usr/bin/env bash
# Wi-Vi Sentinel — unified start / control script
#
# On the Pi (systemd installed):
#   ./start.sh              start via systemd (or direct if unit missing)
#   ./start.sh restart      sudo systemctl restart wivi-sentinel
#   ./start.sh stop         sudo systemctl stop wivi-sentinel
#   ./start.sh status       systemctl status wivi-sentinel
#   ./start.sh logs         journalctl -u wivi-sentinel -f
#
# On Mac / dev machines (no systemd):
#   ./start.sh dev          Flask API + Vite hot-reload (requires Node >=18)
#   ./start.sh build        Build dist/ only (run on Mac, rsync to Pi)
#
# Auto-detect: if systemd unit exists → use systemctl; else → direct process

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

# ── Helper: does the systemd unit exist? ──────────────────────────────────────
_has_systemd_unit() {
    systemctl list-unit-files wivi-sentinel.service &>/dev/null 2>&1 && \
    systemctl list-unit-files wivi-sentinel.service | grep -q "wivi-sentinel"
}

# ── Explicit control commands ─────────────────────────────────────────────────

if [ "$MODE" = "restart" ]; then
    echo "[systemd] Restarting wivi-sentinel..."
    sudo systemctl restart wivi-sentinel
    sleep 1
    systemctl is-active wivi-sentinel && echo "[systemd] Running." || echo "[systemd] Failed — check: journalctl -u wivi-sentinel -n 30"
    exit $?
fi

if [ "$MODE" = "stop" ]; then
    echo "[systemd] Stopping wivi-sentinel..."
    sudo systemctl stop wivi-sentinel
    echo "[systemd] Stopped."
    exit 0
fi

if [ "$MODE" = "status" ]; then
    systemctl status wivi-sentinel
    exit $?
fi

if [ "$MODE" = "logs" ]; then
    journalctl -u wivi-sentinel -f
    exit 0
fi

# ── Build mode ────────────────────────────────────────────────────────────────
if [ "$MODE" = "build" ]; then
    echo "[Build]  Building dashboard into dist/..."
    npm run build
    echo "[Build]  Done. Deploy dist/ to your Pi:"
    echo "         rsync -av dist/ <user>@<pi-ip>:~/wivi-sentinel/dist/"
    exit 0
fi

# ── Dev mode (Mac / machines with Node >=18) ──────────────────────────────────
if [ "$MODE" = "dev" ]; then
    if [ -f venv/bin/activate ]; then source venv/bin/activate; fi

    cleanup() {
        echo ""; echo "Shutting down..."
        [ -n "$VITE_PID" ]  && kill "$VITE_PID"  2>/dev/null
        [ -n "$FLASK_PID" ] && kill "$FLASK_PID" 2>/dev/null
        wait 2>/dev/null; exit 0
    }
    trap cleanup INT TERM

    echo "[Flask]  Starting API server on :${FLASK_PORT}"
    python3 server.py &
    FLASK_PID=$!

    echo "[Vite]   Starting dashboard on :${VITE_PORT}"
    npx vite --host &
    VITE_PID=$!

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Wi-Vi Sentinel  (dev mode)"
    echo "  Dashboard:  http://localhost:${VITE_PORT}"
    echo "  API:        http://localhost:${FLASK_PORT}"
    echo "  Press Ctrl+C to stop"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    wait
    exit 0
fi

# ── Auto / prod mode: prefer systemd, fall back to direct ─────────────────────

if _has_systemd_unit; then
    IS_ACTIVE=$(systemctl is-active wivi-sentinel 2>/dev/null || true)
    if [ "$IS_ACTIVE" = "active" ]; then
        echo "[systemd] wivi-sentinel is already running — restarting to apply changes..."
        sudo systemctl restart wivi-sentinel
    else
        echo "[systemd] Starting wivi-sentinel..."
        sudo systemctl start wivi-sentinel
    fi
    sleep 1
    STATUS=$(systemctl is-active wivi-sentinel 2>/dev/null || echo "unknown")
    PI_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Wi-Vi Sentinel  (systemd — ${STATUS})"
    echo "  Dashboard:  http://${PI_IP}:${FLASK_PORT}"
    echo "  API:        http://${PI_IP}:${FLASK_PORT}/api/status"
    echo "  Logs:       journalctl -u wivi-sentinel -f"
    echo "  Stop:       ./start.sh stop"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
fi

# ── Direct fallback (no systemd unit — Pi without service installed, or Mac) ──
if [ -f venv/bin/activate ]; then source venv/bin/activate; fi

if [ ! -f dist/index.html ]; then
    echo "[WARN]   dist/index.html not found."
    echo "         Run './start.sh build' on a machine with Node >=18,"
    echo "         then rsync dist/ to this machine."
    exit 1
fi

cleanup() {
    echo ""; echo "Shutting down..."
    [ -n "$FLASK_PID" ] && kill "$FLASK_PID" 2>/dev/null
    wait 2>/dev/null; exit 0
}
trap cleanup INT TERM

echo "[Flask]  Starting server on :${FLASK_PORT} (serving pre-built dashboard)"
python3 server.py &
FLASK_PID=$!

PI_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Wi-Vi Sentinel  (direct)"
echo "  Dashboard:  http://${PI_IP}:${FLASK_PORT}"
echo "  Press Ctrl+C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
wait
