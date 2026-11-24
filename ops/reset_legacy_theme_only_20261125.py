#!/usr/bin/env python3
"""One-time CEO-directed rollback of legacy-repair attempts, retaining theme only.

Do not modify any live site publication or unrelated runtime job. Preserves the
entire prior job as an audit snapshot before staging the new Planning attempt.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
from growth_recovery import all_jobs, job_dir, load_job, read_artifact, write_artifact, _save_state, _iso
from article_theme import product_theme

BATCH = 'ceo-theme-only-rebuild-20261125'
INVALIDATE = (
    'payload.json', 'evidence.json', 'selection.json', 'planning-provider.json',
    'viability-probes.json', 'product-search-progress.json', 'product-blocker.json',
    'product-attempt-fingerprint.json', 'product-qa.json', 'acceptance.json',
    'qa.json', 'qa-certification.json', 'review-findings.json', 'gate-feedback.json',
    'editorial-plan.json', 'editorial-plan-review.json', 'independent-quality-receipts.json',
    'claim-qa-reuse.json', 'chat-publish-approval.json', 'ceo-review-decision.json',
    'eyecatch-receipt.json', 'eyecatch.png', 'writer-blocked.json',
    'product-selection.json', 'product-acceptance.json',
)


def inventory():
    result = []
    for row in all_jobs():
        if row.get('source') != 'legacy_repair':
            continue
        if row.get('status') == 'PUBLISHED' or row.get('current_stage') == 'PUBLISHED':
            # Do not silently re-open completed publications. The current run
            # is scoped to legacy repair attempts, not original site baselines.
            raise ValueError('published legacy repair requires separate rollback: ' + row['slug'])
        slug = row['slug']
        topic = read_artifact(slug, 'topic.json', {}) or {}
        title = (read_artifact(slug, 'payload.json', {}) or {}).get('h1') or topic.get('title')
        theme = product_theme({**topic, 'title': title})
        if not theme or len(theme) > 55:
            raise ValueError('invalid retained theme: ' + slug)
        result.append((row, theme))
    return sorted(result, key=lambda entry: entry[0]['slug'])


def reset(rows):
    # Back up every selected job in full, before touching any active state.
    snapshots = []
    for row, theme in rows:
        folder = job_dir(row['slug'])
        backup = folder / ('rollback-' + BATCH)
        if backup.exists():
            if not (backup / 'state.json').is_file():
                raise ValueError('incomplete backup: ' + row['slug'])
        else:
            temp = folder / ('.rollback-' + BATCH + '-building')
            if temp.exists():
                shutil.rmtree(temp)
            temp.mkdir()
            for file in folder.iterdir():
                if file.is_file():
                    shutil.copy2(file, temp / file.name)
            if not (temp / 'state.json').exists():
                raise ValueError('missing backup state: ' + row['slug'])
            os.replace(temp, backup)
        snapshots.append((row, theme, backup))
    changed = []
    for original, theme, backup in snapshots:
        slug = original['slug']
        existing = read_artifact(slug, 'legacy-theme-only-rebuild.json', {}) or {}
        if existing.get('batch') == BATCH:
            changed.append(slug)
            continue
        state = load_job(slug)
        if state.get('source') != 'legacy_repair':
            raise ValueError('source changed while resetting: ' + slug)
        for filename in INVALIDATE:
            file = job_dir(slug) / filename
            if file.exists():
                file.unlink()
        write_artifact(slug, 'topic.json', {
            'slug': slug, 'category': state['category'], 'product_theme': theme,
        })
        write_artifact(slug, 'product-search-progress.json', {
            'candidates': [], 'excluded_asins': [], 'queries': [], 'reviewed_sets': [],
            'researched_asins': [], 'source_backfill_attempted': [],
            'complete': False, 'reuse_previous_products': False,
        })
        prior_marker = read_artifact(slug, 'legacy-repair.json', {}) or {}
        write_artifact(slug, 'legacy-repair.json', {
            **prior_marker, 'phase': 'planning_required',
            'adapter_revision': 'shared-product-v1',
        })
        write_artifact(slug, 'legacy-theme-only-rebuild.json', {
            'batch': BATCH, 'theme': theme, 'first_draft_completed': False,
            'prior_stage': original.get('current_stage'),
            'prior_status': original.get('status'),
            'audit_snapshot': backup.name,
        })
        state.update(status='ACTIVE', current_stage='PLANNING_PENDING', next_retry_at=_iso(),
                     last_failure_class=None, last_failure_code=None,
                     last_failure_reason=None, consecutive_quality_failures=0,
                     retry_count_by_stage={'writer':0,'product':0,'publish':0,'eyecatch':0},
                     role_returns={'planning':0,'writer':0,'designer':0},
                     writer_session_id=None, writer_turn_ids=[],
                     designer_session_id=None, designer_turn_ids=[])
        _save_state(state, event='CEO_LEGACY_THEME_ONLY_FULL_REBUILD',
                    detail={'batch':BATCH,'retained_theme':theme,
                            'audit_snapshot':backup.name})
        changed.append(slug)
    return changed


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--expected-count',type=int,default=279)
    args=parser.parse_args()
    rows=inventory()
    if len(rows)!=args.expected_count:
        raise ValueError(f'cohort changed: {len(rows)} vs expected {args.expected_count}')
    print(json.dumps({'cohort':len(rows),'by_stage':{
        stage:sum(row['current_stage']==stage for row,_ in rows)
        for stage in sorted({row['current_stage'] for row,_ in rows})},
        'published_repair':sum(row['status']=='PUBLISHED' for row,_ in rows)},ensure_ascii=False))
    if args.apply:
        changed=reset(rows)
        print(json.dumps({'reset':len(changed),'batch':BATCH},ensure_ascii=False))

if __name__=='__main__':main()
