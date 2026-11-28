import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import coo_article_review as m


class ExactCOODecisionTest(unittest.TestCase):
    def test_requires_exact_version_without_repeated_qa(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            def mock(name, **kw):
                return stack.enter_context(patch.object(m, name, **kw))
            mock('load_job', return_value={'status': 'ACTIVE', 'current_stage': 'COO_REVIEW_PENDING'})
            payload, evidence = {'h1': 'test'}, {'products': []}
            mock('read_artifact', side_effect=lambda s, n, *d: {'payload.json': payload, 'evidence.json': evidence}.get(n))
            writes = mock('write_artifact')
            stage = mock('set_stage')
            mock('require_acceptance')
            receipts = mock('require_receipts', side_effect=ValueError('QA missing'))
            cert = mock('require_certification')
            kw = dict(payload_sha=m.digest(payload), evidence_sha=m.digest(m.evidence_content(evidence)), decision='CERTIFIED', reason='reviewed')
            with self.assertRaisesRegex(ValueError, 'changed'):
                m.decide('topic', **{**kw, 'payload_sha': 'stale'})
            writes.assert_not_called()
            stage.assert_not_called()
            m.decide('topic', **kw)
            receipts.assert_not_called()
            cert.assert_not_called()
            self.assertEqual(writes.call_args_list[0].args[2]['reviewer_role'], 'coo')
            self.assertEqual(stage.call_args.args, ('topic', 'EYECATCH_PENDING'))
