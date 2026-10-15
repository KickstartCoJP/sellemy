from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

import responses_provider as rp
import eyecatch_router
import recovery_provider_policy as policy


class RecoveryProviderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy_patch = patch.object(policy, "active_policy", return_value={"active": True, "codex_allowed": False, "claude_allowed": False, "responses_api_allowed": True, "responses_primary_model": "gpt-6.1-sol", "api_failure_policy": "fail_closed"})
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)

    def test_active_policy_forces_responses_and_never_calls_local(self):
        local = unittest.mock.Mock(side_effect=AssertionError('local fallback called'))
        with patch.object(rp, 'generate', return_value=({'ok': True}, {'model': 'gpt-6.1-sol'})):
            value, meta = rp.route('writer', {'type':'object','properties':{}}, 'x', local)
        self.assertEqual(value, {'ok': True})
        self.assertEqual(meta['route_selected'], 'openai_responses')
        self.assertEqual(meta['route_weights'], {'api': 100.0, 'codex': 0.0})
        local.assert_not_called()

    def test_api_transport_failure_fails_closed_without_local(self):
        local = unittest.mock.Mock(side_effect=AssertionError('local fallback called'))
        with patch.object(rp, 'generate', side_effect=rp.ResponsesError('down', kind='transport')):
            with self.assertRaises(rp.ResponsesError):
                rp.route('writer', {'type':'object','properties':{}}, 'x', local)
        local.assert_not_called()

    def test_codex_and_claude_are_hard_blocked(self):
        with self.assertRaises(policy.RecoveryProviderPolicyError):
            policy.require_local_provider_allowed('codex')
        with self.assertRaises(policy.RecoveryProviderPolicyError):
            policy.require_local_provider_allowed('claude')

    def test_eyecatch_api_failure_does_not_fallback_to_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt_dir = root / 'data' / 'eyecatch-receipts'
            receipt_dir.mkdir(parents=True)
            cfg = {
                'mode':'weighted_random','codex_share':20,'api_share':80,
                'codex_reserve_percent':20,'api_fallback_on_codex_failure':True,
                'codex_fallback_on_api_failure':True,'api_model':'x','api_quality':'high'
            }
            with patch.object(eyecatch_router, 'ROOT', root), \
                 patch.object(eyecatch_router, '_existing_valid', return_value=None), \
                 patch.object(eyecatch_router, '_load_config', return_value=cfg), \
                 patch.object(eyecatch_router, '_latest_codex_rate_limit', return_value=None), \
                 patch.object(eyecatch_router, '_generate_api', side_effect=RuntimeError('api down')), \
                 patch.object(eyecatch_router, 'ensure_codex_eyecatch') as codex:
                with self.assertRaises(eyecatch_router.EyecatchRoutingError):
                    eyecatch_router.ensure_routed_eyecatch(slug='x', title='x', category='gadget', evidence={}, payload={})
            codex.assert_not_called()


if __name__ == '__main__':
    unittest.main()
