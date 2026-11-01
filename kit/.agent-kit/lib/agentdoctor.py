"""Offline, read-only checks for an installed agent-kit. Never print input values."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

CORE_TOOLS = ("bash", "git", "python3", "gh")
PROJECT_TOOLS = {
    "package.json": "node", "package-lock.json": "npm", "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "bun.lock": "bun", "bun.lockb": "bun",
    "uv.lock": "uv", "poetry.lock": "poetry", "go.mod": "go", "Cargo.toml": "cargo",
    "Gemfile.lock": "bundle", "composer.lock": "composer", "pom.xml": "java",
}
SKILLS = ("cloud-worklog", "issue-task", "pr-review", "publish-polish")
FLAGS = {"AGENT_POLISH_REQUIRED", "AGENT_SETUP_STRICT", "AGENT_LOG_STRICT", "AGENT_LOG_ENFORCE",
         "AGENT_GIT_IDENTITY", "AGENT_CLOUD_AUTO_ALLOW", "AGENT_ALLOW_NO_CI", "AGENT_CLAIM_RENEW",
         "AGENT_NODE_ALLOW_NO_LOCK", "AGENT_DOCKER_PULL", "AGENT_JVM_WARMUP"}
TIMEOUTS = {"AGENT_STEP_TIMEOUT", "AGENT_LOG_SYNC_TIMEOUT", "AGENT_GEMINI_TIMEOUT"}


class Finding(ValueError):
    pass


def file_path(root: Path, rel: str) -> Path:
    p = root
    for part in Path(rel).parts:
        p = p / part
        if p.is_symlink():
            raise Finding("invalid: " + rel)
    try:
        mode = p.stat().st_mode
    except FileNotFoundError:
        raise Finding("missing: " + rel) from None
    if not stat.S_ISREG(mode):
        raise Finding("invalid: " + rel)
    return p


def read_text(root: Path, rel: str) -> str:
    p = file_path(root, rel)
    if p.stat().st_size > 1024 * 1024:
        raise Finding("invalid: " + rel)
    return p.read_text(encoding="utf-8")


def config_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(AGENT_[A-Z0-9_]+)\s*=\s*(.*)", line)
        if not match or match[1] in values:
            raise ValueError("invalid config")
        key, value = match.groups()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise ValueError("invalid config")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def gh_api_support(executable: str, run=subprocess.run) -> bool | None:
    """Read local help only: no endpoint, credentials, prompts or update checks."""
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ") if k in os.environ}
    env.update(GH_NO_UPDATE_NOTIFIER="1", GH_NO_EXTENSION_UPDATE_NOTIFIER="1",
               GH_PROMPT_DISABLED="1", NO_COLOR="1", CLICOLOR="0")
    try:
        result = run([executable, "api", "--help"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return re.search(r"(?m)^\s+--slurp(?:\s|$)", result.stdout) is not None


def inspect(root: Path, which=shutil.which, environ=None, probe_gh=False, gh_check=gh_api_support) -> list[str]:
    """Never create state; execute gh help only when explicitly requested."""
    environ = os.environ if environ is None else environ
    problems = []
    failed = object()

    def check(rel, parse=None):
        try:
            return parse(read_text(root, rel)) if parse else file_path(root, rel)
        except Finding as e:
            problems.append(str(e))
        except (OSError, UnicodeError, ValueError):
            problems.append("invalid: " + rel)
        return failed

    tools = set(CORE_TOOLS)
    for marker, tool in PROJECT_TOOLS.items():
        if (root / marker).exists():
            tools.add(tool)
    found = {tool: which(tool) for tool in sorted(tools)}
    for tool, executable in found.items():
        if executable is None:
            problems.append("missing: tool " + tool)
    if found.get("gh") is not None:
        if not probe_gh:
            problems.append("unverified: gh api --slurp support (opt in with --probe-gh)")
        else:
            supported = gh_check(found["gh"])
            if supported is False:
                problems.append("unsupported: gh api --slurp (gh 2.48+ required)")
            elif supported is None:
                problems.append("unverified: gh api --slurp support")

    cfg = check(".agent-kit/config.env", config_values)
    if cfg is not failed:
        keys = FLAGS | TIMEOUTS | {"AGENT_LOG_PENDING_MODE"}
        effective = {**cfg, **{k: environ[k] for k in keys if k in environ}}
        bad = any(effective[k] not in ("0", "1") for k in FLAGS & effective.keys())
        bad |= effective.get("AGENT_LOG_PENDING_MODE", "outbox") not in ("outbox", "repo")
        for key in TIMEOUTS:
            value = effective.get(key, "1")
            bad |= not value.isascii() or not value.isdigit() or not value.strip("0")
        if bad:
            problems.append("invalid: .agent-kit/config.env")

    manifest = check(".agent-kit/install-manifest.json", json.loads)
    targets = []
    if manifest is not failed:
        if (not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict)
                or not isinstance(manifest.get("targets"), list) or not manifest["targets"]
                or any(t not in ("codex", "claude") for t in manifest["targets"])):
            problems.append("invalid: .agent-kit/install-manifest.json")
        else:
            targets = manifest["targets"]
    for rel in (".agent-kit/VERSION", ".agent-kit/lib/common.sh", ".agent-kit/bin/setup-deps.sh",
                ".agent-kit/bin/agent-log", ".agent-kit/bin/agent-gh", ".agent-kit/bin/agent-publish"):
        path = check(rel)
        if path is not failed and rel in (".agent-kit/bin/agent-gh", ".agent-kit/bin/agent-log"):
            try:
                executable = bool(path.stat().st_mode & 0o111) and os.access(path, os.X_OK)
            except OSError:
                executable = False
            if not executable:
                problems.append("not executable: " + rel)
    for target in targets:
        directory, doc = (".agents", "AGENTS.md") if target == "codex" else (".claude", "CLAUDE.md")
        check(doc)
        for skill in SKILLS:
            check(f"{directory}/skills/{skill}/SKILL.md")
        if target == "claude":
            settings = check(".claude/settings.json", json.loads)
            if settings is not failed and (not isinstance(settings, dict) or not isinstance(settings.get("hooks"), dict)):
                problems.append("invalid: .claude/settings.json")
    return problems


def main() -> int:
    args = sys.argv[1:]
    if args not in ([], ["--probe-gh"]):
        print("usage: setup-deps.sh {check|doctor} [--probe-gh]")
        return 2
    try:
        selected = os.environ.get("AGENT_REPO_ROOT")
        project = os.environ.get("CLAUDE_PROJECT_DIR")
        if not selected and project and (Path(project) / ".agent-kit").is_dir():
            selected = project
        root = Path(selected or Path(__file__).resolve().parents[2]).resolve(strict=True)
        problems = inspect(root, probe_gh=bool(args)) if root.is_dir() else ["invalid: target directory"]
    except (OSError, RuntimeError, ValueError):
        problems = ["invalid: target directory"]
    for problem in problems:
        print(problem)
    print(f"doctor: {'attention required' if problems else 'ok'}; offline/read-only; versions, credentials and services not checked")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
