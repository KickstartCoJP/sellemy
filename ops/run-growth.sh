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
# Bounded original-Writer prose revision before independent QA for measurable length misses.
export SELLEMY_ENABLE_WRITER_LENGTH_REPAIR=1

# The publication switch does not disable analytics, monitoring or site serving.
PUBLISH_ALLOWED="$("$PY" - <<'PYSWITCH'
import sys
from pathlib import Path
sys.path.insert(0, 'pipeline')
from publish_gate import require_publishing_enabled, PublishGateError
try:
    require_publishing_enabled(Path.cwd())
except PublishGateError:
    print('0')
else:
    print('1')
PYSWITCH
)"
if [[ "$PUBLISH_ALLOWED" == "1" ]]; then
# Keep fast recovery, but only for a job eligible NOW. Backoff/HOLD is not
# pending executable work and must not prevent normal planning admission.
RECOVERY_DUE="$($PY - <<'PYRECOVERY'
import sys
sys.path.insert(0, 'pipeline')
from growth_recovery import eligible_job
print('1' if eligible_job() else '0')
PYRECOVERY
)"
if [[ "$RECOVERY_DUE" == "1" ]]; then
  RECOVERY_RC=0
  "$PY" pipeline/run_with_timeout.py 900 "$PY" pipeline/growth_runtime.py --publish --recovery-only || RECOVERY_RC=$?
  exit "$RECOVERY_RC"
fi
"$PY" - <<'PYSYNC'
import sys
import os
from pathlib import Path
root=Path(os.environ['SELLEMY_ROOT'])
sys.path.insert(0, str(root/'pipeline'))
from publish_gate import sync_clean_main
print('growth preflight sync:', sync_clean_main(root))
PYSYNC
eval "$("$PY" - <<'PYADMIT'
import shlex,sys
import os
from pathlib import Path
root=Path(os.environ['SELLEMY_ROOT'])
sys.path.insert(0, str(root/'pipeline'))
from adaptive_publish import AdaptivePublishController
decision=AdaptivePublishController(root).admit_scheduled()
print('SELLEMY_PREFLIGHT_ALLOWED=' + ('1' if decision.get('allowed') else '0'))
print('SELLEMY_PREFLIGHT_REASON=' + shlex.quote(str(decision.get('reason') or '')))
if decision.get('allowed'):
    print('SELLEMY_PRECLAIMED_ADMISSION_ID=' + shlex.quote(decision['receipt']['admission_id']))
PYADMIT
)"
if [[ "$SELLEMY_PREFLIGHT_ALLOWED" != "1" ]]; then
  echo "$(date -Iseconds) growth preflight skipped: $SELLEMY_PREFLIGHT_REASON"
  exit 0
fi
export SELLEMY_PRECLAIMED_ADMISSION_ID
fi # publication-only recovery, git sync and slot admission
GROWTH_RC=0
if [[ "$PUBLISH_ALLOWED" == "1" ]]; then
  "$PY" pipeline/run_with_timeout.py 900 "$PY" pipeline/growth_runtime.py --publish --scheduled || GROWTH_RC=$?
else
  echo "$(date -Iseconds) global publishing kill switch disabled; metrics remain enabled"
fi
exit "$GROWTH_RC"
