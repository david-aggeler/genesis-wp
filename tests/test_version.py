"""One accessor reports MAJOR.MINOR.PATCH.BUILD for every version surface (requirement-48).

DHF-REQ: genesis-wp/requirement-48
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
CORE = re.compile(r"^\d+\.\d+\.\d+$")


def _core() -> str:
    core = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert CORE.match(core), core
    return core


def _count() -> int:
    out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True)
    return int(out.stdout.strip())


@pytest.fixture(scope="module")
def dists(tmp_path_factory) -> dict[str, Path]:
    """Build a wheel and an sdist from this checkout, offline."""
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "--no-build-isolation", "-q", "-w", str(out)],
        check=True,
    )
    subprocess.run([sys.executable, "setup.py", "-q", "sdist", "-d", str(out)], cwd=ROOT, check=True,
                   capture_output=True)
    return {"wheel": next(out.glob("*.whl")), "sdist": next(out.glob("*.tar.gz"))}


def _no_git_env(tmp_path: Path) -> dict[str, str]:
    """An environment whose PATH has the interpreter's directory but no git."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    os.symlink(sys.executable, bin_dir / "python")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTHON"))}
    env["PATH"] = str(bin_dir)
    return env


def _install(dist: Path, target: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "--no-index", "-q",
         "--target", str(target), str(dist)],
        check=True, capture_output=True,
    )


PROBE = """
import importlib.metadata, json, subprocess, sys
import genesis_wp
from genesis_wp.emulator import cli, climate
out = {"metadata": importlib.metadata.version("genesis-wp"), "dunder": genesis_wp.__version__}
for name, main in (("emulator", cli.main), ("climate", climate.main)):
    try:
        main(["--version"])
    except SystemExit:
        pass
print(json.dumps(out))
"""


def _surfaces(target: Path, env: dict[str, str], cwd: Path) -> tuple[dict, list[str]]:
    env = dict(env, PYTHONPATH=os.pathsep.join([str(target), *[p for p in sys.path if "site-packages" in p]]))
    run = subprocess.run([str(Path(env["PATH"]) / "python"), "-c", PROBE], cwd=cwd, env=env,
                         check=True, capture_output=True, text=True)
    lines = run.stdout.strip().splitlines()
    return json.loads(lines[-1]), lines[:-1]


# DHF-TEST: genesis-wp/ac-191
def test_checkout_build_reports_version_plus_commit_count(dists):
    """ac-191: built from a full clone at commit C, the metadata version is VERSION + "." + rev-list count."""
    expected = f"{_core()}.{_count()}"
    assert dists["wheel"].name.startswith(f"genesis_wp-{expected}-")
    assert dists["sdist"].name == f"genesis_wp-{expected}.tar.gz"


# DHF-TEST: genesis-wp/ac-192, genesis-wp/ac-200
@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_installed_distribution_reports_the_stamp_without_git(dists, kind, tmp_path):
    """ac-192 and ac-200: without git, every version surface reports the version stamped at build."""
    if kind == "sdist":
        # Rebuild a wheel from the unpacked sdist, where no .git exists.
        unpacked = tmp_path / "src"
        shutil.unpack_archive(dists["sdist"], unpacked)
        (src,) = unpacked.iterdir()
        assert not (src / ".git").exists()
        wheel_dir = tmp_path / "wheel"
        subprocess.run([sys.executable, "-m", "pip", "wheel", str(src), "--no-deps", "--no-build-isolation", "-q",
                        "-w", str(wheel_dir)], check=True, capture_output=True, env=_no_git_env(tmp_path / "b"))
        dist = next(wheel_dir.glob("*.whl"))
    else:
        dist = dists["wheel"]
    target = tmp_path / "site"
    _install(dist, target)
    env = _no_git_env(tmp_path / "r")
    assert shutil.which("git", path=env["PATH"]) is None
    expected = f"{_core()}.{_count()}"
    found, printed = _surfaces(target, env, cwd=tmp_path)
    assert found == {"metadata": expected, "dunder": expected}
    assert printed == [expected, expected]  # genesis-wp-emulator --version, genesis-wp-climate --version
    assert "dev" not in expected and "+" not in expected


# DHF-TEST: genesis-wp/ac-193
def test_version_file_is_the_only_semver_literal():
    """ac-193: setup.py and the genesis_wp package carry no version literal of their own."""
    literal = re.compile(r"""(?<![\w.])\d+\.\d+\.\d+(?:\.dev\d+|\.\d+)?(?![\w.])""")
    assignment = re.compile(r"""\bversion\s*=\s*["']""")
    offenders = []
    files = [ROOT / "setup.py", *sorted((ROOT / "genesis_wp").rglob("*.py"))]
    for path in files:
        if path.name == "_build_version.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if assignment.search(line) or (_core() in literal.findall(line)):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == []


def test_accessor_rejects_a_malformed_version_file(tmp_path):
    """A VERSION file that is not MAJOR.MINOR.PATCH fails closed instead of reporting a sentinel."""
    from genesis_wp import _version

    for bad in ("0.4", "v0.4.0", "0.4.0.dev0", "0.4.0-rc1", ""):
        (tmp_path / "VERSION").write_text(bad + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="VERSION"):
            _version.read_core(tmp_path)


def test_checkout_accessor_matches_git():
    """In this checkout, the accessor's derivation is VERSION + the commit count."""
    from genesis_wp import _version

    assert _version.derive(ROOT) == f"{_core()}.{_count()}"
