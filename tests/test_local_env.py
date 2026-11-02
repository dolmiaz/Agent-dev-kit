#!/usr/bin/env python3
"""Local env loading: synthetic credentials only, no network or Gemini process."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_env", ROOT / "local/with-gemini-env.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
FAKE = "synthetic-not-a-real-key"


class LocalEnvTests(unittest.TestCase):
    def fixture(self, root, content, mode=0o600):
        p = root / ".env"
        p.write_text(content)
        p.chmod(mode)
        return p

    def test_plain_and_quoted_assignments(self):
        with tempfile.TemporaryDirectory() as tmp:
            for assignment in (f"GEMINI_API_KEY={FAKE}", f'export GEMINI_API_KEY="{FAKE}"',
                               f"GEMINI_API_KEY='{FAKE}'"):
                for mode in (0o600, 0o400):
                    p = self.fixture(Path(tmp), "# local only\n" + assignment + "\n", mode)
                    self.assertEqual(loader.read_key(p), FAKE)
                    p.chmod(0o600)

    def test_rejects_shell_code_other_keys_empty_and_duplicate_values(self):
        cases = ["GEMINI_API_KEY=", "UNRELATED=secret", "GEMINI_API_KEY=$(touch marker)",
                 "GEMINI_API_KEY=`touch marker`", "GEMINI_API_KEY=${OTHER}",
                 f"GEMINI_API_KEY={FAKE}\nGEMINI_API_KEY={FAKE}",
                 f"GEMINI_API_KEY={FAKE}\nAGENT_POLISH_REQUIRED=0"]
        with tempfile.TemporaryDirectory() as tmp:
            for text in cases:
                p = self.fixture(Path(tmp), text)
                with self.assertRaises(ValueError):
                    loader.read_key(p)
            self.assertFalse((Path(tmp) / "marker").exists())

    def test_opaque_key_can_contain_dots_and_base64_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = FAKE + ".opaque/segment+suffix=="
            p = self.fixture(Path(tmp), f"GEMINI_API_KEY={value}\n")
            self.assertEqual(loader.read_key(p), value)

    def test_rejects_symlink_public_permissions_nonregular_and_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self.fixture(root, f"GEMINI_API_KEY={FAKE}", 0o644)
            with self.assertRaises(ValueError):
                loader.read_key(target)
            target.chmod(0o600)
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                loader.read_key(link)
            pipe = root / "pipe"
            os.mkfifo(pipe, 0o600)
            with self.assertRaises(ValueError):
                loader.read_key(pipe)
            target.write_text("#" * (loader.LIMIT + 1))
            with self.assertRaises(ValueError):
                loader.read_key(target)

    def test_check_never_executes_or_prints_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, f"GEMINI_API_KEY={FAKE}")
            output = io.StringIO()
            with patch.object(loader, "ROOT", root), patch.object(loader.os, "execve") as execute, contextlib.redirect_stdout(output):
                self.assertEqual(loader.main(["--check"]), 0)
            execute.assert_not_called()
            self.assertNotIn(FAKE, output.getvalue())
            self.assertIn("no authentication or request", output.getvalue())

    def test_invalid_input_never_executes_or_prints_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, f"UNKNOWN={FAKE}")
            output = io.StringIO()
            with patch.object(loader, "ROOT", root), patch.object(loader.os, "execve") as execute, contextlib.redirect_stderr(output):
                self.assertEqual(loader.main(["--check"]), 2)
            execute.assert_not_called()
            self.assertNotIn(FAKE, output.getvalue())

    def test_only_key_mapping_added_and_gate_settings_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, f"GEMINI_API_KEY={FAKE}")
            base_env = {"PATH": "/usr/bin", "AGENT_POLISH_REQUIRED": "1", "AGENT_GEMINI_SEND_APPROVED": "0"}
            with patch.object(loader, "ROOT", root), patch.dict(os.environ, base_env, clear=True), patch.object(loader.os, "chdir"), patch.object(loader.os, "execve", side_effect=RuntimeError("intercepted")) as execute:
                with self.assertRaisesRegex(RuntimeError, "intercepted"):
                    loader.main(["polish", "--kind", "pr-title", "--file", "title.txt"])
            exe, argv, env = execute.call_args.args
            self.assertEqual(argv[2:], ["polish", "--kind", "pr-title", "--file", "title.txt"])
            self.assertEqual(Path(argv[1]), root / "kit/.agent-kit/bin/agent-publish")
            self.assertEqual(env, dict(base_env, GEMINI_API_KEY=FAKE, AGENT_GEMINI_KEY_ENV="GEMINI_API_KEY"))
            self.assertNotIn(FAKE, " ".join(argv))


if __name__ == "__main__":
    unittest.main()
