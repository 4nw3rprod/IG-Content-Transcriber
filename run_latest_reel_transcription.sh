#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pick_python() {
  local candidate
  for candidate in "$SCRIPT_DIR/.venv/bin/python" python3.11 python3; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if "$candidate" -c 'import whisper, yt_dlp' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON_BIN="$(pick_python)"; then
  echo "ERROR: no usable Python interpreter found with both 'whisper' and 'yt_dlp' installed" >&2
  echo "Run: python3.11 -m venv \"$SCRIPT_DIR/.venv\" && \"$SCRIPT_DIR/.venv/bin/pip\" install -r \"$SCRIPT_DIR/requirements.txt\"" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/transcribe_latest_reel.py" "$@"
