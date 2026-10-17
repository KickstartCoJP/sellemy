"""Independent, fail-closed six-product image duplicate screening.

Images are supplied by the caller as downloaded bytes, never inferred from ASIN.
Close-looking images are evidence for review, not proof of identical products.
"""
from __future__ import annotations

import hashlib
import io
from itertools import combinations
from urllib.parse import urlsplit, urlunsplit


def canonical_image_url(url: str) -> str:
    parsed = urlsplit(url or '')
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, '', ''))


def _dhash(content: bytes) -> int:
    from PIL import Image, ImageOps  # optional dependency; caller must treat missing as unverified
    with Image.open(io.BytesIO(content)) as im:
        im = ImageOps.exif_transpose(im).convert('L').resize((9, 8))
        px = list(im.getdata())
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (px[y * 9 + x] > px[y * 9 + x + 1])
    return bits


def audit_product_images(products: list[dict], image_bytes: dict[str, bytes], *, threshold: int = 5) -> dict:
    """image_bytes keyed by evidence product ref. Never passes missing/corrupt images."""
    issues = []
    if len(products) != 6:
        issues.append('six independent product images required')
    rows = []
    for idx, p in enumerate(products):
        ref = str(p.get('ref') or f'p{idx+1}')
        url = str(p.get('image_url') or '')
        content = image_bytes.get(ref)
        row = {'ref': ref, 'url': canonical_image_url(url), 'sha256': None, 'dhash': None}
        if not url or not content:
            issues.append(f'{ref}: missing image URL or image bytes')
        else:
            row['sha256'] = hashlib.sha256(content).hexdigest()
            try:
                row['dhash'] = _dhash(content)
            except (ImportError, OSError, ValueError) as exc:
                issues.append(f'{ref}: image could not be visually verified ({type(exc).__name__})')
        rows.append(row)
    suspects = []
    hard_fail_pairs = []
    for a, b in combinations(rows, 2):
        reasons = []
        if a['url'] and a['url'] == b['url']:
            reasons.append('same_image_url')
        if a['sha256'] and a['sha256'] == b['sha256']:
            reasons.append('same_image_bytes')
        if a['dhash'] is not None and b['dhash'] is not None:
            distance = (a['dhash'] ^ b['dhash']).bit_count()
            if distance <= threshold:
                reasons.append(f'visual_similarity_dhash_{distance}')
        if 'same_image_url' in reasons or 'same_image_bytes' in reasons:
            hard_fail_pairs.append({'left': a['ref'], 'right': b['ref'], 'reasons': reasons})
        if reasons:
            suspects.append({'left': a['ref'], 'right': b['ref'], 'reasons': reasons})
    return {
        'status': 'FAIL' if hard_fail_pairs else ('REVIEW_REQUIRED' if issues or suspects else 'PASS'),
        'hard_fail_pairs': hard_fail_pairs,
        'issues': issues, 'suspect_pairs': suspects,
        'image_count': len(rows),
        'requires_product_identity_review': bool(suspects),
        'method': 'url+sha256+dhash64',
    }
