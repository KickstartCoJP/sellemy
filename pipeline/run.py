"""Deprecated public-copy rewriting entry point.

The production growth route is pipeline/growth_runtime.py. This tombstone is
intentional: legacy invocations must stop before touching article HTML.
"""
from __future__ import annotations


def main() -> None:
    raise SystemExit(
        'pipeline/run.py public-copy rewriting is retired; use '
        'pipeline/growth_runtime.py. No article was changed.'
    )


if __name__ == '__main__':
    main()
