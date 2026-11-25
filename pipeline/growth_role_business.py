from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import requests
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from comparison_acceptance import (
    blocked, product_issues, receipt_issues, require_acceptance, load_receipt,
)
from semantic_review import independent_review
from responses_provider import ResponsesError
from analytics_feedback import load_feedback
from affiliate_config import AMAZON_TRACKING_ID
from adaptive_publish import AdaptivePublishController
from discovery_adapters import AutositeDiscoveryAdapter
from feedback_task_bridge import sync_feedback_to_task_event
from eyecatch_router import EyecatchRoutingError, ensure_routed_eyecatch
from codex_provider import DESIGNER_SURFACE, PLANNING_SURFACE, context_session_id, record_context_quality
from payload_schema import validate_evidence, validate_payload, match_refs
from planning_runtime import PlanningError, discover_candidates, rank_candidates
from chat_publish_approval import require_chat_publish_approval
from publish_gate import (
    require_publishing_enabled, PublishGate, PublishGateError, recover_unpublished_commit_after_remote_race, sync_clean_main,
)
from purpose_relation_discovery import run as run_purpose_relation_discovery
from product_selection import ProductSelectionError, dedupe_content_products, select_six
from product_sources import fetch_amazon_item_source
from manufacturer_research import research_selected
from publish_payload import run as publish_payload
from production_deploy import verify as verify_production_deploy, ProductionDeployError
from qa import run_qa
from renderer import render_article
from review_gate import run_review_gate
from writer_runtime import WriterInvocationError, invoke_writer
from recovery_provider_policy import active_policy, require_local_provider_allowed
from local_semantic_review import split_local_review
from growth_recovery import (
    all_jobs, create_job, eligible_job, job_dir, load_job, mark_failure, mark_published,
    materialize_for_publish, read_artifact, record_execution, save_eyecatch,
    save_writer_result, set_stage, WRITER_MAX_ATTEMPTS, write_artifact,
    request_product_reselection, replace_product_evidence,
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


def product_search_query(topic: dict) -> str:
    """Keep editorial intent words out of Amazon product discovery."""
    query = str(topic.get('query') or '').strip()
    query = re.sub(r'(?<!\S)(比較|おすすめ|ランキング|選び方|6選|2026|2025)(?!\S)', ' ', query)
    query = re.sub(r'\s+', ' ', query).strip()
    return query


def discover_products(topic: dict, *, cache_only: bool) -> list[dict]:
    adapter = AutositeDiscoveryAdapter()
    rows = adapter.search_cache(topic['query'], limit=30) if cache_only else adapter.search_amazon_live(product_search_query(topic), limit=30)
    excluded = set(topic.get('_exclude_asins') or [])
    max_products = min(int(topic.get('_max_products', 10)), 30) if not cache_only else 30
    candidates, seen = [], set()
    for row in rows[:30] if not cache_only else rows:
        asin = (row.get('asin') or '').upper()
        from product_candidate_registry import is_ng
        if is_ng(asin):
            continue
        title, image = (row.get('title') or '').strip(), (row.get('image_url') or '').strip()
        if not re.fullmatch(r'[A-Z0-9]{10}', asin) or not title or not image or asin in seen or asin in excluded:
            continue
        if row.get('sponsored'):
            continue
        rating, count = row.get('rating'), row.get('review_count')
        if not cache_only and (rating is None or count is None or rating < 4.0 or count < 100):
            continue
        seen.add(asin)
        candidates.append({
            'asin': asin, 'amazon_title': title, 'brand': (row.get('brand') or '').strip(),
            'image_url': image, 'observed_price': _price(row.get('price')),
            'discovery_score': row.get('score', 1), 'rating': rating, 'review_count': count,
        })
        if len(candidates) >= max_products:
            break
    if not cache_only and candidates and not topic.get('_raw_only'):
        def verified_source(candidate):
            try:
                return fetch_amazon_item_source(candidate['asin'], candidate['amazon_title'])
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=6) as executor:
            sources = executor.map(verified_source, candidates)
            candidates = [dict(candidate, brand=(source or {}).get('brand') or candidate['brand'],
                               sources=[source] if source else [], provisional=not bool(source))
                          for candidate, source in zip(candidates, sources)]
    distinct, _removed = dedupe_content_products(candidates)
    return distinct



def _product_queries(topic: dict):
    """Exactly three searches: approved theme x each of three approved axes."""
    base = product_search_query(topic)
    axes = topic.get('comparison_axes') or []
    if not base or len(axes) != 3:
        raise ProductSelectionError('product discovery requires a theme and exactly three approved axes')
    queries = []
    for axis in axes:
        keywords = axis.get('keywords') or []
        if not keywords or not str(keywords[0]).strip():
            raise ProductSelectionError('product discovery axis lacks Amazon search keyword')
        # Popularity is measured by existing ratings/review counts, not by
        # finding the literal word 'popular' in a technical specification.
        theme = str(topic.get('product_theme') or base).strip()
        query = (theme if topic.get('product_theme') else f'{base} {str(keywords[0]).strip()}') if axis.get('id') == 'popular' else f'{theme} {str(keywords[0]).strip()}'
        if query in queries:
            raise ProductSelectionError('product discovery axes produced duplicate search keywords')
        queries.append(query)
    return queries


def _verified_product(candidate: dict, *, cache_only: bool) -> dict | None:
    """Validate listing identity; optional evidence acquisition must not veto an item."""
    if not all(str(candidate.get(field) or '').strip() for field in ('asin','amazon_title','image_url')):
        return None
    if cache_only:return candidate
    existing = [source for source in candidate.get('sources') or []
                if isinstance(source, dict) and source.get('verified') is True
                and str(source.get('excerpt') or '').strip()]
    if existing:return candidate
    try:
        source = fetch_amazon_item_source(candidate['asin'], candidate['amazon_title'])
    except Exception:
        source = None
    if isinstance(source, dict) and source.get('verified') is True and str(source.get('excerpt') or '').strip():
        return {**candidate, 'brand':source.get('brand') or candidate.get('brand',''),
                'sources':[source], 'provisional':False}
    # No manufacturer-wide lookup for ordinary product identification. Claim
    # QA must still reject unsubstantiated material performance assertions.
    return {**candidate,'sources':list(candidate.get('sources') or []),'provisional':True}


def collect_unique_products(topic: dict, *, cache_only: bool, existing: list[dict] | None = None,
                            target_new: int = 6, used_queries: set[str] | None = None,
                            max_searches: int = 3, rejected_asins: set[str] | None = None) -> tuple[list[dict], dict]:
    """Verify each found product before admission; cap at 10 evidenced items per axis.

    A duplicate may change axis only to fill a deficient axis (fewer than two)
    while leaving at least two evidenced items on its original axis.
    """
    axes = topic['comparison_axes']
    axis_ids = {axis['id'] for axis in axes}
    original, _ = dedupe_content_products(list(existing or []))
    kept = [p for p in original if p.get('discovery_axis_id') in axis_ids
            and all(str(p.get(k) or '').strip() for k in ('asin','amazon_title','image_url'))]
    rejected = set(rejected_asins or [])
    retained_count = len(kept)
    attempts = []
    cross_axis_duplicates = {axis['id']: set() for axis in axes}
    examined_asins = {p['asin'] for p in kept} | rejected
    for axis, query in zip(axes, _product_queries(topic)):
        axis_id = axis['id']
        for retry in range(min(max_searches, 3) + 1):
            count = sum(p.get('discovery_axis_id') == axis_id for p in kept)
            if count >= 10:
                break
            try:
                # Search results are NOT admitted candidates. Review the found
                # products sequentially, stopping at ten evidenced admissions.
                discoveries = discover_products({**topic, 'query': query,
                    '_exclude_asins': sorted(rejected), '_max_products': 30,
                    '_raw_only': not cache_only}, cache_only=cache_only)
                admitted = 0
                for candidate in discoveries:
                    if sum(p.get('discovery_axis_id') == axis_id for p in kept) >= 10:
                        break
                    asin = candidate.get('asin')
                    other = next((p for p in kept if p['asin'] == asin), None)
                    if other:
                        if other['discovery_axis_id'] != axis_id:
                            cross_axis_duplicates[axis_id].add(asin)
                        continue
                    if asin in examined_asins:
                        continue
                    examined_asins.add(asin)
                    product = _verified_product(candidate, cache_only=cache_only)
                    if product is None:
                        continue
                    distinct, _ = dedupe_content_products([*kept, product])
                    if len(distinct) != len(kept) + 1:
                        continue
                    kept.append({**product, 'discovery_axis_id': axis_id})
                    admitted += 1
                count = sum(p.get('discovery_axis_id') == axis_id for p in kept)
                attempts.append({'axis_id': axis_id, 'query': query, 'retry': retry,
                                 'found': len(discoveries), 'new': admitted,
                                 'moved': 0, 'candidate_count': count,
                                 'complete': count >= 2})
            except Exception as exc:
                attempts.append({'axis_id': axis_id, 'query': query, 'retry': retry,
                                 'found': 0, 'new': 0, 'complete': False,
                                 'error': f'{type(exc).__name__}: {exc}'})
    # Reassignment happens only after all searches have finished: a duplicate
    # discovered during collection is not grounds for premature movement.
    for axis in axes:
        target = axis['id']
        for asin in sorted(cross_axis_duplicates[target]):
            if sum(p.get('discovery_axis_id') == target for p in kept) >= 2:
                break
            product = next((p for p in kept if p['asin'] == asin), None)
            if not product:
                continue
            origin = product['discovery_axis_id']
            if sum(p.get('discovery_axis_id') == origin for p in kept) <= 2:
                continue
            product['discovery_axis_id'] = target
            attempts.append({'axis_id': target, 'event': 'deficient_axis_duplicate_transfer',
                             'asin': asin, 'from_axis_id': origin, 'moved': 1})
    complete = all(sum(p.get('discovery_axis_id') == a['id'] for p in kept) >= 2 for a in axes)
    return kept, {'added_unique': len(kept) - retained_count,
                  'candidate_count': len(kept), 'complete': complete, 'attempts': attempts}


def _replace_unproductive_axes(topic: dict, products: list[dict], *, cache_only: bool,
                                prior_ids: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Swap an unproductive angle for a Planner-generated one; keep exactly 3 axes."""
    if cache_only:
        return products, []
    from planning_runtime import propose_replacement_axis, PlanningError
    changes = []
    from comparison_acceptance import source_text
    from product_candidate_registry import is_ng
    from product_selection import dedupe_content_products
    eligible, _ = dedupe_content_products([
        p for p in products if not is_ng(p.get('asin')) and source_text(p).strip()])
    tried = set(prior_ids or []) | {a['id'] for a in topic['comparison_axes']}
    for index, axis in enumerate(list(topic['comparison_axes'])):
        if sum(p.get('discovery_axis_id') == axis['id'] for p in eligible) >= 2:
            continue
        # One new proposal for this unproductive angle in a single execution.
        try:
            replacement = propose_replacement_axis(topic, axis, tried_axis_ids=sorted(tried))
            if replacement['id'] in tried:
                raise PlanningError('replacement axis already attempted')
            trial_topic = {**topic, 'comparison_axes': [
                replacement if j == index else old for j, old in enumerate(topic['comparison_axes'])]}
            current = [p for p in products if p.get('discovery_axis_id') != axis['id']]
            found, trace = collect_unique_products(trial_topic, cache_only=cache_only, existing=current)
            tried.add(replacement['id'])
            changes.append({'from': axis['id'], 'to': replacement['id'],
                            'found': sum(p.get('discovery_axis_id') == replacement['id'] for p in found),
                            'attempts': trace['attempts']})
            replacement_eligible, _ = dedupe_content_products([
                p for p in found if not is_ng(p.get('asin')) and source_text(p).strip()])
            if sum(p.get('discovery_axis_id') == replacement['id'] for p in replacement_eligible) >= 2:
                topic['comparison_axes'][index] = replacement
                products = found
            else:
                changes[-1]['reason'] = 'replacement also insufficient; old axis preserved'
        except (PlanningError, ProductSelectionError) as exc:
            changes.append({'from': axis['id'], 'error': str(exc)})
    return products, changes


def _has_verified_product_source(product: dict) -> bool:
    return any(isinstance(source, dict) and source.get('verified') is True
               and str(source.get('excerpt') or '').strip()
               for source in product.get('sources') or [])


def replenish_products(topic: dict, products: list[dict], *, cache_only: bool,
                       max_batches: int = 1) -> tuple[list[dict], list[dict]]:
    # No second search round or generic keyword supplementation.
    return products, []


def _researchable_candidates(products: list[dict], *, cache_only: bool) -> bool:
    return not cache_only and bool(products) and all(
        isinstance(product, dict)
        and all(product.get(key) for key in ('asin', 'amazon_title', 'image_url'))
        and isinstance(product.get('sources'), list)
        for product in products
    )


def select_viable_topic(
    planning_candidates: list[dict] | None, feedback: dict, *, cache_only: bool,
    max_rounds: int | None = None, regenerate_after_supplied: bool = False
) -> tuple[dict, list[dict], list[dict]]:
    # Consider each novel candidate; request new batches only while they add novel slugs.
    probes = []
    tried_slugs: set[str] = set()
    # Explicitly bounded even for direct callers. The scheduled fresh-create
    # path passes one candidate with regeneration disabled.
    rounds = range(max_rounds if max_rounds is not None else 2)
    for round_index in rounds:
        source = (planning_candidates if round_index == 0 and planning_candidates is not None
                  else discover_candidates())
        ranked = [row for row in rank_candidates(source, feedback) if row['slug'] not in tried_slugs]
        if not ranked:
            # A repeated group is not permission to retry the same topics.
            error = PlanningError('planning produced no novel themes; requires a changed candidate set')
            error.viability_probes = probes
            raise error
        for topic in ranked:
            tried_slugs.add(topic['slug'])
            try:
                products, discovery_trace = collect_unique_products(topic, cache_only=cache_only)
                if not discovery_trace['complete']:
                    products, swaps = _replace_unproductive_axes(topic, products, cache_only=cache_only)
                    if swaps:
                        discovery_trace['axis_replacements'] = swaps
                    discovery_trace['complete'] = all(
                        sum(p.get('discovery_axis_id') == axis['id'] for p in products) >= 2
                        for axis in topic['comparison_axes'])
                probes.append({'round': round_index + 1, 'slug': topic['slug'], 'candidate_count': len(products), 'discovery': discovery_trace, 'error': ''})
            except Exception as exc:
                products, discovery_trace = [], {'complete': False, 'attempts': []}
                probes.append({'round': round_index + 1, 'slug': topic['slug'], 'candidate_count': 0, 'error': f'{type(exc).__name__}: {exc}'})
            if len(products) >= 6 and discovery_trace['complete']:
                try:
                    # Source fetch failure is a recorded unknown, not a planning veto.
                    unavailable = [p['asin'] for p in products if not any(
                        isinstance(src, dict) and src.get('verified') is True
                        and str(src.get('excerpt') or '').strip()
                        for src in p.get('sources') or [])]
                    if unavailable:
                        probes[-1]['unavailable_source_asins'] = unavailable
                    six, selection = select_six(products, topic, feedback)
                    from safety_recall import unsafe_products
                    for safety_retry in range(3):
                        flags, _ = unsafe_products(six)
                        if not flags:
                            break
                        if any(flag['status']=='NOT_CHECKED' for flag in flags):
                            from safety_government_feed import GovernmentFeedUnavailable
                            raise GovernmentFeedUnavailable('official recall register has not completed refresh')
                        rejected = {flag['asin'] for flag in flags}
                        excluded.update(rejected)
                        retained = [p for p in six if p['asin'] not in rejected]
                        products, retry_trace = collect_unique_products(
                            topic, cache_only=cache_only, existing=retained,
                            rejected_asins=excluded, max_searches=1)
                        probes[-1].setdefault('safety_research', []).append({
                            'round': safety_retry + 1, 'blocked_asins': sorted(rejected),
                            'retained': len(retained), 'discovery': retry_trace})
                        if len(products) != 6 or not retry_trace['complete']:
                            raise ProductSelectionError('product safety replacement unavailable')
                        six, selection = select_six(products, topic, feedback, pinned=retained)
                    else:
                        flags, _ = unsafe_products(six)
                    if flags:
                        raise ProductSelectionError('product safety replacement exhausted')
                    evidence = build_evidence(topic, six, selection)
                    acceptance = independent_review(evidence)
                    issues = receipt_issues(acceptance, evidence)
                    excluded = set()
                    # Only rejected identities are replaced. Successful items keep their axis.
                    for qa_retry in range(2):
                        if not issues or cache_only:
                            break
                        reviewed = (acceptance.get('review') or {}).get('products') or []
                        by_ref = {row['ref']: row for row in evidence['products']}
                        failed_refs = {row['ref'] for row in reviewed if isinstance(row, dict)
                                       and row.get('topic_fit') is False}
                        # Missing sources alone never replace all six products.
                        rejected = {by_ref[ref]['asin'] for ref in failed_refs if ref in by_ref}
                        if not rejected:
                            break
                        excluded.update(rejected)
                        retained = [p for p in six if p['asin'] not in rejected]
                        products, retry_trace = collect_unique_products(
                            topic, cache_only=cache_only, existing=retained, rejected_asins=excluded,
                            max_searches=1)
                        probes[-1].setdefault('qa_research', []).append({
                            'round': qa_retry + 1, 'replaced': len(rejected),
                            'retained': len(retained), 'discovery': retry_trace})
                        if len(products) != 6 or not retry_trace['complete']:
                            issues = ['QA-rejected products could not be replenished']
                            break
                        six, selection = select_six(products, topic, feedback, pinned=retained)
                        evidence = build_evidence(topic, six, selection)
                        acceptance = independent_review(evidence)
                        issues = receipt_issues(acceptance, evidence)
                    if issues:
                        probes[-1]['acceptance'] = blocked(issues)
                        continue
                except ProductSelectionError as exc:
                    probes[-1]['acceptance'] = blocked([str(exc)])
                    continue
                selected = {**topic, '_product_acceptance': acceptance,
                            '_accepted_selection_asins': [p['asin'] for p in six],
                            '_accepted_selection_metadata': selection,
                            'selection_reason': 'six products passed independent evidence acceptance'}
                if getattr(source, 'provider_metadata', None):
                    selected['_planning_provider_metadata'] = source.provider_metadata
                # Pass the exact reviewed six onward: discovery rows may lack
                # selection_reason and must not replace signed QA evidence.
                return selected, six, probes
        # Supplied candidates are a complete batch; do not regenerate them.
        if planning_candidates is not None and not regenerate_after_supplied:
            break
    error = PlanningError('all novel planning candidates failed independent comparison acceptance')
    error.viability_probes = probes
    raise error


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
        evidence_products.append({**product, 'ref': f'p{index}', 'product_id': f'AMZ-{asin}', 'asin': asin, 'search_key': _compact_search_key(product)})
    evidence = {
        'slug': topic['slug'], 'category': topic['category'],
        'canonical_url': f'{BASE}/article/{topic["category"]}/{topic["slug"]}.html',
        'eyecatch_image': f'{BASE}/img/{topic["slug"]}/{topic["slug"]}.png',
        'amazon_tag': AMAZON_TRACKING_ID, 'comparison_axes': topic['comparison_axes'],
        'selection': selection or {}, 'products': evidence_products,
    }
    validate_evidence(evidence)
    if topic.get('_product_acceptance'):
        evidence['product_acceptance'] = topic['_product_acceptance']
    return evidence


def evaluate_candidate(payload: dict, evidence: dict, *, acceptance: dict | None = None, require_semantic_acceptance: bool = True) -> tuple[str, list[str], dict]:
    validate_payload(payload); match_refs(payload, evidence)
    rendered = render_article(payload, evidence)
    findings = [repr(finding) for finding in run_review_gate(payload, evidence)]
    image_audit = None
    if os.environ.get('SELLEMY_REQUIRE_PRODUCT_IMAGE_AUDIT') == '1':
        from product_image_audit import audit_remote_product_images
        image_audit = audit_remote_product_images(evidence['products'])
    return rendered, findings, run_qa(
        rendered, payload, evidence, acceptance=acceptance,
        require_semantic_acceptance=require_semantic_acceptance,
        image_audit=image_audit,
    )


def _gate_feedback(findings: list[str], qa: dict) -> dict:
    failed = {key: value for key, value in qa.items() if (key.endswith('_pass') or key.endswith('_in_range')) and value is False}
    measurements = {key: qa.get(key) for key in ('lead_len', 'summary_len', 'how_to_choose_len', 'main_len', 'description_lens', 'h3_lens')}
    result = {'review_findings': findings, 'qa_failures': failed, 'measurements': measurements}
    if failed.get('distinct_openings_endings_pass') is False:
        result['duplicate_openings'] = qa.get('duplicate_openings') or {}
        result['duplicate_endings'] = qa.get('duplicate_endings') or {}
    return result


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _expected_publish_paths(slug: str, category: str) -> set[str]:
    return {
        f'article/{category}/{slug}.html', f'img/{slug}/{slug}.png',
        f'data/acceptance/{slug}.json', f'data/evidence/{slug}.json', f'data/payloads/{slug}.json',
        f'data/eyecatch-receipts/{slug}.json',
        'data/sellemy.db', 'json/articles.json',
        'json/products.json', 'index.html', 'articles.html', 'products.html', 'sitemap.xml',
    }


def _expected_update_paths(slug: str, category: str) -> set[str]:
    # Static catalog pages may be re-generated for an existing article.
    return _expected_publish_paths(slug, category) | {'articles.html', 'products.html'}


def _parse_porcelain_paths(output: str) -> set[str]:
    return {line[3:] for line in output.splitlines() if line}


def _publish(slug: str, category: str, *, update: bool = False) -> str:
    require_publishing_enabled(ROOT)
    payload = json.loads((ROOT / 'data' / 'payloads' / f'{slug}.json').read_text())
    evidence = json.loads((ROOT / 'data' / 'evidence' / f'{slug}.json').read_text())
    require_acceptance(payload, evidence, load_receipt(ROOT, slug))
    expected = _expected_update_paths(slug, category) if update else _expected_publish_paths(slug, category)
    changed = _parse_porcelain_paths(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, text=True))
    article_file = f'article/{category}/{slug}.html'
    valid = (bool(changed) and changed.issubset(expected) and article_file in changed) if update else changed == expected
    if not valid:
        raise GrowthRuntimeError(f'publication unit mismatch; expected subset {sorted(expected)} with article update={update}, observed {sorted(changed)}')
    subprocess.run(['git', 'add', '--', *sorted(changed)], cwd=ROOT, check=True)
    subprocess.run(['git', 'diff', '--cached', '--check'], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', f'Publish Writer-generated growth article: {slug}'], cwd=ROOT, check=True)
    head = _git('rev-parse', 'HEAD')
    require_publishing_enabled(ROOT)
    from article_release import ArticleReleaseError, release
    try:
        deployed_commit = release(ROOT, slug, head)
    except ArticleReleaseError as exc:
        raise PublishRaceError(str(exc), candidate_commit=head) from exc
    if deployed_commit != _git('rev-parse', 'origin/main') or _git('status', '--porcelain'):
        raise GrowthRuntimeError('post-push verification failed: HEAD/origin/main/clean mismatch')
    try:
        if update:
            import hashlib
            article_file = ROOT / 'article' / category / f'{slug}.html'
            verify_production_deploy(
                slug, category, deployed_commit,
                expected_html_sha256=hashlib.sha256(article_file.read_bytes()).hexdigest())
        else:
            verify_production_deploy(slug, category, deployed_commit)
    except ProductionDeployError as exc:
        raise GrowthRuntimeError(f'production deploy/read-back failed: {exc}') from exc
    return deployed_commit


def _unfinished_jobs() -> list[dict]:
    return [row for row in all_jobs() if row.get('status') not in {'PUBLISHED', 'DISCARDED', 'FAILED', 'RETIRED'}]


def _runtime_failure(exc: Exception) -> bool:
    text = f'{type(exc).__name__}: {exc}'.lower()
    runtime_markers = (
        'unavailable', 'timeout', 'timed out', 'active writer', 'binding', 'working tree',
        'publish sync', 'push origin', 'deploy/read-back', 'remote writer', 'runtime', 'connection',
        'selected model is at capacity', 'websocket closed', 'stream disconnected', 'reconnecting...',
        'rollout has no completed built-in image_gen execution evidence',
    )
    return isinstance(exc, (TypeError, AttributeError, ImportError, NameError, PublishGateError, ProductionDeployError)) or any(x in text for x in runtime_markers)


def _cleanup_expected_dirty(slug: str, category: str, base_head: str) -> None:
    if _git('rev-parse', 'HEAD') != base_head:
        return
    output = subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, text=True
    )
    changed = _parse_porcelain_paths(output)
    expected = _expected_update_paths(slug, category)
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
        ['git', 'log', '--all', '-n', '1', '--format=%H', '--extended-regexp',
         '--grep', rf'^Publish Writer-generated growth article: {re.escape(slug)}( \(#[0-9]+\))?$'],
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
        # Never validate uncommitted replacement inputs then push an older commit.
        if _git('status', '--porcelain', '--untracked-files=all'):
            raise GrowthRuntimeError('reconciliation push requires a clean accepted commit')
        require_publishing_enabled(ROOT)
        payload = json.loads((ROOT / 'data' / 'payloads' / f'{slug}.json').read_text())
        evidence = json.loads((ROOT / 'data' / 'evidence' / f'{slug}.json').read_text())
        require_acceptance(payload, evidence, load_receipt(ROOT, slug))
        from article_release import ArticleReleaseError, release
        try:
            deployed_commit = release(ROOT, slug, commit)
            verify_production_deploy(slug, category, deployed_commit)
            return deployed_commit
        except (ArticleReleaseError, ProductionDeployError):
            try:
                recover_unpublished_commit_after_remote_race(ROOT, commit)
            except PublishGateError:
                pass
    return None


def _reconcile_already_published_jobs() -> list[str]:
    reconciled = []
    for row in _unfinished_jobs():
        # Only publishing that already passed CEO approval may be reconciled.
        # A previously live slug is not proof that a newly rewritten version
        # was approved; do not clear CHAT_REVIEW_PENDING or QA_FAILED.
        if row.get('current_stage') != 'PUBLISH_PENDING':
            continue
        # A published baseline is not proof that its updated content is live.
        if row.get('published_commit'):
            continue
        slug = row['slug']; category = row['category']
        approval = read_artifact(slug, 'chat-publish-approval.json', {}) or {}
        payload = read_artifact(slug, 'payload.json', {}) or {}
        evidence = read_artifact(slug, 'evidence.json', {}) or {}
        from chat_publish_approval import approval_issues
        if approval_issues(approval, payload=payload, evidence=evidence):
            continue
        commit = _existing_publish_commit(slug, category)
        if commit:
            mark_published(slug, commit)
            reconciled.append(slug)
    return reconciled


def _backfill_missing_amazon_sources(pool: list[dict], attempted: set[str], *, limit: int = 8) -> tuple[list[dict], set[str]]:
    """Recover verifiable item facts for historical discovery-only candidates.

    An ASIN with no source is not a researched product. Record bounded attempts
    separately from research completion so old pools remain eligible for recovery.
    """
    from concurrent.futures import ThreadPoolExecutor
    pending = [p for p in pool if p.get('asin') not in attempted and not any(
        isinstance(source, dict) and source.get('verified') is True and str(source.get('excerpt') or '').strip()
        for source in p.get('sources') or [])]
    pending = sorted(pending, key=lambda row: (not bool(row.get('brand')), -float(row.get('discovery_score') or 0)))[:limit]
    if not pending:
        return pool, attempted
    def fetch(p):
        try:
            return fetch_amazon_item_source(p['asin'], p.get('amazon_title', ''))
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        recovered = list(executor.map(fetch, pending))
    new_sources = {item['asin']: source for item, source in zip(pending, recovered)
                   if isinstance(source, dict) and source.get('verified') is True
                   and str(source.get('excerpt') or '').strip()}
    rows = [{**item, 'sources': [*(item.get('sources') or []), new_sources[item['asin']]]}
            if item.get('asin') in new_sources else item for item in pool]
    return rows, attempted | {item['asin'] for item in pending}


def _obsolete_identity_review(artifact: dict) -> bool:
    """An old quote-only veto is not a rejection of the selected products."""
    issues=artifact.get('issues') or []
    checks=(artifact.get('review') or {}).get('products') or []
    return bool(issues and checks and all(item.get('topic_fit') is True for item in checks)
        and any(issue.endswith(': identity has no source anchor') for issue in issues)
        and all(issue in ('independent review did not accept',
                          'independent semantic criteria not satisfied') or
                issue.endswith(': identity has no source anchor') for issue in issues))


def _reselect_products(slug: str, *, cache_only: bool) -> None:
    """Keep a durable candidate pool; only return after six products pass QA.

    A failed independent review discards the rejected six-item combination,
    not the whole discovery history. Search expands on later invocations.
    """
    topic = dict(read_artifact(slug, 'topic.json'))
    topic.pop('_product_acceptance', None)
    progress = read_artifact(slug, 'product-search-progress.json', {}) or {}
    from product_candidate_registry import is_ng, set_ng
    pool = [p for p in (progress.get('candidates') or []) if not is_ng(p.get('asin'))]
    excluded = set(progress.get('excluded_asins') or [])
    tried = list(progress.get('queries') or [])
    reviewed_sets = set(progress.get('reviewed_sets') or [])
    researched_asins = set(progress.get('researched_asins') or [])
    source_backfill_attempted = set(progress.get('source_backfill_attempted') or [])
    previous = read_artifact(slug, 'evidence.json', {})
    if progress.get('reuse_previous_products', True):
        pool.extend(p for p in previous.get('products', [])
                    if all(p.get(k) for k in ('asin', 'amazon_title', 'image_url')))
        # The last independent review is a durable product-candidate snapshot.
        # Restore it when an earlier failed search damaged the saved pool;
        # do not restore genuinely NG or unrelated-axis products.
        reviews = sorted(job_dir(slug).glob('product-review-*.json'),
                         key=lambda path:path.stat().st_mtime,reverse=True)
        if reviews:
            previous_review=read_artifact(slug,reviews[0].name,{}) or {}
            valid_axes={a['id'] for a in topic['comparison_axes']}
            pool.extend(p for p in (previous_review.get('evidence') or {}).get('products',[])
                if all(p.get(k) for k in ('asin','amazon_title','image_url'))
                and p.get('discovery_axis_id') in valid_axes)
    pool, _ = dedupe_content_products([p for p in pool if not is_ng(p.get('asin'))])

    def save_progress():
        write_artifact(slug, 'product-search-progress.json', {
            'candidates': pool, 'excluded_asins': sorted(excluded),
            'queries': tried, 'reviewed_sets': sorted(reviewed_sets),
            'researched_asins': sorted(researched_asins),
            'source_backfill_attempted': sorted(source_backfill_attempted),
            'complete': False,
            'reuse_previous_products': progress.get('reuse_previous_products', True),
        })

    try:
        # Process the already found candidates in sequential non-overlapping
        # batches. Never research the same ASIN twice on evidence-quality failure.
        # A transient network failure is handled by the infrastructure retry path.
        available = [p for p in pool if p.get('asin') not in excluded]
        pending = [p for p in available if p.get('asin') not in researched_asins]
        if cache_only:
            discover_products(topic, cache_only=True)
        if not pending and not pool:
            found, trace = collect_unique_products(topic, cache_only=cache_only)
            pool, _ = dedupe_content_products(found)
            tried.extend(trace['attempts'])
            save_progress()
            available = [p for p in pool if p.get('asin') not in excluded]
            pending = [p for p in available if p.get('asin') not in researched_asins]
        batch = pending[:30]
        if batch:
            # Existing listings identify products; do not crawl every manufacturer
            # before Product QA when no safety/performance claim is being made.
            enriched = batch
            # Keep all source receipts and move the cursor only after the entire batch.
            by_asin = {p['asin']: p for p in enriched}
            pool = [by_asin.get(p['asin'], p) for p in pool]
            researched_asins.update(p['asin'] for p in batch)
            save_progress()
        # Missing optional source excerpts do not block a selectable listing.
        # Writer/Claim QA limit material claims to their actual evidence.
        # Existing searches may have stopped at ten unverified listings.
        # Replenish the evidenced pool before declaring the topic exhausted.
        evidenced = [p for p in pool if all(str(p.get(k) or '').strip()
                     for k in ('asin','amazon_title','image_url'))]
        if any(sum(p.get('discovery_axis_id') == axis['id'] for p in evidenced) < 2
               for axis in topic['comparison_axes']):
            replenished, search_trace = collect_unique_products(
                topic, cache_only=cache_only, existing=evidenced,
                rejected_asins=excluded)
            # Never discard the old researched pool when a later search only
            # returns a sparse partial result or experiences an outage.
            pool, _ = dedupe_content_products([*pool, *replenished])
            tried.extend(search_trace['attempts'])
            if not search_trace['complete']:
                old_ids = {x.get('to') for x in tried if x.get('event') in ('axis_replaced', 'axis_replacement_attempt') and x.get('to')}
                pool, swaps = _replace_unproductive_axes(topic, pool, cache_only=cache_only,
                                                           prior_ids=old_ids)
                for change in swaps:
                    tried.append({'event':'axis_replaced' if change.get('to') and change.get('found', 0) >= 2
                                  else 'axis_replacement_attempt', **change})
                if any(x.get('event') == 'axis_replaced' for x in tried):
                    write_artifact(slug, 'topic.json', topic)
            researched_asins.update(p['asin'] for p in pool)
            save_progress()
        available = [p for p in pool if p.get('asin') not in excluded
                     and all(str(p.get(k) or '').strip()
                             for k in ('asin','amazon_title','image_url'))]
        feedback = load_feedback()
        # Preserve confirmed product fit without making optional source quotes a gate.
        # Failed products alone are replaced; an unchanged rejected set is never retried.
        from comparison_acceptance import normalized, source_text
        import re as _re
        latest = sorted(job_dir(slug).glob('product-review-*.json'),
                        key=lambda path: path.stat().st_mtime, reverse=True)
        pinned, known_bad = [], set()
        old = read_artifact(slug, latest[0].name, {}) or {} if latest else {}
        stale_identity_review = _obsolete_identity_review(old)
        current_axis_by_asin = {item['asin']: item.get('discovery_axis_id') for item in available}
        current_axes = {axis['id'] for axis in topic['comparison_axes']}
        prior_products = (old.get('evidence') or {}).get('products') or []
        if prior_products and any(
            item.get('discovery_axis_id') not in current_axes or
            current_axis_by_asin.get(item.get('asin')) != item.get('discovery_axis_id')
            for item in prior_products
        ):
            # A past reviewed set cannot veto a re-planned topic with new axes.
            reviewed_sets.difference_update({'|'.join(sorted(old.get('asins') or []))})
        if stale_identity_review:
            # Earlier quote-format failures cannot permanently poison candidate
            # combinations after the risk-based Product QA contract changed.
            reviewed_sets.difference_update({ '|'.join(sorted(old.get('asins') or [])) })
        if latest and not stale_identity_review:
            old_evidence = (old.get('evidence') or {}).get('products') or []
            old_checks = ((old.get('review') or {}).get('products') or [])
            by_ref_old = {item['ref']: item for item in old_evidence}
            current_axes = {axis['id'] for axis in topic['comparison_axes']}
            current_candidates = {item['asin']: item for item in available}
            for check in old_checks:
                product = by_ref_old.get(check.get('ref'))
                if not product: continue
                asin = product.get('asin')
                # Never pin a product from a replaced axis, or one absent from
                # the current eligible pool. Those reviews concern a superseded
                # selection, not the current three-axis article.
                current_product = current_candidates.get(asin)
                if (not current_product or
                    product.get('discovery_axis_id') not in current_axes or
                    current_product.get('discovery_axis_id') != product.get('discovery_axis_id')):
                    continue
                key = normalized(str(check.get('identity_key') or ''))
                anchored = bool(key and len(key) >= 3 and key in normalized(source_text(current_product)))
                if check.get('topic_fit') is True and anchored:
                    pinned.append(asin)
                elif check.get('topic_fit') is False:
                    known_bad.add(asin)
                    set_ng(asin, 'independent product review: incompatible with topic', source=slug)
                # A reviewer citation/key defect is not grounds for blacklisting.
        pin_set = set(pinned)
        if len(pin_set) == 6 and latest:
            previous_review = read_artifact(slug, latest[0].name, {}) or {}
            if previous_review.get('issues'):
                from growth_recovery import hold_product_review_conflict
                hold_product_review_conflict(slug, reasons=previous_review['issues'],
                    pinned_asins=sorted(pin_set))
                raise GrowthRuntimeError(f'planning_restart_required for {slug}: incompatible product review')
        # A changed topic invalidates only reviewed combinations whose old
        # product-axis mapping no longer matches the active candidate pool.
        if reviewed_sets:
            for review_file in job_dir(slug).glob('product-review-*.json'):
                archived = read_artifact(slug, review_file.name, {}) or {}
                old_asins = archived.get('asins') or []
                signature = '|'.join(sorted(old_asins))
                if signature not in reviewed_sets:
                    continue
                old_rows = (archived.get('evidence') or {}).get('products') or []
                obsolete_quote_failure = _obsolete_identity_review(archived)
                changed_axes = bool(old_rows) and any(
                    item.get('discovery_axis_id') not in current_axes or
                    current_axis_by_asin.get(item.get('asin')) != item.get('discovery_axis_id')
                    for item in old_rows)
                if obsolete_quote_failure or changed_axes:
                    # Legacy machine-citation failure is not a quality veto;
                    # do not exhaust the remaining axis pool by rotating away
                    # legitimate candidates in a past unusable combination.
                    reviewed_sets.discard(signature)
        if latest and not stale_identity_review and not (read_artifact(slug, 'product-qa-remediation.json', {}) or {}).get('signature'):
            write_artifact(slug, 'product-qa-remediation.json', {
                'retained_asins': pinned, 'needs_remediation_asins': sorted(known_bad),
                'issues': old.get('issues') or [],
                'signature': '|'.join(sorted(old.get('asins') or [])),
            })
        # Choose the previous accepted products first. Only replace the failed slots.
        if known_bad:
            # A genuinely rejected product is never allowed back into a new
            # six-item selection, even when other products passed Product QA.
            available = [p for p in available if p['asin'] not in known_bad]
        if pin_set:
            available_by_asin = {p['asin']: p for p in available}
            pinned_rows = [available_by_asin[a] for a in pinned if a in available_by_asin]
            replacement_candidates = [p for p in available if p['asin'] not in pin_set
                                      and p['asin'] not in known_bad]
            selected_pool = pinned_rows + replacement_candidates
            # No falling back to the already rejected products when replacements
            # are insufficient; stay in source research instead.
            available = selected_pool
        # Product-role state may contain 30 valid items while a previous review
        # poisoned only one six-item combination. Do not report exhaustion
        # solely because that previous combination was refused on formatting.
        while len(available) >= 6:
            if pin_set:
                by_asin = {p['asin']: p for p in available}
                retained = [by_asin[a] for a in pinned if a in by_asin]
                if len(retained) != len(pin_set):
                    break
                selected, selection = select_six(available, topic, feedback, pinned=retained)
                selection['targeted_replacement'] = True
            else:
                selected, selection = select_six(available, topic, feedback)
            six_asins = tuple(sorted(p['asin'] for p in selected))
            signature = '|'.join(six_asins)
            if signature in reviewed_sets:
                # A large durable pool can keep ranking the same rejected six forever.
                # Exclude one non-pinned member of this already-reviewed combination
                # for this selection pass, then select another distinct combination.
                removable = next((asin for asin in six_asins if asin not in pin_set), None)
                if removable is None:
                    break
                available = [p for p in available if p['asin'] != removable]
                continue
            from safety_recall import unsafe_products
            unsafe, _safety_checks = unsafe_products(selected)
            if unsafe:
                if any(flag['status']=='NOT_CHECKED' for flag in unsafe):
                    from safety_government_feed import GovernmentFeedUnavailable
                    raise GovernmentFeedUnavailable('official recall register has not completed refresh')
                blocked_asins={flag['asin'] for flag in unsafe}
                for flag in unsafe:
                    if flag['status'] == 'RECALL':
                        set_ng(flag['asin'],'verified manufacturer recall notice',source=slug)
                excluded.update(blocked_asins)
                pinned=[asin for asin in pinned if asin not in blocked_asins]
                pin_set=set(pinned)
                available=[p for p in available if p['asin'] not in blocked_asins]
                save_progress()
                continue
            evidence = build_evidence(topic, selected, selection)
            evidence['product_acceptance'] = independent_review(evidence)
            issues = product_issues(evidence)
            write_artifact(slug, 'product-review-' + signature.replace('|', '-') + '.json', {
                'asins': list(six_asins), 'evidence': evidence, 'issues': issues,
                'review': (evidence.get('product_acceptance') or {}).get('review'),
            })
            reviewed_sets.add(signature)
            if not issues:
                replace_product_evidence(slug, evidence, selection)
                write_artifact(slug, 'product-search-progress.json', {
                    'candidates': pool, 'excluded_asins': sorted(excluded),
                    'queries': tried, 'reviewed_sets': sorted(reviewed_sets),
                    'researched_asins': sorted(researched_asins),
                    'source_backfill_attempted': sorted(source_backfill_attempted),
                    'complete': True,
                    'reuse_previous_products': True,
                })
                return
            # Record the actionable QA defects; do not count an unchanged retry
            # as an independent QA failure on the following scheduler tick.
            review = (evidence.get('product_acceptance') or {}).get('review') or {}
            by_ref = {row['ref']: row for row in evidence['products']}
            good = []
            rejected = set()
            for check in review.get('products') or []:
                product = by_ref.get(check.get('ref'))
                if product is None: continue
                key = normalized(str(check.get('identity_key') or ''))
                anchor_ok = bool(len(key)>=3 and key in normalized(source_text(product)))
                if check.get('topic_fit') is True and anchor_ok:
                    good.append(product['asin'])
                elif check.get('topic_fit') is False:
                    rejected.add(product['asin'])
            for rejected_asin in rejected:
                set_ng(rejected_asin, 'product QA confirmed topic incompatibility', source=slug)
            write_artifact(slug, 'product-qa-remediation.json', {
                'retained_asins': good, 'needs_remediation_asins': sorted(rejected),
                'issues': issues, 'signature': signature,
            })
            # Retain the genuine passing products, excluding only confirmed
            # topic/identity rejects. A reviewer-output defect is a retryable
            # review failure, not an exhausted product catalogue.
            if not rejected and any('identity has no source anchor' in x for x in issues) and all(
                'identity has no source anchor' in x or x=='independent review did not accept'
                for x in issues):
                # The reviewer emitted an invalid machine citation, not a
                # reason to reopen product research. Record an infrastructure
                # retry and preserve the six concrete candidates.
                reviewed_sets.discard(signature)
                save_progress()
                mark_failure(slug,stage='product',failure_class='runtime_infrastructure',
                             failure_code='PRODUCT_REVIEW_CITATION_INVALID',
                             reason='; '.join(issues),role='review')
                raise GrowthRuntimeError(f'product reviewer citation invalid for {slug}')
            save_progress()
            break
        save_progress()
        request_product_reselection(slug, ['candidate pool exhausted; return to Planning'])
        raise GrowthRuntimeError(f'planning_restart_required for {slug}')
    except ProductSelectionError as exc:
        save_progress()
        # Without a state transition this WRITER_PENDING job is reselected every
        # minute even though the product-selection input has not changed.
        # The existing evidence fingerprint moves unchanged attempts to
        # PRODUCT_HOLD; quality requirements remain intact.
        request_product_reselection(slug, [str(exc)])
        raise GrowthRuntimeError(f'planning_restart_required for {slug}: {exc}') from exc
    except Exception as exc:
        from discovery_adapters import AmazonSearchUnavailable
        if isinstance(exc, AmazonSearchUnavailable):
            save_progress()
            mark_failure(slug, stage='product', failure_class='runtime_infrastructure',
                         failure_code='AMAZON_SEARCH_UNAVAILABLE', reason=str(exc), role='planning')
            raise GrowthRuntimeError(f'Amazon search temporarily unavailable for {slug}: {exc}') from exc
        raise
    except (ResponsesError, OSError, requests.RequestException) as exc:
        save_progress()
        mark_failure(slug, stage='product', failure_class='runtime_infrastructure',
                     failure_code='PRODUCT_RESELECTION_UNAVAILABLE', reason=str(exc), role='planning')
        raise GrowthRuntimeError(f'product reselection unavailable for {slug}') from exc


def _reconcile_ceo_audit_delivery(result: dict) -> None:
    """Idempotent delivery retry for selected published articles; never blocks production."""
    from operating_profile import normal_profile
    if not normal_profile():
        return
    config=json.loads((ROOT/'config/operating-profile.json').read_text())
    if config.get('postpublish_ceo_enabled') is not True:
        return
    from ceo_audit_bridge import submit_postpublish_audit
    for job in all_jobs():
        slug=job.get('slug')
        if job.get('status')!='PUBLISHED' or job.get('operating_profile')!='normal' or not slug:
            continue
        selected=read_artifact(slug,'postpublish-ceo-selection.json',None)
        if not selected or read_artifact(slug,'postpublish-ceo-delivered.json',None):
            continue
        try:
            receipt=submit_postpublish_audit(
                sample=selected, category=job['category'],
                product_evidence_ref=str(job_dir(slug)/'evidence.json'),
                article_file_path=str(ROOT/'article'/job['category']/(slug+'.html')))
            write_artifact(slug,'postpublish-ceo-delivered.json',receipt)
            result.setdefault('postpublish_ceo_deliveries',[]).append(receipt)
        except Exception as exc:
            result.setdefault('postpublish_ceo_delivery_errors',[]).append({
                'slug':slug,'error_type':type(exc).__name__})


def _post_publish(adaptive: AdaptivePublishController, result: dict, slug: str, commit: str) -> None:
    # CEO audit is a POST-publication observation, never a publish authorization.
    # Preserve a journal receipt in the existing job; actual Portal dispatch requires
    # its separate verified integration and cannot be claimed by this local write.
    try:
        from operating_profile import normal_profile,ROOT as profile_root
        from postpublish_ceo_sampling import pending_review_receipt
        if normal_profile():
            policy=json.loads((profile_root/'config/operating-profile.json').read_text())
            every_n=int(policy.get('ceo_review_every_n_articles',10))
            sampling=pending_review_receipt(jobs=all_jobs(),current_slug=slug,every_n=every_n)
            result['postpublish_ceo_selection']=sampling
            selected=sampling['ready_to_dispatch']
            if selected:
                selected_slug=selected['slug']
                write_artifact(selected_slug,'postpublish-ceo-selection.json',selected)
                _reconcile_ceo_audit_delivery(result)
    except Exception as audit_exc:
        result['postpublish_ceo_selection_error']=f'{type(audit_exc).__name__}: {audit_exc}'
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
