#!/usr/bin/env python3
"""Offline-friendly smoke check for core modules."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from debug_scaffold import collect_runtime_context
from browser_chrome_win import (
    list_chrome_profiles,
    is_chrome_path,
    is_windows_default_browser_chrome,
)

ctx = collect_runtime_context(None)
assert isinstance(ctx, dict) and "available_browsers" in ctx
list_chrome_profiles()
is_chrome_path("chrome.exe")
is_chrome_path(None)
assert isinstance(is_windows_default_browser_chrome(), bool)
print("smoke-ok")
