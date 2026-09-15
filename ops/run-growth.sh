#!/bin/zsh
set -euo pipefail
ROOT=/Users/suzukitakayuki/sellemy
PY=/Users/suzukitakayuki/autosite/.venv/bin/python
LOCK=/tmp/sellemy-growth.lock
if ! mkdir "$LOCK" 2>/dev/null; then echo "$(date -Iseconds) already running"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT
cd "$ROOT"
if [[ -n "$(git status --porcelain)" ]]; then echo "$(date -Iseconds) dirty working tree; abort"; exit 2; fi
git fetch origin main --quiet
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then echo "$(date -Iseconds) HEAD differs from origin/main; abort"; exit 3; fi
"$PY" pipeline/growth_runtime.py --publish
