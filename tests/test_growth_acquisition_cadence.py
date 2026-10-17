import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GrowthAcquisitionCadenceTests(unittest.TestCase):
    def test_attempt_marker_controls_collection(self):
        script = (ROOT / "ops/run-growth.sh").read_text()
        due = re.search(r"<<'PYDUE'\n(.*?)\nPYDUE", script, re.S).group(1)
        record = re.search(r"<<'PYQ'\n(.*?)\nPYQ", script, re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            marker = directory / "acquisition-attempt.json"

            def check():
                return subprocess.check_output(
                    [sys.executable, "-c", due, str(marker)], text=True
                ).strip()

            self.assertEqual(check(), "1")
            subprocess.run(
                [sys.executable, "-c", record, "124", "1", tmp], check=True
            )
            self.assertEqual(check(), "0")
            self.assertEqual(json.loads(marker.read_text())["amazon"]["collector_rc"], 124)
            value = json.loads(marker.read_text())
            value["attempted_at"] = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)).isoformat()
            marker.write_text(json.dumps(value))
            self.assertEqual(check(), "1")
            subprocess.run([sys.executable, "-c", record, "0", "0", tmp], check=True)
            self.assertEqual(check(), "0")
            value = json.loads(marker.read_text())
            value["attempted_at"] = (datetime.now(timezone.utc) - timedelta(hours=6, minutes=1)).isoformat()
            marker.write_text(json.dumps(value))
            self.assertEqual(check(), "1")
            marker.write_text("{malformed")
            self.assertEqual(check(), "0")


if __name__ == "__main__":
    unittest.main()
