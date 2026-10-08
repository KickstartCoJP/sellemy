from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from unit_ai_router import UnitAIRouter

ROOT = Path(__file__).resolve().parents[1]
ROUTER = UnitAIRouter(root=ROOT, config_path=ROOT / 'config' / 'purpose_ai_routing.json')

SEMANTIC_SCHEMA = {
    'type': 'object',
    'properties': {
        'decisions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'feature_id': {'type': 'string'},
                    'relation_type': {'type': 'string'},
                    'confidence': {'type': 'number'},
                    'rationale': {'type': 'string'},
                },
            },
        },
        'search_intent_note': {'type': 'string'},
        'overlap_risk': {'type': 'string'},
    },
}

QA_SCHEMA = {
    'type': 'object',
    'properties': {
        'pass': {'type': 'boolean'},
        'feedback': {'type': 'string'},
        'search_intent_pass': {'type': 'boolean'},
        'relation_quality_pass': {'type': 'boolean'},
        'overlap_cannibalization_pass': {'type': 'boolean'},
        'seo_gate_pass': {'type': 'boolean'},
    },
}


def semantic_review(item: dict[str, Any]) -> tuple[dict, dict]:
    prompt = (
        'Purpose Relation Discoveryのsemantic_review候補を判定してください。候補以外へ探索を広げず、'
        '各candidateをcore/supporting/not_relatedのいずれかに判定してください。\nINPUT:\n' +
        json.dumps(item, ensure_ascii=False, sort_keys=True)
    )
    return ROUTER.execute('semantic_review', schema=SEMANTIC_SCHEMA, prompt=prompt, usage_key=str(item.get('slug') or ''))


def independent_qa(payload: dict[str, Any]) -> tuple[dict, dict]:
    prompt = (
        'Purpose Relation/SEOの独立QAを実施してください。生成判断を追認せず、Evidenceと現行Gateから判定してください。\nINPUT:\n' +
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    return ROUTER.execute('independent_qa', schema=QA_SCHEMA, prompt=prompt, usage_key=str(payload.get('feature_id') or ''))
