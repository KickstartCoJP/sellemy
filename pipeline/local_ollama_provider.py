from __future__ import annotations

import json
import os
import time
import urllib.request


class LocalOllamaError(RuntimeError):
    pass


def generate_json(*, schema: dict, prompt: str, model: str | None = None, timeout: int | None = None) -> tuple[dict, dict]:
    model = model or os.environ.get('SELLEMY_LOCAL_LLM_MODEL', 'qwen3.5:27b')
    endpoint = os.environ.get('SELLEMY_LOCAL_LLM_ENDPOINT', 'http://127.0.0.1:11434/api/chat')
    timeout = timeout or int(os.environ.get('SELLEMY_LOCAL_LLM_TIMEOUT_SECONDS', '300'))
    body = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'think': False,
        'format': schema,
        'options': {
            'temperature': float(os.environ.get('SELLEMY_LOCAL_LLM_TEMPERATURE', '0.1')),
            'num_ctx': int(os.environ.get('SELLEMY_LOCAL_LLM_CONTEXT', '65536')),
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.load(response)
        value = json.loads(envelope['message']['content'])
    except Exception as exc:
        raise LocalOllamaError(f'local ollama generation failed: {type(exc).__name__}') from exc
    if not isinstance(value, dict):
        raise LocalOllamaError('local ollama returned non-object JSON')
    return value, {
        'runtime': 'ollama',
        'model': envelope.get('model') or model,
        'session_persisted': False,
        'cost_usd': 0.0,
        'duration_api_ms': None,
        'duration_ms': int((time.monotonic() - started) * 1000),
        'input_tokens': int(envelope.get('prompt_eval_count') or 0),
        'output_tokens': int(envelope.get('eval_count') or 0),
        'total_tokens': int(envelope.get('prompt_eval_count') or 0) + int(envelope.get('eval_count') or 0),
    }
