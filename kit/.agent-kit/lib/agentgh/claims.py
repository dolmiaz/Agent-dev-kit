"""Issue 単位の担当確保。

識別子は「リポジトリ + Issue 番号」で固定する: ref `refs/agent-claims/issue-<番号>`(AGENT_CLAIM_REF_PREFIX で変更可)。
実装ブランチ(feature/<番号>-<slug>)とは別物なので、slug を変えても同じ Issue を二重に確保できない。

ref はブランチではないので push を契機にした CI は動かない。ref の指す commit の本文に状態(JSON)を持たせる:
  {"state": "claimed"|"released", "agent", "session", "branch", "base", "at", ...}
状態の変更は「今の commit を親にした新しい commit」へ ref を fast-forward で更新する(force しない)。
同時に2つが更新すると、後の方は fast-forward にならず GitHub が 422 を返す = 比較して交換する操作になる。
最初の確保は ref の作成(既にあれば 422)で、これも1つしか成功しない。
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from . import issues
from .api import ApiError, api, cfg, repo_root, try_api, warn_on_error

STATE_RE_PREFIX = "agent-claim"


def ref_name(n: int) -> str:
    prefix = cfg("AGENT_CLAIM_REF_PREFIX", "refs/agent-claims").rstrip("/")
    return f"{prefix}/issue-{n}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def read_claim(repo: str, n: int) -> dict | None:
    """確保の状態。ref が無ければ None。"""
    ref = ref_name(n)
    obj = try_api("GET", f"repos/{repo}/git/ref/{ref[len('refs/'):]}")
    if not obj:
        return None
    sha = obj["object"]["sha"]
    commit = api("GET", f"repos/{repo}/git/commits/{sha}")
    data = {}
    msg = commit.get("message", "")
    if "\n\n" in msg:
        try:
            data = json.loads(msg.split("\n\n", 1)[1])
        except json.JSONDecodeError:
            data = {}
    data["_sha"] = sha
    data.setdefault("state", "unknown")
    return data


def _commit(repo: str, tree: str, parents: list[str], data: dict, n: int) -> str:
    msg = f"{STATE_RE_PREFIX}: {data['state']} #{n}\n\n{json.dumps(data, ensure_ascii=False)}"
    return api("POST", f"repos/{repo}/git/commits", {"message": msg, "tree": tree, "parents": parents})["sha"]


def _base_tree(repo: str, base_branch: str) -> tuple[str, str]:
    head = api("GET", f"repos/{repo}/git/ref/heads/{base_branch}")["object"]["sha"]
    return head, api("GET", f"repos/{repo}/git/commits/{head}")["tree"]["sha"]


def write_state(repo: str, n: int, current: dict | None, data: dict, tree: str) -> bool:
    """current(read_claim の結果)から data へ交換する。他が先に変えていれば False。"""
    ref = ref_name(n)
    try:
        if current is None:
            sha = _commit(repo, tree, [], data, n)
            api("POST", f"repos/{repo}/git/refs", {"ref": ref, "sha": sha})
        else:
            sha = _commit(repo, tree, [current["_sha"]], data, n)
            api("PATCH", f"repos/{repo}/git/{ref}", {"sha": sha, "force": False})
        return True
    except ApiError as e:
        if e.status not in (409, 422):
            raise
        # 他が先に変えたのか、別の理由(ref の名前が使えない等)で断られたのかを、読み直して確かめる
        after = read_claim(repo, n)
        if current is not None and after is None:
            return False          # 他が ref を消した: 競合として扱う(状態を確かめ直す)
        if after is None or (current is not None and after.get("_sha") == current.get("_sha")):
            raise ApiError(e.status, f"確保の ref を書けない(他の確保ではない): {e}。"
                                     "ブランチ以外の ref が使えないなら AGENT_CLAIM_REF_PREFIX=refs/heads/agent-claims")
        return False


def conflicts(repo: str, n: int) -> list[str]:
    """確保の記録とは別に、この Issue の作業が既にあるか(既存 PR・feature/<n>- ブランチ)"""
    found = []
    for pr in api("GET", f"repos/{repo}/pulls?state=open&per_page=100", paginate=True) or []:
        ref = (pr.get("head") or {}).get("ref", "")
        if ref.startswith(f"feature/{n}-") or issues.closes_issue(pr.get("body", ""), n):
            found.append(f"PR #{pr['number']}({ref})")
    for r in api("GET", f"repos/{repo}/git/matching-refs/heads/feature/{n}-", paginate=True) or []:
        name = r["ref"].replace("refs/heads/", "")
        if not any(name in f for f in found):
            found.append(f"ブランチ {name}")
    return found


def is_stale(repo: str, claim: dict, hours: float | None = None) -> tuple[bool, str]:
    """確保・ブランチの最終コミット・PR の更新が、どれも hours(既定 AGENT_CLAIM_STALE_HOURS)より古いか"""
    if hours is None:      # 確保した側が記録した値を使う(引き継ぐ側の環境変数で短くできない)
        try:
            hours = float(claim.get("stale_hours"))
        except (TypeError, ValueError):
            hours = float(cfg("AGENT_CLAIM_STALE_HOURS", "24"))
    limit = _now() - dt.timedelta(hours=hours)
    times = [claim.get("at", "")]
    br = claim.get("branch")
    if br:
        c = try_api("GET", f"repos/{repo}/commits/{br}")
        if c:
            times.append(((c.get("commit") or {}).get("committer") or {}).get("date", ""))
        for pr in api("GET", f"repos/{repo}/pulls?state=open&head={repo.split('/')[0]}:{br}", paginate=True) or []:
            times.append(pr.get("updated_at", ""))
    latest = max((dt.datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times if t), default=None)
    if latest is None:
        return True, "時刻の記録が無い"
    return latest < limit, f"最後の動き {latest.isoformat(timespec='minutes')}(基準 {hours * 60:g} 分)"


def ensure_branch(repo: str, branch: str, base_sha: str, reuse_ok: bool) -> str:
    """実装ブランチを作る。既にあれば reuse_ok のときだけ使う。"""
    try:
        api("POST", f"repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})
        return "created"
    except ApiError as e:
        if e.status in (409, 422) and reuse_ok:
            return "reused"
        raise


HELD = ("claimed", "handoff")
IGNORED_FOR_RESUME = ("agent:ready が無い", "担当確保済み")


def session_keys(*values: str) -> set[str]:
    from .review import norm_session
    return {norm_session(v) for v in values if v} - {""}


def _lease_minutes(claim: dict) -> float:
    """リースの長さ。確保した側が確保の記録に書いた値を使う(再開する側の環境変数で短くできない)"""
    try:
        v = float(claim.get("lease_minutes"))
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return max(float(cfg("AGENT_CLAIM_LEASE_MINUTES", "60")), 0.01)


def _check_resume(repo: str, a, current: dict, ev: dict, evidence: dict) -> int:
    """本人(同じ agent 名)の再開。0 なら続けてよい。
    - 同じセッション、または元の担当が明示的に引き継ぎ(release --handoff)した → 再開できる
    - 別のセッション → 確保・ブランチ・PR に AGENT_CLAIM_LEASE_MINUTES(既定 60 分)動きが無いときだけ(稼働中の横取りを防ぐ)
    - どの場合も、blocked と依存(前提 PR のマージ・Issue の完了)を確かめ直す"""
    rest = [r for r in ev["reasons"] if not r.startswith(IGNORED_FOR_RESUME)]
    if rest and not a.force:
        print(f"再開できない: #{a.number}: {'; '.join(rest)}")
        return 2
    mine_keys = session_keys(evidence.get("hook_session_id", ""), evidence.get("remote_session_id", ""))
    holder_keys = session_keys(current.get("hook_session_id", ""), current.get("remote_session_id", ""))
    if current.get("state") == "handoff" or (mine_keys and mine_keys & holder_keys):
        return 0
    lease = _lease_minutes(current) / 60
    stale, why = is_stale(repo, current, hours=lease)
    if not stale:
        print(f"再開できない: #{a.number} は別のセッションで稼働中の可能性がある({why})。元のセッションが止まっているなら"
              f"しばらく待つか、元のセッションで release --handoff してから再開する")
        return 3
    print(f"再開: 元のセッションの動きが止まっている({why})", file=sys.stderr)
    return 0


def _remember(repo: str, n: int, evidence: dict, held: bool, lease_minutes=None) -> None:
    """このセッションが確保中の Issue と、その確保のリースを手元に記録する(hook がリースを更新するのに使う)。失敗しても止めない"""
    try:
        import agentstate
        if evidence.get("hook_session_id"):
            agentstate.set_my_claim(repo_root(), evidence["hook_session_id"], repo, n, held, lease_minutes)
    except Exception:
        pass


def cmd_renew(a, repo: str, evidence: dict) -> int:
    """確保のリースを更新する(確保の時刻 at を今にする)。確保したのと同じセッションからだけ。
    --mine は、このセッションが確保中と手元に記録した Issue すべて(hook が裏で定期的に呼ぶ)"""
    mine_keys = session_keys(evidence.get("hook_session_id", ""), evidence.get("remote_session_id", ""))
    if not mine_keys:
        print("更新しない: このコマンドを出したセッションが分からない(hook の証拠が無い)")
        return 2
    if a.mine:
        import agentstate
        targets = [x["issue"] for x in agentstate.my_claims(repo_root(), evidence.get("hook_session_id", ""))
                   if x["repo"] == repo]
    elif a.number is not None:
        targets = [a.number]
    else:
        print("番号か --mine を指定する")
        return 2
    rc = renewed = 0
    for n in targets:
        current = read_claim(repo, n)
        holder = session_keys(current.get("hook_session_id", ""), current.get("remote_session_id", "")) if current else set()
        if not current or current.get("state") != "claimed" or not (mine_keys & holder):
            print(f"#{n}: 更新しない(このセッションの確保ではない: {current.get('state') if current else 'なし'})")
            _remember(repo, n, evidence, False)
            rc = max(rc, 3)
            continue
        tree = api("GET", f"repos/{repo}/git/commits/{current['_sha']}")["tree"]["sha"]
        data = {k: v for k, v in current.items() if not k.startswith("_")}
        data.update(at=_now().isoformat(timespec="seconds"), renewed=True)
        if not write_state(repo, n, current, data, tree):
            print(f"#{n}: 更新しない(直前に状態が変わった。claim-status で確かめる)")
            rc = max(rc, 3)
            continue
        _remember(repo, n, evidence, True, _lease_minutes(data))     # 手元の記録のリースを、確保の記録の値にそろえる
        renewed += 1
        print(f"#{n}: リースを更新した({data['at']})")
    if renewed and rc == 0:
        try:
            import agentstate
            st = agentstate.renew_stamp(repo_root(), evidence.get("hook_session_id", ""), repo)
            if st:
                st.touch()          # 全部の更新に成功したときだけ、次の更新までの間隔を数え直す
        except Exception:
            pass
    return rc


def _check_held(repo: str, a, current: dict, mine: bool, ev: dict, evidence: dict) -> int:
    """確保中の Issue を、再開(本人)か引き継ぎ(止まっているとき)以外では取らない。0 なら続けてよい。"""
    if mine and a.resume:
        return _check_resume(repo, a, current, ev, evidence)
    if a.takeover:
        rest = [r for r in ev["reasons"] if not r.startswith(IGNORED_FOR_RESUME)]
        if rest and not a.force:              # 引き継ぐときも blocked と依存を確かめ直す
            print(f"引き継げない: #{a.number}: {'; '.join(rest)}")
            return 2
    if not a.takeover:
        print(f"確保できない: #{a.number} は {current.get('agent')} が確保中(ブランチ {current.get('branch')}、"
              f"{current.get('at')})。本人なら --resume、止まっているなら claim-status で確かめて --takeover")
        return 3
    stale, why = is_stale(repo, current)
    if not stale:
        print(f"引き継げない: まだ動きがある({why})")
        return 3
    print(f"引き継ぐ: {current.get('agent')} の確保は止まっている({why})", file=sys.stderr)
    return 0


def cmd_claim(a, repo: str, evidence: dict) -> int:
    it = api("GET", f"repos/{repo}/issues/{a.number}")
    current = read_claim(repo, a.number)
    ev = issues.evaluate_issue(repo, it, current)
    held = bool(current and current.get("state") in HELD)
    mine = held and current.get("agent") == a.agent
    if held:
        rc = _check_held(repo, a, current, mine, ev, evidence)
        if rc:
            return rc
    else:
        if not ev["eligible"] and not a.force:
            print(f"着手できない: #{a.number}: {'; '.join(ev['reasons'])}")
            return 2
        found = [] if a.adopt else conflicts(repo, a.number)
        if found:
            print(f"確保しない: この Issue の作業が既にある: {', '.join(found)}。"
                  f"続きを引き取るなら --adopt <ブランチ名>")
            return 3
    base_branch = ev["stack_on"] or api("GET", f"repos/{repo}")["default_branch"]
    base_sha, tree = _base_tree(repo, base_branch)
    if mine and a.resume:
        branch = current.get("branch")
    else:
        branch = a.adopt or (current.get("branch") if (held and a.takeover) else
                             f"feature/{a.number}-{a.slug or issues.slugify(it['title'])}")
    data = {"state": "claimed", "repo": repo, "issue": a.number, "agent": a.agent, "session": a.session or "",
            "hook_session_id": evidence.get("hook_session_id", ""),
            "remote_session_id": evidence.get("remote_session_id", ""),
            "branch": branch, "base": base_branch, "at": _now().isoformat(timespec="seconds"),
            "lease_minutes": max(float(cfg("AGENT_CLAIM_LEASE_MINUTES", "60")), 0.01),
            "stale_hours": max(float(cfg("AGENT_CLAIM_STALE_HOURS", "24")), 0.0001),
            "via": "resume" if (mine and a.resume) else ("takeover" if a.takeover and held else
                                                        ("adopt" if a.adopt else "new"))}
    if not write_state(repo, a.number, current, data, tree):
        print(f"確保できない: #{a.number} は直前に他のエージェントが確保した")
        return 3
    try:
        how = ensure_branch(repo, branch, base_sha, reuse_ok=bool(a.adopt or a.resume or a.takeover))
    except ApiError as e:
        # ブランチを作れなかった。確保は残っているので、原因を直して --resume で再開できる
        print(f"確保はしたが、ブランチ {branch} を作れなかった({e})。原因を直して claim --resume で再開する", file=sys.stderr)
        return 5
    _remember(repo, a.number, evidence, True, data["lease_minutes"])
    warn_on_error(lambda: api("POST", f"repos/{repo}/issues/{a.number}/labels", {"labels": ["agent:claimed"]}))
    warn_on_error(lambda: api("DELETE", f"repos/{repo}/issues/{a.number}/labels/agent:ready"))
    warn_on_error(lambda: api("POST", f"repos/{repo}/issues/{a.number}/comments", {
        "body": f"{a.agent} が着手します({data['via']})。ブランチ `{branch}`({how}、基点 `{base_branch}`)。\n\n"
                + issues.mark("claim", {k: v for k, v in data.items() if not k.startswith("_")})}))
    print(branch)
    return 0


def cmd_release(a, repo: str, evidence: dict | None = None) -> int:
    current = read_claim(repo, a.number)
    if not current or current.get("state") not in HELD:
        print(f"#{a.number} は確保されていない")
        return 0
    if current.get("agent") != a.agent and not a.force:
        print(f"外せない: 確保しているのは {current.get('agent')}(本人以外が外すときは --force)")
        return 3
    # 外す・渡すのは、確保したセッションだけ(同じ agent 名の別のセッションが、外してから取り直す横取りを防ぐ)
    mine_keys = session_keys((evidence or {}).get("hook_session_id", ""), (evidence or {}).get("remote_session_id", ""))
    holder = session_keys(current.get("hook_session_id", ""), current.get("remote_session_id", ""))
    if mine_keys and holder and not (mine_keys & holder):
        stale, why = is_stale(repo, current)
        if not stale:
            print(f"外せない: #{a.number} は別のセッションが確保中で、まだ動きがある({why})。--force でも外さない。"
                  "元のセッションで release するか、止まっているなら claim-status で確かめてから --takeover")
            return 3
    elif not holder and current.get("agent") != a.agent:
        # 確保がセッションの証拠なしで作られた: 同じ agent 名なら外せる。別の agent は --force でも止まるまで外さない
        stale, why = is_stale(repo, current)
        if not stale:
            print(f"外せない: 確保したセッションが記録されておらず、まだ動きがある({why})。--force でも外さない")
            return 3
    elif not mine_keys and holder:
        # 出したセッションが分からない(hook を通らない・証拠を消した)実行では、--force でも稼働中の確保は外さない
        stale, why = is_stale(repo, current)
        if not stale:
            print(f"外せない: このコマンドを出したセッションが分からない(hook の証拠が無い)うえ、確保にまだ動きがある({why})。"
                  "--force でも外さない。確保したセッションから release するか、止まってから claim-status で確かめる")
            return 3
    head = api("GET", f"repos/{repo}/git/commits/{current['_sha']}")["tree"]["sha"]
    handoff = getattr(a, "handoff", False)
    data = {"state": "handoff" if handoff else "released", "issue": a.number, "agent": current.get("agent") if handoff else a.agent,
            "previous": current.get("agent"), "branch": current.get("branch"), "base": current.get("base"),
            # 確保した側の期限の設定を引き継ぐ(受け取る側・引き継ぐ側の環境変数で短くさせない)
            "lease_minutes": current.get("lease_minutes"), "stale_hours": current.get("stale_hours"),
            # 渡す側のセッションも残す(handoff 中も、そのセッション以外は止まるまで外せない)
            "hook_session_id": current.get("hook_session_id", ""), "remote_session_id": current.get("remote_session_id", ""),
            "at": _now().isoformat(timespec="seconds")}
    if not write_state(repo, a.number, current, data, head):
        print("外せない: 直前に状態が変わった。claim-status で確かめる")
        return 3
    _remember(repo, a.number, evidence or {}, False)       # このセッションはもうリースを更新しない
    if handoff:
        print(f"handoff: 同じ agent 名({data['agent']})の別のセッションが --resume で続けられる(他のエージェントには渡さない)")
        return 0
    warn_on_error(lambda: api("DELETE", f"repos/{repo}/issues/{a.number}/labels/agent:claimed"))
    warn_on_error(lambda: api("POST", f"repos/{repo}/issues/{a.number}/labels", {"labels": ["agent:ready"]}))
    warn_on_error(lambda: api("POST", f"repos/{repo}/issues/{a.number}/comments", {
        "body": f"{a.agent} が担当を外しました(ブランチ `{current.get('branch')}` は残しています)。\n\n"
                + issues.mark("claim-release", data)}))
    print("released")
    return 0


def cmd_status(a, repo: str) -> int:
    c = read_claim(repo, a.number)
    if not c:
        found = conflicts(repo, a.number)
        print(f"#{a.number}: 確保なし" + (f"(ただし既存の作業: {', '.join(found)})" if found else ""))
        return 0
    stale, why = is_stale(repo, c) if c.get("state") in HELD else (False, "")
    print(json.dumps({k: v for k, v in c.items() if not k.startswith("_")} | {"stale": stale, "stale_detail": why},
                     ensure_ascii=False, indent=2))
    return 0
