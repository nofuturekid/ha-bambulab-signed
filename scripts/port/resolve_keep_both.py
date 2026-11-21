"""Resolve diff3 conflict blocks as 'ours (HEAD) then theirs'.

For .json files a trailing comma is added to the last HEAD line so the
merged object stays valid. Usage: resolve_keep_both.py FILE...
"""
import json
import re
import sys

for path in sys.argv[1:]:
    lines = open(path, encoding="utf-8").read().split("\n")
    out, i = [], 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            out.append(lines[i]); i += 1; continue
        i += 1
        head, theirs = [], []
        while not lines[i].startswith("|||||||"):
            head.append(lines[i]); i += 1
        while lines[i] != "=======":
            i += 1
        i += 1
        while not lines[i].startswith(">>>>>>> "):
            theirs.append(lines[i]); i += 1
        i += 1
        if path.endswith(".json") and head and not head[-1].rstrip().endswith(","):
            head[-1] = head[-1].rstrip() + ","
        out += head + theirs
    text = "\n".join(out)
    if path.endswith(".json"):
        json.loads(text)
    open(path, "w", encoding="utf-8").write(text)
    print("resolved", path)
