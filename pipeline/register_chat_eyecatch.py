from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def png_dimensions(raw: bytes) -> tuple[int, int]:
    if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise ValueError('ChatGPT eyecatch must be PNG')
    return struct.unpack('>II', raw[16:24])


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    os.replace(tmp, path)


def register(slug: str, source: Path, *, chat_ref: str = '') -> dict:
    raw = source.read_bytes()
    width, height = png_dimensions(raw)
    if (width, height) != (1536, 1024):
        raise ValueError(f'ChatGPT eyecatch dimensions must be 1536x1024, got {width}x{height}')
    target = ROOT / 'img' / slug / f'{slug}.png'
    receipt_path = ROOT / 'data' / 'eyecatch-receipts' / f'{slug}.json'
    atomic_write(target, raw)
    receipt = {
        'generation_method': 'chatgpt_chat',
        'provider': 'chatgpt_chat',
        'image_sha256': sha256_bytes(raw),
        'width': width,
        'height': height,
        'registered_at': datetime.now(timezone.utc).isoformat(),
        'chat_ref': chat_ref or None,
    }
    atomic_write(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
    return {'eyecatch_path': str(target.relative_to(ROOT)), 'receipt_path': str(receipt_path.relative_to(ROOT)), 'receipt': receipt}


def main() -> None:
    parser = argparse.ArgumentParser(description='Register a ChatGPT Chat-generated Sellemy eyecatch.')
    parser.add_argument('slug')
    parser.add_argument('source')
    parser.add_argument('--chat-ref', default='')
    args = parser.parse_args()
    print(json.dumps(register(args.slug, Path(args.source), chat_ref=args.chat_ref), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
