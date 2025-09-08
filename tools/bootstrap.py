#!/usr/bin/env python3
"""Bootstrap script for offline-friendly development installs."""

from __future__ import annotations

import pathlib
import ssl
import subprocess
import sys
import urllib.request


def _is_online() -> bool:
    try:
        urllib.request.urlopen(
            "https://pypi.org/simple", timeout=3, context=ssl.create_default_context()
        )
        return True
    except Exception:
        return False


def main() -> int:
    py = sys.executable
    subprocess.run(
        [py, "-m", "ensurepip", "--upgrade"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if _is_online():
        print("[install-dev] Online: upgrading pip and installing dev deps if present")
        subprocess.run([py, "-m", "pip", "install", "-U", "pip", "wheel"], check=False)
        root = pathlib.Path(__file__).resolve().parent.parent
        for name in ("requirements.txt", "requirements-dev.txt"):
            path = root / name
            if path.exists():
                subprocess.run(
                    [py, "-m", "pip", "install", "-r", str(path)], check=False
                )
    else:
        print("[install-dev] Offline detected: skipping dev dependency installation")
    return 0


if __name__ == "__main__":  # pragma: no cover - command-line entry
    raise SystemExit(main())
