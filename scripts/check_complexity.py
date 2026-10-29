#!/usr/bin/env python3
"""Fail when any function exceeds the complexity limit (requirement-26, ac-102, ac-103).

Cyclomatic complexity comes from radon, cognitive complexity from complexipy. Every
function, method and nested function under the given paths is measured; the limit applies
to both metrics. A file that does not parse fails the check: a tool that skips a file it
cannot read would report a clean result for code it never measured.

Runner-owned gate stage (``openbrain-client.yaml`` ``transition_gates``)::

    python scripts/check_complexity.py [--max 15] [PATH ...]

Exit status: 0 clean, 1 an offender or an unparsable file, 2 bad usage.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import complexipy
from radon.complexity import cc_visit
from radon.visitors import Class

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = ("genesis_wp", "scripts", "tests")
DEFAULT_MAX = 15


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    function: str
    metric: str  # "cyclomatic", "cognitive" or "parse"
    value: int | None
    detail: str = ""

    def __str__(self) -> str:
        if self.metric == "parse":
            return f"{self.path}:{self.line}: cannot be parsed ({self.detail})"
        return f"{self.path}:{self.line}: {self.function} {self.metric} {self.value}"


def python_files(paths: list[Path]) -> list[Path]:
    files = []
    for path in paths:
        files.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return files


def radon_functions(blocks) -> list:
    """Every function, method and closure radon found, nested ones included, each once.

    ``cc_visit`` lists top-level functions and the methods of top-level classes; closures
    and the methods of nested classes are only reachable through their parents.
    """
    found = {}
    pending = list(blocks)
    while pending:
        block = pending.pop()
        if isinstance(block, Class):
            pending += [*block.methods, *block.inner_classes]
        else:
            found[(block.lineno, block.name)] = block
            pending += block.closures
    return sorted(found.values(), key=lambda b: b.lineno)


def measure(path: Path, limit: int, shown: str) -> list[Finding]:
    """Findings for one file: each function above ``limit``, or the parse failure."""
    try:
        source = path.read_bytes().decode("utf-8")  # not utf-8-sig: a byte order mark fails here
        ast.parse(source, filename=str(path))
        cyclomatic = radon_functions(cc_visit(source))
        cognitive = complexipy.code_complexity(source).functions
    except (SyntaxError, UnicodeDecodeError, ValueError) as err:
        return [Finding(shown, getattr(err, "lineno", None) or 1, "", "parse", None, f"{type(err).__name__}: {err}")]
    findings = [
        Finding(shown, block.lineno, block.fullname, "cyclomatic", block.complexity)
        for block in cyclomatic if block.complexity > limit
    ]
    findings += [
        Finding(shown, function.line_start, function.name, "cognitive", function.complexity)
        for function in cognitive if function.complexity > limit
    ]
    return sorted(findings, key=lambda f: (f.line, f.metric))


def check(paths: list[Path], limit: int, root: Path = ROOT) -> list[Finding]:
    findings = []
    for path in python_files(paths):
        shown = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        findings += measure(path, limit, shown)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help=f"files or directories (default: {' '.join(DEFAULT_PATHS)})")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help=f"highest allowed value (default {DEFAULT_MAX})")
    args = parser.parse_args(argv)
    paths = [p if p.is_absolute() else Path.cwd() / p for p in args.paths] or [ROOT / p for p in DEFAULT_PATHS]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        parser.error(f"no such path: {', '.join(missing)}")
    files = python_files(paths)
    findings = check(paths, args.max)
    for finding in findings:
        print(finding)
    verdict = "FAIL" if findings else "ok"
    print(f"complexity {verdict}: {len(files)} files, limit {args.max} cyclomatic and cognitive, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
