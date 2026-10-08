import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_provider
from unit_ai_router import UnitAIRouter, UnitAIRouterError


class UnitAIRouterTests(unittest.TestCase):
    def test_unit_surfaces_get_isolated_briefs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_state, a_brief = codex_provider._runtime_paths(root, 'bu-codex-sellemy-purpose-semantic')
            b_state, b_brief = codex_provider._runtime_paths(root, 'bu-codex-sellemy-purpose-qa')
            self.assertNotEqual(a_state, b_state)
            self.assertNotEqual(a_brief, b_brief)
            self.assertIn('purpose-semantic', a_brief.name)
            self.assertIn('purpose-qa', b_brief.name)

    def test_growth_planning_writer_keep_shared_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _a, planning = codex_provider._runtime_paths(root, codex_provider.PLANNING_SURFACE)
            _b, writer = codex_provider._runtime_paths(root, 'bu-codex-sellemy-writer')
            self.assertEqual(planning, writer)
            self.assertEqual(planning.name, 'brief.md')

    def test_api_is_explicitly_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / 'routing.json'
            cfg.write_text(json.dumps({'api': {'enabled': False}, 'stages': {'x': {'provider': 'openai_api'}}}))
            router = UnitAIRouter(root=root, config_path=cfg)
            with self.assertRaisesRegex(UnitAIRouterError, 'not enabled'):
                router.execute('x', schema={'type':'object','properties':{}}, prompt='x')

    def test_codex_route_uses_dedicated_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / 'routing.json'
            cfg.write_text(json.dumps({'api': {'enabled': False}, 'stages': {'x': {
                'provider': 'codex', 'codex_surface': 'bu-codex-sellemy-short-qa', 'model': 'gpt-6-astra'
            }}}))
            router = UnitAIRouter(root=root, config_path=cfg)
            with patch('unit_ai_router.generate_persistent', return_value=({'pass': True}, {'session_id':'abc'})) as generate:
                value, meta = router.execute('x', schema={'type':'object','properties':{'pass':{'type':'boolean'}}}, prompt='x')
            self.assertTrue(value['pass'])
            self.assertEqual(meta['surface'], 'bu-codex-sellemy-short-qa')
            self.assertEqual(generate.call_args.kwargs['surface_key'], 'bu-codex-sellemy-short-qa')


if __name__ == '__main__':
    unittest.main()
