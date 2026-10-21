#!/usr/bin/env bash
# Build the patched integration for one upstream release.
#
#   ./build.sh <upstream-tag> <build-number> <out-dir>
#
# Clones greghesp/ha-bambulab at <upstream-tag>, applies patches/*.patch,
# runs the upstream library tests, the translation parity check and our
# envelope test, and writes <out-dir>/bambu_lab.zip plus <out-dir>/version
# (<upstream-version>.<build-number>, e.g. 2.2.26.1).
set -euo pipefail

tag=$1
build=$2
out=$(realpath -m "$3")
root=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

git clone -q --depth 1 --branch "$tag" https://github.com/greghesp/ha-bambulab.git "$work/src"
cd "$work/src"
git -c user.name=build -c user.email=build@localhost am -q --3way "$root"/patches/*.patch

version="${tag#v}.${build}"
python3 - "$version" <<'PY'
import json
import sys

path = "custom_components/bambu_lab/manifest.json"
manifest = json.load(open(path))
manifest["version"] = sys.argv[1]
open(path, "w").write(json.dumps(manifest, indent=2) + "\n")
PY

python3 -m compileall -q custom_components/bambu_lab
python3 -m pytest -q -p no:cacheprovider tests/pybambu
if [ -f scripts/check_translation_parity.py ]; then
	python3 scripts/check_translation_parity.py
fi
BAMBU_SRC="$work/src" python3 -m pytest -q -p no:cacheprovider "$root/tests"

mkdir -p "$out"
rm -f "$out/bambu_lab.zip"
find custom_components/bambu_lab -name __pycache__ -prune -exec rm -rf {} +
(cd custom_components/bambu_lab && zip -q -r "$out/bambu_lab.zip" .)
echo "$version" >"$out/version"
echo "built $version"
