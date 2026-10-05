"""Bounded tracked-source check; prints filenames only, never matched values.

This detects common credential formats, not every possible secret. It never reads
untracked .env files, host credential stores, or connected-account credentials.
"""

import json
import re
import subprocess
from pathlib import Path

PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def main():
    names = subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    findings = []
    for name in filter(None, names):
        file = Path(name)
        if file.name == ".env" or file.suffix in {".pem", ".key", ".dump"}:
            findings.append(name)
            continue
        if file.suffix not in {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".sh"}:
            continue
        # CI removes previously generated reports from the working tree. Inspect
        # the committed/staged Git blob, so that deletion cannot bypass this check.
        size = int(subprocess.check_output(["git", "cat-file", "-s", ":" + name], text=True))
        if size > 2_000_000:
            raise SystemExit("tracked-source check requires review of oversized artifact: " + name)
        content = subprocess.check_output(["git", "show", ":" + name]).decode(errors="replace")
        if any(pattern.search(content) for pattern in PATTERNS):
            findings.append(name)
    print(
        json.dumps(
            {
                "check": "common tracked credential formats",
                "findings": findings,
                "exhaustive": False,
            }
        )
    )
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
