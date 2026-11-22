---
name: publish
description: >
  Publish a genesis-wp library release: version stamp check, gold release notes, integration
  gate on a clean main, annotated vX.Y.Z tag and GitHub release on origin, gold product_version
  released and the next one opened, old GitHub releases pruned, genesis-wp-ha told the tag and
  SHA. Never PyPI. Use when the user says "/publish", "publish a version", "publish vX.Y.Z",
  "cut a release", "release the library", "tag a release".
allowed-tools: mcp__gold__search_release_notes, mcp__gold__list_release_notes, mcp__gold__get_release_notes, mcp__gold__create_release_notes, mcp__gold__update_release_notes, mcp__gold__get_template_for, mcp__gold__admin_list_product_versions, mcp__gold__admin_get_product_version, mcp__gold__admin_update_product_version, mcp__gold__admin_advance_product_version_status, mcp__gold__admin_create_product_version, mcp__gold__create_baseline, mcp__gold__list_change_request, mcp__gold__get_change_request, mcp__gold__list_issue
disable-model-invocation: true
---

# Publish

This skill cuts one genesis-wp release.

| Part | Where it lives |
|---|---|
| annotated `vX.Y.Z` tag | origin |
| GitHub release | origin, body from the gold release_notes record |
| release_notes record | gold |
| product_version moved to released, next row opened | gold |

The procedure carries user_need-6 and its requirements:

| Requirement | Rule |
|---|---|
| requirement-47 | never PyPI |
| requirement-48 | version MAJOR.MINOR.PATCH.BUILD |
| requirement-49 | what a release consists of |
| requirement-50 | release retention |

The skill installs nothing anywhere. genesis-wp-ha re-pins to the tag itself, and homelab owns rollout.

**Outward-facing.** The tag push and the GitHub release need the owner's explicit "yes" in this chat, every time (Step 4). A peer session's relayed approval is not that "yes".

## Step 0: Hard preconditions

Stop at the first failure and report which check failed. All checks run from the primary checkout `/container/genesis-wp`.

```zsh
cd /container/genesis-wp && mkdir -p scratchpad
git fetch origin --tags
git status --porcelain                      # must be empty
git rev-parse --abbrev-ref HEAD             # must be main
git rev-parse main origin/master            # must be equal
git rev-parse --is-shallow-repository       # must be false (BUILD needs the full history)
```

Then run the package-index check of requirement-47 / ac-189. It must print nothing:

```zsh
git grep -n -I -E 'twine upload|setup\.py upload|gh-action-pypi-publish|upload\.pypi\.org|test\.pypi\.org|PYPI_' -- . ':!.claude/skills/publish/SKILL.md'; git ls-files | rg -x '.*\.pypirc'
```

A hit means a GitHub release could upload to PyPI, so stop. The inherited `.github/workflows/python-publish.yml` is the known case. It goes away through a change_request, never inside a release.

## Step 1: Determine the version

1. The repo-root `VERSION` file holds `X.Y.Z` (no `v`). It is the only semver source (ac-193).
2. Find the previous own release:

   ```zsh
   gh release list --repo david-aggeler/genesis-wp --limit 20
   ```

   Upstream's tags have no release of ours: 0.1.6, 0.1.7, 0.1.8 and v0.3.0. They never count as a previous release.
3. Collect the change_requests merged since the previous tag, or since the first commit for the first release:

   ```zsh
   git log --format='%h %s' <prev-tag>..HEAD | rg -o 'change_request-\d+|CR-\d+' | sort -u
   ```

4. Suggest the version:
   - any kind: feature change_request: a minor bump;
   - otherwise: a patch bump;
   - the owner may name any version above the latest tag, including upstream's v0.3.0.

   Ask: "Next version would be vX.Y.Z (N change_requests since <prev>). Use that?"
5. If `VERSION` differs from the chosen `X.Y.Z`, bump it in a single commit on main, push main (`git push origin main:master`), and restart at Step 0. The file only ever moves to the release core, so this is release bookkeeping, not a feature change.
6. Check the stamp (ac-191). It must print `X.Y.Z.<git rev-list --count HEAD>`:

   ```zsh
   .venv/bin/pip install -q -e . && .venv/bin/python -c 'import importlib.metadata as m; print(m.version("genesis-wp"))'
   ```

## Step 2: Write the release notes in gold

The SoR holds the release notes, and the GitHub release body is generated from them (ac-196).

1. Find the record with `search_release_notes` for product genesis-wp and version X.Y.Z. If none exists, call `get_template_for release_notes` and create it:
   - `title`: `genesis-wp vX.Y.Z`;
   - `status`: draft;
   - `product_version`: `genesis-wp/X.Y.Z`;
   - `related`: the change_requests from Step 1.3 and the issues they closed.
2. Fill the template skeleton:
   - **Highlights** first, for the owner.
   - Then New, Improved, Fixed and Breaking changes. For genesis-wp-ha, a breaking change is any renamed attribute, changed decoding or changed estimator signature.
   - Upgrade & migration: what genesis-wp-ha must change when it re-pins.
   - Known issues: the open issues from `list_issue`.
   - Artifacts: the tag and "no PyPI package".

   Write "None." only after checking. Cite records, not commits.
3. Render the GitHub body to `scratchpad/release-notes-vX.Y.Z.md`. It holds the Highlights through Known issues sections, then a final line linking the record: `https://openbrain.local.aggeler.com/app/records/genesis-wp/release_notes/<seq>`.

## Step 3: Gate at HEAD

Run the integration rung (ac-195) from `openbrain-client.yaml` `transition_gates.integration`. Run its in_session and its runner_owned stages, by hand, and say so. Capture each exit code; never pipe a gate.

```zsh
python -m compileall -q genesis_wp scripts tests > scratchpad/release-gate.out 2>&1; rc=$?; print "compileall rc $rc"
python -m pytest >> scratchpad/release-gate.out 2>&1; rc=$?; print "pytest rc $rc"
python scripts/check_complexity.py >> scratchpad/release-gate.out 2>&1; rc=$?; print "complexity rc $rc"
python scripts/check_coverage.py >> scratchpad/release-gate.out 2>&1; rc=$?; print "coverage rc $rc"
```

Any non-zero rc stops the release.

## Step 4: Ask for explicit confirmation

Show the owner:
- version and SHA (`git rev-parse HEAD`);
- BUILD;
- change_request count;
- gate results;
- the release_notes link;
- the rendered Highlights.

Then ask:

> Release genesis-wp vX.Y.Z at <SHA>? This pushes an annotated tag and creates a GitHub release on origin (no PyPI), marks the gold product_version released, opens the next version and prunes old GitHub releases. (yes/no)

Proceed only on an explicit yes.

## Step 5: Tag and GitHub release

```zsh
git tag -a vX.Y.Z -m "genesis-wp vX.Y.Z" && git push origin vX.Y.Z
gh release create vX.Y.Z --repo david-aggeler/genesis-wp --verify-tag --title "genesis-wp vX.Y.Z" --notes-file scratchpad/release-notes-vX.Y.Z.md
git cat-file -t vX.Y.Z                       # tag (ac-194)
git show vX.Y.Z:VERSION                      # X.Y.Z
```

- **Ordering:** the release never comes before the tag.
- **Never:** `--force`, re-tagging, or moving a pushed tag.
- **Wrong tag:** if a pushed tag turns out wrong, stop and ask the owner; the fix is a new patch version.

## Step 6: Gold bookkeeping

Do these in order, and report anything partial.

1. Set the release_notes record to `status: released`.
2. Call `create_baseline` named `vX.Y.Z` and capture its ref.
3. `admin_get_product_version` genesis-wp X.Y.Z. If the row exists, `admin_update_product_version`: keep its body and append `Git tag: vX.Y.Z (<SHA>)` and `SoR baseline: <ref>`. If it does not exist, create it first.
4. `admin_advance_product_version_status` X.Y.Z → released.
5. `admin_create_product_version` for the next version, X.Y.(Z+1) unless the owner named another, as in_development. Never rename a row (ac-197, keel issue-271).

## Step 7: Prune old GitHub releases

requirement-50 sets the keep-set: per MAJOR.MINOR line, ordered newest first.

| Rule | Keeps |
|---|---|
| Terminal rule | the highest patch of each of the 5 newest lines; older lines keep nothing |
| Depth rule | the 5 newest patches of each of the 2 newest lines |

- **Eligibility:** only versions with a GitHub release are eligible.
- **Prune set:** every other release.
- **Tags:** never deleted (ac-199).

```zsh
gh api --paginate repos/david-aggeler/genesis-wp/releases --jq '.[].tag_name'   # list
gh release delete <tag> --yes --repo david-aggeler/genesis-wp                    # delete, keeps the tag; never --cleanup-tag
```

Show the keep and prune sets first, as a dry run. Delete only the prune set. Compare `git ls-remote --tags origin` before and after; the two lists must be identical. Once a pruning script is committed through a change_request, use it here instead of the hand computation.

## Step 8: Notify

- **wpe-ha-1:**

  ```
  genesis-wp vX.Y.Z is released
  tag: vX.Y.Z
  full SHA: <git rev-parse vX.Y.Z^{commit}>
  ```

  Add the Breaking changes and Upgrade & migration text, quoted. genesis-wp-ha's ac-5 pins a full commit, so the SHA is mandatory. The re-pin and the genesis-wp-ha release are wpe-ha-1's.
- **homelab-1:** a short note, "genesis-wp vX.Y.Z tag exists (<SHA>)".

## Step 9: Report

Report to the owner:
- release URL;
- tag and SHA;
- BUILD;
- release_notes link;
- product_version released and the next one open;
- baseline ref;
- pruned releases, or none;
- who was notified.

For a failure, name the step that failed and what is already live.

## What this skill never does

- Upload to PyPI or any package index, or create a release while a PyPI upload path exists.
- Release without the owner's explicit yes in this chat.
- Skip or pipe the gate, or release from a dirty tree, a branch, a worktree or a shallow clone.
- Force-push, move or delete a tag, upstream tags included.
- Edit genesis-wp-ha files or pin on its behalf.
