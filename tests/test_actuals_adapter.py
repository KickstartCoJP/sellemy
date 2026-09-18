import sys, unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import actuals_adapter

class ActualsAdapterTests(unittest.TestCase):
    def test_ga4_uses_date_grain_totals(self):
        doc={'grain':'date','metric':'screenPageViews','generated_at':'2026-09-18T00:00:00+00:00','rows':[
            {'date':'20260917','page_views':7},{'date':'20260918','page_views':5}]}
        rows=actuals_adapter.ga4_actuals(doc)
        daily=[r for r in rows if r['metric_id']=='pv' and 'period_start' in r]
        monthly=[r for r in rows if r['metric_id']=='pv' and 'period_start' not in r]
        self.assertEqual([r['value'] for r in daily],[7,5])
        self.assertEqual(monthly[0]['value'],12)

    def test_ga4_rejects_page_grain_for_top_pv(self):
        with self.assertRaises(ValueError):
            actuals_adapter.ga4_actuals({'grain':'date_x_pagePath','metric':'screenPageViews','generated_at':'x','rows':[]})

    def test_publication_and_zero_semantics(self):
        pub={'generated_at':'2026-09-18T17:00:00+09:00','daily':[
            {'date':'2026-09-17','published_article_count_daily':2,'published_article_count':44},
            {'date':'2026-09-18','published_article_count_daily':1,'published_article_count':45}]}
        ga4={'grain':'date','metric':'screenPageViews','generated_at':'2026-09-18T00:00:00+00:00','rows':[{'date':'20260918','page_views':5}]}
        rows=actuals_adapter.build_actuals(pub,ga4,now=datetime(2026,9,18,17,0,tzinfo=ZoneInfo('Asia/Tokyo')))
        self.assertTrue(any(r['metric_id']=='published_article_count' and r['value']==45 for r in rows))
        self.assertTrue(any(r['metric_id']=='cost' and r['value']==0 for r in rows))
        self.assertFalse(any(r['metric_id'] in {'revenue','profit'} for r in rows))

if __name__=='__main__': unittest.main()
