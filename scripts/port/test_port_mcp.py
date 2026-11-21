"""Adversarial tests for scripts/port/port_mcp.py, the porter's only tools.

Why: the porter (Claude, holding the OAuth secret) reaches git and the port
scripts only through this server, and its inputs can be steered by prompt
injection from upstream content. Each test feeds an input that would escape
the clone, inject a git option, or follow a symlink, and requires a refusal.

Run: python3 -m pytest -q scripts/port
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import port_mcp

SERVER = Path(port_mcp.__file__).resolve()


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "up"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a\n")
    (r / "sub").mkdir()
    (r / "sub" / "x").write_text("x\n")
    (r / "link").symlink_to("/etc/passwd")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    git(r, "tag", "v1.0.0")
    # The series: one patch adding b.txt.
    git(r, "checkout", "-q", "-b", "series")
    (r / "b.txt").write_text("b\n")
    git(r, "add", "b.txt")
    git(r, "commit", "-q", "-m", "Add b")
    # A patch that tries to create a symlink out of the clone.
    (r / "bad").symlink_to("/etc/passwd")
    git(r, "add", "bad")
    git(r, "commit", "-q", "-m", "Add a symlink")
    patches = tmp_path / "patches"
    git(r, "format-patch", "-q", "-o", str(patches), "v1.0.0..HEAD")
    # Leave the clone on a commit without the symlink, so that checking out
    # the tag has to create it.
    git(r, "checkout", "-q", "-b", "other", "v1.0.0")
    git(r, "rm", "-q", "link")
    git(r, "commit", "-q", "-m", "Drop link")
    # An untracked symlink out of the clone, as a planted path would be.
    (r / "evil").symlink_to("/etc")
    return r, patches


BAD_PATHS = [
    "../x",
    "./patches/../x",
    "./a.txt",
    "a.txt/",
    "sub//x",
    "sub/./x",
    "sub/../a.txt",
    "-x",
    "--output=/tmp/pwned",
    "x\x00y",
    "ａ.txt",
    "é",
    "a.txt\n",
    " a.txt",
    ".git/config",
    ".git",
    "sub/.git/x",
    "/etc/passwd",
    "",
    ":(top)a.txt",
    ":!a.txt",
    "evil",
    "evil/x",
    "*",
    "a?.txt",
]


@pytest.mark.parametrize("value", BAD_PATHS)
def test_path_refused(repo, value):
    r, _ = repo
    with pytest.raises(port_mcp.Refused):
        port_mcp.parse_path(r.resolve(), value)


def test_path_accepted_is_returned_unchanged(repo):
    r, _ = repo
    assert port_mcp.parse_path(r.resolve(), "sub/x") == "sub/x"
    assert port_mcp.parse_path(r.resolve(), "a.txt") == "a.txt"


@pytest.mark.parametrize(
    "value",
    [
        "-o",
        "--output=x",
        "a;b",
        "$(id)",
        "a b",
        "HEAD@{1}",
        "x\n",
        "refs/../x",
        "a..b",
        "v1^{/x}",
        "a:b",
        "",
        "a/",
        "a.lock",
        None,
        3,
    ],
)
def test_rev_refused(value):
    with pytest.raises(port_mcp.Refused):
        port_mcp.parse_rev(value)


@pytest.mark.parametrize(
    "value", ["v1.0.0..--output=x", "a....b", "a..b..c", "..a", "a..", "a...-x"]
)
def test_range_refused(value):
    with pytest.raises(port_mcp.Refused):
        port_mcp.parse_range(value)


@pytest.mark.parametrize(
    "value", ["HEAD~1", "v2.4.1", "clusterm/mqtt_signing", "v2.4.0..v2.4.1", "a...b"]
)
def test_rev_and_range_accepted(value):
    assert port_mcp.parse_range(value) == value


def call(r, patches, *requests):
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    ]
    lines += [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": i + 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
        for i, (name, args) in enumerate(requests)
    ]
    out = subprocess.run(
        [sys.executable, "-I", str(SERVER), "port", "v1.0.0", str(r), str(patches)],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [json.loads(line)["result"] for line in out.splitlines()][1:]


def test_server_refuses_injection_and_creates_no_symlinks(repo, tmp_path):
    r, patches = repo
    target = tmp_path / "pwned"
    results = call(
        r, patches,
        ("port_start", {}),  # refused: the planted symlink "evil" is in the tree
        ("port_start", {}),
        ("git_diff", {"range": f"--output={target}"}),
        ("git_log", {"range": "v1.0.0", "path": "../x"}),
        ("port_add", {"paths": [".git/config"]}),
        ("port_resolve", {"strategy": "keep_both", "paths": ["-h"]}),
        ("port_fixup", {"paths": ["a.txt"], "commit": "--exec=id"}),
        ("no_such_tool", {}),
        ("git_show", {"rev": "HEAD", "path": "b.txt"}),
    )
    assert results[0]["isError"] and "evil" in results[0]["content"][0]["text"]
    assert not (r / "evil").exists() and not (r / "evil").is_symlink()
    assert not results[1]["isError"] and "applied cleanly" in results[1]["content"][0]["text"]
    assert all(res["isError"] for res in results[2:8])
    assert not target.exists()
    # core.symlinks=false: neither the tag's symlink nor the one the patch
    # adds became a symlink; git wrote plain files holding the link text.
    for name in ("link", "bad"):
        assert (r / name).is_file() and not (r / name).is_symlink()
    assert results[8]["content"][0]["text"].endswith("b\n")


def test_review_mode_has_only_read_tools(repo):
    r, _ = repo
    server = port_mcp.Server("review", [str(r)])
    assert set(server.tools) == {"git_log", "git_diff", "git_show"}
