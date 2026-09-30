#!/bin/zsh
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ROOT=/Users/kickstart/sellemy
PY=/Users/kickstart/sellemy/.venv/bin/python
GA4PY=/Users/kickstart/sellemy/.venv/bin/python
SECRETS=/Users/kickstart/ai-management-os/.env
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
export SELLEMY_WRITER_PRIMARY_COMMAND=/opt/homebrew/bin/claude
export SELLEMY_WRITER_PRIMARY_MODEL=sonnet
if [[ -n "$(git status --porcelain)" ]]; then echo "$(date -Iseconds) dirty working tree; abort"; exit 2; fi
git fetch origin main --quiet
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then echo "$(date -Iseconds) HEAD differs from origin/main; abort"; exit 3; fi
eval "$("$PY" - <<'PYADMIT'
import shlex,sys
sys.path.insert(0, '/Users/kickstart/sellemy/pipeline')
from pathlib import Path
from adaptive_publish import AdaptivePublishController
decision=AdaptivePublishController(Path('/Users/kickstart/sellemy')).admit_scheduled()
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
