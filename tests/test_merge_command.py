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
    """The caller's environment minus every inherited ``GIT_*`` variable, plus GIT_ENV.

    An inherited GIT_DIR, GIT_WORK_TREE or GIT_INDEX_FILE would point the fixture's git
    calls at a foreign repository instead of ``tmp_path``.
    """
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return {**inherited, **GIT_ENV}


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
    return _make_repo(tmp_path)


def _make_repo(tmp_path: Path) -> Path:
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


def _assert_refused(
    repo: Path, done: subprocess.CompletedProcess, main_before: str, reason: str, merge_head: bool = False
) -> None:
    assert done.returncode != 0, done.stdout
    assert reason in done.stderr, done.stderr
    assert "code_change_ref=" not in done.stdout
    assert _git(repo, "rev-parse", "refs/heads/main") == main_before
    assert (repo / ".git" / "MERGE_HEAD").exists() == merge_head


def _assert_merged(repo: Path, done: subprocess.CompletedProcess, main_before: str, tip: str) -> None:
    assert done.returncode == 0, done.stderr
    assert _git(repo, "rev-list", "--parents", "-n", "1", "refs/heads/main").split()[1:] == [main_before, tip]
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/main") == "Merge cr-7 (change_request-7)"


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
    _assert_refused(repo, _run_merge(repo, "cr-7"), main_before, "on branch elsewhere, want main")
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "elsewhere"


def test_refuses_a_detached_head(repo):
    _git(repo, "switch", "-q", "--detach", "main")
    _assert_refused(repo, _run_merge(repo, "cr-7"), _git(repo, "rev-parse", "main"), "HEAD is detached")


@pytest.mark.parametrize("staged", [False, True])
def test_refuses_a_dirty_tracked_tree(repo, staged):
    (repo / "base").write_text("changed\n")
    if staged:
        _git(repo, "add", "base")
    _assert_refused(repo, _run_merge(repo, "cr-7"), _git(repo, "rev-parse", "main"), "tracked changes")


@pytest.mark.parametrize(
    "ref, reason",
    [
        ("cr-8", "no branch cr-8"),
        ("cr-07", "cannot resolve cr-07"),
        ("0" * 40, "cannot resolve " + "0" * 40),
        ("not-a-ref", "cannot resolve not-a-ref"),
        ("", "cannot resolve"),
    ],
)
def test_refuses_an_unresolvable_argument(repo, ref, reason):
    _assert_refused(repo, _run_merge(repo, ref), _git(repo, "rev-parse", "main"), reason)


def test_refuses_a_sha_no_unit_branch_points_at(repo):
    sha = _git(repo, "rev-parse", "cr-7")
    _git(repo, "branch", "-q", "-m", "cr-7", "topic")
    _assert_refused(repo, _run_merge(repo, sha), _git(repo, "rev-parse", "main"), "0 unit branches point at")


def test_refuses_a_sha_several_unit_branches_point_at(repo):
    sha = _git(repo, "rev-parse", "cr-7")
    _git(repo, "branch", "-q", "cr-9", "cr-7")
    _assert_refused(repo, _run_merge(repo, sha), _git(repo, "rev-parse", "main"), "2 unit branches point at")


@pytest.mark.parametrize("by", ["sha", "branch"])
def test_refuses_a_unit_with_nothing_ahead_of_main(repo, by):
    _git(repo, "branch", "-q", "-f", "cr-7", "main")
    ref = _git(repo, "rev-parse", "cr-7") if by == "sha" else "cr-7"
    _assert_refused(repo, _run_merge(repo, ref), _git(repo, "rev-parse", "main"), "cr-7 has nothing ahead of main")


def test_refuses_extra_arguments(repo):
    _assert_refused(repo, _run_merge(repo, "cr-7", "cr-7"), _git(repo, "rev-parse", "main"), "usage:")


def test_refuses_no_argument(repo):
    done = subprocess.run(_merge_command(), cwd=repo, env=_env(), capture_output=True, text=True)
    _assert_refused(repo, done, _git(repo, "rev-parse", "main"), "usage:")


def test_conflict_aborts_the_merge(repo):
    _git(repo, "switch", "-q", "cr-7")
    _commit(repo, "shared", "unit\n")
    _git(repo, "switch", "-q", "main")
    _commit(repo, "shared", "main\n")
    main_before = _git(repo, "rev-parse", "main")
    _assert_refused(repo, _run_merge(repo, "cr-7"), main_before, "merge of cr-7 into main failed; merge aborted")
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_untracked_overwrite_reports_no_merge_was_started(repo):
    _git(repo, "switch", "-q", "cr-7")
    _commit(repo, "clash", "unit\n")
    _git(repo, "switch", "-q", "main")
    (repo / "clash").write_text("operator\n")
    main_before = _git(repo, "rev-parse", "main")
    _assert_refused(repo, _run_merge(repo, "cr-7"), main_before, "merge of cr-7 into main failed; no merge started")
    assert (repo / "clash").read_text() == "operator\n"


# ---- operator state in the checkout is never discarded ----------------------------------


def test_refuses_and_keeps_an_operator_merge_in_progress(repo):
    _git(repo, "switch", "-q", "-c", "other")
    _commit(repo, "other")
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "-q", "--no-commit", "-s", "ours", "other")
    merge_head = (repo / ".git" / "MERGE_HEAD").read_text()
    main_before = _git(repo, "rev-parse", "main")
    _assert_refused(repo, _run_merge(repo, "cr-7"), main_before, "MERGE_HEAD exists", merge_head=True)
    assert (repo / ".git" / "MERGE_HEAD").read_text() == merge_head


@pytest.mark.parametrize("marker", ["CHERRY_PICK_HEAD", "REVERT_HEAD"])
def test_refuses_and_keeps_a_sequencer_operation_in_progress(repo, marker):
    (repo / ".git" / marker).write_text(_git(repo, "rev-parse", "cr-7") + "\n")
    _assert_refused(repo, _run_merge(repo, "cr-7"), _git(repo, "rev-parse", "main"), f"{marker} exists")
    assert (repo / ".git" / marker).exists()


def test_refuses_and_rolls_back_when_a_hook_rewrites_the_subject(repo):
    hook = repo / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text('#!/bin/sh\nprintf "rewritten\\n" > "$1"\n')
    hook.chmod(0o755)
    main_before = _git(repo, "rev-parse", "main")
    _assert_refused(repo, _run_merge(repo, "cr-7"), main_before, "subject is 'rewritten'")
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""


# ---- refs are resolved by full refname --------------------------------------------------


@pytest.mark.parametrize("by", ["sha", "branch"])
def test_a_unit_tag_does_not_hide_the_unit_branch(repo, by):
    _git(repo, "tag", "cr-7", "main")
    main_before, tip = _git(repo, "rev-parse", "refs/heads/main"), _git(repo, "rev-parse", "refs/heads/cr-7")
    _assert_merged(repo, _run_merge(repo, tip if by == "sha" else "cr-7"), main_before, tip)


def test_a_main_tag_does_not_refuse_the_merge(repo):
    main_before, tip = _git(repo, "rev-parse", "refs/heads/main"), _git(repo, "rev-parse", "refs/heads/cr-7")
    _git(repo, "tag", "main", tip)
    _assert_merged(repo, _run_merge(repo, tip), main_before, tip)


# ---- the fixture stays inside tmp_path ---------------------------------------------------


@pytest.mark.parametrize("variable", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"])
def test_an_inherited_git_variable_does_not_redirect_the_fixture(tmp_path, monkeypatch, variable):
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _git(decoy, "init", "-q", "-b", "main")
    _commit(decoy, "decoy")
    before = _git(decoy, "for-each-ref", "--format=%(refname) %(objectname)")
    target = {"GIT_DIR": decoy / ".git", "GIT_WORK_TREE": decoy, "GIT_INDEX_FILE": decoy / ".git" / "index"}
    monkeypatch.setenv(variable, str(target[variable]))
    repo = _make_repo(tmp_path)
    assert _git(repo, "rev-parse", "--show-toplevel") == str(repo)
    monkeypatch.delenv(variable)
    assert _git(decoy, "for-each-ref", "--format=%(refname) %(objectname)") == before
    assert _git(decoy, "status", "--porcelain") == ""
