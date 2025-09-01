"""Windows-specific helpers for interacting with Google Chrome profiles.

These helpers detect the Windows default browser, locate Chrome installations,
list available Chrome user profiles, and launch Chrome with a specific profile.
All functions fail safely on non-Windows platforms and never raise
platform-specific errors to callers.
"""

# mypy: disable-error-code=unreachable

from __future__ import annotations

import json
import os
import subprocess
import sys
import typing as t
from typing import Literal

if sys.platform == "win32":  # pragma: no cover - executed only on Windows
    import winreg
else:  # pragma: no cover - not executed on Windows
    winreg = t.cast(t.Any, None)


def is_windows_default_browser_chrome() -> bool:
    """Return True if the Windows default browser is Google Chrome."""
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            progid, _ = winreg.QueryValueEx(key, "ProgId")
            return isinstance(progid, str) and progid.startswith("ChromeHTML")
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _reg_query_app_paths() -> t.Optional[str]:
    """Return chrome.exe path from Windows App Paths registry, if available."""
    for hive in (
        getattr(winreg, "HKEY_CURRENT_USER", None),
        getattr(winreg, "HKEY_LOCAL_MACHINE", None),
    ):
        if hive is None:
            continue
        try:
            with winreg.OpenKey(
                hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            ) as key:
                val, _ = winreg.QueryValueEx(key, None)
                if isinstance(val, str) and os.path.isfile(val):
                    return val
        except OSError:
            continue
    return None


def find_chrome_exe() -> t.Optional[str]:
    """Locate chrome.exe on Windows, returning the executable path if found."""
    if sys.platform != "win32":
        return None
    reg_path = _reg_query_app_paths()
    if reg_path:
        return reg_path
    candidates: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe")
        )
    candidates.extend(
        [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def is_chrome_path(path: str | None) -> bool:
    """Return True if *path* refers to a chrome.exe executable."""
    if not path:
        return False
    return os.path.basename(path).lower() == "chrome.exe"


def list_chrome_profiles() -> list[tuple[str, str]]:
    """Return a list of available Chrome profiles as (dir_id, display_name)."""
    if sys.platform != "win32":
        return []
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    user_data = os.path.join(local, "Google", "Chrome", "User Data")
    local_state = os.path.join(user_data, "Local State")
    results: list[tuple[str, str]] = []
    info_cache: dict[str, dict[str, t.Any]] = {}
    try:
        with open(local_state, "r", encoding="utf-8") as f:
            state = json.load(f)
            info_cache = state.get("profile", {}).get("info_cache", {}) or {}
    except (OSError, json.JSONDecodeError):
        info_cache = {}
    for dir_id, meta in info_cache.items():
        display = meta.get("gaia_name") or meta.get("name") or dir_id
        results.append((dir_id, f"{display} ({dir_id})"))
    default_dir = os.path.join(user_data, "Default")
    if os.path.isdir(default_dir) and not any(d == "Default" for d, _ in results):
        results.insert(0, ("Default", "Default (Default)"))
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for dir_id, label in results:
        if dir_id not in seen:
            seen.add(dir_id)
            deduped.append((dir_id, label))
    deduped.sort(key=lambda x: (x[0] != "Default", x[1].lower()))
    return deduped


def launch_chrome_with_profile(
    url: str,
    profile_dir_id: str,
    open_target: Literal["tab", "window"] = "tab",
    chrome_path: str | None = None,
) -> bool:
    """Launch Chrome with *profile_dir_id* and open *url*.

    ``open_target`` controls whether the URL opens in a new tab or window.
    Returns True if Chrome was started successfully, otherwise False.
    Failure is silent; callers should fall back to other open mechanisms.
    """
    if sys.platform != "win32":
        return False
    chrome = chrome_path or find_chrome_exe()
    if not chrome:
        return False
    try:
        cmd = [chrome, f"--profile-directory={profile_dir_id}"]
        if open_target == "window":
            cmd.append("--new-window")
        cmd.append(url)
        subprocess.Popen(cmd, close_fds=True)
        return True
    except OSError:
        return False
