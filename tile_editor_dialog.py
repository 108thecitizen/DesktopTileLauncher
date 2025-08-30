from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

if TYPE_CHECKING:  # pragma: no cover - only for type checking
    from tile_launcher import Tile


def _normalize_url(raw: str) -> str:
    """Ensure a URL has a scheme; default to https."""
    s = (raw or "").strip()
    if not s:
        return ""
    parsed = urllib.parse.urlparse(s)
    return s if parsed.scheme else f"https://{s}"


class TileEditorDialog(QDialog):
    """Dialog for adding or editing a tile."""

    def __init__(
        self,
        *,
        tabs: list[str],
        browsers: list[str],
        tile: Tile | None = None,
        favicon_fetcher: Callable[[str], Optional[Path]] | None = None,
        default_tab: str = "Main",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Tile" if tile else "Add Tile")

        self._fetch_favicon = favicon_fetcher
        self.icon_path: str | None = tile.icon if tile else None

        self.name_edit = QLineEdit(tile.name if tile else "")
        self.url_edit = QLineEdit(tile.url if tile else "")
        self.url_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"https?://.+"), self)
        )

        self.tab_combo = QComboBox()
        self.tab_combo.addItems(tabs)
        self.tab_combo.setCurrentText(tile.tab if tile else default_tab)

        self.browser_combo = QComboBox()
        self.browser_combo.addItems(browsers)
        browser_initial = tile.browser if tile and tile.browser else "Default"
        self.browser_combo.setCurrentText(browser_initial)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setScaledContents(True)
        self._update_icon_preview()

        self.browse_btn = QPushButton("Browse…")
        self.fetch_btn = QPushButton("Fetch Favicon")
        self.browse_btn.clicked.connect(self._browse_icon)
        self.fetch_btn.clicked.connect(self._fetch_icon)
        if not self._fetch_favicon:
            self.fetch_btn.setEnabled(False)

        icon_widget = QWidget()
        icon_layout = QHBoxLayout(icon_widget)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.addWidget(self.icon_label)
        icon_layout.addWidget(self.browse_btn)
        icon_layout.addWidget(self.fetch_btn)

        layout = QFormLayout(self)
        layout.addRow("Name:", self.name_edit)
        layout.addRow("URL:", self.url_edit)
        layout.addRow("Tab:", self.tab_combo)
        layout.addRow("Browser:", self.browser_combo)
        layout.addRow("Icon:", icon_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        # placeholders for results
        self.name: str = tile.name if tile else ""
        self.url: str = tile.url if tile else ""
        self.tab: str = tile.tab if tile else default_tab
        self.browser: Optional[str] = tile.browser if tile else None

    # ---- helpers ----
    def _update_icon_preview(self) -> None:
        if self.icon_path and Path(self.icon_path).exists():
            self.icon_label.setPixmap(QPixmap(self.icon_path))
        else:
            self.icon_label.setPixmap(QPixmap())

    def _browse_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose icon", "", "Images (*.png *.ico)"
        )
        if path:
            self.icon_path = path
            self._update_icon_preview()

    def _fetch_icon(self) -> None:
        if not self._fetch_favicon:
            return
        url = _normalize_url(self.url_edit.text())
        if not url:
            QMessageBox.warning(self, "URL Required", "Enter a URL first.")
            return
        fetched = self._fetch_favicon(url)
        if fetched:
            self.icon_path = str(fetched)
            self._update_icon_preview()

    # ---- overrides ----
    def accept(self) -> None:  # noqa: D401 - standard override
        """Validate and store selections before closing."""
        name = self.name_edit.text().strip()
        url_raw = self.url_edit.text().strip()
        if not name or not url_raw:
            QMessageBox.warning(self, "Missing data", "Name and URL are required.")
            return
        url = _normalize_url(url_raw)
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            QMessageBox.warning(self, "Invalid URL", "Enter a valid URL.")
            return
        self.name = name
        self.url = url
        self.tab = self.tab_combo.currentText()
        browser_choice = self.browser_combo.currentText()
        self.browser = None if browser_choice == "Default" else browser_choice
        super().accept()
