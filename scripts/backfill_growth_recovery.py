from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from growth_recovery import (
    create_job, job_dir, load_job, mark_failure, read_artifact, save_eyecatch,
    save_writer_result, set_stage, state_path,
)

LOG = ROOT / 'logs' / 'growth.log'
SESSIONS = Path.home() / '.codex' / 'sessions'
GENERATED = Path.home() / '.codex' / 'generated_images'
RECOVERY_ARCHIVE = Path.home() / 'Library/Application Support/Sellemy/recovery-archive'


def _growth_rows() -> list[dict]:
    rows = []
    for line in LOG.read_text(encoding='utf-8', errors='ignore').splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get('topic'), dict) and row.get('candidate_count', 0) >= 6:
            rows.append(row)
    return rows


def latest_failed_viable(limit: int = 35) -> list[dict]:
    by_slug: dict[str, dict] = {}
    for row in _growth_rows():
        slug = row['topic'].get('slug')
        if slug:
            by_slug[slug] = row
    failed = [row for row in by_slug.values() if not row.get('published')]
    return failed[-limit:]


def classify(row: dict) -> str:
    blocker = str(row.get('blocker') or '')
    if row.get('status') == 'awaiting_owner_eyecatch' or 'attest built-in image_gen' in blocker or 'eyecatch' in blocker.lower():
        return 'eyecatch'
    if 'Review/QA' in blocker or (row.get('writer_attempts') and not (row.get('qa') or {}).get('overall_pass', False)):
        return 'writer_qa'
    if 'working tree' in blocker.lower() or 'publish' in blocker.lower() or 'deploy' in blocker.lower():
        return 'publish'
    return 'other'


def _content_text(payload: dict) -> str:
    content = payload.get('content') or []
    parts = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get('text'), str):
            parts.append(item['text'])
    return '\n'.join(parts)


def _user_text(row: dict) -> str:
    payload = row.get('payload') or {}
    if row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
        return _content_text(payload)
    if row.get('type') == 'event_msg' and payload.get('type') == 'item_completed':
        item = payload.get('item') or {}
        if item.get('type') == 'UserMessage':
            return _content_text(item)
    return ''


def _assistant_json(row: dict) -> dict | None:
    payload = row.get('payload') or {}
    candidates = []
    if row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'assistant':
        candidates.extend(str(x.get('text') or '') for x in payload.get('content') or [] if isinstance(x, dict))
    if row.get('type') == 'event_msg' and payload.get('type') == 'task_complete':
        candidates.append(str(payload.get('last_agent_message') or ''))
    for raw in reversed(candidates):
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _decode_after(text: str, marker: str) -> dict | None:
    pos = text.find(marker)
    if pos < 0:
        return None
    raw = text[pos + len(marker):].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _session_files(session_ids: set[str], slug: str) -> list[Path]:
    files: list[Path] = []
    for session_id in session_ids:
        files.extend(SESSIONS.glob(f'**/*{session_id}.jsonl'))
    if files:
        return sorted(set(files))
    for path in SESSIONS.glob('**/*.jsonl'):
        try:
            if slug in path.read_text(encoding='utf-8', errors='ignore'):
                files.append(path)
        except OSError:
            pass
    return sorted(set(files))


def recover_writer(row: dict) -> tuple[dict | None, dict | None, dict | None]:
    slug = row['topic']['slug']
    sessions = {
        str(item.get('session_id')) for item in (row.get('writer_attempts') or [])
        if isinstance(item, dict) and item.get('session_id')
    }
    evidence = topic = payload = None
    for path in _session_files(sessions, slug):
        active = False
        for raw in path.read_text(encoding='utf-8', errors='ignore').splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            text = _user_text(event)
            if text and 'You are the Writer stage' in text:
                if f'"slug": "{slug}"' in text or f'"slug":"{slug}"' in text:
                    active = True
                    topic = _decode_after(text, 'TOPIC:\n') or topic
                    evidence = _decode_after(text, 'EVIDENCE:\n') or evidence
                else:
                    active = False
            if active:
                value = _assistant_json(event)
                if isinstance(value, dict) and value.get('slug') == slug and 'products' in value:
                    payload = value
    return topic, evidence, payload


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return struct.unpack('>II', raw[16:24])


def recover_designer(slug: str) -> tuple[dict, Path] | None:
    best = None
    for path in _session_files(set(), slug):
        m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$', path.name, re.I)
        thread_id = m.group(1) if m else None
        active = False
        imagegen = False
        extension_id = None
        for raw in path.read_text(encoding='utf-8', errors='ignore').splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            text = _user_text(event)
            if text and 'Goal: create the single production article eyecatch PNG' in text:
                active = f'Slug: {slug}' in text
                if active:
                    imagegen = False; extension_id = None
            payload = event.get('payload') or {}
            if active and event.get('type') == 'event_msg' and payload.get('type') == 'item_completed':
                item = payload.get('item') or {}
                if item.get('type') == 'Extension' and item.get('kind') == 'image_gen.generation' and item.get('status') == 'completed':
                    imagegen = True
                    extension_id = item.get('id')
            if not active or not imagegen:
                continue
            result = _assistant_json(event)
            if not isinstance(result, dict) or result.get('status') != 'completed':
                continue
            candidates = []
            if result.get('output_path'):
                candidates.append(Path(str(result['output_path'])).expanduser())
            if thread_id and extension_id:
                candidates.append(GENERATED / thread_id / f'{extension_id}.png')
            for image in candidates:
                if image.is_file() and _png_dimensions(image) == (1536, 1024):
                    raw_image = image.read_bytes()
                    receipt = {
                        'generation_method': 'codex_cli_imagegen',
                        'generation_route': 'built-in_image_gen',
                        'generation_route_attestation': 'historical_codex_rollout_extension',
                        'model_reported_generation_route': result.get('generation_route'),
                        'role_id': 'bu-codex-sellemy-designer',
                        'thread_id': thread_id,
                        'member_binding_revision': None,
                        'model': 'gpt-6-astra',
                        'visual_family': result.get('visual_family') or 'Historical recovered image_gen output',
                        'summary': result.get('summary') or 'Recovered from historical completed Codex image_gen turn.',
                        'image_sha256': hashlib.sha256(raw_image).hexdigest(),
                        'width': 1536, 'height': 1024,
                        'generated_at': None,
                        'token_usage_source': 'historical_rollout',
                        'image_generation_internal_usage_exposed': False,
                        'token_usage': {},
                    }
                    best = (receipt, image)
    return best

def archived_eyecatch(slug: str) -> tuple[dict, Path] | None:
    for receipt_path in RECOVERY_ARCHIVE.glob(f'**/{slug}.json'):
        image_candidates = list(receipt_path.parent.glob(f'**/{slug}.png'))
        if not image_candidates:
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            continue
        image = image_candidates[0]
        if _png_dimensions(image) == (1536, 1024):
            receipt = dict(receipt)
            receipt['generation_route_attestation'] = receipt.get('generation_route_attestation') or 'historical_verified_receipt'
            return receipt, image
    return None


def migrate(row: dict, *, dry_run: bool) -> dict:
    slug = row['topic']['slug']
    failure_stage = classify(row)
    topic_from_session, evidence, payload = recover_writer(row)
    topic = dict(row['topic'])
    topic.pop('_planning_provider_metadata', None)
    if topic_from_session:
        topic.update({k: v for k, v in topic_from_session.items() if k in topic or k in {'slug','category','query','title','intent_key','comparison_axes','signals'}})
    report = {
        'slug': slug, 'failure_stage': failure_stage,
        'writer_evidence_recovered': isinstance(evidence, dict),
        'writer_payload_recovered': isinstance(payload, dict),
        'eyecatch_recovered': False,
        'registered': False,
    }
    if not isinstance(evidence, dict):
        report['reason'] = 'historical evidence not recoverable from writer session'
        return report
    if dry_run:
        recovered = archived_eyecatch(slug) or recover_designer(slug)
        report['eyecatch_recovered'] = bool(recovered)
        return report
    if state_path(slug).exists():
        report['registered'] = True
        report['existing'] = True
        return report
    state = create_job(
        topic=topic, evidence=evidence, selection=row.get('selection') or {},
        planning_provider=row.get('planning_provider') or {},
        viability_probes=row.get('viability_probes') or [], source='historical_backfill',
    )
    qa = row.get('qa') if isinstance(row.get('qa'), dict) else {'overall_pass': False}
    findings = row.get('review_findings') if isinstance(row.get('review_findings'), list) else []
    writer_meta = (row.get('writer_attempts') or [{}])[-1]
    if isinstance(payload, dict):
        passed = bool(qa.get('overall_pass')) and not findings
        feedback = {
            'review_findings': findings,
            'qa_failures': {k:v for k,v in qa.items() if (k.endswith('_pass') or k.endswith('_in_range')) and v is False},
            'measurements': {k:qa.get(k) for k in ('lead_len','summary_len','how_to_choose_len','main_len','description_lens','h3_lens')},
        }
        save_writer_result(slug, payload=payload, qa=qa, findings=findings, feedback=feedback,
                           metadata=writer_meta if isinstance(writer_meta, dict) else {}, passed=passed)
        if not passed:
            attempts = max(1, len(row.get('writer_attempts') or []))
            for _ in range(attempts):
                mark_failure(slug, stage='writer', failure_class='role_quality', failure_code='HISTORICAL_REVIEW_QA_FAILED',
                             reason=str(row.get('blocker') or ''), role_return=True, role='writer')
    if failure_stage in {'eyecatch','publish'} and isinstance(payload, dict) and bool(qa.get('overall_pass')) and not findings:
        recovered = archived_eyecatch(slug) or recover_designer(slug)
        if recovered:
            receipt, image = recovered
            save_eyecatch(slug, receipt=receipt, image_path=image)
            report['eyecatch_recovered'] = True
        if failure_stage == 'eyecatch' and not recovered:
            mark_failure(slug, stage='eyecatch', failure_class='runtime_infrastructure',
                         failure_code='HISTORICAL_ATTESTATION_FAILURE', reason=str(row.get('blocker') or ''), role='designer')
        elif failure_stage == 'publish':
            if recovered:
                mark_failure(slug, stage='publish', failure_class='runtime_infrastructure',
                             failure_code='HISTORICAL_PUBLISH_FAILURE', reason=str(row.get('blocker') or ''), role='publish')
            else:
                set_stage(slug, 'EYECATCH_PENDING', event='MIGRATED_PRE_EYECATCH_PUBLISH_GATE_FAILURE',
                          detail={'original_failure': str(row.get('blocker') or '')})
    report['registered'] = True
    report['state'] = load_job(slug).get('current_stage')
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=35)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    rows = latest_failed_viable(args.limit)
    result = [migrate(row, dry_run=not args.apply) for row in rows]
    print(json.dumps({
        'count': len(result),
        'stages': {k: sum(x['failure_stage']==k for x in result) for k in ('eyecatch','writer_qa','publish','other')},
        'evidence_recovered': sum(x['writer_evidence_recovered'] for x in result),
        'payload_recovered': sum(x['writer_payload_recovered'] for x in result),
        'eyecatch_recovered': sum(x['eyecatch_recovered'] for x in result),
        'registered': sum(x['registered'] for x in result),
        'rows': result,
    }, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
