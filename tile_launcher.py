# tile_launcher.py
# Minimal desktop launcher: tile grid that opens URLs in the default browser.
# Windows/Mac/Linux.  Requires: Python 3.10+  pip install PySide6
# encoding changed
# SPDX-License-Identifier: MIT


import json
import os
import sys
import webbrowser
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional
import shutil

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QToolBar,
    QToolButton,
    QFileDialog,
    QWidget,
)

APP_NAME = "TileLauncher"


def app_dirs():
    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
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
    """Return a list of locally available browser names."""
    try_order: Iterable[str] = getattr(webbrowser, "_tryorder", [])
    browsers: list[str] = []
    for name in try_order:
        try:
            webbrowser.get(name)
        except webbrowser.Error:
            continue
        browsers.append(name)

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
        if name in browsers:
            continue
        try:
            webbrowser.get(name)
        except webbrowser.Error:
            exe = _find_browser(paths)
            if not exe:
                continue
            webbrowser.register(name, None, webbrowser.BackgroundBrowser(exe))
        browsers.append(name)

    return sorted(set(browsers))


@dataclass
class Tile:
    name: str
    url: str
    tab: str = "Main"
    icon: Optional[str] = None  # path to png/ico
    bg: str = "#F5F6FA"  # background color (CSS)
    browser: Optional[str] = None  # webbrowser name


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
        # first run ï¿½ create a friendly default
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
        data = {
            "title": self.title,
            "columns": self.columns,
            "tiles": [asdict(t) for t in self.tiles],
            "tabs": self.tabs,
        }
        CFG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def guess_domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc
        return netloc.split("@")[-1]  # strip creds if any
    except Exception:
        return ""


def fetch_favicon(url: str, size: int = 128) -> Optional[Path]:
    """Try to save a favicon PNG using Google's s2 service."""
    domain = guess_domain(url)
    if not domain:
        return None
    out = ICON_DIR / f"{domain}_{size}.png"
    try:
        src = f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"
        with urllib.request.urlopen(src, timeout=5) as r, open(out, "wb") as f:
            f.write(r.read())
        return out
    except Exception:
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

        self.tabs_widget = QTabWidget()
        self.tabs_widget.currentChanged.connect(
            lambda _=0: QTimer.singleShot(0, self.resize_to_fit_tiles)
        )
        self.setCentralWidget(self.tabs_widget)

        self.rebuild()

    # -------- UI building --------
    def rebuild(self) -> None:
        self.tabs_widget.clear()
        self._grids: dict[str, QGridLayout] = {}
        for tab in self.cfg.tabs:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            grid = QGridLayout(container)
            grid.setSpacing(12)
            grid.setContentsMargins(16, 16, 16, 16)
            scroll.setWidget(container)
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
        try:
            if tile.browser:
                webbrowser.get(tile.browser).open(tile.url)
            else:
                webbrowser.open(tile.url)
        except webbrowser.Error:
            webbrowser.open(tile.url)

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

    def add_tile(self) -> None:
        name, ok = QInputDialog.getText(self, "Tile name", "Name:")
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "Tile URL", "URL (https://…):")
        if not ok or not url.strip():
            return

        # try to fetch a favicon automatically
        icon_path = fetch_favicon(url)
        icon = str(icon_path) if icon_path else None

        bg = "#F5F6FA"
        current_tab = self.current_tab()
        tab, ok = QInputDialog.getItem(
            self,
            "Assign Tab",
            "Tab:",
            self.cfg.tabs,
            self.cfg.tabs.index(current_tab) if current_tab in self.cfg.tabs else 0,
            False,
        )
        if not ok or not tab.strip():
            tab = current_tab
        else:
            tab = tab.strip()

        browsers = ["Default"] + available_browsers()
        browser_choice, ok = QInputDialog.getItem(
            self,
            "Browser",
            "Browser:",
            browsers,
            0,
            False,
        )
        browser_sel = None if not ok or browser_choice == "Default" else browser_choice
        self.cfg.tiles.append(
            Tile(
                name=name.strip(),
                url=url.strip(),
                icon=icon,
                bg=bg,
                tab=tab,
                browser=browser_sel,
            )
        )
        self.cfg.save()
        self.rebuild()
        self.tabs_widget.setCurrentIndex(self.cfg.tabs.index(tab))

    def edit_tile(self, tile: Tile) -> None:
        name, ok = QInputDialog.getText(self, "Edit tile", "Name:", text=tile.name)
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "Edit tile", "URL:", text=tile.url)
        if not ok or not url.strip():
            return

        browsers = ["Default"] + available_browsers()
        current_browser = tile.browser if tile.browser else "Default"
        browser_choice, ok = QInputDialog.getItem(
            self,
            "Browser",
            "Browser:",
            browsers,
            browsers.index(current_browser) if current_browser in browsers else 0,
            False,
        )
        if not ok:
            browser_choice = current_browser
        browser_sel = None if browser_choice == "Default" else browser_choice

        # optional: change icon file
        change_icon = QMessageBox.question(
            self,
            "Icon",
            "Change icon file?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        icon = tile.icon
        if change_icon == QMessageBox.StandardButton.Yes:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose icon (png/ico)", str(ICON_DIR), "Images (*.png *.ico)"
            )
            if path:
                icon = path

        tile.name, tile.url, tile.icon, tile.browser = (
            name.strip(),
            url.strip(),
            icon,
            browser_sel,
        )
        self.cfg.save()
        self.rebuild()

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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    mw = Main()
    mw.show()
    sys.exit(app.exec())
