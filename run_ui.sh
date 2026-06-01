#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
AUTO_OPEN=1

if [[ -f "$SCRIPT_DIR/.env.local" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env.local"
fi

pick_python() {
  local candidate
  for candidate in "$SCRIPT_DIR/.venv/bin/python" python3.11 python3; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if "$candidate" -c 'import fastapi, uvicorn, whisper, yt_dlp' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON_BIN="$(pick_python)"; then
  echo "ERROR: no usable Python interpreter found with fastapi, uvicorn, whisper, and yt_dlp installed" >&2
  echo "Run: uv venv \"$SCRIPT_DIR/.venv\" -p python3.11 && uv pip install --python \"$SCRIPT_DIR/.venv/bin/python\" -r \"$SCRIPT_DIR/requirements.txt\"" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is required to build the shadcn frontend" >&2
  exit 1
fi

if [[ "${1:-}" == "--no-open" ]]; then
  AUTO_OPEN=0
  shift
fi

PORT="${PORT:-}"
RELOAD="${RELOAD:-0}"

pick_port() {
  "$PYTHON_BIN" - <<'PY'
import socket

preferred_ports = [8000, 8001, 8002, 8010, 8080]
for port in preferred_ports:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        raise SystemExit(0)

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

wait_and_open() {
  local url="$1"
  if [[ "$AUTO_OPEN" != "1" ]]; then
    return 0
  fi

  "$PYTHON_BIN" - <<PY >/dev/null 2>&1 &
import time
import urllib.request
import webbrowser

url = ${url@Q}
for _ in range(80):
    try:
        with urllib.request.urlopen(url, timeout=0.5):
            webbrowser.open(url)
            break
    except Exception:
        time.sleep(0.25)
PY
}

if [[ -z "$PORT" ]]; then
  PORT="$(pick_port)"
fi

APP_URL="http://127.0.0.1:${PORT}"
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "Installing frontend dependencies..."
  (cd "$FRONTEND_DIR" && npm install)
fi

echo "Building frontend..."
(cd "$FRONTEND_DIR" && npm run build)

echo "Starting UI at ${APP_URL}"
wait_and_open "$APP_URL"

if [[ "$RELOAD" == "1" ]]; then
  exec "$PYTHON_BIN" -m uvicorn web_app:app --host 127.0.0.1 --port "$PORT" --reload "$@"
fi

exec "$PYTHON_BIN" -m uvicorn web_app:app --host 127.0.0.1 --port "$PORT" "$@"
