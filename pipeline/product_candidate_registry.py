"""Persistent, cross-job product eligibility register. An NG ASIN is never eligible."""
from __future__ import annotations
import json, os, tempfile
from pathlib import Path

REGISTRY = Path(os.environ.get('SELLEMY_CANDIDATE_REGISTRY', str(Path.home() / 'Library/Application Support/Sellemy/product-candidates.json'))).expanduser()

def read_registry() -> dict:
    if not REGISTRY.exists():
        return {}
    value = json.loads(REGISTRY.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('product candidate registry must be an object')
    return value

def is_ng(asin: str) -> bool:
    return (read_registry().get(str(asin).upper()) or {}).get('ng') is True

def set_ng(asin: str, reason: str, *, source: str = 'review') -> None:
    asin = str(asin).upper()
    if not asin or not str(reason).strip():
        raise ValueError('ASIN and NG reason are required')
    data = read_registry()
    data[asin] = {**data.get(asin, {}), 'ng': True, 'reason': reason, 'source': source}
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.product-candidates-', dir=REGISTRY.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush(); os.fsync(f.fileno())
        os.replace(name, REGISTRY)
    finally:
        if os.path.exists(name): os.unlink(name)
