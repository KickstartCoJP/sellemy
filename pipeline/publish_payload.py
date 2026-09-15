from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from qa import run_qa
from renderer import render_article
from review_gate import run_review_gate


ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'json' / 'articles.json'
SITEMAP = ROOT / 'sitemap.xml'
DB = ROOT / 'data' / 'sellemy.db'
GENERIC_EYECATCH = ROOT / 'img' / 'gadget-category-eyecatch.png'


def _load(slug: str) -> tuple[dict, dict]:
    payload = json.loads((ROOT / 'data' / 'payloads' / f'{slug}.json').read_text(encoding='utf-8'))
    evidence = json.loads((ROOT / 'data' / 'evidence' / f'{slug}.json').read_text(encoding='utf-8'))
    return payload, evidence


def _article_entry(payload: dict, evidence: dict) -> dict:
    return {
        'title': payload['h1'],
        'slug': payload['slug'],
        'category': payload['category'],
        'img': evidence['eyecatch_image'],
        'summary': payload['summary'],
    }


def _update_articles(payload: dict, evidence: dict, *, allow_existing: bool) -> str:
    rows = json.loads(ARTICLES.read_text(encoding='utf-8'))
    matches = [i for i, row in enumerate(rows) if row.get('slug') == payload['slug']]
    if matches and not allow_existing:
        raise ValueError(f'article slug already exists: {payload["slug"]}')
    if len(matches) > 1:
        raise ValueError(f'article slug is duplicated in articles.json: {payload["slug"]}')
    if not matches and any(row.get('title') == payload['h1'] for row in rows):
        raise ValueError(f'article title already exists with a different slug: {payload["h1"]}')
    entry = _article_entry(payload, evidence)
    if matches:
        rows[matches[0]] = entry
        action = 'updated'
    else:
        rows.append(entry)
        action = 'added'
    ARTICLES.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return action


def _update_sitemap(evidence: dict) -> str:
    canonical = evidence['canonical_url']
    text = SITEMAP.read_text(encoding='utf-8')
    if f'<loc>{canonical}</loc>' in text:
        return 'unchanged'
    entry = (
        '  <url>\n'
        f'    <loc>{canonical}</loc>\n'
        f'    <lastmod>{date.today().isoformat()}</lastmod>\n'
        '  </url>\n'
    )
    if '</urlset>' not in text:
        raise ValueError('sitemap.xml has no closing urlset tag')
    SITEMAP.write_text(text.replace('</urlset>', entry + '</urlset>', 1), encoding='utf-8')
    return 'added'


def _upsert_products(evidence: dict) -> int:
    now = datetime.now(timezone.utc).isoformat()
    article_file = f'{evidence["slug"]}.html'
    sql = '''
        INSERT INTO products
          (product_id, canonical_name, asin, article_file, amazon_url, image_url,
           status, brand, observed_price, observed_at, amazon_title)
        VALUES (?, ?, ?, ?, ?, ?, 'amazon_verified', ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
          canonical_name=excluded.canonical_name,
          asin=excluded.asin,
          article_file=excluded.article_file,
          amazon_url=excluded.amazon_url,
          image_url=excluded.image_url,
          status='amazon_verified',
          brand=excluded.brand,
          observed_price=COALESCE(excluded.observed_price, products.observed_price),
          observed_at=COALESCE(excluded.observed_at, products.observed_at),
          amazon_title=excluded.amazon_title
    '''
    with sqlite3.connect(DB) as connection:
        for product in evidence['products']:
            connection.execute(
                sql,
                (
                    product['product_id'],
                    product['amazon_title'],
                    product['asin'],
                    article_file,
                    f'https://www.amazon.co.jp/dp/{product["asin"]}?tag={evidence["amazon_tag"]}',
                    product['image_url'],
                    product.get('brand'),
                    product.get('observed_price'),
                    now if product.get('observed_price') is not None else None,
                    product['amazon_title'],
                ),
            )
        connection.commit()
    return len(evidence['products'])


def run(slug: str, *, apply: bool, allow_existing: bool) -> dict:
    payload, evidence = _load(slug)
    rendered = render_article(payload, evidence)
    findings = run_review_gate(payload, evidence)
    qa = run_qa(rendered, payload, evidence)
    result = {
        'slug': slug,
        'review_findings': [repr(finding) for finding in findings],
        'qa': qa,
        'applied': False,
    }
    if findings or not qa['overall_pass']:
        return result
    if not apply:
        return result

    article_path = ROOT / 'article' / evidence['category'] / f'{slug}.html'
    article_path.write_text(rendered + '\n', encoding='utf-8')
    eyecatch_path = ROOT / 'img' / slug / f'{slug}.png'
    if not eyecatch_path.exists():
        eyecatch_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(GENERIC_EYECATCH, eyecatch_path)
    result['articles_json'] = _update_articles(payload, evidence, allow_existing=allow_existing)
    result['sitemap'] = _update_sitemap(evidence)
    result['products_upserted'] = _upsert_products(evidence)
    result['article_path'] = str(article_path.relative_to(ROOT))
    result['eyecatch_path'] = str(eyecatch_path.relative_to(ROOT))
    result['applied'] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description='Review, machine-QA, render, and stage one Writer payload.')
    parser.add_argument('slug')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--allow-existing', action='store_true')
    args = parser.parse_args()
    result = run(args.slug, apply=args.apply, allow_existing=args.allow_existing)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['review_findings'] or not result['qa']['overall_pass']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
