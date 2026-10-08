from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

import eyecatch_router


class EyecatchRouterTests(unittest.TestCase):
    def config(self):
        return {
            'mode': 'weighted_random',
            'codex_share': 20,
            'api_share': 80,
            'codex_reserve_percent': 20,
            'api_fallback_on_codex_failure': True,
            'codex_fallback_on_api_failure': True,
            'api_model': 'gpt-image-2.5-sunburst',
            'api_quality': 'high',
        }

    def test_reserve_threshold_forces_api(self):
        route, meta = eyecatch_router._choose_route(
            self.config(), {'remaining_percent': 20.0}
        )
        self.assertEqual(route, 'openai_api')
        self.assertEqual(meta['reason'], 'codex_reserve_threshold')

    def test_weighted_random_can_select_codex_or_api(self):
        with patch.object(eyecatch_router.secrets, 'randbelow', return_value=10):
            self.assertEqual(eyecatch_router._choose_route(self.config(), None)[0], 'codex')
        with patch.object(eyecatch_router.secrets, 'randbelow', return_value=90):
            self.assertEqual(eyecatch_router._choose_route(self.config(), None)[0], 'openai_api')

    def test_api_failure_falls_back_to_codex_and_records_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt_path = root / 'data' / 'eyecatch-receipts' / 'sample.json'
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            with (
                patch.object(eyecatch_router, 'ROOT', root),
                patch.object(eyecatch_router, '_existing_valid', return_value=None),
                patch.object(eyecatch_router, '_load_config', return_value=self.config()),
                patch.object(
                    eyecatch_router, '_latest_codex_rate_limit',
                    return_value={'remaining_percent': 58.0, 'used_percent': 42.0},
                ),
                patch.object(eyecatch_router, '_choose_route', return_value=(
                    'openai_api', {'reason': 'weighted_random', 'roll': 90, 'remaining_percent': 58.0}
                )),
                patch.object(eyecatch_router, '_generate_api', side_effect=RuntimeError('api down')),
                patch.object(eyecatch_router, 'ensure_codex_eyecatch', return_value={
                    'generation_method': 'codex_cli_imagegen',
                    'image_sha256': 'x', 'width': 1536, 'height': 1024,
                }),
            ):
                receipt = eyecatch_router.ensure_routed_eyecatch(
                    slug='sample', title='sample', category='gadget', evidence={}, payload={},
                )
            self.assertEqual(receipt['routing']['executed_route'], 'codex')
            self.assertEqual(receipt['routing']['fallback_from'], 'openai_api')
            self.assertTrue(receipt['routing']['errors_before_success'])
            stored = json.loads(receipt_path.read_text())
            self.assertEqual(stored['routing']['executed_route'], 'codex')


if __name__ == '__main__':
    unittest.main()
