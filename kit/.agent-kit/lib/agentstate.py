"""agent-kit の状態置き場と「セッションの証拠」を扱う共通部品(標準ライブラリのみ)。

- state_dir(root): チェックアウトごとの状態ディレクトリ(lib/common.sh の agent_state_dir と同じ計算)
- write_evidence(): PreToolUse hook が、agent-gh を呼ぶ Bash コマンドを見たときに
  「どのセッション(hook の session_id・transcript)がそのコマンドを出したか」を記録する
- find_evidence(): agent-gh が自分を呼んだセッションの証拠を探す(新しく、コマンドが一致するもの)
- transcript_models() / transcript_commands(): セッションの transcript(JSONL)から、
  実際に応答したモデルと、実行した Bash コマンドを読む

限界: transcript の書式は Claude Code の公開 API ではない。読めない・モデル名が無いときは
「未確認」として扱い、確認できたふりをしない。証拠ファイルは同じ VM の中のエージェントが
書き換えようと思えば書き換えられる(誤判定を防ぐための仕組みで、改ざん対策ではない)。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

EVIDENCE_TTL = 60           # 秒。hook はコマンドの直前に動くので、これより古い証拠は使わない
EVIDENCE_KEEP = 200         # 残す証拠ファイルの数


def checkout_key(root: str) -> str:
    real = os.path.realpath(root)
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(real))[:40]
    return f"{base}-{hashlib.sha256(real.encode()).hexdigest()[:12]}"


def state_dir(root: str) -> Path:
    base = os.environ.get("AGENT_STATE_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.join(str(Path.home()), ".local", "state"), "agent-kit")
    d = Path(base) / checkout_key(root)
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_key(v: str) -> str:
    """ファイル名に使えるセッションの鍵(hook の session_id から)"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", (v or "").strip())[:80]


# --- このセッションが確保中の Issue(リースの更新に使う。hook と agent-gh の間だけの記録) ---------------
def _claims_file(root: str, session: str) -> Path | None:
    k = safe_key(session)
    return state_dir(root) / f"claims-{k}.json" if k else None


def my_claims(root: str, session: str) -> list[dict]:
    f = _claims_file(root, session)
    try:
        v = json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else []
        return [x for x in v if isinstance(x, dict) and "repo" in x and "issue" in x] if isinstance(v, list) else []
    except (OSError, ValueError, RecursionError):
        return []


def set_my_claim(root: str, session: str, repo: str, issue: int, held: bool, lease_minutes: float | None = None) -> None:
    """読んで・変えて・書く間は排他にする(裏のリース更新と、同じセッションの新しい確保が重なっても消し合わない)"""
    f = _claims_file(root, session)
    if not f:
        return
    import fcntl
    with open(f.with_suffix(".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        v = [x for x in my_claims(root, session) if not (x["repo"] == repo and x["issue"] == issue)]
        if held:
            rec = {"repo": repo, "issue": issue}
            if isinstance(lease_minutes, (int, float)) and lease_minutes > 0:
                rec["lease_minutes"] = float(lease_minutes)     # 確保の記録に書いたリース(更新の間隔はこれで決める)
            v.append(rec)
        if v:
            tmp = f.with_name(f"{f.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
            tmp.write_text(json.dumps(v), encoding="utf-8")
            os.replace(tmp, f)
        else:
            f.unlink(missing_ok=True)


def renew_stamp(root: str, session: str, repo: str = "") -> Path | None:
    """最後にリースの更新が成功した時刻の印(リポジトリごと。更新に成功したときだけ触る)"""
    k = safe_key(session)
    r = ("-" + hashlib.sha256(repo.encode()).hexdigest()[:12]) if repo else ""
    return state_dir(root) / f"claims-{k}{r}.renewed" if k else None


def renew_interval_seconds(root: str, session: str, fallback_minutes: float, repo: str = "") -> float | None:
    """保持中の確保(repo を渡せばそのリポジトリのもの)のリースのうち一番短いものの 1/3(秒)。確保が無ければ None。
    手元の設定(AGENT_CLAIM_LEASE_MINUTES)は、リースが記録されていない古い確保のときだけ使う"""
    held = [x for x in my_claims(root, session) if not repo or x["repo"] == repo]
    if not held:
        return None
    leases = [x["lease_minutes"] if isinstance(x.get("lease_minutes"), (int, float)) and x["lease_minutes"] > 0
              else fallback_minutes for x in held]
    return max(min(leases) * 60 / 3, 1.0)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> dt.datetime | None:
    try:
        v = dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# --- 証拠(hook → agent-gh) ----------------------------------------------------
def write_evidence(root: str, hook_input: dict, command: str, injected: bool = False) -> str | None:
    """agent-gh を含む Bash コマンドについて、出したセッションを記録し、一意な実行 ID を返す。失敗しても例外を出さない。
    hook はこの ID を AGENT_EVIDENCE_ID としてコマンドに付ける(updatedInput)。agent-gh はその ID の証拠だけを使う。"""
    try:
        d = state_dir(root) / "evidence"
        d.mkdir(parents=True, exist_ok=True)
        exec_id = secrets.token_hex(16)
        rec = {
            "exec_id": exec_id,
            "hook_session_id": hook_input.get("session_id", ""),
            "transcript_path": hook_input.get("transcript_path", ""),
            "remote_session_id": os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", ""),
            "remote": os.environ.get("CLAUDE_CODE_REMOTE") == "true",
            # サブエージェントの中から出たコマンドなら、hook の入力にその印がある(無い版もある)
            "agent_id": hook_input.get("agent_id", ""), "agent_type": hook_input.get("agent_type", ""),
            "command_sha": hashlib.sha256(command.encode()).hexdigest(),
            "command": command[:2000],
            # hook が実行 ID をコマンドに付けた(updatedInput)記録。ID を持たない呼び出し(代替の探し方)では使わない
            "injected": bool(injected),
            "at": now_iso(),
            "at_epoch": time.time(),
        }
        p = d / f"{time.time_ns()}-{exec_id}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        olds = sorted(d.glob("*.*"))[:-EVIDENCE_KEEP]
        for o in olds:
            o.unlink(missing_ok=True)
        return exec_id
    except Exception:
        return None


def find_evidence(root: str, must_contain: list[str], ttl: int = EVIDENCE_TTL, consume: bool = True,
                  exec_id: str = "", caller_remote: str = "") -> dict | None:
    """このコマンドを出したセッションの証拠を返す。見つからない・決められないときは None(= 未確認)。
    - exec_id(hook が付けた AGENT_EVIDENCE_ID)があれば、その ID の証拠だけを使う(コマンドの語も一致を確かめる)。
    - 無ければ(hook がコマンドを書き換えられなかった等)、ttl 秒以内でコマンドの語が一致する証拠を探す。
      caller_remote(CLAUDE_CODE_REMOTE_SESSION_ID)があれば同じセッションのものだけ。
      **別々のセッションの候補が2つ以上あれば、どれか決められないので使わない**(並行実行で取り違えない)。
    consume=True なら使った証拠は名前を変えて二度と使わない。"""
    d = state_dir(root) / "evidence"
    if not d.is_dir():
        return None
    limit = time.time() - ttl
    want = set(must_contain)
    cands = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if rec.get("at_epoch", 0) < limit:
            break
        if exec_id and rec.get("exec_id") != exec_id:
            continue
        if not exec_id and rec.get("injected"):
            continue          # ID を付けて渡した記録は、その ID を持つコマンドだけが使う(別のセッションに取られない)
        if not want <= command_words(rec.get("command", "")):
            continue
        if caller_remote and rec.get("remote_session_id") and rec["remote_session_id"] != caller_remote:
            continue
        cands.append((p, rec))
        if exec_id:
            break
    if not cands:
        return None
    if not exec_id and len({(r.get("hook_session_id"), r.get("remote_session_id")) for _, r in cands}) > 1:
        return None
    p, rec = cands[0]
    if consume:
        try:
            os.rename(p, p.with_suffix(".used"))
        except OSError:
            return None             # 他のプロセスが先に使った
    return rec


def command_words(cmd: str) -> set[str]:
    """コマンドの語(パスはファイル名も語として足す: .agent-kit/bin/agent-gh → agent-gh)。
    --opt=値 は --opt と 値 に分ける(argv 側も同じ関数で語にするので、書き方で一致しなくならない)"""
    words = set(re.findall(r"[^\s'\"=;&|()]+", cmd))
    return words | {os.path.basename(w) for w in words if "/" in w}


# --- transcript ----------------------------------------------------------------
class TranscriptProblem(Exception):
    pass


def _iter_transcript(path: str, strict: bool = False):
    """JSONL を1行ずつ読む。strict=True なら、読めない行・読み取りの失敗を TranscriptProblem にする
    (飛ばして残りだけで判定しない)"""
    try:
        with open(path, encoding="utf-8", errors="strict" if strict else "replace") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    ent = json.loads(line)
                except json.JSONDecodeError:
                    if strict:
                        raise TranscriptProblem(f"{os.path.basename(path)} の {n} 行目を読めない")
                    continue
                if not isinstance(ent, dict):
                    if strict:
                        raise TranscriptProblem(f"{os.path.basename(path)} の {n} 行目が記録の形でない")
                    continue
                yield ent
    except (OSError, UnicodeDecodeError) as e:
        if strict:
            raise TranscriptProblem(f"{os.path.basename(path)} を読み切れない({type(e).__name__})")
        return


def transcript_files(path: str, strict: bool = False) -> list[str]:
    """セッションの transcript と、サブエージェントの transcript(<名前>/subagents/*.jsonl があれば)。
    strict=True なら、本体の transcript が無い・サブエージェントの一覧を読めないときは TranscriptProblem
    (サブエージェントの記録だけで判定しない)"""
    if not (path and os.path.isfile(path)):
        if strict:
            raise TranscriptProblem("セッション本体の transcript が無い")
        return []
    files = [path]
    sub = os.path.join(os.path.splitext(path)[0], "subagents")
    try:      # isdir は権限の無いときも False になるので、一覧を取ってみて「無い」と「読めない」を分ける
        files += sorted(os.path.join(sub, f) for f in os.listdir(sub) if f.endswith(".jsonl"))
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError as e:
        if strict:
            raise TranscriptProblem(f"サブエージェントの transcript の一覧を読めない({type(e).__name__})")
    return files


def transcript_models(path: str, since: str = "") -> dict:
    """判定の対象 = transcript(とサブエージェントの transcript)の type が "assistant" の記録。
    その記録のモデル名を集める。対象の記録にモデル名が無い・読めない行がある・読み切れないときは
    readable=False / problem に理由を入れる(残りの記録だけで確認済みにしない)。
    "<synthetic>" はエラー表示などで Claude Code が作る記録で、モデルの応答ではないので対象から外す。
    since(ISO 時刻)を渡すとそれ以降だけ(空ならセッション全体)。"""
    try:
        files = transcript_files(path, strict=True)
    except TranscriptProblem as e:
        return {"readable": False, "models": [], "count": 0, "sidechain": 0, "problem": str(e)}
    t0 = parse_iso(since) if since else None
    models, count, side = [], 0, 0
    try:
        for f in files:
            for ent in _iter_transcript(f, strict=True):
                if not isinstance(ent, dict) or ent.get("type") != "assistant":
                    continue
                msg = ent.get("message")
                m = msg.get("model") if isinstance(msg, dict) else None
                if m == "<synthetic>":
                    continue
                if not isinstance(m, str) or not m:
                    raise TranscriptProblem(f"{os.path.basename(f)} にモデル名の無い応答の記録がある")
                ts = parse_iso(ent.get("timestamp", ""))
                if t0 and (ts is None or ts < t0):
                    continue
                count += 1
                side += 1 if (ent.get("isSidechain") or f != path) else 0
                if m not in models:
                    models.append(m)
    except TranscriptProblem as e:
        return {"readable": False, "models": models, "count": count, "sidechain": side, "problem": str(e)}
    return {"readable": True, "models": models, "count": count, "sidechain": side, "problem": ""}


def transcript_commands(path: str, strict: bool = False) -> list[str]:
    """セッション(サブエージェントを含む)で実行した Bash コマンド(tool_use の input.command)。
    strict=True なら、読めない行・読み切れない・本体が無いときは TranscriptProblem(一部だけで「実装していない」としない)"""
    out = []
    for ent in (e for f in transcript_files(path, strict) for e in _iter_transcript(f, strict)):
        if not isinstance(ent, dict):
            continue
        msg = ent.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            # 応答(assistant)の記録は、中身が一覧の形のはず。違えば、その中のコマンドを見落とし得る
            if strict and ent.get("type") == "assistant":
                raise TranscriptProblem("応答の記録の形が違う")
            continue
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash":
                inp = c.get("input")
                if not isinstance(inp, dict):
                    if strict:
                        raise TranscriptProblem("Bash の記録の形が違う")
                    continue
                cmd = inp.get("command")
                if isinstance(cmd, str):
                    out.append(cmd)
                elif strict:
                    raise TranscriptProblem("Bash の記録のコマンドの形が違う")
    return out


def model_matches(model: str, want: str) -> bool:
    """claude-opus-5-5 と claude-opus-5-5-20261001(日付つき)は同じとみなす。それ以外は別モデル。"""
    return model == want or bool(re.fullmatch(re.escape(want) + r"-\d{8}", model or ""))
