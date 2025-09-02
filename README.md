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

## Multiple windows

DesktopTileLauncher supports multiple peer windows. Create another window from
**Window → New Window** or from the system tray icon's **New Window** action.
The tray icon remains after all windows close so new ones can be spawned; use
**Quit** in the tray to exit the app.

Open windows persist across restarts. Each window's ID, geometry, maximized
state, and last selected tab are stored in `config.json` under a `windows`
array. On startup, one window per entry is restored (if none exist, a single
window opens with the first tab).

Windows receive model updates lazily: changes made on a background tab are not
rendered in other windows until that tab is selected, avoiding unnecessary
rebuilds.
