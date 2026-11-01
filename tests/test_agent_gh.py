#!/usr/bin/env python3
"""agent-gh の回帰テスト(偽の gh・偽の transcript・本物の hook)。

各テストの結果を `PASS 名前` / `FAIL 名前: 理由` で1行ずつ出す(run-tests.sh が数える)。
  python3 tests/test_agent_gh.py <作業ディレクトリ>
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
W = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("/tmp/agentgh-test")
W.mkdir(parents=True, exist_ok=True)
R = W / "repo"
STATE = W / "gh.json"
RESULTS: list[tuple[bool, str, str]] = []
CODEX_EMAIL = "codex-cloud@example.com"

BASE_ENV = {k: v for k, v in os.environ.items()
            if k not in ("CLAUDE_CODE_REMOTE", "CLAUDE_CODE_REMOTE_SESSION_ID", "AGENT_RUNTIME", "CLAUDE_PROJECT_DIR")}
BASE_ENV.update(GH_BIN=str(KIT / "tests" / "fake_gh.py"), FAKE_GH_STATE=str(STATE),
                AGENT_STATE_DIR=str(W / "state"), AGENT_MERGEABLE_RETRY="1", AGENT_MERGEABLE_WAIT="0",
                AGENT_GIT_EMAIL_CODEX=CODEX_EMAIL, AGENT_CLAIM_RENEW="0")   # リースの裏の更新は専用の試験でだけ動かす
BASE_CONFIG = f'AGENT_REVIEW_MODEL=claude-opus-5-5\nAGENT_GIT_EMAIL_CODEX="{CODEX_EMAIL}"\n'   # ベースブランチの config.env


# --- 準備 ----------------------------------------------------------------------
def sh(*cmd, cwd=None, env=None, inp=None):
    return subprocess.run(cmd, cwd=cwd, env=env or BASE_ENV, input=inp, capture_output=True, text=True)


def setup_repo():
    if R.exists():
        return
    sh("git", "init", "-q", "--bare", str(W / "origin.git"))
    sh("git", "init", "-q", "-b", "main", str(R))
    sh("git", "-C", str(R), "remote", "add", "origin", str(W / "origin.git"))
    sh("git", "-C", str(R), "config", "user.name", "Test Agent")
    sh("git", "-C", str(R), "config", "user.email", "test@example.invalid")
    sh("git", "-C", str(R), "config", "commit.gpgsign", "false")
    (R / "README.md").write_text("# demo\n")
    sh("git", "-C", str(R), "add", "-A"); sh("git", "-C", str(R), "commit", "-qm", "init")
    r = sh(sys.executable, str(KIT / "install.py"), str(R), "--only", "claude")
    assert r.returncode == 0, r.stderr + r.stdout
    os.chmod(KIT / "tests" / "fake_gh.py", 0o755)


def commit(msg: str) -> str:
    with open(R / "work.txt", "a") as f:
        f.write(msg + "\n")
    sh("git", "-C", str(R), "add", "work.txt"); sh("git", "-C", str(R), "commit", "-qm", msg)
    return sh("git", "-C", str(R), "rev-parse", "HEAD").stdout.strip()


ISSUE_BODY = "### 問題\nx\n\n### 期待する動作\ny\n\n### 受入条件/確認方法\n- [ ] z\n\n### 依存\n{dep}\n"


def L(*n):
    return [{"name": x} for x in n]


def reset_state(**extra):
    s = {"default_branch": "main", "refs": {"main": "sha-main", "feature/9-base": "sha-50"},
         "issues": {
             "1": {"number": 1, "title": "Add login", "state": "open", "labels": L("agent:ready"), "body": ISSUE_BODY.format(dep="なし")},
             "2": {"number": 2, "title": "Needs 1", "state": "open", "labels": L("agent:ready"), "body": ISSUE_BODY.format(dep="#1")},
             "3": {"number": 3, "title": "Stack on PR", "state": "open", "labels": L("agent:ready", "agent:stack-ok"), "body": ISSUE_BODY.format(dep="#50")},
             "4": {"number": 4, "title": "No acceptance", "state": "open", "labels": L("agent:ready"), "body": "### 問題\nx\n"},
             "7": {"number": 7, "title": "Has PR", "state": "open", "labels": L("agent:ready"), "body": ISSUE_BODY.format(dep="なし")}},
         "pulls": {"50": {"number": 50, "state": "open", "merged": False, "labels": [], "draft": False, "body": "",
                          "head": {"ref": "feature/9-base", "sha": "sha-50"}, "base": {"ref": "main"}},
                   "60": {"number": 60, "state": "open", "merged": False, "labels": [], "draft": False, "body": "Closes #7",
                          "head": {"ref": "feature/7-older", "sha": "sha-60"}, "base": {"ref": "main"}}},
         "comments": {}, "contents": {"main": {".agent-kit/config.env": BASE_CONFIG}}}
    s["refs"]["feature/7-older"] = "sha-60"
    s.update(extra)
    STATE.write_text(json.dumps(s))
    Path(str(STATE) + ".log").write_text("")


def st() -> dict:
    return json.loads(STATE.read_text())


def patch(fn):
    s = st(); fn(s); STATE.write_text(json.dumps(s))


class Session:
    """Claude Code のセッションの代わり: hook(pretool.py)に証拠を書かせてからコマンドを実行する"""

    def __init__(self, sid: str, remote_id: str = "", hooks: bool = True):
        # 本物と同じ形の ID にする(hook の session_id は UUID、クラウドの ID は cse_<英数字>)
        import hashlib, uuid
        self.name = sid
        self.sid = str(uuid.uuid5(uuid.NAMESPACE_URL, "session:" + sid))
        if remote_id and not re.match(r"cse_[A-Za-z0-9]{8,}$", remote_id):
            remote_id = "cse_" + hashlib.md5(remote_id.encode()).hexdigest()[:16]
        self.remote_id, self.hooks = remote_id, hooks
        self.transcript = W / f"transcript-{sid}.jsonl"
        self.transcript.write_text("")

    def say(self, model: str, bash: str | None = None, ts: str | None = None):
        ts = ts or dt.datetime.now(dt.timezone.utc).isoformat()
        content = [{"type": "text", "text": "..."}]
        if bash:
            content.append({"type": "tool_use", "name": "Bash", "input": {"command": bash}})
        with open(self.transcript, "a") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": ts, "sessionId": self.sid,
                                "message": {"model": model, "content": content}}) + "\n")

    def run(self, args: str, model: str | None = "claude-opus-5-5", env: dict | None = None):
        cmd = f"python3 .agent-kit/bin/agent-gh --repo o/r {args}"
        e = dict(BASE_ENV, **(env or {}))
        if self.remote_id:
            e.update(CLAUDE_CODE_REMOTE="true", CLAUDE_CODE_REMOTE_SESSION_ID=self.remote_id)
        if model:
            self.say(model, bash=cmd)
        run_cmd = self.hook(cmd, e) if self.hooks else cmd
        r = subprocess.run(run_cmd, shell=True, cwd=R, env=e, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def hook(self, cmd: str, e: dict | None = None, rewrite: bool = True) -> str:
        """本物の PreToolUse hook を通す。hook が updatedInput を返したら、Claude Code と同じくその入力で実行する"""
        hook_env = dict(e or BASE_ENV, AGENT_HOOKS_LOCAL="1", CLAUDE_PROJECT_DIR=str(R))
        hook_in = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                              "session_id": self.sid, "transcript_path": str(self.transcript)})
        out = sh(sys.executable, str(R / ".agent-kit/claude/pretool.py"), env=hook_env, inp=hook_in).stdout
        try:
            upd = json.loads(out)["hookSpecificOutput"].get("updatedInput") if out.strip() else None
        except (json.JSONDecodeError, KeyError):
            upd = None
        return upd["command"] if (upd and rewrite) else cmd


def test(name):
    def deco(fn):
        def run():
            try:
                fn()
                RESULTS.append((True, name, ""))
            except AssertionError as e:
                RESULTS.append((False, name, str(e) or traceback.format_exc(limit=1).strip().splitlines()[-1]))
            except Exception:
                RESULTS.append((False, name, traceback.format_exc(limit=2).strip().splitlines()[-1]))
        run.__name__ = fn.__name__
        TESTS.append(run)
        return run
    return deco


TESTS = []


def claim_state(n=1):
    s = st()
    sha = s["refs"].get(f"refs/agent-claims/issue-{n}")
    if not sha:
        return None
    return json.loads(s["commits"][sha]["message"].split("\n\n", 1)[1])


# --- 修正5: Issue 単位の担当確保 ------------------------------------------------
@test("claim: 別エージェント・別 slug で同時に確保しても成功は1つ")
def t_claim_concurrent():
    reset_state()
    rcs, lock = [], threading.Lock()

    def go(agent, slug):
        rc, _ = Session(f"s-{agent}").run(f"claim 1 --agent {agent} --slug {slug}", model=None)
        with lock:
            rcs.append(rc)
    ts = [threading.Thread(target=go, args=(f"a{i}", f"slug-{i}")) for i in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(rcs) == [0, 3, 3, 3, 3], rcs
    feats = [k for k in st()["refs"] if k.startswith("feature/1-")]
    assert len(feats) == 1, feats


@test("claim: 識別子は refs/agent-claims/issue-<番号>、実装ブランチとは別")
def t_claim_ref():
    reset_state()
    rc, out = Session("s1").run("claim 1 --agent claude", model=None)
    assert rc == 0, out
    c = claim_state(1)
    assert c["state"] == "claimed" and c["branch"] == "feature/1-add-login" and c["hook_session_id"] == Session("s1").sid, c
    rc, out = Session("s2").run("claim 1 --agent dot --slug other-name", model=None)
    assert rc == 3, out
    assert "feature/1-other-name" not in st()["refs"]


@test("claim: 本人の --resume は同じブランチで再開、他人の --resume は拒否")
def t_claim_resume():
    reset_state()
    Session("s1").run("claim 1 --agent claude", model=None)
    rc, out = Session("s1").run("claim 1 --agent claude --resume", model=None)
    assert rc == 0 and out.strip().endswith("feature/1-add-login"), out
    rc, _ = Session("s2").run("claim 1 --agent dot --resume", model=None)
    assert rc == 3


@test("claim: 止まっていない確保は --takeover できず、止まっていれば引き継げる")
def t_claim_takeover():
    reset_state()
    Session("s1").run("claim 1 --agent claude", model=None)
    rc, out = Session("s2").run("claim 1 --agent dot --takeover", model=None, env={"AGENT_CLAIM_STALE_HOURS": "0"})
    assert rc == 3 and "まだ動きがある" in out, "引き継ぐ側の環境変数で期限を短くできてはいけない: " + out
    reset_state()
    Session("s1").run("claim 1 --agent claude", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "0.01", "AGENT_CLAIM_STALE_HOURS": "0.0001"})
    time.sleep(1.5)
    rc, out = Session("s2").run("claim 1 --agent dot --takeover", model=None)
    assert rc == 0, out
    c = claim_state(1)
    assert c["agent"] == "dot" and c["via"] == "takeover" and c["branch"] == "feature/1-add-login", c


@test("release: 本人以外は外せない。外した後は別エージェントが確保でき、同時なら1つだけ")
def t_release_reclaim():
    reset_state()
    Session("s1").run("claim 1 --agent claude", model=None)
    rc, _ = Session("s2").run("release 1 --agent dot", model=None)
    assert rc == 3
    rc, _ = Session("s1").run("release 1 --agent claude", model=None)
    assert rc == 0 and claim_state(1)["state"] == "released"
    rcs = []
    ts = [threading.Thread(target=lambda a=a: rcs.append(Session("x" + a).run(f"claim 1 --agent {a} --adopt feature/1-add-login", model=None)[0]))
          for a in ("p", "q", "r")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(rcs) == [0, 3, 3], rcs


@test("claim: 既存 PR・ブランチがある Issue は確保しない(--adopt で引き取れる)")
def t_claim_existing_pr():
    reset_state()
    rc, out = Session("s1").run("claim 7 --agent claude", model=None)
    assert rc == 3 and "PR #60" in out, out
    rc, out = Session("s1").run("claim 7 --agent claude --adopt feature/7-older", model=None)
    assert rc == 0 and out.strip().endswith("feature/7-older"), out


@test("claim: ブランチ作成で止まっても確保は残り、--resume で再開できる")
def t_claim_branch_fail():
    reset_state(fail_branch_create=True)
    rc, out = Session("s1").run("claim 1 --agent claude", model=None)
    assert rc == 5 and "--resume" in out, out
    assert claim_state(1)["state"] == "claimed" and "feature/1-add-login" not in st()["refs"]
    rc, _ = Session("s2").run("claim 1 --agent dot", model=None)
    assert rc == 3, "止まった確保を他が黙って取らない"
    patch(lambda s: s.update(fail_branch_create=False))
    rc, out = Session("s1").run("claim 1 --agent claude --resume", model=None)
    assert rc == 0 and "feature/1-add-login" in st()["refs"], out


@test("eligible: 依存待ち・受入条件なし・確保済み(ref)を除き、stack-ok は前提 PR の上")
def t_eligible():
    reset_state()
    Session("s1").run("claim 7 --agent claude --adopt feature/7-older", model=None)
    patch(lambda s: s["issues"]["7"]["labels"].append({"name": "agent:ready"}))   # ラベルが戻っても ref で判定する
    rc, out = Session("s1").run("eligible --json", model=None)
    r = {x["number"]: x for x in json.loads(out[:out.rindex("]") + 1])}
    assert r[1]["eligible"] and not r[2]["eligible"] and not r[4]["eligible"] and not r[7]["eligible"], r
    assert r[3]["eligible"] and r[3]["stack_on"] == "feature/9-base"


@test("pr-create: draft が使えないプランでは通常 PR + agent:wip、実装セッションを記録")
def t_pr_create():
    reset_state(draft_supported=False)
    impl = Session("impl-1", remote_id="cse_impl")
    impl.run("claim 1 --agent claude", model="claude-sonnet-5-5")
    rc, out = impl.run("pr-create --issue 1 --branch feature/1-add-login --title Add", model="claude-sonnet-5-5")
    assert rc == 0 and "draft PR が使えない" in out, out
    p = st()["pulls"]["102"]
    assert p["title"].startswith("[WIP]") and {"name": "agent:wip"} in p["labels"]
    assert f'"hook_session_id": "{impl.sid}"' in p["body"] and f'"remote_session_id": "{impl.remote_id}"' in p["body"]


# --- 修正1・3: レビュー ---------------------------------------------------------
def make_pr(impl_sid="impl-1", impl_remote="cse_impl", with_marker=True, commit_emails=None):
    """実装セッションが PR を作った状態。HEAD は本物のコミット"""
    head = commit(f"work {time.time_ns()}")
    reset_state(next_pr_sha=head)
    impl = Session(impl_sid, remote_id=impl_remote)
    if with_marker:
        impl.run("claim 1 --agent claude", model="claude-sonnet-5-5")
        impl.run("pr-create --issue 1 --branch feature/1-add-login --title Add --no-draft", model="claude-sonnet-5-5")
    else:
        patch(lambda s: s["pulls"].__setitem__("102", {
            "number": 102, "state": "open", "draft": False, "title": "Add", "body": "Closes #1", "labels": [],
            "head": {"ref": "feature/1-add-login", "sha": head}, "base": {"ref": "main", "sha": "b"},
            "merged": False, "mergeable": True, "mergeable_state": "clean"}))
    emails = commit_emails or ["claude-cloud@example.com"]
    patch(lambda s: s.setdefault("pr_commits", {}).__setitem__("102", [
        {"commit": {"message": "work", "author": {"email": e}}} for e in emails]))
    return head


def green_ci(head, names=("test",)):
    patch(lambda s: (s.update(rules=[{"type": "required_status_checks", "parameters": {
        "required_status_checks": [{"context": n} for n in names]}}]),
        s.setdefault("checks", {}).__setitem__(head, [
            {"id": i, "name": n, "status": "completed", "conclusion": "success", "started_at": "2026-10-29T10:00:00Z"}
            for i, n in enumerate(names)])))


def write_body():
    p = W / "rv.md"; p.write_text("LGTM\n"); return p


def comments(n="102"):
    return st()["comments"].get(n, [])


def gate():
    rc, out = Session("gate", hooks=False).run("merge-gate 102 --json", model=None)
    return json.loads(out[out.index("{"):out.rindex("}") + 1])


def review(sess: Session, head: str, model="claude-opus-5-5", verdict="pass", blocking=0, extra=""):
    rc, out = sess.run("review-start 102", model=model)
    assert rc == 0, out
    sess.say(model)
    return sess.run(f"review-post 102 --sha {head} --verdict {verdict} --blocking {blocking} --body-file {write_body()} {extra}",
                    model=model)


@test("review-post: review-start が無ければ記録しない")
def t_review_needs_start():
    head = make_pr()
    rc, out = Session("rev-1").run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 2 and "review-start" in out, out


@test("review: 開始→追加 push→古い差分の投稿は通らない(保留・再レビュー、コメントなし)")
def t_review_moved():
    head = make_pr()
    rev = Session("rev-1", remote_id="cse_rev")
    rc, out = rev.run("review-start 102")
    assert rc == 0 and out.startswith(head), out
    patch(lambda s: s.setdefault("head_sequence", {}).__setitem__("102", ["sha-pushed-later"] * 5))
    rev.say("claude-opus-5-5")
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 6 and "保留" in out, out
    assert not any("agent-review" in c["body"] for c in comments())


@test("review-post: 固定した SHA と違う SHA・手元に無い SHA は記録しない")
def t_review_wrong_sha():
    head = make_pr()
    rev = Session("rev-1", remote_id="cse_rev")
    rev.run("review-start 102")
    rc, out = rev.run(f"review-post 102 --sha {'0' * 40} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 2, out
    patch(lambda s: s["pulls"]["102"]["head"].__setitem__("sha", "f" * 40))
    rev.run("review-start 102")
    rc, out = rev.run(f"review-post 102 --sha {'f' * 40} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 2 and "この環境に無い" in out, out


@test("review: transcript のモデルが Opus 5.5 でなければ中止(置き換えず報告)")
def t_review_wrong_model():
    head = make_pr()
    rc, out = review(Session("rev-1", remote_id="cse_rev"), head, model="claude-sonnet-5-5")
    assert rc == 4 and "本人に報告" in out, out
    assert not any("agent-review" in c["body"] for c in comments())


@test("review: レビュー途中でモデルが変わったら中止")
def t_review_model_switch():
    head = make_pr()
    rev = Session("rev-1", remote_id="cse_rev")
    rev.run("review-start 102")
    rev.say("claude-opus-5-5"); rev.say("claude-sonnet-5-5")
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}",
                      model="claude-opus-5-5")
    assert rc == 4 and "claude-sonnet-5-5" in out, out


@test("review: 自己申告と transcript が食い違えば中止")
def t_review_self_report():
    head = make_pr()
    rc, out = review(Session("rev-1", remote_id="cse_rev"), head, model="claude-sonnet-5-5",
                     extra="--model claude-opus-5-5")
    assert rc == 4, out


@test("review: hook の証拠が無い(自己申告だけ)なら未確認として記録し、merge-gate は不合格")
def t_review_no_evidence():
    head = make_pr(); green_ci(head)
    rev = Session("rev-1", hooks=False)
    rc, out = review(rev, head, extra="--model claude-opus-5-5")
    assert rc == 0 and "unverified" in out, out
    g = gate()
    assert not g["mergeable"] and any("モデルが未確認" in r for r in g["reasons"]), g


@test("review: 開始時の記録(sessions/*.json)だけではモデル確認にしない")
def t_review_session_start_only():
    head = make_pr(); green_ci(head)
    from importlib import util
    spec = util.spec_from_file_location("agentstate", R / ".agent-kit/lib/agentstate.py")
    m = util.module_from_spec(spec); os.environ["AGENT_STATE_DIR"] = BASE_ENV["AGENT_STATE_DIR"]; spec.loader.exec_module(m)
    d = m.state_dir(str(R)) / "sessions"; d.mkdir(exist_ok=True)
    (d / f"{Session('rev-old').sid}.json").write_text(json.dumps({"model": "claude-opus-5-5", "session_id": Session('rev-old').sid}))
    rev = Session("rev-old", hooks=False)
    review(rev, head)
    g = gate()
    assert any("モデルが未確認" in r for r in g["reasons"]), g


@test("review: transcript が読めなければ未確認")
def t_review_unreadable():
    head = make_pr(); green_ci(head)
    rev = Session("rev-1", remote_id="cse_rev")
    rev.run("review-start 102")
    rev.transcript.unlink()
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}", model=None)
    assert rc == 0 and "unverified" in out, out
    assert any("モデルが未確認" in r for r in gate()["reasons"])


@test("review: 実装したセッションではレビューしない")
def t_review_same_session():
    head = make_pr(impl_sid="same-1")
    rc, out = review(Session("same-1", remote_id="cse_impl"), head)
    assert rc == 4 and "実装したセッション" in out, out


@test("review: 同じセッションの cse_<id> と Claude-Session: .../session_<id> は同じとみなす")
def t_review_session_id_forms():
    head = make_pr(with_marker=False)
    patch(lambda s: s["pr_commits"]["102"][0]["commit"].__setitem__(
        "message", "work\n\nClaude-Session: https://claude.ai/code/session_01AbCdEfGhIjKlMn"))
    rc, out = review(Session("rev-x", remote_id="cse_01AbCdEfGhIjKlMn"), head)
    assert rc == 4 and "実装したセッション" in out, out


@test("review: このセッションで refspec 無しの git push をしていたら実装したセッションとみなす")
def t_review_push_no_refspec():
    head = make_pr(with_marker=False)
    rev = Session("rev-p", remote_id="cse_rev")
    rev.say("claude-opus-5-5", bash="git push -u origin HEAD")
    rc, out = review(rev, head)
    assert rc == 4 and "実装している" in out, out


def make_signer():
    key = W / "human_key"
    if not key.exists():
        sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "human", "-f", str(key))
    pub = (W / "human_key.pub").read_text().strip()
    return str(key), f"human@example.com namespaces=\"agent-kit-human-confirm\" {pub}\n"


@test("review: 実装側のセッションが分からなければ独立性は未確認 → 不合格。署名つきの本人確認で合格")
def t_review_human_confirm():
    head = make_pr(with_marker=False, commit_emails=["someone@example.com"])
    green_ci(head)
    rc, out = review(Session("rev-1", remote_id="cse_rev"), head)
    assert rc == 0, out
    g = gate()
    assert not g["mergeable"] and any("別のセッションか未確認" in r for r in g["reasons"]), g
    key, allowed = make_signer()
    hc = f"human-confirm 102 --sha {head} --items session --key {key} --principal human@example.com"
    rc, _ = Session("human", hooks=False).run(hc, model=None, env={"CLAUDE_CODE_REMOTE": "true"})
    assert rc == 2, "クラウドのセッションからは記録しない"
    rc, out = Session("human", hooks=False).run(hc, model=None)
    assert rc == 0, out
    g = gate()
    assert not g["mergeable"] and any("allowed-signers が無い" in n for n in g["notes"]), "公開鍵がベースに無ければ使わない"
    patch(lambda s: s["contents"]["main"].__setitem__(".agent-kit/human-confirm-allowed-signers", allowed))
    assert gate()["mergeable"], gate()
    # 署名の無い(エージェントが作った)本人確認の目印は使わない
    head2 = commit("again"); patch(lambda s: s["pulls"]["102"]["head"].__setitem__("sha", head2)); green_ci(head2)
    review(Session("rev-2", remote_id="cse_rev2"), head2)
    mark = json.dumps({"sha": head2, "items": ["session"], "principal": "human@example.com", "sig": "forged"})
    patch(lambda s: s["comments"]["102"].append({"id": 1, "body": f"<!-- agent-human-confirm: {mark} -->",
                                                 "created_at": "2026-10-29T12:00:00Z", "updated_at": "2026-10-29T12:00:00Z",
                                                 "user": {"login": "owner"}, "performed_via_github_app": None,
                                                 "author_association": "OWNER"}))
    g = gate()
    assert not g["mergeable"] and any("署名を確かめられない" in n for n in g["notes"]), g


@test("review: 実装が Codex の作者名のコミットだけで、レビューが Claude のセッションなら独立")
def t_review_codex_impl():
    head = make_pr(with_marker=False, commit_emails=[CODEX_EMAIL, CODEX_EMAIL])
    green_ci(head)
    rc, out = review(Session("rev-1", remote_id="cse_rev"), head)
    assert rc == 0 and "独立性 confirmed" in out, out
    g = gate()
    assert g["mergeable"] and g["merge_performed"] is False, g


# --- 修正4: 必須 CI -------------------------------------------------------------
def reviewed_pr():
    head = make_pr()
    rc, out = review(Session("rev-1", remote_id="cse_rev"), head)
    assert rc == 0 and "モデル confirmed / 独立性 confirmed" in out, out
    return head


@test("gate: 確認済みレビュー + 必須 CI 成功 → マージ可(マージはしない)")
def t_gate_ok():
    head = reviewed_pr(); green_ci(head)
    g = gate()
    assert g["mergeable"] and g["merge_performed"] is False, g
    log = Path(str(STATE) + ".log").read_text()
    assert "/merge" not in log


@test("gate: 必須 CI が決まっておらず、無関係な check だけ成功 → 不合格")
def t_gate_no_required():
    head = reviewed_pr()
    patch(lambda s: s.setdefault("checks", {}).__setitem__(head, [
        {"id": 1, "name": "label-sync", "status": "completed", "conclusion": "success"}]))
    g = gate()
    assert not g["mergeable"] and any("必須 CI が決まっていない" in r for r in g["reasons"]), g


@test("gate: 必須の test が無く lint だけ成功 → 未起動として不合格")
def t_gate_missing_required():
    head = reviewed_pr(); green_ci(head, names=("lint",))
    patch(lambda s: s["rules"][0]["parameters"]["required_status_checks"].append({"context": "test"}))
    g = gate()
    assert any("「test」の結果が無い" in r for r in g["reasons"]), g


@test("gate: 必須 CI が実行中・待機中なら不合格")
def t_gate_in_progress():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: s["checks"][head][0].update(status="queued", conclusion=None))
    assert any("実行中" in r for r in gate()["reasons"])


@test("gate: skipped は既定で不合格、ci-policy で許可した項目だけ成功扱い")
def t_gate_skipped_policy():
    head = reviewed_pr(); green_ci(head, names=("test", "e2e"))
    patch(lambda s: s["checks"][head][1].update(conclusion="skipped"))
    assert any("「e2e」が skipped" in r for r in gate()["reasons"])
    patch(lambda s: s["contents"]["main"].__setitem__(".agent-kit/ci-policy.json",
                                                      json.dumps({"required": [{"name": "e2e", "allow": ["success", "skipped"]}]})))
    assert gate()["mergeable"], gate()


@test("gate: check が多くても全ページを見る。ページ未取得なら不合格")
def t_gate_pages():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: s["checks"][head].extend(
        {"id": 1000 + i, "name": f"matrix-{i}", "status": "completed", "conclusion": "success"} for i in range(140)))
    patch(lambda s: s["checks"][head].append({"id": 5000, "name": "late-required", "status": "completed", "conclusion": "success"}))
    patch(lambda s: s["rules"][0]["parameters"]["required_status_checks"].append({"context": "late-required"}))
    assert gate()["mergeable"], "2ページ目の必須 check も見つかる"
    patch(lambda s: s.update(drop_check_pages=1))
    g = gate()
    assert any("ページ未取得" in r for r in g["reasons"]), g


@test("gate: CI の取得に失敗したら不合格")
def t_gate_fetch_fail():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: s.update(fail_paths=[r"check-runs"]))
    g = gate()
    assert not g["mergeable"] and any("取得できない" in r for r in g["reasons"]), g


@test("gate: ブランチ保護の必須 status(commit status)も照合する")
def t_gate_branch_protection():
    head = reviewed_pr()
    patch(lambda s: s.update(protection={"required_status_checks": {"contexts": ["ci/legacy"]}}))
    assert any("ci/legacy" in r for r in gate()["reasons"])
    patch(lambda s: s.setdefault("statuses", {}).__setitem__(head, [{"context": "ci/legacy", "state": "success"}]))
    assert gate()["mergeable"]


@test("gate: mergeable が不明・コンフリクトありなら不合格")
def t_gate_mergeable():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: s["pulls"]["102"].update(mergeable=None, mergeable_state="unknown"))
    assert any("mergeable が不明" in r for r in gate()["reasons"])
    patch(lambda s: s["pulls"]["102"].update(mergeable=False, mergeable_state="dirty"))
    assert any("コンフリクト" in r for r in gate()["reasons"])


@test("gate: 古いコミットのレビュー・重大な指摘・必要な未検証事項は不合格")
def t_gate_review_conditions():
    head = reviewed_pr(); green_ci(head)
    new = commit("more")
    patch(lambda s: s["pulls"]["102"]["head"].__setitem__("sha", new))
    green_ci(new)
    assert any("最新コミットのレビューが無い" in r for r in gate()["reasons"])
    rc, out = review(Session("rev-2", remote_id="cse_rev2"), new, blocking=1)
    assert any("重大な指摘" in r for r in gate()["reasons"])
    rc, out = review(Session("rev-3", remote_id="cse_rev3"), new, extra='--unverified 実機での表示')
    assert any("未検証" in r for r in gate()["reasons"])


# --- 独立レビューの指摘への回帰テスト --------------------------------------------
@test("gate: 手元(PR のブランチ)の ci-policy.json・config.env では条件を変えられない。PR が変えたら注記")
def t_gate_local_policy_ignored():
    head = reviewed_pr()
    pol, cfgf = R / ".agent-kit" / "ci-policy.json", R / ".agent-kit" / "config.env"
    orig = cfgf.read_text()
    pol.write_text(json.dumps({"use_platform_required": False, "required": []}))
    cfgf.write_text(orig + "\nAGENT_ALLOW_NO_CI=1\nAGENT_REVIEW_MODEL=claude-sonnet-5-5\n")
    patch(lambda s: s.setdefault("pr_files", {}).__setitem__("102", [{"filename": ".agent-kit/ci-policy.json"}]))
    try:
        g = gate()
        assert not g["mergeable"] and any("必須 CI が決まっていない" in r for r in g["reasons"]), g
        assert any("判定の設定" in n for n in g["notes"]), g
        patch(lambda s: s["contents"]["main"].__setitem__(".agent-kit/ci-policy.json",
                                                          json.dumps({"use_platform_required": False, "required": [{"name": "unit"}]})))
        green_ci(head)
        g = gate()
        assert any("「unit」の結果が無い" in r for r in g["reasons"]) and any("use_platform_required" in n for n in g["notes"]), g
    finally:
        pol.unlink(); cfgf.write_text(orig)


@test("gate: ベースの設定を読めない(1MB 超など)ときは不合格")
def t_gate_base_unreadable():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: s.update(fail_paths=[r"contents/\.agent-kit/ci-policy\.json"]))
    g = gate()
    assert not g["mergeable"] and any("ci-policy.json を読めない" in r for r in g["reasons"]), g


@test("gate: 編集されたコメントのレビュー記録は使わない(古いレビューの SHA を書き換えても通らない)")
def t_gate_edited_comment():
    head = reviewed_pr(); green_ci(head)
    new = commit("pushed after review")
    patch(lambda s: s["pulls"]["102"]["head"].__setitem__("sha", new)); green_ci(new)
    c = [x for x in comments() if "agent-review" in x["body"]][-1]
    sh(sys.executable, str(KIT / "tests" / "fake_gh.py"), "api", "-X", "PATCH", f"repos/o/r/issues/comments/{c['id']}",
       "--input", "-", inp=json.dumps({"body": c["body"].replace(head, new)}))
    g = gate()
    assert not g["mergeable"] and any("編集されたコメント" in n for n in g["notes"]), g


@test("gate: AGENT_REVIEWER_LOGINS(ベース)があれば、その投稿者のレビューだけを使う")
def t_gate_reviewer_logins():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: s["contents"]["main"].__setitem__(".agent-kit/config.env", BASE_CONFIG + "AGENT_REVIEWER_LOGINS=review-bot\n"))
    assert any("最新コミットのレビューが無い" in r for r in gate()["reasons"])
    patch(lambda s: [c.__setitem__("user", {"login": "review-bot"}) for c in s["comments"]["102"]])
    assert gate()["mergeable"]


@test("gate: app を指定した必須 check は commit status や別 app では満たさない")
def t_gate_app_id():
    head = reviewed_pr()
    patch(lambda s: s.update(rules=[{"type": "required_status_checks", "parameters": {
        "required_status_checks": [{"context": "test", "integration_id": 15368}]}}]))
    patch(lambda s: s.setdefault("statuses", {}).__setitem__(head, [{"context": "test", "state": "success"}]))
    patch(lambda s: s.setdefault("checks", {}).__setitem__(head, [
        {"id": 1, "name": "test", "status": "completed", "conclusion": "success", "app": {"id": 999}}]))
    assert any("app 15368" in r for r in gate()["reasons"])
    patch(lambda s: s["checks"][head].append({"id": 2, "name": "test", "status": "completed", "conclusion": "success",
                                              "app": {"id": 15368}, "check_suite": {"id": 7}}))
    assert gate()["mergeable"]


@test("gate: 必須 check の再実行が待機中なら、前の成功があっても不合格。別 suite の失敗も見る")
def t_gate_rerun():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: s["checks"][head][0].update(check_suite={"id": 1}))
    patch(lambda s: s["checks"][head].append({"id": 50, "name": "test", "status": "queued", "conclusion": None,
                                              "started_at": None, "check_suite": {"id": 1}}))
    assert any("実行中" in r for r in gate()["reasons"])
    patch(lambda s: s["checks"][head].__setitem__(1, {"id": 50, "name": "test", "status": "completed", "conclusion": "success",
                                                      "check_suite": {"id": 1}}))
    patch(lambda s: s["checks"][head].append({"id": 60, "name": "test", "status": "completed", "conclusion": "failure",
                                              "check_suite": {"id": 2}}))
    assert any("「test」が failure" in r for r in gate()["reasons"])


@test("review: 同じ SHA で review-start をやり直しても、最初の記録とセッション全体で判定する")
def t_review_restart():
    head = make_pr()
    rev = Session("rev-r", remote_id="cse_rr")
    rev.run("review-start 102", model="claude-sonnet-5-5")
    rev.say("claude-sonnet-5-5")
    rev.run("review-start 102", model="claude-opus-5-5")
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 4 and "claude-sonnet-5-5" in out, out


@test("review: 証拠つきで開始したレビューを、証拠の無い実行から投稿できない")
def t_review_post_without_evidence():
    head = make_pr()
    Session("rev-e", remote_id="cse_re").run("review-start 102")
    rc, out = Session("rev-e", hooks=False).run(
        f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}", model=None)
    assert rc == 2 and "review-start の記録が無い" in out, out


@test("review: サブエージェントの transcript に別モデルがあれば中止、サブエージェントから実行したら未確認")
def t_review_subagent():
    head = make_pr()
    rev = Session("rev-s", remote_id="cse_rs")
    sub = W / f"transcript-rev-s" / "subagents"; sub.mkdir(parents=True, exist_ok=True)
    (sub / "agent-1.jsonl").write_text(json.dumps({"type": "assistant", "isSidechain": True, "timestamp": "2026-10-29T10:00:00Z",
                                                   "message": {"model": "claude-sonnet-5-5", "content": []}}) + "\n")
    rc, out = review(rev, head)
    assert rc == 4 and "サブエージェント" in out, out
    import shutil; shutil.rmtree(W / "transcript-rev-s")
    # hook の入力に agent_id がある(サブエージェントの中から出たコマンド)
    rev2 = Session("rev-s2", remote_id="cse_rs2")
    cmd = "python3 .agent-kit/bin/agent-gh --repo o/r review-start 102"
    hook_in = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": rev2.sid,
                          "transcript_path": str(rev2.transcript), "agent_id": "sub-1", "agent_type": "general-purpose"})
    rev2.say("claude-opus-5-5", bash=cmd)
    o = sh(sys.executable, str(R / ".agent-kit/claude/pretool.py"), env=dict(BASE_ENV, AGENT_HOOKS_LOCAL="1", CLAUDE_PROJECT_DIR=str(R)), inp=hook_in).stdout
    run_cmd = json.loads(o)["hookSpecificOutput"]["updatedInput"]["command"]      # Claude Code と同じく書き換え後で実行
    subprocess.run(run_cmd, shell=True, cwd=R, env=BASE_ENV, capture_output=True, text=True)
    rc, out = rev2.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 0 and "unverified" in out, out


@test("claim: ref の名前が使えない等で断られたら、他の確保と取り違えずに API エラーとして報告")
def t_claim_bad_prefix():
    reset_state()
    rc, out = Session("s1").run("claim 1 --agent claude", model=None, env={"AGENT_CLAIM_REF_PREFIX": "badref"})
    assert rc == 5 and "他の確保ではない" in out, out


@test("gate: 所有者・メンバー・共同作業者以外(公開リポジトリの第三者)のレビューの目印は使わない")
def t_gate_stranger_mark():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: [c.__setitem__("author_association", "NONE") for c in s["comments"]["102"]])
    g = gate()
    assert not g["mergeable"] and any("所有者・メンバー" in n for n in g["notes"]), g


@test("gate: 形の正しくない目印があっても止まらず、その目印を使わない")
def t_gate_malformed_marks():
    head = reviewed_pr(); green_ci(head)
    bad = [{"sha": head, "items": [{}], "principal": "x", "sig": "y"}, {"sha": head, "items": [1]}]
    for b in bad:
        patch(lambda s, b=b: s["comments"]["102"].append({"id": 7, "body": f"<!-- agent-human-confirm: {json.dumps(b)} -->",
              "created_at": "2026-10-29T12:00:00Z", "updated_at": "2026-10-29T12:00:00Z", "user": {"login": "owner"},
              "author_association": "OWNER", "performed_via_github_app": None}))
    rv = {"reviewed_sha": head, "verdict": "pass", "blocking": 0, "unverified": [1], "model_status": "confirmed",
          "model": "claude-opus-5-5", "independence": "confirmed"}
    patch(lambda s: s["comments"]["102"].append({"id": 8, "body": f"<!-- agent-review: {json.dumps(rv)} -->",
          "created_at": "2026-10-29T12:30:00Z", "updated_at": "2026-10-29T12:30:00Z", "user": {"login": "owner"},
          "author_association": "OWNER", "performed_via_github_app": None}))
    g = gate()
    assert g["mergeable"] and any("形の正しくない" in n for n in g["notes"]), g


@test("review: 引用符・変数を使った push も実装とみなす")
def t_review_quoted_push():
    head = make_pr(with_marker=False)
    rev = Session("rev-q", remote_id="cse_rq")
    rev.say("claude-opus-5-5", bash='git push origin "$BR"')
    rc, out = review(rev, head)
    assert rc == 4 and "実装している" in out, out


@test("review: 自由に書ける --session の値だけでは独立性を確認済みにしない")
def t_review_freetext_session():
    head = commit(f"w {time.time_ns()}")
    reset_state(next_pr_sha=head)
    impl = Session("impl-ft", hooks=False)
    impl.run("claim 1 --agent dot", model=None)
    impl.run("pr-create --issue 1 --branch feature/1-add-login --title Add --no-draft --session mywork", model=None)
    patch(lambda s: s.setdefault("pr_commits", {}).__setitem__("102", [{"commit": {"message": "w", "author": {"email": "x@example.com"}}}]))
    green_ci(head)
    rc, out = review(Session("rev-ft", remote_id="cse_rft"), head)
    assert rc == 0 and "独立性 unverified" in out, out


@test("human-confirm: PR の今の HEAD の 40 桁の SHA でなければ署名しない")
def t_human_confirm_sha():
    head = make_pr()
    key, _ = make_signer()
    rc, out = Session("human", hooks=False).run(f"human-confirm 102 --sha HEAD --items model --key {key} --principal human@example.com", model=None)
    assert rc == 2 and "40 桁" in out, out


@test("gate: マージ先が既定のブランチでなければ注記する")
def t_gate_base_retarget():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: (s["pulls"]["102"]["base"].__setitem__("ref", "evil"),
                     s["contents"].__setitem__("evil", {".agent-kit/config.env": BASE_CONFIG + "AGENT_ALLOW_NO_CI=1\n"})))
    g = gate()
    assert any("既定のブランチ main ではない" in n for n in g["notes"]), g


# --- 今回の修正(R3: 再開と所有権、R5: 証拠の欠落、R8: 並行セッション) -------------------
@test("R3 claim: 同じ agent 名でも、稼働中の確保を別セッションから --resume できない")
def t_r3_resume_active():
    reset_state()
    Session("w1").run("claim 1 --agent claude", model=None)
    rc, out = Session("w2").run("claim 1 --agent claude --resume", model=None)
    assert rc == 3 and "稼働中の可能性" in out, out


@test("R3 claim: 同じセッションなら稼働中でも再開できる。止まった後(期限切れ)は別セッションから再開できる")
def t_r3_resume_same_or_stale():
    reset_state()
    Session("w1").run("claim 1 --agent claude", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "0.01", "AGENT_CLAIM_STALE_HOURS": "0.0001"})
    rc, out = Session("w1").run("claim 1 --agent claude --resume", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "0.01", "AGENT_CLAIM_STALE_HOURS": "0.0001"})
    assert rc == 0, out
    time.sleep(1.5)
    rc, out = Session("w3").run("claim 1 --agent claude --resume", model=None)
    assert rc == 0 and claim_state(1)["hook_session_id"] == Session("w3").sid, out


@test("R3 claim: release --handoff で明示的に引き継げば、稼働中でも別セッションが再開できる(他の agent には渡らない)")
def t_r3_handoff():
    reset_state()
    Session("w1").run("claim 1 --agent claude", model=None)
    rc, out = Session("w1").run("release 1 --agent claude --handoff", model=None)
    assert rc == 0 and claim_state(1)["state"] == "handoff", out
    rc, _ = Session("x9").run("claim 1 --agent dot", model=None)
    assert rc == 3, "handoff 中は他の agent が確保できない"
    rc, out = Session("w4").run("claim 1 --agent claude --resume", model=None)
    assert rc == 0 and claim_state(1)["state"] == "claimed", out


@test("R3 claim: 止まった確保を2つのセッションが同時に再開しても、成功は1つ")
def t_r3_concurrent_resume():
    reset_state()
    Session("w1").run("claim 1 --agent claude", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "0.01", "AGENT_CLAIM_STALE_HOURS": "0.0001"})
    time.sleep(1.5)
    rcs, lock = [], threading.Lock()

    def go(n):
        rc, _ = Session(f"r{n}").run("claim 1 --agent claude --resume", model=None)
        with lock:
            rcs.append(rc)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(rcs) == [0, 3, 3], rcs


@test("R3 claim: 再開のときも blocked と依存(前提 PR のマージ)を確かめ直す")
def t_r3_resume_rechecks_deps():
    reset_state()
    patch(lambda s: s["issues"]["3"]["labels"].remove({"name": "agent:stack-ok"}))   # 依存 #50(未マージ)を待つ Issue
    rc, _ = Session("w1").run("claim 3 --agent claude", model=None)
    assert rc == 2, "前提 PR が未マージなら最初から確保しない"
    patch(lambda s: s["pulls"]["50"].update(merged=True, state="closed"))
    rc, out = Session("w1").run("claim 3 --agent claude", model=None)
    assert rc == 0, out
    patch(lambda s: s["issues"]["3"]["labels"].append({"name": "agent:blocked"}))
    rc, out = Session("w1").run("claim 3 --agent claude --resume", model=None)
    assert rc == 2 and "agent:blocked" in out, out
    patch(lambda s: (s["issues"]["3"]["labels"].remove({"name": "agent:blocked"}), s["pulls"]["50"].update(merged=False, state="open")))
    rc, out = Session("w1").run("claim 3 --agent claude --resume", model=None)
    assert rc == 2 and "依存待ち" in out, out


@test("R5 review: transcript に読めない行がある・応答の記録にモデル名が無いなら未確認(残りだけで確認済みにしない)")
def t_r5_partial_transcript():
    head = make_pr(); green_ci(head)
    rev = Session("rev-p5", remote_id="cse_rp5")
    rev.run("review-start 102")
    with open(rev.transcript, "a") as f:
        f.write("{broken json\n")
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 0 and "モデル unverified" in out and "読めない" in comments()[-1]["body"], out
    head = make_pr(); green_ci(head)
    rev = Session("rev-p6", remote_id="cse_rp6")
    rev.run("review-start 102")
    with open(rev.transcript, "a") as f:
        f.write(json.dumps({"type": "assistant", "timestamp": "2026-10-29T10:00:00Z", "message": {"content": []}}) + "\n")
        f.write(json.dumps({"type": "user", "message": {"content": "関係のない記録(モデル名なし)"}}) + "\n")
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 0 and "モデル unverified" in out and "モデル名の無い応答" in comments()[-1]["body"], out
    assert not gate()["mergeable"]


@test("R5 review: 応答でない記録(user 等)にモデル名が無いのは問題にしない")
def t_r5_non_assistant_ok():
    head = make_pr(); green_ci(head)
    rev = Session("rev-p7", remote_id="cse_rp7")
    with open(rev.transcript, "a") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")
        f.write(json.dumps({"type": "summary", "summary": "x"}) + "\n")
    rc, out = review(rev, head)
    assert rc == 0 and "モデル confirmed" in out, out


@test("R5 review: レビュー側のセッション ID が ID の形でなければ、実装側の ID があっても独立性は未確認")
def t_r5_empty_reviewer_id():
    head = make_pr(); green_ci(head)
    rev = Session("rev-raw")
    rev.sid = "not-a-session-id"
    rc, out = review(rev, head)
    assert rc == 0 and "独立性 unverified" in out, out


@test("R8 証拠: 同じ checkout・同じ引数の2セッションを並行に実行しても、それぞれ自分の証拠だけを使う")
def t_r8_parallel_same_args():
    head = make_pr()
    a, b = Session("par-a", remote_id="cse_para"), Session("par-b", remote_id="cse_parb")
    cmd = "python3 .agent-kit/bin/agent-gh --repo o/r review-start 102"
    a.say("claude-opus-5-5", bash=cmd); b.say("claude-opus-5-5", bash=cmd)
    ca, cb = a.hook(cmd), b.hook(cmd)            # 両方の hook が先に走る(取り違えやすい順)
    assert "AGENT_EVIDENCE_ID=" in ca and ca != cb
    res = {}

    def run(name, c):
        res[name] = subprocess.run(c, shell=True, cwd=R, env=BASE_ENV, capture_output=True, text=True)
    ts = [threading.Thread(target=run, args=("b", cb)), threading.Thread(target=run, args=("a", ca))]
    [t.start() for t in ts]; [t.join() for t in ts]
    from importlib import util
    spec = util.spec_from_file_location("agentstate", R / ".agent-kit/lib/agentstate.py")
    m = util.module_from_spec(spec); spec.loader.exec_module(m)
    recs = [json.loads(p.read_text()) for p in (m.state_dir(str(R)) / "reviews").glob(f"*-102-{head[:12]}-*.json")]
    sids = {r["hook_session_id"] for r in recs}
    assert {a.sid, b.sid} <= sids, (sids, a.sid, b.sid)
    for r in recs:
        if r["hook_session_id"] in (a.sid, b.sid):
            assert r["transcript_path"].endswith(f"transcript-par-{'a' if r['hook_session_id'] == a.sid else 'b'}.jsonl"), r


@test("R8 証拠: 実行 ID が付かず(hook が書き換えなかった)、別セッションの同じコマンドの証拠が並ぶなら、どちらも使わない")
def t_r8_ambiguous_without_exec_id():
    head = make_pr()
    a, b = Session("amb-a", remote_id="cse_amba"), Session("amb-b", remote_id="cse_ambb")
    cmd = "python3 .agent-kit/bin/agent-gh --repo o/r review-start 102"
    a.hook(cmd, rewrite=False); b.hook(cmd, rewrite=False)
    r = subprocess.run(cmd, shell=True, cwd=R, env=BASE_ENV, capture_output=True, text=True)
    assert "証拠(hook)が無い" in r.stderr, r.stderr


# --- 独立レビュー(4回目)の指摘への修正 -------------------------------------------------
def wait_for(pred, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


@test("R3 lease: claim-renew は確保したセッションからだけ時刻を更新する。hook が裏で --mine を呼び、稼働中の確保は横取りされない")
def t_r3_renew():
    reset_state()
    w1 = Session("rn1")
    w1.run("claim 1 --agent claude", model=None)
    rc, out = Session("rn2").run("claim-renew 1", model=None)
    assert rc == 3 and "このセッションの確保ではない" in out, out
    rc, out = w1.run("claim-renew 1", model=None)
    assert rc == 0 and "リースを更新した" in out and claim_state(1).get("renewed") is True, out
    assert claim_state(1)["hook_session_id"] == w1.sid
    # hook(pretool)が、このセッションの確保を覚えていて、裏で claim-renew --mine を呼ぶ
    ref_sha = lambda: st()["refs"].get("refs/agent-claims/issue-1")
    agentstate_mod().renew_stamp(str(R), w1.sid, "o/r").unlink(missing_ok=True)     # 手動の更新の成功から間隔が過ぎた状態にする
    before = ref_sha()
    w1.run("claim-status 1", model=None, env={"AGENT_CLAIM_RENEW": "1"})
    assert wait_for(lambda: ref_sha() != before), "hook が裏でリースを更新していない"
    assert claim_state(1)["state"] == "claimed" and claim_state(1)["hook_session_id"] == w1.sid
    mid = ref_sha()
    w1.run("claim-status 1", model=None, env={"AGENT_CLAIM_RENEW": "1"})      # 1/3 リース以内の2回目は呼ばない
    time.sleep(1.0)
    assert ref_sha() == mid, "間隔の内側でも更新している"
    rc, out = Session("rn3").run("claim 1 --agent claude --resume", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "30"})
    assert rc == 3 and "稼働中の可能性" in out, out
    # 外したら、このセッションはもう更新しない
    rc, _ = w1.run("release 1 --agent claude", model=None)
    rc, out = w1.run("claim-renew --mine", model=None)
    assert rc == 0 and "リースを更新した" not in out, out
    rc, out = Session("rn4", hooks=False).run("claim-renew 1", model=None)
    assert rc == 2 and "セッションが分からない" in out, out


@test("v2.3③ gate: 判定の途中でマージ先(base ref)が変わったら、変わった後のベースの条件で判定し直す")
def t_v23_gate_base_ref_change():
    head = reviewed_pr(); green_ci(head)
    assert gate()["mergeable"]
    # dev ブランチの設定では別のモデルを指定している
    patch(lambda s: (s["refs"].__setitem__("dev", "sha-dev"),
                     s["contents"].__setitem__("dev", {".agent-kit/config.env": BASE_CONFIG.replace("claude-opus-5-5", "claude-sonnet-5-5")}),
                     s.__setitem__("base_sequence", {"102": ["main", "dev", "dev", "dev", "dev", "dev", "dev", "dev"]})))
    g = gate()
    assert not g["mergeable"], g
    assert any("マージ先" in r or "base" in r or "指定 claude-sonnet-5-5" in r for r in g["reasons"]), g


@test("v2.3③ gate: 判定の途中でベースの先頭(SHA)が動き、レビューした差分の基点が変わったら合格にしない")
def t_v23_gate_base_sha_change():
    head = reviewed_pr(); green_ci(head)
    patch(lambda s: (s.__setitem__("ref_seq", {"main": ["sha-main", "sha-main2", "sha-main2", "sha-main2", "sha-main2"]}),
                     s.setdefault("merge_bases", {}).__setitem__("sha-main2", "sha-main2")))
    g = gate()
    assert not g["mergeable"], g
    assert any("差分" in r for r in g["reasons"]), g


@test("v2.3③ review-post: review-start の後でベースが動いてレビュー対象の差分の基点が変わったら投稿しない")
def t_v23_review_post_base_moved():
    head = make_pr(); green_ci(head)
    rev = Session("rev-bm", remote_id="cse_revbm")
    rc, out = rev.run("review-start 102")
    assert rc == 0, out
    patch(lambda s: (s["refs"].__setitem__("main", "sha-main3"), s.setdefault("merge_bases", {}).__setitem__("sha-main3", "sha-main3")))
    n0 = len(comments())
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}")
    assert rc == 6 and "差分" in out and len(comments()) == n0, out


def agentstate_mod():
    from importlib import util
    spec = util.spec_from_file_location("agentstate", R / ".agent-kit/lib/agentstate.py")
    m = util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


@test("v2.3② lease: 更新の間隔は、保持中の確保のリース(一番短いもの)の 1/3。手元の設定を後で変えても確保の値に合わせる")
def t_v23_renew_interval():
    reset_state()
    m = agentstate_mod()
    w = Session("iv1")
    w.run("claim 1 --agent claude", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "10"})       # 確保時 10 分
    assert claim_state(1)["lease_minutes"] == 10.0
    # その後、手元の設定を 60 分に変えても、更新の間隔は 10 分の 1/3 = 200 秒(60 分の 1/3 = 1200 秒ではない)
    assert m.renew_interval_seconds(str(R), w.sid, 60.0) == 200.0
    w.run("claim 7 --agent claude --adopt feature/7-older", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "3"})
    assert m.renew_interval_seconds(str(R), w.sid, 60.0) == 60.0, "複数の確保なら一番短いリースに合わせる"
    w.run("release 7 --agent claude", model=None)
    assert m.renew_interval_seconds(str(R), w.sid, 60.0) == 200.0
    # 更新に成功すると、確保の記録のリースが手元にもそろう(古い記録で 60 分のままにならない)
    w.run("claim-renew 1", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "60"})
    assert claim_state(1)["lease_minutes"] == 10.0
    assert m.renew_interval_seconds(str(R), w.sid, 60.0) == 200.0


@test("v2.3② lease: 更新に失敗したら成功の印を進めず、再試行の間隔の後にもう一度呼ぶ")
def t_v23_renew_failure_retry():
    reset_state()
    m = agentstate_mod()
    w = Session("rf1")
    w.run("claim 1 --agent claude", model=None)
    stamp = m.renew_stamp(str(R), w.sid, "o/r")
    stamp.unlink(missing_ok=True)
    ref = "refs/agent-claims/issue-1"
    saved = st()["refs"][ref]
    patch(lambda s: s.__setitem__("fail_patch_refs", True))            # fake GitHub: ref の更新を断る
    w.run("claim-status 1", model=None, env={"AGENT_CLAIM_RENEW": "1", "AGENT_CLAIM_RENEW_RETRY_SECONDS": "0"})
    time.sleep(2.0)
    assert not stamp.exists(), "失敗したのに成功の印が付いた"
    assert st()["refs"][ref] == saved
    patch(lambda s: s.pop("fail_patch_refs", None))
    w.run("claim-status 1", model=None, env={"AGENT_CLAIM_RENEW": "1", "AGENT_CLAIM_RENEW_RETRY_SECONDS": "0"})
    assert wait_for(lambda: st()["refs"][ref] != saved), "失敗の後に再試行していない"
    assert wait_for(lambda: stamp.exists())


@test("R5 review: セッション本体の transcript が無ければ、サブエージェントの記録だけで確認済みにしない")
def t_r5_missing_main_transcript():
    head = make_pr(); green_ci(head)
    rev = Session("rev-mm", remote_id="cse_revmm")
    rc, out = rev.run("review-start 102")
    sub = W / "transcript-rev-mm" / "subagents"; sub.mkdir(parents=True, exist_ok=True)
    (sub / "a.jsonl").write_text(json.dumps({"type": "assistant", "isSidechain": True, "timestamp": "2026-10-29T10:00:00Z",
                                             "message": {"model": "claude-opus-5-5", "content": []}}) + "\n")
    rev.transcript.unlink()
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}", model=None)
    assert rc == 0 and "モデル unverified" in out and "本体の transcript が無い" in comments()[-1]["body"], out
    assert not gate()["mergeable"]


@test("R5 review: 独立性の判定も transcript を厳しく読む(読めない行があれば未確認。残りだけで「実装していない」としない)")
def t_r5_independence_strict():
    head = make_pr(); green_ci(head)
    rev = Session("rev-is", remote_id="cse_revis")
    rev.run("review-start 102")
    with open(rev.transcript, "a", encoding="latin-1") as f:
        f.write("\xff\xfe broken\n")              # UTF-8 として読めない行(この行に push があっても見えない)
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}", model=None)
    assert rc == 0 and "独立性 unverified" in out, out


@test("R8 証拠: hook が実行 ID を付けて渡した記録は、ID を持たない別のコマンドからは使えない")
def t_r8_injected_not_for_fallback():
    head = make_pr()
    a = Session("inj-a", remote_id="cse_inja")
    cmd = "python3 .agent-kit/bin/agent-gh --repo o/r review-start 102"
    rewritten = a.hook(cmd)                       # a の hook は ID を付けた(a はまだ実行していない)
    assert "AGENT_EVIDENCE_ID=" in rewritten
    r = subprocess.run(cmd, shell=True, cwd=R, env=BASE_ENV, capture_output=True, text=True)   # ID を持たない実行
    assert "証拠(hook)が無い" in r.stderr, r.stderr
    r = subprocess.run(rewritten, shell=True, cwd=R, env=BASE_ENV, capture_output=True, text=True)
    assert r.returncode == 0 and "証拠(hook)が無い" not in r.stderr, r.stderr


@test("R8 証拠: --opt=値 の書き方でも、hook の記録と引数が一致する")
def t_r8_equals_words():
    from importlib import util
    spec = util.spec_from_file_location("agentstate", R / ".agent-kit/lib/agentstate.py")
    m = util.module_from_spec(spec); spec.loader.exec_module(m)
    rec = m.command_words("python3 .agent-kit/bin/agent-gh --repo=o/r review-post 102 --sha=abc --body-file=/tmp/x.md")
    argv = m.command_words(" ".join(["--repo=o/r", "review-post", "102", "--sha=abc", "--body-file=/tmp/x.md"]))
    assert argv <= rec and "abc" in argv and "--sha" in argv, (argv, rec)


@test("R3 claim(5回目): 同じ agent 名の別セッションは、外して(release・--handoff・--force)から取り直す横取りができない")
def t_r3_release_other_session():
    reset_state()
    Session("h1").run("claim 1 --agent claude", model=None)
    for args in ("release 1 --agent claude --handoff", "release 1 --agent claude", "release 1 --agent claude --force"):
        rc, out = Session("h2").run(args, model=None)
        assert rc == 3 and "別のセッションが確保中" in out, (args, out)
    assert claim_state(1)["state"] == "claimed" and claim_state(1)["hook_session_id"] == Session("h1").sid
    rc, out = Session("h2").run("claim 1 --agent claude --resume", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "0"})
    assert rc == 3 and "稼働中の可能性" in out, "再開する側の環境変数でリースを短くできてはいけない: " + out
    rc, out = Session("h0", hooks=False).run("release 1 --agent claude", model=None)
    assert rc == 3 and "証拠が無い" in out, out
    rc, out = Session("h1").run("release 1 --agent claude --handoff", model=None)
    assert rc == 0, out


@test("R3 claim(6回目): hook の証拠の無い実行は --force でも稼働中の確保を外せない。handoff の後も期限は確保した側の値")
def t_r3_force_without_evidence():
    reset_state()
    Session("f1").run("claim 1 --agent claude", model=None)
    rc, out = Session("f2", hooks=False).run("release 1 --agent claude --force", model=None)
    assert rc == 3 and "--force でも外さない" in out, out
    rc, out = Session("f3", hooks=False).run("release 1 --agent dot --force", model=None)
    assert rc == 3, out
    assert claim_state(1)["state"] == "claimed" and claim_state(1)["hook_session_id"] == Session("f1").sid
    rc, out = Session("f1").run("release 1 --agent claude --handoff", model=None)
    assert rc == 0 and claim_state(1).get("stale_hours") is not None, out
    rc, out = Session("f4").run("claim 1 --agent dot --takeover", model=None, env={"AGENT_CLAIM_STALE_HOURS": "0.0001"})
    assert rc == 3 and "まだ動きがある" in out, "引き継ぐ側の環境変数で handoff 中の期限を短くできてはいけない: " + out
    rc, out = Session("f5").run("release 1 --agent dot --force", model=None)
    assert rc == 3, "handoff 中の確保を、別の agent が --force で外せてはいけない: " + out
    rc, out = Session("f6", hooks=False).run("release 1 --agent dot --force", model=None)
    assert rc == 3, out
    assert claim_state(1)["state"] == "handoff"


@test("R3 claim(5回目): --takeover でも blocked と依存を確かめ直す")
def t_r3_takeover_rechecks():
    reset_state()
    Session("t1").run("claim 1 --agent claude", model=None, env={"AGENT_CLAIM_LEASE_MINUTES": "0.01", "AGENT_CLAIM_STALE_HOURS": "0.0001"})
    patch(lambda s: s["issues"]["1"]["labels"].append({"name": "agent:blocked"}))
    time.sleep(1.5)
    rc, out = Session("t2").run("claim 1 --agent dot --takeover", model=None)
    assert rc == 2 and "agent:blocked" in out, out


@test("R5 review(5回目): JSON としては読めても記録の形でない行(配列・数値)があれば未確認")
def t_r5_non_object_line():
    head = make_pr(); green_ci(head)
    rev = Session("rev-no", remote_id="cse_revno")
    rev.run("review-start 102")
    with open(rev.transcript, "a") as f:
        f.write("[1, 2]\n")
    rc, out = rev.run(f"review-post 102 --sha {head} --verdict pass --blocking 0 --body-file {write_body()}", model=None)
    assert rc == 0 and "モデル unverified" in out and "独立性 unverified" in out, out


def main():
    setup_repo()
    for t in TESTS:
        t()
    fails = 0
    for ok, name, why in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f": {why}"))
        fails += 0 if ok else 1
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
