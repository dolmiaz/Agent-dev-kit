#!/usr/bin/env python3
"""Public-release regression tests. Only synthetic values; no external calls."""
import importlib.util
from pathlib import Path
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

    def test_public_copy_contains_documentation_and_no_private_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "public"
            result = subprocess.run(["bash", str(ROOT / "tools/make-public.sh"), str(out)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "Public-copy validation failed; inspect locally.")
            for name in (".gitignore", "docs/10-publish-polish.md", "SECURITY.md", "LICENSE", "LICENSE-NOTICE.md"):
                self.assertTrue((out / name).is_file(), name)
            self.assertEqual((out / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            for name in ("personal", "reports", ".git"):
                self.assertFalse((out / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
