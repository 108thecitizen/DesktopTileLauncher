from __future__ import annotations

# mypy: disable-error-code=unreachable

import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    # Only for type hints; runtime import cycles avoided.
    from tile_launcher import Tile  # noqa: F401

from browser_chrome_win import (
    is_chrome_path,
    is_windows_default_browser_chrome,
    list_chrome_profiles,
)


def _normalize_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    parsed = urllib.parse.urlparse(s)
    return s if parsed.scheme else f"https://{s}"


class TileEditorDialog(QDialog):
    """Dialog for creating or editing a tile."""

    def __init__(
        self,
        *,
        tabs: list[str],
        browsers: list[str],
        icon_dir: Path,
        fetch_favicon: Callable[[str], Path | None],
        tile: "Tile | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tile")
        self.icon_dir = icon_dir
        self.fetch_favicon = fetch_favicon
        self._icon_path: str | None = getattr(tile, "icon", None) if tile else None
        self.data: dict[str, str | None] | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.name_edit = QLineEdit(getattr(tile, "name", "") if tile else "")
        form.addRow("Name:", self.name_edit)

        self.url_edit = QLineEdit(getattr(tile, "url", "") if tile else "")
        form.addRow("URL:", self.url_edit)

        icon_row = QHBoxLayout()
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(64, 64)
        self.icon_preview.setScaledContents(True)
        icon_row.addWidget(self.icon_preview)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_icon)
        icon_row.addWidget(browse_btn)

        fetch_btn = QPushButton("Fetch Favicon")
        fetch_btn.clicked.connect(self._fetch_icon)
        icon_row.addWidget(fetch_btn)

        form.addRow("Icon:", icon_row)

        self.tab_combo = QComboBox()
        self.tab_combo.addItems(tabs)
        if tile:
            idx = self.tab_combo.findText(
                getattr(tile, "tab", ""), Qt.MatchFlag.MatchExactly
            )
            if idx >= 0:
                self.tab_combo.setCurrentIndex(idx)
        form.addRow("Tab:", self.tab_combo)

        self.browser_combo = QComboBox()
        self.browser_combo.addItem("Default")
        for b in browsers:
            self.browser_combo.addItem(b)
        if tile and getattr(tile, "browser", None):
            idx = self.browser_combo.findText(tile.browser, Qt.MatchFlag.MatchExactly)  # type: ignore[attr-defined]
            if idx >= 0:
                self.browser_combo.setCurrentIndex(idx)
        form.addRow("Browser:", self.browser_combo)

        self.open_target_combo = QComboBox()
        self.open_target_combo.addItem("New tab in existing window", "tab")
        self.open_target_combo.addItem("New browser window", "window")
        if tile:
            idx = self.open_target_combo.findData(getattr(tile, "open_target", "tab"))
            if idx >= 0:
                self.open_target_combo.setCurrentIndex(idx)
        form.addRow("Open in:", self.open_target_combo)

        self.chromeProfileLabel = QLabel("Chrome profile")
        self.chromeProfileCombo = QComboBox()
        self.chromeProfileCombo.addItem("None (use Chrome default)", "")
        if sys.platform == "win32":
            for dir_id, display in list_chrome_profiles():
                self.chromeProfileCombo.addItem(display, dir_id)
        self.chromeProfileLabel.setVisible(False)
        self.chromeProfileCombo.setVisible(False)
        form.addRow(self.chromeProfileLabel, self.chromeProfileCombo)

        if tile and getattr(tile, "chrome_profile", None):
            idx = self.chromeProfileCombo.findData(tile.chrome_profile)
            if idx >= 0:
                self.chromeProfileCombo.setCurrentIndex(idx)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(self.button_box)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self._update_icon_preview()
        self._update_ok()

        self.name_edit.textChanged.connect(self._update_ok)  # type: ignore[arg-type]
        self.url_edit.textChanged.connect(self._update_ok)  # type: ignore[arg-type]
        self.browser_combo.currentIndexChanged.connect(
            self._refresh_chrome_profile_visibility
        )
        self._refresh_chrome_profile_visibility()

    def _update_ok(self) -> None:
        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setEnabled(
            bool(self.name_edit.text().strip()) and bool(self.url_edit.text().strip())
        )

    def _update_icon_preview(self) -> None:
        if self._icon_path and Path(self._icon_path).exists():
            pix = QPixmap(self._icon_path)
            pix = pix.scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.icon_preview.setPixmap(pix)
        else:
            self.icon_preview.clear()

    def _browse_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose icon", str(self.icon_dir), "Images (*.png *.ico)"
        )
        if path:
            self._icon_path = path
            self._update_icon_preview()

    def _fetch_icon(self) -> None:
        url = _normalize_url(self.url_edit.text())
        if not url:
            return
        try:
            result = self.fetch_favicon(url)
        except Exception:
            result = None
        if result:
            self._icon_path = str(result)
            self._update_icon_preview()

    def _is_effective_browser_chrome(self) -> bool:
        """Return True if the dialog's browser selection resolves to Chrome."""
        sel = self.browser_combo.currentText()
        if sys.platform != "win32":
            return False
        if sel == "Default":
            return is_windows_default_browser_chrome()
        return is_chrome_path(sel) or "chrome" in sel.lower()

    def _refresh_chrome_profile_visibility(self) -> None:
        """Show or hide the Chrome profile widgets based on browser selection."""
        is_chrome = self._is_effective_browser_chrome()
        self.chromeProfileLabel.setVisible(is_chrome)
        self.chromeProfileCombo.setVisible(is_chrome)

    def accept(self) -> None:  # noqa: D401
        name = self.name_edit.text().strip()
        url = _normalize_url(self.url_edit.text())
        tab = self.tab_combo.currentText()
        browser_text = self.browser_combo.currentText()
        browser = None if browser_text == "Default" else browser_text

        # If the user didn’t pick an icon, try to fetch a favicon now (behavior parity with old Add).
        chosen: Path | None = Path(self._icon_path) if self._icon_path else None
        if chosen is None:
            try:
                auto = self.fetch_favicon(url)
                chosen = auto if auto else None
            except Exception:
                chosen = None

        icon: str | None = None
        if chosen and chosen.exists():
            dest = self.icon_dir / chosen.name
            try:
                if chosen != dest:
                    shutil.copy(chosen, dest)
                icon = str(dest)
            except Exception:
                icon = None

        chrome_prof_data = self.chromeProfileCombo.currentData()
        chrome_profile = str(chrome_prof_data) if chrome_prof_data else None

        open_target_data = self.open_target_combo.currentData()
        open_target = str(open_target_data) if open_target_data else "tab"

        self.data = {
            "name": name,
            "url": url,
            "tab": tab,
            "icon": icon,
            "browser": browser,
            "chrome_profile": chrome_profile,
            "open_target": open_target,
        }
        super().accept()
