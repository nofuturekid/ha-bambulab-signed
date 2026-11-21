"""MCP server (stdio) with the only operations the Claude jobs may run.

The auto-port gives Claude no shell. The porter gets these tools instead,
the reviewer only the read-only git tools.

  port_mcp.py port <upstream-tag> <upstream-clone> <patches-dir>
  port_mcp.py review <upstream-clone>

Every argument goes through one parser (parse_path, parse_rev, parse_range)
and only the parsed value is used afterwards. Paths are ASCII, relative,
already canonical (no ".", "..", "//", leading "-", nothing under .git) and
must resolve inside the clone without passing a symlink; git gets them after
"--" as literal pathspecs. Revisions go after --end-of-options.

git runs with a minimal environment (no secrets), no hooks, fsmonitor,
pager, external diff or textconv, no file:// transport, and with
core.symlinks=false, so no git operation can create a symlink in the tree.
The scripts it runs come from the same (read-only) directory as this file.
"""

import json
import os
import re
import subprocess
import tempfile
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
MAX_OUT = 60_000

_REF = r"[A-Za-z0-9_][A-Za-z0-9._/~^-]{0,99}"
REV = re.compile(_REF)
PATH_PART = re.compile(r"[A-Za-z0-9_@+][A-Za-z0-9._@+-]{0,99}")
COMMIT = re.compile(r"[0-9a-f]{7,40}")
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


class Refused(Exception):
    pass


def parse_rev(value, pattern=REV, what="rev"):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Refused(f"{what} not allowed: {value!r}")
    if ".." in value or "//" in value or "/." in value or value.endswith((".", "/", ".lock")):
        raise Refused(f"{what} not allowed: {value!r}")
    return value


def parse_range(value):
    """A rev, or two revs joined by ".." or "..."; each side parsed as a rev."""
    if not isinstance(value, str):
        raise Refused(f"range not allowed: {value!r}")
    sep = "..." if "..." in value else ".."
    sides = value.split(sep, 1) if sep in value else [value]
    for side in sides:
        parse_rev(side, what="range")
    return value


def parse_path(clone, value):
    """Canonical relative path inside clone, or Refused."""
    if not isinstance(value, str) or len(value) > 300:
        raise Refused(f"path not allowed: {value!r}")
    parts = value.split("/")
    if not all(PATH_PART.fullmatch(p) for p in parts) or ".git" in parts:
        raise Refused(f"path not allowed: {value!r}")
    if str(PurePosixPath(value)) != value:
        raise Refused(f"path not canonical: {value!r}")
    # No component on the way may be a symlink; the target must stay inside.
    current = clone
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise Refused(f"path passes a symlink: {value!r}")
    if os.path.realpath(current) != str(current):
        raise Refused(f"path leaves the clone: {value!r}")
    return value


def parse_paths(clone, values):
    if not isinstance(values, list) or not values or len(values) > 200:
        raise Refused("paths must be a list of 1 to 200 paths")
    return [parse_path(clone, v) for v in values]


def git_env(home):
    config = {
        "core.hooksPath": "/dev/null",
        "core.fsmonitor": "false",
        "core.symlinks": "false",
        "core.pager": "cat",
        "protocol.file.allow": "never",
        "diff.external": "",
    }
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": home,
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_CONFIG_COUNT": str(len(config)),
    }
    for i, (key, value) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


class Server:
    def __init__(self, mode, argv):
        if mode == "port":
            tag, clone, patches = argv
            if not TAG.fullmatch(tag):
                raise SystemExit("bad tag")
            self.tag, self.patches = tag, Path(patches).resolve()
        elif mode == "review":
            (clone,) = argv
        else:
            raise SystemExit("usage: port_mcp.py port|review ...")
        self.clone = Path(clone).resolve()
        # An empty, read-only HOME: git reads no user config or attributes.
        home = tempfile.mkdtemp(prefix="port-home-")
        os.chmod(home, 0o500)
        self.env = git_env(home)
        self.tools = dict(
            self.read_tools(), **(self.port_tools() if mode == "port" else {})
        )

    # --- running ---------------------------------------------------------
    def run(self, cmd):
        proc = subprocess.run(
            cmd,
            cwd=self.clone,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
        text = proc.stdout.decode("utf-8", "replace")
        if len(text) > MAX_OUT:
            text = (
                text[:MAX_OUT]
                + f"\n[... truncated, {len(text)} characters; narrow the path]"
            )
        return f"exit {proc.returncode}\n{text}"

    def git(self, *args):
        return self.run(["git", "--no-pager", "--literal-pathspecs", *args])

    def one_path(self, args):
        p = args.get("path")
        return ["--", parse_path(self.clone, p)] if p is not None else []

    # --- read-only tools -------------------------------------------------
    def t_log(self, a):
        mode = ["-p"] if a.get("patch") is True else ["--oneline"]
        return self.git(
            "log",
            "--no-ext-diff",
            "--no-textconv",
            "--max-count=300",
            *mode,
            "--end-of-options",
            parse_range(a.get("range")),
            *self.one_path(a),
        )

    def t_diff(self, a):
        stat = ["--stat"] if a.get("stat") is True else []
        return self.git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            *stat,
            "--end-of-options",
            parse_range(a.get("range")),
            *self.one_path(a),
        )

    def t_show(self, a):
        rev = parse_rev(a.get("rev"))
        if a.get("path") is not None:
            return self.git(
                "show",
                "--no-textconv",
                "--end-of-options",
                f"{rev}:{parse_path(self.clone, a['path'])}",
            )
        return self.git(
            "show",
            "--no-ext-diff",
            "--no-textconv",
            "--stat",
            "-p",
            "--end-of-options",
            rev,
        )

    # --- port tools ------------------------------------------------------
    def no_symlinks(self, out):
        """After any step that changes the tree: refuse to go on if it holds a
        symlink, from any source. The symlinks are removed first."""
        found = subprocess.run(
            ["find", str(self.clone), "-path", "*/.git", "-prune", "-o", "-type", "l", "-print"],
            env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
        ).stdout.split()
        if found:
            for link in found:
                os.unlink(link)
            raise Refused(f"symlinks in the tree (removed); the port cannot go on: {found}\n{out}")
        return out

    def t_start(self, a):
        return self.no_symlinks(
            self.run([str(SCRIPTS / "rebase-series.sh"), self.tag, str(self.clone), str(self.patches)])
        )

    def t_status(self, a):
        raw = self.git("am", "--show-current-patch=raw")
        subject = next(
            (l for l in raw.splitlines() if l.startswith("Subject: ")),
            "(no git am in progress)",
        )
        return (
            self.git("status", "--short")
            + "\nconflicted:\n"
            + self.git("diff", "--name-only", "--diff-filter=U")
            + "\npatch being applied: "
            + subject
        )

    def t_current_patch(self, a):
        return self.git("am", "--show-current-patch=diff")

    def t_resolve(self, a):
        script = {
            "keep_both": "resolve_keep_both.py",
            "replace_base": "resolve_replace_base.py",
        }.get(a.get("strategy"))
        if not script:
            raise Refused("strategy must be keep_both or replace_base")
        files = [str(self.clone / p) for p in parse_paths(self.clone, a.get("paths"))]
        return self.run(["python3", "-I", str(HERE / script), *files])

    def t_add(self, a):
        files = parse_paths(self.clone, a.get("paths"))
        for f in files:
            if re.search(
                rb"^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)( |$)",
                (self.clone / f).read_bytes(),
                re.MULTILINE,
            ):
                raise Refused(f"{f} still has conflict markers")
        return self.git("add", "--", *files)

    def t_continue(self, a):
        return self.no_symlinks(self.git("am", "--continue"))

    def t_fixup(self, a):
        commit = parse_rev(a.get("commit"), COMMIT, "commit")
        out = self.t_add(a)
        return self.no_symlinks(
            out + self.run([str(HERE / "fixup.sh"), self.tag, str(self.clone), commit])
        )

    def t_export(self, a):
        return self.run(
            [
                str(SCRIPTS / "export-series.sh"),
                self.tag,
                str(self.clone),
                str(self.patches),
            ]
        )

    # --- tool table ------------------------------------------------------
    def read_tools(self):
        s = {"type": "string"}
        return {
            "git_log": (
                self.t_log,
                "git log of a range (e.g. v2.4.0..v2.4.1, clusterm/mqtt_signing), optionally one path; patch=true adds diffs",
                {"range": s, "path": s, "patch": {"type": "boolean"}},
                ["range"],
            ),
            "git_diff": (
                self.t_diff,
                "git diff of a range (A..B, or one rev against the work tree), optionally one path; stat=true for a summary",
                {"range": s, "path": s, "stat": {"type": "boolean"}},
                ["range"],
            ),
            "git_show": (
                self.t_show,
                "git show of a commit, or of one file at a rev when path is given",
                {"rev": s, "path": s},
                ["rev"],
            ),
        }

    def port_tools(self):
        s = {"type": "string"}
        paths = {"type": "array", "items": s, "minItems": 1}
        return {
            "port_start": (
                self.t_start,
                "Check out the target tag and apply patches/ with git am --3way (scripts/rebase-series.sh). Exit 2 = conflict; git am stopped.",
                {},
                [],
            ),
            "port_status": (
                self.t_status,
                "git status, conflicted files and the patch git am is applying",
                {},
                [],
            ),
            "port_current_patch": (
                self.t_current_patch,
                "The diff of the patch git am stopped at (large; prefer git_diff with a path)",
                {},
                [],
            ),
            "port_resolve": (
                self.t_resolve,
                "Resolve diff3 conflict blocks with a resolver: keep_both (upstream's lines, then the patch's) or replace_base (upstream's additions, then the patch's rewrite of the base lines)",
                {"strategy": s, "paths": paths},
                ["strategy", "paths"],
            ),
            "port_add": (
                self.t_add,
                "Stage resolved files (refused while they contain conflict markers)",
                {"paths": paths},
                ["paths"],
            ),
            "port_continue": (self.t_continue, "git am --continue", {}, []),
            "port_fixup": (
                self.t_fixup,
                "Stage the given files and fold them into an applied patch (commit id) by an autosquash rebase",
                {"paths": paths, "commit": s},
                ["paths", "commit"],
            ),
            "port_export": (
                self.t_export,
                "Write the finished series back to patches/ (scripts/export-series.sh)",
                {},
                [],
            ),
        }

    def call(self, name, args):
        if name not in self.tools or not isinstance(args, dict):
            raise Refused(f"unknown tool {name!r}")
        return self.tools[name][0](args)

    def handle(self, req):
        method, msg_id, params = (
            req.get("method"),
            req.get("id"),
            req.get("params") or {},
        )
        if msg_id is None:
            return None  # notification
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "port", "version": "1"},
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": n,
                            "description": d,
                            "inputSchema": {
                                "type": "object",
                                "properties": p,
                                "required": r,
                                "additionalProperties": False,
                            },
                        }
                        for n, (_, d, p, r) in self.tools.items()
                    ]
                },
            }
        if method == "tools/call":
            try:
                text, err = (
                    self.call(params.get("name"), params.get("arguments") or {}),
                    False,
                )
            except (Refused, OSError, subprocess.TimeoutExpired) as e:
                text, err = f"refused: {e}", True
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": err},
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": "unknown method"},
        }


def main():
    server = Server(sys.argv[1], sys.argv[2:])
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if isinstance(req, dict) and (resp := server.handle(req)) is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
