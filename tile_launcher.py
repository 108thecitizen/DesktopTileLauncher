# tile_launcher.py
# Minimal desktop launcher: lockable tile grid that opens URLs in the default browser.
# Windows/Mac/Linux.  Requires: Python 3.10+  pip install PySide6
# encoding changed
# SPDX-License-Identifier: MIT


import json
import os
import sys
import webbrowser
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
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


@dataclass
class Tile:
    name: str
    url: str
    icon: Optional[str] = None  # path to png/ico
    bg: str = "#F5F6FA"  # background color (CSS)


@dataclass
class LauncherConfig:
    title: str = "Launcher"
    columns: int = 5
    tiles: list["Tile"] = field(default_factory=list)

    @staticmethod
    def load():
        if CFG_PATH.exists():
            data = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            tiles = [Tile(**t) for t in data.get("tiles", [])]
            return LauncherConfig(
                title=data.get("title", "Launcher"),
                columns=data.get("columns", 5),
                tiles=tiles,
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
        )
        cfg.save()
        return cfg

    def save(self):
        data = {
            "title": self.title,
            "columns": self.columns,
            "tiles": [asdict(t) for t in self.tiles],
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
    def __init__(self, tile: Tile, locked: bool, on_open, on_edit, on_remove):
        super().__init__()
        self.tile = tile
        self.locked = locked
        self.on_open = on_open
        self.on_edit = on_edit
        self.on_remove = on_remove

        self.setText(tile.name)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIcon(self._icon_for_tile())
        self.setIconSize(QSize(72, 72))
        self.setFixedSize(150, 140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        if self.locked:
            self.on_open(self.tile)
        else:
            self.on_edit(self.tile)

    def contextMenuEvent(self, event):
        m = QMenu(self)
        m.addAction("Open", lambda: self.on_open(self.tile))
        if not self.locked:
            m.addSeparator()
            m.addAction("Editï¿½", lambda: self.on_edit(self.tile))
            m.addAction("Remove", lambda: self.on_remove(self.tile))
        m.exec(event.globalPos())

    def refresh(self, locked: bool):
        self.locked = locked
        self.setIcon(self._icon_for_tile())
        self._apply_style()


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = LauncherConfig.load()
        self.locked = True

        self.setWindowTitle(self.cfg.title)

        self.toolbar = QToolBar()
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)

        self.lock_action = QAction("?? Locked", self)
        self.lock_action.triggered.connect(self.toggle_lock)
        self.toolbar.addAction(self.lock_action)

        add_action = QAction("? Add", self)
        add_action.triggered.connect(self.add_tile)
        self.toolbar.addAction(add_action)

        save_action = QAction("?? Save", self)
        save_action.triggered.connect(self.save)
        self.toolbar.addAction(save_action)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(12)
        self.grid.setContentsMargins(16, 16, 16, 16)
        self.scroll.setWidget(self.container)
        self.setCentralWidget(self.scroll)

        self.rebuild()

    # -------- UI building --------
    def rebuild(self):
        # clear
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        cols = max(1, int(self.cfg.columns))
        r = c = 0
        for tile in self.cfg.tiles:
            btn = TileButton(
                tile,
                self.locked,
                on_open=self.open_tile,
                on_edit=self.edit_tile,
                on_remove=self.remove_tile,
            )
            self.grid.addWidget(btn, r, c)
            c += 1
            if c >= cols:
                c = 0
                r += 1

        self.container.adjustSize()
        self.update_lock_ui()
        QTimer.singleShot(0, self.resize_to_fit_tiles)

    def resize_to_fit_tiles(self):
        cols = max(1, int(self.cfg.columns))
        tile_w, tile_h = 150, 140
        spacing = self.grid.spacing()
        margins = self.grid.contentsMargins()
        rows = (len(self.cfg.tiles) + cols - 1) // cols

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

        if len(self.cfg.tiles) > 20:
            self.move(self.x(), screen.top())

    def update_lock_ui(self):
        self.lock_action.setText("?? Locked" if self.locked else "?? Editing")

    # -------- actions --------
    def toggle_lock(self):
        if self.locked:
            ok = QMessageBox.question(
                self,
                "Unlock to edit?",
                "Unlock the launcher to add or edit tiles?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                return
        self.locked = not self.locked
        for i in range(self.grid.count()):
            w = self.grid.itemAt(i).widget()
            if isinstance(w, TileButton):
                w.refresh(self.locked)
        self.update_lock_ui()

    def open_tile(self, tile: Tile):
        webbrowser.open(tile.url)  # default browser

    def add_tile(self):
        name, ok = QInputDialog.getText(self, "Tile name", "Name:")
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "Tile URL", "URL (https://ï¿½):")
        if not ok or not url.strip():
            return

        # try to fetch a favicon automatically
        icon_path = fetch_favicon(url)
        icon = str(icon_path) if icon_path else None

        bg = "#F5F6FA"
        self.cfg.tiles.append(
            Tile(name=name.strip(), url=url.strip(), icon=icon, bg=bg)
        )
        self.rebuild()

    def edit_tile(self, tile: Tile):
        name, ok = QInputDialog.getText(self, "Edit tile", "Name:", text=tile.name)
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "Edit tile", "URL:", text=tile.url)
        if not ok or not url.strip():
            return

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

        tile.name, tile.url, tile.icon = name.strip(), url.strip(), icon
        self.rebuild()

    def remove_tile(self, tile: Tile):
        ok = QMessageBox.warning(
            self,
            "Remove tile?",
            f"Remove ï¿½{tile.name}ï¿½ from the launcher?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok == QMessageBox.StandardButton.Yes:
            self.cfg.tiles = [t for t in self.cfg.tiles if t is not tile]
            self.rebuild()

    def save(self):
        self.cfg.save()
        QMessageBox.information(self, "Saved", f"Config saved to:\n{CFG_PATH}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    mw = Main()
    mw.show()
    sys.exit(app.exec())
