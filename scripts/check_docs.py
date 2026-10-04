"""Fail on broken repository-relative Markdown links in authored documentation."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def main():
    root = Path.cwd().resolve()
    failures = []
    files = [root / "README.md", root / "SECURITY.md", *sorted((root / "docs").rglob("*.md"))]
    for source in files:
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", source.read_text()):
            url = urlsplit(target)
            if url.scheme or target.startswith("#"):
                continue
            path = (source.parent / unquote(url.path)).resolve()
            if not path.is_relative_to(root) or not path.exists():
                failures.append(f"{source.relative_to(root)}: {target}")
    if failures:
        raise SystemExit("Broken local documentation links:\n" + "\n".join(failures))
    print(f"Repository-relative links valid across {len(files)} documentation files.")


if __name__ == "__main__":
    main()
