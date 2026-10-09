"""Offline reviewer mocks; never production acceptance receipts."""
import json
from pathlib import Path
from comparison_acceptance import make_receipt

FIXTURES = Path(__file__).parent / 'fixtures' / 'quality_recovery'


def fixture(name='good-reading-stands'):
    return tuple(json.loads((FIXTURES / f'{name}.{kind}.json').read_text())
                 for kind in ('payloads', 'evidence'))


def review(evidence, *, article=True):
    from product_selection import _axis_matches
    return {
        'status': 'ACCEPTED', 'needs_product_reselection': False,
        'reason': 'Offline mock: distinct source identities, dimensions and use-specific guidance.',
        **{k: True for k in ('distinct_products', 'meaningful_comparison', 'sufficient_evidence')},
        **{k: article for k in ('claims_grounded', 'buying_guidance', 'complete_comparison')},
        'products': [{
            'ref': p['ref'], 'identity_key': ' '.join(p['amazon_title'].split(' ')[:2]),
            'identity_quote': ' '.join(p['amazon_title'].split(' ')[:2]),
            'adequate_facts': True, 'topic_fit': True,
            'reason': 'Offline source-supported dimensions distinguish user fit.',
            'axis_support': [{'axis_id': a, 'quote': next(
                word for axis in evidence['comparison_axes'] if axis['id'] == a
                for word in axis['keywords'] if word in p['amazon_title'])}
                for a in sorted(_axis_matches(p, evidence['comparison_axes']))],
        } for p in evidence['products']],
    }


def independent(evidence, payload=None):
    return make_receipt(evidence, review(evidence, article=payload is not None),
                        {'model': 'offline-review-mock'}, payload)


def accepted_fixture():
    payload, evidence = fixture()
    evidence['product_acceptance'] = independent(evidence)
    return payload, evidence, independent(evidence, payload)
