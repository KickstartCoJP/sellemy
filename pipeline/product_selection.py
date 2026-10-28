from __future__ import annotations

from analytics_feedback import product_signal
import re
import json
import os
import shlex
import subprocess
import unicodedata
from urllib.parse import urlsplit, urlunsplit


def _normalize_identity_text(value: str) -> str:
    value = unicodedata.normalize('NFKC', str(value or '')).lower()
    return re.sub(r'[^0-9a-zぁ-んァ-ヶ一-龥]+', '', value)


def _canonical_image_url(value: str) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    parts = urlsplit(raw)
    path = re.sub(r'\._[^/]+_(?=\.[A-Za-z0-9]+$)', '', parts.path)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, '', ''))


def content_identity(candidate: dict) -> tuple[str, str]:
    return (_normalize_identity_text(candidate.get('amazon_title') or ''), _canonical_image_url(candidate.get('image_url') or ''))


_COLOR_VARIANT = re.compile(r"(?:\s*[（(]\s*(?:アクア|ブルー|クリア|ホワイト|ブラック|グレー|ベージュ|ピンク|レッド|グリーン|イエロー|パープル|オレンジ|ネイビー|シルバー|ゴールド|透明|白|黒|青|赤|緑|灰色)\s*[）)])\s*$")


DUPLICATE_CHECK_ITEMS = (
    'same_product_body', 'color_design_only', 'sales_packaging_only',
    'size_capacity_difference', 'function_structure_difference',
    'series_generation_difference', 'manufacturer_brand_relation',
    'independent_comparison_value',
)


def _color_variant_identity(candidate: dict) -> str:
    title = str(candidate.get('amazon_title') or '').strip()
    stem = _COLOR_VARIANT.sub('', title)
    if stem == title:
        return ''
    brand = _normalize_identity_text(candidate.get('brand') or '')
    key = _normalize_identity_text(stem)
    return f'{brand}:{key}' if brand and len(key) >= 12 else ''


def duplicate_check(a: dict, b: dict) -> dict:
    """Itemized, evidence-conservative pair screen. UNKNOWN never proves duplication."""
    fields = {key: 'UNKNOWN' for key in DUPLICATE_CHECK_ITEMS}
    reasons = []
    left, right = content_identity(a), content_identity(b)
    left_asin, right_asin = str(a.get('asin') or ''), str(b.get('asin') or '')
    color_a, color_b = _color_variant_identity(a), _color_variant_identity(b)
    duplicate = False
    if left_asin and left_asin == right_asin:
        fields['same_product_body'] = 'FAIL'
        reasons.append('same_asin')
        duplicate = True
    elif left[0] and left[0] == right[0]:
        fields['same_product_body'] = 'FAIL'
        reasons.append('normalized_title_exact')
        duplicate = True
    elif left[1] and left[1] == right[1]:
        fields['same_product_body'] = 'FAIL'
        reasons.append('canonical_image_exact')
        duplicate = True
    elif color_a and color_a == color_b:
        fields['color_design_only'] = 'FAIL'
        reasons.append('same_named_model_color_variant')
        duplicate = True
    return {'status': 'DUPLICATE' if duplicate else 'UNDETERMINED',
            'items': fields, 'reasons': reasons, 'pair': [left_asin, right_asin]}


def dedupe_content_products(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, removed = [], []
    for candidate in candidates:
        match = next(((previous, duplicate_check(previous, candidate)) for previous in kept
                      if duplicate_check(previous, candidate)['status'] == 'DUPLICATE'), None)
        if match:
            previous, check = match
            removed.append({'asin': candidate.get('asin'), 'duplicate_of_asin': previous.get('asin'),
                            'reason': check['reasons'][0], 'check_items': check['items']})
        else:
            kept.append(candidate)
    return kept, removed


class ProductSelectionError(RuntimeError):
    pass


def _text(candidate: dict) -> str:
    parts = []
    for source in candidate.get('sources') or []:
        if isinstance(source, dict) and source.get('verified') is True:
            parts.append(str(source.get('excerpt') or '').lower())
    return ' '.join(parts)


def _axis_matches(candidate: dict, axes: list[dict]) -> set[str]:
    text = _text(candidate)
    return {axis['id'] for axis in axes if any(str(word).lower() in text for word in axis.get('keywords', []))}


def identification_priority(candidate: dict) -> int:
    """Tie-break only; absence never rejects a product."""
    sources = [s for s in candidate.get('sources') or []
               if isinstance(s, dict) and s.get('verified') is True]
    official = any(s.get('source_type') == 'manufacturer_official' for s in sources)
    # A model identifier must be explicitly supplied by an identified product source.
    # Do not infer a model from a brand name, product title or ASIN.
    model = bool(str(candidate.get('model_number') or '').strip()) and bool(sources)
    return int(model) + int(official)


def select_six(candidates: list[dict], topic: dict, feedback: dict, *, pinned: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Select six by AI from twenty independently identifiable, evidenced products."""
    from comparison_acceptance import source_text
    pinned = list(pinned or [])
    distinct, removed = dedupe_content_products([*pinned, *candidates])
    axes = topic['comparison_axes']

    def eligible(item):
        corpus = source_text(item).lower()
        return bool(_text(item).strip() and corpus and any(
            str(word).lower() in corpus for axis in axes for word in axis.get('keywords', [])))

    pool = [p for p in distinct if eligible(p)]
    if len(pool) < 20:
        raise ProductSelectionError(f'needs_product_reselection: {len(pool)} eligible candidates; require 20')
    pinned_asins = {p['asin'] for p in pinned}
    if not pinned_asins.issubset({p['asin'] for p in pool}):
        raise ProductSelectionError('retained product lacks verified topic-axis evidence')
    pool = [*pinned, *[p for p in pool if p['asin'] not in pinned_asins]][:20]
    if len(pinned_asins) > 6:
        raise ProductSelectionError('more than six pinned products')
    items = [{
        'asin': p['asin'], 'title': p.get('amazon_title'), 'brand': p.get('brand'),
        'price': p.get('observed_price'), 'verified_facts': _text(p)[:2400],
        'covered_axes': sorted(_axis_matches(p, axes)),
    } for p in pool]
    prompt = ("""あなたはSellemyの商品選定担当です。モノとの出会いを大切にし、20商品から記事に掲載する異なる6商品を選んでください。
検索順位や形式的なスコアで決めず、読者の購入目的、商品それぞれの特徴・用途・使用場面、選択肢としての違いを根拠情報から判断してください。
確認できない事実や使用経験を作らないでください。同一商品の色違い、パッケージ違い、実質的な重複を選ばないでください。
retained_asinsに記載した独立審査済みの商品は必ず維持してください。
出力はJSONのみ。形式は {"selected_asins":["ASIN",...]} とし、入力候補に含まれる相異なるASINをちょうど6件指定してください。
"""+json.dumps({'topic': {k:topic.get(k) for k in ('title','query','intent_key','reader_persona','comparison_axes')},
                   'retained_asins': sorted(pinned_asins), 'candidates':items}, ensure_ascii=False))
    command = shlex.split(os.environ.get('SELLEMY_PRODUCT_SELECTOR_COMMAND',
                                          os.environ.get('SELLEMY_PLANNING_COMMAND', '/opt/homebrew/bin/codex')))
    try:
        result = subprocess.run([*command, 'exec', '--ephemeral', '--json',
                                 '--skip-git-repo-check', '--sandbox', 'read-only',
                                 '--model', os.environ.get('SELLEMY_PRODUCT_SELECTOR_MODEL', 'gpt-6-sol'), '-'],
                                input=prompt, text=True, capture_output=True,
                                timeout=int(os.environ.get('SELLEMY_PRODUCT_SELECTOR_TIMEOUT_SECONDS', '300')))
        if result.returncode:
            raise ProductSelectionError('AI selection provider failed')
        messages = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'item.completed' and (event.get('item') or {}).get('type') == 'agent_message':
                messages.append(event['item'].get('text', ''))
        if not messages:
            raise ProductSelectionError('AI selection returned no output')
        raw = messages[-1].strip()
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
        selected_asins = json.loads(raw)['selected_asins']
    except ProductSelectionError:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError) as exc:
        raise ProductSelectionError(f'AI product selection failed: {type(exc).__name__}') from exc
    by_asin = {p['asin']:p for p in pool}
    if (not isinstance(selected_asins, list) or len(selected_asins) != 6
            or len(set(selected_asins)) != 6 or any(asin not in by_asin for asin in selected_asins)
            or not pinned_asins.issubset(set(selected_asins))):
        raise ProductSelectionError('AI selection returned invalid six ASINs or omitted retained products')
    selected = [by_asin[asin] for asin in selected_asins]
    covered = set().union(*(_axis_matches(p, axes) for p in selected))
    if len(covered) < 2:
        raise ProductSelectionError('AI selected six lack two grounded topic axes')
    return selected, {
        'method': 'ai_selection_from_20', 'candidate_count': len(pool),
        'comparison_axes_covered': sorted(covered),
        'brand_count': len({str(p.get('brand') or '').lower() for p in selected if p.get('brand')}),
        'content_duplicates_removed': removed,
        'retained_qa_passed_asins': sorted(pinned_asins),
    }
