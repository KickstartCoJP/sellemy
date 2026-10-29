"""Role-owned artifact work. Uses existing files and existing growth_recovery writers.

No new production artifact naming, schema, or persistence adapter is introduced.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))


def _inspect(slug: str, *, cache_only: bool) -> dict:
    from growth_role_business import GrowthRuntimeError, _reselect_products
    from comparison_acceptance import VERSION, product_issues, receipt_issues
    from growth_recovery import (load_job, read_artifact, write_artifact, set_stage,
                                 request_product_reselection)
    state = load_job(slug)
    evidence = read_artifact(slug, 'evidence.json', {}) or {}
    topic = read_artifact(slug, 'topic.json', {}) or {}
    selection = read_artifact(slug, 'selection.json', {}) or {}
    details = {'topic': topic, 'selection': selection,
               'selected_asins': [p.get('asin') for p in evidence.get('products', [])],
               'candidate_count': len(evidence.get('products', []))}
    blocker = read_artifact(slug, 'product-blocker.json', {}) or {}
    if blocker.get('needs_product_reselection'):
        acceptance = evidence.get('product_acceptance') or {}
        if len(evidence.get('products') or []) == 6 and acceptance.get('contract_version') != VERSION:
            from semantic_review import independent_review
            fresh = independent_review(evidence)
            evidence['product_acceptance'] = fresh
            issues = product_issues(evidence)
            write_artifact(slug, 'contract-revalidation.json', {
                'previous_version': acceptance.get('contract_version'),
                'current_version': VERSION,
                'result': 'BLOCKED' if issues else 'ACCEPTED',
                'issues': issues,
            })
            if not issues:
                write_artifact(slug, 'evidence.json', evidence)
                write_artifact(slug, 'product-blocker.json', {})
                set_stage(slug, 'WRITER_PENDING', event='EXISTING_PRODUCTS_REVALIDATED')
                return {**details, 'action': 'continue'}
            checks = (fresh.get('review') or {}).get('products') or []
            if len(checks) == 6 and all(isinstance(c, dict) and c.get('adequate_facts') is True
                                        and c.get('topic_fit') is True for c in checks):
                from growth_recovery import hold_product_review_conflict
                hold_product_review_conflict(slug, reasons=issues,
                    pinned_asins=[p['asin'] for p in evidence['products']])
                raise GrowthRuntimeError(f'independent review conflict held for {slug}')
        if evidence.get('product_acceptance') and not product_issues(evidence):
            write_artifact(slug, 'product-blocker.json', {})
            progress = read_artifact(slug, 'product-search-progress.json', {}) or {}
            progress['complete'] = True
            write_artifact(slug, 'product-search-progress.json', progress)
            return {**details, 'action': 'continue'}
        return {**details, 'action': 'product'}
    stage = state.get('current_stage')
    if stage in {'WRITER_PENDING', 'QA_FAILED'}:
        return {**details, 'action': 'writer'}
    if stage == 'CHAT_REVIEW_PENDING':
        return {**details, 'action': 'ceo_review'}
    if stage in {'EYECATCH_PENDING', 'PUBLISH_PENDING'}:
        issues = product_issues(evidence)
        if issues:
            request_product_reselection(slug, issues)
            raise GrowthRuntimeError(f'needs_product_reselection for {slug}')
        payload = read_artifact(slug, 'payload.json', {})
        acceptance = read_artifact(slug, 'acceptance.json')
        issues = receipt_issues(acceptance, evidence, payload)
        if issues:
            set_stage(slug, 'QA_FAILED', event='ACCEPTANCE_REVALIDATION_REQUIRED')
            write_artifact(slug, 'gate-feedback.json', {'semantic_acceptance_issues': issues})
            raise GrowthRuntimeError(f'acceptance revalidation required for {slug}')
        from independent_quality_gate import require_receipts, QualityGateError
        try:
            require_receipts(payload, evidence, acceptance,
                             read_artifact(slug, 'independent-quality-receipts.json', {}))
        except QualityGateError as exc:
            if exc.gate in ('product', 'evidence'):
                request_product_reselection(slug, exc.issues)
            else:
                set_stage(slug, 'QA_FAILED', event='INDEPENDENT_QA_REVALIDATION_REQUIRED')
                write_artifact(slug, 'gate-feedback.json', {'independent_quality_gate': {
                    'gate': exc.gate, 'issues': exc.issues}})
            raise GrowthRuntimeError(f'independent QA revalidation required for {slug}: {exc}') from exc
        return {**details, 'action': ('designer' if stage == 'EYECATCH_PENDING' else 'publish')}
    if stage == 'PUBLISHED':
        return {**details, 'action': 'published', 'commit': str(state.get('published_commit') or '')}
    raise GrowthRuntimeError(f'non-retryable recovery state for {slug}: {stage}')


def _plan(*, provided: list | None, exclude: list[str]) -> dict:
    from planning_runtime import PlanningError, discover_candidates, rank_candidates
    from analytics_feedback import load_feedback
    feedback = load_feedback()
    source = provided if provided is not None else discover_candidates()
    candidates = [c for c in rank_candidates(source, feedback)
                  if c.get('slug') not in set(exclude)]
    if not candidates:
        raise PlanningError('planning produced no novel eligible themes')
    return {'candidates': candidates,
            'provider': getattr(source, 'provider_metadata', None),
            'candidate_slugs': [c['slug'] for c in candidates]}


def _create_job(*, cache_only: bool, candidates: list, provider: dict | None,
                admission: dict | None) -> dict:
    from growth_role_business import (require_clean_current_main, select_viable_topic,
                                      select_six, build_evidence)
    from analytics_feedback import load_feedback
    from growth_recovery import create_job
    base_head = require_clean_current_main()
    feedback = load_feedback()
    topic, products, probes = select_viable_topic(candidates, feedback,
                                                cache_only=cache_only)
    topic.pop('_planning_provider_metadata', None)
    accepted_asins = topic.pop('_accepted_selection_asins', None)
    accepted_selection = topic.pop('_accepted_selection_metadata', None)
    if accepted_asins and accepted_selection:
        by_asin = {p['asin']: p for p in products}
        selected = [by_asin[a] for a in accepted_asins]
        selection = accepted_selection
    else:
        selected, selection = select_six(products, topic, feedback)
    evidence = build_evidence(topic, selected, selection)
    job = create_job(topic=topic, evidence=evidence, selection=selection,
                     planning_provider=provider, viability_probes=probes,
                     save_planning_provider=True)
    slug = job['slug']
    return {'slug': slug, 'base_head': base_head,
            'planning_provider': provider, 'viability_probes': probes,
            'topic': topic,
            'admission': admission}


def _commit_planning(slug: str, *, provider: dict | None, admission: dict | None) -> dict:
    from growth_recovery import write_artifact, record_execution
    from codex_provider import PLANNING_SURFACE, record_context_quality
    write_artifact(slug, 'planning-provider.json', provider or {})
    if admission and admission.get('allowed'):
        receipt = admission.get('receipt') or {}
        write_artifact(slug, 'scheduled-admission.json', {
            'admission_id': receipt.get('admission_id'), 'slot': admission.get('slot'),
            'admitted_at': receipt.get('admitted_at')})
    metadata = ((provider or {}).get('codex_runtime') or {})
    if metadata:
        record_execution(slug=slug, role='planning', event_type='turn',
                         stage='planning', metadata=metadata)
        record_execution(slug=slug, role='planning', event_type='success',
                         stage='planning', metadata=metadata)
        record_context_quality(ROOT, PLANNING_SURFACE, True,
                               session_id=metadata.get('session_id'))
    return {'planning_committed': True}


def _ceo_reconcile(*, published: bool) -> dict:
    from growth_role_business import _reconcile_ceo_audit_delivery
    from growth_recovery import all_jobs, read_artifact, write_artifact
    result = {}
    if published:
        try:
            from operating_profile import normal_profile
            if normal_profile():
                config = json.loads((ROOT / 'config' / 'operating-profile.json').read_text())
                if config.get('ceo_failed_ten_enabled') is True:
                    from ceo_failure_bridge import submit_failed_ten_inspection
                    for item in all_jobs():
                        slug = item.get('slug')
                        if not slug or read_artifact(slug, 'ceo-inspection-delivered.json', None):
                            continue
                        request = read_artifact(slug, 'ceo-inspection-required.json', None)
                        if request:
                            try:
                                receipt = submit_failed_ten_inspection(request)
                                write_artifact(slug, 'ceo-inspection-delivered.json', receipt)
                            except Exception as exc:
                                result.setdefault('ceo_inspection_errors', []).append(
                                    f'{slug}:{type(exc).__name__}:{exc}')
        except Exception as exc:
            result['ceo_inspection_reconcile_error'] = f'{type(exc).__name__}:{exc}'
    try:
        _reconcile_ceo_audit_delivery(result)
    except Exception as exc:
        result['postpublish_ceo_reconcile_error'] = type(exc).__name__
    if published:
        try:
            from ceo_audit_feedback import reconcile_feedback
            from pathlib import Path
            import os
            canonical = Path(os.environ.get('SELLEMY_AI_OS_CANONICAL_ROOT',
                            str(Path.home() / 'Library/Application Support/AIManagementOS/canonical')))
            result['postpublish_ceo_feedback'] = reconcile_feedback(
                canonical_root=canonical, jobs=all_jobs(),
                read_artifact=read_artifact, write_artifact=write_artifact)
        except Exception as exc:
            result['postpublish_ceo_feedback_error'] = type(exc).__name__
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=(
        'writer', 'reviewer', 'review_reset', 'product', 'inspect', 'designer', 'publish', 'postpublish', 'ceo_reconcile',
        'planning', 'planning_commit', 'fresh_create', 'already_published', 'slot_compensation', 'recovery', 'report', 'state_queue', 'scheduler'), required=True)
    parser.add_argument('--slug', default='')
    parser.add_argument('--cache-only', action='store_true')
    args = parser.parse_args()
    request = json.loads(sys.stdin.read() or '{}')
    from growth_role_business import (_reselect_products, _post_publish,
                                      _reconcile_already_published_jobs)
    result = {}
    try:
        if args.role == 'writer':
            from role_execution_writer import writer
            result = writer(args.slug)
        elif args.role == 'review_reset':
            from role_execution_writer import reset_review_artifacts
            result = reset_review_artifacts(args.slug)
        elif args.role == 'reviewer':
            from role_execution_writer import reviewer
            result = reviewer(args.slug, request.get('metadata') or {})
        elif args.role == 'product':
            _reselect_products(args.slug, cache_only=args.cache_only)
        elif args.role == 'inspect':
            result = _inspect(args.slug, cache_only=args.cache_only)
        elif args.role == 'designer':
            from role_execution_publish import designer
            result = designer(args.slug)
        elif args.role == 'publish':
            from role_execution_publish import publish
            result = publish(args.slug)
        elif args.role == 'postpublish':
            from adaptive_publish import AdaptivePublishController
            result = dict(request.get('growth_run') or {})
            result['published'] = True
            result['commit'] = request['commit']
            _post_publish(AdaptivePublishController(ROOT), result, args.slug, request['commit'])
        elif args.role == 'ceo_reconcile':
            result = _ceo_reconcile(published=request.get('publish', False))
        elif args.role == 'planning':
            result = _plan(provided=request.get('planning_candidates'),
                           exclude=request.get('exclude') or [])
        elif args.role == 'planning_commit':
            result = _commit_planning(args.slug, provider=request.get('provider'),
                                      admission=request.get('admission'))
        elif args.role == 'fresh_create':
            result = _create_job(cache_only=args.cache_only,
                                 candidates=request['candidates'],
                                 provider=request.get('provider'),
                                 admission=request.get('admission'))
        elif args.role == 'scheduler':
            from adaptive_publish import AdaptivePublishController
            scheduler = AdaptivePublishController(ROOT)
            operation = request.get('operation')
            if operation == 'current_state':
                result = scheduler.current_state()
            elif operation == 'admit_scheduled':
                result = scheduler.admit_scheduled()
            elif operation == 'validate_scheduled_admission':
                result = scheduler.validate_scheduled_admission(request['admission_id'])
            elif operation == 'reschedule_after_failure':
                result = scheduler.reschedule_after_failure(failed_slot=request['failed_slot'])
            else:
                raise ValueError(f'invalid scheduler operation: {operation}')
        elif args.role == 'state_queue':
            from growth_recovery import eligible_job, all_jobs
            due = eligible_job()
            unfinished = [job for job in all_jobs() if job.get('status') == 'ACTIVE'
                          and job.get('current_stage') not in {
                              'CHAT_REVIEW_PENDING', 'OWNER_EYECATCH_PENDING'}]
            result = {'eligible': due, 'unfinished': unfinished}
        elif args.role == 'already_published':
            result = {'slugs': _reconcile_already_published_jobs()}
        elif args.role == 'recovery':
            from growth_recovery import recover_legacy_failed_jobs
            result = {'recovered_legacy_failed': recover_legacy_failed_jobs()}
        elif args.role == 'report':
            from growth_role_business import _write_json
            path = Path(request['path']).resolve()
            _write_json(path, request['result'])
        elif args.role == 'slot_compensation':
            from growth_recovery import write_artifact
            write_artifact(args.slug, 'slot-compensation.json', request)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, default=str))
    except Exception as exc:
        if getattr(exc, 'viability_probes', None):
            result['viability_probes'] = exc.viability_probes
        print(json.dumps({'ok': False, 'error': str(exc),
                          'error_type': type(exc).__name__, 'result': result}, ensure_ascii=False))
        raise SystemExit(2) from exc


if __name__ == '__main__':
    main()
