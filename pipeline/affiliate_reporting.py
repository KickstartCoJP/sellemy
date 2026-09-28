from __future__ import annotations

"""Deprecated compatibility entry point.

Production collection, reconciliation and lineage live in affiliate_pipeline.py.
The former Creators API probe and manual-import workflow were removed because
neither is a verified production source for Sellemy revenue.
"""

import argparse
import json
from pathlib import Path

from affiliate_pipeline import DB_PATH, connect, reconcile, run, stamp

STATE_PATH = DB_PATH.parent / "affiliate_source_state.json"


def refresh_state() -> dict:
    db = connect(DB_PATH)
    summary = reconcile(db)
    providers = [dict(row) for row in db.execute(
        "SELECT * FROM provider_watermarks ORDER BY provider"
    )]
    db.close()
    state = {
        "generated_at": stamp(),
        "production_pipeline": "pipeline/affiliate_pipeline.py",
        "providers": providers,
        "reconcile": summary,
        "creators_api_enabled": False,
        "manual_import_enabled": False,
        "revenue_actual_ready": any(x["state"] == "available" for x in providers),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(STATE_PATH.read_text(encoding="utf-8")) != state:
        raise OSError("affiliate source state read-back mismatch")
    return state


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--import-provider")
    p.add_argument("--import-file", type=Path)
    a = p.parse_args()
    if a.import_provider or a.import_file:
        raise SystemExit("manual import path removed; use provider production collectors")
    if a.refresh or not (a.import_provider or a.import_file):
        run("daily")
        print(json.dumps(refresh_state(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
