#!/usr/bin/env bash
# Fold the staged changes in the upstream clone into an applied patch.
#
#   scripts/port/fixup.sh <upstream-tag> <upstream-clone> <commit>
#
# For changes found after `git am` finished: stage them, then name the
# commit (patch) they belong to. Runs `git commit --fixup` and an
# autosquash rebase onto <upstream-tag>; runs no other program.
set -euo pipefail

tag=$1
up=$2
commit=$3
cd "$up"
git rev-parse --verify -q "$commit^{commit}" >/dev/null
git merge-base --is-ancestor "$tag" "$commit"
git commit -q --fixup="$commit"
GIT_SEQUENCE_EDITOR=true git rebase -q -i --autosquash "$tag"
git log --oneline "$tag..HEAD"
