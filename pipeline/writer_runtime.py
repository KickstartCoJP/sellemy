from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from responses_provider import LOG, route
from comparison_acceptance import blocked, product_issues
from dataclasses import dataclass
from pathlib import Path
from codex_provider import CodexProviderError, generate_isolated as codex_generate_persistent
from recovery_provider_policy import require_local_provider_allowed, active_policy
from local_ollama_provider import generate_json as local_generate_json
from responses_provider import validate_output


class WriterInvocationError(RuntimeError):
    def __init__(self, message: str, *, availability: bool = False):
        super().__init__(message)
        self.availability = availability


WRITER_JSON_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'needs_product_reselection', 'reasons', 'slug', 'category', 'h1', 'lead', 'summary', 'comparison_groups', 'products', 'how_to_choose', 'conclusion'],
    'properties': {
        'status': {'type': 'string', 'enum': ['READY', 'BLOCKED']},
        'needs_product_reselection': {'type': 'boolean'},
        'reasons': {'type': 'array', 'items': {'type': 'string'}},
        'slug': {'type': 'string'}, 'category': {'type': 'string'}, 'h1': {'type': 'string'},
        'lead': {'type': 'string'}, 'summary': {'type': 'string'},
        'comparison_groups': {
            'type': 'array', 'minItems': 0, 'maxItems': 3,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['id', 'title', 'angle', 'product_refs'],
                'properties': {
                    'id': {'type': 'string'}, 'title': {'type': 'string'}, 'angle': {'type': 'string'},
                    'product_refs': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'string'}},
                },
            },
        },
        'products': {
            'type': 'array', 'minItems': 0, 'maxItems': 6,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['ref', 'h3', 'description'],
                'properties': {'ref': {'type': 'string'}, 'h3': {'type': 'string'}, 'description': {'type': 'string'}},
            },
        },
        'how_to_choose': {'type': 'string'}, 'conclusion': {'type': 'string'},
    },
}

AVAILABILITY_PATTERN = re.compile(
    r'\b(?:429|529|rate.?limit|usage.?limit|weekly limit|session limit|quota|overload|service unavailable|temporar(?:y|ily) unavailable|capacity)\b',
    re.I,
)


def _diagnostic(completed: subprocess.CompletedProcess) -> str:
    try:
        envelope = json.loads(completed.stdout)
        if isinstance(envelope, dict) and isinstance(envelope.get('result'), str):
            return envelope['result'][-500:]
    except (TypeError, json.JSONDecodeError):
        pass
    return re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '[redacted-id]',
                  (completed.stderr or completed.stdout).strip()[-500:], flags=re.I)


@dataclass(frozen=True)
class WriterProvider:
    name: str
    command: tuple[str, ...]
    model: str
    certified: bool
    kind: str = 'claude'


def _default_executable() -> str | None:
    return next((str(p) for p in (Path('/opt/homebrew/bin/codex'), Path('/usr/local/bin/codex')) if p.is_file()), shutil.which('codex'))


def _provider(name: str) -> WriterProvider:
    upper = name.upper()
    configured = os.environ.get(f'SELLEMY_WRITER_{upper}_COMMAND', '').strip()
    if configured:
        command = tuple(shlex.split(configured))
    elif name == 'primary':
        executable = _default_executable()
        command = (executable,) if executable else ()
    else:
        command = ()
    if name == 'primary':
        from api_role_binding import require_codex_settings
        model, _effort = require_codex_settings('bu-codex-sellemy-writer', 'writer')
    else:
        model = os.environ.get(f'SELLEMY_WRITER_{upper}_MODEL', '').strip()
    certified = name == 'primary' or os.environ.get('SELLEMY_WRITER_SECONDARY_CERTIFIED', '').lower() in ('1', 'true', 'yes')
    kind = os.environ.get(f'SELLEMY_WRITER_{upper}_KIND', 'codex' if name == 'primary' else 'claude').strip().lower()
    return WriterProvider(name=name, command=command, model=model, certified=certified, kind=kind)


def _command() -> list[str]:
    """Compatibility helper for environment checks; production invocation uses providers."""
    primary = _provider('primary')
    if not primary.command:
        raise WriterInvocationError('Primary Writer runtime unavailable', availability=True)
    return list(primary.command)


def _prompt(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> str:
    from editorial_plan import build_editorial_plan
    plan = build_editorial_plan(topic, evidence, str((gate_feedback or {}).get('ceo_rework') or ''))
    prompt = (
        'あなたは、モノとの出会いをコンセプトとする「Sellemy」のブランドライターです。構造化JSONのみを返してください。\\n\\n'
        '読者が商品を選びやすくなる、自然な、読んで楽しい文章を書いてください。ブランドイメージを最重視し、監査報告書、説明書、仕様書のような文章にしないでください。\\n\\n'
        '6つの商品カセット以外には、具体的な商品名、ブランド名、商品そのものの説明をしないでください。また、事実や使用経験を創作しないでください。\\n\\n'
        '導入文は商品の用途・読者が得られる価値と比較ポイントを自然に伝えてください。困りごとや具体的な利用シーンを無理に設定する必要はありません。用途・特徴・選定ポイントから直接始めても構いません。架空の体験談や不自然な状況設定を避けてください。比較記事であることを毎回宣言する必要はありません。家電・ガジェットでは「比べます」も自然に使えます。美容・コスメでは機械的な比較宣言を避け、カテゴリと文脈に合う自然な導入を選んでください。\\n\\n'
        '導入文leadは日本語180〜230文字、推奨200〜210文字にしてください。確認できない商品の形状・端子位置・性能差を推測で記述しないでください。\\n\\n'
        '結びも商品名は記載せず、自然に短く締めてください。結びで比較済み機能・スペック・商品別条件を再列挙せず、読者が自分の用途に合う一台を選ぶ気持ちになれる自然な一文で締めてください。\\n\\n'
        '商品カセットでは、6つの異なる商品を2商品ずつ3組に構成してください。各商品について、検証済みの固有の特徴、向く人、選ぶ理由、他候補と異なる点を具体的に説明してください。各商品説明の目安は160〜260字で、字数合わせの水増しは不要です。\\n\\n'
        '文脈を壊す不用意な水増し文章、定型的な注意書き、内部QA用語、不要な仕様説明を書かないでください。slug、category、商品参照IDを維持してください。\\n\\n'
        '編集QAで根拠のない具体表現（仕様・数値・性能・効果・使用体験）が指摘されたら、該当表現は削除して再利用せず、確認できた事実と一般的な選び方・利用場面の自然な文章で所定の文字量を補ってください。確認できない事実を創作して補完しないでください。\\n\\n'
        '記事の既存順序を維持してください。leadは読者にとっての価値と判断の方向を先に示し、summaryはleadの繰り返しを避け選択の要点を示します。3組のcomparison_groupsは用途別の選び分けを具体的に伝え、各商品カードでは根拠のある固有の違いを説明します。how_to_chooseは条件別の選び方を示し、conclusionは自然に短く締めます。記事全体や一般解説合計に文字数目標を設けず、各パーツの必要な内容を過不足なく記載してください。\n\n'
        f'TOPIC:\n{json.dumps(topic, ensure_ascii=False, sort_keys=True)}\n\n'
        '3組の商品はEvidence.selection.axesの軸別割当をそのまま使い、軸をまたいで並べ替えないでください。選定理由は確認済み事実の範囲で商品の説明に反映してください。\n'
        f'EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}'
        '\nEDITORIAL_PLAN:\n' + json.dumps(plan, ensure_ascii=False, sort_keys=True)
    )
    if previous_payload is not None and isinstance(gate_feedback, dict):
        measures = gate_feedback.get('measurements') or {}
        failures = gate_feedback.get('qa_failures') or {}
        # Overall article length is a soft editorial guide, never a numeric QA failure.
        # A short draft must be revised only for substantive issues identified by QA.
        for field, low, high in (('summary',130,170),('how_to_choose',190,270)):
            length = measures.get(field + '_len')
            if isinstance(length,int) and failures.get(field + '_in_range') is False:
                prompt += (f'\nCRITICAL {field} LENGTH: prior {length}, required '
                           f'{low}-{high}, aim {low + (high-low)//2}; revise this field.\n')
    persona=topic.get('reader_persona') if isinstance(topic,dict) else None
    if isinstance(persona,dict):
        prompt += '\nREADER_PERSONA (follow this reader through introduction, terminology and recommendation): '+json.dumps(persona,ensure_ascii=False)+'\n'
    if previous_payload is not None:
        prompt += (
            '\n\nRewrite the complete payload using the gate diagnostics. Do not patch with scripted filler.\n\n'
            f'PREVIOUS_PAYLOAD:\n{json.dumps(previous_payload, ensure_ascii=False, sort_keys=True)}\n\n'
            f'GATE_FEEDBACK:\n{json.dumps(gate_feedback or {}, ensure_ascii=False, sort_keys=True)}'
        )
    if previous_payload is not None and isinstance(gate_feedback, dict):
        failed = gate_feedback.get('qa_failures') or {}
        semantic = gate_feedback.get('semantic_review') or {}
        if failed.get('lead_in_range') is False:
            prompt += '\nMANDATORY: Rewrite lead to 200-210 Japanese characters (allowed 180-230).\n'
        if failed.get('semantic_acceptance_pass') is False:
            prompt += ('\nMANDATORY: Correct the cited semantic QA failure, do not invent an unsupported differentiator. '
                       'Every product-specific advantage must appear in its verified source. '
                       'Reviewer issue: ' + str(semantic.get('reason') or '')[:1800] + '\n')
    if isinstance(gate_feedback,dict) and gate_feedback.get('editorial_plan_requirements'):
        prompt += ('\nMANDATORY EDITORIAL QUALITY REMEDIATION: Previous independent editorial review failed: '
                   + str(gate_feedback['editorial_plan_requirements'])[:1600]
                   + '\nRewrite all affected public prose in clear, natural Japanese. Do not merely assert that the '
                   'criterion is satisfied. Eliminate unnatural phrases, internal audit commentary, unsupported '
                   'comparisons, and repeated formulaic descriptions. Recheck E07 language quality and E08 evidence-grounded factual accuracy on the final complete article.\n')
    if isinstance(gate_feedback,dict) and gate_feedback.get('ceo_rework'):
        prompt += '\nMANDATORY CEO REWORK: '+str(gate_feedback['ceo_rework'])+'\nDo not repeat the cited defects.\n'
    return prompt


def _invoke(provider: WriterProvider, prompt: str, *, usage_key: str = "") -> tuple[dict, dict]:
    if not provider.command or not provider.command[0]:
        raise WriterInvocationError(f'{provider.name} Writer command unavailable', availability=True)
    if not provider.model:
        raise WriterInvocationError(f'{provider.name} Writer model is not configured', availability=True)
    if provider.kind in {'codex', 'claude'}:
        require_local_provider_allowed(provider.kind)
    if provider.kind == 'codex':
        try:
            payload, codex_meta = codex_generate_persistent(
                provider.command, provider.model, WRITER_JSON_SCHEMA, prompt,
                timeout=int(os.environ.get('SELLEMY_WRITER_TIMEOUT_SECONDS', '600')),
                root=Path(__file__).resolve().parents[1], surface_key='bu-codex-sellemy-writer', stage='writer', usage_key=usage_key,
            )
        except CodexProviderError as exc:
            raise WriterInvocationError(f'{provider.name} Writer {exc}', availability=True) from exc
        return payload, {'runtime': provider.command[0], 'model': provider.model,
                         'cost_usd': None, 'duration_api_ms': None, 'session_persisted': False,
                         **codex_meta}
    if provider.kind != 'claude':
        raise WriterInvocationError(f'unknown Writer provider kind: {provider.kind}')
    budget = os.environ.get('SELLEMY_WRITER_MAX_BUDGET_USD', '1.00')
    timeout = int(os.environ.get('SELLEMY_WRITER_TIMEOUT_SECONDS', '600'))
    args = list(provider.command) + ['-p', '--safe-mode', '--no-session-persistence', '--tools', '', '--model', provider.model, '--max-budget-usd', budget, '--output-format', 'json', '--json-schema', json.dumps(WRITER_JSON_SCHEMA)]
    try:
        completed = subprocess.run(args, input=prompt, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WriterInvocationError(f'{provider.name} Writer unavailable: {exc}', availability=True) from exc
    diagnostic = _diagnostic(completed)
    if completed.returncode != 0:
        raise WriterInvocationError(
            f'{provider.name} Writer exited {completed.returncode}: {diagnostic or "no diagnostic"}',
            availability=bool(AVAILABILITY_PATTERN.search(diagnostic)),
        )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WriterInvocationError(f'{provider.name} Writer returned non-JSON output') from exc
    payload = envelope.get('structured_output')
    if not isinstance(payload, dict):
        try:
            payload = json.loads(envelope.get('result')) if isinstance(envelope.get('result'), str) else None
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict):
        raise WriterInvocationError(f'{provider.name} Writer returned no structured_output object')
    metadata = {
        'runtime': provider.command[0], 'model': envelope.get('modelUsage') or provider.model,
        'cost_usd': envelope.get('total_cost_usd'), 'duration_api_ms': envelope.get('duration_api_ms'),
        'session_persisted': False,
    }
    return payload, metadata


def _invoke_local_writer(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> tuple[dict, dict]:
    primary, secondary = _provider('primary'), _provider('secondary')
    prompt = _prompt(topic, evidence, previous_payload=previous_payload, gate_feedback=gate_feedback)
    requested = f'{primary.name}:{primary.model}'
    try:
        payload, metadata = _invoke(primary, prompt, usage_key=str(topic.get('slug') or ''))
        return payload, {**metadata, 'writer_model_requested': requested, 'writer_model_used': requested, 'writer_provider_requested': primary.kind, 'writer_provider_used': primary.kind, 'fallback_used': False, 'fallback_reason': None, 'writer_attempt_count': 1}
    except WriterInvocationError as exc:
        if not exc.availability:
            raise
        if not secondary.command or not secondary.model or not secondary.certified:
            raise WriterInvocationError(f'{exc}; certified Secondary Writer unavailable', availability=True) from exc
        LOG.warning('stage=writer provider=codex fallback=claude reason=local_provider_error')
        payload, metadata = _invoke(secondary, prompt, usage_key=str(topic.get('slug') or ''))
        used = f'{secondary.name}:{secondary.model}'
        return payload, {**metadata, 'writer_model_requested': requested, 'writer_model_used': used, 'writer_provider_requested': primary.kind, 'writer_provider_used': secondary.kind, 'fallback_used': True, 'fallback_reason': 'primary_availability_error', 'writer_attempt_count': 2}


def invoke_writer(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> tuple[dict, dict]:
    issues = product_issues(evidence)
    if issues:
        return {**blocked(issues), 'slug': topic.get('slug'), 'category': topic.get('category')}, {'writer_invoked': False}
    prompt = _prompt(topic, evidence, previous_payload=previous_payload, gate_feedback=gate_feedback)
    policy = active_policy() or {}
    if policy.get('writer_provider') == 'ollama':
        require_local_provider_allowed('ollama')
        payload, metadata = local_generate_json(schema=WRITER_JSON_SCHEMA, prompt=prompt)
        validate_output(payload, WRITER_JSON_SCHEMA)
        metadata.update({
            'writer_provider_requested': 'ollama', 'writer_provider_used': 'ollama',
            'writer_model_requested': metadata.get('model'), 'writer_model_used': metadata.get('model'),
            'fallback_used': False, 'fallback_reason': None, 'writer_attempt_count': 1,
        })
        return payload, metadata
    return route(
        'writer', WRITER_JSON_SCHEMA, prompt,
        lambda: _invoke_local_writer(
            topic, evidence, previous_payload=previous_payload, gate_feedback=gate_feedback
        ),
        escalate=bool(gate_feedback),
    )
