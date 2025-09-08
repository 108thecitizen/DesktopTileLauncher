#!/usr/bin/env python3
"""Tiny helper to detect if the Python package index is reachable."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def _log(msg: str) -> None:
    if "--verbose" in sys.argv[1:]:
        print(msg)


def main() -> int:
    """Return 0 if online, non-zero otherwise.

    An exit code of 99 indicates that offline mode was requested via the
    ``MAKE_OFFLINE`` environment variable.
    """

    if os.environ.get("MAKE_OFFLINE") == "1":
        _log("[netprobe] MAKE_OFFLINE=1 → forcing offline mode")
        return 99

    index = os.environ.get("PIP_INDEX_URL", "https://pypi.org/simple/")
    url = index.rstrip("/") + "/wheel/"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:  # nosec - fixed URL
            if 200 <= resp.status < 300:
                _log(f"[netprobe] {url} reachable (status {resp.status})")
                return 0
            _log(f"[netprobe] {url} responded with status {resp.status}")
            return 98
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        _log(f"[netprobe] error probing {url}: {exc}")
        return 97


if __name__ == "__main__":  # pragma: no cover - command-line entry
    sys.exit(main())
