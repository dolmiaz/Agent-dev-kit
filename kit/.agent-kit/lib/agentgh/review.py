"""レビューの記録。

1. review-start: レビューする SHA をその場で固定して記録する(以後、このセッションはこの SHA を読む)。
2. review-post --sha <固定した SHA>: 投稿直前に PR の HEAD を取り直し、変わっていたら投稿せず「保留・再レビュー」。
3. モデルと独立性は自己申告ではなく、hook が残した証拠(どのセッションがコマンドを出したか)と、
   そのセッションの transcript から確かめる。確かめられなければ「未確認」として記録し、合格扱いにしない。

確認できる範囲(限界):
- モデル: transcript に記録された、review-start 以降の応答のモデル名。transcript が読めない・モデル名が無いときは未確認。
- 独立性: レビューしたセッションの transcript に、この Issue の claim / pr-create / ブランチへの push が無いこと、
  かつ実装側のセッション(PR の目印・Claude-Session トレーラー)と同じ ID でないこと。
  実装側の ID が分からず、Codex の作者名のコミットでもないときは未確認。
- どちらも、本人が `agent-gh human-confirm` で確認すれば合格の条件を満たせる(merge-gate が投稿者を確かめる)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import humanconfirm, issues
from .api import api, cfg, repo_root
from .basecfg import BaseConfig, diff_anchor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agentstate  # noqa: E402

EXIT_MODEL = 4
EXIT_MOVED = 6


def _review_dir() -> Path:
    d = agentstate.state_dir(repo_root()) / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _start_path(repo: str, n: int, sha: str, session: str) -> Path:
    """セッションごと・SHA ごとの記録(別のセッションの記録を上書きしない)"""
    sess = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "no-session")[:60]
    return _review_dir() / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', repo)}-{n}-{sha[:12]}-{sess}.json"


def own_evidence(argv: list[str], tool: str = "agent-gh") -> dict:
    """自分(このコマンド)を出したセッションの証拠。hook が付けた実行 ID(AGENT_EVIDENCE_ID)の証拠だけを使う。
    実行 ID が無いときは、引数が一致して、どのセッションのものか1つに決められる証拠だけ(決められなければ未確認)。"""
    words = [tool] + sorted(agentstate.command_words(" ".join(argv)))
    return agentstate.find_evidence(repo_root(), words, exec_id=os.environ.get("AGENT_EVIDENCE_ID", ""),
                                    caller_remote=os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "")) or {}


def cmd_review_start(a, repo: str) -> int:
    pr = api("GET", f"repos/{repo}/pulls/{a.number}")
    ev = a.evidence
    sha = pr["head"]["sha"]
    path = _start_path(repo, a.number, sha, ev.get("hook_session_id", ""))
    if path.exists():
        # 同じセッション・同じ SHA の2回目は、最初の記録を残す(やり直しで判定の範囲をずらさない)
        rec = json.loads(path.read_text(encoding="utf-8"))
        print(f"注: この SHA のレビューは {rec['started_at']} に開始済み。最初の記録を使う", file=sys.stderr)
    else:
        rec = {"repo": repo, "pr": a.number, "sha": sha, "head_ref": pr["head"]["ref"],
               "base_ref": pr["base"]["ref"], "base_sha": pr["base"].get("sha", ""),
               # レビュー対象の差分(merge-base...head)。投稿の直前と merge-gate で、同じ差分かを確かめる
               "anchor": diff_anchor(repo, pr["base"]["ref"], sha),
               "started_at": agentstate.now_iso(),
               "hook_session_id": ev.get("hook_session_id", ""), "remote_session_id": ev.get("remote_session_id", ""),
               "transcript_path": ev.get("transcript_path", ""), "agent_id": ev.get("agent_id", ""),
               "evidence": bool(ev)}
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(sha)
    print(f"レビューする SHA を固定した: {sha[:12]}(PR #{a.number}、{rec['head_ref']} → {rec['base_ref']})", file=sys.stderr)
    print(f"  git fetch origin {rec['head_ref']} {rec['base_ref']} && git checkout --detach {sha}", file=sys.stderr)
    print(f"  差分: git diff origin/{rec['base_ref']}...{sha}", file=sys.stderr)
    print(f"  記録: agent-gh review-post {a.number} --sha {sha} --verdict ... --blocking N --body-file F", file=sys.stderr)
    if not ev:
        print("注: このセッションの証拠(hook)が無い。モデルと独立性は「未確認」で記録される", file=sys.stderr)
    return 0


def model_check(start: dict, want: str, self_reported: str | None) -> dict:
    """このセッションで応答したモデルを、セッション全体(サブエージェントを含む)の transcript から確かめる。
    レビューのセッションは最初から指定モデルで動かす前提(途中で変えた・開始前に別モデルで読んだ、も検出する)。"""
    if self_reported and not agentstate.model_matches(self_reported, want):
        return {"status": "mismatch", "detail": f"申告モデル {self_reported}"}
    if not start.get("evidence") or not start.get("transcript_path"):
        return {"status": "unverified", "detail": "hook の証拠が無い(自己申告だけ)"}
    if start.get("agent_id"):
        return {"status": "unverified", "detail": "サブエージェントの中から実行された(どのモデルが読んだか確かめられない)"}
    tm = agentstate.transcript_models(start["transcript_path"])
    if not tm["readable"]:
        return {"status": "unverified", "detail": f"transcript を確かめられない({tm.get('problem') or '読めない'})"}
    if not tm["models"]:
        return {"status": "unverified", "detail": "transcript にモデル名が無い"}
    bad = [m for m in tm["models"] if not agentstate.model_matches(m, want)]
    if bad:
        return {"status": "mismatch", "detail": f"このセッションで {', '.join(bad)} が応答している(サブエージェントを含む。"
                "Explore などの組み込みのサブエージェントも別のモデルで動くことがある。レビューは新しいセッションで、"
                "サブエージェントを使わずに行う)", "models": tm["models"]}
    return {"status": "confirmed", "detail": f"セッション全体の {tm['count']} 回の応答がすべて {want}"
            + (f"(うちサブエージェント {tm['sidechain']} 回)" if tm["sidechain"] else ""), "models": tm["models"]}


def implementer_ids(repo: str, n: int, pr: dict) -> tuple[set[str], list[str]]:
    """実装側のセッション ID と、コミットの作者メール"""
    ids: set[str] = set()
    for m in issues.marks(pr.get("body", ""), "impl"):
        ids |= {v for k, v in m.items() if k in ("session", "hook_session_id", "remote_session_id") and isinstance(v, str) and v}
    emails = []
    for c in api("GET", f"repos/{repo}/pulls/{n}/commits?per_page=100", paginate=True) or []:
        commit = c.get("commit") or {}
        for t in re.findall(r"^Claude-Session:\s*(\S+)", commit.get("message", ""), re.M):
            ids.add(t)
        emails.append(((commit.get("author") or {}).get("email") or "").lower())
    return ids, emails


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def norm_session(v: str) -> str:
    """セッション ID の表記をそろえる。Claude は同じセッションを cse_<id>(環境変数)と
    https://claude.ai/code/session_<id>(コミットのトレーラー)で表すので、<id> の部分で比べる。
    ID と分からない文字列(自由に書ける --session の値など)は "" を返し、独立性の根拠に使わない。"""
    v = (v or "").strip()
    m = re.search(r"(?:session_|cse_)([A-Za-z0-9]{8,})", v)
    if m:
        return m.group(1)
    return v.lower() if UUID_RE.match(v) else ""


def _split(text: str) -> list[str]:
    import shlex
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _pushes_head(cmd: str, head_ref: str) -> bool:
    """PR のブランチ(または今のブランチ)への push か。refspec 無し・HEAD・ブランチ名(+・refs/heads/・src:dst・引用符の
    どの書き方でも)を実装とみなす。変数・コマンド置換・ワイルドカードを含む refspec は、行き先が分からないので実装とみなす"""
    def target(r: str) -> str:
        r = r.lstrip("+")
        dst = r.split(":", 1)[1] if ":" in r else r
        return dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
    for m in re.finditer(r"\bgit\b((?:\s+-[Cc]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*)\s+push\b([^;&|\n]*)", cmd):
        raw = m.group(2)
        args = [a for a in _split(raw) if not a.startswith("-")]
        refs = args[1:]                       # args[0] はリモート名
        if not refs:
            return True
        if any(ch in raw for ch in "$`*"):
            return True
        for r in refs:
            if r.lstrip("+") in ("HEAD", "@") or r.lstrip("+").startswith(("HEAD:", "@:")) or (head_ref and target(r) == head_ref):
                return True
    return False


def _did_implement(cmds: list[str], issue: int | None, head_ref: str) -> list[str]:
    hits = []
    for c in cmds:
        w = agentstate.command_words(c)
        if "agent-gh" in w and issue is not None and str(issue) in w and ("claim" in w or "pr-create" in w):
            hits.append(c[:120])
        elif "agent-gh" in w and "pr-create" in w and head_ref and head_ref in w:
            hits.append(c[:120])
        elif _pushes_head(c, head_ref):
            hits.append(c[:120])
    return hits


def independence_check(repo: str, n: int, pr: dict, start: dict, codex_email: str) -> dict:
    if not start.get("evidence"):
        return {"status": "unverified", "detail": "レビュー側の hook の証拠が無い"}
    rev_ids = {norm_session(x) for x in (start.get("hook_session_id"), start.get("remote_session_id")) if x} - {""}
    if not rev_ids:
        return {"status": "unverified", "detail": "レビュー側のセッション ID が分からない(ID の形でない)"}
    raw_impl, emails = implementer_ids(repo, n, pr)
    impl_ids = {norm_session(x) for x in raw_impl} - {""}     # ID と分かるものだけ
    issue = (issues.marks(pr.get("body", ""), "impl") or [{}])[-1].get("issue")
    try:      # 読めない行・読み切れない・本体が無いときは、残りだけで「実装していない」と判定しない
        cmds = agentstate.transcript_commands(start.get("transcript_path", ""), strict=True)
    except agentstate.TranscriptProblem as e:
        return {"status": "unverified", "detail": f"レビュー側の transcript を確かめられない({e})"}
    hits = _did_implement(cmds, issue, start["head_ref"])
    if hits:
        return {"status": "same", "detail": f"このセッションで実装している: {hits[0]}"}
    if rev_ids & impl_ids:
        return {"status": "same", "detail": "実装と同じセッション ID"}
    codex_email = codex_email.lower()
    if impl_ids:
        return {"status": "confirmed", "detail": f"実装側 {len(impl_ids)} 件の ID と一致しない"}
    if emails and all(e == codex_email for e in emails) and os.environ.get("CLAUDE_CODE_REMOTE") == "true":
        return {"status": "confirmed", "detail": "実装は Codex の作者名のコミットだけ、レビューは Claude のセッション"}
    return {"status": "unverified", "detail": "実装側のセッションが分からない"}


def _local_has(sha: str) -> bool:
    r = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo_root(), capture_output=True)
    return r.returncode == 0


def cmd_review_post(a, repo: str) -> int:
    pr = api("GET", f"repos/{repo}/pulls/{a.number}")
    bc = BaseConfig(repo, pr["base"]["ref"])        # 指定モデル等は PR が変えられないベースの設定で決める
    want = bc.get("AGENT_REVIEW_MODEL")
    now_ev = a.evidence
    sp = _start_path(repo, a.number, a.sha, now_ev.get("hook_session_id", ""))
    if not sp.exists():
        others = list(_review_dir().glob(f"{re.sub(r'[^A-Za-z0-9_.-]', '_', repo)}-{a.number}-{a.sha[:12]}-*.json"))
        hint = ("別のセッション(または hook を通らない実行)で開始した記録はある。このセッションから直接、"
                "review-start からやり直す" if others else "先に review-start を実行する")
        print(f"中止: このセッションの SHA {a.sha[:12]} の review-start の記録が無い。{hint}", file=sys.stderr)
        return 2
    start = json.loads(sp.read_text(encoding="utf-8"))
    if start["sha"] != a.sha:
        print("中止: review-start で固定した SHA と違う", file=sys.stderr)
        return 2
    if not _local_has(a.sha):
        print(f"中止: {a.sha[:12]} がこの環境に無い(レビューした内容を確かめられない)。fetch してから読む", file=sys.stderr)
        return 2
    model = model_check(start, want, a.model)
    if model["status"] == "mismatch":
        print(f"中止: レビュー担当のモデルが指定({want})と違う: {model['detail']}。"
              "黙って別モデルでレビューせず、本人に報告すること", file=sys.stderr)
        return EXIT_MODEL
    indep = independence_check(repo, a.number, pr, start, bc.get("AGENT_GIT_EMAIL_CODEX"))
    if indep["status"] == "same":
        print(f"中止: 実装したセッションではレビューしない({indep['detail']})", file=sys.stderr)
        return EXIT_MODEL
    info = {"reviewed_sha": a.sha, "model": want if model["status"] == "confirmed" else (a.model or ""),
            "model_status": model["status"], "model_detail": model["detail"],
            "independence": indep["status"], "independence_detail": indep["detail"],
            "reviewer_session": start.get("hook_session_id", ""), "reviewer_remote_session": start.get("remote_session_id", ""),
            "verdict": a.verdict, "blocking": a.blocking, "unverified": a.unverified or [],
            "started_at": start["started_at"], "at": agentstate.now_iso()}
    body = Path(a.body_file).read_text(encoding="utf-8")
    note = ""
    if model["status"] != "confirmed" or indep["status"] != "confirmed":
        note = (f"\n\n> モデル: {model['status']}({model['detail']}) / 独立性: {indep['status']}({indep['detail']})。"
                "未確認のままでは merge-gate は合格にしない。本人が確かめたら `agent-gh human-confirm` を実行する。")
    now = api("GET", f"repos/{repo}/pulls/{a.number}")     # 投稿の直前に取り直す
    head = now["head"]["sha"]
    if head != a.sha:
        print(f"保留: レビュー中に PR が更新された({a.sha[:12]} → {head[:12]})。"
              "古い差分のレビューは投稿しない。review-start からやり直す", file=sys.stderr)
        return EXIT_MOVED
    anchor = start.get("anchor") or {}
    if not anchor.get("merge_base"):
        print("中止: review-start の記録に、レビュー対象の差分の基点が無い(古い版で開始した)。review-start からやり直す",
              file=sys.stderr)
        return 2
    cur = diff_anchor(repo, now["base"]["ref"], head)
    if cur["base_ref"] != anchor.get("base_ref") or cur["merge_base"] != anchor.get("merge_base"):
        print(f"保留: レビュー中にマージ先かその先頭が変わり、レビュー対象の差分が変わった"
              f"({anchor.get('base_ref')}@{anchor.get('merge_base', '')[:12]} → {cur['base_ref']}@{cur['merge_base'][:12]})。"
              "古い差分のレビューは投稿しない。review-start からやり直す", file=sys.stderr)
        return EXIT_MOVED
    info["reviewed_base_ref"] = cur["base_ref"]
    info["reviewed_merge_base"] = cur["merge_base"]
    api("POST", f"repos/{repo}/issues/{a.number}/comments", {
        "body": f"### レビュー({a.sha[:12]})\n\n{body}{note}\n\n{issues.mark('review', info)}"})
    print(f"review recorded for {a.sha[:12]}: {a.verdict}(モデル {model['status']} / 独立性 {indep['status']})")
    return 0


def cmd_human_confirm(a, repo: str) -> int:
    """本人がモデル・独立性を確かめたことを、本人の鍵で署名して記録する。本人の端末で実行する。"""
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true" or os.environ.get("AGENT_RUNTIME", "").endswith("cloud"):
        print("中止: これは本人が自分の端末で実行する(クラウドのエージェントからは実行しない)", file=sys.stderr)
        return 2
    items = sorted({x for x in a.items.split(",") if x in ("model", "session")})
    if not items:
        print("--items は model,session のどちらか(両方可)", file=sys.stderr)
        return 2
    head = api("GET", f"repos/{repo}/pulls/{a.number}")["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", a.sha or "") or a.sha != head:
        print(f"中止: --sha は PR の今の HEAD の 40 桁の SHA を書く(今の HEAD: {head})。確かめたレビューの SHA と同じか見直す",
              file=sys.stderr)
        return 2
    key = a.key or os.environ.get("AGENT_HUMAN_CONFIRM_KEY", "")
    principal = a.principal or os.environ.get("AGENT_HUMAN_CONFIRM_PRINCIPAL", "")
    if not key or not principal:
        print("中止: 署名に使う鍵(--key / AGENT_HUMAN_CONFIRM_KEY)と名前(--principal / AGENT_HUMAN_CONFIRM_PRINCIPAL)が要る。"
              "公開鍵はベースブランチの .agent-kit/human-confirm-allowed-signers に置く(docs/09)", file=sys.stderr)
        return 2
    try:
        sig = humanconfirm.sign(key, humanconfirm.payload(repo, a.number, a.sha, items))
    except RuntimeError as e:
        print(f"中止: {e}", file=sys.stderr)
        return 2
    info = {"sha": a.sha, "items": items, "principal": principal, "sig": sig, "note": a.note or "", "at": agentstate.now_iso()}
    api("POST", f"repos/{repo}/issues/{a.number}/comments", {
        "body": f"本人確認({a.sha[:12]}): {', '.join(items)}(署名つき)\n\n{issues.mark('human-confirm', info)}"})
    print("recorded")
    return 0
