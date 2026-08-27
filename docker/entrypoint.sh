#!/bin/sh
set -eu

STATE_DIR="${TIMELAPSE_STATE_DIR:-/var/lib/pi-timelapse}"
mkdir -p "$STATE_DIR"

if [ "${1:-}" = "check-camera" ]; then
  exec /opt/pi-timelapse/.venv/bin/timelapse check-camera
fi

if [ $# -gt 0 ]; then
  exec "$@"
fi

exec /opt/pi-timelapse/.venv/bin/timelapse serve \
  --host "${TIMELAPSE_HOST:-0.0.0.0}" \
  --port "${TIMELAPSE_PORT:-8080}" \
  --state-dir "$STATE_DIR"
