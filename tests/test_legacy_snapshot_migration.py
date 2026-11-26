import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from renderer import render_article
from payload_schema import PayloadValidationError, validate_payload


class LegacySnapshotMigration(unittest.TestCase):
    def test_scope_and_lossless_regeneration(self):
        paths = ROOT / 'data' / 'payloads'
        indexed = json.loads((ROOT / 'json' / 'articles.json').read_bytes())
        for row in indexed:
            self.assertTrue((paths / (row['slug'] + '.json')).is_file(), row['slug'])
        contract = json.loads((ROOT / 'change-contracts' / 'bu002-legacy-json33-20261127.json').read_bytes())
        targets = [ROOT / p for p in contract['allowed_paths'] if p.startswith('data/payloads/')]
        self.assertEqual(len(set(targets)), 33)
        for p in targets:
            with self.subTest(slug=p.stem):
                d = json.loads(p.read_bytes())
                self.assertEqual(d['slug'], p.stem)
                self.assertEqual([r for r in indexed if r['slug'] == p.stem], [d['catalog_entry']])
                self.assertEqual(d['source_path'], f"article/{d['category']}/{p.stem}.html")
                expected = (ROOT / d['source_path']).read_bytes()
                self.assertEqual(hashlib.sha256(expected).hexdigest(), d['source_sha256'])
                self.assertEqual(render_article(d, None).encode('utf-8'), expected)
                self.assertEqual(sum(len(s['cards']) for s in d['sections']), 6)
                with self.assertRaises(PayloadValidationError):
                    validate_payload(d)
                with self.assertRaises(PayloadValidationError):
                    render_article(d, {})
                with self.assertRaises(ValueError):
                    render_article(d, None, related_features=[])
                altered = copy.deepcopy(d)
                altered['document']['main'] += 'x'
                with self.assertRaises(ValueError):
                    render_article(altered, None)
