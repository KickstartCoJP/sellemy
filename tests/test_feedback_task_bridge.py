from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from feedback_task_bridge import sync_feedback_to_task_event  # noqa: E402


class FeedbackTaskBridgeTests(unittest.TestCase):
    def config(self):
        return {
            'feedback_task_project_id': 'BU-002',
            'feedback_task_role_id': 'sellemy-ops',
            'feedback_task_prefix': 'TASK-BU002-QUALITY-FEEDBACK',
        }

    def feedback(self, *, canonical=True):
        return {
            'event_id': 'abcdef1234567890fedcba0987654321',
            'slug': 'sample-slug',
            'commit': 'deadbeef',
            'issues': ['topic_novelty_near_duplicate'],
            'improvement_suggestions': ['Choose a distinct intent.'],
            'canonical_feedback_required': canonical,
        }

    def test_no_signal_creates_no_task_event(self):
        with patch('feedback_task_bridge._run_action') as action:
            result = sync_feedback_to_task_event(
                feedback=self.feedback(canonical=False),
                controller_state={'ceo_alert_required': False},
                config=self.config(),
            )
        self.assertFalse(result['required'])
        action.assert_not_called()

    def test_canonical_feedback_creates_in_progress_task_event(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {'SELLEMY_AI_OS_CANONICAL_ROOT': directory}, clear=False
        ), patch('feedback_task_bridge._run_action') as action:
            action.side_effect = [
                {'message_id': 'created'},
                {'message_id': 'FEEDBACK-BU-002-ABCDEF1234567890', 'status': 'in_progress'},
            ]
            result = sync_feedback_to_task_event(
                feedback=self.feedback(),
                controller_state={'ceo_alert_required': False, 'target_per_day': 1},
                config=self.config(),
            )
        self.assertTrue(result['required'])
        self.assertTrue(result['created'])
        self.assertEqual(result['status'], 'in_progress')
        self.assertEqual(action.call_count, 2)
        append_payload = action.call_args_list[1].kwargs['payload']
        self.assertEqual(append_payload['status'], 'in_progress')
        self.assertTrue(append_payload['detail']['canonical_feedback_required'])

    def test_ceo_alert_uses_existing_user_decision_required_status(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {'SELLEMY_AI_OS_CANONICAL_ROOT': directory}, clear=False
        ), patch('feedback_task_bridge._run_action') as action:
            action.side_effect = [
                {'message_id': 'created'},
                {'message_id': 'FEEDBACK-BU-002-ABCDEF1234567890', 'status': 'user_decision_required'},
            ]
            result = sync_feedback_to_task_event(
                feedback=self.feedback(),
                controller_state={'ceo_alert_required': True, 'target_per_day': 1, 'publish_paused': True},
                config=self.config(),
            )
        self.assertEqual(result['status'], 'user_decision_required')
        append_payload = action.call_args_list[1].kwargs['payload']
        self.assertEqual(append_payload['detail']['requested_to'], 'ceo')
        self.assertEqual(append_payload['detail']['resume_role_id'], 'sellemy-ops')

    def test_existing_same_message_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / 'tasks' / 'TASK-BU002-QUALITY-FEEDBACK-ABCDEF1234567890'
            history = task / 'history'
            history.mkdir(parents=True)
            (task / 'task.json').write_text('{}', encoding='utf-8')
            (history / '000000000002.json').write_text(json.dumps({
                'message_id': 'FEEDBACK-BU-002-ABCDEF1234567890', 'status': 'in_progress'
            }), encoding='utf-8')
            with patch.dict(os.environ, {'SELLEMY_AI_OS_CANONICAL_ROOT': directory}, clear=False), patch(
                'feedback_task_bridge._run_action'
            ) as action:
                result = sync_feedback_to_task_event(
                    feedback=self.feedback(),
                    controller_state={'ceo_alert_required': False},
                    config=self.config(),
                )
        self.assertTrue(result['duplicate'])
        action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
