"""Commit the porter's patch set to the auto-port branch through the API.

Runs in the auto-port workflow's publish job, which holds the write token;
the porter job that produced the files has none. Commits created through the
API with the workflow token are signed by GitHub.

Environment:
  IN_DIR      artifact from the porter (patches/ only is used)
  FORK        checkout of PARENT (to compare against)
  PARENT      commit the new commit goes on (main for round 1, the PR head
              for the fix round)
  BRANCH      auto-port/<tag>
  ROUND       1 creates BRANCH, 2 fast-forwards it
  MESSAGE     commit message
  GH_TOKEN, GITHUB_REPOSITORY, GITHUB_API_URL, GITHUB_OUTPUT

Only patches/NNNN-*.patch and patches/clusterm-mqtt_signing.sha are taken.
Anything else in the artifact fails the job. Writes changed=true|false and
head=<sha> to GITHUB_OUTPUT.
"""

import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

PATCH = re.compile(r"^[0-9]{4}-[A-Za-z0-9._-]{1,80}\.patch$")
PIN = "clusterm-mqtt_signing.sha"
# git format-patch writes the commit id into the first line; the files are
# generated with --zero-commit, the committed ones may still carry a real id.
FROM_LINE = re.compile(rb"^From [0-9a-f]{40} Mon Sep 17 00:00:00 2001\n")
MAX_BYTES = 2_000_000


def fail(msg):
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def read_set(directory):
    files = {}
    for path in sorted(Path(directory).iterdir()):
        name = path.name
        if path.is_symlink() or not path.is_file():
            fail(f"not a regular file: {name}")
        if not (PATCH.match(name) or name == PIN):
            fail(f"unexpected file in patches/: {name}")
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            fail(f"too large: {name}")
        if name == PIN and not re.fullmatch(rb"[0-9a-f]{40}\n?", data):
            fail(f"{PIN} must hold one commit id")
        files[name] = data
    if not any(PATCH.match(n) for n in files):
        fail("no patches in the porter's output")
    return files


def normal(data):
    return FROM_LINE.sub(
        b"From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n",
        data,
        count=1,
    )


def api(method, path, body=None):
    url = (
        f"{os.environ['GITHUB_API_URL']}/repos/{os.environ['GITHUB_REPOSITORY']}{path}"
    )
    req = urllib.request.Request(
        url, method=method, data=None if body is None else json.dumps(body).encode()
    )
    req.add_header("Authorization", f"Bearer {os.environ['GH_TOKEN']}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.writelines(f"{key}={value}\n" for key, value in values.items())


def main():
    new = read_set(Path(os.environ["IN_DIR"], "patches"))
    old = read_set(Path(os.environ["FORK"], "patches"))
    parent = os.environ["PARENT"]
    if not re.fullmatch(r"[0-9a-f]{40}", parent):
        fail("PARENT is not a commit id")

    if {n: normal(d) for n, d in new.items()} == {n: normal(d) for n, d in old.items()}:
        print(
            "The porter's patch set is identical to the current one: nothing to publish."
        )
        output(changed="false", head=parent)
        return

    tree = []
    for name, data in new.items():
        if old.get(name) == data:
            continue
        blob = api(
            "POST",
            "/git/blobs",
            {"content": base64.b64encode(data).decode(), "encoding": "base64"},
        )
        tree.append(
            {
                "path": f"patches/{name}",
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    for name in old.keys() - new.keys():
        tree.append(
            {"path": f"patches/{name}", "mode": "100644", "type": "blob", "sha": None}
        )

    base_tree = api("GET", f"/git/commits/{parent}")["tree"]["sha"]
    new_tree = api("POST", "/git/trees", {"base_tree": base_tree, "tree": tree})["sha"]
    commit = api(
        "POST",
        "/git/commits",
        {"message": os.environ["MESSAGE"], "tree": new_tree, "parents": [parent]},
    )
    sha = commit["sha"]
    branch = os.environ["BRANCH"]
    if os.environ["ROUND"] == "1":
        api("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
    else:
        api("PATCH", f"/git/refs/heads/{branch}", {"sha": sha, "force": False})
    print(
        f"Committed {sha} to {branch} (signature verified: {commit['verification']['verified']})"
    )
    output(changed="true", head=sha)


main()
