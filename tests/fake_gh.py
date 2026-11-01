#!/usr/bin/env python3
"""テスト用の gh(`gh api` の一部だけ)。状態は $FAKE_GH_STATE の JSON。

GitHub と同じにしてあるところ:
- ref の作成は原子的で、既にあれば 422
- ref の更新(PATCH, force=false)は fast-forward のときだけ成功、そうでなければ 422
- check-runs は per_page ごとのページに分け、--paginate --slurp ではページの配列を返す
  (state の "drop_check_pages": n で最後の n ページを返さない = ページ未取得の再現)
- コメントの user.login と performed_via_github_app
"""
import fcntl
import hashlib
import json
import os
import re
import sys

STATE = os.environ["FAKE_GH_STATE"]


def load():
    with open(STATE) as f:
        return json.load(f)


def save(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE)


class Err(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


def main():
    a = sys.argv[1:]
    assert a[0] == "api"
    method, path, body, slurp = "GET", None, None, False
    i = 1
    while i < len(a):
        if a[i] == "-X":
            method = a[i + 1]; i += 2
        elif a[i] == "-H":
            i += 2
        elif a[i] == "--input":
            body = json.loads(sys.stdin.read()); i += 2
        elif a[i] in ("--paginate", "--slurp"):
            slurp = slurp or a[i] == "--slurp"; i += 1
        else:
            path = a[i]; i += 1
    with open(STATE + ".lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        s = load()
        with open(STATE + ".log", "a") as lg:
            lg.write(f"{method} {path}\n")
        if s.get("fail_paths") and any(re.search(p, path) for p in s["fail_paths"]):
            print(json.dumps({"message": "Server Error"}))
            print("gh: Server Error (HTTP 502)", file=sys.stderr)
            sys.exit(1)
        try:
            out = route(s, method, path.split("?")[0], path, body, slurp)
        except Err as e:
            print(json.dumps({"message": e.msg}))
            print(f"gh: {e.msg} (HTTP {e.code})", file=sys.stderr)
            sys.exit(1)
        save(s)
    if slurp and not (isinstance(out, list) and out and isinstance(out[0], dict) and out[0].get("_page")):
        out = [out]
    if isinstance(out, list):
        for p in out:
            if isinstance(p, dict):
                p.pop("_page", None)
    print(json.dumps(out))


def ancestors(s, sha):
    seen, stack = set(), [sha]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack += s.get("commits", {}).get(x, {}).get("parents", [])
    return seen


def route(s, method, p, full, body, slurp):
    m = re.match(r"repos/([^/]+/[^/]+)(.*)$", p)
    repo, rest = m.group(1), m.group(2)
    refs = s.setdefault("refs", {})
    if rest == "" and method == "GET":
        return {"default_branch": s.get("default_branch", "main")}
    # --- git data ---
    if (m := re.match(r"/git/ref/(.+)$", rest)) and method == "GET":
        name = m.group(1)
        key = name[len("heads/"):] if name.startswith("heads/") else "refs/" + name
        seq = s.get("ref_seq", {}).get(key)
        if seq:                                  # 試験用: 読むたびに次の値へ進む(判定中にベースが動く)
            refs[key] = seq.pop(0)
        ref = refs.get(key)
        if not ref:
            raise Err(404, "Not Found")
        return {"ref": "refs/" + name, "object": {"sha": ref}}
    if (m := re.match(r"/git/matching-refs/heads/(.+)$", rest)):
        pre = m.group(1)
        return [{"ref": "refs/heads/" + k} for k in refs if not k.startswith("refs/") and k.startswith(pre)]
    if rest == "/git/refs" and method == "POST":
        r = body["ref"]
        key = r[len("refs/heads/"):] if r.startswith("refs/heads/") else r
        if s.get("fail_branch_create") and r.startswith("refs/heads/"):
            raise Err(500, "Internal Server Error")
        if key in refs:
            raise Err(422, "Reference already exists")
        if r.count("/") < 2:
            raise Err(422, "Reference name is invalid")
        refs[key] = body["sha"]
        return {"ref": r, "object": {"sha": body["sha"]}}
    if (m := re.match(r"/git/refs/(.+)$", rest)) and method == "PATCH":
        name = m.group(1)
        key = name[len("heads/"):] if name.startswith("heads/") else "refs/" + name
        cur = refs.get(key)
        if cur is None:
            raise Err(422, "Reference does not exist")
        if s.get("fail_patch_refs"):
            raise Err(503, "Service Unavailable (test)")
        if not body.get("force") and cur not in ancestors(s, body["sha"]):
            raise Err(422, "Update is not a fast forward")
        refs[key] = body["sha"]
        return {"ref": "refs/" + name, "object": {"sha": body["sha"]}}
    if rest == "/git/commits" and method == "POST":
        sha = hashlib.sha1(json.dumps(body, sort_keys=True).encode() + os.urandom(4)).hexdigest()
        s.setdefault("commits", {})[sha] = {"message": body["message"], "tree": body["tree"], "parents": body["parents"]}
        return {"sha": sha}
    if (m := re.match(r"/git/commits/([0-9a-zA-Z_-]+)$", rest)):
        c = s.get("commits", {}).get(m.group(1))
        if not c:
            return {"sha": m.group(1), "message": "", "tree": {"sha": "tree-" + m.group(1)}, "parents": []}
        return {"sha": m.group(1), "message": c["message"], "tree": {"sha": c["tree"]}, "parents": [{"sha": x} for x in c["parents"]]}
    if (m := re.match(r"/commits/([^/]+)$", rest)) and method == "GET":
        info = s.get("branch_commits", {}).get(m.group(1))
        if info is None:
            raise Err(404, "No commit found")
        return {"commit": {"committer": {"date": info}}}
    # --- issues ---
    if rest == "/issues" and method == "GET":
        lab = re.search(r"labels=([^&]+)", full)
        return [it for it in s["issues"].values() if it["state"] == "open" and (not lab or lab.group(1) in [l["name"] for l in it["labels"]])]
    if (m := re.match(r"/issues/(\d+)$", rest)):
        n = m.group(1)
        if n in s["issues"]:
            return s["issues"][n]
        if n in s["pulls"]:
            pr = s["pulls"][n]
            return {"number": int(n), "pull_request": {"url": f"https://api.github.com/repos/{repo}/pulls/{n}"}, "state": pr["state"], "labels": pr.get("labels", [])}
        raise Err(404, "Not Found")
    if (m := re.match(r"/issues/(\d+)/labels$", rest)):
        tgt = s["issues"].get(m.group(1)) or s["pulls"].get(m.group(1))
        for l in body["labels"]:
            if l not in [x["name"] for x in tgt["labels"]]:
                tgt["labels"].append({"name": l})
        return tgt["labels"]
    if (m := re.match(r"/issues/(\d+)/labels/(.+)$", rest)) and method == "DELETE":
        tgt = s["issues"].get(m.group(1)) or s["pulls"].get(m.group(1))
        before = len(tgt["labels"])
        tgt["labels"] = [x for x in tgt["labels"] if x["name"] != m.group(2)]
        if len(tgt["labels"]) == before:
            raise Err(404, "Label does not exist")
        return {}
    if (m := re.match(r"/issues/(\d+)/comments$", rest)):
        cs = s["comments"].setdefault(m.group(1), [])
        if method == "POST":
            s["clock"] = s.get("clock", 0) + 1
            t = f"2026-10-29T10:{s['clock'] // 60:02d}:{s['clock'] % 60:02d}Z"
            c = {"id": 9000 + s["clock"], "body": body["body"], "created_at": t, "updated_at": t,
                 "user": {"login": s.get("actor", "owner")}, "performed_via_github_app": s.get("actor_app"),
                 "author_association": s.get("actor_assoc", "OWNER")}
            cs.append(c)
            return c
        return cs
    if (m := re.match(r"/issues/comments/(\d+)$", rest)) and method == "PATCH":
        for cs in s["comments"].values():
            for c in cs:
                if c["id"] == int(m.group(1)):
                    s["clock"] = s.get("clock", 0) + 1
                    c["body"] = body["body"]
                    c["updated_at"] = f"2026-10-29T11:{s['clock'] // 60:02d}:{s['clock'] % 60:02d}Z"
                    return c
        raise Err(404, "Not Found")
    if (m := re.match(r"/compare/([^.]+)\.\.\.(.+)$", rest)) and method == "GET":
        b, h = m.group(1), m.group(2)
        mb = s.get("merge_bases", {}).get(f"{b}...{h}") or s.get("merge_bases", {}).get(b) or b
        return {"merge_base_commit": {"sha": mb}, "status": "ahead"}
    if (m := re.match(r"/contents/(.+)$", rest)):
        ref = (re.search(r"ref=([^&]+)", full) or [None, s.get("default_branch", "main")])[1]
        cont = s.get("contents", {})
        if ref not in cont:                      # SHA で読んだら、その SHA を指すブランチの中身(試験用)
            for br, sha in list(refs.items()) + list(s.get("contents_by_sha", {}).items()):
                if sha == ref and br in cont:
                    ref = br
                    break
        text = cont.get(ref, {}).get(m.group(1))
        if text is None:
            raise Err(404, "Not Found")
        import base64
        return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
    # --- pulls ---
    if rest == "/pulls" and method == "GET":
        return [pr for pr in s["pulls"].values() if pr["state"] == "open"]
    if (m := re.match(r"/pulls/(\d+)/files$", rest)):
        return s.get("pr_files", {}).get(m.group(1), [])
    if (m := re.match(r"/pulls/(\d+)/commits$", rest)):
        return s.get("pr_commits", {}).get(m.group(1), [])
    if (m := re.match(r"/pulls/(\d+)$", rest)):
        pr = s["pulls"].get(m.group(1))
        if not pr:
            raise Err(404, "Not Found")
        seq = s.get("head_sequence", {}).get(m.group(1))   # 呼ぶたびに HEAD が進む(レビュー中の push の再現)
        if seq:
            pr["head"]["sha"] = seq.pop(0)
        bseq = s.get("base_sequence", {}).get(m.group(1))   # 呼ぶたびにマージ先が変わる(判定中のベースの変更の再現)
        if bseq:
            pr["base"]["ref"] = bseq.pop(0)
        return pr
    if rest == "/pulls" and method == "POST":
        if body.get("draft") and not s.get("draft_supported", True):
            raise Err(422, "Draft pull requests are not supported in this repository.")
        n = str(100 + len(s["pulls"]))
        s["pulls"][n] = {"number": int(n), "state": "open", "draft": body.get("draft", False), "title": body["title"],
                         "body": body["body"], "labels": [], "head": {"ref": body["head"], "sha": s.get("next_pr_sha", "sha-" + n)},
                         "base": {"ref": body["base"], "sha": "base-sha"}, "merged": False, "mergeable": True,
                         "mergeable_state": "clean", "html_url": f"https://github.com/{repo}/pull/{n}",
                         "updated_at": "2026-10-29T10:00:00Z"}
        return s["pulls"][n]
    # --- CI ---
    if (m := re.match(r"/commits/([^/]+)/check-runs$", rest)):
        runs = s.get("checks", {}).get(m.group(1), [])
        per = int((re.search(r"per_page=(\d+)", full) or [None, "30"])[1])
        pages = [runs[i:i + per] for i in range(0, len(runs), per)] or [[]]
        drop = s.get("drop_check_pages", 0)
        pages = pages[:len(pages) - drop] if drop else pages
        out = [{"_page": True, "total_count": len(runs), "check_runs": pg} for pg in pages]
        return out if slurp else out[0]
    if (m := re.match(r"/commits/([^/]+)/statuses$", rest)):
        return s.get("statuses", {}).get(m.group(1), [])
    if (m := re.match(r"/rules/branches/(.+)$", rest)):
        return s.get("rules", [])
    if (m := re.match(r"/branches/(.+)$", rest)):
        return {"name": m.group(1), "protected": bool(s.get("protection")), "protection": s.get("protection", {})}
    if rest == "/labels" and method == "POST":
        if body["name"] in s.setdefault("repo_labels", []):
            raise Err(422, "already_exists")
        s["repo_labels"].append(body["name"])
        return body
    raise Err(404, f"fake gh: no route {method} {p}")


if __name__ == "__main__":
    main()
