"""判定に使う設定を、PR が変えられない場所(ベースブランチ)から読む。

merge-gate と review-post は PR のブランチを checkout した場所で実行されることが多い。手元の
.agent-kit/config.env や ci-policy.json を読むと、PR 自身が判定の条件(必須 CI・指定モデル・CI なしの許可)を
変えられてしまう。そこで、これらはベースブランチのファイルを GitHub の API で読む。環境変数でも上書きしない。

ベースブランチから読むもの:
  .agent-kit/config.env                     AGENT_REVIEW_MODEL / AGENT_ALLOW_NO_CI / AGENT_REVIEWER_LOGINS / AGENT_GIT_EMAIL_CODEX
  .agent-kit/ci-policy.json                 必須 CI の追加・結論の許可
  .agent-kit/human-confirm-allowed-signers  本人確認の署名を確かめる公開鍵(ssh-keygen の allowed_signers 形式)
"""
from __future__ import annotations

import base64
import json

from .api import ApiError, api, parse_config_text

GATE_DEFAULTS = {
    "AGENT_REVIEW_MODEL": "claude-opus-5-5",
    "AGENT_ALLOW_NO_CI": "0",
    "AGENT_REVIEWER_LOGINS": "",
    "AGENT_GIT_EMAIL_CODEX": "codex-cloud@example.com",
}


def diff_anchor(repo: str, base_ref: str, head_sha: str) -> dict:
    """レビュー対象の差分を決めるもの: マージ先の名前・その先頭の SHA・head との merge-base。
    差分(git diff base...head)は merge-base と head で決まる。読めなければ例外(ApiError)"""
    tip = api("GET", f"repos/{repo}/git/ref/heads/{base_ref}")["object"]["sha"]
    cmp = api("GET", f"repos/{repo}/compare/{tip}...{head_sha}")
    mb = ((cmp or {}).get("merge_base_commit") or {}).get("sha", "")
    if not mb:
        raise ApiError(0, "merge-base を読めない")
    return {"base_ref": base_ref, "base_tip": tip, "merge_base": mb, "head": head_sha}


class BaseConfig:
    def __init__(self, repo: str, base_ref: str, at_sha: str | None = None):
        """at_sha を渡すと、その SHA の時点のファイルを読む(判定の途中でベースが動いても条件が混ざらない)"""
        self.repo, self.name = repo, base_ref
        self.ref = at_sha or base_ref
        self.problems: list[str] = []
        self._files: dict[str, str | None] = {}
        self.values = parse_config_text(self.file(".agent-kit/config.env") or "")

    def file(self, path: str) -> str | None:
        """ベースブランチのファイルの中身(無ければ None。取得できなければ problems に記録して None)"""
        if path in self._files:
            return self._files[path]
        text = None
        try:
            j = api("GET", f"repos/{self.repo}/contents/{path}?ref={self.ref}")
            if isinstance(j, dict) and j.get("encoding") == "base64":
                text = base64.b64decode(j.get("content", "")).decode("utf-8", errors="replace")
            else:   # 1MB を超えるファイル(encoding "none")・ディレクトリ等。無いものとして扱わない
                self.problems.append(f"ベースブランチ {self.name} の {path} を読めない(形式 {j.get('encoding') if isinstance(j, dict) else type(j).__name__})")
        except ApiError as e:
            if e.status != 404:
                self.problems.append(f"ベースブランチ {self.name} の {path} を読めない({e})")
        self._files[path] = text
        return text

    def get(self, key: str) -> str:
        return self.values.get(key, GATE_DEFAULTS[key])

    def policy(self) -> dict:
        text = self.file(".agent-kit/ci-policy.json")
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            return {"_error": f"ベースブランチの ci-policy.json を読めない: {e}"}
