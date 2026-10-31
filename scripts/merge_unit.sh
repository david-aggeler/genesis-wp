#!/usr/bin/env bash
# Land one unit branch cr-<n> on main as a no-ff merge commit titled
# "Merge cr-<n> (change_request-<n>)" and print code_change_ref=<merge sha>.
#
# openbrain-client merge runs this as merge_command (openbrain-client.yaml) and appends one
# argument: the commit SHA it resolved from branch-or-ref. A person may pass the branch
# name instead. Fails closed, leaving main untouched: not on main, dirty tracked tree,
# argument that resolves to no single cr-<n> branch, nothing ahead of main, conflict.
#
# DHF-REQ: genesis-wp/requirement-34
set -euo pipefail

MAIN=main
UNIT='^cr-[1-9][0-9]*$'

refuse() {
	printf 'merge_unit: %s\n' "$*" >&2
	exit 1
}

[ "$#" -eq 1 ] || refuse "usage: merge_unit.sh <cr-<n> | commit sha>"
arg=$1

head=$(git symbolic-ref --quiet --short HEAD) || refuse "HEAD is detached, want $MAIN"
[ "$head" = "$MAIN" ] || refuse "on branch $head, want $MAIN"
[ -z "$(git status --porcelain --untracked-files=no)" ] || refuse "tracked changes in the working tree"

if [[ $arg =~ $UNIT ]]; then
	branch=$arg
	sha=$(git rev-parse --quiet --verify "refs/heads/$branch^{commit}") || refuse "no branch $branch"
else
	sha=$(git rev-parse --quiet --verify "$arg^{commit}") || refuse "cannot resolve $arg to a commit"
	branches=$(git for-each-ref --points-at "$sha" --format='%(refname:short)' refs/heads/ | grep -E "$UNIT" || true)
	count=$(printf '%s' "$branches" | grep -c . || true)
	[ "$count" -eq 1 ] || refuse "$count unit branches point at $sha, want exactly one: ${branches//$'\n'/ }"
	branch=$branches
fi

[ "$(git rev-list --count "$MAIN..$sha")" -gt 0 ] || refuse "$branch has nothing ahead of $MAIN"

subject="Merge $branch (change_request-${branch#cr-})"
if ! git merge --quiet --no-ff --no-edit -m "$subject" "$sha" >&2; then
	git merge --abort || true
	refuse "merge of $branch into $MAIN failed; merge aborted"
fi

printf 'code_change_ref=%s\n' "$(git rev-parse HEAD)"
