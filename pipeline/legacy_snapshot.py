"""Lossless rendering of registered, previously published HTML-only articles."""
import hashlib

SCHEMA = 'sellemy-legacy-html-snapshot/v1'


def render_snapshot(payload: dict) -> str:
    if (payload.get('schema') != SCHEMA
            or payload.get('status') != 'LEGACY_SNAPSHOT'
            or payload.get('publish_eligible') is not False):
        raise ValueError('invalid legacy snapshot identity')
    article = payload['catalog_entry']
    if (article.get('slug'), article.get('category')) != (payload.get('slug'), payload.get('category')):
        raise ValueError('legacy snapshot catalog identity mismatch')
    if article.get('status') not in {'published', 'unlisted'}:
        raise ValueError('invalid legacy snapshot listing status')
    document = payload['document']
    order = ('before_main', 'main', 'after_main')
    if set(document) != set(order) or not all(isinstance(document[k], str) for k in order):
        raise ValueError('invalid legacy snapshot document')
    result = ''.join(document[k] for k in order)
    if hashlib.sha256(result.encode('utf-8')).hexdigest() != payload.get('source_sha256'):
        raise ValueError('legacy snapshot is not an exact copy of the approved baseline')
    return result
