#!/usr/bin/env python3
"""Stage semantic article-to-pillar assignments and noindex category page previews.

Requires actual classifier JSON with an explicit decision for each published article.
Never treats a model decision as an independently certified publication mapping.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = Path('/Users/kickstart/sellemy-growth-runtime/data/competitor_categories/sellemy_category_strategy_final_20261012.json')
GENRES = {'beauty': '美容', 'dailygoods': '日用品', 'gadget': '家電'}


def combine(taxonomy: dict, articles: list[dict], results: dict[str, dict]) -> dict:
    pillars = {p['id']: p for p in taxonomy['pillars']}
    published = [a for a in articles if a.get('status') == 'published']
    if set(results) != {a['slug'] for a in published}:
        missing = set(a['slug'] for a in published) - set(results)
        extra = set(results) - set(a['slug'] for a in published)
        raise ValueError(f'incomplete semantic review missing={len(missing)} extra={len(extra)}')
    if len(published) != len(results):
        raise ValueError('duplicate published articles')
    entries = []
    for article in published:
        slug = article['slug']
        decision = results[slug]
        pillar_id = decision['pillar_id']
        confidence = decision['confidence']
        if pillar_id != 'HOLD':
            if pillar_id not in pillars:
                raise ValueError('unknown pillar for ' + slug)
            if pillars[pillar_id]['parent_id'] != 'genre:' + GENRES[article['category']]:
                raise ValueError('cross-genre category decision for ' + slug)
        if confidence not in {'high', 'medium', 'low'}:
            raise ValueError('unsupported semantic confidence for ' + slug)
        reason = decision['reason']
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('missing classification rationale for ' + slug)
        # High confidence is sufficient for a preview link, never a public mapping.
        preview_ready = pillar_id != 'HOLD' and confidence == 'high'
        entries.append({
            'slug': slug,
            'title': article['title'],
            'genre': GENRES[article['category']],
            'article_url': f'/article/{article["category"]}/{slug}.html',
            'image_url': article.get('img') or '',
            'pillar_id': pillar_id,
            'confidence': confidence,
            'classification_reason': reason,
            'preview_link_eligible': preview_ready,
            'public_mapping_approved': False,
            'classification_state': 'SEMANTIC_PREVIEW_CANDIDATE' if preview_ready else 'HOLD_FOR_EDITORIAL_REVIEW',
        })
    group_counts = Counter(e['pillar_id'] for e in entries if e['preview_link_eligible'])
    state_counts = Counter(e['classification_state'] for e in entries)
    return {
        'schema': 'sellemy-semantic-category-associations/v1',
        'status': 'STAGING_PREVIEW_ONLY',
        'method': 'Product-type semantic candidate assignments from published title/slug; independent editorial QA pending',
        'taxonomy_sha256': hashlib.sha256(json.dumps(taxonomy['pillars'],ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        'counts': {'published_articles': len(published), 'pillar_groups': len(pillars),
                   'preview_candidates': state_counts['SEMANTIC_PREVIEW_CANDIDATE'],
                   'editorial_hold': state_counts['HOLD_FOR_EDITORIAL_REVIEW'],
                   'public_mappings_approved': 0},
        'pillars': [{'id': p['id'], 'name': p['name'], 'genre': p['parent_id'].split(':', 1)[1],
                     'preview_article_count': group_counts[p['id']],
                     'public_page_enabled': False} for p in taxonomy['pillars']],
        'articles': entries,
    }


def render_pages(report: dict, outroot: Path) -> list[Path]:
    from collections import defaultdict
    # Only pages created by this staging generator are removed before a rerun.
    # The real article/genre pages are outside this directory.
    if outroot.exists():
        for stale in outroot.rglob('*.html'):
            stale.unlink()
    by_pillar = defaultdict(list)
    for entry in report['articles']:
        if entry['preview_link_eligible']:
            by_pillar[entry['pillar_id']].append(entry)
    created = []
    for pillar in report['pillars']:
        articles = by_pillar.get(pillar['id'], [])
        if len(articles) < 3:
            continue
        genre = pillar['genre']
        category = next(key for key, value in GENRES.items() if value == genre)
        stem = pillar['id'].rsplit(':', 1)[1]
        path = outroot / category / (stem + '.html')
        cards = '\n'.join(
            '<a class="article-card" href="' + html.escape(a['article_url'],quote=True) + '">'
            '<img loading="lazy" src="' + html.escape(a['image_url'],quote=True) + '" alt="">'
            '<span class="article-text"><strong>' + html.escape(a['title']) + '</strong></span></a>'
            for a in sorted(articles, key=lambda x: x['slug'])
        )
        heading = html.escape(pillar['name'])
        page = f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{heading}｜Sellemy 検証プレビュー</title>
<link rel="stylesheet" href="/css/style.css"><style>
.category-review {{max-width:1080px;margin:36px auto;padding:0 18px}}
.review-notice {{font-size:.9rem;color:#695b37;padding:12px;background:#fff4e6;border-radius:6px}}
.article-grid {{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:24px 0}}
.article-grid .article-card {{display:flex;gap:12px;align-items:flex-start;text-decoration:none;color:inherit;border:1px solid #eee;border-radius:8px;padding:12px}}
.article-grid img {{width:80px;height:80px;object-fit:cover;flex-shrink:0}}
.article-text strong {{font-size:.95rem;line-height:1.5}}
</style></head><body><main class="category-review">
<p class="review-notice">STAGING PREVIEW：記事とカテゴリの意味分類の確認用。本番ページではありません。</p>
<p><a href="/article/{category}/">{html.escape(genre)}の記事一覧</a></p>
<h1>{heading}</h1><p>このテーマに関連する記事の候補を紹介しています。</p>
<div class="article-grid">{cards}</div>
</main></body></html>
'''
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page)
        created.append(path)
    return created


def load_results(paths: dict[str, Path]) -> dict[str, dict]:
    results = {}
    for category, path in paths.items():
        data = json.loads(path.read_text())
        assignments = data.get('assignments')
        if not isinstance(assignments, list):
            raise ValueError('missing model assignments for '+category)
        for item in assignments:
            slug = item.get('slug')
            if slug in results:
                raise ValueError('duplicate model assignment '+str(slug))
            results[slug] = item
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--method', default='model-semantic-review')
    ap.add_argument('--curation', type=Path)
    ap.add_argument('--beauty', type=Path, required=True)
    ap.add_argument('--dailygoods', type=Path, required=True)
    ap.add_argument('--gadget', type=Path, required=True)
    ap.add_argument('--output', type=Path, default=ROOT/'staging/category-association/semantic-reviewed.json')
    ap.add_argument('--preview-root', type=Path, default=ROOT/'preview/category-pages')
    args = ap.parse_args()
    decisions = load_results({cat: getattr(args,cat) for cat in GENRES})
    applied = []
    if args.curation:
        curated = json.loads(args.curation.read_text())
        if curated.get('schema') != 'sellemy-category-semantic-curation/v1':
            raise ValueError('invalid curation schema')
        for slug, override in curated['overrides'].items():
            if slug not in decisions:
                raise ValueError('curation refers to unknown article '+slug)
            decisions[slug] = {**decisions[slug], **override}
            applied.append(slug)
    data = combine(json.loads(TAXONOMY.read_text()), json.loads((ROOT/'json/articles.json').read_text()), decisions)
    data['classification_method'] = args.method
    data['curation_applied_to'] = applied
    pages = render_pages(data,args.preview_root)
    data['preview_pages'] = [str(p.relative_to(ROOT)) for p in pages]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'counts':data['counts'],'preview_pages':len(pages)},ensure_ascii=False))

if __name__ == '__main__':
    main()
