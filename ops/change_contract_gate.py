#!/usr/bin/env python3
"""Fail-closed change-scope check. Keep publication commits on the existing path."""
from __future__ import annotations
import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZERO = '0' * 40
PREFIX = 'Publish Writer-generated growth article: '
CONTRACT_DIR = 'change-contracts/'


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def changed_paths(base: str, head: str) -> set[str]:
    return set(filter(None, git('diff', '--name-only', '--diff-filter=ACDMRT', base, head).splitlines()))


def publication_paths(slug: str, category: str) -> set[str]:
    return {
        f'article/{category}/{slug}.html', f'img/{slug}/{slug}.png',
        f'data/acceptance/{slug}.json', f'data/evidence/{slug}.json',
        f'data/payloads/{slug}.json', f'data/eyecatch-receipts/{slug}.json',
        'data/sellemy.db', 'json/articles.json', 'json/products.json',
        'index.html', 'sitemap.xml',
    }


def is_publication_only(changes: set[str], base: str, head: str) -> bool:
    """Preserve only the existing verified, single-article publication unit."""
    titles = git('log', '--format=%s', f'{base}..{head}').splitlines()
    if len(titles) != 1 or not titles[0].startswith(PREFIX):
        return False
    slug = titles[0][len(PREFIX):]
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
        return False
    articles = [p for p in changes if re.fullmatch(r'article/[^/]+/' + re.escape(slug) + r'\.html', p)]
    return len(articles) == 1 and changes == publication_paths(slug, articles[0].split('/')[1])


def validate_contract(changes: set[str], head: str) -> list[str]:
    errors: list[str] = []
    contracts = sorted(p for p in changes if p.startswith(CONTRACT_DIR) and p.endswith('.json'))
    if len(contracts) != 1:
        return ['development change must include exactly one change-contracts/*.json file']
    path = contracts[0]
    try:
        data = json.loads(git('show', f'{head}:{path}'))
    except (subprocess.CalledProcessError, ValueError):
        return ['change contract file is missing or invalid JSON']
    required_strings = ('task_id', 'instruction_ref', 'instruction', 'implementer')
    for name in required_strings:
        if not isinstance(data.get(name), str) or not data[name].strip():
            errors.append(f'missing contract field: {name}')
    if data.get('schema') != 'kickstart-change-contract/v1':
        errors.append('invalid change contract schema')
    for key in ('canonical_refs', 'allowed_paths', 'preserved_rules', 'acceptance_tests'):
        value = data.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
            errors.append(f'contract {key} must be a non-empty list')
    allowed = data.get('allowed_paths', [])
    if isinstance(allowed, list) and all(isinstance(v, str) for v in allowed):
        for p in allowed:
            if p.startswith('/') or '..' in Path(p).parts or any(ch in p for ch in '*?[]'):
                errors.append(f'allowed path must be an exact repository path: {p}')
        outside = changes - set(allowed)
        if outside:
            errors.append(f'out-of-scope changed paths: {sorted(outside)}')
        if path not in allowed:
            errors.append('contract must allow its own path')
    tests = data.get('acceptance_tests', [])
    if isinstance(tests, list):
        for p in tests:
            if not isinstance(p, str) or not p.startswith('tests/') or p not in allowed:
                errors.append(f'acceptance test must be an explicitly allowed tests/ path: {p}')
            elif not (ROOT / p).is_file():
                errors.append(f'acceptance test does not exist: {p}')
    if not any(p.startswith('tests/') for p in changes):
        errors.append('development changes require an acceptance/regression test change')
    # Never treat an implementation-authored approval field as independent approval.
    # GitHub's separate branch review/CI must evaluate contract correctness.
    return errors


def check(base: str, head: str, *, destination: str) -> list[str]:
    paths = changed_paths(base, head)
    if not paths:
        return []
    if destination == 'refs/heads/main' and is_publication_only(paths, base, head):
        return []
    if destination == 'refs/heads/main':
        return ['direct development pushes to main are forbidden; submit a reviewed PR']
    return validate_contract(paths, head)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base')
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--destination', default='refs/heads/development')
    parser.add_argument('--pre-push', action='store_true')
    args = parser.parse_args()
    checks = []
    if args.pre_push:
        for line in sys.stdin:
            fields = line.split()
            if len(fields) != 4:
                print('change-contract: invalid pre-push ref update', file=sys.stderr)
                return 1
            _, local_sha, remote_ref, remote_sha = fields
            if local_sha == ZERO:
                continue
            base = remote_sha if remote_sha != ZERO else git('merge-base', local_sha, 'refs/remotes/origin/main')
            checks.append((base, local_sha, remote_ref))
    else:
        if not args.base:
            parser.error('--base is required outside --pre-push')
        checks.append((args.base, args.head, args.destination))
    failures = []
    for base, head, destination in checks:
        try:
            failures += check(base, head, destination=destination)
        except (subprocess.CalledProcessError, OSError) as exc:
            failures.append(f'cannot verify complete git change: {type(exc).__name__}')
    for failure in failures:
        print('change-contract: DENIED: ' + failure, file=sys.stderr)
    if not failures:
        print('change-contract: PASS')
    return int(bool(failures))

if __name__ == '__main__':
    raise SystemExit(main())
