from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

import publish_payload
from fixtures import valid_evidence, valid_payload


class PublicationUnitTests(unittest.TestCase):
    def test_top_uses_current_domain_and_recent_articles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            top = root / 'index.html'; top.write_text('<meta property="og:url" content="https://takayuki-sexy-suzuki.github.io/sellemy/" /><!-- フッター -->')
            articles = root / 'articles.json'; articles.write_text(json.dumps([{'slug': 'new-one', 'category': 'beauty', 'title': '新着', 'summary': '要約'}]))
            with patch.object(publish_payload, 'TOP', top), patch.object(publish_payload, 'ARTICLES', articles):
                result = publish_payload._update_top()
            text = top.read_text()
        self.assertNotIn('takayuki-sexy-suzuki.github.io', text)
        self.assertIn('/article/beauty/new-one.html', text)
        self.assertIn('/products.html', text)
        self.assertEqual(result['recent_articles'], 1)


    def test_new_article_sets_publish_event_and_update_preserves_published_at(self):
        payload, evidence = valid_payload(), valid_evidence()
        with tempfile.TemporaryDirectory() as tmp:
            articles = Path(tmp) / 'articles.json'; articles.write_text('[]')
            first_time = datetime(2026, 9, 18, 8, 0, tzinfo=ZoneInfo('Asia/Tokyo'))
            second_time = datetime(2026, 9, 19, 9, 30, tzinfo=ZoneInfo('Asia/Tokyo'))
            with patch.object(publish_payload, 'ARTICLES', articles):
                first = publish_payload._update_articles(payload, evidence, allow_existing=False, now=first_time)
                second = publish_payload._update_articles(payload, evidence, allow_existing=True, now=second_time)
            row = json.loads(articles.read_text())[0]
        self.assertEqual(first['metadata']['published_at'], first_time.isoformat())
        self.assertEqual(first['metadata']['published_at_source'], 'publish_event')
        self.assertEqual(second['metadata']['published_at'], first_time.isoformat())
        self.assertEqual(second['metadata']['updated_at'], second_time.isoformat())
        self.assertEqual(row['published_at'], first_time.isoformat())
        self.assertEqual(row['updated_at'], second_time.isoformat())

    def test_sitemap_lastmod_tracks_updated_at_and_html_exposes_article_times(self):
        evidence = valid_evidence()
        with tempfile.TemporaryDirectory() as tmp:
            sitemap = Path(tmp) / 'sitemap.xml'
            sitemap.write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>')
            updated_at = '2026-09-19T00:30:00+09:00'
            with patch.object(publish_payload, 'SITEMAP', sitemap):
                action = publish_payload._update_sitemap(evidence, updated_at=updated_at)
            rendered = '<html><head><title>x</title></head><body></body></html>'
            metadata = {'published_at': '2026-09-18T08:00:00+09:00', 'updated_at': updated_at}
            html_text = publish_payload._inject_article_times(rendered, metadata)
            sitemap_text = sitemap.read_text()
        self.assertEqual(action, 'added')
        self.assertIn('<lastmod>2026-09-19</lastmod>', sitemap_text)
        self.assertIn('article:published_time', html_text)
        self.assertIn('2026-09-18T08:00:00+09:00', html_text)
        self.assertIn('article:modified_time', html_text)
        self.assertIn(updated_at, html_text)

    def test_same_asin_reuses_canonical_product_id_and_catalog_syncs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db = root / 'sellemy.db'; catalog = root / 'products.json'; catalog.write_text('[]')
            with sqlite3.connect(db) as c:
                c.execute('''CREATE TABLE products (product_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, asin TEXT, model_number TEXT, official_url TEXT, article_file TEXT NOT NULL, amazon_url TEXT, image_url TEXT, status TEXT NOT NULL DEFAULT 'active', brand TEXT, observed_price INTEGER, observed_at TEXT, amazon_title TEXT)''')
                c.execute("INSERT INTO products(product_id,canonical_name,asin,article_file,status) VALUES('existing-id','Old','B0TEST0001','old.html','active')")
            payload, evidence = valid_payload(), valid_evidence()
            with patch.object(publish_payload, 'DB', db), patch.object(publish_payload, 'PRODUCTS_JSON', catalog):
                ids = publish_payload._upsert_products(payload, evidence)
                sync = publish_payload._sync_products_json(payload, evidence)
            with sqlite3.connect(db) as c:
                count = c.execute("SELECT count(*) FROM products WHERE asin='B0TEST0001'").fetchone()[0]
                relation_count = c.execute('SELECT count(*) FROM product_articles').fetchone()[0]
            rows = json.loads(catalog.read_text())
        self.assertEqual(ids[0], 'AMZ-B0TEST0001')
        self.assertEqual(count, 1)
        self.assertEqual(relation_count, 6)
        self.assertEqual(sync['total'], 6)
        self.assertEqual({row['asin'] for row in rows}, {f'B0TEST{i:04d}' for i in range(1, 7)})

    def test_legacy_catalog_row_gets_asin_identity_by_article_and_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'sellemy.db'
            with sqlite3.connect(db) as c:
                c.execute('''CREATE TABLE products (product_id TEXT PRIMARY KEY, asin TEXT, article_file TEXT, amazon_url TEXT, image_url TEXT, brand TEXT)''')
                c.execute("INSERT INTO products VALUES('AMZ-B0TEST0001','B0TEST0001','old-article.html','https://amazon.example','https://m.media-amazon.com/images/I/image-key._AC_SY300_.jpg','Brand')")
            rows = [{'name': '旧表示名', 'articleSlug': 'old-article', 'imageUrl': 'https://www.sellemy.jp/img/old-article/image-key._AC_SL1000_.jpg'}]
            with patch.object(publish_payload, 'DB', db):
                count = publish_payload._backfill_catalog_identities(rows)
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]['asin'], 'B0TEST0001')
        self.assertEqual(rows[0]['name'], '旧表示名')

    def test_affiliate_preflight_requires_all_18_links_and_rejects_bad_request(self):
        links = []
        for label, base in [('Amazon', 'https://amazon.example/'), ('楽天', 'https://rakuten.example/'), ('Yahoo', 'https://yahoo.example/')]:
            links.extend([f'<a href="{base}{i}">{label}</a>' for i in range(6)])
        rendered = ''.join(links)
        ok = type('Response', (), {'status_code': 302})()
        with patch.object(publish_payload.requests, 'get', return_value=ok) as get:
            result = publish_payload._affiliate_preflight(rendered)
        self.assertEqual(result['count'], 18)
        self.assertEqual((result['amazon'], result['rakuten'], result['yahoo']), (6, 6, 6))
        self.assertEqual(get.call_count, 18)

        bad = type('Response', (), {'status_code': 400})()
        with patch.object(publish_payload.requests, 'get', return_value=bad):
            with self.assertRaises(RuntimeError):
                publish_payload._affiliate_preflight(rendered)


    def test_publish_eyecatch_rejects_category_copy_even_with_ai_receipt(self):
        payload, evidence = valid_payload(), valid_evidence()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            category = root / 'category.png'; category.write_bytes(b'category-bytes')
            output = root / 'hero.png'
            fake_metadata = type('Category', (), {'eyecatch': 'category.png'})()
            generator = type('Generator', (), {'generate': lambda self, **kwargs: (kwargs['output'].write_bytes(b'category-bytes') or {'generation_method': 'generative_ai'})})()
            with patch.object(publish_payload, 'ROOT', root), patch.object(publish_payload, 'get_category', return_value=fake_metadata), patch.object(publish_payload.EyecatchGenerator, 'from_env', return_value=generator):
                with self.assertRaises(RuntimeError):
                    publish_payload._generate_article_eyecatch(payload, evidence, output)

    def test_category_specific_eyecatch_mapping_exists(self):
        for category in ('beauty', 'dailygoods', 'gadget'):
            metadata = publish_payload.get_category(category)
            self.assertTrue((ROOT / metadata.eyecatch).is_file())


if __name__ == '__main__':
    unittest.main()
