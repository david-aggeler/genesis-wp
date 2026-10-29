"""Coverage and complexity checks and their gate declaration (requirement-26).

The checks run here only on synthetic trees in ``tmp_path``. Running them over this
repository is a runner-owned gate stage, not part of the suite.
"""

from __future__ import annotations

import configparser
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parent.parent
RUNGS = ("prose", "static", "unit", "integration", "system")
COMPLEXITY = ["python", "scripts/check_complexity.py"]
COVERAGE = ["python", "scripts/check_coverage.py"]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def _ifs(count: int) -> str:
    """A function with ``count`` sequential ifs: cyclomatic count + 1, cognitive count."""
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(count))
    return f"def f(x):\n{body}    return -1\n"


def _nested(depth: int) -> str:
    """Nested ifs: cyclomatic depth + 1, cognitive 1 + 2 + ... + depth."""
    lines = ["def f(x):"]
    for level in range(depth):
        lines.append("    " * (level + 1) + f"if x > {level}:")
    lines.append("    " * (depth + 1) + "return 1")
    lines.append("    return 0")
    return "\n".join(lines) + "\n"


def _complexity(tmp_path: Path, source: str | bytes, limit: int = 15):
    path = tmp_path / "subject.py"
    path.write_bytes(source if isinstance(source, bytes) else source.encode())
    return _script("check_complexity").check([path], limit, root=tmp_path)


# ---- complexity (ac-102, ac-103) ------------------------------------------------------


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-102
def test_function_at_the_limit_passes(tmp_path):
    assert _complexity(tmp_path, _ifs(14)) == []  # cyclomatic 15, cognitive 14


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-102
def test_function_above_the_limit_is_named_with_both_metrics(tmp_path):
    findings = _complexity(tmp_path, _ifs(16))  # cyclomatic 17, cognitive 16
    assert {(f.function, f.metric, f.value) for f in findings} == {("f", "cyclomatic", 17), ("f", "cognitive", 16)}
    assert all(str(f).startswith("subject.py:1: f ") for f in findings)


def test_cognitive_alone_fails(tmp_path):
    findings = _complexity(tmp_path, _nested(6))  # cyclomatic 7, cognitive 21
    assert [(f.metric, f.value) for f in findings] == [("cognitive", 21)]


def test_cyclomatic_alone_fails(tmp_path):
    operands = " and ".join(f"x{i}" for i in range(17))  # radon: 1 + 16, complexipy: 1
    findings = _complexity(tmp_path, f"def f({', '.join(f'x{i}' for i in range(17))}):\n    return {operands}\n")
    assert [(f.metric, f.value) for f in findings] == [("cyclomatic", 17)]


def test_closures_and_nested_class_methods_are_measured(tmp_path):
    source = "def outer():\n" + "".join("    " + line + "\n" for line in _ifs(16).replace("def f", "def inner").splitlines())
    source += "    return inner\n\nclass A:\n    class B:\n" + "".join("        " + line + "\n" for line in _ifs(16).replace("def f(x)", "def m(self, x)").splitlines())
    cyclomatic = {f.function for f in _complexity(tmp_path, source) if f.metric == "cyclomatic"}
    assert cyclomatic == {"inner", "B.m"}


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-103
@pytest.mark.parametrize("source", [b"\xef\xbb\xbfx = 1\n", b"def f(:\n    pass\n", b"x = '\xff'\n"])
def test_unparsable_file_fails(tmp_path, source):
    findings = _complexity(tmp_path, source)
    assert [f.metric for f in findings] == ["parse"]
    assert "cannot be parsed" in str(findings[0])


def test_complexity_exit_status(tmp_path, capsys):
    check = _script("check_complexity")
    (tmp_path / "ok.py").write_text(_ifs(3))
    assert check.main([str(tmp_path / "ok.py")]) == 0
    (tmp_path / "bad.py").write_text(_ifs(16))
    assert check.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "bad.py:1: f cyclomatic 17" in out and "complexity FAIL" in out


# ---- coverage (ac-101) ----------------------------------------------------------------


def _project(tmp_path: Path, floor: int, failing_test: bool = False) -> Path:
    """A package with one covered and one uncovered function: 50 % of its statements run."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("def used():\n    return 1\n\n\ndef unused():\n    return 2\n")
    (tmp_path / "tests").mkdir()
    check = "assert used() == 2" if failing_test else "assert used() == 1"
    (tmp_path / "tests" / "test_pkg.py").write_text(f"from pkg import used\n\n\ndef test_used():\n    {check}\n")
    (tmp_path / ".coveragerc").write_text(f"[run]\nsource = pkg\nbranch = True\n\n[report]\nfail_under = {floor}\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    return tmp_path


def _coverage(root: Path, monkeypatch) -> int:
    for name in list(os.environ):
        if name.startswith(("COV_CORE_", "COVERAGE_")):
            monkeypatch.delenv(name)
    return _script("check_coverage").run(root, ["-q"])


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-101
def test_coverage_below_the_floor_fails(tmp_path, monkeypatch, capfd):
    assert _coverage(_project(tmp_path, floor=90), monkeypatch) != 0
    assert "coverage FAIL" in capfd.readouterr().out


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-101
def test_coverage_at_or_above_the_floor_passes(tmp_path, monkeypatch, capfd):
    assert _coverage(_project(tmp_path, floor=50), monkeypatch) == 0
    assert "coverage ok" in capfd.readouterr().out


def test_failing_suite_fails_whatever_the_coverage(tmp_path, monkeypatch):
    assert _coverage(_project(tmp_path, floor=0, failing_test=True), monkeypatch) != 0


def test_missing_config_fails(tmp_path):
    assert _script("check_coverage").run(tmp_path, []) != 0


def test_committed_coverage_floor_is_90_over_genesis_wp_with_branches():
    config = configparser.ConfigParser()
    config.read(ROOT / ".coveragerc")
    assert config["run"]["source"] == "genesis_wp"
    assert config["run"].getboolean("branch")
    assert config["report"].getfloat("fail_under") == 90


# ---- gate declaration (ac-104) --------------------------------------------------------


def _transition_gates() -> dict:
    """The rungs of openbrain-client.yaml transition_gates (one JSON flow mapping per line)."""
    text = (ROOT / "openbrain-client.yaml").read_text()
    section = text.split("\ntransition_gates:\n", 1)[1]
    gates = {}
    for line in section.splitlines():
        match = re.fullmatch(r"  ([a-z]+): (\{.*\})", line)
        if not match:
            break
        gates[match[1]] = json.loads(match[2])
    return gates


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-104
def test_every_rung_is_declared_and_cumulative():
    gates = _transition_gates()
    assert tuple(gates) == RUNGS
    for lower, higher in zip(RUNGS, RUNGS[1:]):
        for kind in ("in_session", "runner_owned"):
            assert all(stage in gates[higher][kind] for stage in gates[lower][kind]), (lower, higher, kind)


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-104
def test_checks_are_runner_owned_and_never_in_session():
    gates = _transition_gates()
    for rung in RUNGS:
        owned, in_session = gates[rung]["runner_owned"], gates[rung]["in_session"]
        assert (COMPLEXITY in owned) == (RUNGS.index(rung) >= RUNGS.index("static")), rung
        assert (COVERAGE in owned) == (RUNGS.index(rung) >= RUNGS.index("integration")), rung
        assert not any("check_" in " ".join(stage) or "--cov" in stage for stage in in_session), rung


# DHF-REQ: genesis-wp/requirement-26  DHF-TEST: genesis-wp/ac-104
def test_plain_pytest_does_not_measure_coverage():
    config = configparser.ConfigParser()
    config.read(ROOT / "pytest.ini")
    assert "--cov" not in config["pytest"]["addopts"]
    setup_cfg = configparser.ConfigParser()
    setup_cfg.read(ROOT / "setup.cfg")
    assert "tool:pytest" not in setup_cfg.sections()  # a second, dead pytest config
