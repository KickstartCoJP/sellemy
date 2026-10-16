"""Bounded, one-turn official URL discovery. Search output is never evidence."""
import json
import os
import re
import shlex
import subprocess

from product_sources import fetch_manufacturer_source


def research_selected(products, *, cache_only=False):
    if cache_only or not products:
        return products
    needs = [p for p in products[:6] if not any(
        s.get('source_type') == 'manufacturer_official' and s.get('verified')
        for s in p.get('sources', []) if isinstance(s, dict))]
    if not needs:
        return products
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
                                 '--model', 'gpt-6-sol', '-'], input=prompt, text=True,
                                capture_output=True, timeout=120)
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
        candidates = {str(row.get('asin')): row.get('url') for row in rows[:6] if isinstance(row, dict)}
        if usage:
            from pathlib import Path
            from codex_provider import _append_usage
            _append_usage(Path(__file__).resolve().parents[1], 'bu-codex-sellemy-writer',
                          stage='source_research', model='gpt-6-sol', session_id='',
                          session_turn=1, usage=usage)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return products
    enriched = []
    for product in products:
        source = fetch_manufacturer_source(candidates.get(product.get('asin')), product)
        enriched.append({**product, 'sources': [*(product.get('sources') or []), source]} if source else product)
    return enriched
