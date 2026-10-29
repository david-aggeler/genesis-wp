#!/usr/bin/env python3
"""Fail when line-and-branch coverage of genesis_wp falls below the floor (requirement-26, ac-101).

Runs the full test suite under coverage with the committed ``.coveragerc`` (source
genesis_wp, branches on, ``fail_under``). A failing test fails the check too: coverage of a
red suite proves nothing.

Runner-owned gate stage (``openbrain-client.yaml`` ``transition_gates``)::

    python scripts/check_coverage.py [--root DIR] [-- PYTEST_ARGS ...]

Exit status: 0 at or above the floor with a green suite, otherwise non-zero (pytest's code).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(root: Path, pytest_args: list[str]) -> int:
    config = root / ".coveragerc"
    if not config.is_file():
        print(f"coverage FAIL: no {config}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="check-coverage-") as data:
        # Own data file: a concurrent run in the same checkout cannot mix into this measurement.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE_", "COVERAGE_"))}
        env["COVERAGE_FILE"] = str(Path(data) / ".coverage")
        command = [
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
            "--cov", f"--cov-config={config}", "--cov-report=term-missing", *pytest_args,
        ]
        code = subprocess.run(command, cwd=root, env=env).returncode
    print(f"coverage {'ok' if code == 0 else 'FAIL'}: pytest exit status {code}")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root holding .coveragerc (default: this repo)")
    parser.add_argument("pytest_args", nargs="*", help="extra pytest arguments, after --")
    args = parser.parse_args(argv)
    return run(args.root.resolve(), args.pytest_args)


if __name__ == "__main__":
    sys.exit(main())
