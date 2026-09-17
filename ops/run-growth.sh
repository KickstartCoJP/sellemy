#!/bin/zsh
set -euo pipefail
ROOT=/Users/suzukitakayuki/sellemy
PY=/Users/suzukitakayuki/autosite/.venv/bin/python
GA4PY=/Users/suzukitakayuki/sellemy/.venv/bin/python
SECRETS=/Users/suzukitakayuki/ai-management-os/.env
LOCK=/tmp/sellemy-growth.lock
if ! mkdir "$LOCK" 2>/dev/null; then echo "$(date -Iseconds) already running"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT
cd "$ROOT"
if [[ -f "$SECRETS" ]]; then set -a; source "$SECRETS"; set +a; fi
export SELLEMY_EYECATCH_ENDPOINT=https://api.openai.com/v1/images/generations
export SELLEMY_EYECATCH_TOKEN_ENV=OPENAI_API_KEY
export SELLEMY_EYECATCH_PROVIDER=openai
export SELLEMY_EYECATCH_MODEL=gpt-image-2.5-sunburst
export SELLEMY_EYECATCH_QUALITY=high
export SELLEMY_EYECATCH_TIMEOUT_SECONDS=300
if [[ ! -x "$GA4PY" ]]; then echo "$(date -Iseconds) GA4 venv missing; abort"; exit 4; fi
"$GA4PY" pipeline/ga4_sync.py --days 28
if [[ -n "$(git status --porcelain)" ]]; then echo "$(date -Iseconds) dirty working tree; abort"; exit 2; fi
git fetch origin main --quiet
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then echo "$(date -Iseconds) HEAD differs from origin/main; abort"; exit 3; fi
"$PY" pipeline/growth_runtime.py --publish --scheduled
