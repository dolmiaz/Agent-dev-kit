#!/usr/bin/env python3
"""PreToolUse hook(Claude Code on the web 専用の許可判定)。

方針(独立レビューを受けて作り直した版):
- クラウド(CLAUDE_CODE_REMOTE=true)で、AGENT_CLOUD_AUTO_ALLOW=1 のときだけ判定を返す。ローカルでは何も返さない。
  (AGENT_HOOKS_LOCAL=1 はテスト用)
- **allow は厳しいホワイトリスト**: コマンドとサブコマンドと使ってよいオプションを明示し、語の配列で照合する。
  少しでも外れたら allow しない(=判定しない。UI で選んだモード Auto / Accept edits に任せる)。
- deny / ask は危険な形の検出(できるだけ広く)。ただし完全な防御ではない。
  npm test・pytest・node script.js のような「コードを実行する」コマンドを allow すると、そのコードが行う操作も
  通ってしまう。最終的な防御は GitHub 側の保護(main の保護・force push 禁止)と Claude のプロキシ(ブランチ削除の拒否)。
- 判定の強さ: deny > ask > allow。設定ファイルの deny / ask はこの hook の allow より優先される(Claude Code の仕様)。
"""
from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
from pathlib import Path

CONTROL = {";", "&", "&&", "||", "|", "|&"}
REDIRECT = re.compile(r"^\d*(>>?|>\||>&|<>|&>>?)$")
UNSAFE_CHARS = re.compile(r"[$`{}~]|(^|/)\.\.(/|$)")   # 展開・置換・親ディレクトリ(..)→ allow しない
GLOB = re.compile(r"[*?\[]")
PROTECTED = [".agent-kit/claude/*", ".agent-kit/config.env", ".agent-kit/lib/*", ".agent-kit/bin/*",
             ".claude/*", ".git/hooks/*", ".git/config", ".github/workflows/*"]
PROTECTED_DIRS = {".claude", ".agent-kit", ".agent-kit/claude", ".agent-kit/bin", ".agent-kit/lib",
                  ".git", ".git/hooks", ".github", ".github/workflows"}
SECRET_OK = {".env.example", ".env.sample", ".env.template", ".env.dist"}
READ_ONLY = {"cat", "head", "tail", "ls", "grep", "rg", "wc", "diff", "less", "jq", "file", "stat"}
DEPLOY_WORDS = re.compile(r"(^|[:_-])(publish|deploy|release|upload|login|logout|adduser|unpublish|dist-tag|owner|token|push)($|[:_-])", re.I)
SAFE_NAME = re.compile(r"^[A-Za-z0-9._/@+-]+$")


# --- 設定 --------------------------------------------------------------------
def parse_config(path) -> dict:
    """config.env を読む(シェルの load_config と同じ規則: 空白・CRLF・行末コメント・引用符)"""
    out = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if not re.fullmatch(r"AGENT_[A-Z0-9_]+", k):
            continue
        if v[:1] in ("'", '"') and v[0] in v[1:]:
            v = v[1:v.index(v[0], 1)]
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        out[k] = v
    return out


def cfg(root: Path, key: str, default: str) -> str:
    if key in os.environ:
        return os.environ[key]
    return parse_config(root / ".agent-kit" / "config.env").get(key, default)


def load_rules(root: Path) -> dict:
    try:
        return json.loads((root / ".agent-kit" / "claude" / "cloud-permissions.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


# --- 字句解析とパス -------------------------------------------------------------
def tokenize(cmd: str) -> list[str] | None:
    lx = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>")
    lx.whitespace_split = True
    lx.commenters = ""
    try:
        return list(lx)
    except ValueError:
        return None


def split_segments(tokens: list[str]) -> tuple[list[list[str]], list[str]]:
    """制御演算子で区切った語の列と、リダイレクト先の一覧"""
    segs, cur, targets = [], [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in CONTROL:
            if cur:
                segs.append(cur)
            cur = []
        elif REDIRECT.match(t) or t in ("<", "<<", "<<<"):
            if i + 1 < len(tokens):
                targets.append(tokens[i + 1])
                if not REDIRECT.match(t):
                    pass
            i += 1
        else:
            cur.append(t)
        i += 1
    if cur:
        segs.append(cur)
    return segs, targets


def norm(p: str) -> str:
    p = p.strip()
    if not p:
        return p
    n = posixpath.normpath(p)
    while n.startswith("./"):
        n = n[2:]
    return n


def path_candidates(word: str) -> list[str]:
    """語に含まれうるパス(--opt=値、rev:path の後ろも含める)"""
    out = [word]
    if "=" in word and word.startswith("-"):
        out.append(word.split("=", 1)[1])
    if ":" in word and not word.startswith("-") and "://" not in word:
        out.append(word.split(":", 1)[1])
    root = os.environ.get("CLAUDE_PROJECT_DIR", "")
    res = []
    for c in out:
        if root and c.startswith(root + "/"):
            c = c[len(root) + 1:]
        res.append(c)
    return res


def is_protected(word: str) -> bool:
    for c in path_candidates(word):
        n = norm(c)
        if not n:
            continue
        if n in PROTECTED_DIRS or n.rstrip("/") in PROTECTED_DIRS:
            return True
        if any(fnmatch.fnmatchcase(n, p) or fnmatch.fnmatchcase(n, "*/" + p) for p in PROTECTED):
            return True
        if (GLOB.search(n) or "{" in n) and any(n.startswith(d[:3]) for d in (".cl", ".ag", ".gi")):
            return True
    return False


def is_secret(word: str, pats: list[str]) -> bool:
    for c in path_candidates(word):
        n = norm(c)
        base = n.rstrip("/").split("/")[-1]
        if not base or base in SECRET_OK or GLOB.search(base):
            continue
        if any(fnmatch.fnmatchcase(base, p) or fnmatch.fnmatchcase(n, p) or fnmatch.fnmatchcase(n, "*/" + p) for p in pats):
            return True
    return False


# --- 危険の検出(deny / ask) -----------------------------------------------------
def danger(w: list[str], rules: dict) -> tuple[str, str] | None:
    """語の列から危険な形を探す(包み・スクリプトの中身も含め、見える範囲で)"""
    text = " ".join(w)
    for t in w:
        if is_secret(t, rules.get("deny_read", [])):
            return "deny", f"秘密情報らしいファイル({t})はクラウドでは扱わない"
    p = next((p for p in rules.get("deny", []) if fnmatch.fnmatchcase(text, p)), None)
    if p:
        return "deny", f"クラウドでは禁止の操作({p})"
    # git の force push・削除・履歴の書き換え(語のどこにあっても)
    if "git" in w or any(os.path.basename(x) == "git" for x in w):
        gi = next(i for i, x in enumerate(w) if os.path.basename(x) == "git")
        g = [x for x in w[gi + 1:]]
        # git -c / -C を外してサブコマンドを得る
        while g and g[0] in ("-c", "-C", "--git-dir", "--work-tree", "--exec-path"):
            g = g[2:]
        sub = g[0] if g else ""
        opts = g[1:]
        if sub == "push":
            for o in opts:
                name = o.split("=", 1)[0]
                if o.startswith("--") and (name.startswith("--force") or (len(name) >= 4 and any(
                        full.startswith(name) for full in ("--force", "--force-with-lease", "--force-if-includes",
                                                           "--mirror", "--delete", "--prune")))):
                    return "deny", f"force push・ブランチ削除はしない({o})"
                if re.match(r"^-[A-Za-z]+$", o) and set(o[1:]) & set("fd"):
                    return "deny", f"force push・ブランチ削除はしない({o})"
                if o.startswith("+") or (o.startswith(":") and len(o) > 1):
                    return "deny", f"force push・ブランチ削除はしない({o})"
        if sub == "branch":
            for o in opts:
                if o in ("-d", "-D") or (re.match(r"^-[A-Za-z]+$", o) and set(o[1:]) & set("dD")) or \
                        (o.startswith("--d") and "--delete".startswith(o.split("=")[0])):
                    return "deny", "ブランチの削除はしない"
        if sub == "tag" and any(o in ("-d", "--delete") or (o.startswith("--d") and "--delete".startswith(o)) for o in opts):
            return "deny", "タグの削除はしない"
        if sub in ("filter-repo", "filter-branch"):
            return "deny", "履歴の書き換えはしない"
    cmd0 = os.path.basename(w[0])
    writers = {"cp", "mv", "rm", "ln", "install", "rsync", "chmod", "chown", "truncate", "dd", "sed", "perl", "tee",
               "sort", "uniq", "patch", "touch", "mkdir", "unzip", "tar", "cat"}
    if cmd0 in writers - {"cat"} and any(is_protected(t) for t in w[1:]):
        return "ask", "権限・hook・CI・設定のファイルの変更は確認が必要"
    if any(t.startswith("--output") and is_protected(t) for t in w):
        return "ask", "権限・hook・CI・設定のファイルへの書き込みは確認が必要"
    if cmd0 == "git":
        g = [x for x in w[1:] if not x.startswith("-")]
        if g and g[0] in ("mv", "rm", "checkout", "restore", "apply") and any(is_protected(t) for t in w[2:]):
            return "ask", "権限・hook・CI・設定のファイルの変更は確認が必要"
    if any(DEPLOY_WORDS.search(t) for t in w[1:]) and os.path.basename(w[0]) in (
            "npm", "pnpm", "yarn", "bun", "npx", "bunx", "cargo", "uv", "poetry", "make", "twine", "gem", "docker",
            "python", "python3", "pip", "gh", "deno", "vercel", "netlify", "wrangler", "firebase", "fly", "flyctl"):
        return "ask", "公開・デプロイ・リリース・ログインに関わる操作は確認が必要"
    p = next((p for p in rules.get("ask", []) if fnmatch.fnmatchcase(text, p)), None)
    if p:
        return "ask", f"確認が必要な操作({p})"
    return None


# --- allow のホワイトリスト -------------------------------------------------------
def opts_ok(opts: list[str], allowed: set[str], with_value: set[str] = frozenset()) -> bool:
    i = 0
    while i < len(opts):
        o = opts[i]
        if o in with_value:
            i += 2
            continue
        if o.startswith("-") and o not in allowed and o.split("=", 1)[0] not in allowed:
            return False
        i += 1
    return True


def safe_paths(args: list[str]) -> bool:
    return all(not a.startswith("-") or a == "--" for a in args) is not None and \
        all(not is_protected(a) for a in args)


GIT_RULES = {
    "status": ({"-s", "-sb", "-b", "--short", "--branch", "--porcelain", "--porcelain=v1", "--porcelain=v2", "-u", "-uno", "--untracked-files", "--untracked-files=no", "--untracked-files=normal", "--untracked-files=all"}, set()),
    "diff": ({"--stat", "--name-only", "--name-status", "--cached", "--staged", "--numstat", "--shortstat", "--no-color", "--color", "-p", "--patch", "-U0", "-U3", "--word-diff", "--"}, set()),
    "log": ({"--oneline", "--stat", "--graph", "--decorate", "--no-color", "-p", "--patch", "--name-only", "--name-status", "-1", "-5", "-10", "-20", "-n", "--max-count", "--format", "--pretty", "--since", "--until", "--author", "--first-parent", "--reverse", "--all", "--"}, {"-n"}),
    "show": ({"--stat", "--name-only", "--name-status", "--oneline", "--no-color", "--format", "--pretty", "-p", "--"}, set()),
    "add": ({"-A", "--all", "-u", "--update", "--", "-v"}, set()),
    "commit": ({"-m", "-a", "-am", "-s", "--signoff", "-q", "--quiet", "--message", "-v", "--allow-empty"}, set()),
    "switch": ({"-c", "--create"}, set()),
    "checkout": ({"-b"}, set()),
    "fetch": ({"--all", "--prune", "-p", "--quiet", "-q", "--tags", "--no-tags", "--depth", "--unshallow"}, set()),
    "pull": ({"--rebase", "--ff-only", "--no-rebase", "--quiet", "-q", "--autostash"}, set()),
    "rebase": ({"--continue", "--abort", "--skip", "--autostash", "--onto"}, set()),
    "restore": ({"--staged", "-S", "--worktree", "-W", "--"}, set()),
    "rm": ({"-r", "--cached", "-q", "--quiet", "--"}, set()),
    "mv": ({"-v", "--"}, set()),
    "ls-files": ({"-m", "-o", "-c", "--others", "--modified", "--cached", "--exclude-standard", "-z"}, set()),
    "rev-parse": ({"--abbrev-ref", "--short", "--show-toplevel", "--verify", "--git-dir", "HEAD", "-q", "--quiet"}, set()),
    "merge-base": ({"--is-ancestor"}, set()),
}


def allow_git(g: list[str]) -> str | None:
    if not g or g[0].startswith("-"):
        return None                                  # git -c / -C など(alias・pager の差し替えに使える)は allow しない
    sub, args = g[0], g[1:]
    if sub == "push":
        if args[:1] in (["-u"], ["--set-upstream"]):
            args = args[1:]
        if len(args) == 2 and args[0] == "origin" and args[1].startswith("feature/") and SAFE_NAME.match(args[1]) \
                and ":" not in args[1] and not args[1].endswith("/") and "//" not in args[1]:
            return "作業ブランチ(feature/)への push"
        return None
    if sub == "branch":
        if all(a in ("-a", "-v", "-vv", "-r", "--show-current", "--list") for a in args):
            return "ブランチの一覧"
        if len(args) == 1 and SAFE_NAME.match(args[0]) and not args[0].startswith("-"):
            return "ブランチの作成"
        return None
    if sub == "stash":
        if not args or args[0] in ("list", "show", "pop", "apply") or (args[0] == "push" and opts_ok(args[1:], {"-m", "--message"})):
            return "stash"
        return None
    if sub in ("checkout", "switch"):
        pos = [a for a in args if not a.startswith("-")]
        if "--" in args or len(pos) != 1 or not SAFE_NAME.match(pos[0]):
            return None                              # ファイルを別の版で上書きする形は allow しない
    if sub == "rebase" and [a for a in args if not a.startswith("-")][1:]:
        return None
    if sub in ("restore", "rm", "mv", "add") and any(is_protected(a) for a in args):
        return None
    if sub == "commit":
        # -m の値は自由な文字列なので飛ばす
        rest, i = [], 0
        while i < len(args):
            if args[i] in ("-m", "-am", "--message"):
                i += 2
                continue
            if args[i].startswith("--message="):
                i += 1
                continue
            rest.append(args[i])
            i += 1
        return "コミット" if opts_ok(rest, GIT_RULES["commit"][0]) and all(r.startswith("-") for r in rest) else None
    rule = GIT_RULES.get(sub)
    if not rule:
        return None
    allowed, with_value = rule
    def base(o):
        return o.split("=", 1)[0] if o.startswith("--") else o
    for o in args:
        if o.startswith("-") and o not in allowed and base(o) not in allowed and not re.match(r"^-\d+$", o):
            return None
    return f"git {sub}"


PM = {"npm", "pnpm", "yarn", "bun"}
PM_ALLOWED_SUB = {"ci", "install", "i", "test", "t", "run", "add", "build", "lint", "typecheck", "format", "check", "dev"}


def allow_words(w: list[str]) -> str | None:
    if not w or any(UNSAFE_CHARS.search(x) for x in w):
        return None
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]):
        return None                                  # VAR=値 で始まる(PAGER・EDITOR 等の差し替え)
    cmd = w[0]
    if any(GLOB.search(x) for x in w[1:]) and cmd not in ("ls",):
        return None
    if cmd == "git":
        return allow_git(w[1:])
    if cmd in PM:
        sub = w[1] if len(w) > 1 else "install"
        if sub in ("exec", "dlx", "x", "create", "init", "link", "publish", "pack", "config", "set", "login"):
            return None
        if sub == "run" and (len(w) < 3 or DEPLOY_WORDS.search(w[2])):
            return None
        if sub not in PM_ALLOWED_SUB and (cmd == "npm" or DEPLOY_WORDS.search(sub)):
            return None
        if any(x in ("-c", "--call", "-e", "--eval") or x.startswith("--call=") for x in w):
            return None
        return f"{cmd} の通常の操作"
    if cmd in ("pytest", "ruff", "mypy", "black", "tsc", "eslint", "prettier", "jest", "vitest", "go", "cargo",
               "make", "bundle", "composer", "mvn", "./gradlew", "pg_isready", "check-tools"):
        if cmd == "go" and len(w) > 1 and w[1] not in ("build", "test", "vet", "mod", "fmt", "list", "version", "env"):
            return None
        if cmd == "cargo" and len(w) > 1 and (w[1].startswith("+") or w[1] not in ("build", "test", "check", "clippy", "fmt", "fetch", "tree", "metadata")):
            return None
        if cmd == "make" and any(DEPLOY_WORDS.search(x) for x in w[1:]):
            return None
        if cmd == "bundle" and len(w) > 1 and w[1] not in ("install", "check"):
            return None
        return f"{cmd}(ビルド・テスト)"
    if cmd in ("python", "python3"):
        if len(w) > 1 and (w[1].startswith("-c") or w[1] in ("-") or w[1].startswith("-m") and w[1:3] != ["-m", "pytest"] and w[1:3] != ["-m", "venv"] and w[1:3] != ["-m", "pip"]):
            return None
        if w[1:3] == ["-m", "pip"] and (len(w) < 4 or w[3] != "install"):
            return None
        return "python の実行"
    if cmd == "node":
        if len(w) > 1 and (w[1].startswith("-e") or w[1].startswith("-p") or w[1].startswith("--eval") or w[1].startswith("--print") or w[1] == "-"):
            return None
        return "node の実行"
    if cmd == "uv" and len(w) > 1 and w[1] in ("sync", "pip", "lock", "venv", "tree") and (w[1] != "pip" or w[2:3] == ["install"]):
        return "uv"
    if cmd == "poetry" and len(w) > 1 and w[1] in ("install", "check", "show", "lock"):
        return "poetry"
    if cmd in ("bash", "sh") and len(w) >= 2 and not w[1].startswith("-"):
        n = norm(w[1])
        if n.startswith(".agent-kit/bin/") or n.startswith(".agent-kit/codex/") or n == ".agent-kit/bin/setup-deps.sh":
            return "agent-kit のスクリプト"
        return None
    if cmd.startswith(".agent-kit/bin/") or (cmd == "python3" and len(w) > 1 and norm(w[1]).startswith(".agent-kit/bin/")):
        return "agent-kit のスクリプト"
    if cmd in ("ls", "cat", "head", "tail", "wc", "pwd", "which", "true", "diff", "jq", "echo", "test", "file", "stat"):
        return "読み取り"
    if cmd == "grep":
        if any(re.match(r"^-[A-Za-z]*[rR]", x) or x in ("--recursive", "--dereference-recursive") for x in w):
            return None
        return "読み取り"
    if cmd == "rg":
        if any(x in ("--no-ignore", "--hidden", "-u", "-uu", "-uuu", "--unrestricted", "-.") for x in w):
            return None
        return "読み取り"
    if cmd == "sed":
        if len(w) >= 3 and w[1] == "-n" and re.fullmatch(r"\d+(,\d+)?p", w[2]):
            return "読み取り"
        return None
    if cmd in ("mkdir", "touch", "cp", "mv") and not any(is_protected(x) for x in w[1:]):
        return "ファイル操作"
    if cmd in ("psql", "redis-cli", "service") and (cmd != "service" or w[1:2] in (["postgresql"], ["redis-server"])):
        return "開発用サービス"
    if cmd == "docker":
        if len(w) > 1 and w[1] in ("ps", "images", "logs"):
            return "docker の参照"
        if w[1:3] in (["compose", "up"], ["compose", "down"], ["compose", "ps"], ["compose", "logs"], ["compose", "build"]):
            return "docker compose"
        return None
    if cmd == "gh" and w[1:2] == ["api"]:
        args, i, path = w[2:], 0, ""
        while i < len(args):
            a = args[i]
            if a in ("-H", "--header", "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview"):
                i += 2
                continue
            if a in ("--paginate", "--slurp", "-i", "--include", "--silent", "--verbose"):
                i += 1
                continue
            if a.startswith("-"):
                return None                          # -X / --method / -f / -F / --input など
            if path:
                return None
            path = a
            i += 1
        if re.match(r"^/?repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(/[A-Za-z0-9._/-]*)?(\?[A-Za-z0-9=&_.:-]*)?$", path):
            return "gh api の読み取り(GET)"
        return None
    return None


# --- 判定本体 ----------------------------------------------------------------
def unwrap(w: list[str]) -> list[str] | None:
    """包み(xargs・env・timeout・パッケージマネージャの exec 等)や、シェル・評価の中身を返す"""
    cmd = os.path.basename(w[0]) if w else ""
    if cmd in ("xargs", "env", "timeout", "nohup", "sudo", "exec", "command", "nice", "time", "stdbuf", "builtin",
               "doas", "setsid", "watch", "parallel", "npx", "bunx"):
        rest = w[1:]
        while rest and (rest[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0]) or re.match(r"^\d+[smhd]?$", rest[0])):
            rest = rest[1:]
        return rest
    if cmd in ("pnpm", "yarn", "bun", "npm") and len(w) > 2 and w[1] in ("exec", "dlx", "x"):
        return w[2:]
    if cmd in ("uv", "poetry") and len(w) > 2 and w[1] == "run":     # 任意のコマンドを走らせる
        return w[2:]
    if cmd in ("bash", "sh", "zsh", "dash") and "-c" in w:
        i = w.index("-c") + 1
        return tokenize(w[i]) if i < len(w) else None
    if cmd in ("eval", "source", "."):
        return tokenize(" ".join(w[1:]))
    if cmd == "find":
        for i, a in enumerate(w):
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                return [x for x in w[i + 1:] if x not in (";", "+", "{}")]
    return None


def decide_segment(w: list[str], rules: dict, depth: int = 0) -> tuple[str, str] | None:
    if not w or depth > 4:
        return None
    d = danger(w, rules)
    if d:
        return d
    inner = unwrap(w)
    if inner:
        di = decide_segment(inner, rules, depth + 1)
        if di and di[0] in ("deny", "ask"):
            return di
        return None                                  # 包み・評価は allow しない
    cmd = os.path.basename(w[0])
    if cmd in ("python", "python3", "node", "perl", "ruby", "php", "deno") and len(w) > 1 and re.match(r"^-(c|e|p|E|r)|^--(eval|print)", w[1]):
        code = " ".join(w[2:]) if len(w[1]) <= 2 or w[1].startswith("--") and "=" not in w[1] else w[1] + " " + " ".join(w[2:])
        toks = tokenize(code.replace("(", " ").replace(")", " ").replace('"', " ").replace("'", " ").replace(",", " ")) or []
        if danger(toks, rules):
            return "deny", "スクリプト内に危険な操作がある"
        return None
    if cmd == "sed" and len(w) > 1 and not (w[1] == "-n" and len(w) > 2 and re.fullmatch(r"\d+(,\d+)?p", w[2])):
        if any(re.search(r"(^|[;\s])\d*[ew](\s|$)", x) for x in w[1:]) or "-i" in w or any(x.startswith("-i") for x in w):
            if any(is_protected(x) for x in w):
                return "ask", "権限・hook・設定のファイルの変更は確認が必要"
        return None
    if cmd in ("sort", "uniq", "tee") and any(is_protected(x) for x in w[1:]):
        return "ask", "権限・hook・設定のファイルの変更は確認が必要"
    if w[:2] == ["git", "push"]:
        r = allow_git(w[1:])
        return ("allow", r) if r else ("ask", "feature/ 以外・オプション付きの push は確認が必要")
    if w[:2] == ["git", "branch"] and not allow_git(w[1:]):
        return "ask", "ブランチの改名・強制変更は確認が必要"
    if w[:2] == ["git", "commit"] and any(x == "--amend" or (x.startswith("--am") and "--amend".startswith(x)) for x in w):
        return "ask", "コミットの書き換え(--amend)は確認が必要"
    if w[:2] == ["git", "rebase"] and any(x in ("-i", "--interactive", "--root", "-x") or x.startswith("--exec") or x.startswith("--root") for x in w):
        return "ask", "対話的・全履歴・コマンド実行付きの rebase は確認が必要"
    if w[:2] == ["git", "stash"] and len(w) > 2 and w[2] in ("drop", "clear"):
        return "ask", "stash の破棄は確認が必要"
    if w[:2] == ["git", "reset"] and "--hard" in w:
        return "ask", "変更を捨てる reset --hard は確認が必要"
    if w[:2] in (["git", "fetch"], ["git", "pull"]) and any(x.startswith(("--upload-pack", "--receive-pack", "--force")) or x == "-f" for x in w):
        return "ask", "確認が必要な fetch / pull のオプション"
    if w[:2] == ["gh", "api"] and not allow_words(w):
        if any(a in ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input") or re.match(r"^(-X\S|--method=|-[fF]\S|--(raw-)?field=|--input=)", a) for a in w[2:]):
            return "ask", "gh api の GET 以外・データ送信は確認が必要"
    if cmd == "docker" and len(w) > 1 and w[1] == "run" and any(a in ("-v", "--volume", "--mount", "--privileged", "--pid", "--network") or a.startswith(("--volume=", "--mount=", "--network=", "-v")) for a in w):
        return "ask", "ホストを触れる docker run は確認が必要"
    r = allow_words(w)
    return ("allow", r) if r else None


def decide_text(cmd: str, rules: dict) -> tuple[str, str] | None:
    toks = tokenize(cmd)
    if toks is None:
        return None
    segs, targets = split_segments(toks)
    for t in targets:
        if is_protected(t):
            return "ask", "権限・hook・CI・設定のファイルへの書き込みは確認が必要"
        if is_secret(t, rules.get("deny_read", [])):
            return "deny", "秘密情報らしいファイルの読み書きはしない"
    results = [decide_segment(s, rules) for s in segs]
    for kind in ("deny", "ask"):
        for r in results:
            if r and r[0] == kind:
                return r
    if targets or re.search(r"(\$\(|`|<\(|>\()", cmd):
        return None
    if results and all(r and r[0] == "allow" for r in results):
        return "allow", results[0][1]
    return None


def decide(tool: str, tool_input: dict, rules: dict) -> tuple[str, str] | None:
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        fp = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if is_secret(fp, rules.get("deny_read", [])):
            return "deny", f"秘密情報らしいファイル({fp.split('/')[-1]})はクラウドでは扱わない"
        if tool != "Read" and is_protected(fp):
            return "ask", "権限・hook・CI・設定のファイルの変更は確認が必要"
        return None
    if tool != "Bash":
        return None
    cmd = str(tool_input.get("command", "")).strip()
    return decide_text(cmd, rules) if cmd else None


def record_evidence(root: Path, data: dict, injected: bool = False) -> str | None:
    """agent-gh を呼ぶコマンドなら、出したセッション(session_id・transcript)を記録し、実行 ID を返す。
    agent-gh はこれを使って、レビューしたモデルと、実装と別のセッションかを確かめる(lib/agentstate.py)。"""
    if data.get("tool_name") != "Bash":
        return None
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if "agent-gh" not in cmd and "agent-publish" not in cmd:
        return None
    try:
        sys.path.insert(0, str(root / ".agent-kit" / "lib"))
        import agentstate
        return agentstate.write_evidence(str(root), data, cmd, injected=injected)
    except Exception:
        return None


def renew_claims(root: Path, data: dict) -> None:
    """このセッションが確保中の Issue があれば、確保の時刻を更新する(リース。保持中の確保のリースの 1/3 ごと)。
    待たずに裏で動かす。失敗しても何もしない(更新が止まれば、いずれ別のセッションが再開できるようになるだけ)。"""
    try:
        sys.path.insert(0, str(root / ".agent-kit" / "lib"))
        import agentstate
        sid = agentstate.safe_key(data.get("session_id", ""))
        if not sid:
            return
        d = agentstate.state_dir(str(root))
        mine = d / f"claims-{sid}.json"
        if not mine.exists():
            return
        if cfg(root, "AGENT_CLAIM_RENEW", "1") != "1":
            return
        # 間隔は、保持中の確保の記録に書かれたリース(リポジトリごとに一番短いもの)の 1/3。手元の設定が後で変わっても、
        # 確保のリースに合わせる。成功の印・試みの印もリポジトリごと(1つの成功で別のリポジトリの失敗を隠さない)
        import time
        import subprocess
        fallback = float(cfg(root, "AGENT_CLAIM_LEASE_MINUTES", "60"))
        sess = data.get("session_id", "")
        repos = sorted({x["repo"] for x in agentstate.my_claims(str(root), sess)})
        for repo in repos:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                continue
            interval = agentstate.renew_interval_seconds(str(root), sess, fallback, repo)
            if interval is None:
                continue
            stamp = agentstate.renew_stamp(str(root), sess, repo)
            if stamp.exists() and time.time() - stamp.stat().st_mtime < interval:
                continue
            # 失敗が続いても毎回は呼ばない(前回の試みから AGENT_CLAIM_RENEW_RETRY_SECONDS、既定は間隔の 1/4 か 60 秒の短い方)
            attempt = stamp.with_suffix(".renew-attempt")
            retry = float(cfg(root, "AGENT_CLAIM_RENEW_RETRY_SECONDS", str(min(60.0, interval / 4))))
            if attempt.exists() and time.time() - attempt.stat().st_mtime < retry:
                continue
            attempt.touch()
            cmd = f"agent-gh --repo {repo} claim-renew --mine"
            exec_id = agentstate.write_evidence(str(root), data, cmd, injected=True)
            if not exec_id:
                continue
            env = dict(os.environ, AGENT_EVIDENCE_ID=exec_id, AGENT_REPO_ROOT=str(root))
            lp = d / f"claims-{sid}.renew.log"
            if lp.exists() and lp.stat().st_size > 64 * 1024:     # 増え続けないように
                lp.unlink(missing_ok=True)
            with open(lp, "a") as log:
                subprocess.Popen([sys.executable, str(root / ".agent-kit" / "bin" / "agent-gh"), "--repo", repo,
                                  "claim-renew", "--mine"], cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
    except Exception:
        return


# --- 対外文章の公開経路(agent-publish を通す) -------------------------------------------
MCP_PUBLISH = re.compile(r"(pull|_pr\b|_pr_|\bpr_|release).*(create|update|edit|publish|write|merge)|"
                         r"(create|update|edit|publish|write|merge).*(pull|_pr\b|_pr_|\bpr_|release)|"
                         r"push_files|create_or_update_file|update_file|create_file|create_commit|update_ref|create_ref|"
                         r"update_issue|issue_write|create_tree|create_blob|delete_file|create_branch|git_push|\bpush\b|"
                         r"\bmr_(create|update|merge)|(create|update|merge)_mr\b|"
                         r"(create|update|merge|edit)_?merge_?request|merge_?request_?(create|update|merge|edit)", re.I)
GIT_SERVER_WORDS = {"git", "github", "gitlab", "gitea", "gh", "ghe", "bitbucket", "forgejo", "codeberg", "gitkraken", "devops", "ado"}


def mcp_git_server(name: str) -> bool:
    """PR・リポジトリを扱う MCP サーバーの名前か(区切りの語で見る。digitalocean・legit 等は違う)"""
    parts = [x for x in re.split(r"[_\-.]+", name.lower()) if x]
    return any(x in GIT_SERVER_WORDS for x in parts) or any(x.startswith(("github", "gitlab")) for x in parts)
GH_PR_TEXT_FLAGS = ("-t", "--title", "-b", "--body", "-F", "--body-file")
GH_REL_TEXT_FLAGS = ("-t", "--title", "-n", "--notes", "-F", "--notes-file", "--generate-notes")
GH_API_PUBLISH = re.compile(r"(^|/)repos/[^/]+/[^/]+/(pulls|releases)(/\d+)?/?$|(^|/)repos/[^/]+/[^/]+/issues/\d+/?$|"
                            r"(^|/)repos/[^/]+/[^/]+/(contents/.+|git/(refs|commits|trees|blobs)(/.*)?)$")
GH_API_VALUE_FLAGS = {"-H", "--header", "-f", "-F", "--field", "--raw-field", "-X", "--method", "--input", "-q", "--jq",
                      "-t", "--template", "--hostname", "-p", "--preview", "--cache"}
GH_GLOBAL_VALUE_FLAGS = {"-R", "--repo", "--hostname"}
GIT_GLOBAL_VALUE_FLAGS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
SHELL_WORDS = {"(", ")", "{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "in",
               "time", "exec", "command", "builtin", "nohup", "case", "esac"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PUBLISH_ENV = re.compile(r"^(AGENT_[A-Z0-9_]*|GEMINI_[A-Z0-9_]*|GOOGLE_[A-Z0-9_]*|HOME|PATH|XDG_[A-Z_]*|PYTHONPATH|GIT_DIR|GIT_WORK_TREE)=")


def _has_flag(w: list[str], flags) -> bool:
    return any(x in flags or any(x.startswith(f + "=") for f in flags if f.startswith("--")) or
               any(x.startswith(f) and len(x) > 2 for f in flags if len(f) == 2) for x in w)


def _strip_shell(w: list[str]) -> list[str]:
    """先頭の (・{・if 等の語と、変数の代入(FOO=1 cmd)を外し、末尾の )・} を外す"""
    w = [x for x in w]
    while w:
        t = w[0]
        if t in SHELL_WORDS:
            w = w[1:]
        elif t[:1] in "({" and len(t) > 1:
            w = [t.lstrip("({")] + w[1:]
        elif ASSIGN.match(t):
            w = w[1:]
        else:
            break
    while w and w[-1] in (")", "}", "fi", "done", "esac"):
        w = w[:-1]
    if w and w[-1].endswith((")", "}")) and len(w[-1]) > 1:      # (git push origin feature/x) の閉じ括弧
        w = w[:-1] + [w[-1].rstrip(")}")]
    return w


def _gh_positional(w: list[str], value_flags: set[str]) -> list[str]:
    out, i = [], 0
    while i < len(w):
        x = w[i]
        if x in value_flags:
            i += 2
            continue
        if x.startswith("-"):
            i += 1
            continue
        out.append(x)
        i += 1
    return out


def _repo_of(path: Path) -> Path | None:
    """path を含む git のリポジトリ(キットが入っているものだけ。入っていなければ管理の対象外)"""
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    return top if top and (top / ".agent-kit" / "config.env").exists() else None


def publish_segment(root: Path, w: list[str], depth: int = 0, cwd: Path | None = None, start_dir: Path | None = None) -> str | None:
    """この語の列が、管理対象の公開(PR のタイトル・本文、リリース、README を含む push)を agent-publish を通さずに
    行うなら、その理由を返す。cwd はそのコマンドが動く場所(hook の入力の cwd と、直前の cd)"""
    if not w or depth > 4:
        return None
    cwd = cwd or root
    if "agent-publish" in " ".join(w):
        # コマンドの前の代入と、env の後の代入(-u・-C・-S の値を飛ばし、-S の文字列の中も見る)に設定・場所の値があれば止める
        assigns, i = [], 0
        while i < len(w):
            x = w[i]
            if ASSIGN.match(x):
                assigns.append(x)
            elif os.path.basename(x) == "env":
                pass
            elif i > 0 and x in ("-u", "--unset", "-C", "--chdir"):
                i += 1
            elif i > 0 and x in ("-S", "--split-string") and i + 1 < len(w):
                assigns += [t for t in (tokenize(w[i + 1]) or []) if ASSIGN.match(t)]
                i += 1
            elif i > 0 and x.startswith("-") and os.path.basename(w[0]) == "env":
                pass
            else:
                break
            i += 1
        bad_env = [x for x in assigns if PUBLISH_ENV.match(x)]
        if bad_env:
            return f"agent-publish の設定・場所を変える環境変数を付けない({bad_env[0].split('=')[0]})"
    orig = list(w)                                   # 代入の前置き(GIT_DIR=… git push 等)は _strip_shell で消えるので取っておく
    w = _strip_shell(w)
    if not w:
        return None
    if w[0] in ("export", "declare", "typeset", "local", "readonly"):
        names = [x.lstrip("\\") for x in w[1:] if not x.startswith("-") or x == "--"]
        if any(re.match(r"^AGENT_", x) for x in names):
            return "agent-publish・Gemini の設定を変える環境変数を export しない(設定はファイルとクラウドの環境の設定で本人が決める)"
    inner = unwrap(w)
    if inner:
        segs, _ = split_segments(inner)              # bash -c 'a; b' の中の複数のコマンドも1つずつ見る
        here, stack = cwd, []
        for sg in segs:
            here = _cd(sg, here, stack)
            r = publish_segment(root, sg, depth + 1, here, start_dir or cwd)
            if r:
                return r
        return None
    base = [os.path.basename(x) for x in w]
    cmd = base[0]
    if cmd == "agent-publish" or (cmd.startswith("python") and len(base) > 1 and base[1] == "agent-publish"):
        return None
    if cmd == "gh" and len(w) >= 2:
        g = w[1:]
        while g and g[0].startswith("-"):           # gh -R o/r pr create ... の -R 等を外す
            g = g[2:] if (g[0] in GH_GLOBAL_VALUE_FLAGS) else g[1:]
        a, b = (g + ["", ""])[:2]
        rest = g[2:]
        if a == "pr" and b in ("create", "new"):
            return "PR は agent-publish pr-create で作る(タイトル・本文を推敲・確認してから)"
        if a == "pr" and b == "edit" and _has_flag(rest, GH_PR_TEXT_FLAGS):
            return "PR のタイトル・本文の更新は agent-publish pr-edit で行う"
        if a == "release" and b in ("create", "new"):
            return "リリースは agent-publish release で作る(リリースノートを推敲・確認してから)"
        if a == "release" and b == "edit" and _has_flag(rest, GH_REL_TEXT_FLAGS):
            return "リリースノート・タイトルの更新は agent-publish を通す"
        if a == "api":
            pos = _gh_positional(g[1:], GH_API_VALUE_FLAGS)
            path = (pos[0] if pos else "").split("?", 1)[0]
            method = ""
            for i, x in enumerate(g):
                if x in ("-X", "--method") and i + 1 < len(g):
                    method = g[i + 1].upper()
                elif x.startswith("--method="):
                    method = x.split("=", 1)[1].upper()
                elif x.startswith("-X") and len(x) > 2:
                    method = x[2:].upper()
            sends = method in ("POST", "PATCH", "PUT", "DELETE") or (method != "GET" and _has_flag(
                g[1:], ("-f", "-F", "--field", "--raw-field", "--input")))
            if path in ("graphql", "/graphql"):
                if re.search(r"(create|update)(PullRequest|Release|Ref|Commit)|mutation", " ".join(g), re.I):
                    return "GraphQL での PR・リリース・ブランチの作成・更新はしない(agent-publish を通す)"
                if any("=@" in x or x.startswith("--input") or "$" in x or "`" in x for x in g):
                    return "GraphQL の問い合わせをファイル・変数・コマンドの結果から読む呼び出しは、中身を確かめられないので止める"
            if GH_API_PUBLISH.search(path) and sends:
                return "PR・リリースの作成・更新(Issue の番号での本文の更新を含む)は agent-publish を通す(gh api の直接の呼び出しはしない)"
    if "agent-gh" in base and "pr-create" in w:
        return "PR は agent-publish pr-create で作る(agent-gh pr-create を直接呼ばない)"
    if "git" in base:
        gi = base.index("git")
        g = w[gi + 1:]
        where = cwd
        if "push" in g:
            pre = g[:g.index("push")]
            own_env = any(ASSIGN.match(x) and x.split("=")[0] in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT")
                          or (ASSIGN.match(x) and x.startswith("GIT_CONFIG")) for x in orig) or \
                (base[0] == "env" and ("-C" in w or any(x.startswith("--chdir") for x in w)))
            if cwd == UNKNOWN_DIR or own_env or any(x.startswith(("--git-dir", "--work-tree")) for x in pre):
                return "push するリポジトリの場所が分からない(cd $変数・--git-dir・GIT_DIR・env -C 等)。git -C <場所> push か、そのリポジトリで agent-publish push を使う"
            cfg_keys = [x.split("=", 1)[-1] if x.startswith("--config-env=") else x for x in pre]
            if any(re.match(r"^(remote\.[^=]*\.(push|mirror|pushurl)|push\.default|remote\.pushdefault|branch\.[^=]*\.pushremote|"
                            r"include\.path|includeif\.)", x, re.I) for x in cfg_keys) or \
                    any(ASSIGN.match(x) and x.split("=")[0].startswith(("GIT_CONFIG", "GIT_DIR", "GIT_WORK_TREE")) for x in orig):
                return "設定(-c・--config-env・GIT_CONFIG_* で remote.*.push・mirror・push.default 等)で push の対象を変える push はしない"
        while g and g[0].startswith("-"):           # git --no-pager / --git-dir=… / -c x=y / -C dir を外す
            if g[0] == "-C" and len(g) > 1:
                where = (where / g[1]) if not os.path.isabs(g[1]) else Path(g[1])
            g = g[2:] if (g[0] in GIT_GLOBAL_VALUE_FLAGS) else g[1:]
        if g and g[0] == "push":
            if where == UNKNOWN_DIR:
                return "push するリポジトリの場所が分からない。git -C <場所> push を使う"
            # cd が失敗した・括弧の中だけだった場合もあるので、追った場所と、通った場所・コマンドの始めの場所の全部で確かめる
            # (git -C で場所を書いたときは、その場所だけ)
            places = [where] if where != cwd else [where] + list(reversed(_VISITED)) + [start_dir or where]
            targets = []
            for k, place in enumerate(dict.fromkeys(places)):
                t = _repo_of(place) if place.exists() else None
                if t and t not in [x[0] for x in targets]:
                    targets.append((t, k > 0))
            bad = []
            try:
                sys.path.insert(0, str(root / ".agent-kit" / "lib"))
                import agentpub
                for target, secondary in targets:
                    cfgt = agentpub.load_config(target)
                    if cfgt["AGENT_POLISH_REQUIRED"] != "1":
                        continue
                    bad += agentpub.unconfirmed_in_push(target, cfgt, g[1:], missing_ok=secondary)
            except Exception as e:
                return f"push で公開される文書(README 等)を確かめられない({type(e).__name__})。agent-publish push を使う"
            if bad:
                return (f"確認済みでない公開文書を含む push({', '.join(bad[:5])})。agent-publish polish --kind readme → confirm → "
                        "apply → commit してから agent-publish push を使う")
    return None


UNKNOWN_DIR = Path("/nonexistent/agent-kit-unknown-dir")
_VISITED: list[Path] = []          # このコマンドの cd・pushd で通った場所(publish_gate が毎回空にする)
_CDPATH = [False]                  # CDPATH が効いている(相対の cd の行き先を決められない)


def _cd(seg: list[str], cwd: Path, stack: list | None = None) -> Path:
    """cd・pushd・popd のときは、その後のコマンドの場所を変える。決められなければ UNKNOWN_DIR。
    (env -C・GIT_DIR= はそのコマンドだけに効くので、ここでは変えない。publish_segment が見る)"""
    s2 = _strip_shell(seg)
    stack = stack if stack is not None else []
    if s2 and s2[0] == "popd":
        if "-n" in s2[1:]:
            return cwd                               # popd -n は場所を変えない
        return stack.pop() if stack else UNKNOWN_DIR
    if s2 and s2[0] in ("cd", "pushd"):
        if s2[0] == "pushd" and "-n" in s2[1:]:
            return cwd                               # pushd -n は場所を変えない
        args = [x for x in s2[1:] if x not in ("-P", "-L", "-e", "-@", "--")]
        if not args or any(ch in args[0] for ch in "$~`") or args[0] == "-" or args[0].startswith(("-", "+")):
            return UNKNOWN_DIR
        if s2[0] == "pushd":
            stack.append(cwd)
        nxt = Path(args[0]) if os.path.isabs(args[0]) else (cwd / args[0])
        _VISITED.append(cwd)
        if not nxt.is_dir() or (not os.path.isabs(args[0]) and _CDPATH[0]):
            return UNKNOWN_DIR                       # 無い場所(cd が失敗する・CDPATH で別の場所へ行く)は分からないとして扱う
        return nxt
    return cwd


PUBLISH_HINT = re.compile(r"\b(gh\s|agent-gh|git\b.*\bpush\b|agent-publish|graphql)")


def publish_gate(root: Path, data: dict) -> str | None:
    """管理対象の公開経路を agent-publish に寄せる(AGENT_POLISH_REQUIRED=1 のとき)。
    見えるのは Bash のコマンドと、名前で分かる MCP のツールだけ。ブラウザ・別のスクリプト・API の直接の呼び出しは
    ここでは止められない(docs/10 の非対応の経路)"""
    try:
        sys.path.insert(0, str(root / ".agent-kit" / "lib"))
        import agentpub
        if agentpub.load_config(root)["AGENT_POLISH_REQUIRED"] != "1":
            return None
    except Exception:
        return None
    tool = data.get("tool_name", "")
    parts = tool.split("__")
    if tool.startswith("mcp__") and len(parts) >= 3 and mcp_git_server(parts[1]) and MCP_PUBLISH.search("__".join(parts[2:])):
        return "MCP からの PR・リリースの作成・更新・ファイルの push はしない(agent-publish を通す)"
    if tool != "Bash":
        return None
    raw = str((data.get("tool_input") or {}).get("command", ""))
    if "agent-publish" in raw:
        # agent-publish を含むコマンドでは、場所・設定を変える export・set -a(allexport)を止める
        envnames = r"(AGENT_|GEMINI_|GOOGLE_GENAI|HOME\b|XDG_|PATH\b|PYTHONPATH\b|GIT_DIR\b)"
        if re.search(r"\b(export|declare|typeset|readonly)\b[^;&|\n]*" + envnames, raw) or \
                re.search(r"\bset\s+(-[a-z]*a|-o\s+allexport)", raw) or re.search(r"\bexport\b[^;&|\n]*\$", raw):
            return "agent-publish と一緒に、設定・場所を変える環境変数を export しない"
    toks = tokenize(raw)
    if toks is None:
        if PUBLISH_HINT.search(raw):
            return "コマンドを解析できない(引用符の閉じ忘れ等)。公開に関わる語があるので止める"
        return None
    segs, _ = split_segments(toks)
    start = Path(data.get("cwd") or root)
    here, stack = start, []
    _VISITED.clear()
    _CDPATH[0] = bool(os.environ.get("CDPATH")) or "CDPATH" in raw
    if re.search(r"\b(export|declare|typeset)\b[^;&|\n]*\b(GIT_DIR|GIT_WORK_TREE|GIT_CONFIG[A-Z_0-9]*)\b", raw) and re.search(r"\bpush\b", raw):
        return "push と一緒に、git の場所・設定を変える環境変数(GIT_DIR・GIT_CONFIG_* 等)を export しない"
    for s in segs:
        here = _cd(s, here, stack)
        r = publish_segment(root, s, 0, here, start)
        if r:
            return r
    return None


def with_exec_id(data: dict, exec_id: str) -> dict:
    """コマンドの先頭で AGENT_EVIDENCE_ID を export する(複合コマンドの全体に効く)。他の入力はそのまま"""
    ti = dict(data.get("tool_input") or {})
    ti["command"] = f"export AGENT_EVIDENCE_ID={exec_id}; {ti.get('command', '')}"
    return ti


def main() -> int:
    if os.environ.get("CLAUDE_CODE_REMOTE") != "true" and os.environ.get("AGENT_HOOKS_LOCAL") != "1":
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    d = None
    if cfg(root, "AGENT_CLOUD_AUTO_ALLOW", "1") == "1":
        d = decide(data.get("tool_name", ""), data.get("tool_input") or {}, load_rules(root))
    pg = publish_gate(root, data)          # 自動許可を止めていても、公開経路の検査はする
    if pg:
        d = ("deny", pg)
    # 判定の後で証拠を書く: コマンドに実行 ID を付けられる(判定と一緒に updatedInput を返す)かを記録に残すため。
    # 権限判定を止めていても、証拠は残す
    exec_id = record_evidence(root, data, injected=bool(d and d[0] in ("allow", "ask")))
    renew_claims(root, data)
    out = {"hookEventName": "PreToolUse"}
    if d:
        out.update(permissionDecision=d[0], permissionDecisionReason=d[1])
    if exec_id and d and d[0] in ("allow", "ask"):
        # 入力の書き換え(updatedInput)は判定と一緒に返す。判定しないときは書き換えない(agent-gh は「1つに決められる証拠」だけを使う)
        out["updatedInput"] = with_exec_id(data, exec_id)
    if len(out) > 1:
        print(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
