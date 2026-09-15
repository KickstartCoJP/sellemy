from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from analytics_feedback import load_feedback
from category_metadata import CATEGORIES
from planning_runtime import rank_candidates
from product_selection import select_six
from publish_payload import run as publication_dry_run
from qa import run_qa
from renderer import render_article
from review_gate import run_review_gate


def _candidate(slug: str, category: str) -> dict:
    return {
        'slug': slug, 'category': category, 'query': slug.replace('-', ' '), 'title': f'{slug} 6選',
        'intent_key': slug, 'comparison_axes': [
            {'id': 'use', 'label': '利用場面', 'keywords': ['用']},
            {'id': 'performance', 'label': '性能差', 'keywords': ['性能']},
        ],
        'signals': {key: .7 for key in ('search_demand', 'purchase_intent', 'product_viability', 'seasonality', 'profitability', 'evidence_availability')},
    }


def validate() -> dict:
    payload_paths = sorted((ROOT / 'data' / 'payloads').glob('*.json'))
    actual_articles = []
    for payload_path in payload_paths:
        slug = payload_path.stem
        evidence_path = ROOT / 'data' / 'evidence' / payload_path.name
        if not evidence_path.exists():
            continue
        result = publication_dry_run(slug, apply=False, allow_existing=True)
        actual_articles.append({'slug': slug, 'review_findings': result['review_findings'], 'qa_overall': result['qa']['overall_pass']})

    base_payload = json.loads(payload_paths[0].read_text(encoding='utf-8'))
    base_evidence = json.loads((ROOT / 'data' / 'evidence' / payload_paths[0].name).read_text(encoding='utf-8'))
    category_results = {}
    for category in CATEGORIES:
        payload, evidence = copy.deepcopy(base_payload), copy.deepcopy(base_evidence)
        payload['category'] = evidence['category'] = category
        evidence['canonical_url'] = f'https://www.sellemy.jp/article/{category}/{payload["slug"]}.html'
        evidence['eyecatch_image'] = f'https://www.sellemy.jp/{CATEGORIES[category].eyecatch}'
        rendered = render_article(payload, evidence)
        qa = run_qa(rendered, payload, evidence)
        category_results[category] = {'category_pass': qa['category_pass'], 'review_findings': len(run_review_gate(payload, evidence))}

    planning = rank_candidates([
        _candidate('skin-texture-care-tools', 'beauty'),
        _candidate('compact-kitchen-storage', 'dailygoods'),
        _candidate('portable-work-accessories', 'gadget'),
    ], load_feedback())

    evidence_rows = []
    for path in sorted((ROOT / 'data' / 'evidence').glob('*.json')):
        evidence_rows.extend(json.loads(path.read_text(encoding='utf-8'))['products'])
    unique = {row['asin']: row for row in evidence_rows}
    selection_candidates = [
        {**row, 'discovery_score': 1, 'observed_price': row.get('observed_price')}
        for row in unique.values()
    ]
    topic = _candidate('validation-selection', 'gadget')
    selected, selection = select_six(selection_candidates, topic, load_feedback())

    with sqlite3.connect(ROOT / 'data' / 'sellemy.db') as connection:
        product_total, asin_total, asin_distinct = connection.execute('SELECT count(*),count(asin),count(distinct asin) FROM products').fetchone()
        noncanonical = connection.execute("SELECT count(*) FROM products WHERE asin IS NOT NULL AND product_id != 'AMZ-' || upper(asin)").fetchone()[0]
        relation_total = connection.execute('SELECT count(*) FROM product_articles').fetchone()[0]
    catalog = json.loads((ROOT / 'json' / 'products.json').read_text(encoding='utf-8'))
    growth_asins = {row['asin'] for row in evidence_rows}
    catalog_asins = {row.get('asin') for row in catalog}
    top = (ROOT / 'index.html').read_text(encoding='utf-8')

    checks = {
        'actual_article_dry_runs': bool(actual_articles) and all(not row['review_findings'] and row['qa_overall'] for row in actual_articles),
        'three_categories': all(row['category_pass'] and row['review_findings'] == 0 for row in category_results.values()),
        'continuous_planning_three_categories': {row['category'] for row in planning} == set(CATEGORIES),
        'variable_selection': len(selected) == 6 and selection['price_used_as_constraint_only'],
        'asin_canonical': product_total == asin_total == asin_distinct and noncanonical == 0,
        'catalog_synced': growth_asins <= catalog_asins,
        'top_connected': 'PUBLICATION:RECENT:START' in top and '/products.html' in top,
        'current_og_domain': 'takayuki-sexy-suzuki.github.io' not in top and 'https://www.sellemy.jp/' in top,
    }
    return {
        'status': 'pass' if all(checks.values()) else 'fail', 'checks': checks,
        'actual_articles': actual_articles, 'categories': category_results,
        'planning_candidates': [{'slug': row['slug'], 'category': row['category'], 'score': row['planning_score']} for row in planning],
        'selection': {'asins': [row['asin'] for row in selected], **selection},
        'product_canonical': {'products': product_total, 'distinct_asins': asin_distinct, 'noncanonical_ids': noncanonical, 'article_relations': relation_total},
        'catalog': {'rows': len(catalog), 'growth_asins': len(growth_asins), 'missing_growth_asins': sorted(growth_asins - catalog_asins)},
    }


if __name__ == '__main__':
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'pass' else 2)
