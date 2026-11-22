#!/usr/bin/env python3
"""Prune old GitHub releases to the requirement-50 keep-set; never delete a git tag.

The policy is openbrain requirement-1163, verbatim in genesis-wp requirement-50: three named
bounds, maxMinorLines, maxDeepLines and maxPatches (defaults 5, 2, 5), under the invariant
maxDeepLines <= maxMinorLines, enforced when a policy is constructed.

- Keep the terminal (highest-numbered) release of each of the maxMinorLines most recent
  major.minor lines.
- Keep the maxPatches most recent patch releases of each of the maxDeepLines most recent lines.
- Delete every other GitHub release.

Only versions with a GitHub release are eligible, because the list comes from the releases API.
Pruning deletes the release entry only. The GitHub boundary has no tag-deletion operation and
never passes ``--cleanup-tag``. genesis-wp publishes no other per-version artifact, so a tag
without a release has nothing to prune.

Usage::

    python scripts/release_prune.py --dry-run
    python scripts/release_prune.py [--repo david-aggeler/genesis-wp-ha] [--max-minor-lines N] ...

DHF-REQ: genesis-wp/requirement-50
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

ALLOWED_REPOS = ("david-aggeler/genesis-wp", "david-aggeler/genesis-wp-ha")
DEFAULT_REPO = ALLOWED_REPOS[0]
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class RetentionPolicy:
    """The three bounds; a policy that breaks maxDeepLines <= maxMinorLines is rejected."""

    max_minor_lines: int = 5
    max_deep_lines: int = 2
    max_patches: int = 5

    def __post_init__(self) -> None:
        for name in ("max_minor_lines", "max_deep_lines", "max_patches"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be an integer of at least 1, got {value!r}")
        if self.max_deep_lines > self.max_minor_lines:
            raise ValueError(f"maxDeepLines ({self.max_deep_lines}) must not exceed maxMinorLines "
                             f"({self.max_minor_lines})")


@dataclass(frozen=True)
class Plan:
    """Keep and prune sets, newest first; tags that are not vMAJOR.MINOR.PATCH are ignored."""

    keep: list[str] = field(default_factory=list)
    prune: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


def _parse(tag: str) -> tuple[int, int, int] | None:
    match = _TAG.match(tag)
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def plan(releases: Iterable[str], policy: RetentionPolicy) -> Plan:
    """The keep-set over ``releases`` (tags that carry a GitHub release)."""
    parsed = {}
    ignored = []
    for tag in releases:
        version = _parse(tag)
        if version is None:
            ignored.append(tag)
        else:
            parsed[tag] = version
    lines: dict[tuple[int, int], list[str]] = {}
    for tag in sorted(parsed, key=parsed.get, reverse=True):
        lines.setdefault(parsed[tag][:2], []).append(tag)
    recent = list(lines.values())[: policy.max_minor_lines]  # dicts keep insertion order: newest first
    keep = set()
    for index, tags in enumerate(recent):
        keep.add(tags[0])  # terminal rule
        if index < policy.max_deep_lines:
            keep.update(tags[: policy.max_patches])  # depth rule
    ordered = sorted(parsed, key=parsed.get, reverse=True)
    return Plan(keep=[t for t in ordered if t in keep], prune=[t for t in ordered if t not in keep],
                ignored=sorted(ignored))


Runner = Callable[..., subprocess.CompletedProcess]


class GitHubReleases:
    """The GitHub boundary: list releases and delete a release entry. Nothing else."""

    def __init__(self, repo: str, runner: Runner = subprocess.run) -> None:
        self._repo = repo
        self._run = runner

    def list_releases(self) -> list[str]:
        out = self._run(["gh", "api", "--paginate", f"repos/{self._repo}/releases", "--jq", ".[].tag_name"],
                        check=True, capture_output=True, text=True)
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]

    def delete_release(self, tag: str) -> None:
        # Deletes the release entry and keeps the git tag: no --cleanup-tag, ever.
        self._run(["gh", "release", "delete", tag, "--yes", "--repo", self._repo],
                  check=True, capture_output=True, text=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prune old GitHub releases; git tags are never deleted.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"one of {', '.join(ALLOWED_REPOS)}")
    parser.add_argument("--dry-run", action="store_true", help="print the keep and prune sets, delete nothing")
    parser.add_argument("--max-minor-lines", type=int, default=RetentionPolicy.max_minor_lines)
    parser.add_argument("--max-deep-lines", type=int, default=RetentionPolicy.max_deep_lines)
    parser.add_argument("--max-patches", type=int, default=RetentionPolicy.max_patches)
    return parser


def main(argv: list[str] | None = None,
         releases: Callable[[str], GitHubReleases] = GitHubReleases) -> int:
    args = _parser().parse_args(argv)
    if args.repo not in ALLOWED_REPOS:
        print(f"error: --repo must be one of {', '.join(ALLOWED_REPOS)}, got {args.repo!r}", file=sys.stderr)
        return 2
    try:
        policy = RetentionPolicy(args.max_minor_lines, args.max_deep_lines, args.max_patches)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    boundary = releases(args.repo)
    try:
        result = plan(boundary.list_releases(), policy)
        print(f"repo: {args.repo}")
        print("keep: " + " ".join(result.keep))
        print("prune: " + " ".join(result.prune))
        if result.ignored:
            print("ignored (not vMAJOR.MINOR.PATCH): " + " ".join(result.ignored))
        if not args.dry_run:
            for tag in result.prune:
                boundary.delete_release(tag)
                print(f"deleted release {tag} (tag kept)")
    except subprocess.CalledProcessError as exc:
        print(f"error: gh failed: {exc.stderr or exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
