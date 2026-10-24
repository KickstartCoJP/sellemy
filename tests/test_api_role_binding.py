import json
import tempfile
from pathlib import Path
from unittest import TestCase
from api_role_binding import STAGE_ROWS, SellemyRouteConfigError, require_codex_route

class SellemyApiRoleBindingTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'rows.json'
        self.rows = {name: {'unit': 'BU002：Sellemy', 'binding': name.removeprefix('sellemy-'),
                            'api_percent': 0} for names in STAGE_ROWS.values() for name in names}
        self._write()

    def _write(self):
        self.path.write_text(json.dumps(self.rows), encoding='utf-8')

    def test_all_five_records_bound_with_codex_100_percent(self):
        keys = [key for (surface, stage) in STAGE_ROWS for key in require_codex_route(surface, stage, path=self.path)]
        self.assertEqual(len(keys), 5)
        self.assertEqual(set(keys), set(self.rows))

    def test_nonzero_api_weight_is_rejected(self):
        self.rows['sellemy-image-generation']['api_percent'] = 50
        self._write()
        with self.assertRaisesRegex(SellemyRouteConfigError, 'explicit API cutover'):
            require_codex_route('bu-codex-sellemy-designer', 'eyecatch', path=self.path)

    def test_missing_image_generation_record_is_rejected(self):
        del self.rows['sellemy-image-generation']
        self._write()
        with self.assertRaisesRegex(SellemyRouteConfigError, 'row invalid'):
            require_codex_route('bu-codex-sellemy-designer', 'eyecatch', path=self.path)

    def test_unknown_stages_are_not_intercepted(self):
        self.assertEqual(require_codex_route('bu-codex-sellemy-short-qa', 'independent_creative_qa', path=self.path), ())
