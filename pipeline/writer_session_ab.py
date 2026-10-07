from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from codex_provider import _strict_schema, _usage
from growth_recovery import job_dir, read_artifact
from growth_runtime import evaluate_candidate, _gate_feedback
from writer_runtime import WRITER_JSON_SCHEMA, _prompt, invoke_writer

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = Path.home() / 'Library/Application Support/Sellemy/writer-session-ab'


def _fresh(topic: dict, evidence: dict, previous: dict | None, feedback: dict | None) -> tuple[dict, dict]:
    command = os.environ.get('SELLEMY_WRITER_AB_CODEX_COMMAND', '/opt/homebrew/bin/codex')
    model = os.environ.get('SELLEMY_WRITER_AB_MODEL', 'gpt-6-astra')
    schema = _strict_schema(WRITER_JSON_SCHEMA)
    base = _prompt(topic, evidence, previous_payload=previous, gate_feedback=feedback)
    canon = ROOT / 'config' / 'sellemy-codex-canon-bootstrap.md'
    brief = ROOT / '.runtime' / 'sellemy-codex' / 'brief.md'
    prompt = (
        'Fresh-session AB variant. This is evaluation only; do not edit files or publish. '
        f'Read {canon} and {brief}, then execute the Writer task.\n\n' + base
    )
    with tempfile.TemporaryDirectory(prefix='sellemy-writer-ab-') as tmp:
        tmp = Path(tmp); schema_path = tmp / 'schema.json'; output = tmp / 'response.json'
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding='utf-8')
        args = [command, 'exec', '--ephemeral', '--json', '--sandbox', 'read-only', '--skip-git-repo-check',
                '--cd', str(ROOT), '--model', model, '--output-schema', str(schema_path),
                '--output-last-message', str(output), '-']
        started = time.monotonic()
        completed = subprocess.run(args, input=prompt, text=True, capture_output=True, timeout=900, check=False)
        duration_ms = int((time.monotonic() - started) * 1000)
        if completed.returncode:
            raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
        payload = json.loads(output.read_text(encoding='utf-8'))
        return payload, {'session_mode': 'article_fresh', 'duration_ms': duration_ms, **_usage(completed.stdout)}


def _evaluate(payload: dict, evidence: dict) -> dict:
    try:
        _rendered, findings, qa = evaluate_candidate(payload, evidence)
        return {'pass': not findings and bool(qa.get('overall_pass')), 'findings': findings, 'qa': qa,
                'feedback': _gate_feedback(findings, qa)}
    except Exception as exc:
        return {'pass': False, 'findings': [f'{type(exc).__name__}: {exc}'], 'qa': {'overall_pass': False}}


def run(slug: str) -> dict:
    topic = read_artifact(slug, 'topic.json'); evidence = read_artifact(slug, 'evidence.json')
    previous = read_artifact(slug, 'payload.json'); feedback = read_artifact(slug, 'gate-feedback.json', {})
    if not isinstance(topic, dict) or not isinstance(evidence, dict):
        raise RuntimeError(f'recovery job artifacts unavailable: {slug}')
    p_started = time.monotonic()
    persistent_payload, persistent_meta = invoke_writer(
        topic, evidence, previous_payload=previous if isinstance(previous, dict) else None,
        gate_feedback=feedback if isinstance(feedback, dict) else None,
    )
    persistent_meta = {**persistent_meta, 'session_mode': 'persistent',
                       'duration_ms': int((time.monotonic() - p_started) * 1000)}
    fresh_payload, fresh_meta = _fresh(topic, evidence, previous if isinstance(previous, dict) else None,
                                       feedback if isinstance(feedback, dict) else None)
    result = {
        'slug': slug, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'persistent': {'meta': persistent_meta, 'evaluation': _evaluate(persistent_payload, evidence)},
        'fresh': {'meta': fresh_meta, 'evaluation': _evaluate(fresh_payload, evidence)},
        'production_mutated': False,
    }
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (OUT_ROOT / f'{stamp}-{slug}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Non-publishing persistent-vs-fresh Writer AB.')
    ap.add_argument('slug')
    args = ap.parse_args()
    print(json.dumps(run(args.slug), ensure_ascii=False, indent=2))
