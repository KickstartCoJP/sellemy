from datetime import date
from pathlib import Path
import importlib.util

SPEC = importlib.util.spec_from_file_location("periodic_quality_review", Path(__file__).parents[1] / "pipeline" / "periodic_quality_review.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

def test_normalize_gsc_and_window():
    art = {"url":"https://www.sellemy.jp/article/beauty/a.html","published_at":"2026-09-20T00:00:00+09:00"}
    ga4 = {
        ("20260920","/article/beauty/a.html"):{"page_views":10,"sessions":8,"active_users":7,"affiliate_clicks":2},
        ("20260921","/article/beauty/a.html"):{"page_views":5,"sessions":4,"active_users":4,"affiliate_clicks":1},
    }
    gsc = {
        ("20260920",art["url"]):{"clicks":2.0,"impressions":20.0,"ctr":0.1,"position":5.0},
        ("20260921",art["url"]):{"clicks":1.0,"impressions":10.0,"ctr":0.1,"position":7.0},
    }
    out = m.aggregate_window(art, 7, ga4, gsc, date(2026,9,21))
    assert out["ga4"]["page_views"] == 15
    assert out["ga4"]["affiliate_clicks"] == 3
    assert out["ga4"]["affiliate_ctr"] == 0.2
    assert out["search_console"]["impressions"] == 30.0
    assert round(out["search_console"]["average_position"], 4) == 5.6667

def test_missing_clicks_stay_missing():
    art = {"url":"https://www.sellemy.jp/article/gadget/a.html","published_at":"2026-09-20T00:00:00+09:00"}
    ga4 = {("20260920","/article/gadget/a.html"):{"page_views":4,"sessions":3,"active_users":3,"affiliate_clicks":None}}
    out = m.aggregate_window(art, 7, ga4, {}, date(2026,9,20))
    assert out["ga4"]["affiliate_clicks"] is None
    assert out["ga4"]["affiliate_ctr"] is None
    assert out["search_console"]["state"] == "missing"
