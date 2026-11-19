"""Existing off-slot recovery owns automatic legacy admission, preserving new slots."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_runtime


class LegacyGrowthCoordinatorTests(unittest.TestCase):
    def _role(self, invocations):
        def role(name, slug='', **kwargs):
            invocations.append((name, slug))
            if name == 'scheduler':
                operation = (kwargs.get('request') or {}).get('operation')
                if operation == 'current_state':
                    return {'publish_paused': False}
                return {'allowed': True, 'slot': 'test'}
            if name == 'state_queue':
                admitted = ('legacy_admit', '') in invocations
                return {'eligible': {'slug': 'old-article'} if admitted else None,
                        'unfinished': []}
            if name == 'legacy_admit':
                return {'admitted': True, 'slug': 'old-article'}
            if name == 'planning':
                return {'candidates': ['new']}
            if name == 'fresh_create':
                return {'slug': 'new-article'}
            return {}
        return role

    def test_offslot_recovery_admits_published_article_without_new_planning(self):
        invocations = []

        def process(slug, result, **kwargs):
            self.assertEqual(slug, 'old-article')
            result['status'] = 'awaiting_chatgpt_final_review'
            return None

        with (patch.object(growth_runtime, 'execute_role', side_effect=self._role(invocations)),
              patch.object(growth_runtime, '_execute_job', side_effect=process),
              patch.object(growth_runtime, 'require_publishing_enabled')):
            result = growth_runtime.run(publish=True, recovery_only=True, report_path=None)
        self.assertEqual(result['status'], 'awaiting_chatgpt_final_review')
        self.assertEqual(result['recovery_slug'], 'old-article')
        self.assertNotIn(('planning', ''), invocations)
        self.assertEqual(invocations.count(('legacy_admit', '')), 1)

    def test_due_writer_does_not_starve_legacy_hold_admission(self):
        invocations = []
        def role(name, slug='', **kwargs):
            invocations.append((name, slug))
            if name == 'scheduler':
                return {'publish_paused': False}
            if name == 'state_queue':
                return {'eligible': {'slug': 'existing-writer'}, 'unfinished': []}
            if name == 'legacy_admit':
                return {'admitted': True, 'slug': 'old-held-article', 'normalized_retry': True}
            return {}
        def process(slug, result, **kwargs):
            self.assertEqual(slug, 'existing-writer')
            result['status'] = 'awaiting_chatgpt_final_review'
            return None
        with (patch.object(growth_runtime, 'execute_role', side_effect=role),
              patch.object(growth_runtime, '_execute_job', side_effect=process),
              patch.object(growth_runtime, 'require_publishing_enabled')):
            result = growth_runtime.run(publish=True, recovery_only=True, report_path=None)
        self.assertEqual(result['legacy_repair_admission']['slug'], 'old-held-article')
        self.assertEqual(invocations.count(('legacy_admit', '')), 1)
        self.assertEqual(result['recovery_slug'], 'existing-writer')
        self.assertNotIn(('planning', ''), invocations)

    def test_normal_scheduled_slot_still_processes_fresh_article(self):
        invocations = []

        def process(slug, result, **kwargs):
            self.assertEqual(slug, 'new-article')
            result['status'] = 'awaiting_chatgpt_final_review'
            return None

        with (patch.object(growth_runtime, 'execute_role', side_effect=self._role(invocations)),
              patch.object(growth_runtime, '_execute_job', side_effect=process),
              patch.object(growth_runtime, 'require_publishing_enabled')):
            result = growth_runtime.run(publish=True, scheduled=True, report_path=None)
        self.assertEqual(result['status'], 'awaiting_chatgpt_final_review')
        self.assertNotIn(('legacy_admit', ''), invocations)
        self.assertIn(('fresh_create', ''), invocations)


if __name__ == '__main__':
    unittest.main()
