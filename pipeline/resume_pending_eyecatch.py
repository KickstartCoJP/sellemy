from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from eyecatch_owner_bridge import pending_dir
from growth_runtime import ROOT, _publish
from publish_gate import PublishGate
from publish_payload import run as publish_payload


def resume(slug: str) -> dict:
    pending = pending_dir(slug)
    req = json.loads((pending / 'request.json').read_text(encoding='utf-8'))
    evidence = json.loads((pending / 'evidence.json').read_text(encoding='utf-8'))
    payload = json.loads((pending / 'payload.json').read_text(encoding='utf-8'))
    image = pending / 'eyecatch.png'
    receipt = pending / 'receipt.json'
    if not image.is_file() or not receipt.is_file():
        raise RuntimeError('pending ChatGPT Chat eyecatch image/receipt missing')
    category = str(evidence['category'])
    with PublishGate(ROOT).acquire() as gate:
        targets = {
            ROOT / 'data' / 'evidence' / f'{slug}.json': pending / 'evidence.json',
            ROOT / 'data' / 'payloads' / f'{slug}.json': pending / 'payload.json',
            ROOT / 'img' / slug / f'{slug}.png': image,
            ROOT / 'data' / 'eyecatch-receipts' / f'{slug}.json': receipt,
        }
        for target, source in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        applied = publish_payload(slug, apply=True, allow_existing=False)
        if not applied.get('applied'):
            raise RuntimeError('publication stage refused pending article')
        commit = _publish(slug, category)
    result = {
        'slug': slug, 'published': True, 'commit': commit, 'gate': gate,
        'article_metadata': applied.get('articles_json', {}).get('metadata'),
        'task_id': req.get('task_id'), 'message_id': req.get('message_id'),
        'completed_at': datetime.now(timezone.utc).isoformat(),
    }
    (pending / 'completed.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('slug')
    args = parser.parse_args()
    print(json.dumps(resume(args.slug), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
