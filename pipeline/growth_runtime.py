"""Sellemy progress coordinator.

Business-artifact reads, writes, creation, review and publication occur in
separate role processes. The coordinator handles only scheduling and job state.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from growth_recovery import load_job, discard
from publish_gate import require_publishing_enabled
from role_stage_client import execute_role, RoleStageFailure

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'data' / 'growth_last_run.json'


class GrowthRuntimeError(RuntimeError):
    pass


def _execute_job(slug: str, result: dict, *, cache_only: bool = False) -> str | None:
    # Do not inspect, read, or write business artifacts here; each Role does it.
    while True:
        state = load_job(slug)
        result['recovery_state'] = state
        try:
            inspected = execute_role('inspect', slug, cache_only=cache_only)
            for key in ('topic', 'selection', 'selected_asins', 'candidate_count'):
                if key in inspected:
                    result[key] = inspected[key]
            action = inspected['action']
            if action == 'continue':
                continue
            if action == 'product':
                execute_role('product', slug, cache_only=cache_only)
                continue
            if action == 'writer':
                reset = execute_role('review_reset', slug)
                if reset.get('review_authorizations_revoked') is not True:
                    raise GrowthRuntimeError('review gate reset did not acknowledge revocation')
                writer_receipt = execute_role('writer', slug)
                if writer_receipt.get('writer_ready') is not True:
                    raise GrowthRuntimeError('writer omitted ready receipt')
                reviewer_receipt = execute_role('reviewer', slug, request={
                    'metadata': writer_receipt.get('metadata') or {}
                })
                if reviewer_receipt.get('review_passed') is not True:
                    raise GrowthRuntimeError('reviewer omitted pass receipt')
                continue
            if action == 'ceo_review':
                result['status'] = 'awaiting_chatgpt_final_review'
                result['published'] = False
                return None
            if action == 'designer':
                result.update(execute_role('designer', slug))
                continue
            if action == 'publish':
                receipt = execute_role('publish', slug)
                result.update(receipt)
                return str(receipt['commit'])
            if action == 'published':
                return str(inspected['commit'])
            raise GrowthRuntimeError(f'unknown Role stage action: {action}')
        except RoleStageFailure as exc:
            raise GrowthRuntimeError(str(exc)) from exc

def run(*, publish: bool, cache_only: bool = False, report_path: Path | None = REPORT,
        planning_candidates: list[dict] | None = None, scheduled: bool = False,
        controller: object | None = None,
        recovery_only: bool = False) -> dict:
    result: dict = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'route': ['recovery_queue', 'continuous_planning', 'product_selection',
                  'writer', 'local_qa', 'machine_qa', 'chatgpt_final_review',
                  'eyecatch', 'publish'],
        'published': False,
    }
    if recovery_only and (not publish or scheduled):
        raise ValueError('recovery-only requires publish without scheduled admission')
    if publish:
        require_publishing_enabled(ROOT)
    try:
        result.update(execute_role('ceo_reconcile', request={'publish': publish}))
    except RoleStageFailure as exc:
        result['ceo_reconcile_error'] = str(exc)
    if controller is not None:
        raise ValueError('Runtime cannot execute an in-process controller with artifact access')
    try:
        controller_state = execute_role('scheduler', request={'operation': 'current_state'})
        result['controller_before'] = controller_state
        if publish and controller_state['publish_paused']:
            result['status'] = 'paused_by_controller'
            return result
        if publish and scheduled:
            claimed = os.environ.get('SELLEMY_PRECLAIMED_ADMISSION_ID', '').strip()
            decision = (execute_role('scheduler', request={
                            'operation': 'validate_scheduled_admission', 'admission_id': claimed}) if claimed
                        else execute_role('scheduler', request={'operation': 'admit_scheduled'}))
            result['controller_decision'] = decision
            if not decision['allowed']:
                result['status'] = 'skipped_by_controller'
                return result
        if publish:
            result.update(execute_role('recovery'))
            reconciled = execute_role('already_published').get('slugs', [])
            if reconciled:
                result['reconciled_already_published'] = reconciled
        else:
            result['recovered_legacy_failed'] = []

        def create_fresh() -> str:
            excluded: set[str] = set()
            while True:
                planned = execute_role('planning', request={
                    'planning_candidates': planning_candidates, 'exclude': sorted(excluded)})
                try:
                    created = execute_role('fresh_create', cache_only=cache_only,
                        request={'candidates': planned['candidates'],
                                 'provider': planned.get('provider'),
                                 'admission': result.get('controller_decision') if scheduled else None})
                except RoleStageFailure as exc:
                    probes = exc.details.get('viability_probes') or []
                    result.setdefault('viability_probes', []).extend(probes)
                    if planning_candidates is not None or not probes:
                        raise
                    newly = {row.get('slug') for row in probes if row.get('slug')}
                    if not (newly - excluded):
                        raise
                    excluded.update(newly)
                    continue
                result.update({key: value for key, value in created.items() if key != 'slug'})
                execute_role('planning_commit', created['slug'], request={
                    'provider': planned.get('provider'),
                    'admission': result.get('controller_decision') if scheduled else None})
                return created['slug']

        queue = execute_role('state_queue')
        recovery = None if scheduled and not recovery_only else queue.get('eligible')
        unfinished = queue.get('unfinished') or []
        if recovery:
            slug = recovery['slug']
            result.update({'recovery_mode': True, 'recovery_slug': slug})
        else:
            if recovery_only:
                result['status'] = 'recovery_no_eligible_job'
                return result
            if unfinished:
                result['recovery_waiting'] = [
                    {'slug': row['slug'], 'stage': row.get('current_stage'),
                     'next_retry_at': row.get('next_retry_at')}
                    for row in sorted(unfinished, key=lambda x: x.get('created_at') or '')[:20]]
            result['recovery_mode'] = False
            slug = create_fresh()
        if not publish:
            result['status'] = 'recovery_staged' if result['recovery_mode'] else 'staged'
            return result
        try:
            commit = _execute_job(slug, result, cache_only=cache_only)
        except GrowthRuntimeError as exc:
            if result.get('recovery_mode') and str(exc).startswith('planning_restart_required'):
                discard(slug, 'Automatically returned to Planning after exhausting product options')
                result['replanned_from'] = slug
                if recovery_only:
                    result['status'] = 'planning_restart_queued'
                    return result
                result['recovery_mode'] = False
                slug = create_fresh()
                commit = _execute_job(slug, result, cache_only=cache_only)
            else:
                state = load_job(slug)
                deferred = (
                    result.get('recovery_mode') is True
                    and str(exc).startswith(('product discovery still in progress',
                                            'needs_product_reselection'))
                    and state.get('current_stage') in {'WRITER_PENDING', 'PRODUCT_HOLD'}
                    and (state.get('next_retry_at') is None or
                         datetime.fromisoformat(state['next_retry_at']) > datetime.now(timezone.utc))
                )
                if not deferred:
                    raise
                result['skipped_recovery'] = {'slug': slug, 'reason': str(exc),
                                              'next_retry_at': state.get('next_retry_at')}
                if recovery_only:
                    result['status'] = 'recovery_deferred'
                    return result
                result['recovery_mode'] = False
                slug = create_fresh()
                commit = _execute_job(slug, result, cache_only=cache_only)
        if commit is None and result.get('status') == 'awaiting_chatgpt_final_review':
            return result
        result.update({'commit': commit, 'published': True, 'status': 'published'})
        try:
            result.update(execute_role('postpublish', slug, request={'commit': commit, 'growth_run': result}))
        except RoleStageFailure as exc:
            result['status'] = 'published_feedback_failed'
            raise GrowthRuntimeError(f'post-publish role failed: {exc}') from exc
        return result
    except Exception as exc:
        result['status'] = ('published_feedback_failed' if result.get('published')
                            else 'blocked_before_publish')
        result['blocker'] = f'{type(exc).__name__}: {exc}'
        if hasattr(exc, 'viability_probes'):
            result['viability_probes'] = exc.viability_probes
        decision = result.get('controller_decision') or {}
        if publish and scheduled and not result.get('published') and decision.get('allowed'):
            try:
                result['recovery_schedule'] = execute_role('scheduler', request={
                    'operation': 'reschedule_after_failure', 'failed_slot': decision['slot']})
                if 'slug' in locals():
                    execute_role('slot_compensation', slug, request={
                        'reason': 'scheduled_runtime_failure',
                        'failed_slot': decision['slot'],
                        'schedule': result['recovery_schedule'],
                    })
            except Exception as recovery_error:
                result['recovery_schedule_error'] = f'{type(recovery_error).__name__}: {recovery_error}'
        raise
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        if report_path is not None:
            execute_role('report', request={'path': str(report_path), 'result': result})


def main() -> None:
    import json
    parser = argparse.ArgumentParser(description='Sellemy progress-only coordinator')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--cache-only', action='store_true')
    parser.add_argument('--scheduled', action='store_true')
    parser.add_argument('--recovery-only', action='store_true')
    args = parser.parse_args()
    try:
        result = run(publish=args.publish, cache_only=args.cache_only,
                     scheduled=args.scheduled, recovery_only=args.recovery_only)
    except Exception as exc:
        print(json.dumps({'status': 'blocked_before_publish',
                          'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
