from __future__ import annotations

import argparse
import html
import json
import shutil
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from category_metadata import get_category
from qa import run_qa
from renderer import render_article
from review_gate import run_review_gate

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'json' / 'articles.json'
PRODUCTS_JSON = ROOT / 'json' / 'products.json'
SITEMAP = ROOT / 'sitemap.xml'
TOP = ROOT / 'index.html'
DB = ROOT / 'data' / 'sellemy.db'
TOP_START = '<!-- PUBLICATION:RECENT:START -->'
TOP_END = '<!-- PUBLICATION:RECENT:END -->'


def _load(slug: str) -> tuple[dict, dict]:
    payload = json.loads((ROOT / 'data' / 'payloads' / f'{slug}.json').read_text(encoding='utf-8'))
    evidence = json.loads((ROOT / 'data' / 'evidence' / f'{slug}.json').read_text(encoding='utf-8'))
    return payload, evidence


def _article_entry(payload: dict, evidence: dict) -> dict:
    return {'title': payload['h1'], 'slug': payload['slug'], 'category': payload['category'], 'img': evidence['eyecatch_image'], 'summary': payload['summary']}


def _update_articles(payload: dict, evidence: dict, *, allow_existing: bool) -> str:
    rows = json.loads(ARTICLES.read_text(encoding='utf-8'))
    matches = [i for i, row in enumerate(rows) if row.get('slug') == payload['slug']]
    if matches and not allow_existing:
        raise ValueError(f'article slug already exists: {payload["slug"]}')
    if len(matches) > 1:
        raise ValueError(f'article slug is duplicated: {payload["slug"]}')
    if not matches and any(row.get('title') == payload['h1'] for row in rows):
        raise ValueError(f'article title already exists with another slug: {payload["h1"]}')
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
    entry = f'  <url>\n    <loc>{canonical}</loc>\n    <lastmod>{date.today().isoformat()}</lastmod>\n  </url>\n'
    if '</urlset>' not in text:
        raise ValueError('sitemap.xml has no closing urlset tag')
    SITEMAP.write_text(text.replace('</urlset>', entry + '</urlset>', 1), encoding='utf-8')
    return 'added'


def _ensure_product_schema(connection: sqlite3.Connection) -> None:
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS products_asin_unique ON products(asin) WHERE asin IS NOT NULL')
    connection.execute('''CREATE TABLE IF NOT EXISTS product_articles (
        product_id TEXT NOT NULL, article_slug TEXT NOT NULL, category TEXT NOT NULL,
        article_title TEXT NOT NULL, linked_at TEXT NOT NULL,
        PRIMARY KEY(product_id, article_slug),
        FOREIGN KEY(product_id) REFERENCES products(product_id)
    )''')


def _upsert_products(payload: dict, evidence: dict) -> list[str]:
    now = datetime.now(timezone.utc).isoformat()
    payload_by_ref = {p['ref']: p for p in payload['products']}
    canonical_ids = []
    with sqlite3.connect(DB) as connection:
        _ensure_product_schema(connection)
        for product in evidence['products']:
            asin = product['asin'].upper()
            old = connection.execute('SELECT product_id FROM products WHERE asin=?', (asin,)).fetchone()
            product_id = f'AMZ-{asin}'
            if old and old[0] != product_id:
                if connection.execute('SELECT 1 FROM products WHERE product_id=?', (product_id,)).fetchone():
                    raise ValueError(f'canonical product id collision for ASIN {asin}')
                connection.execute('UPDATE products SET product_id=? WHERE product_id=?', (product_id, old[0]))
            values = (
                product['amazon_title'], asin, f'{evidence["slug"]}.html',
                f'https://www.amazon.co.jp/dp/{asin}?tag={evidence["amazon_tag"]}', product['image_url'],
                product.get('brand'), product.get('observed_price'), now if product.get('observed_price') is not None else None,
                product['amazon_title'], product_id,
            )
            if old:
                connection.execute('''UPDATE products SET canonical_name=?,asin=?,article_file=?,amazon_url=?,image_url=?,status='amazon_verified',brand=?,observed_price=COALESCE(?,observed_price),observed_at=COALESCE(?,observed_at),amazon_title=? WHERE product_id=?''', values)
            else:
                connection.execute('''INSERT INTO products (canonical_name,asin,article_file,amazon_url,image_url,status,brand,observed_price,observed_at,amazon_title,product_id) VALUES (?,?,?,?,?,'amazon_verified',?,?,?,?,?)''', values)
            connection.execute('''INSERT INTO product_articles(product_id,article_slug,category,article_title,linked_at) VALUES(?,?,?,?,?) ON CONFLICT(product_id,article_slug) DO UPDATE SET category=excluded.category,article_title=excluded.article_title,linked_at=excluded.linked_at''', (product_id, evidence['slug'], evidence['category'], payload['h1'], now))
            product['product_id'] = product_id
            canonical_ids.append(product_id)
        connection.commit()
    return canonical_ids


def _sync_products_json(payload: dict, evidence: dict) -> dict:
    rows = json.loads(PRODUCTS_JSON.read_text(encoding='utf-8'))
    payload_by_ref = {p['ref']: p for p in payload['products']}
    index = {str(row.get('asin', '')).upper(): i for i, row in enumerate(rows) if row.get('asin')}
    image_index = {row.get('imageUrl'): i for i, row in enumerate(rows) if row.get('imageUrl')}
    added = updated = 0
    for product in evidence['products']:
        copy = payload_by_ref[product['ref']]
        entry = {
            'productId': product['product_id'], 'asin': product['asin'], 'name': copy['h3'],
            'brand': product.get('brand') or '',
            'amazonUrl': f'https://www.amazon.co.jp/dp/{product["asin"]}?tag={evidence["amazon_tag"]}',
            'imageUrl': product['image_url'], 'description': copy['description'],
            'articleSlug': evidence['slug'], 'articleTitle': payload['h1'], 'category': evidence['category'],
        }
        position = index.get(product['asin'], image_index.get(product['image_url']))
        if position is not None:
            rows[position] = entry
            index[product['asin']] = position
            image_index[product['image_url']] = position
            updated += 1
        else:
            index[product['asin']] = len(rows)
            rows.append(entry)
            added += 1
    PRODUCTS_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'added': added, 'updated': updated, 'total': len(rows)}


def _update_top() -> dict:
    text = TOP.read_text(encoding='utf-8')
    old_og = 'https://takayuki-sexy-suzuki.github.io/sellemy/'
    text = text.replace(old_og, 'https://www.sellemy.jp/')
    articles = json.loads(ARTICLES.read_text(encoding='utf-8'))
    cards = []
    for row in reversed(articles[-6:]):
        category = get_category(row['category'])
        cards.append(
            f'<a class="article-card" href="/article/{category.slug}/{html.escape(row["slug"])}.html">'
            f'<img src="/img/{html.escape(row["slug"])}/{html.escape(row["slug"])}.png" alt="{html.escape(row["title"])}" />'
            f'<div class="article-text"><span class="article-category">{category.label}</span><h2>{html.escape(row["title"])}</h2><p class="summary">{html.escape(row["summary"])}</p></div></a>'
        )
    block = f'{TOP_START}\n<section class="recent-publications"><div class="section-title"><h2>新着記事</h2></div><div class="article-list">{"".join(cards)}</div><p><a href="/articles.html">記事一覧を見る</a> · <a href="/products.html">商品一覧から探す</a></p></section>\n{TOP_END}'
    if TOP_START in text and TOP_END in text:
        before, rest = text.split(TOP_START, 1)
        _old, after = rest.split(TOP_END, 1)
        text = before + block + after
    else:
        marker = '<!-- フッター -->'
        if marker not in text:
            raise ValueError('index.html has no footer insertion marker')
        text = text.replace(marker, f'{block}\n\n  {marker}', 1)
    TOP.write_text(text, encoding='utf-8')
    return {'recent_articles': min(6, len(articles)), 'og_url': 'https://www.sellemy.jp/'}


def run(slug: str, *, apply: bool, allow_existing: bool) -> dict:
    payload, evidence = _load(slug)
    rendered = render_article(payload, evidence)
    findings = run_review_gate(payload, evidence)
    qa = run_qa(rendered, payload, evidence)
    result = {'slug': slug, 'review_findings': [repr(finding) for finding in findings], 'qa': qa, 'applied': False}
    if findings or not qa['overall_pass'] or not apply:
        return result

    article_path = ROOT / 'article' / evidence['category'] / f'{slug}.html'
    article_path.write_text(rendered + '\n', encoding='utf-8')
    eyecatch_path = ROOT / 'img' / slug / f'{slug}.png'
    if not eyecatch_path.exists():
        eyecatch_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / get_category(evidence['category']).eyecatch, eyecatch_path)
    result['articles_json'] = _update_articles(payload, evidence, allow_existing=allow_existing)
    result['canonical_product_ids'] = _upsert_products(payload, evidence)
    result['products_json'] = _sync_products_json(payload, evidence)
    result['top'] = _update_top()
    result['sitemap'] = _update_sitemap(evidence)
    result['article_path'] = str(article_path.relative_to(ROOT))
    result['eyecatch_path'] = str(eyecatch_path.relative_to(ROOT))
    result['applied'] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description='Review, QA, render, and stage one complete publication unit.')
    parser.add_argument('slug'); parser.add_argument('--apply', action='store_true'); parser.add_argument('--allow-existing', action='store_true')
    args = parser.parse_args()
    result = run(args.slug, apply=args.apply, allow_existing=args.allow_existing)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['review_findings'] or not result['qa']['overall_pass']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
