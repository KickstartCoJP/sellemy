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
  "$PY" pipeline/run_with_timeout.py 900 "$PY" pipeline/growth_runtime.py --publish
  "$PY" pipeline/article_publication_metrics.py >/dev/null || true
  "$GA4PY" pipeline/actuals_adapter.py --sync || true
  exit 0
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
if [[ ! -x "$GA4PY" ]]; then echo "$(date -Iseconds) GA4 venv missing; abort"; exit 4; fi
"$GA4PY" pipeline/ga4_sync.py --days 28
QUALITY_DIR="$HOME/Library/Application Support/Sellemy/analytics"
mkdir -p "$QUALITY_DIR"
COLLECT_FROM="$(date -v-7d +%F)"
COLLECT_TO="$(date +%F)"
# Each provider has its own attempt clock. A failed provider retries hourly,
# while successful collections are refreshed every six hours.
for PROVIDER in amazon rakuten; do
  PROVIDER_DUE="$("$GA4PY" - "$QUALITY_DIR/acquisition-attempt.json" "$PROVIDER" <<'PYDUE'
import json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
path,provider=Path(sys.argv[1]),sys.argv[2]
if not path.exists():
 print('1')
else:
 try:
  value=json.loads(path.read_text())
  if provider not in value:
   print('1');sys.exit(0)
  record=value[provider]
  attempted=datetime.fromisoformat(record.get('attempted_at') or value['attempted_at'])
  assert attempted.tzinfo is not None
  assert type(record['collector_rc']) is int
  age=datetime.now(timezone.utc)-attempted
  due=timedelta(hours=1 if record['collector_rc'] else 6)
  print('1' if due<=age<=timedelta(days=3650) else '0')
 except (ValueError,TypeError,KeyError,AssertionError,OverflowError):
  print('0')
PYDUE
)"
  if [[ "$PROVIDER_DUE" != "1" ]]; then continue; fi
  COLLECT_RC=0
  "$PY" pipeline/run_with_timeout.py 120 "$GA4PY" pipeline/firefox_collectors.py --provider "$PROVIDER" --start-date "$COLLECT_FROM" --end-date "$COLLECT_TO" || COLLECT_RC=$?
  "$GA4PY" - "$COLLECT_RC" "$PROVIDER" "$QUALITY_DIR" <<'PYQ'
import json,os,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
rc,provider,directory=int(sys.argv[1]),sys.argv[2],Path(sys.argv[3])
now=datetime.now(timezone.utc).isoformat()
for name in ('acquisition-quality.json','acquisition-attempt.json'):
 path=directory/name
 try: value=json.loads(path.read_text())
 except (OSError,ValueError): value={}
 if not isinstance(value,dict): value={}
 value['updated_at' if name=='acquisition-quality.json' else 'attempted_at']=now
 record={'collector_rc':rc}
 if name=='acquisition-quality.json':record['state']='available' if rc==0 else 'degraded'
 else:record['attempted_at']=now
 value[provider]=record
 with tempfile.NamedTemporaryFile(mode='w',dir=directory,delete=False) as f:
  json.dump(value,f,ensure_ascii=False,indent=2,sort_keys=True)
  f.write('\n');tmp=f.name
 os.replace(tmp,path)
PYQ
  if (( COLLECT_RC != 0 )); then
    echo "$(date -Iseconds) $PROVIDER affiliate collector degraded; retry in one hour without blocking the other provider"
  fi
done
AFFILIATE_ARGS=(--mode daily)
if [[ -r "$HOME/.config/valuecommerce/report-api.json" ]]; then
  AFFILIATE_ARGS+=(--fetch)
fi
"$GA4PY" pipeline/affiliate_pipeline.py "${AFFILIATE_ARGS[@]}"
"$GA4PY" pipeline/actuals_adapter.py --sync
GROWTH_RC=0
if [[ "$PUBLISH_ALLOWED" == "1" ]]; then
  "$PY" pipeline/run_with_timeout.py 900 "$PY" pipeline/growth_runtime.py --publish --scheduled || GROWTH_RC=$?
else
  echo "$(date -Iseconds) global publishing kill switch disabled; metrics remain enabled"
fi
MONITOR_RC=0
"$PY" pipeline/article_publication_metrics.py >/dev/null || MONITOR_RC=$?
"$GA4PY" pipeline/actuals_adapter.py --sync || MONITOR_RC=$?
if (( MONITOR_RC != 0 )); then
  echo "$(date -Iseconds) publication monitoring refresh failed; publication outcome preserved"
fi
exit "$GROWTH_RC"
