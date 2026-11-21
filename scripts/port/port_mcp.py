"""MCP server (stdio) with the only operations the Claude jobs may run.

The auto-port gives Claude no shell. The porter gets these tools instead;
every argument is checked against a strict pattern and git runs with a
minimal environment (no secrets), without hooks, fsmonitor, pager, external
diff or textconv, and without the file:// transport.

  port_mcp.py port <upstream-tag> <upstream-clone> <patches-dir>
  port_mcp.py review <upstream-clone>

`review` offers only the read-only tools (log, diff, show).
The scripts it runs come from the same (read-only) directory as this file.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
REV = re.compile(r"^(?!-)[A-Za-z0-9._/^~-]{1,100}$")
RANGE = re.compile(
    r"^(?!-)[A-Za-z0-9._/^~-]{1,100}(\.\.\.?(?!-)[A-Za-z0-9._/^~-]{1,100})?$"
)
PATH = re.compile(
    r"^(?!-)(?!.*(^|/)\.\.(/|$))(?!(.*/)?\.git(/|$))[A-Za-z0-9._@+/-]{1,300}$"
)
MAX_OUT = 60_000

mode = sys.argv[1]
if mode == "port":
    TAG, CLONE, PATCHES = (
        sys.argv[2],
        Path(sys.argv[3]).resolve(),
        Path(sys.argv[4]).resolve(),
    )
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", TAG):
        sys.exit("bad tag")
elif mode == "review":
    CLONE = Path(sys.argv[2]).resolve()
else:
    sys.exit("usage: port_mcp.py port|review ...")

ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": str(CLONE),
    "LANG": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "true",
    "GIT_SEQUENCE_EDITOR": "true",
    "GIT_CONFIG_COUNT": "4",
    "GIT_CONFIG_KEY_0": "core.hooksPath",
    "GIT_CONFIG_VALUE_0": "/dev/null",
    "GIT_CONFIG_KEY_1": "core.fsmonitor",
    "GIT_CONFIG_VALUE_1": "false",
    "GIT_CONFIG_KEY_2": "protocol.file.allow",
    "GIT_CONFIG_VALUE_2": "never",
    "GIT_CONFIG_KEY_3": "core.pager",
    "GIT_CONFIG_VALUE_3": "cat",
}


class Refused(Exception):
    pass


def check(pattern, value, what):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Refused(f"{what} not allowed: {value!r}")
    return value


def paths(values):
    if not isinstance(values, list) or not values:
        raise Refused("paths must be a non-empty list")
    out = []
    for value in values:
        check(PATH, value, "path")
        full = (CLONE / value).resolve()
        if CLONE not in full.parents or full.is_symlink():
            raise Refused(f"path outside the clone: {value!r}")
        out.append(value)
    return out


def run(cmd, cwd=None):
    proc = subprocess.run(
        cmd,
        cwd=cwd or CLONE,
        env=ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=600,
    )
    text = proc.stdout.decode("utf-8", "replace")
    if len(text) > MAX_OUT:
        text = (
            text[:MAX_OUT]
            + f"\n[... truncated, {len(text)} characters in total; narrow the path]"
        )
    return f"exit {proc.returncode}\n{text}"


def git(*args):
    return run(["git", "--no-pager", *args])


def opt_path(args):
    p = args.get("path")
    return ["--", *paths([p])] if p else []


def t_log(a):
    return git(
        "log",
        "--no-ext-diff",
        "--no-textconv",
        "--max-count=300",
        *(["-p"] if a.get("patch") else ["--oneline"]),
        check(RANGE, a["range"], "range"),
        *opt_path(a),
    )


def t_diff(a):
    return git(
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        *(["--stat"] if a.get("stat") else []),
        check(RANGE, a["range"], "range"),
        *opt_path(a),
    )


def t_show(a):
    rev = check(REV, a["rev"], "rev")
    if a.get("path"):
        return git("show", "--no-textconv", f"{rev}:{paths([a['path']])[0]}")
    return git("show", "--no-ext-diff", "--no-textconv", "--stat", "-p", rev)


def t_start(a):
    return run([str(SCRIPTS / "rebase-series.sh"), TAG, str(CLONE), str(PATCHES)])


def t_status(a):
    raw = git("am", "--show-current-patch=raw")
    subject = next((line for line in raw.splitlines() if line.startswith("Subject: ")), "(no git am in progress)")
    return (git("status", "--short")
            + "\nconflicted:\n" + git("diff", "--name-only", "--diff-filter=U")
            + "\npatch being applied: " + subject)


def t_current_patch(a):
    return git("am", "--show-current-patch=diff")


def t_resolve(a):
    script = {
        "keep_both": "resolve_keep_both.py",
        "replace_base": "resolve_replace_base.py",
    }.get(a.get("strategy"))
    if not script:
        raise Refused("strategy must be keep_both or replace_base")
    return run(["python3", "-I", str(HERE / script), *paths(a["paths"])])


def t_add(a):
    files = paths(a["paths"])
    for f in files:
        if re.search(
            rb"^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)( |$)", (CLONE / f).read_bytes(), re.MULTILINE
        ):
            raise Refused(f"{f} still has conflict markers")
    return git("add", "--", *files)


def t_continue(a):
    return git("am", "--continue")


def t_fixup(a):
    commit = check(re.compile(r"^[0-9a-f]{7,40}$"), a["commit"], "commit")
    out = t_add(a)
    return out + run([str(HERE / "fixup.sh"), TAG, str(CLONE), commit])


def t_export(a):
    return run([str(SCRIPTS / "export-series.sh"), TAG, str(CLONE), str(PATCHES)])


S = {"type": "string"}
PATHS = {"type": "array", "items": S, "minItems": 1}
READ_TOOLS = {
    "git_log": (
        t_log,
        "git log of a range (e.g. v2.4.0..v2.4.1, clusterm/mqtt_signing), optionally one path; patch=true adds diffs",
        {"range": S, "path": S, "patch": {"type": "boolean"}},
        ["range"],
    ),
    "git_diff": (
        t_diff,
        "git diff of a range (A..B or one rev against the work tree), optionally one path; stat=true for a summary",
        {"range": S, "path": S, "stat": {"type": "boolean"}},
        ["range"],
    ),
    "git_show": (
        t_show,
        "git show of a commit, or of one file at a rev when path is given",
        {"rev": S, "path": S},
        ["rev"],
    ),
}
PORT_TOOLS = {
    "port_start": (
        t_start,
        "Check out the target tag and apply patches/ with git am --3way (scripts/rebase-series.sh). Exit 2 = conflict; git am stopped.",
        {},
        [],
    ),
    "port_status": (
        t_status,
        "git status, conflicted files and the patch git am is applying",
        {},
        [],
    ),
    "port_current_patch": (t_current_patch, "The diff of the patch git am stopped at (large; prefer git_diff with a path)", {}, []),
    "port_resolve": (
        t_resolve,
        "Resolve diff3 conflict blocks in files with a resolver: keep_both (HEAD then patch) or replace_base (HEAD without the base lines, then the patch)",
        {"strategy": S, "paths": PATHS},
        ["strategy", "paths"],
    ),
    "port_add": (
        t_add,
        "Stage resolved files (refused while they contain conflict markers)",
        {"paths": PATHS},
        ["paths"],
    ),
    "port_continue": (t_continue, "git am --continue", {}, []),
    "port_fixup": (
        t_fixup,
        "Stage the given files and fold them into an applied patch (commit id) by an autosquash rebase",
        {"paths": PATHS, "commit": S},
        ["paths", "commit"],
    ),
    "port_export": (
        t_export,
        "Write the finished series back to patches/ (scripts/export-series.sh)",
        {},
        [],
    ),
}
TOOLS = dict(READ_TOOLS, **(PORT_TOOLS if mode == "port" else {}))


def reply(msg_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": msg_id}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, msg_id, params = req.get("method"), req.get("id"), req.get("params") or {}
    if msg_id is None:
        continue  # notifications
    if method == "initialize":
        reply(
            msg_id,
            {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "port", "version": "1"},
            },
        )
    elif method == "tools/list":
        reply(
            msg_id,
            {
                "tools": [
                    {
                        "name": name,
                        "description": desc,
                        "inputSchema": {
                            "type": "object",
                            "properties": props,
                            "required": req_,
                            "additionalProperties": False,
                        },
                    }
                    for name, (_, desc, props, req_) in TOOLS.items()
                ]
            },
        )
    elif method == "tools/call":
        name = params.get("name")
        try:
            if name not in TOOLS:
                raise Refused(f"unknown tool {name}")
            text, is_error = TOOLS[name][0](params.get("arguments") or {}), False
        except (Refused, KeyError, OSError, subprocess.TimeoutExpired) as e:
            text, is_error = f"refused: {e}", True
        reply(
            msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error}
        )
    elif method == "ping":
        reply(msg_id, {})
    else:
        reply(msg_id, error={"code": -32601, "message": f"unknown method {method}"})
