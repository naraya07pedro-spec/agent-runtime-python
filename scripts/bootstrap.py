"""Generate local sandbox credentials without printing or overwriting existing secrets."""

import os
import secrets
from pathlib import Path


def main():
    destination = Path(".env")
    if destination.exists():
        print("Existing local configuration left unchanged; contents were not read.")
        return
    source = Path(".env.example").read_text()
    keys = {
        "RUNTIME_API_KEY",
        "RUNTIME_APPROVAL_KEY",
        "RUNTIME_OPERATOR_KEY",
        "RUNTIME_WEBHOOK_SECRET",
        "RUNTIME_TOOL_API_TOKEN",
    }
    lines = []
    changed = False
    for line in source.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in keys and not value.strip():
            line = key + "=" + secrets.token_urlsafe(32)
            changed = True
        lines.append(line)
    if changed or not destination.exists():
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write("\n".join(lines) + "\n")
        destination.chmod(0o600)
    print("Local configuration is ready. Existing nonempty keys were preserved.")


if __name__ == "__main__":
    main()
