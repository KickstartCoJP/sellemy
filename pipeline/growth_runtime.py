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

from discovery_adapters import AutositeDiscoveryAdapter
from payload_schema import validate_evidence, validate_payload, match_refs
from publish_payload import run as publish_payload
from qa import run_qa
from renderer import render_article
from review_gate import run_review_gate
from writer_runtime import invoke_writer

ARTICLES = ROOT / 'json' / 'articles.json'
TOPICS = ROOT / 'data' / 'growth_topics.json'
REPORT = ROOT / 'data' / 'growth_last_run.json'
BASE = 'https://www.sellemy.jp'
AMAZON_TAG = 'suzuron-22'


class GrowthRuntimeError(RuntimeError):
    pass


def _git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def require_clean_current_main() -> str:
    if _git('status', '--porcelain'):
        raise GrowthRuntimeError('working tree is not clean; scheduled growth aborted before Writer')
    subprocess.run(['git', 'fetch', 'origin', 'main', '--quiet'], cwd=ROOT, check=True)
    head = _git('rev-parse', 'HEAD')
    origin = _git('rev-parse', 'origin/main')
    if head != origin:
        raise GrowthRuntimeError(f'HEAD {head} differs from origin/main {origin}; no generation or publish')
    return head


def next_topic() -> dict:
    existing = json.loads(ARTICLES.read_text(encoding='utf-8'))
    used_slugs = {row['slug'] for row in existing}
    used_intent_text = [
        _normalize_intent(' '.join(str(row.get(key, '')) for key in ('slug', 'title', 'summary')))
        for row in existing
    ]
    for topic in json.loads(TOPICS.read_text(encoding='utf-8')):
        intent = _normalize_intent(topic.get('intent_key') or topic['query'])
        duplicate_intent = any(intent and intent in existing_intent for existing_intent in used_intent_text)
        if topic['slug'] not in used_slugs and not duplicate_intent:
            return topic
    raise GrowthRuntimeError('no curated unpublished topic remains; add a reviewed search-intent topic')


def _normalize_intent(value: str) -> str:
    value = value.lower().replace('６', '6')
    value = re.sub(r'(おすすめ|比較|選び方|6選|6picks|top6)', '', value)
    return re.sub(r'[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+', '', value)


def _price(value) -> int | None:
    match = re.search(r'[0-9][0-9,]*', str(value or ''))
    return int(match.group().replace(',', '')) if match else None


def discover_products(topic: dict, *, cache_only: bool) -> list[dict]:
    adapter = AutositeDiscoveryAdapter()
    rows = (
        adapter.search_cache(topic['query'], limit=30)
        if cache_only else adapter.search_amazon_live(topic['query'], limit=18)
    )
    candidates = []
    seen = set()
    for row in rows:
        asin = (row.get('asin') or '').upper()
        title = (row.get('title') or '').strip()
        image = (row.get('image_url') or '').strip()
        price = _price(row.get('price'))
        if not re.fullmatch(r'[A-Z0-9]{10}', asin) or not title or not image or price is None or asin in seen:
            continue
        seen.add(asin)
        candidates.append({
            'asin': asin,
            'amazon_title': title,
            'brand': (row.get('brand') or '').strip(),
            'image_url': image,
            'observed_price': price,
        })
    return sorted(candidates, key=lambda item: item['observed_price'])


def select_six(candidates: list[dict]) -> list[dict]:
    if len(candidates) < 6:
        raise GrowthRuntimeError(f'product evidence incomplete: found {len(candidates)}, require 6')
    indexes = [0, 1, max(2, len(candidates) // 2 - 1), min(len(candidates) - 1, max(3, len(candidates) // 2)), len(candidates) - 2, len(candidates) - 1]
    selected = []
    seen = set()
    for index in indexes + list(range(len(candidates))):
        product = candidates[index]
        if product['asin'] not in seen:
            seen.add(product['asin'])
            selected.append(product)
        if len(selected) == 6:
            break
    if len(selected) != 6:
        raise GrowthRuntimeError('could not select six unique ASINs')
    return selected


def build_evidence(topic: dict, products: list[dict]) -> dict:
    tiers = ('lowrange', 'lowrange', 'midrange', 'midrange', 'highrange', 'highrange')
    evidence_products = []
    for index, (product, tier) in enumerate(zip(products, tiers), 1):
        evidence_products.append({
            'ref': f'p{index}',
            'tier': tier,
            'product_id': f'GROW-{topic["slug"]}-{index}',
            **product,
        })
    evidence = {
        'slug': topic['slug'],
        'category': topic['category'],
        'canonical_url': f'{BASE}/article/{topic["category"]}/{topic["slug"]}.html',
        'eyecatch_image': f'{BASE}/img/{topic["slug"]}/{topic["slug"]}.png',
        'amazon_tag': AMAZON_TAG,
        'products': evidence_products,
    }
    validate_evidence(evidence)
    return evidence


def evaluate_candidate(payload: dict, evidence: dict) -> tuple[str, list[str], dict]:
    validate_payload(payload)
    match_refs(payload, evidence)
    rendered = render_article(payload, evidence)
    findings = [repr(finding) for finding in run_review_gate(payload, evidence)]
    qa = run_qa(rendered, payload, evidence)
    return rendered, findings, qa


def _gate_feedback(findings: list[str], qa: dict) -> dict:
    failed = {
        key: value
        for key, value in qa.items()
        if (key.endswith('_pass') or key.endswith('_in_range')) and value is False
    }
    measurements = {
        key: qa.get(key)
        for key in ('lead_len', 'summary_len', 'how_to_choose_len', 'main_len', 'description_lens', 'h3_lens')
    }
    return {'review_findings': findings, 'qa_failures': failed, 'measurements': measurements}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _expected_publish_paths(slug: str, category: str) -> set[str]:
    return {
        f'article/{category}/{slug}.html',
        f'img/{slug}/{slug}.png',
        f'data/evidence/{slug}.json',
        f'data/payloads/{slug}.json',
        'data/sellemy.db',
        'json/articles.json',
        'sitemap.xml',
    }


def _parse_porcelain_paths(output: str) -> set[str]:
    return {line[3:] for line in output.splitlines() if line}


def _publish(slug: str, category: str) -> str:
    expected = _expected_publish_paths(slug, category)
    status = subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, text=True
    )
    changed = _parse_porcelain_paths(status)
    if changed != expected:
        raise GrowthRuntimeError(
            f'publish change-set mismatch; expected {sorted(expected)}, observed {sorted(changed)}'
        )
    subprocess.run(['git', 'add', '--', *sorted(expected)], cwd=ROOT, check=True)
    subprocess.run(['git', 'diff', '--cached', '--check'], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', f'Publish Writer-generated growth article: {slug}'], cwd=ROOT, check=True)
    subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT, check=True)
    head = _git('rev-parse', 'HEAD')
    if head != _git('rev-parse', 'origin/main') or _git('status', '--porcelain'):
        raise GrowthRuntimeError('post-push verification failed: HEAD/origin/main/clean mismatch')
    return head


def run(*, publish: bool, cache_only: bool = False) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    result = {
        'started_at': started_at,
        'route': ['product_evidence', 'writer_structured_payload', 'independent_review', 'machine_qa', 'renderer', 'publish'],
        'published': False,
    }
    try:
        base_head = require_clean_current_main()
        topic = next_topic()
        result['base_head'] = base_head
        result['topic'] = topic
        candidates = discover_products(topic, cache_only=cache_only)
        selected = select_six(candidates)
        evidence = build_evidence(topic, selected)
        max_attempts = 2
        payload = None
        writer_attempts = []
        findings = []
        qa = {}
        rendered = ''
        feedback = None
        for attempt in range(1, max_attempts + 1):
            payload, writer = invoke_writer(
                topic,
                evidence,
                previous_payload=payload,
                gate_feedback=feedback,
            )
            rendered, findings, qa = evaluate_candidate(payload, evidence)
            writer_attempts.append({'attempt': attempt, **writer, 'gate_pass': not findings and qa['overall_pass']})
            if not findings and qa['overall_pass']:
                break
            feedback = _gate_feedback(findings, qa)
        result.update({
            'candidate_count': len(candidates),
            'selected_asins': [product['asin'] for product in selected],
            'writer_attempts': writer_attempts,
            'review_findings': findings,
            'qa': qa,
        })
        if findings:
            raise GrowthRuntimeError(f'independent review failed after {max_attempts} Writer attempts: {findings}')
        if not qa['overall_pass']:
            raise GrowthRuntimeError(
                f'machine QA failed after {max_attempts} Writer attempts; no files applied or published'
            )

        evidence_path = ROOT / 'data' / 'evidence' / f'{topic["slug"]}.json'
        payload_path = ROOT / 'data' / 'payloads' / f'{topic["slug"]}.json'
        _write_json(evidence_path, evidence)
        _write_json(payload_path, payload)
        applied = publish_payload(topic['slug'], apply=True, allow_existing=False)
        if not applied['applied']:
            raise GrowthRuntimeError('publish stage refused candidate after repeated gates')
        result['applied'] = applied
        if publish:
            result['commit'] = _publish(topic['slug'], topic['category'])
            result['published'] = True
        else:
            result['rendered_chars'] = len(rendered)
        result['status'] = 'published' if publish else 'staged'
        return result
    except Exception as exc:
        result['status'] = 'blocked_before_publish'
        result['blocker'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        _write_json(REPORT, result)


def main() -> None:
    parser = argparse.ArgumentParser(description='Fail-closed unattended Writer growth runtime.')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--cache-only', action='store_true')
    args = parser.parse_args()
    try:
        result = run(publish=args.publish, cache_only=args.cache_only)
    except Exception as exc:
        print(json.dumps({'status': 'blocked_before_publish', 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
