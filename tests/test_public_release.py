#!/usr/bin/env python3
"""Public-release regression tests. Only synthetic values; no external calls."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish_check", ROOT / "tools/publish-check.py")
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class PublicReleaseTests(unittest.TestCase):
    def test_values_never_appear_in_findings(self):
        samples = [
            "sk-" + "A" * 30,
            "AIza" + "B" * 35,
            "ghp_" + "C" * 30,
            "private.person@" + "test-company.org",
            "/home/" + "private-person",
        ]
        for value in samples:
            with self.subTest(value_type=value[:2]):
                hits = scanner.scan_text("sample.txt", value, scanner.GENERIC)
                self.assertTrue(hits)
                self.assertNotIn(value, "\n".join(hits))
                self.assertTrue(all("[REDACTED]" in hit for hit in hits))

    def test_polish_scan_does_not_expose_personal_prefix(self):
        sys.path.insert(0, str(ROOT / "kit/.agent-kit/lib"))
        import agentpub
        value = "person@" + "users.noreply.github.com"
        hits = agentpub.scan(value, {})
        self.assertTrue(hits)
        self.assertNotIn(value[:4], "\n".join(hits))

    def test_examples_are_allowed(self):
        self.assertEqual(scanner.scan_text("sample", "bot@example.com", scanner.GENERIC), [])

    def test_env_sample_requires_blank_assignments_and_still_scans_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / ".env.sample"
            sample.write_text("# public template\nGEMINI_API_KEY=\n")
            self.assertEqual(scanner.scan_tree(root, scanner.GENERIC, set()), [])
            for text in ("GEMINI_API_KEY=synthetic-not-real\n", "GEMINI_API_KEY=\"\"\n",
                         "export GEMINI_API_KEY=\n", "# comments only\n"):
                sample.write_text(text)
                hits = scanner.scan_tree(root, scanner.GENERIC, set())
                self.assertTrue(hits)
                self.assertNotIn("synthetic-not-real", "\n".join(hits))
            sample.write_text("# " + "ghp_" + "A" * 30 + "\nGEMINI_API_KEY=\n")
            self.assertTrue(scanner.scan_tree(root, scanner.GENERIC, set()))
            sample.write_text("GEMINI_API_KEY=\n")
            (root / ".env").write_text("not inspected")
            (root / ".env.production").write_text("not inspected")
            hits = scanner.scan_tree(root, scanner.GENERIC, set())
            self.assertEqual(len(hits), 2)

    def test_sample_history_rejects_deleted_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = self.make_repo(root)
            sample = root / ".env.sample"
            sample.write_text("GEMINI_API_KEY=\n")
            git("add", ".env.sample")
            git("commit", "-m", "Blank sample")
            self.assertEqual(scanner.scan_history(root, scanner.GENERIC), [])
            sample.write_text("GEMINI_API_KEY=synthetic-not-real\n")
            git("add", ".env.sample")
            git("commit", "-m", "Synthetic populated sample")
            sample.write_text("GEMINI_API_KEY=\n")
            git("add", ".env.sample")
            git("commit", "-m", "Blank sample again")
            hits = scanner.scan_history(root, scanner.GENERIC)
            self.assertTrue(hits)
            self.assertNotIn("synthetic-not-real", "\n".join(hits))

    def make_repo(self, root):
        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args], check=True,
                                  capture_output=True)
        git("init")
        git("config", "user.name", "Test Agent")
        git("config", "user.email", "test@example.invalid")
        git("config", "commit.gpgsign", "false")
        return git

    def test_deleted_binary_history_fails_closed_without_exposing_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = self.make_repo(root)
            value = "ghp_" + "D" * 30
            (root / "sample.bin").write_bytes(b"\0" + value.encode())
            git("add", ".")
            git("commit", "-m", "Synthetic binary fixture")
            git("rm", "sample.bin")
            git("commit", "-m", "Remove fixture")
            hits = scanner.scan_history(root, scanner.GENERIC)
            self.assertTrue(hits)
            self.assertNotIn(value, "\n".join(hits))
            result = subprocess.run([sys.executable, str(ROOT / "tools/publish-check.py"),
                                     str(root), "--history"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_every_git_read_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = self.make_repo(root)
            (root / "sample.txt").write_text("fixture")
            git("add", ".")
            git("commit", "-m", "Synthetic fixture")
            real_run = subprocess.run
            for operation in ("rev-list", "ls-tree", "rev-parse", "cat-file", "log"):
                def fail_one(args, **kwargs):
                    if args[3] == operation:
                        return subprocess.CompletedProcess(args, 1, b"", b"private diagnostic")
                    return real_run(args, **kwargs)
                with self.subTest(operation=operation), patch.object(scanner.subprocess, "run", side_effect=fail_one):
                    hits = scanner.scan_history(root, scanner.GENERIC)
                    self.assertTrue(hits)
                    self.assertNotIn("private diagnostic", "\n".join(hits))

    def test_shallow_history_fails_closed_without_exposing_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            git = self.make_repo(source)
            value = "ghp_" + "E" * 30
            (source / "sample.txt").write_text(value)
            git("add", ".")
            git("commit", "-m", "Synthetic history fixture")
            git("rm", "sample.txt")
            (source / "README.md").write_text("Clean current tree\n")
            git("add", ".")
            git("commit", "-m", "Remove synthetic value")
            self.assertTrue(scanner.scan_history(source, scanner.GENERIC))

            shallow = Path(tmp) / "shallow"
            subprocess.run(["git", "clone", "--depth", "1", source.as_uri(), str(shallow)],
                           check=True, capture_output=True)
            count = subprocess.run(["git", "-C", str(shallow), "rev-list", "--count", "HEAD"],
                                   check=True, capture_output=True, text=True)
            self.assertEqual(count.stdout.strip(), "1")
            result = subprocess.run([sys.executable, str(ROOT / "tools/publish-check.py"),
                                     str(shallow), "--history"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("shallow clone", result.stdout)
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_public_copy_contains_documentation_and_no_private_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "public"
            result = subprocess.run(["bash", str(ROOT / "tools/make-public.sh"), str(out)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "Public-copy validation failed; inspect locally.")
            for name in (".gitignore", ".env.sample", "README.md", "README.ja.md", "local/with-gemini-env.py", "docs/10-publish-polish.md", "SECURITY.md", "LICENSE", "LICENSE-NOTICE.md"):
                self.assertTrue((out / name).is_file(), name)
            self.assertEqual((out / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            for name in ("personal", "reports", ".git", ".env"):
                self.assertFalse((out / name).exists(), name)


class InstallerSafetyTests(unittest.TestCase):
    def repo(self, root):
        root.mkdir()
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        return root

    def run_installer(self, repo, state, *args):
        env = dict(os.environ, AGENT_STATE_DIR=str(state), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(ROOT / "install.py"), str(repo), *args],
                              env=env, capture_output=True, text=True)

    def snapshot(self, root):
        if not root.exists():
            return None
        out = {}
        for p in root.rglob("*"):
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_file():
                out[rel] = ("file", p.read_bytes(), p.stat().st_mode)
            else:
                out[rel] = ("dir", p.stat().st_mode)
        return out

    def test_symlink_targets_fail_before_install_changes(self):
        cases = [(".claude", True), (".claude/settings.json", False),
                 (".claude/skills", True), (".agents/skills", True),
                 ("AGENTS.md", False), (".agent-kit", True),
                 (".agent-kit/install-manifest.json", False),
                 (".github/ISSUE_TEMPLATE/agent-task.yml", False),
                 (".claude/skills/publish-polish/SKILL.md", False)]
        for rel, directory in cases:
            with self.subTest(target=rel), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                repo = self.repo(base / "repo")
                external = base / "external"
                external.mkdir()
                marker = external / "settings.json"
                marker.write_text('{"user":"synthetic marker"}\n')
                p = repo / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.symlink_to(external if directory else marker, target_is_directory=directory)
                before, outside = self.snapshot(repo), self.snapshot(external)
                state = base / "state"
                result = self.run_installer(repo, state, "--github-templates")
                self.assertEqual(result.returncode, 2)
                self.assertTrue(self.snapshot(repo) == before, "Target tree changed unexpectedly")
                self.assertTrue(self.snapshot(external) == outside, "External tree changed unexpectedly")
                self.assertFalse(state.exists())

    def test_backup_tree_and_state_symlinks_fail_before_changes(self):
        for location in ("backup-source", "backup-destination", "dangling-target"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                repo = self.repo(base / "repo")
                external = base / "external"
                external.mkdir()
                marker = external / "marker.txt"
                marker.write_text("synthetic external data\n")
                state = base / "state"
                if location == "backup-source":
                    p = repo / ".github/workflows/keep.yml"
                    p.parent.mkdir(parents=True)
                    p.symlink_to(marker)
                elif location == "backup-destination":
                    state.symlink_to(external, target_is_directory=True)
                else:
                    p = repo / ".claude/settings.json"
                    p.parent.mkdir()
                    p.symlink_to(external / "missing.json")
                before, outside = self.snapshot(repo), self.snapshot(external)
                result = self.run_installer(repo, state)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(self.snapshot(repo) == before, "Target tree changed unexpectedly")
                self.assertTrue(self.snapshot(external) == outside, "External tree changed unexpectedly")
                if location != "backup-destination":
                    self.assertFalse(state.exists())

    def test_manifest_cannot_select_unowned_or_outside_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            installed = self.repo(base / "installed")
            self.assertEqual(self.run_installer(installed, base / "initial-state").returncode, 0)
            outside = base / "outside.txt"
            outside.write_text("synthetic external value\n")
            empty = base / "outside-empty"
            empty.mkdir()
            digest = hashlib.sha256(outside.read_bytes()).hexdigest()
            cases = [("files", "../outside.txt"), ("files", str(outside)),
                     ("files", ".git/config"), ("files", "README.md"),
                     ("blocks", "../outside.txt"), ("blocks", str(outside)),
                     ("created_dirs", "../outside-empty"), ("created_dirs", str(empty)),
                     ("created_dirs", ".git")]
            for index, (field, value) in enumerate(cases):
                for uninstall in (False, True):
                    with self.subTest(field=field, absolute=value.startswith("/"), uninstall=uninstall):
                        repo = base / f"repo-{index}-{uninstall}"
                        shutil.copytree(installed, repo)
                        (repo / "README.md").write_text("synthetic user file\n")
                        manifest = repo / ".agent-kit/install-manifest.json"
                        man = json.loads(manifest.read_text())
                        if field == "files":
                            man[field][value] = digest
                        elif field == "blocks":
                            man[field].append({"file": value, "created": True})
                        else:
                            man[field].append(value)
                        manifest.write_text(json.dumps(man))
                        before = self.snapshot(repo)
                        state = base / f"state-{index}-{uninstall}"
                        result = self.run_installer(repo, state, *(["--uninstall"] if uninstall else []))
                        self.assertEqual(result.returncode, 2)
                        self.assertTrue(self.snapshot(repo) == before, "Target tree changed unexpectedly")
                        self.assertEqual(outside.read_text(), "synthetic external value\n")
                        self.assertTrue(empty.is_dir())
                        self.assertFalse(state.exists())

    def test_recorded_file_symlinks_fail_before_update_or_uninstall(self):
        for uninstall in (False, True):
            with self.subTest(uninstall=uninstall), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                repo = self.repo(base / "repo")
                self.assertEqual(self.run_installer(repo, base / "initial-state").returncode, 0)
                p = repo / ".agent-kit/bin/services.sh"
                external = base / "external.sh"
                external.write_bytes(p.read_bytes())
                p.unlink()
                p.symlink_to(external)
                before, data = self.snapshot(repo), external.read_bytes()
                state = base / "rejected-state"
                result = self.run_installer(repo, state, *(["--uninstall"] if uninstall else []))
                self.assertEqual(result.returncode, 2)
                self.assertTrue(self.snapshot(repo) == before, "Target tree changed unexpectedly")
                self.assertEqual(external.read_bytes(), data)
                self.assertFalse(state.exists())

    def test_regular_install_update_uninstall_preserve_user_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.repo(base / "repo")
            (repo / ".claude").mkdir()
            settings = repo / ".claude/settings.json"
            original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-hook"}]}]}}
            settings.write_text(json.dumps(original))
            (repo / "AGENTS.md").write_text("User instructions\n")
            state = base / "state"
            self.assertEqual(self.run_installer(repo, state, "--github-templates").returncode, 0)
            before = self.snapshot(repo)
            self.assertEqual(self.run_installer(repo, state, "--github-templates").returncode, 0)
            self.assertTrue(self.snapshot(repo) == before, "Target tree changed unexpectedly")
            modified = repo / ".agent-kit/bin/services.sh"
            modified.write_text(modified.read_text() + "\n# synthetic local modification\n")
            content = modified.read_bytes()
            self.assertEqual(self.run_installer(repo, state, "--github-templates").returncode, 0)
            self.assertEqual(modified.read_bytes(), content)
            self.assertEqual(self.run_installer(repo, state, "--uninstall").returncode, 0)
            self.assertEqual(json.loads(settings.read_text()), original)
            self.assertEqual((repo / "AGENTS.md").read_text(), "User instructions\n")
            self.assertEqual(modified.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
