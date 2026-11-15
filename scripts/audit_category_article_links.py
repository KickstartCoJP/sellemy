#!/usr/bin/env python3
"""Read-only taxonomy/article match audit; never authorizes a public category link."""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY = Path('/Users/kickstart/sellemy-growth-runtime/data/competitor_categories/sellemy_category_strategy_final_20261012.json')
GENRES = {'beauty': '美容', 'dailygoods': '日用品', 'gadget': '家電'}


def build(taxonomy: dict, articles: list[dict]) -> dict:
    pillars = taxonomy['pillars']
    expected = {'美容': 6, '日用品': 9, '家電': 6}
    if Counter(p['parent_id'].split(':', 1)[1] for p in pillars) != Counter(expected):
        raise ValueError('taxonomy 21 pillar design mismatch')
    tags = [x for x in taxonomy['mapping']
            if x.get('taxonomy_state') == 'active_tag' and x.get('pillar_id')]
    if len(tags) != 720:
        raise ValueError('taxonomy active tag count mismatch')
    by_pillar = {x['id']: [] for x in pillars}
    results = []
    slugs = set()
    for a in articles:
        if a.get('status') != 'published':
            continue
        slug = a['slug']
        if slug in slugs:
            raise ValueError('duplicate published article: ' + slug)
        slugs.add(slug)
        genre = GENRES[a['category']]
        title = a.get('title') or ''
        # Exact contiguous category-label presence: candidate evidence only.
        # Even a unique label can be a substring of an unrelated product.
        hits = sorted(({'category_id': x['canonical_id'], 'category_name': x['name'],
                        'pillar_id': x['pillar_id']} for x in tags
                       if x['genre'] == genre and len(x['name']) >= 3 and x['name'] in title),
                      key=lambda x: (x['category_id'], x['pillar_id']))
        pillars_found = sorted({x['pillar_id'] for x in hits})
        status = 'NO_LITERAL_MATCH' if not hits else ('CANDIDATE_SINGLE_PILLAR' if len(pillars_found) == 1 else 'AMBIGUOUS_PILLAR')
        entry = {'article_slug': slug, 'article_title': title, 'genre': genre,
                 'status': status, 'candidate_pillars': pillars_found,
                 'matched_labels': hits,
                 'publication_eligible': False,
                 'reason': 'taxonomy title match is not an independent category-fit decision'}
        results.append(entry)
        for pillar_id in pillars_found:
            by_pillar[pillar_id].append(slug)
    summary = Counter(r['status'] for r in results)
    return {'schema': 'sellemy-category-association-audit/v1',
            'status': 'CANDIDATES_ONLY_NO_PUBLICATION',
            'taxonomy_source_sha256': taxonomy['source_sha256'],
            'counts': {'published_articles': len(results), 'active_category_tags': len(tags),
                       'pillar_groups': len(pillars), **dict(summary)},
            'pillars': [{'id': p['id'], 'name': p['name'], 'genre': p['parent_id'].split(':', 1)[1],
                         'candidate_articles': len(by_pillar[p['id']]),
                         'approved_articles': 0,
                         'publication_state': 'not_published'} for p in pillars],
            'articles': results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--taxonomy', type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument('--articles', type=Path, default=ROOT/'json/articles.json')
    parser.add_argument('--output', type=Path, default=ROOT/'staging/category-association/coverage.json')
    args = parser.parse_args()
    data = build(json.loads(args.taxonomy.read_text()), json.loads(args.articles.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'counts': data['counts'], 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
