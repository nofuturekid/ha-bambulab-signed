#!/usr/bin/env bash
# Prepare the reviewer's material. Uses git only; runs no upstream or PR code.
#
#   scripts/auto-port/review-context.sh <prev-tag> <tag> <old-patches> <new-patches> <out>
#
# <out>/upstream          greghesp/ha-bambulab with history, remote clusterm
# <out>/patched           worktree: <tag> with the new series applied
# <out>/range-diff.txt    old series on <prev-tag> vs new series on <tag>
# <out>/upstream.stat     what upstream changed from <prev-tag> to <tag>
# <out>/upstream-touched.diff  those changes, limited to files the series touches
# <out>/clusterm.txt      ClusterM's mqtt_signing since the pinned commit
set -euo pipefail

prev=$1
tag=$2
old=$(realpath "$3")
new=$(realpath "$4")
out=$(realpath -m "$5")
for t in "$prev" "$tag"; do
	[[ $t =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
		echo "bad tag: $t" >&2
		exit 1
	}
done

mkdir -p "$out"
up="$out/upstream"
git clone -q --filter=blob:none https://github.com/greghesp/ha-bambulab.git "$up"
cd "$up"
git config user.name review
git config user.email review@localhost
git config advice.detachedHead false
git remote add clusterm https://github.com/ClusterM/ha-bambulab.git
git fetch -q clusterm mqtt_signing || echo "ClusterM mqtt_signing could not be fetched" >"$out/clusterm.txt"

git checkout -q -b old "$prev"
git am -q --3way "$old"/*.patch
git checkout -q -b new "$tag"
git am -q --3way "$new"/*.patch
git worktree add -q --detach "$out/patched" new

git range-diff "$prev..old" "$tag..new" >"$out/range-diff.txt"
git diff --stat "$prev" "$tag" >"$out/upstream.stat"
mapfile -t touched < <(git diff --name-only "$tag" new)
git diff "$prev" "$tag" -- "${touched[@]}" >"$out/upstream-touched.diff"

if [ ! -s "$out/clusterm.txt" ]; then
	pin=$(tr -d '\n' <"$new/clusterm-mqtt_signing.sha")
	{
		echo "Pinned: $pin   Branch head: $(git rev-parse clusterm/mqtt_signing)"
		git log --oneline "$pin..clusterm/mqtt_signing" 2>/dev/null || echo "(pinned commit not on the branch)"
	} >"$out/clusterm.txt"
fi
ls "$out"
