"""Rotate historical Amazon access failures back through normal Planning QA."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

QUEUE = Path.home() / 'Library/Application Support/Sellemy/growth-recovery/amazon-search-retry-queue.json'


def next_themes(batch_size: int = 20) -> list[str]:
    if not QUEUE.is_file():
        return []
    record = json.loads(QUEUE.read_text(encoding='utf-8'))
    entries = [row for row in record.get('entries', []) if row.get('status') == 'pending']
    if not entries:
        return []
    index = int(record.get('cursor', 0)) % len(entries)
    batch = [entries[(index + step) % len(entries)]['slug']
             for step in range(min(batch_size, len(entries)))]
    record['cursor'] = (index + len(batch)) % len(entries)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=QUEUE.parent,
                                     prefix='.amazon-search-retry-', delete=False) as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        temporary = Path(stream.name)
    os.replace(temporary, QUEUE)
    return batch


def mark_restarted(slug: str) -> None:
    if not QUEUE.is_file():
        return
    record = json.loads(QUEUE.read_text(encoding='utf-8'))
    matching = [row for row in record.get('entries', [])
                if row.get('slug') == slug and row.get('status') == 'pending']
    if not matching:
        return
    for row in matching:
        row['status'] = 'restarted'
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=QUEUE.parent,
                                     prefix='.amazon-search-retry-', delete=False) as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        temporary = Path(stream.name)
    os.replace(temporary, QUEUE)
