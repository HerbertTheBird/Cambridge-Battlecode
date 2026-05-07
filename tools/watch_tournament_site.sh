#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTERVAL_SECONDS="${TOURNAMENT_WATCH_INTERVAL_SECONDS:-600}"
LOG_FILE="${TOURNAMENT_WATCH_LOG:-$ROOT_DIR/public_tournament_site/publisher.log}"

mkdir -p "$(dirname "$LOG_FILE")"

while true; do
  {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting tournament publish run"
    "$ROOT_DIR/tools/publish_tournament_site.sh"
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] finished tournament publish run"
  } >> "$LOG_FILE" 2>&1 || {
    status=$?
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] tournament publish failed with status $status" >> "$LOG_FILE"
  }

  sleep "$INTERVAL_SECONDS"
done
