from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'sellemy.db'
ARTICLES = ROOT / 'json' / 'articles.json'
RELATION_SEED = ROOT / 'config' / 'purpose_relation_seed.json'
FEATURE_SEED = ROOT / 'config' / 'purpose_features_seed.json'
SITEMAP = ROOT / 'sitemap.xml'
TOP = ROOT / 'index.html'
THEMES = ROOT / 'themes'
BASE = 'https://www.sellemy.jp'
START = '<!-- PURPOSE_RELATED_FEATURES_START -->'
END = '<!-- PURPOSE_RELATED_FEATURES_END -->'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_articles() -> list[dict[str, Any]]:
    raw = json.loads(ARTICLES.read_text(encoding='utf-8'))
    return raw if isinstance(raw, list) else raw.get('articles', [])


def _article_id(row: dict[str, Any]) -> str:
    return str(row.get('article_id') or row.get('id') or row['slug'])


def _load_feature(feature_id: str) -> dict[str, Any]:
    raw = json.loads(FEATURE_SEED.read_text(encoding='utf-8'))
    matches = [x for x in raw.get('features', []) if x.get('feature_id') == feature_id]
    if len(matches) != 1:
        raise ValueError(f'feature seed not found or ambiguous: {feature_id}')
    return matches[0]


def _load_relations(feature_id: str) -> list[dict[str, Any]]:
    raw = json.loads(RELATION_SEED.read_text(encoding='utf-8'))
    return [r for r in raw.get('relations', []) if r.get('feature_id') == feature_id]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS articles (
      article_id TEXT PRIMARY KEY,
      slug TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL,
      category TEXT NOT NULL,
      summary TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL,
      published_at TEXT,
      updated_at TEXT,
      semantic_metadata_json TEXT NOT NULL DEFAULT '{}',
      metadata_updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS features (
      feature_id TEXT PRIMARY KEY,
      slug TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL,
      purpose_statement TEXT NOT NULL,
      scope_include TEXT NOT NULL,
      scope_exclude TEXT NOT NULL,
      intro TEXT NOT NULL,
      lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('draft','published','archived')),
      navigation_visibility TEXT NOT NULL CHECK(navigation_visibility IN ('promoted','listed','hidden')),
      seo_state TEXT NOT NULL CHECK(seo_state IN ('index','noindex')),
      seo_title TEXT NOT NULL,
      meta_description TEXT NOT NULL,
      hero_image TEXT NOT NULL DEFAULT '',
      editorial_priority INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS article_feature_relations (
      article_id TEXT NOT NULL,
      feature_id TEXT NOT NULL,
      relation_type TEXT NOT NULL CHECK(relation_type IN ('core','supporting')),
      relevance_score REAL,
      confidence REAL,
      display_order INTEGER NOT NULL DEFAULT 0,
      rationale TEXT NOT NULL DEFAULT '',
      source TEXT NOT NULL CHECK(source IN ('manual','runtime')),
      first_linked_at TEXT NOT NULL,
      last_revalidated_at TEXT NOT NULL,
      review_candidate INTEGER NOT NULL DEFAULT 0,
      review_reason TEXT NOT NULL DEFAULT '',
      active INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(article_id, feature_id),
      FOREIGN KEY(article_id) REFERENCES articles(article_id),
      FOREIGN KEY(feature_id) REFERENCES features(feature_id)
    );
    CREATE INDEX IF NOT EXISTS idx_article_feature_relations_feature
      ON article_feature_relations(feature_id, active, relation_type, display_order);
    CREATE INDEX IF NOT EXISTS idx_article_feature_relations_article
      ON article_feature_relations(article_id, active, display_order);
    ''')


def sync_articles(conn: sqlite3.Connection) -> int:
    rows = [r for r in _load_articles() if r.get('slug')]
    for row in rows:
        semantic = {
            k: row.get(k)
            for k in ('purpose', 'problem', 'use_case', 'season', 'target', 'environment', 'product_type', 'keywords')
            if row.get(k) not in (None, '', [])
        }
        conn.execute('''
        INSERT INTO articles(article_id,slug,title,category,summary,status,published_at,updated_at,semantic_metadata_json,metadata_updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(article_id) DO UPDATE SET
          slug=excluded.slug,title=excluded.title,category=excluded.category,summary=excluded.summary,status=excluded.status,
          published_at=excluded.published_at,updated_at=excluded.updated_at,semantic_metadata_json=excluded.semantic_metadata_json,
          metadata_updated_at=excluded.metadata_updated_at
        ''', (
            _article_id(row), row['slug'], row.get('title') or row['slug'], row.get('category') or '', row.get('summary') or '',
            row.get('status') or '', row.get('published_at'), row.get('updated_at'), json.dumps(semantic, ensure_ascii=False), _now(),
        ))
    return len(rows)


def upsert_feature(conn: sqlite3.Connection, feature: dict[str, Any]) -> None:
    now = _now()
    existing = conn.execute('SELECT created_at FROM features WHERE feature_id=?', (feature['feature_id'],)).fetchone()
    created = existing[0] if existing else now
    conn.execute('''
    INSERT INTO features(feature_id,slug,title,purpose_statement,scope_include,scope_exclude,intro,lifecycle_status,navigation_visibility,seo_state,seo_title,meta_description,hero_image,editorial_priority,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(feature_id) DO UPDATE SET
      slug=excluded.slug,title=excluded.title,purpose_statement=excluded.purpose_statement,scope_include=excluded.scope_include,
      scope_exclude=excluded.scope_exclude,intro=excluded.intro,lifecycle_status=excluded.lifecycle_status,
      navigation_visibility=excluded.navigation_visibility,seo_state=excluded.seo_state,seo_title=excluded.seo_title,
      meta_description=excluded.meta_description,hero_image=excluded.hero_image,editorial_priority=excluded.editorial_priority,
      updated_at=excluded.updated_at
    ''', (
        feature['feature_id'], feature['slug'], feature['title'], feature['purpose_statement'], feature['scope_include'],
        feature['scope_exclude'], feature['intro'], feature['lifecycle_status'], feature['navigation_visibility'], feature['seo_state'],
        feature['seo_title'], feature['meta_description'], feature.get('hero_image') or '', int(feature.get('editorial_priority') or 0), created, now,
    ))


def upsert_relations(conn: sqlite3.Connection, feature: dict[str, Any], relations: list[dict[str, Any]]) -> int:
    rows = {r['slug']: r for r in _load_articles() if r.get('slug')}
    now = _now()
    count = 0
    for order, rel in enumerate(relations, 1):
        article = rows.get(rel['slug'])
        if not article or article.get('status') != 'published':
            raise ValueError(f'accepted relation article is not published: {rel["slug"]}')
        article_id = _article_id(article)
        existing = conn.execute(
            'SELECT first_linked_at FROM article_feature_relations WHERE article_id=? AND feature_id=?',
            (article_id, feature['feature_id'])
        ).fetchone()
        first = existing[0] if existing else now
        confidence = rel.get('confidence')
        if confidence is None:
            confidence = 0.90 if rel.get('relation_type') == 'core' else 0.82
        score = 1.0 if rel.get('relation_type') == 'core' else 0.8
        conn.execute('''
        INSERT INTO article_feature_relations(article_id,feature_id,relation_type,relevance_score,confidence,display_order,rationale,source,first_linked_at,last_revalidated_at,review_candidate,review_reason,active)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)
        ON CONFLICT(article_id,feature_id) DO UPDATE SET
          relation_type=excluded.relation_type,relevance_score=excluded.relevance_score,confidence=excluded.confidence,
          display_order=excluded.display_order,rationale=excluded.rationale,source=excluded.source,
          last_revalidated_at=excluded.last_revalidated_at,review_candidate=0,review_reason='',active=1
        ''', (
            article_id, feature['feature_id'], rel['relation_type'], score, float(confidence), order,
            rel.get('rationale') or 'Owner-accepted semantic review state', 'manual', first, now, 0, '',
        ))
        count += 1
    return count


def article_related_features(slug: str, *, db_path: Path = DB) -> list[dict[str, str]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute('''
          SELECT f.feature_id,f.slug,f.title,r.relation_type
          FROM articles a
          JOIN article_feature_relations r ON r.article_id=a.article_id AND r.active=1
          JOIN features f ON f.feature_id=r.feature_id
          WHERE a.slug=? AND f.lifecycle_status='published' AND f.navigation_visibility!='hidden'
          ORDER BY CASE r.relation_type WHEN 'core' THEN 0 ELSE 1 END, f.editorial_priority DESC, f.feature_id
          LIMIT 3
        ''', (slug,)).fetchall()
        return [
            {'feature_id': row[0], 'slug': row[1], 'title': row[2], 'relation_type': row[3], 'url': f'/themes/{row[1]}.html'}
            for row in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def render_related_features(features: list[dict[str, str]]) -> str:
    inner = ''
    if features:
        links = ''.join(
            f'<li><a href="{html.escape(f["url"])}">{html.escape(f["title"])}</a></li>' for f in features
        )
        inner = f'<aside class="related-features"><h2>関連する目的</h2><ul>{links}</ul></aside>'
    return f'{START}{inner}{END}'


def replace_related_features_block(text: str, features: list[dict[str, str]]) -> str:
    block = render_related_features(features)
    if START in text and END in text:
        before, rest = text.split(START, 1)
        _old, after = rest.split(END, 1)
        return before + block + after
    marker = '</main>'
    if marker not in text:
        raise ValueError('article has no </main> marker')
    return text.replace(marker, block + marker, 1)


def _article_path(row: dict[str, Any]) -> Path:
    return ROOT / 'article' / row['category'] / f'{row["slug"]}.html'


def render_feature_page(conn: sqlite3.Connection, feature_id: str) -> str:
    feature = conn.execute('''SELECT slug,title,purpose_statement,intro,seo_title,meta_description FROM features WHERE feature_id=?''', (feature_id,)).fetchone()
    if not feature:
        raise ValueError(feature_id)
    slug, title, purpose, intro, seo_title, meta_description = feature
    rows = conn.execute('''
      SELECT a.slug,a.title,a.category,a.summary,r.relation_type,r.display_order
      FROM article_feature_relations r JOIN articles a ON a.article_id=r.article_id
      WHERE r.feature_id=? AND r.active=1 AND a.status='published'
      ORDER BY CASE r.relation_type WHEN 'core' THEN 0 ELSE 1 END,r.display_order,a.slug
    ''', (feature_id,)).fetchall()
    def card(row: tuple) -> str:
        a_slug, a_title, category, summary, _typ, _order = row
        return (
            f'<a class="feature-article-card" href="/article/{html.escape(category)}/{html.escape(a_slug)}.html">'
            f'<img src="/img/{html.escape(a_slug)}/{html.escape(a_slug)}.png" alt="{html.escape(a_title)}" />'
            f'<div><h3>{html.escape(a_title)}</h3><p>{html.escape(summary)}</p></div></a>'
        )
    core = ''.join(card(row) for row in rows if row[4] == 'core')
    supporting = ''.join(card(row) for row in rows if row[4] == 'supporting')
    canonical = f'{BASE}/themes/{slug}.html'
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />'
        f'<title>{html.escape(seo_title)}</title><meta name="description" content="{html.escape(meta_description)}" />'
        f'<link rel="canonical" href="{canonical}" /><link rel="stylesheet" href="/css/style.css" />'
        '<script async src="https://www.googletagmanager.com/gtag/js?id=G-SZ5RQR5H7L"></script><script src="/js/ga4.js"></script>'
        '</head><body class="feature-detail" data-category="themes">'
        '<header class="site-header"><div class="brand"><a href="/index.html"><img src="/img/sellemy-logo.png" alt="Sellemyロゴ" class="logo" /></a>'
        '<span class="tagline">選びやすくて、わたしにちょうどいい情報ガイド</span></div></header><main>'
        f'<nav class="breadcrumb"><ul><li><a href="/index.html">Top</a></li><li><a href="/themes/">目的から探す</a></li><li><span>{html.escape(title)}</span></li></ul></nav>'
        f'<div class="section-title"><h1>{html.escape(title)}</h1></div><p class="feature-purpose">{html.escape(purpose)}</p><p class="lead">{html.escape(intro)}</p>'
        f'<section><div class="section-title"><h2>まず整えたいアイテム</h2></div><div class="feature-article-list">{core}</div></section>'
        + (f'<section><div class="section-title"><h2>周辺環境も整える</h2></div><div class="feature-article-list">{supporting}</div></section>' if supporting else '')
        + '</main><footer><p>&copy; 2026 Sellemy. All rights reserved.</p></footer></body></html>'
    )


def render_themes_index(conn: sqlite3.Connection) -> str:
    rows = conn.execute('''
      SELECT slug,title,purpose_statement FROM features
      WHERE lifecycle_status='published' AND navigation_visibility IN ('promoted','listed')
      ORDER BY editorial_priority DESC,title
    ''').fetchall()
    cards = ''.join(
        f'<a class="feature-card" href="/themes/{html.escape(slug)}.html"><h2>{html.escape(title)}</h2><p>{html.escape(purpose)}</p></a>'
        for slug, title, purpose in rows
    )
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />'
        '<title>目的から探す - Sellemy</title><meta name="description" content="暮らしの中で良くしたいこと・整えたいことから、Sellemyの比較記事を探せる特集ページです。" />'
        f'<link rel="canonical" href="{BASE}/themes/" /><link rel="stylesheet" href="/css/style.css" />'
        '<script async src="https://www.googletagmanager.com/gtag/js?id=G-SZ5RQR5H7L"></script><script src="/js/ga4.js"></script>'
        '</head><body class="feature-index" data-category="themes"><header class="site-header"><div class="brand">'
        '<a href="/index.html"><img src="/img/sellemy-logo.png" alt="Sellemyロゴ" class="logo" /></a>'
        '<span class="tagline">選びやすくて、わたしにちょうどいい情報ガイド</span></div></header><main>'
        '<nav class="breadcrumb"><ul><li><a href="/index.html">Top</a></li><li><span>目的から探す</span></li></ul></nav>'
        '<div class="section-title"><h1>暮らしを、もう少し快適に</h1></div><p class="lead">商品カテゴリではなく、「何を良くしたいか」から比較記事を探せます。</p>'
        f'<div class="feature-card-list">{cards}</div></main><footer><p>&copy; 2026 Sellemy. All rights reserved.</p></footer></body></html>'
    )


def _ensure_top_link() -> None:
    text = TOP.read_text(encoding='utf-8')
    link = '<li><a href="/themes/" class="nav-link" data-name="themes">目的から探す</a></li>'
    if link in text:
        return
    marker = '<li><a href="/articles.html" class="nav-link" data-name="articles">記事一覧</a></li>'
    if marker not in text:
        raise ValueError('TOP nav insertion marker missing')
    TOP.write_text(text.replace(marker, link + '\n        ' + marker, 1), encoding='utf-8')


def _ensure_sitemap_urls(urls: list[str]) -> None:
    text = SITEMAP.read_text(encoding='utf-8')
    marker = '</urlset>'
    if marker not in text:
        raise ValueError('sitemap.xml has no closing urlset tag')
    today = datetime.now().date().isoformat()
    blocks=[]
    for url in urls:
        if f'<loc>{url}</loc>' in text:
            continue
        blocks.append(
            '  <url>\n'
            f'    <loc>{url}</loc>\n'
            f'    <lastmod>{today}</lastmod>\n'
            '  </url>'
        )
    if not blocks:
        return
    insertion='\n'.join(blocks) + '\n'
    text=text.replace(marker, insertion + marker, 1)
    SITEMAP.write_text(text, encoding='utf-8')


def _ensure_css() -> None:
    css = ROOT / 'css' / 'style.css'
    text = css.read_text(encoding='utf-8')
    marker = '/* ========= Purpose Features ========= */'
    if marker in text:
        return
    text += '''\n\n/* ========= Purpose Features ========= */\n.feature-index main,.feature-detail main{max-width:1000px;margin:0 auto;padding:2rem;line-height:1.8}.feature-card-list,.feature-article-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1.25rem;margin:1.5rem 0 2.5rem}.feature-card,.feature-article-card{display:block;background:#fff;border:1px solid #eee;border-radius:8px;padding:1rem;text-decoration:none;color:#333}.feature-card:hover,.feature-article-card:hover{box-shadow:0 0 8px rgba(0,0,0,.08)}.feature-article-card img{width:100%;height:180px;object-fit:contain;border-radius:6px}.feature-article-card h3{font-size:1.05rem;margin:.8rem 0 .4rem}.feature-article-card p,.feature-card p{font-size:.9rem;color:#666}.feature-purpose{font-weight:600;color:#555}.related-features{margin:2rem 0 0;padding:1rem 1.2rem;border-top:1px solid #eee;background:#fafafa;border-radius:8px}.related-features h2{font-size:1rem!important;background:none!important;border:0!important;padding:0!important;margin:0 0 .5rem!important;color:#666!important}.related-features ul{margin:0;padding-left:1.2rem}.related-features a{color:#6d514c;text-decoration:underline}\n'''
    css.write_text(text, encoding='utf-8')


def apply_feature_slice(feature_id: str) -> dict[str, Any]:
    feature = _load_feature(feature_id)
    relations = _load_relations(feature_id)
    if not relations:
        raise ValueError(f'no reviewed relations: {feature_id}')
    backup_dir = Path.home() / 'Library/Application Support/Sellemy/purpose-feature-backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    backup = backup_dir / f'sellemy-before-{feature_id}-{stamp}.db'
    shutil.copy2(DB, backup)
    before_hash = _sha256(DB)
    conn = sqlite3.connect(DB)
    try:
        ensure_schema(conn)
        synced = sync_articles(conn)
        upsert_feature(conn, feature)
        relation_count = upsert_relations(conn, feature, relations)
        conn.commit()
        THEMES.mkdir(parents=True, exist_ok=True)
        (THEMES / f'{feature["slug"]}.html').write_text(render_feature_page(conn, feature_id) + '\n', encoding='utf-8')
        (THEMES / 'index.html').write_text(render_themes_index(conn) + '\n', encoding='utf-8')
        article_rows = {r['slug']: r for r in _load_articles() if r.get('slug')}
        patched = []
        for rel in relations:
            row = article_rows[rel['slug']]
            path = _article_path(row)
            current = path.read_text(encoding='utf-8')
            features = article_related_features(row['slug'])
            updated = replace_related_features_block(current, features)
            if updated != current:
                path.write_text(updated, encoding='utf-8')
                patched.append(str(path.relative_to(ROOT)))
        _ensure_top_link()
        _ensure_sitemap_urls([f'{BASE}/themes/', f'{BASE}/themes/{feature["slug"]}.html'])
        _ensure_css()
        feature_row = conn.execute('SELECT feature_id,slug,title,lifecycle_status,navigation_visibility,seo_state FROM features WHERE feature_id=?', (feature_id,)).fetchone()
        relation_readback = conn.execute('SELECT COUNT(*),SUM(relation_type="core"),SUM(relation_type="supporting") FROM article_feature_relations WHERE feature_id=? AND active=1', (feature_id,)).fetchone()
    finally:
        conn.close()
    return {
        'feature_id': feature_id,
        'db_backup': str(backup),
        'db_before_sha256': before_hash,
        'db_after_sha256': _sha256(DB),
        'articles_synced': synced,
        'feature_readback': feature_row,
        'relation_readback': {'total': relation_readback[0], 'core': relation_readback[1], 'supporting': relation_readback[2]},
        'article_pages_patched': patched,
        'theme_page': f'themes/{feature["slug"]}.html',
        'theme_index': 'themes/index.html',
        'production_push': False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Apply one controlled Purpose Feature production-ready slice without pushing it.')
    parser.add_argument('--feature-id', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        feature = _load_feature(args.feature_id)
        relations = _load_relations(args.feature_id)
        print(json.dumps({'feature': feature, 'relation_count': len(relations), 'apply': False}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(apply_feature_slice(args.feature_id), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
