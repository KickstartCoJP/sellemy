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
    from qa_return import writer_feedback, writer_evidence, writer_revision_evidence
    gate_feedback = writer_feedback(gate_feedback)
    topic = {key: value for key, value in topic.items() if key != 'title'}
    # The canonical plan/hash must always refer to FULL saved evidence, not the
    # smaller Writer-only view used when correcting a specific QA defect.
    plan = build_editorial_plan(topic, evidence, str((gate_feedback or {}).get('ceo_rework') or ''))
    evidence = writer_revision_evidence(evidence, gate_feedback) if previous_payload is not None else writer_evidence(evidence)
    prompt = (
        'あなたはSellemyの商品紹介記事を書く編集者です。構造化JSONのみを返してください。\\n\\n'
        '検索集客とアフィリエイト送客を目的とする記事を書いてください。一般記事部分はSEO・AEOの検索意図に答える自然な説明とし、確定商品の紹介は商品カセットにのみ短く掲載してください。\\n\\n'
        '企画の検索意図と読者の目的に合う一般情報を優先してください。事実のない具体性を補わないでください。\n\n'
        '自然で簡潔な日本語を使い、商品説明の長文化と同義反復を避けてください。\\n\\n'
        '商品カセット以外のタイトル、導入、要約、グループ見出し・説明、一般解説、結びに、この記事内の個別商品名・ブランド名・固有仕様・個別商品の参照や誘導を出してはいけません。成分・規格・用途の一般解説は可能ですが、記事内の特定商品には紐づけないでください。\\n\\n'
        '導入は商品の特徴、使い道、読者が気になる点など、読者に伝わる内容から始めてください。架空の体験談や根拠のない状況設定を避けてください。\\n\\n'
        '導入文leadは日本語180文字以上にしてください。上限は設けず、内容に必要な分量で自然に書いてください。確認できない商品の形状・端子位置・性能差を推測で記述しないでください。\\n\\n'
        '結びはテーマについて実用的で自然な情報量を持つ文章にし、個別商品や商品カセットへの誘導を行わないでください。機械的な字数合わせは不要です。\\n\\n'
        '商品カセットでは、企画で確定した商品と割当を維持し、Evidenceで確定した商品・商品画像・購入リンク・掲載順・割当を維持してください。商品説明は2〜3文・おおむね90〜150文字を執筆上の目安とし、確定した切り口に沿って確認済みの特徴・用途を記述してください。情報のない箇所を一般論や推測で埋めず、必要なら短くしてください。1文で十分な商品も許容します。文数・文字数の最低・最高条件はありません。規格や仕様の記載は任意です。書いた規格、性能、効果、互換性は必ず対象商品の根拠と一致させ、別型番や別商品から転用しないでください。\\n\\n'
        'comparison_groupsの割当は既定のまま維持してください。各組の説明には商品の特徴や使い道を自然な読者向けの文章で書いてください。\\n\\n'
        '文脈を壊す不用意な水増し文章、定型的な注意書き、内部QA用語、不要な仕様説明を書かないでください。slug、category、商品参照IDを維持してください。\\n\\n'
        '編集QAで根拠のない具体表現（仕様・数値・性能・効果・使用体験）が指摘されたら、該当表現は削除して再利用せず、確認できた事実と裏付けのある特徴・用途・生活場面を自然に伝える文章へ修正してください。確認できない事実を創作して補完しないでください。\\n\\n'
        '商品ページの掲載タイトルから確認できる名称・型番・数量・寸法等は掲載情報として紹介できます。詳細出典がない場合は性能・耐久性・効能などを断定しないでください。追加の仕様が不明なだけで記事をBLOCKEDにせず、確認できる商品情報だけを記述してください。商品refが違う根拠を転用せず、QAが削除指定した根拠のない主張を言い換えて戻さないでください。\\n\\n'
        '記事の既存順序を維持し、lead・summary・comparison_groups・how_to_choose・conclusionは商品非依存の一般情報としてください。SEO・AEOの検索意図に自然に答え、商品カセットでは短く正確な個別商品説明に限定してください。導入と結びには十分な内容を持たせ、単に商品紹介を言い換えることは避けてください。\n\n'
        f'TOPIC:\n{json.dumps(topic, ensure_ascii=False, sort_keys=True)}\n\n'
        '商品はEvidence.selection.axesの確定済み割当をそのまま使い、組をまたいで並べ替えないでください。選定理由に含まれる未検証の規格・性能を本文へ転用しないでください。\n'
        f'EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}'
        '\nEDITORIAL_PLAN:\n' + json.dumps({key:value for key,value in plan.items() if key != 'products'}, ensure_ascii=False, sort_keys=True)
    )
    if previous_payload is not None and isinstance(gate_feedback, dict):
        measures = gate_feedback.get('measurements') or {}
        failures = gate_feedback.get('qa_failures') or {}
    persona=topic.get('reader_persona') if isinstance(topic,dict) else None
    if isinstance(persona,dict):
        prompt += '\nREADER_PERSONA (follow this reader through introduction, terminology and recommendation): '+json.dumps(persona,ensure_ascii=False)+'\n'
    if previous_payload is not None:
        prompt += (
            '\n\nReturn the complete JSON payload, preserving every unaffected field verbatim. Apply only the concrete failing QA items below; do not rewrite unrelated article prose or change the existing product assignments.\n\n'
            f'PREVIOUS_PAYLOAD:\n{json.dumps(previous_payload, ensure_ascii=False, sort_keys=True)}\n\n'
            f'GATE_FEEDBACK:\n{json.dumps(gate_feedback or {}, ensure_ascii=False, sort_keys=True)}'
        )
    if previous_payload is not None and isinstance(gate_feedback, dict):
        failed = gate_feedback.get('qa_failures') or {}
        semantic = gate_feedback.get('semantic_issue') or {}
        if failed.get('lead_in_range') is False:
            prompt += '\nMANDATORY: Rewrite lead to at least 180 Japanese characters, with no upper limit or padding.\n'
        independent = gate_feedback.get('independent_quality_gate') or {}
        if independent.get('gate') == 'editorial' and independent.get('issues'):
            prompt += ('\nMANDATORY EDITORIAL REVISION: Repair the exact affected product descriptions '
                       'identified in independent_quality_gate. If a closing sentence merely repeats '
                       'the opening or preceding recommendation, remove that sentence or replace it '
                       'with no sentence at all when the fact is redundant. Never manufacture '
                       'a new benefit just to fill a paragraph. Preserve every unaffected product '
                       'and field. Independently re-read the corrected prose before returning JSON.\n')
        if failed.get('semantic_acceptance_pass') is False:
            prompt += ('\nMANDATORY: Correct the cited semantic QA failure, do not invent an unsupported differentiator. '
                       'Use only attributable listing facts or verified source facts for objective product claims; never invent performance or efficacy. '
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
