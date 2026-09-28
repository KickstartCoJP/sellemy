import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import affiliate_reporting


class AffiliateReportingCompatibilityTests(unittest.TestCase):
    def test_refresh_reads_new_pipeline_and_disables_old_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db_path = root / "actuals.sqlite3"
            state_path = root / "state.json"
            with patch.object(affiliate_reporting, "DB_PATH", db_path), \
                 patch.object(affiliate_reporting, "STATE_PATH", state_path):
                state = affiliate_reporting.refresh_state()
            self.assertFalse(state["creators_api_enabled"])
            self.assertFalse(state["manual_import_enabled"])
            self.assertEqual({x["provider"] for x in state["providers"]},
                             {"amazon", "rakuten", "valuecommerce"})
            self.assertEqual(json.loads(state_path.read_text()), state)


if __name__ == "__main__":
    unittest.main()
