"""対外文章(PR のタイトル・本文、リリースノート、README)の推敲と公開の共通部分(agent-publish が使う)。

流れ: 下書き → 対象・秘密情報の検査 → Gemini の推敲 → 差分の機械検査 → 作成側エージェントの内容確認 → 既存の公開手順。
- Gemini に渡すのは、対象の文章と、必要最小限の事実メモだけ(リポジトリ・会話・環境変数は渡さない)。
- 推敲の結果は、数値・URL・コードブロック・インラインコード・識別子が下書きと同じかを機械で確かめる。機械の検査は
  意味が同じことの保証ではないので、作成側エージェントの確認(confirm)を必ず挟む。
- 下書き・推敲稿・記録はリポジトリの外(状態ディレクトリ)に置く。公開する本文のハッシュと記録を結び付ける。
- Gemini が使えない(未設定・認証切れ・上限・失敗)ときは、下書きはそのまま残し、管理対象の公開操作だけを保留する。
  別のモデル・有料の API へ勝手に切り替えない。
- 技術レビュー・Opus のレビュー・PR の承認・マージ判断の代わりにはならない(別の工程)。

設定は、.agent-kit/config.env < 本人の手元の上書き(利用者の本当のホームの ~/.config/agent-kit/publish.env。HOME では
決めない) < 環境変数(クラウドの環境の設定に本人が書いた値)の順に読む。コマンドの前に付けた値・export で変えることは、
Claude Code では PreToolUse の hook が止める(docs/10)。同じ利用者のプロセスがファイルを書き換えることは、ツールでは防げない。
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

KIT_LIB = Path(__file__).resolve().parent
KIT = KIT_LIB.parent
KINDS = {"pr-title": "PR のタイトル", "pr-body": "PR の本文", "release-notes": "リリースノート", "readme": "README などの公開文書"}
MAX_CHARS = {"pr-title": 300, "pr-body": 60000, "release-notes": 120000, "readme": 200000}
MAX_FACTS = 8000
MODEL_ALIASES = {"auto", "pro", "flash", "flash-lite", "auto-preview", "preview"}
AUTH_TYPES = {"api-key", "vertex", "oauth"}
DEFAULTS = {
    "AGENT_POLISH_REQUIRED": "1",
    "AGENT_GEMINI_SEND_APPROVED": "0",
    "AGENT_GEMINI_BIN": "gemini",
    "AGENT_GEMINI_MODEL": "",
    "AGENT_GEMINI_AUTH": "",
    "AGENT_GEMINI_KEY_ENV": "GEMINI_API_KEY",
    "AGENT_GEMINI_HOME": "",
    "AGENT_GEMINI_CLI_VERSION": "",
    "AGENT_GEMINI_TIMEOUT": "180",
    "AGENT_GEMINI_MAX_ATTEMPTS": "3",
    "AGENT_PUBLIC_DOC_GLOBS": "README* */README* CHANGELOG* RELEASE_NOTES*",
    "AGENT_PRIVATE_PATTERNS_FILE": "",
}
PASS_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy",
            "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE")
VERTEX_ENV = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GOOGLE_CLOUD_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS")


# --- 設定 ---------------------------------------------------------------------------
def _parse_env_file(p: Path) -> dict:
    out = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split(" #", 1)[0].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k.strip()] = v
    except OSError:
        pass
    return out


def real_home() -> Path:
    """利用者の本当のホーム(環境変数 HOME では決めない)"""
    import pwd
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return Path(os.path.expanduser("~"))


def local_config_path() -> Path:
    return real_home() / ".config" / "agent-kit" / "publish.env"


def load_config(root: Path) -> dict:
    c = dict(DEFAULTS)
    c.update({k: v for k, v in _parse_env_file(root / ".agent-kit" / "config.env").items() if k in DEFAULTS})
    local = local_config_path()
    try:
        local_ok = root.resolve() not in local.resolve().parents
    except OSError:
        local_ok = False
    if local_ok:
        c.update({k: v for k, v in _parse_env_file(local).items() if k in DEFAULTS})
    c.update({k: os.environ[k] for k in DEFAULTS if k in os.environ})
    return c


def state_root(root: Path) -> Path:
    import agentstate
    d = agentstate.state_dir(str(root)) / "publish"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- 検査: 秘密・非公開の情報 ----------------------------------------------------------
_SKILLSYNC = None


def _secret_text(t: str) -> bool:
    global _SKILLSYNC
    if _SKILLSYNC is None:
        import runpy
        _SKILLSYNC = runpy.run_path(str(KIT / "bin" / "skillsync"), run_name="skillsync_lib")
    return _SKILLSYNC["secret_text"](t)


ALLOWED_EMAIL = re.compile(r"@(example\.(com|org|net)|[\w-]+\.invalid|anthropic\.com)$", re.I)
PRIVATE_PATTERNS = [
    ("メールアドレス", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")),
    ("Google Drive の ID らしい文字列", re.compile(r"(?<![\w-])1[A-Za-z0-9_-]{32}(?![\w-])")),
    ("手元のホームのパス", re.compile(r"/(Users|home)/(?!runner\b|user\b|USER\b|you\b|<)[A-Za-z0-9._-]+")),
    ("Apps Script の公開 URL", re.compile(r"script\.google(?:usercontent)?\.com/macros/s/[A-Za-z0-9_-]{20,}")),
    ("内部のホスト名・アドレス", re.compile(r"\b([\w-]+\.(internal|corp|lan|local)\b|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)")),
    ("非公開の印", re.compile(r"(社外秘|部外秘|非公開|CONFIDENTIAL|INTERNAL ONLY|DO NOT SHARE)", re.I)),
]


def scan(text: str, cfg: dict) -> list[str]:
    """秘密・非公開の情報らしいもの(種類だけを返し、値は一部も表示しない)"""
    found = []
    if _secret_text(text):
        found.append("秘密らしい文字列(鍵・トークンの形)")
    for name, rx in PRIVATE_PATTERNS:
        for m in rx.finditer(text):
            v = m.group(0)
            if name == "メールアドレス" and ALLOWED_EMAIL.search(v):
                continue
            found.append(f"{name}(値は非表示)")
            break
    pf = cfg.get("AGENT_PRIVATE_PATTERNS_FILE")
    if pf:
        try:
            for line in Path(os.path.expanduser(pf)).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and re.search(line, text, re.I):
                    found.append("個人の一覧(AGENT_PRIVATE_PATTERNS_FILE)に当たる語")
                    break
        except (OSError, re.error):
            found.append("個人の一覧(AGENT_PRIVATE_PATTERNS_FILE)を読めない")
    return found


# --- 差分の機械検査: 変えてはいけないもの ------------------------------------------------
FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]{0,3}\1[`~]*[ \t]*$|\Z)", re.S | re.M)   # 閉じていない囲みは最後まで
INDENTED = re.compile(r"^(?:    |\t)[ \t]*(?![-*+] |\d+[.)] )\S.*$", re.M)   # 4 字以上の字下げのコード(箇条書きの行は除く)
LINK_FULL = re.compile(r"\[([^\]]*(?:://|`)[^\]]*)\]\(([^)\s]+)\)")   # 文字列が URL・コードのリンクは、文字列と行き先の組で
INLINE = re.compile(r"`[^`\n]+`")
URL = re.compile(r"https?://[^\s<>()\[\]\"'`、。「」（）]+")
LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:[.,:/_-]\d+)*%?")   # 日本語に続く数(12件)も拾う
ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*[A-Za-z0-9]|[A-Za-z]")
IDENT = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*|#\d+|[A-Za-z]+\d+\w*|\d+[A-Za-z]+\w*|[A-Za-z_][\w]*(?:[./:@-][\w]+)+|[A-Za-z]+[A-Z][\w]*|[A-Z][A-Z0-9_]{1,}|[\w]*_[\w]+|[0-9a-f]{7,40})(?![\w-])")
# 主張(機能がある・動く・試験に通った)の語。推敲で増えたら不採用(書いていないことを言わせない)
CLAIM = re.compile(r"(合格|通過|成功|動作|動く|動き|対応|サポート|検証済|確認済|完了|問題な|正常|解決|修正済|保証|全件|すべての試験|"
                   r"\bpass(ed|es|ing)?\b|\bsucceed(ed|s)?\b|\bsuccess|\bworks?\b|\bworking\b|\bsupport(s|ed)?\b|\bverified\b|"
                   r"\btested\b|\bguarantee|\bgreen\b|\bfixed\b|\bresolved\b|\bcomplete[ds]?\b|\bdone\b|\bready\b|\bstable\b)", re.I)
# 否定。ない(少ない・危ない等の形容詞は除く)・ません・ず(に)・ぬ・不・非・無・未・なし。英語は not 等
NEGATION = re.compile(r"((?<![少危汚切幼])な(い|く)|ません|せず|ずに|(?<![まは必])ず[、。]|ぬ|不|非|無|未|なし|"
                      r"\bnot\b|\bno\b|\bnever\b|n't\b|\bwithout\b|\bcannot\b|\bnone\b|\bunable\b)", re.I)
# 問題・制限・注意の語。数が変われば不採用(既知の問題・注意書きの文が落ちた・足されたのを見つける)
PROBLEM = re.compile(r"(失敗|不具合|既知|(?<!主)問題(?!な)|エラー|警告|制限|注意|未検証|未確認|\bfail(s|ed|ing|ure)?\b|\berrors?\b|"
                     r"\bbugs?\b|\bissues?\b|\bknown\b|\blimitations?\b|\bwarnings?\b|\bcaveats?\b|\bunverified\b|\buntested\b)", re.I)


LIST_ITEM = re.compile(r"^[ \t]*([-*+]|\d+[.)])[ \t]+")


def _indented_code(text: str) -> list[str]:
    """字下げのコードの行。空行の後で、(箇条書きの中なら項目の本文の位置からさらに)4 字以上の字下げの行。
    箇条書きの続きの行(項目の本文の位置の字下げ)はコードとみなさない"""
    out, base, prev_blank, in_code = [], 0, True, False
    for line in text.splitlines():
        if not line.strip():
            prev_blank = True
            continue
        exp = line.expandtabs(4)
        ind = len(exp) - len(exp.lstrip())
        m = LIST_ITEM.match(exp)
        if m and not in_code:
            base = len(m.group(0))            # 項目の本文が始まる位置
            in_code = False
        elif ind == 0:
            base, in_code = 0, False
        elif ind >= base + 4 and (in_code or prev_blank):
            in_code = True
            out.append(line.strip())
        else:
            in_code = False
        prev_blank = False
    return out


def _mostly_japanese(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and sum(1 for ch in letters if ord(ch) > 0x2E7F) / len(letters) >= 0.3


def invariants(text: str) -> dict[str, Counter]:
    """変えてはいけないもの。数値と URL は順序も含めて比べる(入れ替えも見つける)。それ以外は個数で比べる"""
    rest = text
    out: dict = {}
    out["コードブロック"] = Counter(m.group(0).strip() for m in FENCE.finditer(rest))
    rest = FENCE.sub(" ", rest)
    out["字下げのコード"] = Counter(_indented_code(rest))
    out["リンク(URL・コードの文字列と行き先の組)"] = Counter(f"[{a}]({b})" for a, b in LINK_FULL.findall(rest))
    out["インラインコード"] = Counter(INLINE.findall(rest))
    rest = INLINE.sub(" ", rest)
    urls = [u.rstrip(".,;:!?") for u in URL.findall(rest)]
    out["URL(順序も)"] = tuple(urls)
    out["リンク先(順序も)"] = tuple(t for t in LINK_TARGET.findall(rest) if not t.startswith("http"))
    rest = URL.sub(" ", rest)
    rest = LINK_TARGET.sub(" ", rest)
    out["数値(順序も)"] = tuple(NUMBER.findall(rest))
    out["識別子"] = Counter(IDENT.findall(rest))
    if _mostly_japanese(text):
        # 日本語の文書では、英字の語(道具の名前・コマンド・小文字の識別子)は推敲で変える理由が無い。全部を同じにする
        out["英字の語"] = Counter(ASCII_WORD.findall(rest))
    return out


def compare(draft: str, polished: str) -> tuple[list[str], list[str]]:
    """(違反, 注意)。違反があれば推敲稿は使わない"""
    a, b = invariants(draft), invariants(polished)
    bad = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k, Counter()), b.get(k, Counter())
        if x == y:
            continue
        if isinstance(x, tuple) or isinstance(y, tuple):
            cx, cy = Counter(x), Counter(y)
            if cx == cy:
                bad.append(f"{k}の順序が変わった(入れ替え: {list(x)[:6]} → {list(y)[:6]})")
                continue
            x, y = cx, cy
        removed = list((x - y).elements())[:5]
        added = list((y - x).elements())[:5]
        bad.append(f"{k}が変わった(消えた: {removed or 'なし'} / 増えた: {added or 'なし'})")
    ca, cb = Counter(m.group(0).lower() for m in CLAIM.finditer(draft)), Counter(m.group(0).lower() for m in CLAIM.finditer(polished))
    if cb - ca:
        bad.append(f"主張の語が増えた({list((cb - ca).elements())[:5]})。下書きに無い機能・結果を言わせない")
    warn = []
    if ca - cb:
        warn.append(f"主張の語が減った({list((ca - cb).elements())[:5]})。言い換えか、内容が落ちたかを確かめる")
    pa = Counter(m.group(0).lower() for m in PROBLEM.finditer(draft))
    pb = Counter(m.group(0).lower() for m in PROBLEM.finditer(polished))
    if sum(pa.values()) != sum(pb.values()):
        bad.append(f"問題・制限・注意の語の数が変わった({sum(pa.values())} → {sum(pb.values())})。既知の問題・注意書きが落ちた・足された可能性")
    na, nb = len(NEGATION.findall(draft)), len(NEGATION.findall(polished))
    if na != nb:
        bad.append(f"否定の言い方の数が変わった({na} → {nb})。機能の有無・試験結果の意味が変わり得るので使わない")
    if len(polished) < len(draft) * 0.85 - 20:
        warn.append(f"短くなった({len(draft)} → {len(polished)} 文字)。文・注意書きが落ちていないか、差分で必ず確かめる")
    elif len(polished) > len(draft) * 1.5 + 50:
        warn.append(f"長くなった({len(draft)} → {len(polished)} 文字)。内容が足されていないか確かめる")
    warn.append("機械の検査で見えないもの: 否定の入れ替え(どの文に否定が付くか)、言葉で書いた数、文中のコマンドの言い換え、"
                "主張の言い換え。差分を読んで確かめる")
    return bad, warn


def unified_diff(a: str, b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), "下書き", "推敲稿"))


# --- Gemini の呼び出し ---------------------------------------------------------------
class Hold(Exception):
    """推敲できない(設定が無い・認証切れ・上限・失敗)。下書きは残し、公開だけを保留する"""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind


def gemini_ready(cfg: dict) -> None:
    """送ってよいか・設定がそろっているか(送る前に確かめる)"""
    if cfg["AGENT_GEMINI_SEND_APPROVED"] != "1":
        raise Hold("not-approved", "Gemini への送信を本人が承認していない(AGENT_GEMINI_SEND_APPROVED=1 が無い)。"
                                   "利用する認証方式と、その利用条件(入力の扱い)を確かめてから、本人の手元の設定に書く")
    model = cfg["AGENT_GEMINI_MODEL"].strip()
    if not model:
        raise Hold("config", "AGENT_GEMINI_MODEL が無い(モデル名は推測で決めない。本人が確かめた名前を書く)")
    if model.lower() in MODEL_ALIASES:
        raise Hold("config", f"AGENT_GEMINI_MODEL={model} は別名(自動で別のモデルに振り分けられうる)。具体的なモデル名を書く")
    if cfg["AGENT_GEMINI_AUTH"] not in AUTH_TYPES:
        raise Hold("config", f"AGENT_GEMINI_AUTH は {'/'.join(sorted(AUTH_TYPES))} のどれか(今: {cfg['AGENT_GEMINI_AUTH'] or '空'})")
    if not cfg["AGENT_GEMINI_CLI_VERSION"].strip():
        raise Hold("config", "AGENT_GEMINI_CLI_VERSION が無い(確かめた Gemini CLI の版を書く。違う版では動かさない)")
    if not cfg["AGENT_GEMINI_HOME"].strip():
        raise Hold("config", "AGENT_GEMINI_HOME が無い(推敲専用の Gemini の設定の置き場。普段の ~/.gemini とは分ける)")


def gemini_settings(cfg: dict) -> dict:
    """推敲専用の設定: ツール・MCP・拡張・skills・hooks を持たせない。統計・テレメトリを送らない。
    作業ディレクトリのファイル(GEMINI.md・ディレクトリの一覧)を読ませない。自動で別モデル・有料枠へ切り替えない"""
    auth = {"api-key": "gemini-api-key", "vertex": "vertex-ai", "oauth": "oauth-personal"}[cfg["AGENT_GEMINI_AUTH"]]
    return {
        "security": {"auth": {"selectedType": auth, "enforcedType": auth}, "folderTrust": {"enabled": False},
                     "blockGitExtensions": True, "disableYoloMode": True},
        "tools": {"core": [], "exclude": ["run_shell_command", "web_fetch", "google_web_search", "write_file", "replace",
                                          "read_file", "read_many_files", "glob", "grep_search", "list_directory"]},
        "mcp": {"allowed": []},              # この専用の設定には MCP サーバーを書かない(どれも起動しない)
        "skills": {"enabled": False},
        "hooksConfig": {"enabled": False},
        "context": {"fileName": ["__agent_kit_no_context__.md"], "includeDirectoryTree": False, "memoryBoundaryMarkers": []},
        "telemetry": {"enabled": False, "logPrompts": False},
        "privacy": {"usageStatisticsEnabled": False},
        "general": {"checkpointing": {"enabled": False}},
        "billing": {"overageStrategy": "never"},
        "advanced": {"ignoreLocalEnv": True},
    }


POLICY_DENY_ALL = '[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\ndenyMessage = "edit-only run: no tools"\n'


def gemini_env(cfg: dict, home: Path) -> dict:
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env.update(GEMINI_CLI_HOME=str(home), HOME=str(home), NO_COLOR="1", GEMINI_TELEMETRY_ENABLED="false")
    if cfg["AGENT_GEMINI_AUTH"] == "api-key":
        kv = cfg["AGENT_GEMINI_KEY_ENV"]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", kv or ""):
            raise Hold("config", f"AGENT_GEMINI_KEY_ENV の名前が不正({kv})")
        if not os.environ.get(kv):
            raise Hold("auth", f"API キーの環境変数 {kv} が無い(認証が無い・切れた)")
        env["GEMINI_API_KEY"] = os.environ[kv]
    elif cfg["AGENT_GEMINI_AUTH"] == "vertex":
        env["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        env.update({k: os.environ[k] for k in VERTEX_ENV if k in os.environ})
    return env


def build_input(kind: str, draft: str, facts: str, nonce: str) -> str:
    tmpl = (KIT / "publish" / "polish-instructions.md").read_text(encoding="utf-8")
    return (tmpl.replace("{KIND}", KINDS[kind]).replace("{NONCE}", nonce)
            + f"\n<<<FACTS-{nonce}\n{facts}\nFACTS-{nonce}>>>\n\n<<<DOC-{nonce}\n{draft}\nDOC-{nonce}>>>\n")


def run_gemini(cfg: dict, kind: str, draft: str, facts: str) -> tuple[str, dict]:
    """推敲稿と、呼び出しの記録(版・モデル・統計)を返す。できなければ Hold"""
    gemini_ready(cfg)
    exe = shutil.which(cfg["AGENT_GEMINI_BIN"]) if "/" not in cfg["AGENT_GEMINI_BIN"] else cfg["AGENT_GEMINI_BIN"]
    if not exe or not os.access(exe, os.X_OK):
        raise Hold("missing", f"Gemini CLI({cfg['AGENT_GEMINI_BIN']})が見つからない")
    home = Path(os.path.expanduser(cfg["AGENT_GEMINI_HOME"])).resolve()
    (home / ".gemini").mkdir(parents=True, exist_ok=True)
    (home / ".gemini" / "settings.json").write_text(json.dumps(gemini_settings(cfg), indent=2), encoding="utf-8")
    env = gemini_env(cfg, home)
    with tempfile.TemporaryDirectory(prefix="agent-polish-") as work, \
            tempfile.TemporaryDirectory(prefix="agent-polish-policy-") as pol:   # 作業ディレクトリは空(リポジトリを見せない)
        try:
            v = subprocess.run([exe, "--version"], cwd=work, env=env, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Hold("missing", f"Gemini CLI の版を確かめられない({type(e).__name__})")
        version = (v.stdout or "").strip().splitlines()[-1:] or [""]
        version = version[0].strip()
        if version != cfg["AGENT_GEMINI_CLI_VERSION"].strip():
            raise Hold("version", f"Gemini CLI の版が {version or '不明'}(確かめた版は {cfg['AGENT_GEMINI_CLI_VERSION']})。"
                                  "新しい版の設定と挙動を確かめてから AGENT_GEMINI_CLI_VERSION を更新する")
        policy = Path(pol) / "deny-all.toml"
        policy.write_text(POLICY_DENY_ALL, encoding="utf-8")
        nonce = secrets.token_hex(8)
        cmd = [exe, "-p", "Follow the instructions in the input. Output only the rewritten document between the markers.",
               "--output-format", "json", "-m", cfg["AGENT_GEMINI_MODEL"], "--approval-mode", "default",
               "--extensions", "none", "--policy", str(policy)]
        try:
            r = subprocess.run(cmd, cwd=work, env=env, input=build_input(kind, draft, facts, nonce), capture_output=True,
                               text=True, timeout=float(cfg["AGENT_GEMINI_TIMEOUT"]))
        except subprocess.TimeoutExpired:
            raise Hold("timeout", f"Gemini が {cfg['AGENT_GEMINI_TIMEOUT']} 秒で終わらない")
        except OSError as e:
            raise Hold("missing", f"Gemini CLI を起動できない({e})")
    info = {"cli_version": version, "model_requested": cfg["AGENT_GEMINI_MODEL"], "auth": cfg["AGENT_GEMINI_AUTH"],
            "exit": r.returncode}
    data = _parse_json(r.stdout) or _parse_json(r.stderr) or {}
    err = data.get("error") if isinstance(data, dict) else None
    if r.returncode != 0 or err:
        raise _classify(r.returncode, err, r.stderr)
    stats = data.get("stats") or {}
    used = sorted((stats.get("models") or {}).keys())
    info["models_used"] = used
    if not used or any(m != cfg["AGENT_GEMINI_MODEL"] for m in used):
        raise Hold("model", f"指定と違うモデルが応答した、または応答したモデルが分からない({used or '記録なし'})。"
                            "別のモデルの推敲稿は使わない")
    calls = ((stats.get("tools") or {}).get("totalCalls")) or 0
    if calls:
        raise Hold("tools", f"推敲でツールが使われた({calls} 回)。編集専用の設定が効いていない。推敲稿は使わない")
    resp = data.get("response")
    m = re.search(rf"<<<OUT-{nonce}\n(.*?)\nOUT-{nonce}>>>", resp or "", re.S)
    if not m:
        raise Hold("format", "推敲稿が決めた印の間に無い(指示どおりの形で返っていない)")
    info["tokens"] = {k: v.get("tokens", {}).get("total") for k, v in (stats.get("models") or {}).items()}
    return m.group(1), info


def _parse_json(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                return None
    return None


def _classify(rc: int, err, stderr: str) -> Hold:
    msg = (err or {}).get("message", "") if isinstance(err, dict) else ""
    code = str((err or {}).get("code", "")) if isinstance(err, dict) else ""
    text = f"{msg} {code} {stderr[-400:] if stderr else ''}"
    if rc == 41 or re.search(r"auth|credential|login|401|403|PERMISSION_DENIED|UNAUTHENTICATED", text, re.I):
        return Hold("auth", f"Gemini の認証が無い・切れた(終了コード {rc})。本人が認証し直すまで公開は保留")
    if rc in (173,) or re.search(r"429|quota|RESOURCE_EXHAUSTED|rate limit|exhausted", text, re.I):
        return Hold("quota", f"Gemini の利用枠の上限(終了コード {rc})。別のモデル・有料枠には切り替えない。時間を置いて再試行")
    if rc in (42, 44, 52, 55):
        return Hold("config", f"Gemini CLI の設定・入力の問題(終了コード {rc}): {msg[:160]}")
    return Hold("api", f"Gemini の呼び出しに失敗(終了コード {rc}): {msg[:160] or (stderr or '')[-160:]}")


# --- 記録 -------------------------------------------------------------------------
def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def rec_dir(root: Path, rid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", rid or ""):
        raise ValueError(f"記録の ID が不正({rid})")
    return state_root(root) / rid


def load(root: Path, rid: str) -> dict:
    d = rec_dir(root, rid)
    rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
    rec["_dir"] = str(d)
    return rec


def save(root: Path, rec: dict) -> None:
    d = rec_dir(root, rec["id"])
    d.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in rec.items() if not k.startswith("_")}
    tmp = d / f".record.{secrets.token_hex(4)}.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, d / "record.json")
    if rec.get("status") == "confirmed" and rec.get("final_sha256"):
        idx = state_root(root) / "by-hash"
        idx.mkdir(exist_ok=True)
        (idx / f"{rec['final_sha256']}.json").write_text(json.dumps(
            {"id": rec["id"], "kind": rec["kind"], "path": rec.get("path", "")}), encoding="utf-8")


def text_of(rec: dict, name: str) -> str:
    return (Path(rec["_dir"]) / name).read_text(encoding="utf-8")


def confirmed_text(root: Path, rid: str, kind: str | tuple) -> str:
    """公開してよい本文(確認済みで、保存した本文のハッシュが記録と同じもの)。違えば ValueError"""
    rec = load(root, rid)
    kinds = (kind,) if isinstance(kind, str) else kind
    if rec["kind"] not in kinds:
        raise ValueError(f"記録 {rid} は {rec['kind']}({'/'.join(kinds)} ではない)")
    if rec.get("status") != "confirmed":
        raise ValueError(f"記録 {rid} は {rec.get('status')}(確認済みでない)。{rec.get('hold_reason', '')}")
    t = text_of(rec, "final.md")
    if sha256(t) != rec.get("final_sha256"):
        raise ValueError(f"記録 {rid} の本文が確認のあとで変わっている。agent-publish revise で検証し直す")
    return t


def find_confirmed_by_hash(root: Path, digest: str) -> dict | None:
    p = state_root(root) / "by-hash" / f"{digest}.json"
    if not p.is_file():
        return None
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
        rec = load(root, idx["id"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    if rec.get("status") == "confirmed" and rec.get("final_sha256") == digest and sha256(text_of(rec, "final.md")) == digest:
        return rec
    return None


# --- push で公開される文書 -------------------------------------------------------------
def _git(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)


def push_base(root: Path, remote: str, branch: str | None, head: str = "HEAD") -> str | None:
    """push で新しく公開されるコミットの手前(リモートにある先頭と head の merge-base)。決められなければ None"""
    cands = []
    if branch:
        cands.append(f"refs/remotes/{remote}/{branch}")
    if head == "HEAD":
        cands.append("@{upstream}")
    cands += [f"refs/remotes/{remote}/HEAD", f"refs/remotes/{remote}/main", f"refs/remotes/{remote}/master"]
    for c in cands:
        r = _git(root, "rev-parse", "-q", "--verify", c + "^{commit}")
        if r.returncode == 0 and r.stdout.strip():
            mb = _git(root, "merge-base", r.stdout.strip(), head)
            if mb.returncode == 0:
                return mb.stdout.strip()
    # 既定のブランチが main・master 以外で、リモートの HEAD も無い: リモートのブランチのうち head に一番近いものを基点にする
    best = None
    refs = _git(root, "for-each-ref", "--format=%(objectname)", f"refs/remotes/{remote}").stdout.split()
    for sha in refs:
        mb = _git(root, "merge-base", sha, head)
        if mb.returncode != 0:
            continue
        n = _git(root, "rev-list", "--count", f"{mb.stdout.strip()}..{head}")
        cnt = int(n.stdout.strip() or 1 << 30) if n.returncode == 0 else 1 << 30
        if best is None or cnt < best[0]:
            best = (cnt, mb.stdout.strip())
    return best[1] if best else None


def push_targets(root: Path, args: list[str]) -> list[tuple[str, str, str | None]]:
    """git push の引数(オプションを除いたもの)から、(リモート, push するローカルの ref, リモートのブランチ名) の一覧。
    refspec が無ければ今のブランチ。--all はローカルの全ブランチ"""
    value_opts = {"-o", "--push-option", "--repo", "--receive-pack", "--exec", "--signed", "--force-with-lease"}
    opts, pos, i = [], [], 0
    while i < len(args):
        x = args[i]
        if x in value_opts and x not in ("--signed", "--force-with-lease"):
            opts.append(x); i += 2; continue
        (opts if x.startswith("-") else pos).append(x)
        i += 1
    repo_opt = next((args[k + 1] for k, x in enumerate(args) if x == "--repo" and k + 1 < len(args)), None) or \
        next((x.split("=", 1)[1] for x in args if x.startswith("--repo=")), None)
    if repo_opt:
        remote = repo_opt
        pos = [repo_opt] + pos
    elif pos:
        remote = pos[0]
    else:                             # リモートを書かないときは、git と同じく branch.<今>.pushRemote・remote.pushDefault・origin の順
        cur0 = _git(root, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
        remote = (_git(root, "config", "--get", f"branch.{cur0}.pushRemote").stdout.strip() if cur0 else "") or \
            _git(root, "config", "--get", "remote.pushDefault").stdout.strip() or "origin"
    specs = pos[1:]
    out = []
    if {"-d", "--delete"} & set(opts):
        return []                     # 削除(公開する中身は無い。削除は別の規則で拒否)
    if specs[:1] == ["tag"]:
        specs = specs[2:]             # git push origin tag v1(タグは確かめない。docs/10)
    if "--all" in opts or "--branches" in opts:
        r = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
        return [(remote, f"refs/heads/{b}", b) for b in r.stdout.split()]
    if not specs:
        # リポジトリの設定で push の対象が決まる(remote.<名前>.push・mirror・push.default=matching)なら、全部のブランチを確かめる
        conf = _git(root, "config", "--get-regexp", rf"^(remote\.{re.escape(remote)}\.(push|mirror)|push\.default)$").stdout
        if re.search(r"\.push |\.mirror (true|yes|on|1)\b|push\.default (matching|upstream)", conf, re.I):
            r = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
            return [(remote, f"refs/heads/{b}", b) for b in r.stdout.split()]
        cur = _git(root, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
        return [(remote, "HEAD", cur or None)]
    for sp in specs:
        sp = sp.lstrip("+")
        src, _, dst = sp.partition(":")
        if not src and dst:
            continue                  # :branch はリモートのブランチの削除(公開する中身は無い。削除は別の規則で拒否)
        src = src or "HEAD"
        dst = dst or (src if src != "HEAD" else _git(root, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip())
        if dst.startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        out.append((remote, src, dst or None))
    return out


def unconfirmed_in_push(root: Path, cfg: dict, args: list[str], missing_ok: bool = False) -> list[str]:
    """push で公開される README 等のうち、確認済みの本文と一致しないもの(push する ref の中身で確かめる)。
    missing_ok: そのリポジトリに push する ref が無ければ飛ばす(念のために確かめる、もう1つの場所のとき)"""
    bad = []
    for remote, src, dst in push_targets(root, args):
        r = _git(root, "rev-parse", "-q", "--verify", src + "^{commit}")
        if r.returncode != 0:
            if missing_ok:
                continue
            raise RuntimeError(f"push する {src} が分からない")
        head = r.stdout.strip()
        base = push_base(root, remote, dst, head)
        files = public_docs_changed(root, cfg, base, head)
        bad += [f"{src}:{f}" if src != "HEAD" else f for f in unconfirmed_docs(root, cfg, files, head)]
    return bad


MANAGED_DIRS = (".git", ".agent-kit", ".claude", ".agents")     # キット・エージェントの管理する場所(配ったスキルの README 等)


def is_public_doc(path: str, cfg: dict) -> bool:
    """README など、推敲の対象の公開文書のパスか(リポジトリの中の相対パス)。.git・.agent-kit・.claude・.agents の中
    (配ったスキルの README 等)は対象にしない。.github/ は、GitHub が表示する README だけを対象にする"""
    import fnmatch
    p = Path(path)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] in MANAGED_DIRS:
        return False
    if p.parts[0] == ".github":       # GitHub が表示する README と、設定の globs に .github/ で書いたものだけ
        return (len(p.parts) == 2 and p.name.lower().startswith("readme")) or any(
            g.startswith(".github/") and fnmatch.fnmatchcase(p.as_posix(), g) for g in cfg["AGENT_PUBLIC_DOC_GLOBS"].split())
    return any(fnmatch.fnmatchcase(p.as_posix(), g) for g in cfg["AGENT_PUBLIC_DOC_GLOBS"].split())


def public_docs_changed(root: Path, cfg: dict, base: str | None, head: str = "HEAD") -> list[str]:
    import fnmatch
    globs = cfg["AGENT_PUBLIC_DOC_GLOBS"].split()
    if base:
        r = _git(root, "diff", "--name-only", "--diff-filter=AMR", base, head)
    else:
        r = _git(root, "ls-tree", "-r", "--name-only", head)
    if r.returncode != 0:
        raise RuntimeError(f"変更されたファイルを確かめられない: {r.stderr.strip()[:160]}")
    return [f for f in r.stdout.splitlines() if is_public_doc(f, cfg)]


def unconfirmed_docs(root: Path, cfg: dict, files: list[str], head: str = "HEAD") -> list[str]:
    """push するコミットの中身が、確認済みの記録(kind readme、同じパス)と一致しない文書"""
    bad = []
    for f in files:
        r = subprocess.run(["git", "-C", str(root), "show", f"{head}:{f}"], capture_output=True, timeout=30)
        if r.returncode != 0:
            bad.append(f"{f}(読めない)")
            continue
        try:
            text = r.stdout.decode("utf-8")
        except UnicodeDecodeError:
            bad.append(f"{f}(UTF-8 でない)")
            continue
        rec = find_confirmed_by_hash(root, sha256(text))
        if not rec or rec["kind"] != "readme" or rec.get("path") != f:
            bad.append(f)
    return bad
