import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import firefox_collectors as fc


class FirefoxCollectorTests(unittest.TestCase):
    def test_month_range_is_inclusive(self):
        self.assertEqual(
            list(fc.months("2025-11-15", "2026-02-01")),
            ["2025-11", "2025-12", "2026-01", "2026-02"],
        )

    def test_install_is_atomic_and_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "source.csv"
            target = root / "raw" / "provider.csv"
            source.write_text("new", encoding="utf-8")
            target.parent.mkdir()
            target.write_text("old", encoding="utf-8")
            self.assertEqual(fc.install(source, target), target)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_empty_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "empty.csv"
            source.write_bytes(b"")
            with self.assertRaises(fc.CollectorError):
                fc.install(source, root / "raw.csv")


if __name__ == "__main__":
    unittest.main()
