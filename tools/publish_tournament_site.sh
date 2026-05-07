#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export TOURNAMENT_PUBLISH_REPO="${TOURNAMENT_PUBLISH_REPO:-$ROOT_DIR/public_tournament_site}"
export TOURNAMENT_PUBLISH_REMOTE="${TOURNAMENT_PUBLISH_REMOTE:-origin}"
export TOURNAMENT_PUBLISH_BRANCH="${TOURNAMENT_PUBLISH_BRANCH:-main}"

cd "$ROOT_DIR"
python tools/tournament_simulator.py
