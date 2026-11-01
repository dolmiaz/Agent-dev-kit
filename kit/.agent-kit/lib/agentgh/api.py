"""GitHub REST(`gh api` 経由)と設定の読み込み。

Claude Code on the web のプロキシでは GraphQL が使えないため、REST だけを使う。
gh コマンドの場所は GH_BIN で変えられる(テスト用)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


class ApiError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(f"HTTP {status}: {msg}")
        self.status = status


def repo_root() -> str:
    root = os.environ.get("AGENT_REPO_ROOT") or subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    return root or os.getcwd()


def parse_config(path) -> dict:
    """config.env を読む(シェルの load_config と同じ規則: 空白・CRLF・行末コメント・引用符)"""
    try:
        return parse_config_text(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {}


def parse_config_text(text: str) -> dict:
    out = {}
    for line in text.splitlines():
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


def cfg(key: str, default: str) -> str:
    """環境変数 > .agent-kit/config.env > 既定値"""
    if key in os.environ:
        return os.environ[key]
    return parse_config(Path(repo_root()) / ".agent-kit" / "config.env").get(key, default)


def api(method: str, path: str, body: dict | None = None, paginate: bool = False):
    """gh api を呼ぶ。HTTP 400 以上は ApiError(status)。
    paginate=True: 全ページを取る。配列の API はつないだ配列、オブジェクトの API はページの配列を返す。"""
    gh = os.environ.get("GH_BIN", "gh")
    cmd = [gh, "api", "-X", method, "-H", "Accept: application/vnd.github+json"]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    cmd.append(path.lstrip("/"))
    inp = None
    if body is not None:
        cmd += ["--input", "-"]
        inp = json.dumps(body)
    try:
        r = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        raise ApiError(0, f"timeout: {method} {path}")
    data = None
    if r.stdout.strip():
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            data = None
    if r.returncode != 0:
        m = re.search(r"HTTP (\d{3})", r.stderr)
        msg = data.get("message") if isinstance(data, dict) else r.stderr.strip()[:200]
        raise ApiError(int(m.group(1)) if m else 1, msg or r.stderr.strip()[:200])
    if paginate and isinstance(data, list) and data and all(isinstance(x, list) for x in data):
        data = [y for x in data for y in x]
    return data


def try_api(method: str, path: str, default=None, ok_status=(404,)):
    """ok_status のエラーは default を返す(存在しないことが分かった、という扱い)"""
    try:
        return api(method, path)
    except ApiError as e:
        if e.status in ok_status:
            return default
        raise


def repo_slug(arg: str | None) -> str:
    if arg:
        return arg
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        sys.exit("リポジトリを判定できない(--repo owner/name を指定)")
    return m.group(1)


def warn_on_error(fn) -> None:
    """付随操作(ラベル・コメント)の失敗は警告に留める。404 は無視。"""
    try:
        fn()
    except ApiError as e:
        if e.status != 404:
            print(f"警告: {e}", file=sys.stderr)
