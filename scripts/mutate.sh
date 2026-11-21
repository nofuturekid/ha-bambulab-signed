#!/usr/bin/env bash
# Mutation check: every mutation in scripts/mutations.txt must make a test
# fail on the patched upstream release.
#
#   scripts/mutate.sh <upstream-tag>
#
# Clones greghesp/ha-bambulab at <upstream-tag> and applies patches/*.patch
# like build.sh, then applies one mutation at a time and runs the tests named
# for it. Prints one line per mutation ("killed:", "SURVIVED:" or
# "NOT APPLIED:") and a final "mutations: N killed: K" line. Exit 0 only if
# every mutation was applied and killed.
set -uo pipefail

tag=$1
fork=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pb=custom_components/bambu_lab/pybambu

git -c advice.detachedHead=false clone -q --depth 1 --branch "$tag" https://github.com/greghesp/ha-bambulab.git "$work/patched" || exit 1
(cd "$work/patched" && git -c user.name=build -c user.email=build@localhost am -q --3way "$fork"/patches/*.patch) || exit 1

run_tests() {
	if [ "$1" = ours ]; then
		BAMBU_SRC="$work/src" python3 -m pytest -q -x -p no:cacheprovider "$fork/tests"
	else
		(cd "$work/src" && python3 -m pytest -q -x -p no:cacheprovider tests/pybambu)
	fi
}

total=0
killed=0
while IFS='|' read -r name file old new target; do
	case $name in '' | '#'*) continue ;; esac
	total=$((total + 1))
	rm -rf "$work/src"
	cp -a "$work/patched" "$work/src"
	if ! python3 -I - "$work/src/$pb/$file" "$old" "$new" <<'PY'
import sys

path = sys.argv[1]
old, new = (s.encode().decode("unicode_escape") for s in sys.argv[2:4])
text = open(path, encoding="utf-8").read()
if text.count(old) < 1:
    sys.exit(1)
open(path, "w", encoding="utf-8").write(text.replace(old, new, 1))
PY
	then
		echo "NOT APPLIED: $name"
		continue
	fi
	if run_tests "$target" </dev/null >/dev/null 2>&1; then
		echo "SURVIVED: $name"
	else
		echo "killed: $name"
		killed=$((killed + 1))
	fi
done <"$fork/scripts/mutations.txt"

echo "mutations: $total killed: $killed"
[ "$total" -gt 0 ] && [ "$killed" -eq "$total" ]
