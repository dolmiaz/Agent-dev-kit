#!/usr/bin/env python3
"""Read only GEMINI_API_KEY from this checkout's .env, then run agent-publish.

--check checks the local file only. It does not authenticate or call Gemini.
The .env is data, never shell code; its values are never printed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 16384


def read_key(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid():
            raise ValueError(".env must be a regular file owned by the current user")
        if stat.S_IMODE(meta.st_mode) not in (0o600, 0o400):
            raise ValueError(".env permissions must be 600 or 400")
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError(".env exceeds the size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise ValueError(".env must be UTF-8 text") from None
    value = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        name, sep, candidate = line.partition("=")
        if not sep or name.strip() != "GEMINI_API_KEY" or value is not None:
            raise ValueError(".env must contain a single GEMINI_API_KEY assignment and comments only")
        candidate = candidate.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
            candidate = candidate[1:-1]
        if not re.fullmatch(r"[A-Za-z0-9_.~+/=:-]{1,2048}", candidate):
            raise ValueError("GEMINI_API_KEY is empty or has unsupported characters")
        value = candidate
    if value is None:
        raise ValueError("GEMINI_API_KEY is missing")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="agent-publish arguments")
    args = parser.parse_args(argv)
    if (args.check and args.args) or (not args.check and not args.args):
        parser.error("use --check alone, or pass agent-publish arguments")
    try:
        value = read_key(ROOT / ".env")
    except (OSError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) else ".env cannot be opened safely"
        print(f"local-env: {message}; no request made", file=sys.stderr)
        return 2
    if args.check:
        print("local-env: GEMINI_API_KEY is present; no authentication or request performed")
        return 0
    env = dict(os.environ, GEMINI_API_KEY=value)
    # The key name is fixed for this launcher. Approval, model, version, and all
    # other gate settings still come from agent-publish's normal configuration.
    env["AGENT_GEMINI_KEY_ENV"] = "GEMINI_API_KEY"
    os.chdir(ROOT)
    os.execve(sys.executable, [sys.executable, str(ROOT / "kit/.agent-kit/bin/agent-publish"), *args.args], env)
    return 2


if __name__ == "__main__":
    sys.exit(main())
