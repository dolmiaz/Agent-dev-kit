#!/usr/bin/env python3
"""publish-check — 公開する前に、個人の値・秘密・テストログが残っていないか調べる(読み取りのみ)。

  python3 tools/publish-check.py <公開するディレクトリ> [--extra 個人の文字列の一覧] [--history]

見るもの
- 一般的な手掛かり: example 以外のメールアドレス、Google Drive の ID らしい文字列、/Users/<名前>、
  Apps Script の公開 URL、秘密鍵・トークンの形
- --extra のファイル(正規表現を1行1つ。例: personal/publish-deny.local.txt)に書いた個人の文字列
- 公開しないファイル: personal/、reports/、*.log、.agent-logs/、.env 系、skills.lock*.json
- 入っていてはいけないディレクトリ: 入れ子の .git、node_modules、.venv、__pycache__
- 中身を点検できないファイル(NUL を含む)は、--allow-binary に書いたもの以外を一覧に出す
- 個人の文字列は大文字・小文字を区別せずに探す
- --history: そのディレクトリが git リポジトリなら、履歴(全コミットの内容)も同じ規則で調べる
  浅い clone では全履歴を読めないため、未検査として不合格にする

見つかったら一覧を出して終了コード 1。何も無ければ 0。見つからないことは「安全の証明」ではない。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# users.noreply.github.com は GitHub のユーザー名を含むので許さない
ALLOWED_EMAIL = re.compile(r"@(example\.(com|org|net)|[\w-]+\.invalid|anthropic\.com)$", re.I)
GENERIC = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")),
    ("drive-id", re.compile(r"(?<![\w-])1[A-Za-z0-9_-]{32}(?![\w-])")),
    ("home-path", re.compile(r"/(?:Users|home)/(?!alice\b|you\b|user\b|USER\b|runner\b|<)[A-Za-z0-9._-]+")),
    ("apps-script-url", re.compile(r"script\.google(?:usercontent)?\.com/macros/s/[A-Za-z0-9_-]{20,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("token", re.compile(r"\b(AIza[A-Za-z0-9_-]{35}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|xox[abpr]-[A-Za-z0-9-]{10,})")),
]
EXCLUDE_PATHS = re.compile(r"(^|/)(personal|reports|\.agent-logs)(/|$)|\.log$|(^|/)\.env(\.|$)|(^|/)\.envrc$|skills\.lock(\.prev)?\.json$")
FLAG_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}   # 公開用の写しに入っていてはいけない


def load_extra(path: str | None) -> list[tuple[str, re.Pattern]]:
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(("personal", re.compile(line, re.I)))
    return out


def scan_text(rel: str, text: str, rules) -> list[str]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for kind, pat in rules:
            for m in pat.finditer(line):
                v = m.group(0)
                if kind == "email" and ALLOWED_EMAIL.search(v):
                    continue
                hits.append(f"{rel}:{i}: {kind}: [REDACTED]")
    return hits


def scan_tree(root: Path, rules, allow_binary: set[str]) -> list[str]:
    hits = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        parts = p.relative_to(root).parts
        if parts[0] == ".git" and len(parts) >= 1:      # 一番上の .git は --history で調べる
            continue
        if any(part in FLAG_DIRS for part in parts):
            top = "/".join(parts[:[i for i, x in enumerate(parts) if x in FLAG_DIRS][0] + 1])
            msg = f"{top}/: 公開用の写しに入れないディレクトリ"
            if msg not in hits:
                hits.append(msg)
            continue
        if p.is_symlink():
            hits.append(f"{rel}: シンボリックリンク(リンク先は非表示)。公開用の写しには実体だけを入れる")
            continue
        if EXCLUDE_PATHS.search(rel):
            parent_flagged = any(h.split(": ", 1)[0].endswith("/") and rel.startswith(h.split(": ", 1)[0]) for h in hits)
            if not parent_flagged:
                hits.append(f"{rel}{'/' if p.is_dir() else ''}: 公開しないファイル・ディレクトリ")
            continue
        if not p.is_file():
            continue
        data = p.read_bytes()
        if b"\0" in data[:65536]:
            if rel not in allow_binary:
                hits.append(f"{rel}: 中身を点検できないファイル(バイナリ・UTF-16 等)。確かめたら --allow-binary に書く")
            continue
        hits += scan_text(rel, data.decode("utf-8", errors="replace"), rules)
    return hits


def scan_history(root: Path, rules) -> list[str]:
    if not (root / ".git").exists():
        return []

    def git(*args: str) -> bytes:
        # Never echo stderr: it can contain private paths or repository data.
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
        if result.returncode:
            raise RuntimeError(args[0])
        return result.stdout

    hits, seen = [], set()
    try:
        if git("rev-parse", "--is-shallow-repository").strip() != b"false":
            return ["history: shallow clone のため全履歴が未検査。公開前に全履歴を取得してください"]
        revs = git("rev-list", "--all").decode().split()
        for rev in revs:
            files = git("ls-tree", "-r", "--name-only", "-z", rev).decode().split("\0")
            for f in filter(None, files):
                blob = git("rev-parse", f"{rev}:{f}").decode().strip()
                if EXCLUDE_PATHS.search(f):
                    hits.append(f"history {rev[:8]}:{f}: 公開しないファイル")
                    continue
                if blob in seen:
                    continue
                seen.add(blob)
                data = git("cat-file", "-p", blob)
                # Binary history cannot be certified by a text-only scan. Fail
                # closed even if the file was deleted from the current tree.
                if b"\0" in data:
                    hits.append(f"history {rev[:8]}:{f}: 履歴のバイナリは未検査。公開前に履歴を除去または別途検証が必要")
                    continue
                hits += [f"history {rev[:8]}:{h}" for h in scan_text(f, data.decode("utf-8", errors="replace"), rules)]
            msg = git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", rev).decode("utf-8", errors="replace")
            hits += [f"history {rev[:8]}:commit-message/author: {h.split(': ', 1)[-1]}" for h in scan_text("msg", msg, rules)]
    except (OSError, UnicodeError, RuntimeError):
        hits.append("history: Git 履歴の読み取りに失敗。未検査のため公開不可（詳細は非表示）")
    return sorted(set(hits))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--extra")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--allow-binary", action="append", default=[], help="確かめたバイナリ(公開する写しの中の相対パス)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    rules = GENERIC + load_extra(a.extra)
    hits = scan_tree(root, rules, set(a.allow_binary)) + (scan_history(root, rules) if a.history else [])
    for h in hits:
        print(h)
    print(f"publish-check: {'問題なし(見つからなかっただけで、安全の証明ではない)' if not hits else f'{len(hits)} 件'}", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
