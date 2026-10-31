"""The committed merge_command that openbrain-client merge runs (requirement-34).

openbrain-client merge refuses to run the command without a runner-minted admission
proof, so these tests drive the committed argv the way the client calls it: the argv
plus one trailing element, the commit SHA the client resolved from ``branch-or-ref``.
Every repository here is a throwaway one in ``tmp_path``; the real checkout is never
merged into.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parent.parent
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@localhost",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@localhost",
}


def _merge_command() -> list:
    """The top-level merge_command of openbrain-client.yaml (one JSON flow list)."""
    text = (ROOT / "openbrain-client.yaml").read_text()
    match = re.search(r"^merge_command: (\[.*\])$", text, re.MULTILINE)
    assert match, "openbrain-client.yaml declares no top-level merge_command"
    return json.loads(match[1])


def _env() -> dict:
    return {**os.environ, **GIT_ENV}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, env=_env(), check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _commit(repo: Path, name: str, content: str = "x\n") -> str:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A clean main checkout with branch cr-7 one commit ahead, and this repo's config and script."""
    repo = tmp_path / "primary"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base")
    _git(repo, "switch", "-q", "-c", "cr-7")
    _commit(repo, "feature")
    _git(repo, "switch", "-q", "main")
    shutil.copy(ROOT / "openbrain-client.yaml", repo / "openbrain-client.yaml")
    (repo / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "merge_unit.sh", repo / "scripts" / "merge_unit.sh")
    (repo / ".git" / "info" / "exclude").write_text("openbrain-client.yaml\nscripts/\n")
    return repo


def _run_merge(repo: Path, ref: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([*_merge_command(), ref, *extra], cwd=repo, env=_env(), capture_output=True, text=True)


def _assert_refused(repo: Path, done: subprocess.CompletedProcess, main_before: str) -> None:
    assert done.returncode != 0, done.stdout
    assert done.stderr.strip(), "a refusal names its reason on stderr"
    assert _git(repo, "rev-parse", "main") == main_before
    assert not (repo / ".git" / "MERGE_HEAD").exists()


# DHF-REQ: genesis-wp/requirement-34
def test_merge_command_is_a_non_empty_argv():
    command = _merge_command()
    assert command and all(isinstance(arg, str) and arg for arg in command)


# ---- the titled no-ff merge (ac-140) ---------------------------------------------------


# DHF-REQ: genesis-wp/requirement-34  DHF-TEST: genesis-wp/ac-140
@pytest.mark.parametrize("by", ["sha", "branch"])
def test_merge_lands_the_unit_as_a_titled_two_parent_commit(repo, by):
    main_before, tip = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "cr-7")
    done = _run_merge(repo, tip if by == "sha" else "cr-7")
    assert done.returncode == 0, done.stderr
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [main_before, tip]
    assert _git(repo, "log", "-1", "--format=%s", "HEAD") == "Merge cr-7 (change_request-7)"


# DHF-REQ: genesis-wp/requirement-34  DHF-TEST: genesis-wp/ac-140
def test_merge_stays_no_ff_when_main_has_advanced(repo):
    _commit(repo, "on-main")
    main_before, tip = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "cr-7")
    done = _run_merge(repo, tip)
    assert done.returncode == 0, done.stderr
    assert _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [main_before, tip]
    assert _git(repo, "log", "-1", "--format=%s", "HEAD") == "Merge cr-7 (change_request-7)"


# ---- the reported merge commit (ac-141) ------------------------------------------------


# DHF-REQ: genesis-wp/requirement-34  DHF-TEST: genesis-wp/ac-141
def test_merge_reports_the_new_main_head_as_code_change_ref(repo):
    done = _run_merge(repo, _git(repo, "rev-parse", "cr-7"))
    assert done.returncode == 0, done.stderr
    refs = re.findall(r"^code_change_ref=([0-9a-f]{40})$", done.stdout, re.MULTILINE)
    assert refs == [_git(repo, "rev-parse", "main")]


# ---- fail closed: main is untouched on every refusal ------------------------------------


def test_refuses_off_main(repo):
    _git(repo, "switch", "-q", "-c", "elsewhere")
    main_before = _git(repo, "rev-parse", "main")
    _assert_refused(repo, _run_merge(repo, "cr-7"), main_before)
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "elsewhere"


@pytest.mark.parametrize("staged", [False, True])
def test_refuses_a_dirty_tracked_tree(repo, staged):
    (repo / "base").write_text("changed\n")
    if staged:
        _git(repo, "add", "base")
    _assert_refused(repo, _run_merge(repo, "cr-7"), _git(repo, "rev-parse", "main"))


@pytest.mark.parametrize("ref", ["cr-8", "cr-07", "0" * 40, "not-a-ref", ""])
def test_refuses_an_unresolvable_argument(repo, ref):
    _assert_refused(repo, _run_merge(repo, ref), _git(repo, "rev-parse", "main"))


def test_refuses_a_sha_no_unit_branch_points_at(repo):
    sha = _git(repo, "rev-parse", "cr-7")
    _git(repo, "branch", "-q", "-m", "cr-7", "topic")
    _assert_refused(repo, _run_merge(repo, sha), _git(repo, "rev-parse", "main"))


def test_refuses_a_sha_several_unit_branches_point_at(repo):
    sha = _git(repo, "rev-parse", "cr-7")
    _git(repo, "branch", "-q", "cr-9", "cr-7")
    _assert_refused(repo, _run_merge(repo, sha), _git(repo, "rev-parse", "main"))


def test_refuses_a_branch_with_nothing_ahead_of_main(repo):
    _git(repo, "branch", "-q", "cr-8", "main")
    _assert_refused(repo, _run_merge(repo, "cr-8"), _git(repo, "rev-parse", "main"))


def test_refuses_extra_arguments(repo):
    _assert_refused(repo, _run_merge(repo, "cr-7", "cr-7"), _git(repo, "rev-parse", "main"))


def test_conflict_aborts_the_merge(repo):
    _git(repo, "switch", "-q", "cr-7")
    _commit(repo, "shared", "unit\n")
    _git(repo, "switch", "-q", "main")
    _commit(repo, "shared", "main\n")
    main_before = _git(repo, "rev-parse", "main")
    _assert_refused(repo, _run_merge(repo, "cr-7"), main_before)
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
