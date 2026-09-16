from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adaptive_publish import AdaptivePublishController, evaluate_published_artifact, load_config  # noqa: E402
from fixtures import valid_evidence, valid_payload  # noqa: E402
from renderer import render_article  # noqa: E402


def feedback(event_id: str, grade: str, issues: list[str] | None = None) -> dict:
    return {
        'schema_version': 1,
        'event_id': event_id,
        'slug': event_id,
        'commit': f'commit-{event_id}',
        'evaluated_at': '2026-09-17T00:00:00+00:00',
        'overall': grade,
        'issues': issues or [],
    }


class AdaptiveControllerTests(unittest.TestCase):
    def controller(self, root: Path, state: Path) -> AdaptivePublishController:
        return AdaptivePublishController(root, state_dir=state, config_path=ROOT / 'config' / 'adaptive_publish.json')

    def test_green_streak_increases_frequency_stepwise(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.controller(ROOT, root)
            for index in range(3):
                controller.record_feedback(feedback(f'green-{index}', 'green'))
            self.assertEqual(controller.current_state()['target_per_day'], 3)
            self.assertFalse(controller.current_state()['ceo_alert_required'])

    def test_yellow_or_red_decreases_frequency(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.controller(ROOT, Path(directory))
            for index in range(3):
                controller.record_feedback(feedback(f'green-{index}', 'green'))
            controller.record_feedback(feedback('yellow', 'yellow', ['minor']))
            self.assertEqual(controller.current_state()['target_per_day'], 2)
            controller.record_feedback(feedback('red', 'red', ['major']))
            self.assertEqual(controller.current_state()['target_per_day'], 1)

    def test_persistent_red_at_minimum_requires_ceo_alert_and_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.controller(ROOT, Path(directory))
            controller.record_feedback(feedback('red-1', 'red', ['major']))
            controller.record_feedback(feedback('red-2', 'red', ['major']))
            _row, state = controller.record_feedback(feedback('red-3', 'red', ['major']))
            self.assertEqual(state['target_per_day'], 1)
            self.assertTrue(state['ceo_alert_required'])
            self.assertTrue(state['publish_paused'])
            self.assertTrue(state['ceo_alert_detail']['task_event_required'])

    def test_state_projection_recovers_from_interrupted_or_stale_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.controller(ROOT, Path(directory))
            controller.record_feedback(feedback('green-1', 'green'))
            controller.state_path.write_text('{"target_per_day": 99}\n', encoding='utf-8')
            recovered = controller.current_state()
            self.assertEqual(recovered['target_per_day'], 2)
            self.assertEqual(json.loads(controller.state_path.read_text(encoding='utf-8')), recovered)

    def test_feedback_recording_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.controller(ROOT, Path(directory))
            row = feedback('same', 'green')
            first, first_state = controller.record_feedback(row)
            retried = deepcopy(row)
            retried['evaluated_at'] = '2026-09-17T00:01:00+00:00'
            second, second_state = controller.record_feedback(retried)
            self.assertEqual(first, second)
            self.assertEqual(first_state, second_state)
            self.assertEqual(len(controller.feedback()), 1)

    def test_initial_two_per_day_preserves_0410_and_1610_only(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.controller(ROOT, Path(directory))
            zone = ZoneInfo('Asia/Tokyo')
            first = controller.admit_scheduled(datetime(2026, 9, 17, 4, 12, tzinfo=zone))
            extra = controller.admit_scheduled(datetime(2026, 9, 17, 10, 10, tzinfo=zone))
            second = controller.admit_scheduled(datetime(2026, 9, 17, 16, 10, tzinfo=zone))
            duplicate = controller.admit_scheduled(datetime(2026, 9, 17, 4, 10, tzinfo=zone))
            self.assertTrue(first['allowed'])
            self.assertFalse(extra['allowed'])
            self.assertTrue(second['allowed'])
            self.assertEqual(duplicate['reason'], 'slot_already_admitted')


class PostPublishEvaluatorTests(unittest.TestCase):
    def _artifact_root(self, base: Path, *, duplicate: bool) -> tuple[Path, str]:
        payload, evidence = valid_payload(), valid_evidence()
        slug = payload['slug']
        (base / 'data' / 'payloads').mkdir(parents=True)
        (base / 'data' / 'evidence').mkdir(parents=True)
        (base / 'data' / 'eyecatch-receipts').mkdir(parents=True)
        (base / 'article' / payload['category']).mkdir(parents=True)
        (base / 'json').mkdir(parents=True)
        (base / 'data' / 'payloads' / f'{slug}.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        (base / 'data' / 'evidence' / f'{slug}.json').write_text(json.dumps(evidence, ensure_ascii=False), encoding='utf-8')
        (base / 'data' / 'eyecatch-receipts' / f'{slug}.json').write_text(json.dumps({
            'generation_method': 'generative_ai', 'image_sha256': 'abc123',
        }), encoding='utf-8')
        (base / 'article' / payload['category'] / f'{slug}.html').write_text(
            render_article(payload, evidence), encoding='utf-8'
        )
        articles = [{'slug': slug, 'title': payload['h1'], 'summary': payload['summary']}]
        if duplicate:
            articles.append({
                'slug': 'near-duplicate-existing',
                'title': payload['h1'],
                'summary': ' '.join(str(payload.get(key, '')) for key in ('lead', 'summary', 'how_to_choose', 'conclusion')),
            })
        else:
            articles.append({'slug': 'distinct-existing', 'title': '真夏の屋外用携帯扇風機', 'summary': '風量と電池寿命を比較します。'})
        (base / 'json' / 'articles.json').write_text(json.dumps(articles, ensure_ascii=False), encoding='utf-8')
        return base, slug

    def test_duplicate_near_article_degrades_novelty(self):
        with tempfile.TemporaryDirectory() as directory:
            root, slug = self._artifact_root(Path(directory), duplicate=True)
            result = evaluate_published_artifact(
                root=root, slug=slug, commit='abc',
                growth_run={'published': True, 'commit': 'abc'},
                config=load_config(ROOT / 'config' / 'adaptive_publish.json'),
                prior_feedback=[],
            )
            self.assertIn(result['topic_novelty']['grade'], {'yellow', 'red'})
            self.assertEqual(result['topic_novelty']['nearest_slug'], 'near-duplicate-existing')
            self.assertIn('topic_novelty_near_duplicate', result['issues'])

    def test_clean_artifact_produces_structured_green_feedback(self):
        result = evaluate_published_artifact(
            root=ROOT, slug='autumn-hand-creams-6-picks', commit='abc',
            growth_run={'published': True, 'commit': 'abc'},
            config=load_config(ROOT / 'config' / 'adaptive_publish.json'),
            prior_feedback=[],
        )
        self.assertEqual(result['overall'], 'green')
        self.assertEqual(result['content_quality']['grade'], 'green')
        self.assertEqual(result['topic_novelty']['grade'], 'green')
        self.assertFalse(result['canonical_feedback_required'])
        self.assertEqual(len(result['event_id']), 64)

    def test_same_publication_has_stable_id_when_report_timestamps_change(self):
        first = evaluate_published_artifact(
            root=ROOT, slug='autumn-hand-creams-6-picks', commit='abc',
            growth_run={'published': True, 'commit': 'abc', 'finished_at': 'first'},
            config=load_config(ROOT / 'config' / 'adaptive_publish.json'),
            prior_feedback=[],
        )
        second = evaluate_published_artifact(
            root=ROOT, slug='autumn-hand-creams-6-picks', commit='abc',
            growth_run={'published': True, 'commit': 'abc', 'finished_at': 'second'},
            config=load_config(ROOT / 'config' / 'adaptive_publish.json'),
            prior_feedback=[],
        )
        self.assertEqual(first['event_id'], second['event_id'])
        self.assertEqual(first['artifacts'], second['artifacts'])


if __name__ == '__main__':
    unittest.main()
