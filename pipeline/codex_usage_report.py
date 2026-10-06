from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_FIELDS = (
    'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
    'uncached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens',
)


def _new_group() -> dict:
    return {
        'turns': 0,
        'token_sums': {field: 0 for field in TOKEN_FIELDS},
        'token_observed_turns': {field: 0 for field in TOKEN_FIELDS},
    }


def summarize(path: Path, *, stage: str | None = None) -> dict:
    groups: dict[tuple[str, str], dict] = defaultdict(_new_group)
    rows = 0
    if not path.is_file():
        return {'rows': 0, 'groups': []}
    for raw in path.read_text(encoding='utf-8').splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if stage and row.get('stage') != stage:
            continue
        rows += 1
        key = (str(row.get('surface') or ''), str(row.get('stage') or ''))
        item = groups[key]
        item['turns'] += 1
        for field in TOKEN_FIELDS:
            value = row.get(field)
            if isinstance(value, int):
                item['token_sums'][field] += value
                item['token_observed_turns'][field] += 1
    result = []
    for (surface, row_stage), item in sorted(groups.items()):
        turns = item['turns']
        output = {'surface': surface, 'stage': row_stage, 'turns': turns}
        for field in TOKEN_FIELDS:
            observed = item['token_observed_turns'][field]
            output[field] = item['token_sums'][field] if observed else None
            output[field + '_observed_turns'] = observed
        observed_total = item['token_observed_turns']['total_tokens']
        observed_uncached = item['token_observed_turns']['uncached_input_tokens']
        output['avg_total_tokens'] = (
            round(item['token_sums']['total_tokens'] / observed_total, 1) if observed_total else None
        )
        output['avg_uncached_input_tokens'] = (
            round(item['token_sums']['uncached_input_tokens'] / observed_uncached, 1) if observed_uncached else None
        )
        result.append(output)
    return {'rows': rows, 'groups': result}


def main() -> None:
    parser = argparse.ArgumentParser(description='Summarize Sellemy Codex turn token usage.')
    parser.add_argument('--stage', default=None)
    parser.add_argument('--path', type=Path, default=ROOT / '.runtime' / 'sellemy-codex' / 'usage.jsonl')
    args = parser.parse_args()
    print(json.dumps(summarize(args.path, stage=args.stage), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
