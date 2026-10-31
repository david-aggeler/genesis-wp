#!/usr/bin/env bash
# Land one unit branch cr-<n> on main as a no-ff merge commit titled
# "Merge cr-<n> (change_request-<n>)" and print code_change_ref=<merge sha>.
#
# openbrain-client merge runs this as merge_command (openbrain-client.yaml) and appends one
# argument: the commit SHA it resolved from branch-or-ref. A person may pass the branch
# name instead. Fails closed, leaving main untouched: not on main, a merge, cherry-pick or
# revert already in progress, dirty tracked tree, argument that resolves to no single
# cr-<n> branch, nothing ahead of main, merge failure, subject rewritten by a hook. Branches
# are resolved by full refname, so a tag named main or cr-<n> changes nothing.
#
# DHF-REQ: genesis-wp/requirement-34
set -euo pipefail

MAIN=main
MAIN_REF=refs/heads/$MAIN
UNIT='^cr-[1-9][0-9]*$'

refuse() {
	printf 'merge_unit: %s\n' "$*" >&2
	exit 1
}

[ "$#" -eq 1 ] || refuse "usage: merge_unit.sh <cr-<n> | commit sha>"
arg=$1

head=$(git symbolic-ref --quiet HEAD) || refuse "HEAD is detached, want $MAIN"
[ "$head" = "$MAIN_REF" ] || refuse "on branch ${head#refs/heads/}, want $MAIN"
for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
	[ ! -e "$(git rev-parse --git-path "$marker")" ] || refuse "$marker exists: conclude or abort the operation in progress first"
done
changes=$(git status --porcelain --untracked-files=no) || refuse "git status failed"
[ -z "$changes" ] || refuse "tracked changes in the working tree"

if [[ $arg =~ $UNIT ]]; then
	branch=$arg
	sha=$(git rev-parse --quiet --verify "refs/heads/$branch^{commit}") || refuse "no branch $branch"
else
	sha=$(git rev-parse --quiet --verify "$arg^{commit}") || refuse "cannot resolve $arg to a commit"
	heads=$(git for-each-ref --points-at "$sha" --format='%(refname:lstrip=2)' refs/heads/) || refuse "cannot list the branches at $sha"
	branches=$(printf '%s\n' "$heads" | grep -E "$UNIT" || true)
	count=$(printf '%s' "$branches" | grep -c . || true)
	[ "$count" -eq 1 ] || refuse "$count unit branches point at $sha, want exactly one: ${branches//$'\n'/ }"
	branch=$branches
fi

ahead=$(git rev-list --count "$MAIN_REF..$sha") || refuse "cannot count the commits of $branch ahead of $MAIN"
[ "$ahead" -gt 0 ] || refuse "$branch has nothing ahead of $MAIN"

main_before=$(git rev-parse --verify "$MAIN_REF")
subject="Merge $branch (change_request-${branch#cr-})"
if ! git merge --quiet --no-ff --no-edit -m "$subject" "$sha" >&2; then
	# MERGE_HEAD was absent above, so one now belongs to this merge.
	[ -e "$(git rev-parse --git-path MERGE_HEAD)" ] || refuse "merge of $branch into $MAIN failed; no merge started"
	git merge --abort || refuse "merge of $branch into $MAIN failed; git merge --abort failed, MERGE_HEAD left in place"
	refuse "merge of $branch into $MAIN failed; merge aborted"
fi

merged=$(git rev-parse --verify HEAD)
actual=$(git log -1 --format=%s "$merged")
if [ "$actual" != "$subject" ]; then
	git reset --quiet --keep "$main_before" || refuse "subject is '$actual', want '$subject'; resetting $MAIN to $main_before failed"
	refuse "subject is '$actual', want '$subject'; $MAIN reset to $main_before"
fi

printf 'code_change_ref=%s\n' "$merged"
