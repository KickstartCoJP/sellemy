from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
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

    def test_category_specific_eyecatch_mapping_exists(self):
        for category in ('beauty', 'dailygoods', 'gadget'):
            metadata = publish_payload.get_category(category)
            self.assertTrue((ROOT / metadata.eyecatch).is_file())


if __name__ == '__main__':
    unittest.main()
