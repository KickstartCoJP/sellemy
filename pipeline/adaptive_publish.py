from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from qa import run_qa
from review_gate import run_review_gate


class AdaptivePublishError(RuntimeError):
    pass


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if json.loads(path.read_text(encoding='utf-8')) != value:
            raise AdaptivePublishError(f'atomic read-back mismatch: {path}')
    finally:
        temporary_path.unlink(missing_ok=True)


def _append_jsonl(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical_json(value) + '\n').encode('utf-8')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = 0
        while written < len(encoded):
            written += os.write(descriptor, encoded[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdaptivePublishError(f'corrupt append-only record {path}:{number}') from exc
        if not isinstance(value, dict):
            raise AdaptivePublishError(f'non-object append-only record {path}:{number}')
        records.append(value)
    return records


def _normalized(value: str) -> str:
    return re.sub(r'[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+', '', value.lower())


def _ngrams(value: str, size: int = 3) -> set[str]:
    normalized = _normalized(value)
    if not normalized:
        return set()
    if len(normalized) <= size:
        return {normalized}
    return {normalized[index:index + size] for index in range(len(normalized) - size + 1)}


def _similarity(left: str, right: str) -> float:
    a, b = _ngrams(left), _ngrams(right)
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return max(intersection / len(a), intersection / len(b))


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    required = {
        'initial_target_per_day', 'minimum_target_per_day', 'maximum_target_per_day',
        'evaluation_window', 'green_streak_to_increase', 'poor_feedback_decrease_step',
        'minimum_red_streak_to_alert', 'repeated_issue_count',
        'slot_grace_minutes', 'novelty_similarity_yellow', 'novelty_similarity_red',
        'publish_slots_by_target', 'feedback_task_project_id', 'feedback_task_role_id',
        'feedback_task_prefix',
    }
    missing = required - set(value)
    if missing:
        raise AdaptivePublishError(f'adaptive publish config missing: {sorted(missing)}')
    minimum = int(value['minimum_target_per_day'])
    initial = int(value['initial_target_per_day'])
    maximum = int(value['maximum_target_per_day'])
    if not minimum <= initial <= maximum:
        raise AdaptivePublishError('adaptive publish target bounds are invalid')
    for target in range(minimum, maximum + 1):
        slots = value['publish_slots_by_target'].get(str(target))
        if not isinstance(slots, list) or len(slots) != target or len(set(slots)) != target:
            raise AdaptivePublishError(f'publish slots must contain exactly {target} unique entries')
        if not all(re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', slot) for slot in slots):
            raise AdaptivePublishError(f'invalid publish slot for target {target}')
    return value


def evaluate_published_artifact(
    *, root: Path, slug: str, commit: str, growth_run: dict, config: dict,
    prior_feedback: list[dict], evaluated_at: datetime | None = None,
) -> dict:
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    payload_path = root / 'data' / 'payloads' / f'{slug}.json'
    evidence_path = root / 'data' / 'evidence' / f'{slug}.json'
    receipt_path = root / 'data' / 'eyecatch-receipts' / f'{slug}.json'
    payload = json.loads(payload_path.read_text(encoding='utf-8'))
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    article_path = root / 'article' / evidence['category'] / f'{slug}.html'
    html = article_path.read_text(encoding='utf-8')

    review_findings = [repr(finding) for finding in run_review_gate(payload, evidence)]
    qa = run_qa(html, payload, evidence)
    content_issues = []
    if review_findings:
        content_issues.append('post_publish_review_gate_failed')
    if not qa.get('overall_pass'):
        content_issues.append('post_publish_machine_qa_failed')
    if receipt.get('generation_method') != 'generative_ai' or not receipt.get('image_sha256'):
        content_issues.append('eyecatch_receipt_invalid')
    if not growth_run.get('published') or growth_run.get('commit') != commit:
        content_issues.append('publication_evidence_mismatch')
    content_grade = 'green' if not content_issues else 'red'

    articles = json.loads((root / 'json' / 'articles.json').read_text(encoding='utf-8'))
    current_text = ' '.join(str(payload.get(key, '')) for key in ('h1', 'lead', 'summary', 'how_to_choose', 'conclusion'))
    comparisons = []
    for article in articles:
        if article.get('slug') == slug:
            continue
        candidate_text = ' '.join(str(article.get(key, '')) for key in ('title', 'summary', 'slug'))
        comparisons.append({'slug': article.get('slug'), 'similarity': round(_similarity(current_text, candidate_text), 6)})
    nearest = max(comparisons, key=lambda row: row['similarity'], default={'slug': None, 'similarity': 0.0})
    similarity = nearest['similarity']
    if similarity >= float(config['novelty_similarity_red']):
        novelty_grade = 'red'
    elif similarity >= float(config['novelty_similarity_yellow']):
        novelty_grade = 'yellow'
    else:
        novelty_grade = 'green'

    issues = list(content_issues)
    if novelty_grade != 'green':
        issues.append('topic_novelty_near_duplicate')
    overall = 'red' if 'red' in (content_grade, novelty_grade) else ('yellow' if 'yellow' in (content_grade, novelty_grade) else 'green')
    recent = prior_feedback[-int(config['evaluation_window']):]
    repeated = {
        issue for issue in issues
        if 1 + sum(issue in row.get('issues', []) for row in recent) >= int(config['repeated_issue_count'])
    }
    canonical_feedback_required = overall == 'red' or bool(repeated)
    suggestions = []
    if content_issues:
        suggestions.append('Review the failed post-publish integrity evidence before the next publication.')
    if novelty_grade != 'green':
        suggestions.append('Choose a materially different search intent, comparison axis, and product set.')

    growth_evidence = {
        'published': growth_run.get('published'),
        'commit': growth_run.get('commit'),
        'base_head': growth_run.get('base_head'),
        'topic': {
            key: (growth_run.get('topic') or {}).get(key)
            for key in ('slug', 'category', 'intent_key')
        },
        'applied': {
            key: (growth_run.get('applied') or {}).get(key)
            for key in ('slug', 'article_path', 'eyecatch_path', 'applied')
        },
    }
    artifacts = {
        'html_sha256': _sha256_bytes(article_path.read_bytes()),
        'payload_sha256': _sha256_bytes(payload_path.read_bytes()),
        'evidence_sha256': _sha256_bytes(evidence_path.read_bytes()),
        'eyecatch_receipt_sha256': _sha256_bytes(receipt_path.read_bytes()),
        'growth_run_sha256': _sha256_bytes(_canonical_json(growth_evidence).encode('utf-8')),
    }
    event_id = _sha256_bytes(_canonical_json({'slug': slug, 'commit': commit, 'artifacts': artifacts}).encode('utf-8'))
    return {
        'schema_version': 1,
        'event_id': event_id,
        'slug': slug,
        'commit': commit,
        'evaluated_at': evaluated_at.astimezone(timezone.utc).isoformat(),
        'overall': overall,
        'content_quality': {
            'grade': content_grade,
            'score': 1.0 if content_grade == 'green' else 0.0,
            'review_findings': review_findings,
            'qa_overall_pass': bool(qa.get('overall_pass')),
            'issues': content_issues,
        },
        'topic_novelty': {
            'grade': novelty_grade,
            'score': round(1.0 - similarity, 6),
            'nearest_slug': nearest['slug'],
            'similarity': similarity,
        },
        'issues': issues,
        'canonical_feedback_required': canonical_feedback_required,
        'improvement_suggestions': suggestions,
        'artifacts': artifacts,
    }


class AdaptivePublishController:
    def __init__(self, root: Path, *, state_dir: Path | None = None, config_path: Path | None = None):
        self.root = root
        self.config_path = config_path or root / 'config' / 'adaptive_publish.json'
        self.config = load_config(self.config_path)
        configured = os.environ.get('SELLEMY_ADAPTIVE_STATE_DIR', '').strip()
        self.state_dir = state_dir or (Path(configured).expanduser() if configured else Path.home() / 'Library' / 'Application Support' / 'Sellemy' / 'adaptive-publish')
        self.feedback_path = self.state_dir / 'feedback.jsonl'
        self.admissions_path = self.state_dir / 'admissions.jsonl'
        self.state_path = self.state_dir / 'controller_state.json'
        self.lock_path = self.state_dir / 'controller.lock'

    @contextmanager
    def _lock(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open('a+', encoding='utf-8') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def feedback(self) -> list[dict]:
        return _read_jsonl(self.feedback_path)

    def derive_state(self, feedback: list[dict] | None = None) -> dict:
        records = self.feedback() if feedback is None else feedback
        recent = records[-int(self.config['evaluation_window']):]
        target = int(self.config['initial_target_per_day'])
        minimum = int(self.config['minimum_target_per_day'])
        maximum = int(self.config['maximum_target_per_day'])
        green_streak = 0
        minimum_red_streak = 0
        for row in recent:
            grade = row.get('overall')
            if grade == 'green':
                green_streak += 1
                minimum_red_streak = 0
                if green_streak >= int(self.config['green_streak_to_increase']) and target < maximum:
                    target += 1
                    green_streak = 0
            elif grade in {'yellow', 'red'}:
                was_at_minimum = target == minimum
                target = max(minimum, target - int(self.config['poor_feedback_decrease_step']))
                green_streak = 0
                minimum_red_streak = minimum_red_streak + 1 if grade == 'red' and was_at_minimum else 0
            else:
                raise AdaptivePublishError(f'unsupported feedback grade: {grade!r}')
        ceo_alert = minimum_red_streak >= int(self.config['minimum_red_streak_to_alert'])
        quality_control_faults = {'post_publish_evaluation_failed', 'feedback_task_bridge_failed'}
        latest_issues = (records[-1].get('issues') or []) if records else []
        quality_control_fault = next((
            issue for issue in latest_issues if issue in quality_control_faults
        ), None)
        return {
            'schema_version': 1,
            'target_per_day': target,
            'minimum_target_per_day': minimum,
            'maximum_target_per_day': maximum,
            'green_streak': green_streak,
            'minimum_red_streak': minimum_red_streak,
            'ceo_alert_required': ceo_alert,
            'quality_control_fault': quality_control_fault,
            'publish_paused': bool(ceo_alert or quality_control_fault),
            'feedback_count': len(records),
            'latest_feedback_event_id': records[-1]['event_id'] if records else None,
            'recent_feedback_event_ids': [row['event_id'] for row in recent],
            'ceo_alert_detail': ({
                'reason': 'red_feedback_persisted_at_minimum_frequency',
                'task_event_required': True,
            } if ceo_alert else None),
        }

    def current_state(self) -> dict:
        state = self.derive_state()
        if self.state_path.exists():
            persisted = json.loads(self.state_path.read_text(encoding='utf-8'))
            if persisted != state:
                _atomic_json(self.state_path, state)
        elif self.feedback_path.exists():
            _atomic_json(self.state_path, state)
        return state

    def admit_scheduled(self, now: datetime | None = None) -> dict:
        zone = ZoneInfo(self.config.get('timezone', 'Asia/Tokyo'))
        local = (now or datetime.now(zone)).astimezone(zone)
        with self._lock():
            state = self.current_state()
            if state['publish_paused']:
                return {'allowed': False, 'reason': 'ceo_alert_pause', 'slot': None, 'state': state}
            slots = self.config['publish_slots_by_target'][str(state['target_per_day'])]
            minute_of_day = local.hour * 60 + local.minute
            eligible = []
            for configured_slot in slots:
                hour, minute = (int(part) for part in configured_slot.split(':'))
                delay = minute_of_day - (hour * 60 + minute)
                if 0 <= delay <= int(self.config['slot_grace_minutes']):
                    eligible.append((delay, configured_slot))
            if not eligible:
                return {'allowed': False, 'reason': 'slot_not_enabled_for_target', 'slot': None, 'state': state}
            slot = min(eligible)[1]
            admission_id = f'{local.date().isoformat()}::{slot}'
            prior = _read_jsonl(self.admissions_path)
            if any(row.get('admission_id') == admission_id for row in prior):
                return {'allowed': False, 'reason': 'slot_already_admitted', 'slot': slot, 'state': state}
            receipt = {
                'admission_id': admission_id,
                'admitted_at': local.astimezone(timezone.utc).isoformat(),
                'slot': slot,
                'target_per_day': state['target_per_day'],
            }
            _append_jsonl(self.admissions_path, receipt)
            return {'allowed': True, 'reason': 'scheduled_slot_admitted', 'slot': slot, 'state': state, 'receipt': receipt}

    def record_feedback(self, feedback: dict) -> tuple[dict, dict]:
        with self._lock():
            records = self.feedback()
            matches = [row for row in records if row.get('event_id') == feedback.get('event_id')]
            if matches:
                identity_keys = ('slug', 'commit', 'artifacts')
                if any(matches[0].get(key) != feedback.get(key) for key in identity_keys):
                    raise AdaptivePublishError('feedback event_id reused with different content')
                state = self.derive_state(records)
                _atomic_json(self.state_path, state)
                return matches[0], state
            _append_jsonl(self.feedback_path, feedback)
            records.append(feedback)
            state = self.derive_state(records)
            _atomic_json(self.state_path, state)
            return feedback, state

    def record_quality_control_failure(
        self, *, slug: str, commit: str, growth_run: dict, error: BaseException, issue: str,
    ) -> tuple[dict, dict]:
        if issue not in {'post_publish_evaluation_failed', 'feedback_task_bridge_failed'}:
            raise AdaptivePublishError(f'unsupported quality-control failure: {issue}')
        artifacts = {
            'growth_run_sha256': _sha256_bytes(_canonical_json({
                'published': growth_run.get('published'), 'commit': commit, 'slug': slug,
            }).encode('utf-8')),
        }
        event_id = _sha256_bytes(_canonical_json({
            'slug': slug, 'commit': commit, 'issue': issue, 'artifacts': artifacts,
        }).encode('utf-8'))
        feedback = {
            'schema_version': 1, 'event_id': event_id, 'slug': slug, 'commit': commit,
            'evaluated_at': datetime.now(timezone.utc).isoformat(), 'overall': 'red',
            'content_quality': {
                'grade': 'red', 'score': 0.0, 'review_findings': [],
                'qa_overall_pass': False, 'issues': [issue],
            },
            'topic_novelty': {
                'grade': 'unknown', 'score': None, 'nearest_slug': None, 'similarity': None,
            },
            'issues': [issue], 'canonical_feedback_required': True,
            'improvement_suggestions': [
                'Repair and verify the adaptive quality-control path before restoring publication.'
            ],
            'evaluation_error': f'{type(error).__name__}: {error}', 'artifacts': artifacts,
        }
        return self.record_feedback(feedback)

    def record_evaluation_failure(
        self, *, slug: str, commit: str, growth_run: dict, error: BaseException,
    ) -> tuple[dict, dict]:
        return self.record_quality_control_failure(
            slug=slug, commit=commit, growth_run=growth_run, error=error,
            issue='post_publish_evaluation_failed',
        )

    def record_bridge_failure(
        self, *, slug: str, commit: str, growth_run: dict, error: BaseException,
    ) -> tuple[dict, dict]:
        return self.record_quality_control_failure(
            slug=slug, commit=commit, growth_run=growth_run, error=error,
            issue='feedback_task_bridge_failed',
        )

    def evaluate_and_record(self, *, slug: str, commit: str, growth_run: dict) -> tuple[dict, dict]:
        prior = self.feedback()
        feedback = evaluate_published_artifact(
            root=self.root, slug=slug, commit=commit, growth_run=growth_run,
            config=self.config, prior_feedback=prior,
        )
        return self.record_feedback(feedback)
