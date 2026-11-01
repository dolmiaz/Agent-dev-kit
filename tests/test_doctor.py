"""Installed doctor/check regression tests; synthetic fixtures and no external APIs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agentdoctor", ROOT / "kit/.agent-kit/lib/agentdoctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "synthetic-repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
        env = dict(os.environ, AGENT_STATE_DIR=str(self.base / "install-state"), PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([sys.executable, str(ROOT / "install.py"), str(self.repo)],
                       env=env, check=True, capture_output=True)

    def findings(self, **kwargs):
        return doctor.inspect(self.repo, which=lambda name: "/synthetic/bin/" + name,
                              environ=kwargs.pop("environ", {}), probe_gh=kwargs.pop("probe_gh", True),
                              gh_check=kwargs.pop("gh_check", lambda _: True), **kwargs)

    def test_normal_install_is_healthy(self):
        self.assertEqual(self.findings(), [])

    def test_direct_entrypoints_require_execution_bits_only_where_needed(self):
        for rel in (".agent-kit/bin/agent-gh", ".agent-kit/bin/agent-log"):
            with self.subTest(entrypoint=rel):
                path = self.repo / rel
                path.chmod(0o644)
                self.assertIn("not executable: " + rel, self.findings())
                path.chmod(0o755)
        for rel in (".agent-kit/bin/agent-publish", ".agent-kit/bin/setup-deps.sh",
                    ".agent-kit/lib/agentdoctor.py"):
            (self.repo / rel).chmod(0o644)
        self.assertEqual(self.findings(), [])

    def test_missing_core_and_project_tools(self):
        (self.repo / "package.json").write_text("{}")
        problems = doctor.inspect(self.repo, which=lambda name: None if name in {"git", "node"} else name,
                                  environ={}, probe_gh=True, gh_check=lambda _: True)
        self.assertEqual(problems, ["missing: tool git", "missing: tool node"])

    def test_missing_file_and_invalid_configuration(self):
        cfg = self.repo / ".agent-kit/config.env"
        cfg.unlink()
        self.assertIn("missing: .agent-kit/config.env", self.findings())
        for text in ('AGENT_POLISH_REQUIRED=invalid\n', 'AGENT_STEP_TIMEOUT=0\n',
                     'AGENT_OTHER="unterminated\n', 'AGENT_OTHER=one\nAGENT_OTHER=two\n'):
            with self.subTest():
                cfg.write_text(text)
                self.assertIn("invalid: .agent-kit/config.env", self.findings())

    def test_invalid_manifest_shapes_and_json(self):
        p = self.repo / ".agent-kit/install-manifest.json"
        for content in ("null", "[]", "{", '{"files": {}, "targets": ["unexpected"]}'):
            with self.subTest():
                p.write_text(content)
                self.assertIn("invalid: .agent-kit/install-manifest.json", self.findings())

    def test_invalid_claude_settings(self):
        p = self.repo / ".claude/settings.json"
        for content in ("null", "[]", "{", '{"hooks": []}'):
            with self.subTest():
                p.write_text(content)
                self.assertIn("invalid: .claude/settings.json", self.findings())

    def test_environment_overrides_are_checked_without_values(self):
        secret = "sk-" + "D" * 30
        problems = self.findings(environ={"AGENT_STEP_TIMEOUT": secret})
        self.assertEqual(problems, ["invalid: .agent-kit/config.env"])
        self.assertNotIn(secret, "\n".join(problems))
        cfg = self.repo / ".agent-kit/config.env"
        cfg.write_text("# Defaults with environment overrides\n")
        self.assertEqual(self.findings(environ={"AGENT_POLISH_REQUIRED": "invalid"}),
                         ["invalid: .agent-kit/config.env"])

    def test_config_symlink_is_rejected_without_exposing_target(self):
        outside = self.base / "synthetic-private-settings"
        secret = "ghp_" + "E" * 30
        outside.write_text(secret)
        p = self.repo / ".agent-kit/config.env"
        p.unlink()
        p.symlink_to(outside)
        problems = self.findings()
        self.assertIn("invalid: .agent-kit/config.env", problems)
        self.assertNotIn(secret, "\n".join(problems))
        self.assertNotIn(str(outside), "\n".join(problems))
        self.assertTrue(outside.read_text() == secret, "External fixture changed")

    def fake_path(self, python=True, slurp=True, allow_gh_probe=True):
        bindir = self.base / "bin"
        bindir.mkdir()
        marker = self.base / "tool-was-executed"
        for name in ("bash", "git", "node", "npm"):
            p = bindir / name
            p.write_text("#!/bin/sh\nprintf 'unexpected' > '" + str(marker) + "'\n")
            p.chmod(0o755)
        gh = bindir / "gh"
        gh.write_text(("#!/bin/sh\n"
                      "if [ \"$#\" -ne 2 ] || [ \"$1\" != api ] || [ \"$2\" != --help ]; then\n"
                      "  printf 'unexpected' > '" + str(marker) + "'\n  exit 9\nfi\n"
                      "printf 'FLAGS\\n    " + ("--slurp" if slurp else "--paginate") + "  synthetic help\\n'\n")
                      if allow_gh_probe else "#!/bin/sh\nprintf 'unexpected' > '" + str(marker) + "'\n")
        gh.chmod(0o755)
        if python:
            (bindir / "python3").symlink_to(sys.executable)
        env = dict(os.environ, PATH=str(bindir), AGENT_REPO_ROOT=str(self.repo),
                   AGENT_STATE_DIR=str(self.base / "doctor-state"))
        return env, marker

    def snapshot(self):
        return {p.relative_to(self.base).as_posix():
                ("link", os.readlink(p)) if p.is_symlink() else
                ("file", hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode, p.stat().st_mtime_ns)
                if p.is_file() else ("dir", p.stat().st_mode, p.stat().st_mtime_ns)
                for p in self.base.rglob("*")}

    def run_command(self, env, *args):
        return subprocess.run(["/bin/bash", str(self.repo / ".agent-kit/bin/setup-deps.sh"), *args],
                              env=env, cwd=self.repo, capture_output=True, text=True)

    def test_check_and_doctor_do_not_write_run_other_tools_or_expose_values(self):
        env, marker = self.fake_path()
        before = self.snapshot()
        for command in ("check", "doctor"):
            result = self.run_command(env, command, "--probe-gh")
            self.assertEqual(result.returncode, 0)
            self.assertIn("doctor: ok", result.stdout)
            self.assertNotIn(str(self.base), result.stdout + result.stderr)
            self.assertTrue(self.snapshot() == before, "Read-only diagnostics changed the fixture")
        secret = "sk-" + "F" * 30
        (self.repo / ".agent-kit/config.env").write_text("AGENT_STEP_TIMEOUT=" + secret + "\n")
        result = self.run_command(env, "doctor", "--probe-gh")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(str(self.base), result.stdout + result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.base / "doctor-state").exists())

    def test_missing_python_and_invalid_arguments_do_not_echo_input(self):
        env, _ = self.fake_path(python=False)
        result = self.run_command(env, "doctor")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "missing: tool python3")
        secret = "ghp_" + "G" * 30
        result = self.run_command(env, "doctor", secret)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(str(self.base), result.stdout + result.stderr)

    def test_claude_project_selection_preserves_root_priority(self):
        env, _ = self.fake_path()
        alternate = self.base / "alternate"
        (alternate / ".agent-kit").mkdir(parents=True)
        env["CLAUDE_PROJECT_DIR"] = str(alternate)
        self.assertEqual(self.run_command(env, "doctor", "--probe-gh").returncode, 0)
        env.pop("AGENT_REPO_ROOT")
        result = self.run_command(env, "doctor")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing: .agent-kit/config.env", result.stdout)
        self.assertNotIn(str(alternate), result.stdout + result.stderr)

    def test_unsupported_gh_fails_without_changing_files(self):
        env, marker = self.fake_path(slurp=False)
        before = self.snapshot()
        result = self.run_command(env, "doctor", "--probe-gh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unsupported: gh api --slurp", result.stdout)
        self.assertNotIn(str(self.base), result.stdout + result.stderr)
        self.assertTrue(self.snapshot() == before, "Unsupported CLI check changed the fixture")
        self.assertFalse(marker.exists())

    def test_default_does_not_execute_gh_and_reports_compatibility_unverified(self):
        env, marker = self.fake_path(allow_gh_probe=False)
        before = self.snapshot()
        for command in ("doctor", "check"):
            result = self.run_command(env, command)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unverified: gh api --slurp support", result.stdout)
            self.assertIn("--probe-gh", result.stdout)
            self.assertNotIn("doctor: ok", result.stdout)
            self.assertTrue(self.snapshot() == before, "Default diagnostics executed a tool or wrote files")
        self.assertFalse(marker.exists())

    def test_gh_probe_is_help_only_and_does_not_forward_credentials(self):
        calls = []
        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "FLAGS\n    --slurp  synthetic help\n", "")
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic", "GITHUB_TOKEN": "synthetic", "GH_HOST": "synthetic.invalid"}):
            self.assertTrue(doctor.gh_api_support("/synthetic/bin/gh", run=run))
        cmd, options = calls[0]
        self.assertEqual(cmd, ["/synthetic/bin/gh", "api", "--help"])
        self.assertEqual(options["timeout"], 5)
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):
            self.assertNotIn(key, options["env"])
        self.assertEqual(options["env"]["GH_NO_UPDATE_NOTIFIER"], "1")
        self.assertEqual(options["env"]["GH_PROMPT_DISABLED"], "1")

    def test_gh_probe_failure_and_timeout_do_not_expose_output(self):
        secret = "ghp_" + "H" * 30
        def failure(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, secret, str(self.base))
        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5, output=secret, stderr=str(self.base))
        for run in (failure, timeout):
            with self.subTest():
                problems = self.findings(gh_check=lambda executable: doctor.gh_api_support(executable, run=run))
                self.assertEqual(problems, ["unverified: gh api --slurp support"])
                self.assertNotIn(secret, "\n".join(problems))
                self.assertNotIn(str(self.base), "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
