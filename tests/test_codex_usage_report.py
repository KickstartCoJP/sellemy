from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from codex_usage_report import summarize


class CodexUsageReportTests(unittest.TestCase):
    def test_summarizes_eyecatch_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            rows = [
                {'surface':'bu-codex-sellemy-designer','stage':'eyecatch','input_tokens':100,'cached_input_tokens':60,'cache_write_input_tokens':0,'uncached_input_tokens':40,'output_tokens':10,'reasoning_output_tokens':2,'total_tokens':110},
                {'surface':'bu-codex-sellemy-designer','stage':'eyecatch','input_tokens':80,'cached_input_tokens':20,'cache_write_input_tokens':0,'uncached_input_tokens':60,'output_tokens':20,'reasoning_output_tokens':3,'total_tokens':100},
                {'surface':'bu-codex-sellemy-writer','stage':'writer','input_tokens':50,'cached_input_tokens':0,'cache_write_input_tokens':0,'uncached_input_tokens':50,'output_tokens':10,'reasoning_output_tokens':0,'total_tokens':60},
            ]
            path.write_text('\n'.join(json.dumps(x) for x in rows) + '\n')
            result = summarize(path, stage='eyecatch')
            self.assertEqual(result['rows'], 2)
            group = result['groups'][0]
            self.assertEqual(group['total_tokens'], 210)
            self.assertEqual(group['avg_total_tokens'], 105.0)
            self.assertEqual(group['uncached_input_tokens'], 100)

    def test_missing_total_tokens_stays_null_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            path.write_text(json.dumps({'surface':'x','stage':'writer','input_tokens':50,'cached_input_tokens':10,'uncached_input_tokens':40}) + '\n')
            group = summarize(path)['groups'][0]
            self.assertIsNone(group['total_tokens'])
            self.assertIsNone(group['avg_total_tokens'])
            self.assertEqual(group['total_tokens_observed_turns'], 0)


if __name__ == '__main__': unittest.main()
