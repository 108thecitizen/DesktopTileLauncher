# SPDX-License-Identifier: Apache-2.0
import re
import sys
from pathlib import Path

SPDX = "# SPDX-License-Identifier: Apache-2.0\n"
CODING_RE = re.compile(rb"^#.*coding[:=]\s*([-\w.]+)")


def add_spdx(path: Path):
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)

    # Already tagged?
    for line in lines[:5]:
        try:
            s = line.decode("utf-8")
        except Exception:
            s = ""
        if s.startswith("# SPDX-License-Identifier:"):
            return False

    i = 0
    # Preserve shebang
    if lines and lines[0].startswith(b"#!"):
        i += 1
    # Preserve encoding cookie
    if i < len(lines) and CODING_RE.match(lines[i]):
        i += 1

    new = b"".join(lines[:i]) + SPDX.encode() + b"".join(lines[i:])
    path.write_bytes(new)
    return True


def main(root: Path):
    changed = 0
    for p in root.rglob("*.py"):
        if any(
            seg in p.parts
            for seg in (
                ".git",
                "dist",
                "build",
                ".venv",
                "venv",
                "__pycache__",
                ".pytest_cache",
            )
        ):
            continue
        changed += 1 if add_spdx(p) else 0
    print(f"SPDX headers added to {changed} file(s).")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
