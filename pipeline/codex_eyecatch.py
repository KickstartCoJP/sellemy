from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from codex_provider import CodexProviderError, generate_persistent

ROOT = Path(__file__).resolve().parents[1]
DESIGNER_ROLE = 'bu-codex-sellemy-designer'
GENERATION_METHOD = 'codex_cli_imagegen'


class CodexEyecatchError(RuntimeError):
    pass


RESULT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'output_path', 'generation_route', 'visual_family', 'summary'],
    'properties': {
        'status': {'type': 'string'},
        'output_path': {'type': 'string'},
        'generation_route': {'type': 'string'},
        'visual_family': {'type': 'string'},
        'summary': {'type': 'string'},
    },
}


def _png_dimensions(raw: bytes) -> tuple[int, int]:
    if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise CodexEyecatchError('designer output must be PNG')
    return struct.unpack('>II', raw[16:24])


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _command() -> tuple[str, ...]:
    configured = os.environ.get('SELLEMY_EYECATCH_CODEX_COMMAND', '').strip()
    if configured:
        return tuple(configured.split())
    executable = next((p for p in ('/opt/homebrew/bin/codex', '/usr/local/bin/codex') if Path(p).is_file()), None)
    return (executable,) if executable else ()



def _generated_root() -> Path:
    return (Path.home() / '.codex' / 'generated_images').resolve()

def _model() -> str:
    return (
        os.environ.get('SELLEMY_EYECATCH_CODEX_MODEL', '').strip()
        or os.environ.get('SELLEMY_WRITER_PRIMARY_MODEL', '').strip()
        or 'gpt-6-astra'
    )


def _prompt(*, slug: str, title: str, category: str, evidence: dict, payload: dict, output: Path) -> str:
    category_label = {'beauty': 'Beauty', 'dailygoods': 'Daily Goods', 'gadget': 'Gadget'}.get(category, category)
    products = [str(row.get('h3') or row.get('amazon_title') or '') for row in payload.get('products', [])]
    return (
        'Goal: create the single production article eyecatch PNG for the exact Sellemy article below. '
        'Use the Codex built-in image generation capability/skill (image_gen). Do not call OpenAI Images API, '
        'any paid external image API, browser image generator, or local Python/ImageMagick creative composition. '
        'Do not copy the generated image into the Sellemy repo; leave it in the Codex generated_images directory.\n\n'
        f'Slug: {slug}\nTitle: {title}\nCategory: {category_label}\n'
        f'Summary: {payload.get("summary", "")}\nProducts/themes: {json.dumps(products, ensure_ascii=False)}\n\n'
        'Visual requirements: exactly 1536x1024 PNG (3:2); one coherent editorial lifestyle scene specific to this '
        'article; no collage, grid, contact sheet, product-card montage, screenshot, ad banner, large article title, '
        'price, ranking number, CTA, button, or long copy. A small "Sellemy | '
        + category_label + '" sign is allowed but optional. Avoid reproducing a category-top image. '
        'For Daily Goods prefer a natural lived-in home context with wood/off-white/sage/soft-blue tendencies; '
        'for Beauty prefer refined vanity/skincare context; for Gadget prefer modern living with restrained technology. '
        'Choose the most suitable Visual Family and make the subject immediately understandable without text.\n\n'
        'Return output_path as the exact absolute path of the PNG in the Codex generated_images directory. '
        'Verify that file is PNG and 1536x1024. Do not generate multiple alternatives unless '
        'the first output fails the hard format/content requirements. Final response must report completed only after '
        'the exact file exists and passes verification.'
    )


def _receipt_valid(slug: str) -> bool:
    image = ROOT / 'img' / slug / f'{slug}.png'
    receipt_path = ROOT / 'data' / 'eyecatch-receipts' / f'{slug}.json'
    if not image.is_file() or not receipt_path.is_file():
        return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        raw = image.read_bytes()
        dims = _png_dimensions(raw)
    except (OSError, json.JSONDecodeError, CodexEyecatchError):
        return False
    return (
        receipt.get('generation_method') == GENERATION_METHOD
        and receipt.get('role_id') == DESIGNER_ROLE
        and dims == (1536, 1024)
        and receipt.get('image_sha256') == hashlib.sha256(raw).hexdigest()
    )


def ensure_codex_eyecatch(*, slug: str, title: str, category: str, evidence: dict, payload: dict) -> dict:
    image = ROOT / 'img' / slug / f'{slug}.png'
    receipt_path = ROOT / 'data' / 'eyecatch-receipts' / f'{slug}.json'
    if _receipt_valid(slug):
        return json.loads(receipt_path.read_text(encoding='utf-8'))

    command, model = _command(), _model()
    if not command:
        raise CodexEyecatchError('Codex CLI unavailable for eyecatch designer')
    try:
        result, meta = generate_persistent(
            command, model, RESULT_SCHEMA,
            _prompt(slug=slug, title=title, category=category, evidence=evidence, payload=payload, output=ROOT),
            timeout=int(os.environ.get('SELLEMY_EYECATCH_CODEX_TIMEOUT_SECONDS', '900')),
            root=ROOT, surface_key=DESIGNER_ROLE, stage='eyecatch', usage_key=slug,
            max_turns=int(os.environ.get('SELLEMY_EYECATCH_CODEX_CHECKPOINT_TURNS', '20')),
        )
    except CodexProviderError as exc:
        raise CodexEyecatchError(str(exc)) from exc

    if result.get('status') != 'completed':
        raise CodexEyecatchError(f'designer did not complete: {result.get("status")}')
    reported = Path(str(result.get('output_path') or '')).expanduser()
    try:
        resolved = reported.resolve()
        generated_root = _generated_root()
        if generated_root not in resolved.parents:
            raise CodexEyecatchError('designer output is outside Codex generated_images')
    except OSError as exc:
        raise CodexEyecatchError('designer output path invalid') from exc
    if result.get('generation_route') != 'built-in_image_gen':
        raise CodexEyecatchError('designer did not attest built-in image_gen route')
    if not resolved.is_file():
        raise CodexEyecatchError('designer produced no image file')

    raw = resolved.read_bytes()
    width, height = _png_dimensions(raw)
    if (width, height) != (1536, 1024):
        raise CodexEyecatchError(f'designer image dimensions invalid: {width}x{height}')
    digest = hashlib.sha256(raw).hexdigest()
    _atomic_write(image, raw)
    receipt = {
        'generation_method': GENERATION_METHOD,
        'generation_route': 'built-in_image_gen',
        'role_id': DESIGNER_ROLE,
        'thread_id': meta.get('session_id'),
        'member_binding_revision': meta.get('member_binding_revision'),
        'model': model,
        'visual_family': result.get('visual_family'),
        'summary': result.get('summary'),
        'image_sha256': digest,
        'width': width,
        'height': height,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'token_usage_source': 'codex_rollout.turn_token_usage',
        'image_generation_internal_usage_exposed': False,
        'token_usage': {
            key: meta.get(key) for key in (
                'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'uncached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens',
            )
        },
    }
    _atomic_write(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
    if not _receipt_valid(slug):
        raise CodexEyecatchError('written designer receipt/image failed read-back')
    return receipt
