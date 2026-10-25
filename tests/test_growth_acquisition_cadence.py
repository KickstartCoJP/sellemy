import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BI_RUNNER = Path("/Users/kickstart/ai-management-os/scripts/run_sellemy_bi_acquisition.sh")


class GrowthAcquisitionCadenceTests(unittest.TestCase):
    def test_attempt_marker_controls_collection(self):
        script = BI_RUNNER.read_text()
        due = re.search(r"<<'PYDUE'\n(.*?)\nPYDUE", script, re.S).group(1)
        record = re.search(r"<<'PYQ'\n(.*?)\nPYQ", script, re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            marker = directory / "acquisition-attempt.json"

            def check(provider):
                return subprocess.check_output(
                    [sys.executable, "-c", due, str(marker), provider], text=True
                ).strip()

            def record_result(provider, code):
                subprocess.run([sys.executable, "-c", record, str(code), provider, tmp], check=True)

            self.assertEqual(check("amazon"), "1")
            self.assertEqual(check("rakuten"), "1")
            record_result("amazon", 124)
            self.assertEqual(check("amazon"), "0")
            self.assertEqual(check("rakuten"), "1")
            record_result("rakuten", 0)
            value = json.loads(marker.read_text())
            self.assertEqual(value["amazon"]["collector_rc"], 124)
            self.assertEqual(value["rakuten"]["collector_rc"], 0)
            value["amazon"]["attempted_at"] = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)).isoformat()
            marker.write_text(json.dumps(value))
            self.assertEqual(check("amazon"), "1")
            self.assertEqual(check("rakuten"), "0")
            record_result("amazon", 0)
            value = json.loads(marker.read_text())
            value["amazon"]["attempted_at"] = (datetime.now(timezone.utc) - timedelta(hours=6, minutes=1)).isoformat()
            marker.write_text(json.dumps(value))
            self.assertEqual(check("amazon"), "1")
            self.assertEqual(check("rakuten"), "0")
            marker.write_text("{malformed")
            self.assertEqual(check("amazon"), "0")



if __name__ == "__main__":
    unittest.main()
