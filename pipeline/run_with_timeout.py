from __future__ import annotations
import os, signal, subprocess, sys

def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit('usage: run_with_timeout.py SECONDS COMMAND [ARG ...]')
    timeout = float(sys.argv[1])
    if timeout <= 0:
        raise SystemExit('timeout must be positive')
    command = sys.argv[2:]
    process = subprocess.Popen(command, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        print(f'command timed out after {timeout:g}s: {command[0]}', file=sys.stderr)
        return 124

if __name__ == '__main__':
    raise SystemExit(main())
