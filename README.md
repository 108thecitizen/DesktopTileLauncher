# DesktopTileLauncher
[?? Download the latest release](https://github.com/108thecitizen/DesktopTileLauncher/releases/latest)
[![Build](https://github.com/108thecitizen/DesktopTileLauncher/actions/workflows/build.yml/badge.svg?branch=master)](https://github.com/108thecitizen/DesktopTileLauncher/actions/workflows/build.yml)
[![Release](https://github.com/108thecitizen/DesktopTileLauncher/actions/workflows/release-tag.yml/badge.svg)](https://github.com/108thecitizen/DesktopTileLauncher/actions/workflows/release-tag.yml)


License: MIT

## Usage

On Windows, selecting Google Chrome for a tile (or leaving the browser as
Default when Chrome is the system default) reveals a **Chrome profile**
dropdown. The list is populated from Chrome's local profile cache and includes
entries like `Default`, `Profile 1`, or names from signed-in Google accounts.
Choosing a profile pins the tile to that persona; select **None** to use
Chrome's last-used profile.

Each tile also provides an **Open in** option:

* **New tab in existing window** *(default)*
* **New browser window**

For Chromium browsers (Chrome/Edge), a new window adds the `--new-window`
switch. Firefox uses `--new-tab` or `--new-window`. If the tile targets the
system's default browser, Python's `webbrowser.open` is used with `new=2` for a
tab or `new=1` for a window. Safari and other unknown browsers fall back to
this behavior and may not differentiate between tabs and windows.

Existing configurations that lack this setting are automatically migrated and
default to opening URLs in a new tab.

## Debugging & Crash Reports

Desktop Tile Launcher writes JSON logs to a rotating `debug.log` in a per-user
directory:

* **Windows:** `%LOCALAPPDATA%/DesktopTileLauncher/`
* **macOS:** `~/Library/Logs/DesktopTileLauncher/`
* **Linux:** `$XDG_STATE_HOME/DesktopTileLauncher/` or
  `~/.local/state/DesktopTileLauncher/`

The application never sends data over the network.  When something goes wrong,
use the *Create Crash Bundle* button on the crash dialog to zip the log files
and a `crash.json` snapshot of runtime context.  Attach this bundle when filing
a GitHub issue.
