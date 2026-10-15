from __future__ import annotations

import json

from comparison_acceptance import CONTRACT, evidence_content, evidence_issues
from local_ollama_provider import generate_json


def obj(properties):
    return {'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties}


STRING = {'type': 'string'}
BOOL = {'type': 'boolean'}
CLAIM_SCHEMA = obj({
    'unsupported_claims': {'type': 'array', 'items': obj({
        'quote': STRING, 'product_ref': STRING, 'reason': STRING,
    })},
    'material_claims_ok': BOOL,
    'reason': STRING,
})
IDENTITY_SCHEMA = obj({
    'identity_ok': BOOL,
    'comparison_axes_ok': BOOL,
    'needs_product_reselection': BOOL,
    'issues': {'type': 'array', 'items': STRING},
    'reason': STRING,
})


def split_local_review(evidence: dict, payload: dict) -> dict:
    deterministic = evidence_issues(evidence)
    if deterministic:
        return {
            'schema': 'sellemy-local-split-qa/v1',
            'status': 'BLOCKED',
            'needs_product_reselection': True,
            'reason': '; '.join(deterministic),
            'claims': {},
            'identity': {'issues': deterministic},
            'metadata': {'runtime': 'deterministic_precheck', 'cost_usd': 0.0},
        }
    data = json.dumps({'evidence': evidence_content(evidence), 'article': payload}, ensure_ascii=False)
    claims, claim_meta = generate_json(
        schema=CLAIM_SCHEMA,
        prompt=(CONTRACT + '\nTASK 1 ONLY: audit every material factual/comparative claim in the article against the supplied Evidence. '
                'List every unsupported or overextended claim. Cautious wording does not make an unsupported claim acceptable. '
                'Do not judge product identity here. Return JSON only.\nDATA:\n' + data),
    )
    identity, identity_meta = generate_json(
        schema=IDENTITY_SCHEMA,
        prompt=(CONTRACT + '\nTASK 2 ONLY: audit product identity/distinctness/topic fit and whether at least two useful comparison axes are grounded in Evidence. '
                'Ignore prose wording defects. Color, quantity, bundle, seller, and listing wording are not independent products. '
                'If the six-product set cannot be safely compared without reselection or Evidence rebuild, needs_product_reselection=true. '
                'Return JSON only.\nDATA:\n' + data),
    )
    if identity.get('needs_product_reselection') is True or identity.get('identity_ok') is not True or identity.get('comparison_axes_ok') is not True:
        status, reselect = 'BLOCKED', True
        reason = identity.get('reason') or '; '.join(identity.get('issues') or []) or 'Product/Evidence QA failed'
    elif claims.get('material_claims_ok') is not True or claims.get('unsupported_claims'):
        status, reselect = 'REVISION_REQUIRED', False
        reason = claims.get('reason') or 'Unsupported article claims require revision'
    else:
        status, reselect = 'ACCEPTED', False
        reason = identity.get('reason') or claims.get('reason') or 'Local split QA passed'
    return {
        'schema': 'sellemy-local-split-qa/v1',
        'status': status,
        'needs_product_reselection': reselect,
        'reason': reason,
        'claims': claims,
        'identity': identity,
        'metadata': {
            'runtime': 'ollama_split_qa',
            'model': claim_meta.get('model') or identity_meta.get('model'),
            'cost_usd': 0.0,
            'input_tokens': int(claim_meta.get('input_tokens') or 0) + int(identity_meta.get('input_tokens') or 0),
            'output_tokens': int(claim_meta.get('output_tokens') or 0) + int(identity_meta.get('output_tokens') or 0),
            'total_tokens': int(claim_meta.get('total_tokens') or 0) + int(identity_meta.get('total_tokens') or 0),
            'duration_ms': int(claim_meta.get('duration_ms') or 0) + int(identity_meta.get('duration_ms') or 0),
        },
    }
