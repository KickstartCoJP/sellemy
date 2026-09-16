from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class EyecatchGenerationError(RuntimeError):
    pass


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _png_dimensions(raw: bytes) -> tuple[int, int]:
    if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise EyecatchGenerationError('generated eyecatch must be PNG')
    width, height = struct.unpack('>II', raw[16:24])
    return width, height


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _prompt(payload: dict, evidence: dict) -> str:
    product_names = [p['h3'] for p in payload['products']]
    return (
        'Create one article-specific editorial eyecatch image for Sellemy, a Japanese product-discovery and comparison media brand. '
        'The result must be a coherent single generated illustration/photo-real editorial visual, not a collage, montage, grid, contact sheet, '
        'catalog layout, screenshot, ad banner, or composition of product listing images. Do not place brand logos, product listing screenshots, '
        'prices, badges, buttons, or long text in the image. Prioritize refined lifestyle/editorial quality, calm clarity, and immediate topical fit. '
        f"Article title: {payload['h1']}. Category: {evidence['category']}. Summary: {payload['summary']}. "
        f"Products/themes represented by the article: {', '.join(product_names)}. "
        'Create exactly one 1536x1024 PNG suitable for both the article hero and OGP.'
    )


class EyecatchGenerator:
    """Fail-closed generative-image adapter. No local composition fallback exists."""

    def __init__(self, *, endpoint: str, token_env: str, provider: str, model: str, timeout_seconds: float = 120.0):
        self.endpoint = endpoint.strip()
        self.token_env = token_env.strip()
        self.provider = provider.strip() or 'configured-provider'
        self.model = model.strip() or 'configured-model'
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_env(cls) -> 'EyecatchGenerator':
        endpoint = os.environ.get('SELLEMY_EYECATCH_ENDPOINT', '')
        token_env = os.environ.get('SELLEMY_EYECATCH_TOKEN_ENV', '')
        provider = os.environ.get('SELLEMY_EYECATCH_PROVIDER', '')
        model = os.environ.get('SELLEMY_EYECATCH_MODEL', '')
        timeout = os.environ.get('SELLEMY_EYECATCH_TIMEOUT_SECONDS', '120')
        return cls(endpoint=endpoint, token_env=token_env, provider=provider, model=model, timeout_seconds=float(timeout))

    def generate(self, *, payload: dict, evidence: dict, output: Path, receipt_path: Path) -> dict:
        if not self.endpoint.startswith('https://'):
            raise EyecatchGenerationError('generative eyecatch endpoint unavailable')
        if not self.token_env or not os.environ.get(self.token_env):
            raise EyecatchGenerationError('generative eyecatch token unavailable')

        request_payload = {
            'prompt': _prompt(payload, evidence),
            'count': 1,
            'width': 1536,
            'height': 1024,
            'format': 'png',
            'model': self.model,
        }
        request_body = json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        request_sha = _sha256_bytes(request_body)
        operation_id = f"sellemy-eyecatch-{evidence['slug']}-{request_sha[:16]}"

        if output.is_file() and receipt_path.is_file():
            try:
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                receipt = None
            if isinstance(receipt, dict) and receipt.get('request_sha256') == request_sha:
                raw = output.read_bytes()
                width, height = _png_dimensions(raw)
                if receipt.get('image_sha256') == _sha256_bytes(raw) and (width, height) == (1536, 1024):
                    return receipt
                raise EyecatchGenerationError('existing eyecatch receipt/file mismatch')

        req = urllib.request.Request(
            self.endpoint,
            data=request_body,
            method='POST',
            headers={
                'Authorization': 'Bearer ' + os.environ[self.token_env],
                'Content-Type': 'application/json',
                'Idempotency-Key': operation_id,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read())
        except Exception as exc:
            raise EyecatchGenerationError('generative eyecatch outcome uncertain; automatic replay forbidden') from exc

        encoded = response_payload.get('image_base64')
        if not isinstance(encoded, str) or not encoded:
            raise EyecatchGenerationError('generative eyecatch response has no image_base64')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise EyecatchGenerationError('generative eyecatch response is invalid base64') from exc
        width, height = _png_dimensions(raw)
        if (width, height) != (1536, 1024):
            raise EyecatchGenerationError(f'generated eyecatch dimensions invalid: {width}x{height}')

        _atomic_write(output, raw)
        receipt = {
            'generation_method': 'generative_ai',
            'provider': self.provider,
            'model': self.model,
            'operation_id': operation_id,
            'request_sha256': request_sha,
            'image_sha256': _sha256_bytes(raw),
            'width': width,
            'height': height,
            'generated_at': datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
        return receipt
