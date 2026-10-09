"""Sharing/setup checks use temporary repos and stub tools; no servers or downloads."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.check_share import check_index, inspect_entry

ROOT = Path(__file__).resolve().parents[1]


class SharingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="whatsapp-share-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/"checkout with spaces"
        self.root.mkdir()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.DEVNULL)

    def test_private_paths_and_credential_patterns_are_blocked(self):
        for path in ("runtime/control-token", "runtime/outreach-review.json", ".env.production", ".envrc",
                     "nested/store/export.json", ".codex/auth.json", "contacts.vcf", "messages.db-wal",
                     "backup.sqlite3", "backup.zip", "api-token"):
            with self.subTest(path=path):
                self.assertTrue(inspect_entry(path, "100644", b"harmless"))
        self.assertTrue(inspect_entry("config.py", "100644", ("sk-" + "x"*40).encode()))
        self.assertTrue(inspect_entry("config.py", "100644", ("password = '" + "x"*32 + "'").encode()))
        self.assertTrue(inspect_entry("outside", "120000", b"/some/private/directory"))
        self.assertFalse(inspect_entry("control/engine.py", "100644", b"token = load_token(path)"))

    def test_scan_checks_index_not_a_sanitized_working_copy(self):
        self.git("init", "-q")
        file = self.root/"settings.py"
        file.write_text("key = '" + "ghp_" + "x"*40 + "'\n")
        self.git("add", "settings.py")
        file.write_text("key = None\n")
        count, issues = check_index(self.root)
        self.assertEqual(count, 1)
        self.assertEqual(issues, [("settings.py", "GitHub-like token")])
        self.git("add", "settings.py")
        self.assertEqual(check_index(self.root), (1, []))

    def test_fresh_git_rules_exclude_private_data(self):
        self.git("init", "-q")
        shutil.copyfile(ROOT/".gitignore", self.root/".gitignore")
        for path in ("runtime/control-token", "runtime/outreach-review.json", ".env.local", "export.vcf", "bin/whatsapp-bridge", ".tools/go/bin/go"):
            file = self.root/path; file.parent.mkdir(parents=True, exist_ok=True); file.write_text("private fixture")
            self.assertEqual(self.git("check-ignore", path).decode().strip(), path)

    def test_setup_uses_checkout_paths_and_never_starts_services(self):
        shutil.copytree(ROOT/"scripts", self.root/"scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root/"whatsapp-bridge").mkdir()
        (self.root/"whatsapp-bridge/go.mod").write_text("module fixture\n\ngo 1.26.0\n")
        (self.root/"whatsapp-mcp-server").mkdir()
        stubs = self.root/"stubs"; stubs.mkdir()
        trace = self.root/"trace"
        go = stubs/"go"
        go.write_text('#!/bin/bash\nset -eu\nprintf "go:%s\\n" "$*" >> "$TEST_TRACE"\nif [ "$1" = env ]; then echo "${TEST_GO_VERSION:-go1.27.1}"; exit; fi\n[ "$1" = build ]\nwhile [ "$1" != -o ]; do shift; done\nshift\nprintf "fixture" > "$1"\n')
        go.chmod(0o700)
        uv = stubs/"uv"
        uv.write_text('#!/bin/bash\nset -eu\nprintf "uv:%s\\n" "$*" >> "$TEST_TRACE"\n[ "$1" = sync ] && [ "$2" = --locked ]\n[ "$UV_PROJECT_ENVIRONMENT" = "$WHATSAPP_ROOT/.venv" ]\n')
        uv.chmod(0o700)
        env = dict(os.environ, PATH=str(stubs)+":"+os.environ["PATH"], TEST_TRACE=str(trace))
        result = subprocess.run(["bash", str(self.root/"scripts/setup.sh")], cwd="/tmp", env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/"bin/whatsapp-bridge").read_text(), "fixture")
        lines = trace.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("--locked", lines[1])
        self.assertIn("go:build -mod=readonly -p 2", lines[2])
        self.assertIn("No server", result.stdout)
        trace.write_text("")
        too_old = subprocess.run(["bash", str(self.root/"scripts/setup.sh")], cwd="/tmp", env=dict(env, TEST_GO_VERSION="go1.25.0"), capture_output=True, text=True)
        self.assertNotEqual(too_old.returncode, 0)
        self.assertEqual(trace.read_text().splitlines(), ["go:env GOVERSION"])


if __name__ == "__main__": unittest.main()
