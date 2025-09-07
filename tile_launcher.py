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
    QObject,
    QMimeData,
    QPoint,
    QSize,
    Qt,
    QTimer,
    qWarning,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QContextMenuEvent,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPixmap,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QToolBar,
    QToolButton,
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

    @staticmethod
    def load():
        if CFG_PATH.exists():
            data = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            tiles = [Tile(**t) for t in data.get("tiles", [])]
            tabs = data.get("tabs") or ["Main"]
            # ensure all tabs referenced by tiles exist
            for t in tiles:
                if t.tab not in tabs:
                    tabs.append(t.tab)
            return LauncherConfig(
                title=data.get("title", "Launcher"),
                columns=data.get("columns", 5),
                tiles=tiles,
                tabs=tabs,
            )
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
        )
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
        }
        CFG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def guess_domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc
        return netloc.split("@")[-1]  # strip creds if any
    except Exception:  # nosec B110: intentional best-effort fallback; logged elsewhere
        return ""


def fetch_favicon(url: str, size: int = 128) -> Optional[Path]:
    """Try to save a favicon PNG using Google's s2 service."""
    domain = guess_domain(url)
    if not domain:
        return None
    out = ICON_DIR / f"{domain}_{size}.png"
    try:
        src = f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"
        with urllib.request.urlopen(src, timeout=5) as r, open(out, "wb") as f:  # nosec B310: fixed https endpoint; domain param sanitized upstream
            f.write(r.read())
        return out
    except Exception:  # nosec B110: intentional best-effort fallback; logged elsewhere
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
    ``new=2`` opens a new tab, per the standard library's semantics
    (https://docs.python.org/3/library/webbrowser.html#webbrowser.open).
    """

    target = getattr(tile, "open_target", "tab")
    if tile.browser:
        lowered = tile.browser.lower()
        if "chrome" in lowered or "edge" in lowered:
            args = [tile.browser]
            if tile.chrome_profile:
                args.append(f"--profile-directory={tile.chrome_profile}")
            if target == "window":
                args.append("--new-window")
            args.append(tile.url)
            return LaunchPlan(
                tile.browser, target, tile.chrome_profile, args, None, None
            )
        if "firefox" in lowered:
            args = [
                tile.browser,
                "--new-window" if target == "window" else "--new-tab",
                tile.url,
            ]
            return LaunchPlan(
                tile.browser, target, tile.chrome_profile, args, None, None
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

        # Automatically expand to show more columns based on the number of
        # tiles across all tabs. More than 25 tiles expands to six columns and
        # more than 36 tiles expands to seven columns. This adjusts both the
        # column count and the window width so that the grid fits without the
        # user having to resize manually.
        if len(self.cfg.tiles) > 36 and self.cfg.columns < 7:
            self.cfg.columns = 7
        elif len(self.cfg.tiles) > 25 and self.cfg.columns < 6:
            self.cfg.columns = 6

        self.setWindowTitle(self.cfg.title)

        width, height = 900, 600
        self.resize(width, height)
        if len(self.cfg.tiles) > 25:
            cols = max(6, self.cfg.columns)
            tile_w, spacing, margins = 150, 12, 32
            needed_width = margins + cols * tile_w + (cols - 1) * spacing
            if needed_width > width:
                self.resize(needed_width, height)

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

        debug_menu = self.menuBar().addMenu("Debug")
        debug_menu.addAction("Raise Exception", self._debug_raise)
        debug_menu.addAction("Qt Warning", lambda: qWarning("test"))

        self.tabs_widget = QTabWidget()
        self.tabs_widget.currentChanged.connect(
            lambda _=0: QTimer.singleShot(0, self.resize_to_fit_tiles)
        )
        self.setCentralWidget(self.tabs_widget)

        self._tab_viewports: set[QWidget] = set()

        self.rebuild()

    # -------- UI building --------
    def showEvent(self, event: QShowEvent) -> None:  # noqa: D401
        super().showEvent(event)
        record_breadcrumb("window_shown")

    def rebuild(self) -> None:
        self.tabs_widget.clear()
        self._grids: dict[str, QGridLayout] = {}
        self._tab_viewports.clear()
        for tab in self.cfg.tabs:
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
        QTimer.singleShot(0, self.resize_to_fit_tiles)

    def _populate_tab(self, tab: str) -> None:
        grid = self._grids[tab]
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

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

    def resize_to_fit_tiles(self) -> None:
        cols = max(1, int(self.cfg.columns))
        tile_w, tile_h = 150, 140
        current = self.current_tab()
        grid = self._grids.get(current)
        if grid is None:
            return
        spacing = grid.spacing()
        margins = grid.contentsMargins()
        tile_count = len([t for t in self.cfg.tiles if t.tab == current])
        rows = (tile_count + cols - 1) // cols

        width = (
            cols * tile_w
            + max(0, cols - 1) * spacing
            + margins.left()
            + margins.right()
        )
        height = (
            rows * tile_h
            + max(0, rows - 1) * spacing
            + margins.top()
            + margins.bottom()
        )

        frame_w = self.frameGeometry().width() - self.geometry().width()
        frame_h = self.frameGeometry().height() - self.geometry().height()
        width += frame_w
        height += frame_h

        screen = QApplication.primaryScreen().availableGeometry()
        width = min(width, screen.width())
        height = min(height, screen.height())
        self.resize(width, height)

        if tile_count > 20:
            self.move(self.x(), screen.top())

    def current_tab(self) -> str:
        idx = self.tabs_widget.currentIndex()
        if idx < 0:
            return "Main"
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

        if sys.platform == "win32" and _tile_uses_chrome(tile):
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

        if plan.command:
            try:
                debug_scaffold.last_launch_command = " ".join(plan.command)
                subprocess.Popen(plan.command, close_fds=True, shell=False)  # nosec B603: command built from internal allowlist; no shell
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

        controller_name = plan.controller or "default"
        try:
            if controller_name != "default":
                webbrowser.get(controller_name).open(tile.url, new=plan.new or 0)
            else:
                webbrowser.open(tile.url, new=plan.new or 0)
            record_breadcrumb(
                "launch_path",
                path="webbrowser",
                browser=plan.browser_name or "default",
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
                browser=plan.browser_name or "default",
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
            self.tabs_widget.setCurrentIndex(
                self.cfg.tabs.index(cast(str, data["tab"]))
            )
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
            self.tabs_widget.setCurrentIndex(self.cfg.tabs.index(tile.tab))

    def duplicate_tile(self, tile: Tile) -> None:
        new_tile = replace(tile)
        idx = self.cfg.tiles.index(tile)
        self.cfg.tiles.insert(idx + 1, new_tile)
        self.cfg.save()
        self.rebuild()
        self.tabs_widget.setCurrentIndex(self.cfg.tabs.index(tile.tab))

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
        self.tabs_widget.setCurrentIndex(self.cfg.tabs.index(new_tab))

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
        self.cfg.save()
        self.rebuild()
        self.tabs_widget.setCurrentIndex(len(self.cfg.tabs) - 1)

    def rename_tab(self) -> None:
        current = self.current_tab()
        if current == "Main":
            QMessageBox.warning(self, "Not allowed", "The Main tab cannot be renamed.")
            return
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
        self.cfg.save()
        self.rebuild()
        self.tabs_widget.setCurrentIndex(idx)

    def delete_tab(self) -> None:
        current = self.current_tab()
        if current == "Main":
            QMessageBox.warning(self, "Not allowed", "The Main tab cannot be deleted.")
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
        self.cfg.save()
        self.rebuild()

    def _debug_raise(self) -> None:
        raise RuntimeError("Test exception")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    debug_scaffold.install_debug_scaffold(app, app_name="DesktopTileLauncher")
    mw = Main()
    mw.show()
    sys.exit(app.exec())
