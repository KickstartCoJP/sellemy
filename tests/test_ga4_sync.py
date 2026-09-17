from __future__ import annotations
import json, tempfile, unittest
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import ga4_sync

class Ga4RawTests(unittest.TestCase):
    def test_merge_replaces_refresh_window(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'raw.json'
            p.write_text(json.dumps({'rows':[{'date':'20260914','page_path':'/a','page_views':1},{'date':'20260916','page_path':'/old','page_views':9}]}))
            rows=ga4_sync.merge_raw(p,[{'date':'20260916','page_path':'/new','page_views':2}],'2026-09-15','2026-09-17')
            self.assertEqual([(r['date'],r['page_path']) for r in rows],[('20260914','/a'),('20260916','/new')])

    def test_feedback_uses_article_slug_and_daily_raw(self):
        rows=[{'date':'29990101','page_path':'/article/gadget/test-item.html','page_title':'X','page_views':4,'sessions':2,'active_users':2,'affiliate_clicks':1}]
        fb=ga4_sync.feedback_from(rows,28)
        row=next(x for x in fb['topic_metrics'] if x['intent_key']=='test-item')
        self.assertEqual(row['affiliate_ctr'],0.25)

if __name__=='__main__': unittest.main()
