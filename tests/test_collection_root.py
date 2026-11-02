"""Bare pytest collection stays within the checkout's own tests tree."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


# DHF-TEST: genesis-wp/requirement-43
# DHF-TEST: genesis-wp/ac-168
def test_bare_collection_ignores_nested_worktree(tmp_path):
    for checkout in (tmp_path, tmp_path / "worktrees" / "cr-x"):
        tests = checkout / "tests"
        tests.mkdir(parents=True)
        shutil.copy2(ROOT / "pytest.ini", checkout / "pytest.ini")
        # Keep the real conftest and its relative-import dependencies intact.
        for name in ("__init__.py", "conftest.py", "harness.py", "plant.py", "targets.py"):
            shutil.copy2(ROOT / "tests" / name, tests / name)
        (tests / "test_sample.py").write_text("def test_sample():\n    pass\n")

    env = os.environ.copy()
    env["GENESIS_WP_TEST_TARGET"] = "emulator"
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    collected = [line for line in result.stdout.splitlines() if "::" in line]
    assert collected == ["tests/test_sample.py::test_sample"], output
