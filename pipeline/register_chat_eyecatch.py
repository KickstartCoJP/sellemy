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
PENDING_ROOT = Path.home() / 'Library/Application Support/Sellemy/pending-eyecatch'


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


def register(slug: str, source: Path, *, chat_ref: str = '', pending: bool = False) -> dict:
    raw = source.read_bytes()
    width, height = png_dimensions(raw)
    if (width, height) != (1536, 1024):
        raise ValueError(f'ChatGPT eyecatch dimensions must be 1536x1024, got {width}x{height}')
    if pending:
        pending_dir = PENDING_ROOT / slug
        target = pending_dir / 'eyecatch.png'
        receipt_path = pending_dir / 'receipt.json'
    else:
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
    def shown(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)
    return {'eyecatch_path': shown(target), 'receipt_path': shown(receipt_path), 'receipt': receipt, 'pending': pending}


def main() -> None:
    parser = argparse.ArgumentParser(description='Register a ChatGPT Chat-generated Sellemy eyecatch.')
    parser.add_argument('slug')
    parser.add_argument('source')
    parser.add_argument('--chat-ref', default='')
    parser.add_argument('--pending', action='store_true')
    args = parser.parse_args()
    print(json.dumps(register(args.slug, Path(args.source), chat_ref=args.chat_ref, pending=args.pending), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
