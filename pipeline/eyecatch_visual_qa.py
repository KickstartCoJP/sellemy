"""Independent actual-image QA, owned by the image production stage."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


class EyecatchVisualError(RuntimeError):
    pass


def review_image(image: Path, *, title: str) -> dict:
    from api_role_binding import require_codex_settings
    from PIL import Image
    if not image.is_file() or not image.stat().st_size:
        raise EyecatchVisualError('image data missing')
    with Image.open(image) as im:
        im.verify()
    raw = image.read_bytes()
    model, effort = require_codex_settings('bu-codex-sellemy-reviewer', 'independent_review')
    prompt = ('Inspect the actual attached image independently. Reject only clear visual defects: '
              'extra or missing visible fingers where anatomy is clearly wrong, duplicated or impossible hands/arms/legs, '
              'severe anatomical distortion, impossible fused objects or obvious broken geometry, and obvious mismatch to the article topic. '
              'Do not infer a defect from fingers hidden by perspective, natural occlusion, or cropping. '
              'Do not assess text, product selection, previous QA, creator identity, provider, or generation route. '
              'Return only JSON {"verdict":"PASS" or "FAIL","reason":"specific visible observation"}. '
              'Article title: ' + title)
    with tempfile.TemporaryDirectory(prefix='sellemy-image-review-') as work:
        output = Path(work)/'verdict.json'
        command = [os.environ.get('SELLEMY_EDITORIAL_CLAIM_CODEX','/opt/homebrew/bin/codex'), 'exec',
                   '--ignore-user-config','--disable','plugins','--ephemeral','--skip-git-repo-check',
                   '--sandbox','read-only','--model',model,'--config',f'model_reasoning_effort={effort}',
                   '--image',str(image.resolve()),'--output-last-message',str(output),'-']
        try:
            result = subprocess.run(command,input=prompt,text=True,capture_output=True,timeout=210)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise EyecatchVisualError('image reviewer runtime unavailable: '+str(exc)) from exc
        if result.returncode:
            raise EyecatchVisualError('image reviewer runtime failed: '+result.stderr[-350:])
        try:
            verdict=json.loads(output.read_text())
            if not isinstance(verdict,dict) or verdict.get('verdict') not in ('PASS','FAIL') or not isinstance(verdict.get('reason'),str) or not verdict['reason'].strip():
                raise ValueError('invalid verdict')
        except (ValueError,OSError) as exc:
            raise EyecatchVisualError('image reviewer runtime returned invalid verdict') from exc
    if hashlib.sha256(image.read_bytes()).digest()!=hashlib.sha256(raw).digest():
        raise EyecatchVisualError('image changed during independent visual QA')
    if verdict['verdict']!='PASS':
        raise EyecatchVisualError('visual QA failed: '+verdict['reason'])
    return {**verdict,'image_sha256':hashlib.sha256(raw).hexdigest(),'model':model}
