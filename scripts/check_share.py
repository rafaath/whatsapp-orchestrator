"""Inspect the actual Git index without printing secrets or starting services."""

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PRIVATE_DIRS = {"runtime", "store", ".cache", ".tools", ".venv", "bin", ".codex", ".agents", ".cursor", "__pycache__"}
PRIVATE_NAMES = {"api-token", "control-token", "pairing-qr.png", "auth.json", "credentials.json", "secrets.json", "mcp.json", "claude_desktop_config.json"}
PRIVATE_SUFFIX = re.compile(r"(?:\.(?:db|sqlite3?)(?:[-.].*)?|\.(?:log|vcf|pem|key|p12|pfx|zip|7z|tar|tgz|pyc)|\.tar\.gz)$", re.I)
SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "OpenAI-like key": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    "GitHub-like token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "AWS-like access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "literal credential": re.compile(rb'''(?i)\b(?:api[_-]?key|api[_-]?token|control[_-]?token|client[_-]?secret|password)\s*[=:]\s*["'][A-Za-z0-9_+/=-]{24,}["']'''),
}


def inspect_entry(path, mode, content):
    issues = []
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    if any(part.lower() in PRIVATE_DIRS for part in parts) or name in PRIVATE_NAMES or name.startswith(".env") or PRIVATE_SUFFIX.search(name):
        issues.append("private/runtime file staged")
    if mode not in {"100644", "100755"}:
        issues.append("symlink, submodule or unsupported index entry; inspect manually")
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(content):
            issues.append(label)
    return issues


def check_index(root):
    listing = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root)
    issues, count = [], 0
    for item in listing.split(b"\0"):
        if not item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        path = raw_path.decode("utf-8", errors="replace")
        if stage != "0":
            issues.append((path, "unmerged file")); continue
        size = int(subprocess.check_output(["git", "cat-file", "-s", oid], cwd=root))
        if size > 8 * 1024 * 1024:
            issues.append((path, "file exceeds source-review limit; inspect manually")); continue
        content = subprocess.check_output(["git", "cat-file", "-p", oid], cwd=root)
        issues.extend((path, issue) for issue in inspect_entry(path, mode, content))
        count += 1
    return count, issues


if __name__ == "__main__":
    count, issues = check_index(Path(__file__).resolve().parents[1])
    for path, reason in issues:
        print(f"BLOCKED: {path}: {reason}")
    if issues:
        raise SystemExit(1)
    print(f"Checked {count} indexed files: no private paths or common credential patterns found.")
    print("Review the staged diff for personal content too. This check does not inspect old commits or prove the absence of every secret.")
