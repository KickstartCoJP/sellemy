from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from unit_ai_router import UnitAIRouter

ROOT = Path(__file__).resolve().parents[1]
ROUTER = UnitAIRouter(root=ROOT, config_path=ROOT / 'config' / 'short_ai_routing.json')

CREATIVE_SCHEMA = {
    'type': 'object',
    'properties': {
        'hook': {'type': 'string'},
        'narration': {'type': 'string'},
        'scenes': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'start_seconds': {'type': 'number'}, 'end_seconds': {'type': 'number'},
            'visual': {'type': 'string'}, 'on_screen_text': {'type': 'string'}, 'motion': {'type': 'string'},
        }}},
        'audio_direction': {'type': 'string'},
        'cta_direction': {'type': 'string'},
        'metadata_direction': {'type': 'string'},
        'rights_notes': {'type': 'string'},
    },
}

QA_SCHEMA = {
    'type': 'object',
    'properties': {
        'pass': {'type': 'boolean'},
        'feedback': {'type': 'string'},
        'exact_asset_sha256': {'type': 'string'},
        'hook_pass': {'type': 'boolean'},
        'pacing_pass': {'type': 'boolean'},
        'audio_pass': {'type': 'boolean'},
        'visual_editorial_pass': {'type': 'boolean'},
        'rights_pass': {'type': 'boolean'},
        'publish_suitability_pass': {'type': 'boolean'},
    },
}


def creative_authoring(brief: dict[str, Any]) -> tuple[dict, dict]:
    prompt = (
        'Sellemy ShortのCreative Specを作成してください。1商品を主役とし、Evidence外の事実や権利状態を捏造しないでください。\nINPUT:\n' +
        json.dumps(brief, ensure_ascii=False, sort_keys=True)
    )
    return ROUTER.execute('creative_authoring', schema=CREATIVE_SCHEMA, prompt=prompt, usage_key=str(brief.get('video_id') or brief.get('job_id') or ''))


def independent_creative_qa(review_input: dict[str, Any]) -> tuple[dict, dict]:
    prompt = (
        'Sellemy Shortの独立Creative QAを実施してください。Creative Authoringを追認せず、exact assetとEvidenceから判定してください。\nINPUT:\n' +
        json.dumps(review_input, ensure_ascii=False, sort_keys=True)
    )
    return ROUTER.execute('independent_creative_qa', schema=QA_SCHEMA, prompt=prompt, usage_key=str(review_input.get('video_id') or review_input.get('job_id') or ''))
