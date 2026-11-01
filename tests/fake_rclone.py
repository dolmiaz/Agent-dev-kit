#!/usr/bin/env python3
"""テスト用の rclone(lsjson / copyto だけ)。Drive は $FAKE_DRIVE/<folderId>/ のディレクトリ。呼び出しは .calls に記録。"""
import hashlib
import json
import os
import shutil
import sys

D = os.environ["FAKE_DRIVE"]
a = sys.argv[1:]
with open(os.path.join(D, ".calls"), "a") as f:
    f.write(" ".join(a) + "\n")
fid = a[a.index("--drive-root-folder-id") + 1]
folder = os.path.join(D, fid)
os.makedirs(folder, exist_ok=True)
if a[0] == "lsjson" and "--dirs-only" in a:
    tree = json.load(open(os.path.join(D, ".tree.json"))) if os.path.exists(os.path.join(D, ".tree.json")) else {}
    out, stack = [], list(tree.get(fid, []))
    while stack:
        c = stack.pop()
        out.append({"IsDir": True, "Path": c} if c.startswith("NOID") else {"ID": c, "IsDir": True, "Path": c})
        stack += tree.get(c, [])
    if os.path.exists(os.path.join(D, ".lsjson-fail")):
        print("ERROR: failed to list", file=sys.stderr)
        sys.exit(3)
    print(json.dumps(out))
elif a[0] == "lsjson":
    inc = a[a.index("--include") + 1].lstrip("/").replace("\\[", "[").replace("\\]", "]")
    out = []
    p = os.path.join(folder, inc)
    if os.path.exists(p):
        out.append({"Name": inc, "Hashes": {"md5": hashlib.md5(open(p, "rb").read()).hexdigest()}})
    print(json.dumps(out))
elif a[0] == "copyto":
    src = a[-2]
    name = a[-1].split(":", 1)[1]
    dst = os.path.join(folder, name)
    if not ("--ignore-existing" in a and os.path.exists(dst)):
        shutil.copy(src, dst)
else:
    sys.exit(2)
