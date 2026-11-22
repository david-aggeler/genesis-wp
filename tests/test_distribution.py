"""genesis-wp ships through git tags and GitHub releases only, never a package index.

DHF-REQ: genesis-wp/requirement-47
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT =Path(__file__).resolve().parent.parent

# Assembled at run time so this file never matches its own scan.
_UPLOAD_MARKERS = (
    "twine" + " upload",
    "setup.py" + " upload",
    "gh-action-" + "pypi-publish",
    "upload." + "pypi.org",
    "test." + "pypi.org",
    "PYPI" + "_",
)
# The publish skill names the markers in its precondition check.
_SCAN_EXEMPT = {"tests/test_distribution.py", ".claude/skills/publish/SKILL.md"}
_PIP_INSTALL = re.compile(r"pip3?\s+install\s+(?P<args>.+)")
_GIT_SOURCE = "git+https://github.com/david-aggeler/genesis-wp@"


def _tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    return [name for name in result.stdout.decode().split("\0") if name]


def _text(name: str) -> str | None:
    try:
        return (ROOT / name).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def test_no_tracked_file_uploads_to_a_package_index():
    """ac-189: no workflow, script or config uploads genesis-wp to PyPI or another index."""
    hits = []
    for name in _tracked_files():
        if Path(name).name == ".pypirc":
            hits.append(f"{name}: .pypirc file")
        if name in _SCAN_EXEMPT:
            continue
        text = _text(name)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            hits.extend(f"{name}:{number}: {marker}" for marker in _UPLOAD_MARKERS if marker in line)
    assert hits == []


def _installs_genesis_wp(args: str) -> bool:
    return "genesis-wp" in args or "genesis_wp" in args or args.lstrip().startswith(("-e", "."))


def _is_git_or_local(args: str) -> bool:
    words = [word.strip("\"'`") for word in args.split()]
    sources = [word for word in words if not word.startswith("-") or word == "-e"]
    for word in sources:
        if word == "-e":
            continue
        if word.startswith(_GIT_SOURCE) or word.startswith((".", "/")):
            return True
    return False


def test_install_instructions_name_a_git_or_local_source():
    """ac-190: every documented genesis-wp install uses a local path or a git URL, never a bare name."""
    offenders = []
    for name in _tracked_files():
        if not name.endswith(".md"):
            continue
        text = _text(name) or ""
        for number, line in enumerate(text.splitlines(), 1):
            match = _PIP_INSTALL.search(line)
            if match and _installs_genesis_wp(match["args"]) and not _is_git_or_local(match["args"]):
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert offenders == []


def test_install_check_rejects_a_bare_package_name():
    """ac-190: the check itself flags an install that pip would resolve from an index."""
    assert _installs_genesis_wp("genesis-wp")
    assert not _is_git_or_local("genesis-wp")
    assert not _is_git_or_local("genesis-wp[emulator]==0.4.0")
    assert _is_git_or_local(f"{_GIT_SOURCE}v0.4.0")
    assert _is_git_or_local('-e ".[emulator]"')
