#!/usr/bin/env bash
# Write the series from a finished port back into patches/.
#
#   scripts/export-series.sh <upstream-tag> <upstream-clone>
#
# Expects branch port/<upstream-tag> from scripts/rebase-series.sh with the
# whole series applied. Replaces patches/*.patch with
# `git format-patch --zero-commit` of <upstream-tag>..HEAD, so the same
# series always produces the same files.
set -euo pipefail

tag=$1
up=$2
fork=$(cd "$(dirname "$0")/.." && pwd)

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

rm -f "$fork"/patches/*.patch
git format-patch -q --zero-commit --no-signature -o "$fork/patches" "$tag..HEAD"
ls "$fork/patches"
