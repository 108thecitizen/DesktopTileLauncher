# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

# mypy: disable-error-code=unreachable

import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
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
from page_title_lookup import (
    LookupRequest,
    TitleSuggestionController,
    fetch_page_title,
)

TitleFetcher = Callable[[str], str | None]


def _normalize_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    parsed = urllib.parse.urlparse(s)
    return s if parsed.scheme else f"https://{s}"


class _TitleLookupSignals(QObject):
    finished = Signal(int, object)


class _TitleLookupRunnable(QRunnable):
    def __init__(
        self,
        request: LookupRequest,
        fetch_title: TitleFetcher,
        signals: _TitleLookupSignals,
    ) -> None:
        super().__init__()
        self.generation = request.generation
        self._url = request.url
        self._fetch_title = fetch_title
        self.signals = signals

    @Slot()
    def run(self) -> None:
        try:
            title = self._fetch_title(self._url)
        except Exception:
            title = None
        self.signals.finished.emit(self.generation, title)


class TileEditorDialog(QDialog):
    """Dialog for creating or editing a tile."""

    def __init__(
        self,
        *,
        tabs: list[str],
        browsers: list[str],
        icon_dir: Path,
        fetch_favicon: Callable[[str], Path | None],
        fetch_title: TitleFetcher = fetch_page_title,
        tile: "Tile | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tile")
        self.icon_dir = icon_dir
        self.fetch_favicon = fetch_favicon
        self.fetch_title = fetch_title
        self._icon_path: str | None = getattr(tile, "icon", None) if tile else None
        self._title_suggestion = TitleSuggestionController(is_add_dialog=tile is None)
        self._title_lookup_signals: dict[int, _TitleLookupSignals] = {}
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
        self.name_edit.textEdited.connect(self._on_name_edited)  # type: ignore[arg-type]
        self.url_edit.textChanged.connect(self._update_ok)  # type: ignore[arg-type]
        self.url_edit.textChanged.connect(self._on_url_changed)  # type: ignore[arg-type]
        self.url_edit.editingFinished.connect(self._on_url_editing_finished)
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

    def _on_name_edited(self, _text: str) -> None:
        self._title_suggestion.name_edited()

    def _on_url_changed(self, _text: str) -> None:
        decision = self._title_suggestion.url_changed(self.name_edit.text())
        if decision.clear_name:
            self.name_edit.clear()

    def _on_url_editing_finished(self) -> None:
        request = self._title_suggestion.begin_lookup(self.url_edit.text())
        if request is None:
            return
        signals = _TitleLookupSignals()
        signals.finished.connect(
            self._on_title_lookup_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._title_lookup_signals[request.generation] = signals
        runnable = _TitleLookupRunnable(request, self.fetch_title, signals)
        QThreadPool.globalInstance().start(runnable)

    @Slot(int, object)
    def _on_title_lookup_finished(self, generation: int, title: object) -> None:
        self._title_lookup_signals.pop(generation, None)
        result = title if isinstance(title, str) else None
        decision = self._title_suggestion.apply_result(
            generation, result, self.name_edit.text()
        )
        if decision.title is not None:
            self.name_edit.setText(decision.title)

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

    def done(self, result: int) -> None:  # noqa: D401
        self._title_suggestion.deactivate()
        super().done(result)

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
