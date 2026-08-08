#!/usr/bin/env python3
"""Run a scan using credentials from a local env file (KEY=VALUE lines).

For interactive/local use where exporting secrets on the command line is
undesirable. CI passes real env vars instead and never uses this.

    python3 scripts/scan_local.py /path/to/creds.env [run_scan args...]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    env_path = sys.argv[1]
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    from scanner import run_scan
    return run_scan.main(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
