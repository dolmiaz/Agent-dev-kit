"""本人確認(human-confirm)を、エージェントが作れない形にする: SSH の署名(ssh-keygen -Y sign / verify)。

クラウドのエージェントは本人の GitHub の権限でコメントできるので、「誰がコメントしたか」では本人を区別できない。
そこで、本人だけが持つ鍵で「リポジトリ・PR・SHA・確認した項目」に署名し、merge-gate はベースブランチの
.agent-kit/human-confirm-allowed-signers(公開鍵だけ)で確かめる。秘密鍵はクラウドへ持ち込まない
(本人の Mac の ssh-agent・1Password の SSH エージェント等で署名する。無人の処理には入れない)。
allowed-signers がベースブランチに無ければ、本人確認は使われない(合格の条件を満たさない)。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

NAMESPACE = "agent-kit-human-confirm"
MISSING_TOOL = "ssh-keygen が無いため本人確認を確かめられない"


def available() -> bool:
    """OpenSSH の ``ssh-keygen -Y`` が PATH にあるか。"""
    return shutil.which("ssh-keygen") is not None


def payload(repo: str, pr: int, sha: str, items: list[str]) -> bytes:
    return f"{NAMESPACE}|{repo}|{pr}|{sha}|{','.join(sorted(set(items)))}\n".encode()


def sign(key: str, data: bytes) -> str:
    """key は秘密鍵のパス、または ssh-agent に入っている鍵の公開鍵ファイル"""
    if not available():
        raise RuntimeError("ssh-keygen が見つからない。OpenSSH を入れてください")
    with tempfile.TemporaryDirectory() as d:
        msg = Path(d) / "msg"
        msg.write_bytes(data)
        try:
            r = subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", os.path.expanduser(key), "-n", NAMESPACE, str(msg)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as e:
            raise RuntimeError("ssh-keygen が見つからない。OpenSSH を入れてください") from e
        if r.returncode != 0:
            raise RuntimeError(f"署名できない: {r.stderr.strip()[:200]}")
        return (Path(d) / "msg.sig").read_text()


def verify_result(
    allowed_signers: str, principal: str, data: bytes, signature: str
) -> tuple[bool, str | None]:
    """Return ``(ok, reason)``. ``reason`` explains a soft failure for merge-gate notes."""
    if not (allowed_signers and principal and signature):
        return False, None
    if not available():
        return False, MISSING_TOOL
    with tempfile.TemporaryDirectory() as d:
        a = Path(d) / "allowed"
        s = Path(d) / "sig"
        a.write_text(allowed_signers)
        s.write_text(signature)
        try:
            r = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(a), "-I", principal, "-n", NAMESPACE, "-s", str(s)],
                input=data,
                capture_output=True,
                timeout=60,
            )
        except FileNotFoundError:
            return False, MISSING_TOOL
        except subprocess.TimeoutExpired:
            return False, None
        if r.returncode == 0:
            return True, None
        return False, None


def verify(allowed_signers: str, principal: str, data: bytes, signature: str) -> bool:
    ok, _ = verify_result(allowed_signers, principal, data, signature)
    return ok
