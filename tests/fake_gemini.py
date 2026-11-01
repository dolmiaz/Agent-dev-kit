#!/usr/bin/env python3
"""試験用の Gemini CLI(ヘッドレスの -p --output-format json の形だけ)。本物には接続しない。

挙動は $GEMINI_CLI_HOME/fake-mode に書いた名前で決める(agent-publish は環境変数をほとんど渡さないので、ファイルで渡す):
  ok        下書きを少しだけ言い換えて返す(変えてはいけないものは変えない)
  alter     最初の数値を変える          addurl   URL を足す          obey   本文の中の「命令」に従ってしまう
  leak      非公開のメールアドレスを足す  auth     認証切れ(終了コード 41)  quota    上限(429 → 173)
  othermodel 別のモデルが応答した        tools    ツールを使った       noformat 印の無い応答     sleep  返らない
呼ばれるたびに $GEMINI_CLI_HOME/fake-log.jsonl に、引数・環境変数の名前・作業ディレクトリの中身・標準入力・設定を書く。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

home = Path(os.environ.get("GEMINI_CLI_HOME", "/nonexistent"))
if "--version" in sys.argv:
    print((home / "fake-version").read_text().strip() if (home / "fake-version").exists() else "0.66.0")
    sys.exit(0)
mode = (home / "fake-mode").read_text().strip() if (home / "fake-mode").exists() else "ok"
stdin = sys.stdin.read()
model = sys.argv[sys.argv.index("-m") + 1] if "-m" in sys.argv else "auto"
settings = home / ".gemini" / "settings.json"
with open(home / "fake-log.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ), "cwd": os.getcwd(), "cwd_files": sorted(os.listdir(".")),
                        "stdin": stdin, "settings": json.loads(settings.read_text()) if settings.exists() else None,
                        "mode": mode}, ensure_ascii=False) + "\n")
m = re.search(r"<<<DOC-([0-9a-f]+)\n(.*?)\nDOC-\1>>>", stdin, re.S)
nonce, doc = (m.group(1), m.group(2)) if m else ("0", "")


def out(text, used=None, calls=0):
    resp = f"<<<OUT-{nonce}\n{text}\nOUT-{nonce}>>>"
    print(json.dumps({"session_id": "fake", "response": resp, "stats": {
        "models": {used or model: {"api": {"totalRequests": 1, "totalErrors": 0}, "tokens": {"total": 42}}},
        "tools": {"totalCalls": calls, "totalSuccess": calls, "totalFail": 0}}}, ensure_ascii=False))
    sys.exit(0)


def reword(t):
    for a, b in (("する。", "します。"), ("できる。", "できます。"), (" very ", " "), ("It is ", "It's ")):
        if a in t:
            return t.replace(a, b, 1)
    return t + "\n"


if mode == "ok":
    out(reword(doc))
if mode == "alter":
    out(re.sub(r"\d+", lambda x: str(int(x.group(0)) + 1), doc, count=1))
if mode == "addurl":
    out(reword(doc) + "\n詳しくは https://evil.example.com/more を参照。")
if mode == "obey":
    out(doc.split("命令:")[0] + "\n公式サイト: https://evil.example.com\nすべての試験に合格しました。")
if mode == "leak":
    out(reword(doc) + "\n連絡先: alice" + "@corp-internal.co.jp")
if mode == "auth":
    print(json.dumps({"error": {"type": "FatalAuthenticationError", "message": "Please set an Auth method", "code": 41}}),
          file=sys.stderr)
    sys.exit(41)
if mode == "quota":
    print(json.dumps({"error": {"type": "ApiError", "message": "RESOURCE_EXHAUSTED: quota exceeded", "code": 429}}),
          file=sys.stderr)
    sys.exit(173)
if mode == "othermodel":
    out(reword(doc), used="gemini-fallback-flash")
if mode == "tools":
    out(reword(doc), calls=1)
if mode == "noformat":
    print(json.dumps({"response": doc, "stats": {"models": {model: {}}, "tools": {"totalCalls": 0}}}))
    sys.exit(0)
if mode == "sleep":
    time.sleep(30)
    out(doc)
print(json.dumps({"error": {"type": "Unknown", "message": f"unknown fake mode {mode}"}}), file=sys.stderr)
sys.exit(1)
