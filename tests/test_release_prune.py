"""Release pruning keeps the requirement-50 keep-set and never deletes a git tag.

The GitHub boundary is replaced by a fake; no test talks to GitHub.

DHF-REQ: genesis-wp/requirement-50
"""

from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parent.parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prune = _script("release_prune")


class FakeReleases:
    """The GitHub boundary with recorded calls."""

    def __init__(self, tags):
        self.tags = list(tags)
        self.listed = 0
        self.deleted: list[str] = []

    def list_releases(self):
        self.listed += 1
        return list(self.tags)

    def delete_release(self, tag):
        self.deleted.append(tag)


def _line(minor: str, patches: range) -> list[str]:
    return [f"v{minor}.{p}" for p in patches]


# DHF-TEST: genesis-wp/ac-198
def test_openbrain_worked_example_keeps_terminal_and_deep_patches():
    """openbrain requirement-1163's diagram at maxMinorLines 3, maxDeepLines 2, maxPatches 2."""
    releases = _line("1.8", range(3)) + _line("1.9", range(4)) + _line("2.0", range(5)) + _line("2.1", range(5))
    plan = prune.plan(releases, prune.RetentionPolicy(max_minor_lines=3, max_deep_lines=2, max_patches=2))
    assert plan.keep == ["v2.1.4", "v2.1.3", "v2.0.4", "v2.0.3", "v1.9.3"]
    assert set(plan.prune) == set(releases) - set(plan.keep)


# DHF-TEST: genesis-wp/ac-198
def test_defaults_keep_at_most_thirteen_releases():
    """Defaults 5/2/5 over seven lines of seven patches: 5 + 5 deep patches and 3 terminal releases."""
    releases = [f"v0.{minor}.{patch}" for minor in range(7) for patch in range(7)]
    plan = prune.plan(releases, prune.RetentionPolicy())
    assert plan.keep == (
        [f"v0.6.{p}" for p in (6, 5, 4, 3, 2)]
        + [f"v0.5.{p}" for p in (6, 5, 4, 3, 2)]
        + ["v0.4.6", "v0.3.6", "v0.2.6"]
    )
    assert len(plan.prune) == len(releases) - 13
    assert "v0.1.6" in plan.prune and "v0.0.6" in plan.prune  # lines outside maxMinorLines keep nothing


# DHF-TEST: genesis-wp/ac-198
def test_ordering_is_numeric_not_lexical_and_unparsable_tags_are_ignored():
    releases = ["v0.4.9", "v0.4.10", "v0.10.0", "0.1.6", "v0.3.0-rc1", "nightly"]
    plan = prune.plan(releases, prune.RetentionPolicy(max_minor_lines=1, max_deep_lines=1, max_patches=1))
    assert plan.keep == ["v0.10.0"]
    assert plan.prune == ["v0.4.10", "v0.4.9"]
    assert plan.ignored == ["0.1.6", "nightly", "v0.3.0-rc1"]


# DHF-TEST: genesis-wp/ac-201
@pytest.mark.parametrize("bounds", [
    {"max_minor_lines": 1, "max_deep_lines": 2},
    {"max_minor_lines": 0},
    {"max_patches": 0},
    {"max_deep_lines": 0},
    {"max_patches": 1.5},
    {"max_minor_lines": True},
])
def test_invalid_policy_is_rejected_not_clamped(bounds):
    with pytest.raises(ValueError):
        prune.RetentionPolicy(**bounds)


# DHF-TEST: genesis-wp/ac-201
def test_invariant_violation_stops_before_any_release_is_listed(capsys):
    fake = FakeReleases(["v0.4.0"])
    code = prune.main(["--max-minor-lines", "1", "--max-deep-lines", "2"], releases=lambda repo: fake)
    assert code == 2
    assert fake.listed == 0 and fake.deleted == []
    assert "maxDeepLines" in capsys.readouterr().err


# DHF-TEST: genesis-wp/ac-199
def test_boundary_has_no_tag_deletion_and_never_cleans_up_tags():
    """The GitHub boundary offers exactly listing and release deletion; delete argv keeps the tag."""
    public = {name for name, _ in inspect.getmembers(prune.GitHubReleases, inspect.isfunction)
              if not name.startswith("_")}
    assert public == {"list_releases", "delete_release"}
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="v0.4.1\nv0.4.0\n", stderr="")

    boundary = prune.GitHubReleases("david-aggeler/genesis-wp-ha", runner=runner)
    assert boundary.list_releases() == ["v0.4.1", "v0.4.0"]
    boundary.delete_release("v0.4.0")
    assert calls[0][:3] == ["gh", "api", "--paginate"]
    assert "repos/david-aggeler/genesis-wp-ha/releases" in calls[0]
    assert calls[1] == ["gh", "release", "delete", "v0.4.0", "--yes", "--repo", "david-aggeler/genesis-wp-ha"]
    assert not any("--cleanup-tag" in argv or "tag" == argv[1] for argv in calls)


# DHF-TEST: genesis-wp/ac-199
def test_run_deletes_only_the_prune_set():
    releases = _line("0.4", range(8))
    fake = FakeReleases(releases)
    assert prune.main([], releases=lambda repo: fake) == 0
    assert fake.deleted == ["v0.4.2", "v0.4.1", "v0.4.0"]


def test_dry_run_deletes_nothing_and_prints_both_sets(capsys):
    fake = FakeReleases(_line("0.4", range(8)))
    assert prune.main(["--dry-run"], releases=lambda repo: fake) == 0
    assert fake.deleted == []
    out = capsys.readouterr().out
    assert "keep: v0.4.7 v0.4.6 v0.4.5 v0.4.4 v0.4.3" in out
    assert "prune: v0.4.2 v0.4.1 v0.4.0" in out


def test_unknown_repo_is_rejected_before_any_call(capsys):
    fake = FakeReleases(["v0.4.0"])
    assert prune.main(["--repo", "ulrichwi/pythermiagenesis17"], releases=lambda repo: fake) == 2
    assert fake.listed == 0
    assert "repo" in capsys.readouterr().err


def test_repo_flag_reaches_the_boundary():
    seen = []
    fake = FakeReleases([])

    def factory(repo):
        seen.append(repo)
        return fake

    assert prune.main(["--repo", "david-aggeler/genesis-wp-ha", "--dry-run"], releases=factory) == 0
    assert seen == ["david-aggeler/genesis-wp-ha"]


def test_gh_failure_stops_with_a_non_zero_exit(capsys):
    def runner(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="HTTP 401")

    code = prune.main([], releases=lambda repo: prune.GitHubReleases(repo, runner=runner))
    assert code == 1
    assert "HTTP 401" in capsys.readouterr().err
