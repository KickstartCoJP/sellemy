import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import affiliate_pipeline as ap


class AffiliatePipelineTests(unittest.TestCase):
    def test_rakuten_requires_positive_sellemy_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rakuten-monthly.csv"
            p.write_text("期間別成果: 2025.04\n\n発生日,成果報酬,クリック数,売上件数,売上金額\n"
                         "date,rewards,clicks,sales,amount\n2025-04-24,120,6,1,3000\n", encoding="utf-8")
            rows = ap.rakuten_rows(p, ap.sha256(p))
            self.assertEqual(rows[0][-1], "no_sellemy_positive_evidence")

    def test_amazon_tracking_id_is_attribution_gate(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "amazon.csv"
            p.write_text("トラッキングID,注文日,ASIN,注文済み商品,注文商品売上,紹介料合計\n"
                         "other-22,2025-01-03,B001,1,1000,50\n"
                         "suzuron-22,2025-01-04,B002,1,2000,100\n"
                         "sellemy-22,2026-09-18,B003,1,3000,150\n", encoding="utf-8")
            rows = ap.amazon_rows(p, ap.sha256(p))
            self.assertEqual([x[-1] for x in rows], ["tracking_id_mismatch", "tracking_id_historical_exact", "tracking_id_current_exact"])

    def test_ingest_is_idempotent_and_reconcile_excludes_unattributed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw"
            raw.mkdir()
            (raw / "amazon.csv").write_text(
                "トラッキングID,注文日,ASIN,注文済み商品,注文商品売上,紹介料合計\n"
                "other-22,2025-01-03,B001,1,1000,50\n"
                "suzuron-22,2025-01-04,B002,1,2000,100\n", encoding="utf-8")
            (raw / "rakuten.csv").write_text(
                "発生日,成果報酬,売上件数,売上金額\n2025-01-05,200,1,5000\n", encoding="utf-8")
            db_path = root / "a.sqlite3"
            db = ap.connect(db_path)
            first = ap.ingest_all(db, raw)
            second = ap.ingest_all(db, raw)
            self.assertEqual(first, second)
            result = ap.reconcile(db)
            eligible = db.execute("SELECT provider,commission_yen FROM provider_daily WHERE revenue_eligible=1").fetchall()
            excluded = db.execute("SELECT provider,exclusion_reason FROM provider_daily WHERE revenue_eligible=0").fetchall()
            self.assertEqual([(x[0], x[1]) for x in eligible], [("amazon", 100)])
            self.assertEqual({x[0] for x in excluded}, {"amazon", "rakuten"})
            self.assertEqual(result["commission_yen"], 100)
            db.close()


    def test_amazon_explicit_zero_report_is_valid_raw_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw"
            raw.mkdir()
            (raw / "amazon-official-zero-2026-09-16-2026-09-23.json").write_text(
                json.dumps({
                    "provider": "amazon",
                    "period_start": "2026-09-16",
                    "period_end": "2026-09-23",
                    "status": "explicit_zero",
                    "evidence": "Tracking-Id CSV データがありませんでした",
                }), encoding="utf-8")
            db = ap.connect(root / "a.sqlite3")
            ap.ingest_all(db, raw)
            ap.reconcile(db)
            row = db.execute("SELECT covered_from,covered_to,raw_file_count,state FROM provider_watermarks WHERE provider='amazon'").fetchone()
            self.assertEqual(tuple(row), ("2026-09-16", "2026-09-23", 1, "available"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM provider_daily WHERE provider='amazon'").fetchone()[0], 0)
            db.close()


    def test_current_month_explicit_zero_requires_all_provider_coverage(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import actuals_adapter as aa
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "a.sqlite3"
            db = ap.connect(db_path)
            for provider in ("amazon", "rakuten", "valuecommerce"):
                db.execute("INSERT OR REPLACE INTO provider_watermarks(provider,covered_from,covered_to,raw_file_count,curated_row_count,last_success_at,state) VALUES(?,?,?,?,?,?,?)",
                           (provider,"2026-09-01","2026-09-23",1,0,"x","available"))
            db.commit(); db.close()
            rows = aa.affiliate_actuals(db_path, now=datetime(2026,9,23,12,0,tzinfo=ZoneInfo("Asia/Tokyo")))
            revenue = [x for x in rows if x["metric_id"] == "revenue" and x["period"] == "2026-09"]
            self.assertEqual(len(revenue),1)
            self.assertEqual(revenue[0]["value"],0)
            self.assertEqual(revenue[0]["source"],"affiliate_provider_reconciled_explicit_zero")

    def test_valuecommerce_empty_month_is_valid_raw_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw"
            raw.mkdir()
            (raw / "valuecommerce-api-2025-01.json").write_text(
                json.dumps({"site_id": "2770133", "from": "2025-01-01", "to": "2025-01-31", "rows": []}),
                encoding="utf-8")
            db = ap.connect(root / "a.sqlite3")
            ap.ingest_all(db, raw)
            ap.reconcile(db)
            row = db.execute("SELECT raw_file_count,state FROM provider_watermarks WHERE provider='valuecommerce'").fetchone()
            self.assertEqual(tuple(row), (1, "available"))
            db.close()


if __name__ == "__main__":
    unittest.main()
