from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'json' / 'articles.json'
SITEMAP = ROOT / 'sitemap.xml'
OUTPUT = Path.home() / 'Library' / 'Application Support' / 'Sellemy' / 'analytics' / 'article_publication_metrics.json'
JST = ZoneInfo('Asia/Tokyo')
BASE = 'https://www.sellemy.jp'


def _sitemap_articles() -> dict[str, str]:
    root = ET.parse(SITEMAP).getroot()
    ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    result: dict[str, str] = {}
    for node in root.findall('s:url', ns):
        loc = node.findtext('s:loc', default='', namespaces=ns)
        lastmod = node.findtext('s:lastmod', default='', namespaces=ns)
        if '/article/' not in loc or not loc.endswith('.html'):
            continue
        if not lastmod:
            raise ValueError(f'sitemap article has no lastmod: {loc}')
        result[loc.rsplit('/', 1)[1][:-5]] = lastmod
    return result


def _timestamp_from_lastmod(value: str) -> str:
    parsed = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=JST)
    return parsed.isoformat()


def _inject_html_times(path: Path, published_at: str, updated_at: str) -> bool:
    text = path.read_text(encoding='utf-8')
    p = html.escape(published_at, quote=True)
    u = html.escape(updated_at, quote=True)
    pub_tag = f'<meta property="article:published_time" content="{p}">'
    mod_tag = f'<meta property="article:modified_time" content="{u}">'
    changed = False
    if '<meta property="article:published_time"' in text:
        new = re.sub(r'<meta property="article:published_time" content="[^"]*">', pub_tag, text, count=1)
    else:
        if '</head>' not in text:
            raise ValueError(f'article has no closing head: {path}')
        new = text.replace('</head>', pub_tag + '\n' + mod_tag + '\n</head>', 1)
        changed = new != text
        if changed:
            path.write_text(new, encoding='utf-8')
        return changed
    if new != text:
        changed = True
        text = new
    if '<meta property="article:modified_time"' in text:
        new = re.sub(r'<meta property="article:modified_time" content="[^"]*">', mod_tag, text, count=1)
    else:
        new = text.replace('</head>', mod_tag + '\n</head>', 1)
    if new != text:
        changed = True
    if changed:
        path.write_text(new, encoding='utf-8')
    return changed


def backfill() -> dict:
    rows = json.loads(ARTICLES.read_text(encoding='utf-8'))
    sitemap = _sitemap_articles()
    by_slug = {row.get('slug'): row for row in rows}
    missing_rows = sorted(set(sitemap) - set(by_slug))
    if missing_rows:
        raise ValueError(f'sitemap slugs missing from articles.json: {missing_rows}')

    updated_rows = 0
    updated_html = 0
    preserved = 0
    for slug, lastmod in sitemap.items():
        row = by_slug[slug]
        timestamp = _timestamp_from_lastmod(lastmod)
        if row.get('published_at'):
            preserved += 1
        else:
            row['article_id'] = row.get('article_id') or slug
            row['status'] = 'published'
            row['published_at'] = timestamp
            row['updated_at'] = row.get('updated_at') or timestamp
            row['published_at_source'] = 'sitemap_lastmod_backfill'
            updated_rows += 1
        category = row['category']
        article_path = ROOT / 'article' / category / f'{slug}.html'
        if not article_path.exists():
            raise ValueError(f'published article file missing: {article_path}')
        if _inject_html_times(article_path, row['published_at'], row['updated_at']):
            updated_html += 1

    # Do not invent publication dates for rows that are not in the production sitemap.
    for row in rows:
        if row.get('slug') in sitemap:
            continue
        row.setdefault('article_id', row.get('slug'))
        row.setdefault('status', 'unlisted')
        row.setdefault('published_at', None)
        row.setdefault('updated_at', None)
        row.setdefault('published_at_source', None)

    ARTICLES.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {
        'sitemap_articles': len(sitemap),
        'backfilled_rows': updated_rows,
        'preserved_rows': preserved,
        'html_updated': updated_html,
        'unlisted_without_invented_date': sum(1 for row in rows if row.get('status') != 'published'),
    }


def generate() -> dict:
    rows = json.loads(ARTICLES.read_text(encoding='utf-8'))
    published = [row for row in rows if row.get('status') == 'published' and row.get('published_at')]
    missing_published_at = [row.get('slug') for row in rows if row.get('status') == 'published' and not row.get('published_at')]
    if missing_published_at:
        raise ValueError(f'published articles missing published_at: {missing_published_at}')
    dates = []
    seen_ids = set()
    for row in published:
        article_id = row.get('article_id') or row.get('slug')
        if article_id in seen_ids:
            raise ValueError(f'duplicate article_id: {article_id}')
        seen_ids.add(article_id)
        dt = datetime.fromisoformat(row['published_at'])
        if dt.tzinfo is None:
            raise ValueError(f'published_at has no timezone: {article_id}')
        dates.append(dt.astimezone(JST).date().isoformat())
    daily_counter = Counter(dates)
    cumulative = 0
    daily = []
    for day in sorted(daily_counter):
        count = daily_counter[day]
        cumulative += count
        daily.append({'date': day, 'published_article_count_daily': count, 'published_article_count': cumulative})
    doc = {
        'source': 'Sellemy article metadata',
        'generated_at': datetime.now(JST).isoformat(),
        'timezone': 'Asia/Tokyo',
        'metric_ids': ['published_article_count_daily', 'published_article_count'],
        'published_articles': len(published),
        'unlisted_or_nonpublished_articles': len(rows) - len(published),
        'daily': daily,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return doc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--backfill', action='store_true')
    args = parser.parse_args()
    if args.backfill:
        print(json.dumps(backfill(), ensure_ascii=False))
    print(json.dumps(generate(), ensure_ascii=False))


if __name__ == '__main__':
    main()
