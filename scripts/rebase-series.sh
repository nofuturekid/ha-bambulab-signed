#!/usr/bin/env bash
# Start a port of patches/*.patch onto an upstream release.
#
#   scripts/rebase-series.sh <upstream-tag> <upstream-clone> [<patches-dir>]
#
# <patches-dir> defaults to this repository's patches/ (the auto-port runs
# a read-only copy of scripts/ and passes the workspace's patches/).
#
# <upstream-clone> is a full clone of greghesp/ha-bambulab (not a partial
# clone: `git am --3way` needs the base blobs locally). The script checks out <upstream-tag> on branch port/<upstream-tag>
# and applies the series.
#
# Exit 0: the whole series applied. Run scripts/export-series.sh next.
# Exit 2: a patch conflicts and `git am` stopped with the conflict in the
#   tree. Resolve it (scripts/port/ has resolvers for the usual shapes),
#   `git add` the files, `git am --continue`, repeat until the series is in,
#   then run scripts/export-series.sh.
set -euo pipefail
# Never create symlinks in the work tree, whatever a tree or patch says.
git() { command git -c core.symlinks=false "$@"; }

tag=$1
up=$2
patches=$(realpath "${3:-$(dirname "$0")/../patches}")
if ! [[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
	echo "not an upstream release tag: $tag" >&2
	exit 1
fi

cd "$up"
git am --abort 2>/dev/null || true
git checkout -q --detach "$tag"
git checkout -q -B "port/$tag"
# The patches carry their authors; the committer is irrelevant because
# export-series.sh writes the patches with --zero-commit.
git config user.name port
git config user.email port@localhost
git config merge.conflictStyle diff3

if git am -q --3way "$patches"/*.patch; then
	echo "series applied cleanly on $tag"
	exit 0
fi
echo "conflict in: $(git am --show-current-patch=raw | sed -n 's/^Subject: //p' | head -1)"
git diff --name-only --diff-filter=U
exit 2
