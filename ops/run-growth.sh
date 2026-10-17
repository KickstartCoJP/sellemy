#!/bin/zsh
set +x
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
GA4PY="$ROOT/.venv/bin/python"
export SELLEMY_ROOT="$ROOT"
SECRETS=/Users/kickstart/ai-management-os/.env
LOCK=/tmp/sellemy-growth.lock
if ! mkdir "$LOCK" 2>/dev/null; then echo "$(date -Iseconds) already running"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT
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
# Recovery fast-path: while durable unfinished article jobs exist, skip acquisition
# refresh/admission overhead and spend this invocation directly on the oldest eligible
# recovery item. Once the queue is empty, normal scheduled operation resumes below.
RECOVERY_PENDING="$("$PY" - <<'PYRECOVERY'
import sys
from pathlib import Path
root=Path(__import__('os').environ['SELLEMY_ROOT'])
sys.path.insert(0, str(root/'pipeline'))
from growth_recovery import all_jobs
pending=any(r.get('status') not in {'PUBLISHED','DISCARDED','FAILED'} for r in all_jobs())
print('1' if pending else '0')
PYRECOVERY
)"
if [[ "$RECOVERY_PENDING" == "1" ]]; then
  "$PY" pipeline/growth_runtime.py --publish
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
COLLECT_DUE="$("$GA4PY" - "$QUALITY_DIR/acquisition-attempt.json" <<'PYDUE'
import json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
path=Path(sys.argv[1])
if not path.exists():
    print('1')
else:
    try:
        value=json.loads(path.read_text())
        attempted=datetime.fromisoformat(value['attempted_at'])
        assert attempted.tzinfo is not None
        assert all(type(value[p]['collector_rc']) is int for p in ('amazon','rakuten'))
        age=datetime.now(timezone.utc)-attempted
        print('1' if timedelta(hours=1 if any(value[p]['collector_rc'] != 0 for p in ('amazon','rakuten')) else 6) <= age <= timedelta(days=3650) else '0')
    except (ValueError,TypeError,KeyError,AssertionError,OverflowError):
        print('0')  # Invalid state must not trigger another browser run.
PYDUE
)"
if [[ "$COLLECT_DUE" == "1" ]]; then
  COLLECT_FROM="$(date -v-7d +%F)"
  COLLECT_TO="$(date +%F)"
  AMAZON_COLLECT_RC=0
  "$PY" pipeline/run_with_timeout.py 120 "$GA4PY" pipeline/firefox_collectors.py --provider amazon --start-date "$COLLECT_FROM" --end-date "$COLLECT_TO" || AMAZON_COLLECT_RC=$?
  RAKUTEN_COLLECT_RC=0
  "$PY" pipeline/run_with_timeout.py 120 "$GA4PY" pipeline/firefox_collectors.py --provider rakuten --start-date "$COLLECT_FROM" --end-date "$COLLECT_TO" || RAKUTEN_COLLECT_RC=$?
  "$GA4PY" - "$AMAZON_COLLECT_RC" "$RAKUTEN_COLLECT_RC" "$QUALITY_DIR" <<'PYQ'
import json,os,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
a,r,directory=int(sys.argv[1]),int(sys.argv[2]),Path(sys.argv[3])
now=datetime.now(timezone.utc).isoformat()
for name,value in (
    ('acquisition-quality.json',{"updated_at":now,"amazon":{"state":"available" if a==0 else "degraded","collector_rc":a},"rakuten":{"state":"available" if r==0 else "degraded","collector_rc":r}}),
    ('acquisition-attempt.json',{"attempted_at":now,"amazon":{"collector_rc":a},"rakuten":{"collector_rc":r}}),
):
    with tempfile.NamedTemporaryFile(mode='w',dir=directory,delete=False) as f:
        json.dump(value,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write('\n')
        tmp=f.name
    os.replace(tmp,directory/name)
PYQ
  if (( AMAZON_COLLECT_RC != 0 || RAKUTEN_COLLECT_RC != 0 )); then
    echo "$(date -Iseconds) affiliate browser collectors degraded; continuing with last verified raw/curated data"
  fi
fi
AFFILIATE_ARGS=(--mode daily)
if [[ -r "$HOME/.config/valuecommerce/report-api.json" ]]; then
  AFFILIATE_ARGS+=(--fetch)
fi
"$GA4PY" pipeline/affiliate_pipeline.py "${AFFILIATE_ARGS[@]}"
"$GA4PY" pipeline/actuals_adapter.py --sync
GROWTH_RC=0
if [[ "$PUBLISH_ALLOWED" == "1" ]]; then
  "$PY" pipeline/growth_runtime.py --publish --scheduled || GROWTH_RC=$?
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
