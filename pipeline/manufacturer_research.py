"""Bounded, one-turn official URL discovery. Search output is never evidence."""
import json
import os
import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor

from product_sources import fetch_manufacturer_source


def research_selected(products, *, cache_only=False, prefer_official=False):
    if cache_only or not products:
        return products
    # Marketplace evidence is valid, but does not suppress the separate
    # manufacturer-source lookup. Preserve both kinds of evidence.
    needs = [p for p in products[:20] if not any(
        s.get('verified') is True and str(s.get('excerpt') or '').strip()
        and s.get('source_type') == 'manufacturer_official'
        for s in p.get('sources', []) if isinstance(s, dict))]
    if not needs:
        return products
    from api_role_binding import require_codex_settings
    model, effort = require_codex_settings('bu-codex-sellemy-planning', 'planning')
    command = shlex.split(os.environ.get('SELLEMY_WRITER_PRIMARY_COMMAND', '/opt/homebrew/bin/codex'))
    prompt = ('Search the web for the product manufacturer’s own product detail page for each item. '
              'Return ONLY JSON {"items":[{"asin":"...","url":"https://..."}]} with at most one URL per ASIN. '
              'Omit uncertain items and marketplaces, retailers, social media, and search pages. '
              'Do not quote page text. Items: ' + json.dumps([
                  {'asin': p['asin'], 'brand': p.get('brand'), 'title': p.get('amazon_title')}
                  for p in needs], ensure_ascii=False))
    try:
        result = subprocess.run([*command, '--search', 'exec', '--ephemeral', '--json',
                                 '--skip-git-repo-check', '--sandbox', 'read-only',
                                 '--model', model, '--config', f'model_reasoning_effort={effort}', '-'], input=prompt, text=True,
                                capture_output=True, timeout=int(os.environ.get("SELLEMY_OFFICIAL_SEARCH_TIMEOUT_SECONDS", "35")))
        if result.returncode:
            return products
        messages = []
        usage = {}
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('type') == 'item.completed' and (event.get('item') or {}).get('type') == 'agent_message':
                messages.append(event['item'].get('text', ''))
            if event.get('type') == 'turn.completed':
                usage = event.get('usage') or {}
        if not messages:
            return products
        raw = messages[-1].strip()
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
        rows = json.loads(raw).get('items', [])
        if not isinstance(rows, list):
            return products
        candidates = {str(row.get('asin')): row.get('url') for row in rows[:20] if isinstance(row, dict)}
        if usage:
            from pathlib import Path
            from codex_provider import _append_usage
            _append_usage(Path(__file__).resolve().parents[1], 'bu-codex-sellemy-writer',
                          stage='source_research', model=model, session_id='',
                          session_turn=1, usage=usage)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return products
    def fetch(product):
        url = candidates.get(product.get('asin'))
        if not url:
            return None
        try:
            return fetch_manufacturer_source(url, product)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=6) as executor:
        sources = list(executor.map(fetch, products))
    return [{**p, 'sources': [*(p.get('sources') or []), source], 'provisional': False}
            if source else p for p, source in zip(products, sources)]
