from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import requests
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from analytics_feedback import load_feedback
from affiliate_config import AMAZON_TRACKING_ID
from adaptive_publish import AdaptivePublishController
from discovery_adapters import AutositeDiscoveryAdapter
from feedback_task_bridge import sync_feedback_to_task_event
from eyecatch_router import EyecatchRoutingError, ensure_routed_eyecatch
from codex_provider import DESIGNER_SURFACE, PLANNING_SURFACE, context_session_id, record_context_quality
from payload_schema import validate_evidence, validate_payload, match_refs
from planning_runtime import PlanningError, discover_candidates, rank_candidates
from publish_gate import (
    PublishGate, PublishGateError, recover_unpublished_commit_after_remote_race, sync_clean_main,
)
from purpose_relation_discovery import run as run_purpose_relation_discovery
from product_selection import dedupe_content_products, select_six
from publish_payload import run as publish_payload
from production_deploy import verify as verify_production_deploy, ProductionDeployError
from qa import run_qa
from renderer import render_article
from review_gate import run_review_gate
from writer_runtime import WriterInvocationError, invoke_writer
from growth_recovery import (
    all_jobs, create_job, eligible_job, job_dir, load_job, mark_failure, mark_published,
    materialize_for_publish, read_artifact, record_execution, save_eyecatch,
    save_writer_result, set_stage, WRITER_MAX_ATTEMPTS,
)

REPORT = ROOT / 'data' / 'growth_last_run.json'
BASE = 'https://www.sellemy.jp'


class GrowthRuntimeError(RuntimeError):
    pass


class PublishRaceError(GrowthRuntimeError):
    def __init__(self, message: str, *, candidate_commit: str):
        super().__init__(message)
        self.candidate_commit = candidate_commit


def _git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def require_clean_current_main() -> str:
    try:
        return sync_clean_main(ROOT)['head_after']
    except PublishGateError as exc:
        raise GrowthRuntimeError(str(exc)) from exc


def _price(value) -> int | None:
    match = re.search(r'[0-9][0-9,]*', str(value or ''))
    return int(match.group().replace(',', '')) if match else None


def discover_products(topic: dict, *, cache_only: bool) -> list[dict]:
    adapter = AutositeDiscoveryAdapter()
    rows = adapter.search_cache(topic['query'], limit=30) if cache_only else adapter.search_amazon_live(topic['query'], limit=18)
    candidates, seen = [], set()
    for row in rows:
        asin = (row.get('asin') or '').upper()
        title, image = (row.get('title') or '').strip(), (row.get('image_url') or '').strip()
        if not re.fullmatch(r'[A-Z0-9]{10}', asin) or not title or not image or asin in seen:
            continue
        seen.add(asin)
        candidates.append({
            'asin': asin, 'amazon_title': title, 'brand': (row.get('brand') or '').strip(),
            'image_url': image, 'observed_price': _price(row.get('price')),
            'discovery_score': row.get('score', 1),
        })
    distinct, _removed = dedupe_content_products(candidates)
    return distinct


def select_viable_topic(
    planning_candidates: list[dict] | None, feedback: dict, *, cache_only: bool, max_rounds: int = 3
) -> tuple[dict, list[dict], list[dict]]:
    probes = []
    tried_slugs: set[str] = set()
    rounds = 1 if planning_candidates is not None else max(1, int(max_rounds))
    for round_index in range(rounds):
        source = planning_candidates if planning_candidates is not None else discover_candidates()
        ranked = [row for row in rank_candidates(source, feedback) if row['slug'] not in tried_slugs]
        if not ranked and planning_candidates is not None:
            raise PlanningError('continuous planning produced no eligible non-duplicate topic; no publish')
        for topic in ranked:
            tried_slugs.add(topic['slug'])
            try:
                products = discover_products(topic, cache_only=cache_only)
                probes.append({'round': round_index + 1, 'slug': topic['slug'], 'candidate_count': len(products), 'error': ''})
            except Exception as exc:
                products = []
                probes.append({'round': round_index + 1, 'slug': topic['slug'], 'candidate_count': 0, 'error': f'{type(exc).__name__}: {exc}'})
            if len(products) >= 6:
                selected = {**topic, 'selection_reason': 'highest ranked candidate with at least six live product identities'}
                if getattr(source, 'provider_metadata', None):
                    selected['_planning_provider_metadata'] = source.provider_metadata
                return selected, products, probes
    raise PlanningError('no planned topic passed live product viability after full candidate scan and replanning: require at least 6 products')


def _compact_search_key(product: dict) -> str:
    brand = re.sub(r'(のストアを表示|ブランド[:：]?)', '', str(product.get('brand') or '')).strip(' ：:')
    title = re.sub(r'【[^】]{0,40}】|\[[^\]]{0,40}\]', ' ', str(product.get('amazon_title') or ''))
    title = re.sub(r'\s+', ' ', title).strip()
    if brand and title.lower().startswith(brand.lower()):
        title = title[len(brand):].lstrip(' ：:-')
    core = title[:32].rstrip(' 、,/・')
    key = f'{brand} {core}'.strip() if brand else core
    return key[:48].strip()

def build_evidence(topic: dict, products: list[dict], selection: dict | None = None) -> dict:
    evidence_products = []
    for index, product in enumerate(products, 1):
        asin = product['asin'].upper()
        evidence_products.append({'ref': f'p{index}', 'product_id': f'AMZ-{asin}', **product, 'asin': asin, 'search_key': _compact_search_key(product)})
    evidence = {
        'slug': topic['slug'], 'category': topic['category'],
        'canonical_url': f'{BASE}/article/{topic["category"]}/{topic["slug"]}.html',
        'eyecatch_image': f'{BASE}/img/{topic["slug"]}/{topic["slug"]}.png',
        'amazon_tag': AMAZON_TRACKING_ID, 'comparison_axes': topic['comparison_axes'],
        'selection': selection or {}, 'products': evidence_products,
    }
    validate_evidence(evidence)
    return evidence


def evaluate_candidate(payload: dict, evidence: dict) -> tuple[str, list[str], dict]:
    validate_payload(payload); match_refs(payload, evidence)
    rendered = render_article(payload, evidence)
    findings = [repr(finding) for finding in run_review_gate(payload, evidence)]
    return rendered, findings, run_qa(rendered, payload, evidence)


def _gate_feedback(findings: list[str], qa: dict) -> dict:
    failed = {key: value for key, value in qa.items() if (key.endswith('_pass') or key.endswith('_in_range')) and value is False}
    measurements = {key: qa.get(key) for key in ('lead_len', 'summary_len', 'how_to_choose_len', 'main_len', 'description_lens', 'h3_lens')}
    return {'review_findings': findings, 'qa_failures': failed, 'measurements': measurements}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _expected_publish_paths(slug: str, category: str) -> set[str]:
    return {
        f'article/{category}/{slug}.html', f'img/{slug}/{slug}.png',
        f'data/evidence/{slug}.json', f'data/payloads/{slug}.json',
        f'data/eyecatch-receipts/{slug}.json',
        'data/sellemy.db', 'json/articles.json',
        'json/products.json', 'index.html', 'sitemap.xml',
    }


def _parse_porcelain_paths(output: str) -> set[str]:
    return {line[3:] for line in output.splitlines() if line}


def _publish(slug: str, category: str) -> str:
    expected = _expected_publish_paths(slug, category)
    changed = _parse_porcelain_paths(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, text=True))
    if changed != expected:
        raise GrowthRuntimeError(f'publication unit mismatch; expected {sorted(expected)}, observed {sorted(changed)}')
    subprocess.run(['git', 'add', '--', *sorted(expected)], cwd=ROOT, check=True)
    subprocess.run(['git', 'diff', '--cached', '--check'], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', f'Publish Writer-generated growth article: {slug}'], cwd=ROOT, check=True)
    head = _git('rev-parse', 'HEAD')
    pushed = subprocess.run(
        ['git', 'push', 'origin', 'main'], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if pushed.returncode != 0:
        detail = ((pushed.stderr or '') + '\n' + (pushed.stdout or '')).strip()
        lowered = detail.lower()
        if 'fetch first' in lowered or 'non-fast-forward' in lowered or '[rejected]' in lowered:
            raise PublishRaceError(
                f'remote writer advanced origin/main during publish: {detail}',
                candidate_commit=head,
            )
        raise GrowthRuntimeError(f'git push origin main failed: {detail}')
    if head != _git('rev-parse', 'origin/main') or _git('status', '--porcelain'):
        raise GrowthRuntimeError('post-push verification failed: HEAD/origin/main/clean mismatch')
    try:
        verify_production_deploy(slug, category, head)
    except ProductionDeployError as exc:
        raise GrowthRuntimeError(f'production deploy/read-back failed: {exc}') from exc
    return head


def _unfinished_jobs() -> list[dict]:
    return [row for row in all_jobs() if row.get('status') not in {'PUBLISHED', 'DISCARDED', 'FAILED'}]


def _runtime_failure(exc: Exception) -> bool:
    text = f'{type(exc).__name__}: {exc}'.lower()
    runtime_markers = (
        'unavailable', 'timeout', 'timed out', 'active writer', 'binding', 'working tree',
        'publish sync', 'push origin', 'deploy/read-back', 'remote writer', 'runtime', 'connection',
        'selected model is at capacity', 'websocket closed', 'stream disconnected', 'reconnecting...',
        'rollout has no completed built-in image_gen execution evidence',
    )
    return isinstance(exc, (PublishGateError, ProductionDeployError)) or any(x in text for x in runtime_markers)


def _cleanup_expected_dirty(slug: str, category: str, base_head: str) -> None:
    if _git('rev-parse', 'HEAD') != base_head:
        return
    output = subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, text=True
    )
    changed = _parse_porcelain_paths(output)
    expected = _expected_publish_paths(slug, category)
    if not changed or not changed.issubset(expected):
        return
    tracked = set(subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines())
    restore = sorted(changed & tracked)
    if restore:
        subprocess.run(['git', 'checkout', '--', *restore], cwd=ROOT, check=True)
    for relative in sorted(changed - tracked):
        path = ROOT / relative
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def _existing_publish_commit(slug: str, category: str) -> str | None:
    """Return an already-live historical publish without requiring the slug to remain on current TOP."""
    subprocess.run(['git', 'fetch', 'origin', 'main', '--quiet'], cwd=ROOT, check=False)
    completed = subprocess.run(
        ['git', 'log', '--all', '-n', '1', '--format=%H', '--grep', f'^Publish Writer-generated growth article: {slug}$'],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    commit = completed.stdout.strip()
    if not commit:
        return None
    origin = _git('rev-parse', 'origin/main')
    on_remote = subprocess.run(
        ['git', 'merge-base', '--is-ancestor', commit, origin], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0
    if on_remote:
        try:
            rows = json.loads((ROOT / 'json' / 'articles.json').read_text(encoding='utf-8'))
            article = next((row for row in rows if row.get('slug') == slug and row.get('status') == 'published'), None)
            if not article:
                return None
            article_url = f'{BASE}/article/{category}/{slug}.html'
            image_url = str(article.get('img') or f'{BASE}/img/{slug}/{slug}.png')
            for url in (article_url, image_url):
                response = requests.get(url, timeout=12, allow_redirects=True, headers={'User-Agent':'Mozilla/5.0'})
                if response.status_code != 200:
                    return None
            return commit
        except (OSError, json.JSONDecodeError, requests.RequestException):
            return None
    head = _git('rev-parse', 'HEAD')
    if head == commit:
        pushed = subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT, text=True, capture_output=True, check=False)
        if pushed.returncode == 0:
            verify_production_deploy(slug, category, commit)
            return commit
        try:
            recover_unpublished_commit_after_remote_race(ROOT, commit)
        except PublishGateError:
            pass
    return None


def _reconcile_already_published_jobs() -> list[str]:
    reconciled = []
    for row in _unfinished_jobs():
        slug = row['slug']; category = row['category']
        commit = _existing_publish_commit(slug, category)
        if commit:
            mark_published(slug, commit)
            reconciled.append(slug)
    return reconciled


def _writer_stage(slug: str, result: dict) -> None:
    state = load_job(slug)
    failures = int((state.get('retry_count_by_stage') or {}).get('writer') or 0)
    if failures >= WRITER_MAX_ATTEMPTS:
        raise GrowthRuntimeError(f'writer retry exhausted for {slug}')
    topic = read_artifact(slug, 'topic.json')
    evidence = read_artifact(slug, 'evidence.json')
    previous = read_artifact(slug, 'payload.json')
    gate_feedback = read_artifact(slug, 'gate-feedback.json', {})
    started = time.monotonic()
    metadata: dict = {}
    payload: dict | None = None
    try:
        payload, metadata = invoke_writer(
            topic, evidence,
            previous_payload=previous if isinstance(previous, dict) else None,
            gate_feedback=gate_feedback if isinstance(gate_feedback, dict) else None,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        record_execution(slug=slug, role='writer', event_type='turn', stage='writer', metadata=metadata, duration_ms=duration_ms)
        try:
            rendered, findings, qa = evaluate_candidate(payload, evidence)
        except Exception as qa_exc:
            findings = [f'{type(qa_exc).__name__}: {qa_exc}']
            qa = {'overall_pass': False, 'evaluation_exception': findings[0]}
            rendered = ''
        feedback = _gate_feedback(findings, qa)
        if not qa.get('overall_pass') and qa.get('evaluation_exception'):
            feedback['evaluation_exception'] = qa['evaluation_exception']
        passed = not findings and bool(qa.get('overall_pass'))
        save_writer_result(
            slug, payload=payload, qa=qa, findings=findings, feedback=feedback,
            metadata=metadata, passed=passed,
        )
        result.setdefault('writer_attempts', []).append({
            'gate_attempt': failures + 1, **metadata, 'gate_pass': passed,
        })
        result['review_findings'] = findings
        result['qa'] = qa
        if passed:
            record_context_quality(ROOT, 'bu-codex-sellemy-writer', True, session_id=metadata.get('session_id'))
            record_execution(slug=slug, role='writer', event_type='success', stage='writer', metadata=metadata)
            return
        mark_failure(
            slug, stage='writer', failure_class='role_quality', failure_code='REVIEW_QA_FAILED',
            reason=json.dumps(feedback, ensure_ascii=False), role_return=True, role='writer',
        )
        record_context_quality(ROOT, 'bu-codex-sellemy-writer', False, session_id=metadata.get('session_id'), reason='Review/QA failed')
        record_execution(slug=slug, role='writer', event_type='return', stage='writer', metadata=metadata, failure_class='role_quality')
        raise GrowthRuntimeError(f'writer/review/QA failed for {slug}; retained for retry')
    except GrowthRuntimeError:
        raise
    except Exception as exc:
        failure_class = 'runtime_infrastructure' if _runtime_failure(exc) or isinstance(exc, WriterInvocationError) else 'role_quality'
        role_return = failure_class == 'role_quality'
        mark_failure(
            slug, stage='writer', failure_class=failure_class,
            failure_code='WRITER_INVOCATION_OR_OUTPUT_ERROR', reason=f'{type(exc).__name__}: {exc}',
            role_return=role_return, role='writer',
        )
        record_execution(
            slug=slug, role='writer', event_type='return' if role_return else 'runtime_retry',
            stage='writer', metadata=metadata, failure_class=failure_class,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise GrowthRuntimeError(f'writer stage failed for {slug}: {exc}') from exc


def _eyecatch_and_publish(slug: str, result: dict) -> str:
    state = load_job(slug)
    topic = read_artifact(slug, 'topic.json')
    evidence = read_artifact(slug, 'evidence.json')
    payload = read_artifact(slug, 'payload.json')
    category = state['category']

    if state.get('current_stage') == 'PUBLISH_PENDING':
        try:
            existing = _existing_publish_commit(slug, category)
        except Exception as exc:
            existing = None
            result['existing_publish_verification_error'] = f'{type(exc).__name__}: {exc}'
        if existing:
            mark_published(slug, existing)
            return existing

    original_head = _git('rev-parse', 'HEAD')
    try:
        for publish_attempt in range(1, 3):
            with PublishGate(ROOT).acquire() as gate_evidence:
                gate_evidence = {**gate_evidence, 'attempt': publish_attempt}
                result.setdefault('publish_gate_attempts', []).append(gate_evidence)
                state = load_job(slug)
                if state.get('current_stage') == 'EYECATCH_PENDING':
                    started = time.monotonic()
                    try:
                        receipt = ensure_routed_eyecatch(
                            slug=slug, title=topic['title'], category=category,
                            evidence=evidence, payload=payload,
                        )
                        save_eyecatch(
                            slug, receipt=receipt,
                            image_path=ROOT / 'img' / slug / f'{slug}.png',
                        )
                        if receipt.get('thread_id'):
                            record_context_quality(ROOT, DESIGNER_SURFACE, True, session_id=receipt.get('thread_id'))
                        record_execution(
                            slug=slug, role='designer', event_type='turn', stage='eyecatch',
                            metadata={
                                **(receipt.get('token_usage') or {}),
                                'session_id': receipt.get('thread_id'),
                                'generation_method': receipt.get('generation_method'),
                                'provider': receipt.get('provider'),
                                'model': receipt.get('model'),
                                'routing': receipt.get('routing'),
                            },
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        record_execution(slug=slug, role='designer', event_type='success', stage='eyecatch')
                        result['eyecatch'] = receipt
                    except Exception as exc:
                        failure_class = 'runtime_infrastructure' if _runtime_failure(exc) else 'role_quality'
                        role_return = failure_class == 'role_quality'
                        mark_failure(
                            slug, stage='eyecatch', failure_class=failure_class,
                            failure_code='EYECATCH_GENERATION_FAILED', reason=f'{type(exc).__name__}: {exc}',
                            role_return=role_return, role='designer',
                        )
                        session = context_session_id(DESIGNER_SURFACE)
                        if role_return:
                            record_context_quality(ROOT, DESIGNER_SURFACE, False, session_id=session, reason=str(exc))
                        record_execution(
                            slug=slug, role='designer', event_type='return' if role_return else 'runtime_retry',
                            stage='eyecatch', failure_class=failure_class,
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        raise GrowthRuntimeError(
                            f'Eyecatch generation failed for {slug}; retained for retry: {exc}'
                        ) from exc

                materialize_for_publish(slug, ROOT)
                applied = publish_payload(slug, apply=True, allow_existing=False)
                if not applied['applied']:
                    raise GrowthRuntimeError('publication stage refused retained recovery candidate')
                result['applied'] = applied
                try:
                    commit = _publish(slug, category)
                except PublishRaceError as race:
                    recovery = recover_unpublished_commit_after_remote_race(ROOT, race.candidate_commit)
                    result.setdefault('publish_race_recoveries', []).append(recovery)
                    if recovery.get('already_published'):
                        commit = race.candidate_commit
                    elif publish_attempt < 2:
                        continue
                    else:
                        raise GrowthRuntimeError('remote publish race recurred after bounded recovery') from race
                mark_published(slug, commit)
                record_execution(slug=slug, role='publish', event_type='success', stage='publish')
                return commit
        raise GrowthRuntimeError('publish gate exhausted without a published commit')
    except GrowthRuntimeError as exc:
        current = load_job(slug)
        if current.get('current_stage') == 'PUBLISH_PENDING' and current.get('status') != 'PUBLISHED':
            mark_failure(
                slug, stage='publish', failure_class='runtime_infrastructure',
                failure_code='PUBLISH_FAILED', reason=f'{type(exc).__name__}: {exc}', role='publish',
            )
            record_execution(slug=slug, role='publish', event_type='runtime_retry', stage='publish', failure_class='runtime_infrastructure')
        _cleanup_expected_dirty(slug, category, original_head)
        raise
    except Exception as exc:
        current = load_job(slug)
        if current.get('current_stage') == 'PUBLISH_PENDING' and current.get('status') != 'PUBLISHED':
            mark_failure(
                slug, stage='publish', failure_class='runtime_infrastructure',
                failure_code='PUBLISH_FAILED', reason=f'{type(exc).__name__}: {exc}', role='publish',
            )
            record_execution(slug=slug, role='publish', event_type='runtime_retry', stage='publish', failure_class='runtime_infrastructure')
        _cleanup_expected_dirty(slug, category, original_head)
        raise GrowthRuntimeError(f'publish failed for {slug}; retained for retry: {exc}') from exc

def _execute_job(slug: str, result: dict) -> str:
    while True:
        state = load_job(slug)
        stage = state.get('current_stage')
        result['recovery_state'] = state
        result['topic'] = read_artifact(slug, 'topic.json')
        result['selection'] = read_artifact(slug, 'selection.json', {})
        evidence = read_artifact(slug, 'evidence.json', {})
        result['selected_asins'] = [p.get('asin') for p in evidence.get('products', [])]
        result['candidate_count'] = len(evidence.get('products', []))
        if stage in {'WRITER_PENDING', 'QA_FAILED'}:
            _writer_stage(slug, result)
            continue
        if stage in {'EYECATCH_PENDING', 'PUBLISH_PENDING'}:
            return _eyecatch_and_publish(slug, result)
        if stage == 'PUBLISHED':
            return str(state.get('published_commit') or '')
        raise GrowthRuntimeError(f'non-retryable recovery state for {slug}: {stage}')


def _post_publish(adaptive: AdaptivePublishController, result: dict, slug: str, commit: str) -> None:
    try:
        post_publish_feedback, controller_after = adaptive.evaluate_and_record(
            slug=slug, commit=commit, growth_run=result,
        )
    except Exception as evaluation_error:
        post_publish_feedback, controller_after = adaptive.record_evaluation_failure(
            slug=slug, commit=commit, growth_run=result, error=evaluation_error,
        )
        result['post_publish_feedback'] = post_publish_feedback
        result['controller_after'] = controller_after
        result['status'] = 'published_feedback_failed'
        raise GrowthRuntimeError(
            f'post-publish evaluation failed and was recorded as red feedback: {evaluation_error}'
        ) from evaluation_error
    result['post_publish_feedback'] = post_publish_feedback
    result['controller_after'] = controller_after
    try:
        result['feedback_task_bridge'] = sync_feedback_to_task_event(
            feedback=post_publish_feedback, controller_state=controller_after, config=adaptive.config,
        )
    except Exception as bridge_error:
        bridge_feedback, bridge_state = adaptive.record_bridge_failure(
            slug=slug, commit=commit, growth_run=result, error=bridge_error,
        )
        result['feedback_task_bridge_error'] = f'{type(bridge_error).__name__}: {bridge_error}'
        result['post_publish_feedback_bridge_failure'] = bridge_feedback
        result['controller_after'] = bridge_state
        result['status'] = 'published_feedback_failed'
        raise GrowthRuntimeError(f'feedback Task/Event bridge failed: {bridge_error}') from bridge_error
    try:
        purpose = run_purpose_relation_discovery()
        result['purpose_relation_discovery'] = {
            'generated_at': purpose['generated_at'], 'source_snapshot': purpose['source_snapshot'],
            'global': purpose['global'], 'local_processing': purpose['local_processing'],
        }
    except Exception as purpose_error:
        result['purpose_relation_discovery_error'] = f'{type(purpose_error).__name__}: {purpose_error}'


def run(
    *, publish: bool, cache_only: bool = False, report_path: Path | None = REPORT,
    planning_candidates: list[dict] | None = None, scheduled: bool = False,
    controller: AdaptivePublishController | None = None,
) -> dict:
    result = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'route': ['recovery_queue', 'continuous_planning', 'product_selection', 'writer', 'qa', 'eyecatch', 'publish'],
        'published': False,
    }
    adaptive = controller or AdaptivePublishController(ROOT)
    try:
        controller_state = adaptive.current_state()
        result['controller_before'] = controller_state
        if publish and controller_state['publish_paused']:
            result['status'] = 'paused_by_controller'
            return result
        if publish and scheduled:
            preclaimed = os.environ.get('SELLEMY_PRECLAIMED_ADMISSION_ID', '').strip()
            decision = adaptive.validate_scheduled_admission(preclaimed) if preclaimed else adaptive.admit_scheduled()
            result['controller_decision'] = decision
            if not decision['allowed']:
                result['status'] = 'skipped_by_controller'
                return result

        reconciled = _reconcile_already_published_jobs()
        if reconciled:
            result['reconciled_already_published'] = reconciled
        recovery = eligible_job()
        unfinished = _unfinished_jobs()
        if recovery:
            slug = recovery['slug']
            result['recovery_mode'] = True
            result['recovery_slug'] = slug
        elif unfinished:
            result['recovery_mode'] = True
            result['status'] = 'waiting_recovery_backoff'
            result['recovery_waiting'] = [
                {'slug': x['slug'], 'stage': x.get('current_stage'), 'next_retry_at': x.get('next_retry_at')}
                for x in sorted(unfinished, key=lambda x: x.get('created_at') or '')[:20]
            ]
            return result
        else:
            result['recovery_mode'] = False
            result['base_head'] = require_clean_current_main()
            feedback = load_feedback()
            topic, candidates, viability_probes = select_viable_topic(
                planning_candidates, feedback, cache_only=cache_only
            )
            planning_provider = topic.pop('_planning_provider_metadata', None)
            result['planning_provider'] = planning_provider
            result['topic'] = topic
            result['viability_probes'] = viability_probes
            selected, selection = select_six(candidates, topic, feedback)
            evidence = build_evidence(topic, selected, selection)
            job = create_job(
                topic=topic, evidence=evidence, selection=selection,
                planning_provider=planning_provider, viability_probes=viability_probes,
            )
            slug = job['slug']
            planning_meta = ((planning_provider or {}).get('codex_runtime') or {})
            if planning_meta:
                record_execution(slug=slug, role='planning', event_type='turn', stage='planning', metadata=planning_meta)
                record_execution(slug=slug, role='planning', event_type='success', stage='planning', metadata=planning_meta)
                record_context_quality(ROOT, PLANNING_SURFACE, True, session_id=planning_meta.get('session_id'))

        if not publish:
            result['status'] = 'recovery_staged' if result['recovery_mode'] else 'staged'
            return result

        commit = _execute_job(slug, result)
        result['commit'] = commit
        result['published'] = True
        result['status'] = 'published'
        _post_publish(adaptive, result, slug, commit)
        return result
    except Exception as exc:
        result['status'] = 'published_feedback_failed' if result.get('published') else 'blocked_before_publish'
        result['blocker'] = f'{type(exc).__name__}: {exc}'
        decision = result.get('controller_decision') or {}
        if publish and scheduled and not result.get('published') and decision.get('allowed'):
            try:
                result['recovery_schedule'] = adaptive.reschedule_after_failure(failed_slot=decision['slot'])
            except Exception as recovery_error:
                result['recovery_schedule_error'] = f'{type(recovery_error).__name__}: {recovery_error}'
        raise
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        if report_path is not None:
            _write_json(report_path, result)

def main() -> None:
    parser = argparse.ArgumentParser(description='Fail-closed continuous Sellemy operating runtime.')
    parser.add_argument('--publish', action='store_true'); parser.add_argument('--cache-only', action='store_true')
    parser.add_argument('--scheduled', action='store_true', help='Apply adaptive publish slot admission.')
    args = parser.parse_args()
    try:
        result = run(publish=args.publish, cache_only=args.cache_only, scheduled=args.scheduled)
    except Exception as exc:
        try:
            failure = json.loads(REPORT.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            failure = {'status': 'blocked_before_publish', 'error': f'{type(exc).__name__}: {exc}'}
        print(json.dumps(failure, ensure_ascii=False)); raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
