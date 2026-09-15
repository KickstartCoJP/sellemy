"""Deprecated growth entry point.

Public prose generation used to live in this module. Keeping the filename as a
fail-closed tombstone prevents an old scheduler or operator command from silently
regressing to template-authored copy.
"""
from __future__ import annotations


def main() -> None:
    raise SystemExit(
        'pipeline/grow.py is retired; use pipeline/growth_runtime.py. '
        'No article was generated or published.'
    )


if __name__ == '__main__':
    main()
