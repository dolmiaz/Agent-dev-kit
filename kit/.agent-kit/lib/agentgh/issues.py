"""Issue / PR 本文の読み取り(見出し・依存・目印)と、着手できるかの判定。"""
from __future__ import annotations

import json
import re

from .api import api

MARK_RE = re.compile(r"<!--\s*agent-([a-z-]+):\s*(\{.*?\})\s*-->", re.S)
ISSUE_REF = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<n>\d+)|https://github\.com/(?P<repo2>[\w.-]+/[\w.-]+)/(?:issues|pull)/(?P<n2>\d+)")
CLOSES_RE = r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#{n}\b"


def marks(text: str, kind: str) -> list[dict]:
    """本文中の <!-- agent-<kind>: {json} --> を取り出す"""
    out = []
    for k, js in MARK_RE.findall(text or ""):
        if k == kind:
            try:
                out.append(json.loads(js))
            except json.JSONDecodeError:
                pass
    return out


def mark(kind: str, data: dict) -> str:
    return f"<!-- agent-{kind}: {json.dumps(data, ensure_ascii=False)} -->"


def section(body: str, names: tuple[str, ...]) -> str:
    """Issue form / PR テンプレートの見出し(### 名前)の本文を取り出す"""
    for n in names:
        m = re.search(rf"^#+\s*{re.escape(n)}[^\n]*\n(.*?)(?=^#+\s|\Z)", body or "", re.S | re.M)
        if m:
            return m.group(1)
    return ""


def dep_refs(repo: str, body: str) -> list[tuple[str, int]]:
    sec = section(body, ("依存", "依存Issue/PR", "依存 Issue / PR", "Depends on", "Dependencies"))
    refs = []
    for m in ISSUE_REF.finditer(sec):
        r = m.group("repo") or m.group("repo2") or repo
        n = int(m.group("n") or m.group("n2"))
        if (r, n) not in refs:
            refs.append((r, n))
    return refs


def dep_status(repo: str, n: int) -> tuple[bool, str, dict]:
    it = api("GET", f"repos/{repo}/issues/{n}")
    if "pull_request" in it:
        pr = api("GET", f"repos/{repo}/pulls/{n}")
        if pr.get("merged"):
            return True, f"PR {repo}#{n} マージ済み", pr
        return False, f"PR {repo}#{n} 未マージ({pr.get('state')})", pr
    if it.get("state") == "closed" and it.get("state_reason") in (None, "completed"):
        return True, f"Issue {repo}#{n} 完了", it
    return False, f"Issue {repo}#{n} 未完了({it.get('state')}/{it.get('state_reason')})", it


def label_names(it: dict) -> set[str]:
    return {l["name"] if isinstance(l, dict) else l for l in it.get("labels", [])}


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].strip("-") or "issue"


def evaluate_issue(repo: str, it: dict, claim: dict | None) -> dict:
    """着手できるか。claim は claims.read_claim の結果(担当確保の正本)。"""
    labels = label_names(it)
    res = {"number": it["number"], "title": it["title"], "eligible": False, "reasons": [], "stack_on": None}
    if "agent:ready" not in labels:
        res["reasons"].append("agent:ready が無い")
    if claim and claim.get("state") in ("claimed", "handoff"):
        res["reasons"].append(f"担当確保済み({claim.get('agent', '?')})")
    if "agent:blocked" in labels:
        res["reasons"].append("agent:blocked")
    body = it.get("body") or ""
    for need, names in (("期待する動作", ("期待する動作", "Expected")),
                        ("受入条件", ("受入条件", "受入条件/確認方法", "Acceptance"))):
        if not section(body, names).strip():
            res["reasons"].append(f"Issue に「{need}」が無い")
    for r, n in dep_refs(repo, body):
        ok, msg, obj = dep_status(r, n)
        if ok:
            continue
        if "agent:stack-ok" in labels and obj.get("head") and obj.get("state") == "open" and r == repo:
            res["stack_on"] = obj["head"]["ref"]
            res.setdefault("notes", []).append(f"{msg} の上に積む(agent:stack-ok)")
        else:
            res["reasons"].append(f"依存待ち: {msg}")
    res["eligible"] = not res["reasons"]
    return res


def closes_issue(body: str, n: int) -> bool:
    return bool(re.search(CLOSES_RE.format(n=n), body or ""))
