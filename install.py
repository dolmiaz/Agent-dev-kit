#!/usr/bin/env python3
"""install.py — agent-kit を対象リポジトリへ入れる / 更新する / 外す。

  python3 install.py <repo> [--only codex|claude] [--github-templates] [--dry-run]
  python3 install.py <repo> --uninstall [--dry-run]

入れるもの(すべて install-manifest.json に記録し、外すときはそれだけを対象にする)
  .agent-kit/                     スクリプト・設定(config.env は既存なら上書きしない)
  .claude/settings.json           hooks だけを追記(許可ルールは足さない。既存の内容は消さない)
  .claude/skills/<kit skill>/     Claude 用 skill(cloud-worklog, issue-task, pr-review)
  .agents/skills/<kit skill>/     Codex 用 skill(同じ内容)
  CLAUDE.md / AGENTS.md           <!-- agent-kit:begin/end --> の区間だけ
  .github/ISSUE_TEMPLATE/agent-task.yml, .github/pull_request_template.md   --github-templates のときだけ、無い場合だけ

安全
- 既にある .agent-kit がキットのもので無ければ(manifest が無ければ)中止する。
- 同名の skill ディレクトリが既にあり、キットが入れたもので無ければ、その skill は入れない。
- 更新時、手で変更されたキットのファイルは上書きしない(報告する)。
- 変更前のファイルはリポジトリの外(~/.local/state/agent-kit/<repo>-<hash>/install-backups/<日時>/)に保存する。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE / "kit" / ".agent-kit"
VERSION = (KIT / "VERSION").read_text(encoding="utf-8").strip() if (KIT / "VERSION").exists() else "dev"
MANIFEST = Path(".agent-kit/install-manifest.json")
BEGIN, END = "<!-- agent-kit:begin", "<!-- agent-kit:end -->"
BLOCK_RE = re.compile(r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
KIT_SKILLS = ("cloud-worklog", "issue-task", "pr-review", "publish-polish")
SKILL_DIRS = {"claude": ".claude/skills", "codex": ".agents/skills"}
GH_TEMPLATES = {".github/ISSUE_TEMPLATE/agent-task.yml": "github/ISSUE_TEMPLATE/agent-task.yml",
                ".github/pull_request_template.md": "github/pull_request_template.md"}
SKIP = {"__pycache__", ".DS_Store"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def backup_root(repo: Path) -> Path:
    """変更前のファイルの退避先。リポジトリの外(状態ディレクトリ)に置き、Git にも .gitignore にも触れない"""
    import os
    real = os.path.realpath(repo)
    key = f"{re.sub(r'[^A-Za-z0-9._-]', '_', os.path.basename(real))[:40]}-{hashlib.sha256(real.encode()).hexdigest()[:12]}"
    base = os.environ.get("AGENT_STATE_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.join(Path.home(), ".local", "state"), "agent-kit")
    return Path(base) / key / "install-backups"


class Plan:
    def __init__(self, repo: Path, dry: bool):
        self.repo, self.dry, self.log = repo, dry, []
        self.stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.backup_dir = backup_root(repo) / self.stamp
        self.created_dirs: set[str] = set()

    def backup(self, rel: str) -> None:
        src = self.repo / rel
        if self.dry or not src.exists():
            return
        dst = self.backup_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    def write_bytes(self, rel: str, data: bytes, mode: int | None = None) -> None:
        p = self.repo / rel
        if p.exists() and p.read_bytes() == data:
            return
        self.log.append(("update" if p.exists() else "create", rel))
        if not self.dry:
            d = p.parent
            while d != self.repo and not d.exists():       # キットが作るディレクトリを記録(取り外しで消してよいもの)
                self.created_dirs.add(d.relative_to(self.repo).as_posix())
                d = d.parent
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            if mode:
                p.chmod(mode)

    def remove(self, rel: str) -> None:
        self.log.append(("delete", rel))
        if not self.dry:
            p = self.repo / rel
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()

    def note(self, what: str, rel: str) -> None:
        self.log.append((what, rel))


def load_manifest(repo: Path) -> dict | None:
    p = repo / MANIFEST
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def kit_files() -> list[tuple[str, Path]]:
    out = []
    for src in sorted(KIT.rglob("*")):
        if src.is_dir() or any(part in SKIP for part in src.parts):
            continue
        out.append((".agent-kit/" + src.relative_to(KIT).as_posix(), src))
    return out


def is_exec(src: Path) -> bool:
    return src.suffix in (".sh", ".py") or src.name in ("agent-log", "agent-gh")


# --- settings.json -----------------------------------------------------------
def hook_commands(group: dict) -> list[str]:
    return [h.get("command", "") for h in group.get("hooks", [])]


def merge_hooks(cur: dict, frag: dict) -> tuple[dict, list[dict]]:
    out = json.loads(json.dumps(cur))
    added = []
    hooks = out.setdefault("hooks", {})
    for ev, groups in frag.get("hooks", {}).items():
        lst = hooks.setdefault(ev, [])
        for g in groups:
            cmds = hook_commands(g)
            if any(set(cmds) & set(hook_commands(eg)) for eg in lst):
                continue
            lst.append(g)
            added.append({"event": ev, "commands": cmds})
    return out, added


def unmerge_hooks(cur: dict, added: list[dict]) -> dict:
    """キットが足したフックの要素だけを外す。本人が同じグループに足したフックは残す"""
    out = json.loads(json.dumps(cur))
    hooks = out.get("hooks", {})
    for a in added:
        groups = []
        for g in hooks.get(a["event"], []):
            g["hooks"] = [h for h in g.get("hooks", []) if h.get("command", "") not in a["commands"]]
            if g["hooks"]:
                groups.append(g)
        if groups:
            hooks[a["event"]] = groups
        else:
            hooks.pop(a["event"], None)
    if not hooks:
        out.pop("hooks", None)
    return out


# --- install -----------------------------------------------------------------
def install(repo: Path, only: str | None, gh_templates: bool, dry: bool) -> list:
    pl = Plan(repo, dry)
    man = load_manifest(repo)
    if (repo / ".agent-kit").exists() and man is None:
        sys.exit("中止: .agent-kit が既にあるが、このキットの manifest が無い(別物の可能性)。手で確認してください")
    prev_files = (man or {}).get("files", {})
    new = {"kit_version": VERSION, "installed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "targets": [t for t in ("claude", "codex") if only in (None, t)], "files": {}, "kept_modified": [],
           "settings": (man or {}).get("settings", {"created": False, "added": []}),
           "blocks": (man or {}).get("blocks", []), "gitignore_added": (man or {}).get("gitignore_added", False),
           "gitignore_created": (man or {}).get("gitignore_created", False)}
    for rel in (".agent-kit", ".claude/settings.json", "CLAUDE.md", "AGENTS.md", ".gitignore", ".github"):
        pl.backup(rel)

    def place(rel: str, src: Path, keep_existing: bool = False) -> None:
        p = repo / rel
        if p.exists():
            cur = sha(p)
            if keep_existing:
                new["files"][rel] = prev_files.get(rel, cur)
                return
            if rel in prev_files and cur != prev_files[rel] and cur != sha(src):
                pl.note("keep(手で変更あり)", rel)
                new["kept_modified"].append(rel)
                new["files"][rel] = prev_files[rel]
                return
            if rel not in prev_files and cur != sha(src):
                pl.note("skip(キット以外の既存ファイル)", rel)
                return
        pl.write_bytes(rel, src.read_bytes(), 0o755 if is_exec(src) else None)
        new["files"][rel] = sha(src)

    for rel, src in kit_files():
        if rel == ".agent-kit/install-manifest.json":
            continue
        place(rel, src, keep_existing=(rel == ".agent-kit/config.env"))

    for t in new["targets"]:
        for s in KIT_SKILLS:
            d = f"{SKILL_DIRS[t]}/{s}"
            mine = any(k.startswith(d + "/") for k in prev_files)
            if (repo / d).exists() and not mine:
                pl.note("skip(同名の skill が既にある)", d)
                continue
            for src in sorted((KIT / "skills" / s).rglob("*")):
                if src.is_file():
                    place(f"{d}/{src.relative_to(KIT / 'skills' / s).as_posix()}", src)

    if "claude" in new["targets"]:
        sp = repo / ".claude/settings.json"
        frag = json.loads((KIT / "claude/settings.fragment.json").read_text(encoding="utf-8"))
        cur = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        if not sp.exists():
            new["settings"]["created"] = True
        merged, added = merge_hooks(cur, frag)
        if added:
            pl.write_bytes(".claude/settings.json", (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode())
            new["settings"]["added"] += added
    for name, t in (("CLAUDE.md", "claude"), ("AGENTS.md", "codex")):
        if t not in new["targets"]:
            continue
        p = repo / name
        existed = p.exists()
        block = (KIT / "snippets" / name).read_text(encoding="utf-8").strip() + "\n"
        doc = p.read_text(encoding="utf-8") if existed else ""
        if BLOCK_RE.search(doc):
            doc2 = BLOCK_RE.sub(lambda _m: "\n\n" + block, doc, count=1).lstrip("\n")
        else:
            doc2 = (doc.rstrip("\n") + "\n\n" if doc.strip() else "") + block
        if doc2 != doc:
            pl.write_bytes(name, doc2.encode())
        if not any(b["file"] == name for b in new["blocks"]):
            new["blocks"].append({"file": name, "created": not existed})
    if gh_templates:
        for rel, srcrel in GH_TEMPLATES.items():
            if (repo / rel).exists() and rel not in prev_files:
                pl.note("skip(既存のテンプレートを残す)", rel)
                continue
            place(rel, KIT / srcrel)
    else:
        for rel in GH_TEMPLATES:
            if rel in prev_files:
                new["files"][rel] = prev_files[rel]
    # 前回入れたが今回のキットに無いファイル(古い版の残り)は、変更されていなければ消す
    for rel, h in prev_files.items():
        if rel not in new["files"] and (repo / rel).exists():
            if sha(repo / rel) == h:
                pl.remove(rel)
            else:
                pl.note("keep(古い版のファイルだが手で変更あり)", rel)
    new["created_dirs"] = sorted(set((man or {}).get("created_dirs", [])) | pl.created_dirs)
    changed = pl.log or man is None or {k: v for k, v in new.items() if k != "installed_at"} != \
        {k: v for k, v in (man or {}).items() if k != "installed_at"}
    if not dry and changed:
        (repo / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
        (repo / MANIFEST).write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pl.log


def uninstall(repo: Path, dry: bool) -> list:
    pl = Plan(repo, dry)
    man = load_manifest(repo)
    if man is None:
        sys.exit("manifest が無い(このキットで入れていない)。何もしない")
    for rel in (".agent-kit", ".claude/settings.json", "CLAUDE.md", "AGENTS.md", ".gitignore", ".github"):
        pl.backup(rel)
    for rel, h in man.get("files", {}).items():
        p = repo / rel
        if not p.exists():
            continue
        if sha(p) == h:
            pl.remove(rel)
        else:
            pl.note("keep(手で変更あり)", rel)
    sp = repo / ".claude/settings.json"
    st = man.get("settings", {})
    if sp.exists() and st.get("added"):
        cur = json.loads(sp.read_text(encoding="utf-8"))
        new = unmerge_hooks(cur, st["added"])
        if st.get("created") and not new:
            pl.remove(".claude/settings.json")
        elif new != cur:
            pl.write_bytes(".claude/settings.json", (json.dumps(new, ensure_ascii=False, indent=2) + "\n").encode())
    for b in man.get("blocks", []):
        p = repo / b["file"]
        if p.exists():
            doc = p.read_text(encoding="utf-8")
            rest = BLOCK_RE.sub("\n", doc).strip("\n")
            if not rest and b.get("created"):
                pl.remove(b["file"])
            elif rest + "\n" != doc:
                pl.write_bytes(b["file"], (rest + "\n").encode())
    gi = repo / ".gitignore"
    if man.get("gitignore_added") and gi.exists():
        lines = [l for l in gi.read_text(encoding="utf-8").splitlines() if l != ".agent-kit-backup/"]
        if not lines and man.get("gitignore_created"):
            pl.remove(".gitignore")
        else:
            pl.write_bytes(".gitignore", ("\n".join(lines) + "\n" if lines else "").encode())
    pl.remove(str(MANIFEST))
    if not dry:   # キットが作ったディレクトリのうち、空になったものだけ消す(もとからあったディレクトリは残す)
        for d in sorted(man.get("created_dirs", []), key=lambda x: -x.count("/")):
            p = repo / d
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
    return pl.log


PROFILE_LINE = re.compile(r"^\s*(AGENT_[A-Z0-9_]+)\s*=(.*)$")


def apply_profile(cfg: Path, profile: Path, dry: bool) -> list[tuple[str, str]]:
    """profile の AGENT_ キーで config.env の同じキーの行を置き換える(無ければ末尾に足す)。他の行は変えない。"""
    vals = {}
    for line in profile.read_text(encoding="utf-8").splitlines():
        m = PROFILE_LINE.match(line.rstrip("\r"))
        if m and not line.lstrip().startswith("#"):
            vals[m.group(1)] = m.group(2).strip()
    lines = cfg.read_text(encoding="utf-8").splitlines() if cfg.exists() else []
    done, out = set(), []
    for line in lines:
        m = PROFILE_LINE.match(line)
        if m and m.group(1) in vals and not line.lstrip().startswith("#"):
            out.append(f"{m.group(1)}={vals[m.group(1)]}")
            done.add(m.group(1))
        else:
            out.append(line)
    extra = [k for k in vals if k not in done]
    if extra:
        out += ["", f"# --- 個人設定({profile.name}) ---"] + [f"{k}={vals[k]}" for k in extra]
    new = "\n".join(out) + "\n"
    if cfg.exists() and new == cfg.read_text(encoding="utf-8"):
        return []
    if not dry:
        cfg.write_text(new, encoding="utf-8")
    print("注: 個人設定の値は導入先の .agent-kit/config.env(コミットされる)に入る。導入先が公開リポジトリなら値も公開される",
          file=sys.stderr)
    return [("profile", f"{cfg.name}: {', '.join(sorted(vals))}")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--only", choices=["codex", "claude"])
    ap.add_argument("--github-templates", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profile", help="個人設定(AGENT_KEY=value の行)。導入先の .agent-kit/config.env の同じキーを置き換える"
                                     "(例: personal/config.personal.env。キットの公開版には含めない)")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    if not (repo / ".git").exists():
        print(f"git リポジトリではない: {repo}", file=sys.stderr)
        return 2
    log = uninstall(repo, a.dry_run) if a.uninstall else install(repo, a.only, a.github_templates, a.dry_run)
    if a.profile and not a.uninstall:
        log += apply_profile(repo / ".agent-kit" / "config.env", Path(a.profile), a.dry_run)
    for act, p in log:
        print(f"{'[dry-run] ' if a.dry_run else ''}{act}: {p}")
    if not log:
        print("変更なし(すでに最新)")
    elif not a.dry_run:
        print(f"変更前のファイルの退避先: {backup_root(repo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
