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
export SELLEMY_PLANNER_API_WEIGHT=100
export SELLEMY_PLANNER_CODEX_WEIGHT=0
export SELLEMY_WRITER_API_WEIGHT=100
export SELLEMY_WRITER_CODEX_WEIGHT=0
export SELLEMY_PLANNER_OPENAI_MODEL=gpt-6.1-sol
export SELLEMY_WRITER_OPENAI_MODEL=gpt-6.1-sol
export SELLEMY_PLANNER_OPENAI_ESCALATION_MODEL=gpt-6-astra
export SELLEMY_WRITER_OPENAI_ESCALATION_MODEL=gpt-6-astra
# Use the project associated with the existing project-scoped API key.
unset SELLEMY_OPENAI_PROJECT_ID
if [[ "${SELLEMY_OPENAI_API_KEY:-}" != sk-* ]]; then
  echo "$(date -Iseconds) ERROR: Sellemy Designer API credential unavailable; selected API route will be recorded as failed and may fall back to Codex for publication continuity" >&2
fi
export SELLEMY_EYECATCH_ENDPOINT=https://api.openai.com/v1/images/generations
export SELLEMY_EYECATCH_TOKEN_ENV=SELLEMY_OPENAI_API_KEY
export SELLEMY_EYECATCH_PROVIDER=openai
export SELLEMY_EYECATCH_MODEL=gpt-image-2.5-sunburst
export SELLEMY_EYECATCH_QUALITY=high
export SELLEMY_EYECATCH_TIMEOUT_SECONDS=300
# Planning / Writer: Responses API is selected independently above; Codex is the
# first fallback and Claude is the
# certified local fallback. Browser/Work fallback is intentionally not in this route.
export SELLEMY_WRITER_PRIMARY_COMMAND=/opt/homebrew/bin/codex
export SELLEMY_WRITER_PRIMARY_KIND=codex
export SELLEMY_WRITER_PRIMARY_MODEL=gpt-6-astra
export SELLEMY_WRITER_SECONDARY_COMMAND=/opt/homebrew/bin/claude
export SELLEMY_WRITER_SECONDARY_KIND=claude
export SELLEMY_WRITER_SECONDARY_MODEL=sonnet
export SELLEMY_WRITER_SECONDARY_CERTIFIED=true
export SELLEMY_PLANNING_PRIMARY_COMMAND=/opt/homebrew/bin/codex
export SELLEMY_PLANNING_PRIMARY_KIND=codex
export SELLEMY_PLANNING_PRIMARY_MODEL=gpt-6-astra
export SELLEMY_PLANNING_SECONDARY_COMMAND=/opt/homebrew/bin/claude
export SELLEMY_PLANNING_SECONDARY_KIND=claude
export SELLEMY_PLANNING_SECONDARY_MODEL=sonnet
export SELLEMY_PLANNING_SECONDARY_CERTIFIED=true
export SELLEMY_CODEX_BRIEF_CHECKPOINT_TURNS=10
export SELLEMY_CODEX_REASONING_EFFORT=low
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
if [[ ! -x "$GA4PY" ]]; then echo "$(date -Iseconds) GA4 venv missing; abort"; exit 4; fi
"$GA4PY" pipeline/ga4_sync.py --days 28
COLLECT_FROM="$(date -v-7d +%F)"
COLLECT_TO="$(date +%F)"
AMAZON_COLLECT_RC=0
"$PY" pipeline/run_with_timeout.py 120 "$GA4PY" pipeline/firefox_collectors.py --provider amazon --start-date "$COLLECT_FROM" --end-date "$COLLECT_TO" || AMAZON_COLLECT_RC=$?
RAKUTEN_COLLECT_RC=0
"$PY" pipeline/run_with_timeout.py 120 "$GA4PY" pipeline/firefox_collectors.py --provider rakuten --start-date "$COLLECT_FROM" --end-date "$COLLECT_TO" || RAKUTEN_COLLECT_RC=$?
QUALITY_DIR="$HOME/Library/Application Support/Sellemy/analytics"
mkdir -p "$QUALITY_DIR"
"$GA4PY" - "$AMAZON_COLLECT_RC" "$RAKUTEN_COLLECT_RC" "$QUALITY_DIR/acquisition-quality.json" <<'PYQ'
import json,sys
from datetime import datetime,timezone
a,r,path=int(sys.argv[1]),int(sys.argv[2]),sys.argv[3]
value={"updated_at":datetime.now(timezone.utc).isoformat(),"amazon":{"state":"available" if a==0 else "degraded","collector_rc":a},"rakuten":{"state":"available" if r==0 else "degraded","collector_rc":r}}
open(path,"w").write(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
PYQ
AFFILIATE_ARGS=(--mode daily)
if [[ -r "$HOME/.config/valuecommerce/report-api.json" ]]; then
  AFFILIATE_ARGS+=(--fetch)
fi
"$GA4PY" pipeline/affiliate_pipeline.py "${AFFILIATE_ARGS[@]}"
"$GA4PY" pipeline/actuals_adapter.py --sync
if (( AMAZON_COLLECT_RC != 0 || RAKUTEN_COLLECT_RC != 0 )); then
  echo "$(date -Iseconds) affiliate browser collectors degraded; continuing with last verified raw/curated data"
fi
GROWTH_RC=0
"$PY" pipeline/growth_runtime.py --publish --scheduled || GROWTH_RC=$?
MONITOR_RC=0
"$PY" pipeline/article_publication_metrics.py >/dev/null || MONITOR_RC=$?
"$GA4PY" pipeline/actuals_adapter.py --sync || MONITOR_RC=$?
if (( MONITOR_RC != 0 )); then
  echo "$(date -Iseconds) publication monitoring refresh failed; publication outcome preserved"
fi
exit "$GROWTH_RC"
