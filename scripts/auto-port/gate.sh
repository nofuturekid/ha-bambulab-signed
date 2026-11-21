#!/usr/bin/env bash
# Merge gate of the auto-port workflow. Runs in a checkout of BASE (main when
# the port started) with HEAD fetched; it runs none of the pull request's
# code. Every check must hold; each failed one is printed and written to
# GITHUB_OUTPUT as part of `reasons`.
#
# Environment: BASE, HEAD, PR, CI_RESULT, CI_SHA, VERDICT, REPORT (mutation
# report from the CI job), GH_TOKEN, GITHUB_REPOSITORY, GITHUB_OUTPUT.
set -uo pipefail

reasons=()
no() {
	echo "::error::gate: $1"
	reasons+=("$1")
}

# 0. This script, scripts/mutations.txt and everything else read below come
#    from the trusted base, never from the pull request.
[ "$(git rev-parse HEAD)" = "$BASE" ] || no "the gate does not run from $BASE"
[ -z "$(git status --porcelain --untracked-files=no)" ] || no "the base checkout is modified"

# 1. CI green on exactly the commit that would be merged.
[ "$CI_RESULT" = success ] || no "PR CI result is '$CI_RESULT', not success"
[ "$CI_SHA" = "$HEAD" ] || no "PR CI tested $CI_SHA, not the head $HEAD"

# 2. The independent review said MERGE.
[ "$VERDICT" = MERGE ] || no "review verdict is '$VERDICT', not MERGE"

# 3. The pull request still points at HEAD, is open and targets main.
pr=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR" --jq '[.state, .head.sha, .base.ref] | join(" ")')
[ "$pr" = "open $HEAD main" ] || no "PR #$PR is '$pr', expected 'open $HEAD main'"

# 4. Protected invariants untouched, and nothing outside patches/ changed.
if ! changed=$(git diff --name-only "$BASE" "$HEAD"); then
	no "git diff $BASE $HEAD failed"
fi
for protected in tests/test_signed_envelope.py scripts/mutations.txt; do
	if grep -qxF "$protected" <<<"$changed"; then
		no "the PR modifies protected $protected"
	fi
done
outside=$(grep -v '^patches/' <<<"$changed" || true)
[ -z "$outside" ] || no "the PR changes files outside patches/: $(tr '\n' ' ' <<<"$outside")"

# 5. Every mutation in the trusted list (from BASE) was applied and killed.
expected=$(grep -cv '^\(#\|$\)' scripts/mutations.txt)
if [ ! -f "$REPORT" ]; then
	no "no mutation report"
else
	killed=$(grep -c '^killed: ' "$REPORT")
	other=$(grep -cE '^(SURVIVED|NOT APPLIED): ' "$REPORT")
	last=$(tail -n1 "$REPORT")
	if [ "$killed" != "$expected" ] || [ "$other" != 0 ] || [ "$last" != "mutations: $expected killed: $expected" ]; then
		no "mutation report: $killed of $expected killed, $other survived or not applied"
	fi
fi

# 6. HEAD is a GitHub-verified commit directly on top of BASE's history, and
#    main has not moved, so landing is a fast-forward.
verified=$(gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD" --jq .commit.verification.verified)
[ "$verified" = true ] || no "head commit signature not verified"
git merge-base --is-ancestor "$BASE" "$HEAD" || no "$HEAD does not contain $BASE"
main=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq .object.sha)
[ "$main" = "$BASE" ] || no "main moved from $BASE to $main"

if [ ${#reasons[@]} -eq 0 ]; then
	echo "gate: all checks passed for $HEAD"
	echo "ok=true" >>"$GITHUB_OUTPUT"
else
	echo "ok=false" >>"$GITHUB_OUTPUT"
	{
		echo "reasons<<GATE_EOF"
		printf -- '- %s\n' "${reasons[@]}"
		echo "GATE_EOF"
	} >>"$GITHUB_OUTPUT"
fi
