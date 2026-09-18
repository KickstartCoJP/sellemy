from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from analytics_feedback import load_feedback
from affiliate_config import AMAZON_TRACKING_ID
from adaptive_publish import AdaptivePublishController
from discovery_adapters import AutositeDiscoveryAdapter
from feedback_task_bridge import sync_feedback_to_task_event
from payload_schema import validate_evidence, validate_payload, match_refs
from planning_runtime import PlanningError, discover_candidates, rank_candidates
from product_selection import select_six
from publish_payload import run as publish_payload
from qa import run_qa
from renderer import render_article
from review_gate import run_review_gate
from writer_runtime import invoke_writer

REPORT = ROOT / 'data' / 'growth_last_run.json'
BASE = 'https://www.sellemy.jp'


class GrowthRuntimeError(RuntimeError):
    pass


def _git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def require_clean_current_main() -> str:
    if _git('status', '--porcelain'):
        raise GrowthRuntimeError('working tree is not clean; growth aborted before Planning')
    subprocess.run(['git', 'fetch', 'origin', 'main', '--quiet'], cwd=ROOT, check=True)
    head, origin = _git('rev-parse', 'HEAD'), _git('rev-parse', 'origin/main')
    if head != origin:
        raise GrowthRuntimeError(f'HEAD {head} differs from origin/main {origin}; no generation or publish')
    return head


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
    return candidates


def select_viable_topic(
    planning_candidates: list[dict] | None, feedback: dict, *, cache_only: bool, max_probes: int = 6
) -> tuple[dict, list[dict], list[dict]]:
    ranked = rank_candidates(planning_candidates if planning_candidates is not None else discover_candidates(), feedback)
    if not ranked:
        raise PlanningError('continuous planning produced no eligible non-duplicate topic; no publish')
    probes = []
    for topic in ranked[:max_probes]:
        try:
            products = discover_products(topic, cache_only=cache_only)
            probes.append({'slug': topic['slug'], 'candidate_count': len(products), 'error': ''})
        except Exception as exc:
            products = []
            probes.append({'slug': topic['slug'], 'candidate_count': 0, 'error': f'{type(exc).__name__}: {exc}'})
        if len(products) >= 6:
            selected = {**topic, 'selection_reason': 'highest ranked candidate with at least six live product identities'}
            return selected, products, probes
    raise PlanningError('no planned topic passed live product viability: require at least 6 products')


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
    subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT, check=True)
    head = _git('rev-parse', 'HEAD')
    if head != _git('rev-parse', 'origin/main') or _git('status', '--porcelain'):
        raise GrowthRuntimeError('post-push verification failed: HEAD/origin/main/clean mismatch')
    return head


def run(
    *, publish: bool, cache_only: bool = False, report_path: Path | None = REPORT,
    planning_candidates: list[dict] | None = None, scheduled: bool = False,
    controller: AdaptivePublishController | None = None,
) -> dict:
    result = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'route': [
            'adaptive_publish_admission', 'continuous_planning', 'product_selection',
            'product_evidence', 'writer_adapter', 'independent_review', 'machine_qa',
            'renderer', 'publication_unit', 'post_publish_evaluation', 'frequency_control',
        ],
        'published': False,
    }
    try:
        adaptive = controller or AdaptivePublishController(ROOT)
        controller_state = adaptive.current_state()
        result['controller_before'] = controller_state
        if publish and controller_state['publish_paused']:
            result['status'] = 'paused_by_controller'
            result['controller_decision'] = {'allowed': False, 'reason': 'ceo_alert_pause'}
            return result
        if publish and scheduled:
            decision = adaptive.admit_scheduled()
            result['controller_decision'] = decision
            if not decision['allowed']:
                result['status'] = 'skipped_by_controller'
                return result
        result['base_head'] = require_clean_current_main()
        feedback = load_feedback()
        topic, candidates, viability_probes = select_viable_topic(
            planning_candidates, feedback, cache_only=cache_only
        )
        result['topic'] = topic
        result['viability_probes'] = viability_probes
        selected, selection = select_six(candidates, topic, feedback)
        evidence = build_evidence(topic, selected, selection)
        payload, findings, qa, rendered, feedback_to_writer = None, [], {}, '', None
        writer_attempts = []
        for attempt in range(1, 3):
            payload, writer = invoke_writer(topic, evidence, previous_payload=payload, gate_feedback=feedback_to_writer)
            rendered, findings, qa = evaluate_candidate(payload, evidence)
            writer_attempts.append({'gate_attempt': attempt, **writer, 'gate_pass': not findings and qa['overall_pass']})
            if not findings and qa['overall_pass']:
                break
            feedback_to_writer = _gate_feedback(findings, qa)
        result.update({'candidate_count': len(candidates), 'selected_asins': [p['asin'] for p in selected], 'selection': selection, 'writer_attempts': writer_attempts, 'review_findings': findings, 'qa': qa})
        if findings or not qa['overall_pass']:
            raise GrowthRuntimeError('mandatory Review/QA gates did not pass; no files applied or published')
        _write_json(ROOT / 'data' / 'evidence' / f'{topic["slug"]}.json', evidence)
        _write_json(ROOT / 'data' / 'payloads' / f'{topic["slug"]}.json', payload)
        applied = publish_payload(topic['slug'], apply=True, allow_existing=False)
        if not applied['applied']:
            raise GrowthRuntimeError('publication stage refused candidate after repeated gates')
        result['applied'] = applied
        result['rendered_chars'] = len(rendered)
        result['status'] = 'published' if publish else 'staged'
        if publish:
            result['commit'] = _publish(topic['slug'], topic['category'])
            result['published'] = True
            try:
                post_publish_feedback, controller_after = adaptive.evaluate_and_record(
                    slug=topic['slug'], commit=result['commit'], growth_run=result,
                )
            except Exception as evaluation_error:
                post_publish_feedback, controller_after = adaptive.record_evaluation_failure(
                    slug=topic['slug'], commit=result['commit'], growth_run=result, error=evaluation_error,
                )
                result['post_publish_feedback'] = post_publish_feedback
                result['controller_after'] = controller_after
                try:
                    result['feedback_task_bridge'] = sync_feedback_to_task_event(
                        feedback=post_publish_feedback, controller_state=controller_after,
                        config=adaptive.config,
                    )
                except Exception as bridge_error:
                    bridge_feedback, bridge_state = adaptive.record_bridge_failure(
                        slug=topic['slug'], commit=result['commit'], growth_run=result, error=bridge_error,
                    )
                    result['feedback_task_bridge_error'] = f'{type(bridge_error).__name__}: {bridge_error}'
                    result['post_publish_feedback_bridge_failure'] = bridge_feedback
                    result['controller_after'] = bridge_state
                result['status'] = 'published_feedback_failed'
                raise GrowthRuntimeError(
                    f'post-publish evaluation failed and was recorded as red feedback: {evaluation_error}'
                ) from evaluation_error
            result['post_publish_feedback'] = post_publish_feedback
            result['controller_after'] = controller_after
            try:
                result['feedback_task_bridge'] = sync_feedback_to_task_event(
                    feedback=post_publish_feedback, controller_state=controller_after,
                    config=adaptive.config,
                )
            except Exception as bridge_error:
                bridge_feedback, bridge_state = adaptive.record_bridge_failure(
                    slug=topic['slug'], commit=result['commit'], growth_run=result, error=bridge_error,
                )
                result['feedback_task_bridge_error'] = f'{type(bridge_error).__name__}: {bridge_error}'
                result['post_publish_feedback_bridge_failure'] = bridge_feedback
                result['controller_after'] = bridge_state
                result['status'] = 'published_feedback_failed'
                raise GrowthRuntimeError(
                    f'feedback Task/Event bridge failed and publication was paused: {bridge_error}'
                ) from bridge_error
        return result
    except Exception as exc:
        result['status'] = 'published_feedback_failed' if result['published'] else 'blocked_before_publish'
        result['blocker'] = f'{type(exc).__name__}: {exc}'
        decision = result.get('controller_decision') or {}
        if publish and scheduled and not result['published'] and decision.get('allowed'):
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
