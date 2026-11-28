#!/bin/zsh
set +x
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
GA4PY="$ROOT/.venv/bin/python"
export SELLEMY_ROOT="$ROOT"
SECRETS=/Users/kickstart/ai-management-os/.env
# OS-managed advisory lock releases even after SIGKILL; no stale directory can block production.
if [[ "${SELLEMY_GROWTH_LOCKED:-}" != "1" ]]; then
  export SELLEMY_GROWTH_LOCKED=1
  if /usr/bin/lockf -t 0 /tmp/sellemy-growth.lockfile /bin/zsh "$0" "$@"; then
    exit 0
  else
    LOCK_RC=$?
    if (( LOCK_RC == 75 )); then
      echo "$(date -Iseconds) already running"
      exit 0
    fi
    exit "$LOCK_RC"
  fi
fi
cd "$ROOT"
# A single launcher owns both admission modes. New topics remain stopped until
# the existing CEO decision explicitly enables them via the launch environment.
NEW_TOPICS_ENABLED="${SELLEMY_NEW_TOPICS_ENABLED:-0}"
if [[ "$NEW_TOPICS_ENABLED" != "0" && "$NEW_TOPICS_ENABLED" != "1" ]]; then
  echo 'invalid new-topic admission setting' >&2
  exit 1
fi
if [[ -f "$SECRETS" ]]; then set -a; source "$SECRETS"; set +a; fi
SELLEMY_SECRETS="$HOME/Library/Application Support/Sellemy/secrets/openai.env"
if [[ -f "$SELLEMY_SECRETS" ]]; then set -a; source "$SELLEMY_SECRETS"; set +a; fi
for stage in planner writer; do
  STAGE_SECRETS="$HOME/Library/Application Support/Sellemy/secrets/$stage.env"
  if [[ -f "$STAGE_SECRETS" ]]; then set -a; source "$STAGE_SECRETS"; set +a; fi
done
# Use the project associated with the existing project-scoped API key.
unset SELLEMY_OPENAI_PROJECT_ID
# The production image route is Codex-only; do not configure API fallback here.
# Normal production routing is freshly read from config/normal-provider-routing.json.
# Recovery-only overrides remain in force exclusively for an explicitly active P0 recovery policy.
SELLEMY_RECOVERY_POLICY_ACTIVE="$($PY - <<'PYPOLICY'
import json
from pathlib import Path
p=Path('config/recovery-provider-policy.json')
try:
 print('1' if json.loads(p.read_text()).get('active') is True else '0')
except Exception:
 print('1')
PYPOLICY
)"
# CEO-approved runtime: Codex only, no paid Responses API even under recovery flags.
if [[ "$SELLEMY_RECOVERY_POLICY_ACTIVE" == "1" ]]; then
  echo 'Sellemy recovery API routing is disabled: explicit CEO reauthorization required' >&2
  exit 1
fi
export SELLEMY_PLANNER_API_WEIGHT=0
export SELLEMY_PLANNER_CODEX_WEIGHT=100
export SELLEMY_WRITER_API_WEIGHT=0
export SELLEMY_WRITER_CODEX_WEIGHT=100
export SELLEMY_PLANNING_PRIMARY_COMMAND=/opt/homebrew/bin/codex
export SELLEMY_PLANNING_PRIMARY_KIND=codex
export SELLEMY_PLANNING_PRIMARY_MODEL=gpt-6-sol
export SELLEMY_WRITER_PRIMARY_COMMAND=/opt/homebrew/bin/codex
export SELLEMY_WRITER_PRIMARY_KIND=codex
export SELLEMY_WRITER_PRIMARY_MODEL=gpt-6-sol
export SELLEMY_EYECATCH_CODEX_MODEL=gpt-6-sol
# Fail closed unless all six Amazon product images pass byte-level identity audit.
export SELLEMY_REQUIRE_PRODUCT_IMAGE_AUDIT=1

# The existing launch agent supervises two independent lanes over the same jobs.
# Each lane starts its next tick without waiting for the other lane to finish.
# No second LaunchAgent, queue, or durable job store is introduced.
run_lane() {
  local lane="$1" child=0
  shift
  trap 'if (( child > 0 )); then kill "$child" 2>/dev/null || true; fi; exit 0' TERM INT HUP
  while true; do
    "$PY" pipeline/growth_runtime.py --publish --lane "$lane" "$@" &
    child=$!
    wait "$child" || true
    child=0
    /bin/sleep 60 &
    child=$!
    wait "$child" || true
    child=0
  done
}
if [[ "$NEW_TOPICS_ENABLED" == "0" ]]; then
  run_lane production --recovery-only &
else
  run_lane production --scheduled &
fi
PRODUCTION_PID=$!
run_lane publication &
PUBLICATION_PID=$!
trap 'kill "$PRODUCTION_PID" "$PUBLICATION_PID" 2>/dev/null || true; wait || true; exit 0' TERM INT HUP
wait "$PRODUCTION_PID" "$PUBLICATION_PID"
