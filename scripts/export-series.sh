#!/usr/bin/env bash
# Write the series from a finished port back into patches/.
#
#   scripts/export-series.sh <upstream-tag> <upstream-clone> [<patches-dir>]
#
# <patches-dir> defaults to this repository's patches/ (the auto-port runs
# a read-only copy of scripts/ and passes the workspace's patches/).
#
# Expects branch port/<upstream-tag> from scripts/rebase-series.sh with the
# whole series applied. Replaces patches/*.patch with
# `git format-patch --zero-commit` of <upstream-tag>..HEAD, so the same
# series always produces the same files.
set -euo pipefail
# Never create symlinks in the work tree, whatever a tree or patch says.
git() { command git -c core.symlinks=false "$@"; }

tag=$1
up=$2
patches=$(realpath "${3:-$(dirname "$0")/../patches}")

cd "$up"
if [ -d "$(git rev-parse --git-path rebase-apply)" ]; then
	echo "git am is still in progress" >&2
	exit 1
fi
if [ "$(git rev-parse --abbrev-ref HEAD)" != "port/$tag" ]; then
	echo "not on port/$tag" >&2
	exit 1
fi
git merge-base --is-ancestor "$tag" HEAD
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
	echo "uncommitted changes in $up" >&2
	exit 1
fi
if git diff "$tag" HEAD | grep -qE '^\+(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)( |$)'; then
	echo "conflict markers in the series" >&2
	exit 1
fi
# Upstream ships some files without a final newline; the series must not
# change that.
if git diff "$tag" HEAD -- custom_components | grep -q 'No newline at end of file'; then
	echo "the series changes a file's final newline" >&2
	exit 1
fi

rm -f "$patches"/*.patch
git format-patch -q --zero-commit --no-signature -o "$patches" "$tag..HEAD"
ls "$patches"
