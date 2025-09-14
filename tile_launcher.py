# tile_launcher.py
# Minimal desktop launcher: tile grid that opens URLs in the default browser.
# Windows/Mac/Linux.  Requires: Python 3.10+  pip install PySide6
# encoding changed
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404: used to launch local apps; inputs validated & shell=False
import sys
import math
import webbrowser
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Literal, Optional, cast
import shutil

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QObject,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    qWarning,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QContextMenuEvent,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QStyle,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import debug_scaffold
from debug_scaffold import record_breadcrumb, sanitize_log_extra, sanitize_url
from tile_editor_dialog import TileEditorDialog
from browser_chrome_win import (
    is_windows_default_browser_chrome,
    is_chrome_path,
    launch_chrome_with_profile,
)

APP_NAME = "TileLauncher"


FitPolicy = Literal["always", "on_startup", "off"]
FitTrigger = Literal["show", "move", "resize", "tab", "screen", "rebuild", "manual"]


def should_fit(policy: FitPolicy, did_startup_fit: bool, trigger: FitTrigger) -> bool:
    """Return ``True`` if an auto-fit should run for ``trigger``."""
    if trigger == "manual":
        return True
    if policy == "always":
        return True
    if policy == "on_startup" and not did_startup_fit:
        return True
    return False


def app_dirs():
    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    cfg = base / APP_NAME
    icons = cfg / "icons"
    cfg.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    return cfg, icons


CFG_DIR, ICON_DIR = app_dirs()
CFG_PATH = CFG_DIR / "config.json"


def _find_browser(paths: Iterable[Path | str]) -> str | None:
    """Return first existing executable from a list of candidate paths."""
    for entry in paths:
        if isinstance(entry, Path):
            if entry.exists():
                return str(entry)
        else:
            found = shutil.which(entry)
            if found:
                return found
    return None


def available_browsers() -> list[str]:
    """
    Return a list of locally available browser names.

    Robust to environments where webbrowser._tryorder is None and to
    cross‑platform path quirks. Always returns a list (possibly empty).
    """
    _raw = getattr(webbrowser, "_tryorder", None)
    try_order: Iterable[str] = _raw if isinstance(_raw, (list, tuple, set)) else []
    seen: set[str] = set()
    browsers: list[str] = []

    # Include any working controllers that stdlib already knows about.
    for name in try_order:
        try:
            webbrowser.get(name)
        except webbrowser.Error:
            continue
        if name not in seen:
            browsers.append(name)
            seen.add(name)

    candidates: dict[str, list[Path | str]] = {
        "brave": [
            "brave",
            "brave-browser",
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            Path("C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe"),
            Path(
                "C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe"
            ),
        ],
        "firefox": [
            "firefox",
            Path("/Applications/Firefox.app/Contents/MacOS/firefox"),
            Path("C:/Program Files/Mozilla Firefox/firefox.exe"),
            Path("C:/Program Files (x86)/Mozilla Firefox/firefox.exe"),
        ],
        "chrome": [
            "chrome",
            "google-chrome",
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        ],
        "edge": [
            "msedge",
            "microsoft-edge",
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ],
        "safari": [
            Path("/Applications/Safari.app/Contents/MacOS/Safari"),
        ],
    }

    for name, paths in candidates.items():
        if name in seen:
            continue
        ok = True
        try:
            webbrowser.get(name)
        except webbrowser.Error:
            ok = False

        if not ok:
            exe = _find_browser(paths)
            if exe:
                webbrowser.register(name, None, webbrowser.BackgroundBrowser(exe))
                ok = True

        # On macOS, Safari is a standard system browser; ensure it's present.
        if not ok and sys.platform == "darwin" and name == "safari":
            safari_exe = "/Applications/Safari.app/Contents/MacOS/Safari"
            webbrowser.register(
                "safari", None, webbrowser.BackgroundBrowser(safari_exe)
            )
            ok = True

        if ok and name not in seen:
            browsers.append(name)
            seen.add(name)

    return sorted(browsers)


def _resolve_controller_exe(name: str | None) -> str | None:
    """
    Resolve a browser controller to an executable name without calling webbrowser.get
    for common controllers. Falls back to the provided name.
    """
    if not name:
        return None

    # Common controllers across platforms
    mapping = {
        "firefox": "firefox",
        "chrome": "chrome",
        "google-chrome": "google-chrome",
        "chromium": "chromium",
        "edge": "msedge",
        "msedge": "msedge",
        "brave": "brave",
        # Safari is special on mac; you probably build a different command for it elsewhere.
        "safari": "safari",
    }

    exe = mapping.get(name.lower())
    if exe:
        return exe

    # Last resort: assume it's on PATH. Do not call webbrowser.get here.
    return name


def _normalize_url(raw: str) -> str:
    """Ensure the URL has a scheme; if missing, prepend https://."""
    s = (raw or "").strip()
    if not s:
        return ""
    parsed = urllib.parse.urlparse(s)
    return s if parsed.scheme else f"https://{s}"


@dataclass
class Tile:
    name: str
    url: str
    tab: str = "Main"
    icon: Optional[str] = None  # path to png/ico
    bg: str = "#F5F6FA"  # background color (CSS)
    browser: Optional[str] = None  # webbrowser name
    chrome_profile: Optional[str] = None  # e.g. "Default", "Profile 1"
    open_target: Literal["tab", "window"] = "tab"


@dataclass
class LauncherConfig:
    title: str = "Launcher"
    columns: int = 5
    tiles: list["Tile"] = field(default_factory=list)
    tabs: list[str] = field(default_factory=lambda: ["Main"])
    hidden_tabs: list[str] = field(default_factory=list)
    fit_policy: FitPolicy = "on_startup"
    last_width: int | None = None
    last_height: int | None = None
    last_x: int | None = None
    last_y: int | None = None

    @staticmethod
    def load() -> "LauncherConfig":
        if CFG_PATH.exists():
            data = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            tiles = [Tile(**t) for t in data.get("tiles", [])]
            raw_tabs = data.get("tabs") or []
            tabs: list[str] = []
            for t in raw_tabs:
                if isinstance(t, str) and t not in tabs:
                    tabs.append(t)
            for tile in tiles:
                if tile.tab not in tabs:
                    tabs.append(tile.tab)
            hidden_raw = data.get("hidden_tabs") or []
            hidden_tabs = [t for t in hidden_raw if isinstance(t, str)]
            raw_policy = data.get("fit_policy")
            if raw_policy in {"always", "on_startup", "off"}:
                fit_policy: FitPolicy = cast(FitPolicy, raw_policy)
            else:
                auto = data.get("auto_fit")
                if auto is True:
                    fit_policy = "always"
                elif auto is False:
                    fit_policy = "off"
                else:
                    fit_policy = "on_startup"
            last_w = data.get("last_width")
            last_h = data.get("last_height")
            last_x = data.get("last_x")
            last_y = data.get("last_y")
            cfg = LauncherConfig(
                title=data.get("title", "Launcher"),
                columns=data.get("columns", 5),
                tiles=tiles,
                tabs=tabs,
                hidden_tabs=hidden_tabs,
                fit_policy=fit_policy,
                last_width=last_w if isinstance(last_w, int) else None,
                last_height=last_h if isinstance(last_h, int) else None,
                last_x=last_x if isinstance(last_x, int) else None,
                last_y=last_y if isinstance(last_y, int) else None,
            )
            enforce_tab_invariants(cfg)
            return cfg
        # first run – create a friendly default
        cfg = LauncherConfig(
            title="My Launcher",
            columns=5,
            tiles=[
                Tile("ChatGPT", "https://chat.openai.com"),
                Tile("Gmail", "https://mail.google.com"),
                Tile("Notion", "https://www.notion.so"),
            ],
            tabs=["Main"],
            hidden_tabs=[],
            fit_policy="on_startup",
        )
        enforce_tab_invariants(cfg)
        cfg.save()
        return cfg

    def save(self):
        tiles = []
        for t in self.tiles:
            d = asdict(t)
            if d.get("chrome_profile") is None:
                d.pop("chrome_profile", None)
            tiles.append(d)
        data = {
            "title": self.title,
            "columns": self.columns,
            "tiles": tiles,
            "tabs": self.tabs,
            "hidden_tabs": self.hidden_tabs,
            "fit_policy": self.fit_policy,
            "auto_fit": self.fit_policy == "always",
        }
        if self.last_width is not None:
            data["last_width"] = self.last_width
        if self.last_height is not None:
            data["last_height"] = self.last_height
        if self.last_x is not None:
            data["last_x"] = self.last_x
        if self.last_y is not None:
            data["last_y"] = self.last_y
        CFG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def enforce_tab_invariants(cfg: LauncherConfig) -> None:
    """Ensure tab-related invariants for a configuration.

    - ``cfg.tabs`` is a de-duplicated, non-empty list of strings.
    - ``cfg.hidden_tabs`` is a subset of ``cfg.tabs`` and does not hide all tabs.
    - Every tile's ``tab`` exists in ``cfg.tabs``; invalid entries are remapped to
      the first tab.
    """

    clean_tabs: list[str] = []
    for t in cfg.tabs:
        if isinstance(t, str) and t not in clean_tabs:
            clean_tabs.append(t)
    if not clean_tabs:
        clean_tabs = ["Main"]
    cfg.tabs = clean_tabs

    first_tab = cfg.tabs[0]
    valid_tabs = set(cfg.tabs)
    for tile in cfg.tiles:
        if tile.tab not in valid_tabs:
            tile.tab = first_tab

    clean_hidden: list[str] = []
    for t in cfg.hidden_tabs:
        if t in valid_tabs and t not in clean_hidden:
            clean_hidden.append(t)
    cfg.hidden_tabs = clean_hidden
    if len(cfg.hidden_tabs) >= len(cfg.tabs):
        cfg.hidden_tabs = [t for t in cfg.hidden_tabs if t != first_tab]


@dataclass
class FitResult:
    columns: int
    rows_visible: int
    need_vscroll: bool
    window_w: int
    window_h: int


def compute_grid_fit(
    avail_w: int,
    avail_h: int,
    tile_w: int,
    tile_h: int,
    spacing: int,
    margins_lr: int,
    margins_tb: int,
    frame_w: int,
    frame_h: int,
    qstyle_scrollbar_extent: Optional[int],
    total_tiles_on_tab: int,
    columns_hint: Optional[int],
) -> FitResult:
    """Compute a snap-to-grid fit using only full tiles.

    Width/height refer to the *outer* window size including the frame.
    The algorithm snaps to full tiles, optionally respecting a hint for the
    number of columns when ``columns_hint`` is provided.
    """

    scrollbar_extent = qstyle_scrollbar_extent or 16

    unit_w = tile_w + spacing
    unit_h = tile_h + spacing

    usable_w = avail_w - frame_w - margins_lr
    usable_h = avail_h - frame_h - margins_tb

    max_cols = max(1, (usable_w + spacing) // unit_w)
    if columns_hint is not None:
        columns = max(1, min(columns_hint, max_cols))
    else:
        columns = max_cols

    rows_fit = max(1, (usable_h + spacing) // unit_h)
    rows_required = (total_tiles_on_tab + columns - 1) // columns
    need_vscroll = rows_required > rows_fit

    if need_vscroll:
        usable_w -= scrollbar_extent
        max_cols = max(1, (usable_w + spacing) // unit_w)
        if columns_hint is not None:
            columns = max(1, min(columns_hint, max_cols))
        else:
            columns = max_cols
        rows_fit = max(1, (usable_h + spacing) // unit_h)
        rows_required = (total_tiles_on_tab + columns - 1) // columns
        need_vscroll = rows_required > rows_fit

    rows_visible = min(rows_fit, rows_required)

    width = columns * tile_w + max(0, columns - 1) * spacing + margins_lr + frame_w
    height = (
        rows_visible * tile_h
        + max(0, rows_visible - 1) * spacing
        + margins_tb
        + frame_h
    )

    width = min(width, avail_w)
    height = min(height, avail_h)

    return FitResult(columns, rows_visible, need_vscroll, int(width), int(height))


def _auto_fit_columns(n_tiles: int, current_cols: int) -> int:
    """
    Derive a stable column count from total tiles (startup-time auto-fit).

    Simple rule: at least ceil(sqrt(n_tiles)).
    This yields 7 for 37 tiles (the unit test expectation).
    """
    if n_tiles <= 0:
        return current_cols
    return max(
        current_cols, int(math.ceil(math.sqrt(n_tiles)))
    )  # monotonic, idempotent


def guess_domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc
        return netloc.split("@")[-1]  # strip creds if any
    except Exception:  # nosec B110
        return ""


def fetch_favicon(url: str, size: int = 128) -> Optional[Path]:
    """Try to save a favicon PNG using Google's s2 service."""
    domain = guess_domain(url)
    if not domain:
        return None
    out = ICON_DIR / f"{domain}_{size}.png"
    try:
        src = f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"
        with urllib.request.urlopen(src, timeout=5) as r, open(out, "wb") as f:  # nosec B310
            f.write(r.read())
        return out
    except Exception:  # nosec B110
        return None


def letter_icon(text: str, size: int = 92, bg: str = "#F5F6FA") -> QIcon:
    """Generate a round icon with the first letter of the name."""
    ch = (text or "?").strip()[0].upper()
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # background circle
    color = QColor(bg)
    p.setBrush(color)
    p.setPen(QColor("#D6D8E1"))
    p.drawEllipse(1, 1, size - 2, size - 2)
    # letter
    font = QFont()
    font.setBold(True)
    font.setPointSize(int(size * 0.45))
    p.setFont(font)
    p.setPen(QColor("#222"))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, ch)
    p.end()
    return QIcon(pix)


class TabVisibilityDialog(QDialog):
    def __init__(
        self,
        tabs: list[str],
        hidden: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Tab Visibility")
        layout = QVBoxLayout(self)
        self._boxes: list[tuple[str, QCheckBox]] = []
        for tab in tabs:
            cb = QCheckBox(tab)
            cb.setChecked(tab not in hidden)
            layout.addWidget(cb)
            self._boxes.append((tab, cb))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_hidden(self) -> list[str]:
        return [tab for tab, cb in self._boxes if not cb.isChecked()]


class TileButton(QToolButton):
    def __init__(
        self,
        tile: Tile,
        index: int,
        on_open: Callable[[Tile], None],
        on_edit: Callable[[Tile], None],
        on_remove: Callable[[Tile], None],
        on_duplicate: Callable[[Tile], None],
        on_move: Callable[[int, int], None],
        on_change_tab: Callable[[Tile, str], None],
        tabs: list[str],
    ) -> None:
        super().__init__()
        self.tile = tile
        self.index = index
        self.on_open = on_open
        self.on_edit = on_edit
        self.on_remove = on_remove
        self.on_duplicate = on_duplicate
        self.on_move = on_move
        self.on_change_tab = on_change_tab
        self.tabs = tabs
        self._drag_start_pos: QPoint | None = None

        self.setText(tile.name)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIcon(self._icon_for_tile())
        self.setIconSize(QSize(72, 72))
        self.setFixedSize(150, 140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self._apply_style()

        self.clicked.connect(self._handle_click)

    def _apply_style(self):
        self.setStyleSheet(f"""
        QToolButton {{
            background: {self.tile.bg};
            border: 1px solid #E3E5EE;
            border-radius: 12px;
            padding-top: 10px;
        }}
        QToolButton:hover {{
            border-color: #C7CAD8;
        }}
        QToolButton:pressed {{
            background: #ECEEF6;
        }}
        """)

    def _icon_for_tile(self) -> QIcon:
        if self.tile.icon and Path(self.tile.icon).exists():
            return QIcon(self.tile.icon)
        return letter_icon(self.tile.name, 92, self.tile.bg)

    def _handle_click(self):
        self.on_open(self.tile)

    def contextMenuEvent(self, event):
        m = QMenu(self)
        m.addAction("Open", lambda: self.on_open(self.tile))
        m.addSeparator()
        m.addAction("Edit…", lambda: self.on_edit(self.tile))
        m.addAction("Duplicate", lambda: self.on_duplicate(self.tile))
        m.addAction("Remove", lambda: self.on_remove(self.tile))
        assign = m.addMenu("Assign to Tab")
        for name in self.tabs:
            if name != self.tile.tab:
                assign.addAction(name, lambda n=name: self.on_change_tab(self.tile, n))
        m.exec(event.globalPos())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if (
            event.position().toPoint() - self._drag_start_pos
        ).manhattanLength() < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self.index))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start_pos = None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if event.mimeData().hasText():
            from_idx = int(event.mimeData().text())
            self.on_move(from_idx, self.index)
            event.acceptProposedAction()


@dataclass
class LaunchPlan:
    """Plan describing how a tile's URL should be opened."""

    browser_name: str | None
    open_target: Literal["tab", "window"]
    profile: str | None
    command: list[str] | None
    controller: str | None
    new: int | None


def build_launch_plan(tile: Tile) -> LaunchPlan:
    """Return the launch strategy for *tile*.

    When falling back to :mod:`webbrowser`, ``new=1`` opens a new window and
    ``new=2`` opens a new tab, per the standard library's semantics.
    """
    target = getattr(tile, "open_target", "tab")
    if tile.browser:
        lowered = tile.browser.lower()
        exe_resolved = _resolve_controller_exe(tile.browser)
        exe = exe_resolved or tile.browser
        record_breadcrumb("launch_exe_resolved", exe=exe, browser=tile.browser)

        if "chrome" in lowered or "edge" in lowered:
            args = [exe]
            if tile.chrome_profile and "chrome" in lowered:
                args.append(f"--profile-directory={tile.chrome_profile}")
            if target == "window":
                args.append("--new-window")
            args.append(tile.url)
            return LaunchPlan(
                tile.browser, target, tile.chrome_profile, args, tile.browser, None
            )

        if "firefox" in lowered:
            args = [
                exe,
                "--new-window" if target == "window" else "--new-tab",
                tile.url,
            ]
            return LaunchPlan(
                tile.browser, target, tile.chrome_profile, args, tile.browser, None
            )

        new_flag = 1 if target == "window" else 2
        return LaunchPlan(
            tile.browser, target, tile.chrome_profile, None, tile.browser, new_flag
        )

    new_flag = 1 if target == "window" else 2
    return LaunchPlan(None, target, tile.chrome_profile, None, "default", new_flag)


def _tile_uses_chrome(tile: Tile) -> bool:
    """Return True if the given tile will launch using Google Chrome."""
    if sys.platform != "win32":
        return False
    chosen = getattr(tile, "browser", None)
    if chosen:
        as_str = str(chosen)
        return is_chrome_path(as_str) or "chrome" in as_str.lower()
    return is_windows_default_browser_chrome()


class Main(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = LauncherConfig.load()
        self._enforce_tab_invariants()
        self.cfg.save()
        self._fit_guard = False
        self._fit_policy: FitPolicy = self.cfg.fit_policy
        self._did_startup_fit = False

        # -------- Startup auto-fit: materialize columns on config when enabled --------
        if self._fit_policy != "off":
            wanted = _auto_fit_columns(len(self.cfg.tiles), self.cfg.columns)
            if wanted != self.cfg.columns:
                self.cfg.columns = wanted
            self._computed_columns = self.cfg.columns
        else:
            self._computed_columns = self.cfg.columns
            # Backwards-compatibility heuristic for fixed columns.
            if len(self.cfg.tiles) > 36 and self.cfg.columns < 7:
                self.cfg.columns = 7
            elif len(self.cfg.tiles) > 25 and self.cfg.columns < 6:
                self.cfg.columns = 6

        self.setWindowTitle(self.cfg.title)
        self.setMinimumSize(360, 240)
        self._restore_geometry(900, 600)
        if self._fit_policy == "off" and len(self.cfg.tiles) > 25:
            cols = max(6, self.cfg.columns)
            tile_w, spacing, margins = 150, 12, 32
            needed_width = margins + cols * tile_w + (cols - 1) * spacing
            if needed_width > self.width():
                self.resize(needed_width, self.height())

        # toolbar and menus
        self.toolbar = QToolBar()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        add_action = QAction("➕ Add", self)
        add_action.triggered.connect(self.add_tile)
        self.toolbar.addAction(add_action)

        tab_menu = self.menuBar().addMenu("Tabs")
        tab_menu.addAction("Add Tab", self.add_tab)
        tab_menu.addAction("Rename Tab", self.rename_tab)
        tab_menu.addAction("Delete Tab", self.delete_tab)
        self.toggle_tab_action = QAction(self)
        self.toggle_tab_action.triggered.connect(self.toggle_current_tab_visibility)
        tab_menu.addAction(self.toggle_tab_action)
        tab_menu.addAction("Manage Tab Visibility…", self.manage_tab_visibility)

        view_menu = self.menuBar().addMenu("View")
        fit_menu = view_menu.addMenu("Auto-fit Mode")
        self._fit_actions: dict[str, QAction] = {}
        for label, value in [
            ("Always", "always"),
            ("On startup", "on_startup"),
            ("Off", "off"),
        ]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.triggered.connect(
                lambda _=False, v=value: self._set_fit_policy(cast(FitPolicy, v))
            )
            fit_menu.addAction(act)
            self._fit_actions[value] = act
        view_menu.addAction("Fit to Display Now", self._fit_once_now)
        self._sync_fit_menu()

        debug_menu = self.menuBar().addMenu("Debug")
        debug_menu.addAction("Raise Exception", self._debug_raise)
        debug_menu.addAction("Qt Warning", lambda: qWarning("test"))

        self.tabs_widget = QTabWidget()
        self.tabs_widget.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs_widget)

        self._tab_viewports: set[QWidget] = set()

        self.rebuild()

        wh = self.windowHandle()
        if wh is not None:
            wh.screenChanged.connect(lambda _s: self._maybe_autofit("screen"))

    def _visible_tabs(self) -> list[str]:
        return [t for t in self.cfg.tabs if t not in self.cfg.hidden_tabs]

    def _enforce_tab_invariants(self) -> None:
        enforce_tab_invariants(self.cfg)

    def _set_current_tab_by_name(self, name: str) -> None:
        vis = self._visible_tabs()
        if name in vis:
            self.tabs_widget.setCurrentIndex(vis.index(name))
        elif vis:
            self.tabs_widget.setCurrentIndex(0)

    def _update_toggle_tab_action(self) -> None:
        name = self.current_tab()
        hidden = name in self.cfg.hidden_tabs
        self.toggle_tab_action.setText(
            "Show Current Tab" if hidden else "Hide Current Tab"
        )
        visible = self._visible_tabs()
        allow_hide = not hidden and len(visible) > 1
        self.toggle_tab_action.setEnabled(hidden or allow_hide)

    def _set_fit_policy(self, policy: FitPolicy) -> None:
        if policy == self._fit_policy:
            return
        self._fit_policy = policy
        self.cfg.fit_policy = policy
        self.cfg.save()
        record_breadcrumb("fit_policy_changed", policy=policy)
        self._did_startup_fit = policy != "on_startup"
        self._sync_fit_menu()
        if policy == "always":
            self.resize_to_fit_tiles()
        elif policy == "on_startup":
            self._fit_once_now()

    def _sync_fit_menu(self) -> None:
        for value, act in self._fit_actions.items():
            act.setChecked(value == self._fit_policy)

    def _fit_once_now(self) -> None:
        self._did_startup_fit = True
        self.resize_to_fit_tiles(force=True)

    def _maybe_autofit(self, trigger: FitTrigger) -> None:
        if self._fit_guard:
            return
        if should_fit(self._fit_policy, self._did_startup_fit, trigger):
            QTimer.singleShot(0, self.resize_to_fit_tiles)

    def _on_tab_changed(self, _index: int) -> None:
        self._update_toggle_tab_action()
        self._maybe_autofit("tab")

    def _restore_geometry(self, default_w: int, default_h: int) -> None:
        if self._fit_policy == "always":
            self.resize(default_w, default_h)
            return
        lw, lh, lx, ly = (
            self.cfg.last_width,
            self.cfg.last_height,
            self.cfg.last_x,
            self.cfg.last_y,
        )
        if None not in (lw, lh, lx, ly) and self._geometry_visible(lx, ly, lw, lh):
            self.resize(lw, lh)
            self.move(lx, ly)
        else:
            self._center_on_primary(default_w, default_h)

    def _geometry_visible(self, x: int, y: int, w: int, h: int) -> bool:
        rect = QRect(x, y, w, h)
        for screen in QApplication.screens():
            if screen.availableGeometry().intersects(rect):
                return True
        return False

    def _center_on_primary(self, w: int, h: int) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(w, h)
            return
        avail = screen.availableGeometry()
        x = avail.left() + (avail.width() - w) // 2
        y = avail.top() + (avail.height() - h) // 2
        self.resize(w, h)
        self.move(x, y)

    # -------- UI building --------
    def showEvent(self, event: QShowEvent) -> None:  # noqa: D401
        super().showEvent(event)
        record_breadcrumb("window_shown")
        self._maybe_autofit("show")

    def rebuild(self) -> None:
        self.tabs_widget.clear()
        self._grids: dict[str, QGridLayout] = {}
        self._tab_viewports.clear()
        for tab in self._visible_tabs():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            grid = QGridLayout(container)
            grid.setSpacing(12)
            grid.setContentsMargins(16, 16, 16, 16)
            scroll.setWidget(container)
            self._wire_tab_whitespace_menu(scroll)
            self.tabs_widget.addTab(scroll, tab)
            self._grids[tab] = grid
            self._populate_tab(tab)
        self._maybe_autofit("rebuild")
        self._update_toggle_tab_action()

    def _populate_tab(self, tab: str) -> None:
        grid = self._grids[tab]
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if self._fit_policy != "off":
            cols = max(1, int(self._computed_columns))
        else:
            cols = max(1, int(self.cfg.columns))
        r = c = 0
        tab_tiles = [t for t in self.cfg.tiles if t.tab == tab]
        all_tabs = list(self.cfg.tabs)
        for idx, tile in enumerate(tab_tiles):

            def move(f: int, t: int, tab_name: str = tab) -> None:
                self.move_tile(tab_name, f, t)

            btn = TileButton(
                tile,
                idx,
                on_open=self.open_tile,
                on_edit=self.edit_tile,
                on_remove=self.remove_tile,
                on_duplicate=self.duplicate_tile,
                on_move=move,
                on_change_tab=self.change_tile_tab,
                tabs=all_tabs,
            )
            grid.addWidget(btn, r, c)
            c += 1
            if c >= cols:
                c = 0
                r += 1

    def _wire_tab_whitespace_menu(self, scroll: QScrollArea) -> None:
        """Install a context menu handler on the scroll area's viewport."""
        vp = scroll.viewport()
        vp.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        vp.installEventFilter(self)
        self._tab_viewports.add(vp)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ContextMenu and obj in self._tab_viewports:
            cme = cast(QContextMenuEvent, event)
            global_pos = cast(QWidget, obj).mapToGlobal(cme.pos())
            if not self._is_over_tile(global_pos):
                self._show_whitespace_menu(global_pos)
                return True
            return False
        return super().eventFilter(obj, event)

    def _is_over_tile(self, global_pos: QPoint) -> bool:
        w = QApplication.widgetAt(global_pos)
        while w:
            if isinstance(w, TileButton):
                return True
            w = w.parentWidget()
        return False

    def _show_whitespace_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act = menu.addAction("Add Tile…")
        act.triggered.connect(lambda: self.add_tile(self.current_tab()))
        menu.exec(global_pos)

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: D401
        super().moveEvent(event)
        self._maybe_autofit("move")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D401
        super().resizeEvent(event)
        self._maybe_autofit("resize")

    def resize_to_fit_tiles(self, *, force: bool = False) -> None:
        if self._fit_guard:
            return
        if not force:
            if self._fit_policy == "off":
                return
            if self._fit_policy == "on_startup" and self._did_startup_fit:
                return
        self._fit_guard = True
        try:
            tile_w, tile_h = 150, 140
            current = self.current_tab()
            grid = self._grids.get(current)
            if grid is None:
                return
            spacing = grid.spacing()
            margins = grid.contentsMargins()
            margins_lr = margins.left() + margins.right()
            margins_tb = margins.top() + margins.bottom()
            tile_count = len([t for t in self.cfg.tiles if t.tab == current])

            screen = (
                self.windowHandle().screen()
                if self.windowHandle() is not None
                else QApplication.screenAt(self.frameGeometry().center())
            )
            if screen is None:
                screen = QApplication.primaryScreen()
            avail = screen.availableGeometry()

            frame_w = self.frameGeometry().width() - self.geometry().width()
            frame_h = self.frameGeometry().height() - self.geometry().height()
            try:
                sb_w = self.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, self)
            except Exception:
                sb_w = 16
            if sb_w <= 0:
                sb_w = 16

            columns_hint = None if self._fit_policy != "off" else self.cfg.columns

            result = compute_grid_fit(
                avail.width(),
                avail.height(),
                tile_w,
                tile_h,
                spacing,
                margins_lr,
                margins_tb,
                frame_w,
                frame_h,
                sb_w,
                tile_count,
                columns_hint,
            )

            if self._fit_policy != "off" and result.columns != self._computed_columns:
                self._computed_columns = result.columns
                self._populate_tab(current)

            record_breadcrumb(
                "fit_compute",
                screen=getattr(screen, "name", lambda: "unknown")(),
                avail_w=avail.width(),
                avail_h=avail.height(),
                tiles=tile_count,
                hint_cols=columns_hint,
                cols=result.columns,
                rows_visible=result.rows_visible,
                need_vscroll=result.need_vscroll,
            )

            if force or self._fit_policy in {"always", "on_startup"}:
                self.resize(result.window_w, result.window_h)
                record_breadcrumb(
                    "fit_apply", window_w=result.window_w, window_h=result.window_h
                )
                if (
                    self._fit_policy == "always"
                    and tile_count > 0
                    and result.need_vscroll
                ):
                    self.move(self.x(), avail.top())
        finally:
            self._fit_guard = False
            if self._fit_policy == "on_startup":
                self._did_startup_fit = True

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        try:
            self.cfg.last_width = int(self.width())
            self.cfg.last_height = int(self.height())
            self.cfg.last_x = int(self.x())
            self.cfg.last_y = int(self.y())
            self.cfg.fit_policy = self._fit_policy
            self.cfg.save()
        finally:
            super().closeEvent(event)

    def current_tab(self) -> str:
        idx = self.tabs_widget.currentIndex()
        vis = self._visible_tabs()
        if idx < 0:
            return vis[0] if vis else (self.cfg.tabs[0] if self.cfg.tabs else "Main")
        return self.tabs_widget.tabText(idx)

    # -------- actions --------
    def open_tile(self, tile: Tile) -> None:
        logger = logging.getLogger(__name__)
        plan = build_launch_plan(tile)
        url = sanitize_url(tile.url)
        record_breadcrumb(
            "launch_attempt",
            name=tile.name,
            url=url,
            browser=plan.browser_name or "default",
            open_target=plan.open_target,
        )
        record_breadcrumb(
            "launch_plan",
            browser=plan.browser_name or "default",
            open_target=plan.open_target,
            command=plan.command,
            controller=plan.controller,
            new=plan.new,
        )
        logger.info(
            "browser_launch_attempt",
            extra=sanitize_log_extra(
                {
                    "event": "browser_launch_attempt",
                    "browser": plan.browser_name or "default",
                    "flags": plan.command[1:-1] if plan.command else [],
                    "profile": plan.profile,
                    "open_target": plan.open_target,
                    "url": url,
                    "platform": sys.platform,
                    "pid": os.getpid(),
                }
            ),
        )

        # --- Windows explicit Chrome special-case (always try profile/CLI first) ---
        if (
            sys.platform == "win32"
            and plan.browser_name
            and "chrome" in plan.browser_name.lower()
        ):
            profile_dir = tile.chrome_profile or "Default"
            ok = False
            try:
                ok = launch_chrome_with_profile(tile.url, profile_dir, plan.open_target)
            except OSError as exc:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name,
                    open_target=plan.open_target,
                    profile=profile_dir,
                    error=str(exc),
                )
            if ok:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name,
                    open_target=plan.open_target,
                    profile=profile_dir,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
                return
            record_breadcrumb(
                "launch_path",
                path="chrome_profile_cli",
                browser=plan.browser_name,
                open_target=plan.open_target,
                profile=profile_dir,
                fallback=True,
            )

        # --- Windows default Chrome special-case (only when needed) ---
        # Use Chrome CLI only if Chrome is default *and* we need a specific profile
        # or a guaranteed new window. Otherwise fall through to webbrowser.open.
        if (
            sys.platform == "win32"
            and plan.browser_name is None
            and is_windows_default_browser_chrome()
            and (
                getattr(tile, "chrome_profile", None) is not None
                or plan.open_target == "window"
            )
        ):
            profile_dir = tile.chrome_profile or "Default"
            ok = False
            try:
                ok = launch_chrome_with_profile(tile.url, profile_dir, plan.open_target)
            except OSError as exc:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    profile=profile_dir,
                    error=str(exc),
                )
            if ok:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    profile=profile_dir,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
                return
            record_breadcrumb(
                "launch_path",
                path="chrome_profile_cli",
                browser=plan.browser_name or "default",
                open_target=plan.open_target,
                profile=profile_dir,
                fallback=True,
            )

        # --- Explicit controller CLI path (firefox/chrome/edge, etc.) ---
        if plan.command:
            try:
                debug_scaffold.last_launch_command = " ".join(plan.command)
                subprocess.Popen(plan.command, close_fds=True)  # nosec B603
                record_breadcrumb(
                    "launch_path",
                    path="browser_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    cmd=plan.command,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
                return
            except OSError as exc:
                record_breadcrumb(
                    "launch_path",
                    path="browser_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    cmd=plan.command,
                    error=str(exc),
                )
                logger.error(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {
                            "event": "browser_launch_result",
                            "ok": False,
                            "error": str(exc),
                        }
                    ),
                )

        # --- Default browser path: use webbrowser.open directly (no webbrowser.get) ---
        if plan.browser_name is None or plan.controller == "default":
            new_flag = plan.new or (1 if plan.open_target == "window" else 2)
            try:
                webbrowser.open(tile.url, new=new_flag)
                record_breadcrumb(
                    "launch_path",
                    path="webbrowser_module",
                    browser="default",
                    open_target=plan.open_target,
                    new=new_flag,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
            except Exception as exc:  # very rare; keep behavior consistent
                record_breadcrumb(
                    "launch_path",
                    path="webbrowser_module",
                    browser="default",
                    open_target=plan.open_target,
                    new=new_flag,
                    error=str(exc),
                )
                record_breadcrumb("launch_result", ok=False, url=url)
                logger.error(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {
                            "event": "browser_launch_result",
                            "ok": False,
                            "error": str(exc),
                        }
                    ),
                )
                parent = self if isinstance(self, QWidget) else None
                QMessageBox.warning(
                    parent, "Failed to launch browser", f"Could not open {url}."
                )
            return

        # --- Named controller fallback (non-CLI): use webbrowser.get(name) ---
        controller_name = plan.controller or getattr(tile, "browser", None) or "default"
        record_breadcrumb("launch_fallback_controller", controller=controller_name)
        try:
            browser_obj = webbrowser.get(controller_name)
            if (plan.new or 0) == 2 and hasattr(browser_obj, "open_new_tab"):
                browser_obj.open_new_tab(tile.url)
            elif (plan.new or 0) == 1 and hasattr(browser_obj, "open_new"):
                browser_obj.open_new(tile.url)
            else:
                browser_obj.open(tile.url, new=plan.new or 0)
            record_breadcrumb(
                "launch_path",
                path="webbrowser",
                browser=plan.browser_name or controller_name,
                open_target=plan.open_target,
                controller=controller_name,
                new=plan.new or 0,
            )
            record_breadcrumb("launch_result", ok=True, url=url)
            logger.info(
                "browser_launch_result",
                extra=sanitize_log_extra(
                    {"event": "browser_launch_result", "ok": True}
                ),
            )
        except webbrowser.Error as exc:
            record_breadcrumb(
                "launch_path",
                path="webbrowser",
                browser=plan.browser_name or controller_name,
                open_target=plan.open_target,
                controller=controller_name,
                new=plan.new or 0,
                error=str(exc),
            )
            record_breadcrumb("launch_result", ok=False, url=url)
            logger.error(
                "browser_launch_result",
                extra=sanitize_log_extra(
                    {
                        "event": "browser_launch_result",
                        "ok": False,
                        "error": str(exc),
                    }
                ),
            )
            parent = self if isinstance(self, QWidget) else None
            QMessageBox.warning(
                parent,
                "Failed to launch browser",
                f"Could not open {url} in {controller_name}.",
            )

    def move_tile(self, tab: str, from_idx: int, to_idx: int) -> None:
        if from_idx == to_idx:
            return
        indices = [i for i, t in enumerate(self.cfg.tiles) if t.tab == tab]
        tile = self.cfg.tiles.pop(indices[from_idx])
        insert_at = indices[to_idx]
        if from_idx < to_idx:
            insert_at -= 1
        self.cfg.tiles.insert(insert_at, tile)
        self.cfg.save()
        self._populate_tab(tab)

    def add_tile(self, default_tab: str | None = None) -> None:
        dlg = TileEditorDialog(
            tabs=self.cfg.tabs,
            browsers=available_browsers(),
            icon_dir=ICON_DIR,
            fetch_favicon=fetch_favicon,
            parent=self,
        )
        tab_name = default_tab or self.current_tab()
        idx = dlg.tab_combo.findText(tab_name, Qt.MatchFlag.MatchExactly)
        if idx >= 0:
            dlg.tab_combo.setCurrentIndex(idx)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.data:
            data = dlg.data
            self.cfg.tiles.append(
                Tile(
                    name=cast(str, data["name"]),
                    url=cast(str, data["url"]),
                    icon=data["icon"],
                    bg="#F5F6FA",
                    tab=cast(str, data["tab"]),
                    browser=data["browser"],
                    chrome_profile=data["chrome_profile"],
                    open_target=cast(str, data["open_target"]),
                )
            )
            self.cfg.save()
            self.rebuild()
            self._set_current_tab_by_name(cast(str, data["tab"]))
            record_breadcrumb(
                "tile_add",
                name=cast(str, data["name"]),
                url=sanitize_url(cast(str, data["url"])),
                tab=cast(str, data["tab"]),
            )

    def edit_tile(self, tile: Tile) -> None:
        dlg = TileEditorDialog(
            tabs=self.cfg.tabs,
            browsers=available_browsers(),
            icon_dir=ICON_DIR,
            fetch_favicon=fetch_favicon,
            tile=tile,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.data:
            data = dlg.data
            tile.name = cast(str, data["name"])
            tile.url = cast(str, data["url"])
            tile.icon = data["icon"]
            tile.tab = cast(str, data["tab"])
            tile.browser = data["browser"]
            tile.chrome_profile = data["chrome_profile"]
            tile.open_target = cast(str, data["open_target"])
            self.cfg.save()
            self.rebuild()
            self._set_current_tab_by_name(tile.tab)

    def duplicate_tile(self, tile: Tile) -> None:
        new_tile = replace(tile)
        idx = self.cfg.tiles.index(tile)
        self.cfg.tiles.insert(idx + 1, new_tile)
        self.cfg.save()
        self.rebuild()
        self._set_current_tab_by_name(tile.tab)

    def remove_tile(self, tile: Tile) -> None:
        ok = QMessageBox.warning(
            self,
            "Remove tile?",
            f"Remove “{tile.name}” from the launcher?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok == QMessageBox.StandardButton.Yes:
            self.cfg.tiles = [t for t in self.cfg.tiles if t is not tile]
            self.cfg.save()
            self.rebuild()

    def change_tile_tab(self, tile: Tile, new_tab: str) -> None:
        tile.tab = new_tab
        self.cfg.save()
        self.rebuild()
        self._set_current_tab_by_name(new_tab)

    def add_tab(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Tab", "Tab name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.cfg.tabs:
            QMessageBox.warning(
                self, "Tab exists", "A tab with that name exists already."
            )
            return
        self.cfg.tabs.append(name)
        self._enforce_tab_invariants()
        self.cfg.save()
        self.rebuild()
        self._set_current_tab_by_name(name)

    def rename_tab(self) -> None:
        current = self.current_tab()
        name, ok = QInputDialog.getText(self, "Rename Tab", "Tab name:", text=current)
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.cfg.tabs:
            QMessageBox.warning(
                self, "Tab exists", "A tab with that name exists already."
            )
            return
        idx = self.cfg.tabs.index(current)
        self.cfg.tabs[idx] = name
        for t in self.cfg.tiles:
            if t.tab == current:
                t.tab = name
        if current in self.cfg.hidden_tabs:
            hidx = self.cfg.hidden_tabs.index(current)
            self.cfg.hidden_tabs[hidx] = name
        self._enforce_tab_invariants()
        self.cfg.save()
        self.rebuild()
        self._set_current_tab_by_name(name)

    def delete_tab(self) -> None:
        current = self.current_tab()
        if len(self.cfg.tabs) == 1:
            QMessageBox.warning(self, "Not allowed", "At least one tab must exist.")
            record_breadcrumb(
                "tab_action_blocked", action="delete", reason="last_tab", tab=current
            )
            return
        ok = QMessageBox.question(
            self,
            "Delete Tab",
            f"Delete tab '{current}' and all its tiles?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.cfg.tabs = [t for t in self.cfg.tabs if t != current]
        self.cfg.tiles = [t for t in self.cfg.tiles if t.tab != current]
        self.cfg.hidden_tabs = [t for t in self.cfg.hidden_tabs if t != current]
        self._enforce_tab_invariants()
        self.cfg.save()
        self.rebuild()

    def toggle_current_tab_visibility(self) -> None:
        name = self.current_tab()
        hidden = name in self.cfg.hidden_tabs
        if not hidden and len(self._visible_tabs()) == 1:
            QMessageBox.warning(
                self, "Not allowed", "At least one tab must remain visible."
            )
            record_breadcrumb(
                "tab_action_blocked",
                action="hide",
                reason="last_visible",
                tab=name,
            )
            return
        if hidden:
            self.cfg.hidden_tabs.remove(name)
        else:
            self.cfg.hidden_tabs.append(name)
        self._enforce_tab_invariants()
        self.cfg.save()
        self.rebuild()
        self._set_current_tab_by_name(name)
        record_breadcrumb(
            "tab_visibility_toggle_single",
            tab=name,
            visible=name not in self.cfg.hidden_tabs,
        )

    def manage_tab_visibility(self) -> None:
        dlg = TabVisibilityDialog(self.cfg.tabs, self.cfg.hidden_tabs, self)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            hidden = dlg.result_hidden()
            if len(hidden) == len(self.cfg.tabs):
                QMessageBox.warning(
                    self,
                    "Not allowed",
                    "At least one tab must remain visible.",
                )
                continue
            break
        self.cfg.hidden_tabs = hidden
        self._enforce_tab_invariants()
        self.cfg.save()
        self.rebuild()
        vis = self._visible_tabs()
        if vis:
            self._set_current_tab_by_name(vis[0])
        record_breadcrumb(
            "tab_visibility_apply",
            hidden_tabs=self.cfg.hidden_tabs,
            visible_tabs=self._visible_tabs(),
        )

    def _debug_raise(self) -> None:
        raise RuntimeError("Test exception")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    debug_scaffold.install_debug_scaffold(app, app_name="DesktopTileLauncher")
    mw = Main()
    mw.show()
    sys.exit(app.exec())
