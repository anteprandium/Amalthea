#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SAGE_BIN="${SAGE_BIN:-/usr/local/bin/sage}"
APP_NAME="Amalthea.app"
DIST_APP="$ROOT_DIR/dist/$APP_NAME"
INSTALLED_APP="/Applications/$APP_NAME"

log() {
  echo "==> $*"
}

stop_amalthea_processes() {
  log "Stopping running Amalthea processes..."

  pkill -x Amalthea 2>/dev/null || true
  pkill -f '/Amalthea\.app/Contents/MacOS/Amalthea' 2>/dev/null || true
  pkill -f '/Amalthea\.py' 2>/dev/null || true

  sleep 1

  pkill -9 -x Amalthea 2>/dev/null || true
  pkill -9 -f '/Amalthea\.app/Contents/MacOS/Amalthea' 2>/dev/null || true
  pkill -9 -f '/Amalthea\.py' 2>/dev/null || true

  sudo pkill -9 -x Amalthea 2>/dev/null || true
  sudo pkill -9 -f '/Amalthea\.app/Contents/MacOS/Amalthea' 2>/dev/null || true
  sudo pkill -9 -f '/Amalthea\.py' 2>/dev/null || true
}

stop_sage_server() {
  if [[ -x "$SAGE_BIN" ]]; then
    log "Stopping Sage/Jupyter server on port 8988..."
    "$SAGE_BIN" -python -m jupyter_server stop 8988 >/dev/null 2>&1 || true
  fi

  if command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -ti tcp:8988 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      log "Killing remaining listeners on port 8988..."
      kill $pids 2>/dev/null || true
      sleep 1
      kill -9 $pids 2>/dev/null || true
      sudo kill -9 $pids 2>/dev/null || true
    fi
  fi
}

install_app() {
  if [[ ! -d "$DIST_APP" ]]; then
    echo "Fresh build not found at: $DIST_APP" >&2
    exit 1
  fi

  log "Removing installed app from /Applications..."
  sudo rm -rf "$INSTALLED_APP"

  log "Moving fresh build into /Applications..."
  sudo mv "$DIST_APP" "$INSTALLED_APP"

  log "Installed: $INSTALLED_APP"
}

cleanup_build_artifacts() {
  log "Cleaning local build artifacts..."
  rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist" "$ROOT_DIR/Amalthea.spec"
}

stop_amalthea_processes
stop_sage_server

log "Building fresh app bundle..."
"$ROOT_DIR/build_macos_app.sh"

install_app
cleanup_build_artifacts
