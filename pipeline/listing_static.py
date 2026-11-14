"""Deterministic initial HTML links; existing JS remains for interactive catalogs."""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = '<!-- STATIC:CATALOG:START -->'
END = '<!-- STATIC:CATALOG:END -->'
CATEGORIES = {'beauty': '美容', 'dailygoods': '日用品', 'daily': '日用品', 'gadget': '家電'}


def article_card(row: dict) -> str:
    category = html.escape(str(row['category']), quote=True)
    slug = html.escape(str(row['slug']), quote=True)
    title = html.escape(str(row['title']))
    summary = html.escape(str(row.get('summary') or ''))
    return (
        f'<a class="article-card" href="/article/{category}/{slug}.html">'
        f'<img src="/img/{slug}/{slug}.png" alt="{title}" loading="lazy">'
        f'<div class="article-text"><span class="article-category">{CATEGORIES.get(row["category"], "")}</span>'
        f'<h2>{title}</h2><p class="summary">{summary}</p></div></a>'
    )


def product_card(row: dict) -> str:
    category = html.escape(str(row['category']), quote=True)
    slug = html.escape(str(row['articleSlug']), quote=True)
    name = html.escape(str(row['name']))
    img = html.escape(str(row['imageUrl']), quote=True)
    title = html.escape(str(row.get('articleTitle') or ''))
    return (
        f'<a class="item-card" href="/article/{category}/{slug}.html">'
        f'<img src="{img}" alt="{name}" loading="lazy">'
        f'<span class="article-category">{CATEGORIES.get(row["category"], "")}</span>'
        f'<h3>{name}</h3><p>{title}</p></a>'
    )


def update(page: Path, rows: list[dict], *, kind: str) -> int:
    text = page.read_text(encoding='utf-8')
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError(f'catalog markers missing or duplicated: {page}')
    before, tail = text.split(START, 1)
    _, after = tail.split(END, 1)
    item = article_card if kind == 'article' else product_card
    cards = ''.join(item(row) for row in rows)
    output = before + START + '\n' + cards + '\n' + END + after
    if text != output:
        page.write_text(output, encoding='utf-8')
    return len(rows)


def sync(*, root: Path = ROOT) -> dict:
    articles = json.loads((root / 'json/articles.json').read_text(encoding='utf-8'))
    products = json.loads((root / 'json/products.json').read_text(encoding='utf-8'))
    article_count = update(root / 'articles.html', articles, kind='article')
    product_count = update(root / 'products.html', products, kind='product')
    return {'articles': article_count, 'products': product_count}


if __name__ == '__main__':
    print(json.dumps(sync(), ensure_ascii=False))
