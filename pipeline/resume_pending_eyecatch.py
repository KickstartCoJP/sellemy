from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from eyecatch_owner_bridge import pending_dir
from growth_role_business import ROOT


def resume(slug: str) -> dict:
    pending = pending_dir(slug)
    req = json.loads((pending / 'request.json').read_text(encoding='utf-8'))
    evidence = json.loads((pending / 'evidence.json').read_text(encoding='utf-8'))
    payload = json.loads((pending / 'payload.json').read_text(encoding='utf-8'))
    image = pending / 'eyecatch.png'
    receipt = pending / 'receipt.json'
    if not image.is_file() or not receipt.is_file():
        raise RuntimeError('pending ChatGPT Chat eyecatch image/receipt missing')
    from growth_recovery import load_job, save_eyecatch
    from eyecatch_router import _existing_valid
    state = load_job(slug)
    if state.get('current_stage') != 'OWNER_EYECATCH_PENDING':
        raise RuntimeError('article does not await Owner eyecatch delivery')
    from eyecatch_visual_qa import review_image
    verified = _existing_valid(slug, image, receipt)
    if not verified:
        raise RuntimeError('Owner image identity/dimensions audit failed')
    visual = review_image(image, title=payload['h1'])
    save_eyecatch(slug, receipt={**verified,'visual_qa':visual}, image_path=image)
    result = {
        'slug': slug, 'published': False, 'returned_to_stage': 'PUBLISH_PENDING',
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
