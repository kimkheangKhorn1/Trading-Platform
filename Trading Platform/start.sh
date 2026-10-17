#!/usr/bin/env bash
# Start the XAUUSD trading platform locally.
#
#   ./start.sh          development: FastAPI (auto-reload) + Vite dev server  -> http://127.0.0.1:5173
#   ./start.sh prod     build the frontend once, FastAPI serves everything     -> http://127.0.0.1:8010
#   ./start.sh test     run the backend and frontend test suites
#
# Ports can be changed with BACKEND_PORT (default 8010) and FRONTEND_PORT (default 5173).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
MODE="${1:-dev}"

die() { echo "error: $*" >&2; exit 1; }
port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

command -v python3 >/dev/null || die "python3 not found (Python 3.12+ required)"
command -v node >/dev/null || die "node not found (Node.js 20+ required)"

# --- one-time setup --------------------------------------------------------------
if [ ! -x "$BACKEND/.venv/bin/python" ]; then
  echo "Creating Python virtual environment…"
  python3 -m venv "$BACKEND/.venv"
  "$BACKEND/.venv/bin/pip" install --upgrade pip >/dev/null
fi
if ! "$BACKEND/.venv/bin/python" -c "import fastapi, uvicorn, numpy, pandas, httpx, multipart, dotenv, cryptography, websockets, pyarrow, duckdb" 2>/dev/null; then
  echo "Installing backend dependencies…"
  "$BACKEND/.venv/bin/pip" install -r "$BACKEND/requirements.txt"
fi
if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "Installing frontend dependencies…"
  (cd "$FRONTEND" && npm install)
fi
[ -f "$BACKEND/.env" ] || echo "note: backend/.env not found - configure the market-data API using backend/.env.example"

case "$MODE" in
  test)
    (cd "$BACKEND" && .venv/bin/python -m pytest)
    (cd "$FRONTEND" && npx tsc -b && npx vitest run)
    exit 0
    ;;
  prod)
    port_busy "$BACKEND_PORT" && die "port $BACKEND_PORT is already in use (another app?). Try: BACKEND_PORT=8020 ./start.sh prod"
    echo "Building frontend…"
    (cd "$FRONTEND" && npm run build)
    echo "Serving on http://127.0.0.1:$BACKEND_PORT"
    cd "$BACKEND"
    exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
    ;;
  dev)
    port_busy "$BACKEND_PORT" && die "port $BACKEND_PORT is already in use (another app?). Try: BACKEND_PORT=8020 ./start.sh"
    port_busy "$FRONTEND_PORT" && die "port $FRONTEND_PORT is already in use. Try: FRONTEND_PORT=5174 ./start.sh"
    pids=()
    cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
    trap cleanup EXIT INT TERM
    (cd "$BACKEND" && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload --reload-dir app) &
    pids+=($!)
    (cd "$FRONTEND" && BACKEND_PORT="$BACKEND_PORT" exec npx vite --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort) &
    pids+=($!)
    echo ""
    echo "  Platform:  http://127.0.0.1:$FRONTEND_PORT"
    echo "  API:       http://127.0.0.1:$BACKEND_PORT/docs"
    echo "  Ctrl+C stops both servers."
    echo ""
    wait
    ;;
  *)
    die "unknown mode '$MODE' (use: dev, prod or test)"
    ;;
esac
