"""マージ可否の技術判定(merge-gate)。マージはしない。

判定の条件はベースブランチの設定で決める(basecfg.py。PR 自身が条件を変えられないように)。

必須 CI(和集合。ci-policy で GitHub 側の必須を外すことはできない):
  1. GitHub のルールセット(GET rules/branches/<base> の required_status_checks。app の指定も守る)
  2. ブランチ保護(GET branches/<base> の protection.required_status_checks)
  3. ベースブランチの .agent-kit/ci-policy.json の required(名前ごとに、成功とみなす結論を決める)
どれからも必須 CI が決まらなければ合格にしない(ベースの AGENT_ALLOW_NO_CI=1 のときだけ通す)。
check-runs・statuses は全ページ取り、件数が total_count に足りなければ「ページ未取得」で合格にしない。
同じ名前の check が複数あるとき(再実行・別の suite)、どれか1つでも未完了・失敗なら合格にしない。
app の指定がある必須 check は、その app の check run だけで満たす(commit status では満たさない)。
未起動(結果が無い)・実行中・取得失敗も合格にしない。mergeable が不明(null・unknown)ならコンフリクトなしと決めつけない。

記録(レビュー・本人確認)は PR のコメントの目印から読む。編集されたコメントの目印は使わない。
同じ GitHub の権限を共有する構成では、目印の偽造そのものは防げない(docs/09 の限界)。
"""
from __future__ import annotations

import json
import time

from . import humanconfirm, issues
from .api import ApiError, api, cfg, try_api
from .basecfg import BaseConfig, diff_anchor

DEFAULT_ALLOW = ["success"]
GATE_FILES = (".agent-kit/config.env", ".agent-kit/ci-policy.json", ".agent-kit/human-confirm-allowed-signers")


# --- 必須 CI -------------------------------------------------------------------
def _app(v):
    """app の指定。-1(どの app でもよい)・0・None は指定なし"""
    return v if isinstance(v, int) and v > 0 else None

def platform_required(repo: str, base: str) -> tuple[dict[str, int | None], list[str]]:
    """GitHub 側で必須の check。名前 → app の id(指定が無ければ None)。取得できなかった理由も返す。"""
    req: dict[str, int | None] = {}
    problems = []

    def add(name, app_id):
        if name and (name not in req or req[name] is None):
            req[name] = app_id
    try:
        for rule in try_api("GET", f"repos/{repo}/rules/branches/{base}", default=[]) or []:
            if rule.get("type") == "required_status_checks":
                for c in (rule.get("parameters") or {}).get("required_status_checks", []):
                    add(c.get("context"), _app(c.get("integration_id")))
    except ApiError as e:
        problems.append(f"ルールセットを読めない({e})")
    try:
        br = api("GET", f"repos/{repo}/branches/{base}")
        rsc = ((br.get("protection") or {}).get("required_status_checks") or {})
        for c in rsc.get("checks") or []:
            add(c.get("context"), _app(c.get("app_id")))
        for c in rsc.get("contexts") or []:
            add(c, None)
    except ApiError as e:
        problems.append(f"ブランチ保護を読めない({e})")
    return req, problems


def required_checks(repo: str, base: str, policy: dict) -> tuple[dict[str, dict], list[str]]:
    """必須 check 名 → {"allow": [...], "app_id": id or None}"""
    names, problems = platform_required(repo, base)
    req = {n: {"allow": list(DEFAULT_ALLOW), "app_id": app} for n, app in names.items()}
    for item in policy.get("required", []):
        cur = req.setdefault(item["name"], {"allow": list(DEFAULT_ALLOW), "app_id": None})
        cur["allow"] = list(item.get("allow") or DEFAULT_ALLOW)
        cur["app_id"] = cur["app_id"] or item.get("app_id")
    return req, problems


# --- 結果の取得 ----------------------------------------------------------------
def fetch_results(repo: str, sha: str) -> tuple[dict[str, list[dict]], list[str]]:
    """名前 → 結果の一覧(同名を全部残す)。{"kind", "status", "conclusion", "app_id", "suite"}"""
    problems, out = [], {}
    pages = api("GET", f"repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=all", paginate=True) or []
    if isinstance(pages, dict):
        pages = [pages]
    runs = [r for p in pages for r in (p.get("check_runs") or [])]
    total = max((p.get("total_count", 0) for p in pages), default=0)
    if len(runs) < total:
        problems.append(f"check-runs のページ未取得({len(runs)}/{total})")
    latest: dict[tuple, dict] = {}       # (名前, suite) ごとに一番新しい run(再実行の前の結果は使わない)
    for r in runs:
        key = (r.get("name"), (r.get("check_suite") or {}).get("id"))
        if key not in latest or (r.get("id") or 0) > (latest[key].get("id") or 0):
            latest[key] = r
    for (name, suite), r in latest.items():
        out.setdefault(name, []).append({"kind": "check", "status": r.get("status"), "conclusion": r.get("conclusion"),
                                         "app_id": (r.get("app") or {}).get("id"), "suite": suite})
    statuses = api("GET", f"repos/{repo}/commits/{sha}/statuses?per_page=100", paginate=True) or []
    seen = set()
    for s in statuses:                      # 新しい順に返る。context ごとに最初のものを使う
        ctx = s.get("context")
        if ctx in seen:
            continue
        seen.add(ctx)
        st = s.get("state")
        out.setdefault(ctx, []).append({"kind": "status",
                                        "status": "completed" if st in ("success", "failure", "error") else "in_progress",
                                        "conclusion": st if st != "error" else "failure", "app_id": None, "suite": None})
    return out, problems


FAILED = ("failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale")


def evaluate_ci(req: dict[str, dict], results: dict[str, list[dict]], policy: dict) -> tuple[list[str], list[str]]:
    reasons, notes = [], []
    for name, spec in sorted(req.items()):
        rs = results.get(name, [])
        if spec["app_id"]:
            rs = [r for r in rs if r["kind"] == "check" and r["app_id"] == spec["app_id"]]
        if not rs:
            reasons.append(f"必須 CI「{name}」の結果が無い(未起動・未報告"
                           + (f"。app {spec['app_id']} の check run だけを数える" if spec["app_id"] else "") + ")")
            continue
        pending = [r for r in rs if r["status"] != "completed"]
        bad = [r for r in rs if r["status"] == "completed" and r["conclusion"] not in spec["allow"]]
        if pending:
            reasons.append(f"必須 CI「{name}」が実行中・待機中({pending[0]['status']})")
        elif bad:
            reasons.append(f"必須 CI「{name}」が {bad[0]['conclusion']}(成功とみなすのは {', '.join(spec['allow'])})")
    if policy.get("block_on_other_failures", True):
        for name, rs in sorted(results.items()):
            if name not in req and any(r["status"] == "completed" and r["conclusion"] in FAILED for r in rs):
                reasons.append(f"必須でない CI「{name}」が失敗")
    others = [n for n in results if n not in req]
    if others:
        notes.append(f"必須でない CI {len(others)} 件は判定に使っていない(失敗以外)")
    return reasons, notes


def ci_section(repo: str, sha: str, base: str, bc: BaseConfig) -> tuple[list[str], list[str]]:
    policy = bc.policy()
    if policy.get("_error"):
        return [policy["_error"]], []
    notes0 = (["ci-policy の use_platform_required:false は無視した(GitHub 側の必須は外せない)"]
              if policy.get("use_platform_required") is False else [])
    try:
        req, problems = required_checks(repo, base, policy)
        results, fetch_problems = fetch_results(repo, sha)
    except ApiError as e:
        return [f"CI の結果を取得できない({e})"], notes0
    reasons = list(fetch_problems) + [f"必須 CI の一覧を確かめられない: {p}" for p in problems]
    if not req:
        if bc.get("AGENT_ALLOW_NO_CI") == "1":
            return reasons, notes0 + ["必須 CI なし(ベースブランチの AGENT_ALLOW_NO_CI=1 で許可)"]
        return reasons + ["必須 CI が決まっていない(ルールセット・ブランチ保護・ベースブランチの .agent-kit/ci-policy.json の"
                          "どれにも無い)。成功している check があっても、それが必要なテストかは判断しない"], notes0
    r2, n2 = evaluate_ci(req, results, policy)
    return reasons + r2, notes0 + n2


# --- mergeable -----------------------------------------------------------------
def mergeable_section(repo: str, number: int, pr: dict) -> list[str]:
    tries = int(cfg("AGENT_MERGEABLE_RETRY", "3"))
    for _ in range(tries):
        if pr.get("mergeable") is not None and pr.get("mergeable_state") != "unknown":
            break
        time.sleep(float(cfg("AGENT_MERGEABLE_WAIT", "2")))
        pr = api("GET", f"repos/{repo}/pulls/{number}")
    if pr.get("mergeable") is None or pr.get("mergeable_state") == "unknown":
        return ["mergeable が不明(GitHub が計算中か取得できない)。コンフリクトなしとは扱わない"]
    if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        return ["コンフリクトがある"]
    return []


# --- レビュー・本人確認 ------------------------------------------------------------
TRUSTED_ASSOC = {"OWNER", "MEMBER", "COLLABORATOR"}


def usable_comments(comments: list[dict], bc: BaseConfig) -> tuple[list[dict], list[str], set[str]]:
    """使ってよいコメント: 編集されていない、かつリポジトリの所有者・メンバー・共同作業者のもの(公開リポジトリの
    第三者の目印は使わない)。AGENT_REVIEWER_LOGINS(ベース)があれば、レビューはさらにその投稿者だけ(from_reviewer)"""
    allowed = {x.strip().lower() for x in bc.get("AGENT_REVIEWER_LOGINS").split(",") if x.strip()}
    out, notes = [], []
    for c in comments:
        has_mark = "<!-- agent-" in c.get("body", "")
        if c.get("updated_at") and c.get("created_at") and c["updated_at"] != c["created_at"]:
            if has_mark:
                notes.append("編集されたコメントの記録は使わない")
            continue
        if c.get("author_association") not in TRUSTED_ASSOC:
            if has_mark:
                notes.append("所有者・メンバー・共同作業者以外のコメントの記録は使わない")
            continue
        out.append(c)
    return out, sorted(set(notes)), allowed


def from_reviewer(c: dict, allowed: set[str]) -> bool:
    if not allowed:
        return True
    login = ((c.get("user") or {}).get("login") or "").lower()
    app = ((c.get("performed_via_github_app") or {}).get("slug") or "").lower()
    return login in allowed or bool(app and f"app:{app}" in allowed)


def valid_review(m: dict) -> bool:
    """目印の形を確かめる(壊れた目印で判定が止まらないように)"""
    str_ok = all(isinstance(m.get(k, ""), str) for k in ("reviewed_sha", "model", "model_status", "independence", "verdict",
                                                          "reviewed_base_ref", "reviewed_merge_base"))
    unv = m.get("unverified", [])
    return str_ok and isinstance(unv, list) and all(isinstance(x, str) for x in unv)


def valid_confirm(m: dict) -> bool:
    items = m.get("items")
    return (isinstance(items, list) and items and all(x in ("model", "session") for x in items)
            and all(isinstance(m.get(k), str) for k in ("sha", "principal", "sig")))


def human_confirms(repo: str, number: int, comments: list[dict], sha: str, bc: BaseConfig) -> tuple[set[str], list[str]]:
    """署名が確かめられた本人確認の項目。ベースブランチに allowed-signers が無ければ使わない。"""
    signers = bc.file(".agent-kit/human-confirm-allowed-signers")
    items: set[str] = set()
    notes = []
    allm = [m for c in comments for m in issues.marks(c.get("body", ""), "human-confirm")]
    if any(not valid_confirm(m) for m in allm):
        notes.append("形の正しくない本人確認の記録は使わない")
    marks = [m for m in allm if valid_confirm(m) and m["sha"] == sha]
    if marks and not signers:
        notes.append("本人確認の記録があるが、ベースブランチに human-confirm-allowed-signers が無いので使わない")
        return items, notes
    for m in marks:
        data = humanconfirm.payload(repo, number, sha, m.get("items") or [])
        if humanconfirm.verify(signers or "", m.get("principal", ""), data, m.get("sig", "")):
            items |= set(m.get("items") or [])
        else:
            notes.append("署名を確かめられない本人確認の記録は使わない")
    return items, sorted(set(notes))


def review_section(repo: str, number: int, comments: list[dict], sha: str, bc: BaseConfig,
                   anchor: dict | None = None) -> tuple[list[str], list[str]]:
    want = bc.get("AGENT_REVIEW_MODEL")
    usable, notes, allowed = usable_comments(comments, bc)
    reviews = []
    for c in usable:
        if not from_reviewer(c, allowed):
            continue
        for m in issues.marks(c.get("body", ""), "review"):
            if not valid_review(m):
                notes.append("形の正しくないレビューの記録は使わない")
                continue
            m["_at"] = c.get("created_at", "")
            reviews.append(m)
    latest = [r for r in reviews if r.get("reviewed_sha") == sha]
    if not latest:
        older = len(reviews)
        return ["最新コミットのレビューが無い" + (f"(古いコミットのレビュー {older} 件は使わない)" if older else "")], notes
    rv = sorted(latest, key=lambda r: r["_at"])[-1]
    reasons = []
    if anchor is not None:
        # レビューした差分(マージ先・merge-base)が、今のマージ先の差分と同じか
        if not rv.get("reviewed_merge_base"):
            reasons.append("レビューの記録に、レビューした差分の基点(merge-base)が無い(古い版のレビュー)。レビューし直す")
        elif rv.get("reviewed_base_ref") != anchor["base_ref"] or rv.get("reviewed_merge_base") != anchor["merge_base"]:
            reasons.append(f"レビューした差分({rv.get('reviewed_base_ref')}@{rv.get('reviewed_merge_base', '')[:12]})と、"
                           f"今の差分({anchor['base_ref']}@{anchor['merge_base'][:12]})が違う。レビューし直す")
    human, hnotes = human_confirms(repo, number, usable, sha, bc)
    notes += hnotes
    if rv.get("model_status") == "confirmed":
        if rv.get("model") != want:
            reasons.append(f"レビューのモデルが {rv.get('model')}(指定 {want})")
    elif "model" not in human:
        reasons.append(f"レビューのモデルが未確認({rv.get('model_status', '記録なし')}: {rv.get('model_detail', '')})。指定 {want}")
    if rv.get("independence") != "confirmed" and "session" not in human:
        reasons.append(f"実装と別のセッションか未確認({rv.get('independence', '記録なし')}: {rv.get('independence_detail', '')})")
    if rv.get("verdict") != "pass":
        reasons.append(f"レビュー判定が {rv.get('verdict')}")
    try:
        blocking = int(rv.get("blocking", 0) or 0)
    except (TypeError, ValueError):
        blocking = 1
    if blocking < 0:
        blocking = 1
    if blocking > 0:
        reasons.append(f"未解決の重大な指摘 {rv.get('blocking')} 件")
    if rv.get("unverified"):
        reasons.append("必要な未検証事項: " + ", ".join(rv["unverified"]))
    return reasons, notes


def touches_gate_files(repo: str, number: int) -> list[str]:
    try:
        files = api("GET", f"repos/{repo}/pulls/{number}/files?per_page=100", paginate=True) or []
    except ApiError:
        return []
    return [f["filename"] for f in files if f.get("filename") in GATE_FILES]


# --- まとめ ------------------------------------------------------------------------
GATE_ATTEMPTS = 3


def merge_gate(repo: str, number: int) -> dict:
    """判定の始めにマージ先・その先頭・head を固定し、最後に取り直す。どれかが変わっていたら最初から判定し直す
    (GATE_ATTEMPTS 回まで。それでも動くなら合格にしない)"""
    last = None
    for attempt in range(GATE_ATTEMPTS):
        res, snap = _merge_gate_once(repo, number)
        pr2 = api("GET", f"repos/{repo}/pulls/{number}")
        try:
            after = diff_anchor(repo, pr2["base"]["ref"], pr2["head"]["sha"])
        except ApiError as e:
            res["reasons"].append(f"判定の後でマージ先を読めない({e})")
            res["mergeable"] = False
            return res
        if after == snap:
            return res
        last = (snap, after)
        res["notes"].append(f"判定中にマージ先・その先頭・head が変わった({snap['base_ref']}@{snap['base_tip'][:12]}/{snap['head'][:12]}"
                            f" → {after['base_ref']}@{after['base_tip'][:12]}/{after['head'][:12]})。判定し直す")
    res["reasons"].append(f"判定中にマージ先・その先頭・head が変わり続けた({GATE_ATTEMPTS} 回)。落ち着いてからもう一度判定する")
    res["mergeable"] = False
    return res


def _merge_gate_once(repo: str, number: int) -> tuple[dict, dict]:
    pr = api("GET", f"repos/{repo}/pulls/{number}")
    sha = pr["head"]["sha"]
    base = pr["base"]["ref"]
    reasons, notes = [], []
    try:
        anchor = diff_anchor(repo, base, sha)
    except ApiError as e:
        anchor = {"base_ref": base, "base_tip": "", "merge_base": "", "head": sha}
        reasons.append(f"マージ先 {base} の先頭・merge-base を読めない({e})")
    bc = BaseConfig(repo, base, anchor["base_tip"] or None)      # 条件はその時点のベースの SHA で読む
    if pr.get("state") != "open":
        reasons.append(f"PR が open でない({pr.get('state')})")
    if pr.get("draft") or "agent:wip" in issues.label_names(pr):
        reasons.append("draft / WIP のまま(実装担当が ready にしていない)")
    comments = api("GET", f"repos/{repo}/issues/{number}/comments?per_page=100", paginate=True) or []
    r, n = review_section(repo, number, comments, sha, bc, anchor)
    reasons += r
    notes += n
    r, n = ci_section(repo, sha, base, bc)
    reasons += r
    notes += n
    reasons += bc.problems
    for dr, dn in issues.dep_refs(repo, pr.get("body", "")):
        ok, msg, _ = issues.dep_status(dr, dn)
        if not ok:
            reasons.append(f"依存が未完了: {msg}")
    reasons += mergeable_section(repo, number, pr)
    default = api("GET", f"repos/{repo}").get("default_branch")
    if default and base != default:
        notes.append(f"PR のマージ先は {base}(既定のブランチ {default} ではない)。判定の設定も {base} のものを使った")
    changed = touches_gate_files(repo, number)
    if changed:
        notes.append(f"この PR は判定の設定 {', '.join(changed)} を変えている。判定はベースブランチ {base} の設定で行った")
    return ({"pr": number, "head_sha": sha, "base_ref": base, "base_sha": anchor["base_tip"], "merge_base": anchor["merge_base"],
             "mergeable": not reasons, "reasons": reasons, "notes": notes, "merge_performed": False}, anchor)


def cmd_merge_gate(a, repo: str) -> int:
    res = merge_gate(repo, a.number)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(("マージ可(技術判定)" if res["mergeable"] else "マージ不可") + f": PR #{res['pr']} @ {res['head_sha'][:12]}")
        for r in res["reasons"]:
            print(f"  - {r}")
        for n in res["notes"]:
            print(f"  注: {n}")
        print("  ※ このツールはマージしない。正式な承認とマージは権限を持つ人が行う。")
    return 0 if res["mergeable"] else 1


def cmd_ci_policy(a, repo: str) -> int:
    """必須 CI の現状(GitHub 側の必須とベースブランチの ci-policy.json)を表示する"""
    base = a.base or api("GET", f"repos/{repo}")["default_branch"]
    names, problems = platform_required(repo, base)
    bc = BaseConfig(repo, base)
    print(json.dumps({"base": base, "platform_required": names, "problems": problems + bc.problems,
                      "policy_file_on_base": bc.policy() or None}, ensure_ascii=False, indent=2))
    return 0
