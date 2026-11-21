"""Resolve diff3 conflicts where HEAD = upstream additions + base lines.

Result: HEAD without the trailing base lines, followed by theirs (the
patch's rewrite of those base lines). Asserts the shape, so any other
kind of conflict stops the script. Usage: resolve_replace_base.py FILE...
"""
import json
import sys

for path in sys.argv[1:]:
    lines = open(path, encoding="utf-8").read().split("\n")
    out, i, n = [], 0, 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            out.append(lines[i]); i += 1; continue
        i += 1
        head, base, theirs = [], [], []
        while not lines[i].startswith("|||||||"):
            head.append(lines[i]); i += 1
        i += 1
        while lines[i] != "=======":
            base.append(lines[i]); i += 1
        i += 1
        while not lines[i].startswith(">>>>>>> "):
            theirs.append(lines[i]); i += 1
        i += 1
        assert base and head[-len(base):] == base, (path, head, base)
        out += head[:-len(base)] + theirs
        n += 1
    text = "\n".join(out)
    if path.endswith(".json"):
        json.loads(text)
    open(path, "w", encoding="utf-8").write(text)
    print("resolved", n, path)
