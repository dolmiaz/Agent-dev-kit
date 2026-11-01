#!/usr/bin/env python3
"""collect-pending.py — Mac で実行する回収(AGENT_LOG_PENDING_MODE=repo を選んだリポジトリだけ)。

既定(outbox)では未送信ログは Git に入らないので、このスクリプトは不要。
repo モードを明示したリポジトリで、ブランチに残った .agent-logs/pending/*.md を、
GitHub のブランチから直接読み(チェックアウト不要・PR 不要)、Drive の正しいフォルダへ置く。

  python3 collect-pending.py [--dry-run] [--branches origin] <repoのパス>...

保証すること
- 置き先はフォルダ ID で固定(名前で探さない)。設定の deny に書いたフォルダ(同期ミラー等)の ID は拒否。
  ID は設定ファイル ~/.config/agent-kit/collector.json(--config で変更)に書く。リポジトリには書かない:
    {"folders": {"codex": "<ID>", "claude": "<ID>"}, "deny": ["<ミラーの ID>", ...]}
- ログの source(codex / claude)とフォルダの対応を確認し、違うフォルダへ置かない。
- 一意性は log_id(UUID)+内容の md5 で判定する。同名で内容が違うファイルが Drive にあれば置かない(conflict)。
- 置いたら Drive 側の md5 を読み直して一致を確かめ、台帳(~/.cache/agent-kit/collected.json)に log_id を記録。
  Drive 側で取り込み後に削除されても、台帳にある log_id は再送しない。
- リポジトリのファイルは消さない(消すのは本人の判断)。

必要: rclone(remote 名は RCLONE_REMOTE、既定 drive)、git。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FOLDERS: dict[str, str] = {}
DENY: set[str] = set()
DEFAULT_CONFIG = Path.home() / ".config" / "agent-kit" / "collector.json"


def load_folders(path: Path) -> str | None:
    """置き先と拒否フォルダを設定ファイルから読む。問題があれば理由を返す。"""
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"設定ファイルを読めない: {path}({e})。docs/05 の例をもとに作る"
    FOLDERS.update({k: v for k, v in (j.get("folders") or {}).items() if k in ("codex", "claude") and v})
    DENY.update(x for x in j.get("deny") or [] if x)
    if set(FOLDERS) != {"codex", "claude"}:
        return "設定の folders に codex と claude の両方の ID が必要"
    if not DENY:
        return "設定の deny が空。書き込んではいけないフォルダ(同期ミラー等)の ID を書く(無ければ明示的に [\"none\"])"
    return None
UUID_RE = re.compile(r"^log_id:\s*\"?([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\"?\s*$", re.M)
SRC_RE = re.compile(r"^source:\s*\"?(codex|claude)\"?\s*$", re.M)
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """失敗(コマンドが無い・時間切れ)も終了コードで返す(例外で落ちずに、理由を出して止められるように)"""
    kw.setdefault("timeout", 300)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]} が見つからない")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]} が {kw['timeout']} 秒で終わらない")


def check_not_under_deny(remote: str) -> str | None:
    """置き先が拒否フォルダの配下でないことを、拒否フォルダの下のフォルダ ID を列挙して確かめる
    (受け口の Code.gs が親をたどるのと同じ保証。確かめられなければ置かない)"""
    for d in sorted(DENY):
        if d == "none":
            continue
        r = run(["rclone", "lsjson", "-R", "--dirs-only", "--drive-root-folder-id", d, f"{remote}:"])
        if r.returncode != 0:
            return f"拒否フォルダ {d} の配下を確かめられない(rclone: {r.stderr.strip()[:160]})。置かない"
        try:
            items = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            return f"拒否フォルダ {d} の一覧を読めない。置かない"
        if not isinstance(items, list):
            return f"拒否フォルダ {d} の一覧の形が想定と違う。置かない"
        missing = [x.get("Path", "?") if isinstance(x, dict) else "?" for x in items
                   if not (isinstance(x, dict) and isinstance(x.get("ID"), str) and x.get("ID"))]
        if missing:
            # ID の無いフォルダがあると、置き先がその下にあるか確かめられない。安全と見なさない
            return (f"拒否フォルダ {d} の配下に ID の分からないフォルダが {len(missing)} 件ある({', '.join(missing[:5])})。"
                    "配下でないと確かめられないので置かない(rclone の版・設定を確かめる)")
        ids = {x["ID"] for x in items}
        bad = [k for k, v in FOLDERS.items() if v in ids]
        if bad:
            return f"置き先({', '.join(bad)})が拒否フォルダ {d} の配下にある。置かない"
    return None


def remote_md5(remote: str, folder: str, name: str) -> str | None:
    r = run(["rclone", "lsjson", "--hash", "--files-only", "--drive-root-folder-id", folder,
             f"{remote}:", "--include", "/" + name.replace("[", "\\[").replace("]", "\\]")])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        raise RuntimeError("rclone の一覧を読めない")
    for e in items if isinstance(items, list) else []:
        if isinstance(e, dict) and e.get("Name") == name:
            return (e.get("Hashes") or {}).get("md5", "")
    return None


def candidates(repo: Path, branches: list[str]) -> list[tuple[str, str, bytes]]:
    out = []
    for f in sorted((repo / ".agent-logs/pending").glob("*.md")):
        out.append(("worktree", f.name, f.read_bytes()))
    for g in branches:
        refs = run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short)", f"refs/remotes/{g}"]).stdout.split()
        for b in refs:
            if b.endswith("/HEAD") or "/" not in b:
                continue
            ls = run(["git", "-C", str(repo), "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", b, "--",
                      ".agent-logs/pending/"]).stdout.splitlines()
            for path in ls:
                if path.endswith(".md"):
                    data = subprocess.run(["git", "-C", str(repo), "show", f"{b}:{path}"], capture_output=True).stdout
                    out.append((b, os.path.basename(path), data))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--branches", default="origin", help="前方一致で見るリモートブランチ(空白区切り)")
    a = ap.parse_args()
    problem = load_folders(Path(a.config).expanduser())
    if problem:
        sys.exit(problem)
    if DENY & set(FOLDERS.values()):
        sys.exit("拒否フォルダ(ミラー等)は置き先にできない")
    remote = os.environ.get("RCLONE_REMOTE", "drive")
    problem = check_not_under_deny(remote)
    if problem:
        sys.exit(problem)
    ledger_p = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agent-kit" / "collected.json"
    ledger_p.parent.mkdir(parents=True, exist_ok=True)
    try:
        ledger = json.loads(ledger_p.read_text()) if ledger_p.exists() else {}
        if not isinstance(ledger, dict):
            raise ValueError("形が違う")
        bad = [k for k, v in ledger.items() if not (isinstance(v, dict) and isinstance(v.get("md5"), str) and v["md5"])]
        if bad:
            raise ValueError(f"md5 の無い記録が {len(bad)} 件({', '.join(bad[:3])})")
    except (OSError, ValueError, RecursionError) as e:
        sys.exit(f"回収の台帳 {ledger_p} を読めない({e})。重複を防げないので何も置かない。台帳を確かめる")
    placed = dup = conflict = skipped = failed = 0
    for repo_s in a.repos:
        repo = Path(repo_s).expanduser().resolve()
        if not (repo / ".git").exists():
            print(f"skip(git リポジトリでない): {repo}")
            continue
        if run(["git", "-C", str(repo), "fetch", "--quiet", "--prune", "origin"]).returncode != 0:
            print(f"fetch 失敗(手元の情報で続行): {repo}")
        seen = set()
        for ref, name, data in candidates(repo, a.branches.split()):
            text = data.decode("utf-8", "replace")
            m_id, m_src = UUID_RE.search(text), SRC_RE.search(text)
            if "kind: \"agent-worklog\"" not in text and "kind: agent-worklog" not in text or not m_id or not m_src:
                print(f"skip(作業ログ v2 の形式でない): {name}")
                skipped += 1
                continue
            log_id, src = m_id.group(1), m_src.group(1)
            if f"-{src}-" not in name:
                print(f"skip(ファイル名と source が食い違う): {name}")
                skipped += 1
                continue
            md5 = hashlib.md5(data).hexdigest()
            if log_id in seen:
                continue
            seen.add(log_id)
            prev = ledger.get(log_id)
            if prev:
                if isinstance(prev, dict) and prev.get("md5") == md5:
                    dup += 1
                else:
                    print(f"conflict(同じ log_id で内容が違う。置かない): {name} ({ref})")
                    conflict += 1
                continue
            folder = FOLDERS[src]
            if a.dry_run:
                print(f"[dry-run] {ref}:{name} → {src} フォルダ {folder}")
                placed += 1
                continue
            try:
                cur = remote_md5(remote, folder, name)
                if cur is not None:
                    if cur == md5:
                        ledger[log_id] = {"md5": md5, "name": name, "status": "duplicate"}
                        dup += 1
                    else:
                        print(f"conflict(Drive に同名で内容の違うファイル。上書きしない): {name}")
                        conflict += 1
                    continue
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / name
                    p.write_bytes(data)
                    r = run(["rclone", "copyto", "--ignore-existing", "--drive-root-folder-id", folder, str(p), f"{remote}:{name}"])
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.strip()[:200])
                if remote_md5(remote, folder, name) != md5:
                    raise RuntimeError("置いた後の確認で md5 が一致しない")
                ledger[log_id] = {"md5": md5, "name": name, "status": "placed"}
                print(f"placed: {name} ({ref} → {src})")
                placed += 1
            except Exception as e:
                print(f"失敗: {name}: {e}")
                failed += 1
            finally:
                ledger_p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
    print(f"置いた {placed} / 済み {dup} / 衝突 {conflict} / 対象外 {skipped} / 失敗 {failed}")
    return 0 if failed == 0 and conflict == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
