# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from url_import import (
    MAX_IMPORT_TEXT_BYTES,
    UrlImportBatchError,
    UrlImportCandidate,
    UrlImportStatus,
    UrlInvalidReason,
    plan_url_import,
)


@dataclass(frozen=True)
class ImportDestination:
    """An existing tab and the URLs already assigned to it."""

    name: str = field(repr=False)
    urls: tuple[str, ...] = field(repr=False)
    hidden: bool = False


@dataclass(frozen=True)
class SelectedUrlImport:
    """A reviewed URL row selected for import."""

    source_line: int
    name: str = field(repr=False)
    url: str = field(repr=False)
    status: UrlImportStatus


_STATUS_TEXT = {
    UrlImportStatus.READY: "Ready",
    UrlImportStatus.DUPLICATE_IN_BATCH: "Duplicate in this import",
    UrlImportStatus.DUPLICATE_IN_TARGET_TAB: "Duplicate on destination tab",
    UrlImportStatus.DUPLICATE_ON_OTHER_TAB: (
        "Duplicate on another tab — review before importing"
    ),
}

_INVALID_REASON_TEXT = {
    UrlInvalidReason.CONTROL_CHARACTER: "contains a control character",
    UrlInvalidReason.EMBEDDED_WHITESPACE: "contains embedded whitespace",
    UrlInvalidReason.UNSUPPORTED_SCHEME: "uses an unsupported scheme",
    UrlInvalidReason.MISSING_HOST: "has no host",
    UrlInvalidReason.CREDENTIALS: "contains credentials",
    UrlInvalidReason.INVALID_HOST: "has an invalid host",
    UrlInvalidReason.INVALID_PORT: "has an invalid port",
    UrlInvalidReason.MALFORMED_URL: "is malformed",
}
_UTF8_BOM = b"\xef\xbb\xbf"
_MAX_LOADED_FILE_BYTES = MAX_IMPORT_TEXT_BYTES + len(_UTF8_BOM)


def _primary_screen() -> QScreen | None:
    """Return the primary screen while preserving Qt's runtime nullability."""
    return QApplication.primaryScreen()


def _bounded_initial_size(
    *,
    hint_width: int,
    hint_height: int,
    available_width: int,
    available_height: int,
    fallback_width: int,
    fallback_height: int,
    margin: int,
) -> tuple[int, int]:
    """Fit a preferred initial size strictly within the available work area."""
    width_budget = max(1, available_width - margin)
    height_budget = max(1, available_height - margin)
    return (
        min(max(hint_width, fallback_width), width_budget),
        min(max(hint_height, fallback_height), height_budget),
    )


class UrlImportDialog(QDialog):
    """Collect and review an offline batch of URLs before importing tiles."""

    _AVAILABLE_AREA_MARGIN = 48
    _FALLBACK_INITIAL_WIDTH = 900
    _FALLBACK_INITIAL_HEIGHT = 680

    def __init__(
        self,
        *,
        destinations: Sequence[ImportDestination],
        default_destination: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not destinations:
            raise ValueError("At least one import destination is required.")
        if len({destination.name for destination in destinations}) != len(destinations):
            raise ValueError("Import destination names must be unique.")

        self._destinations = tuple(destinations)
        self._candidates: tuple[UrlImportCandidate, ...] = ()
        self._has_review = False

        self.setWindowTitle("Import URLs")
        self.setSizeGripEnabled(True)

        root_layout = QVBoxLayout(self)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setAccessibleName("URL import controls and preview")
        content = QWidget(self.content_scroll)
        layout = QVBoxLayout(content)

        destination_label = QLabel("Destination tab:", self)
        self.destination_combo = QComboBox(self)
        self.destination_combo.setAccessibleName("Destination tab")
        self.destination_combo.setAccessibleDescription(
            "Tabs labeled hidden remain hidden after import."
        )
        for destination in self._destinations:
            label = (
                f"{destination.name} (hidden)"
                if destination.hidden
                else destination.name
            )
            self.destination_combo.addItem(label, destination.name)
        default_index = self.destination_combo.findData(default_destination)
        self.destination_combo.setCurrentIndex(max(default_index, 0))
        destination_label.setBuddy(self.destination_combo)
        destination_row = QHBoxLayout()
        destination_row.addWidget(destination_label)
        destination_row.addWidget(self.destination_combo, 1)
        layout.addLayout(destination_row)

        editor_label = QLabel("One URL per line", self)
        self.url_editor = QPlainTextEdit(self)
        self.url_editor.setAccessibleName("URLs to import")
        self.url_editor.setPlaceholderText("https://example.com")
        self.url_editor.setMinimumHeight(120)
        editor_label.setBuddy(self.url_editor)
        layout.addWidget(editor_label)
        layout.addWidget(self.url_editor, 1)

        editor_actions = QHBoxLayout()
        self.load_button = QPushButton("Load .txt…", self)
        self.load_button.setAccessibleDescription(
            "Load a UTF-8 text file containing one URL per line."
        )
        self.review_button = QPushButton("Review URLs", self)
        self.review_button.setDefault(True)
        editor_actions.addWidget(self.load_button)
        editor_actions.addStretch(1)
        editor_actions.addWidget(self.review_button)
        layout.addLayout(editor_actions)

        self.status_label = QLabel("Enter URLs, then choose Review URLs.", self)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("URL review status")
        layout.addWidget(self.status_label)

        self.preview_table = QTableWidget(0, 5, self)
        self.preview_table.setHorizontalHeaderLabels(
            ["Include", "Source line", "Name", "Normalized URL", "Status"]
        )
        self.preview_table.setAccessibleName("URL import preview")
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        header = self.preview_table.horizontalHeader()
        header.setStretchLastSection(True)
        self.preview_table.setColumnWidth(0, 72)
        self.preview_table.setColumnWidth(1, 90)
        self.preview_table.setColumnWidth(2, 180)
        self.preview_table.setColumnWidth(3, 300)
        layout.addWidget(self.preview_table, 2)

        self.selection_summary = QLabel("Import 0 Tiles", self)
        self.selection_summary.setAccessibleName("Selected tile count")
        layout.addWidget(self.selection_summary)

        self.content_scroll.setWidget(content)
        root_layout.addWidget(self.content_scroll, 1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        root_layout.addWidget(self.button_box)
        self._update_import_button()

        self.load_button.clicked.connect(self._load_text_file)
        self.review_button.clicked.connect(self._review_urls)
        self.url_editor.textChanged.connect(self._invalidate_review)
        self.destination_combo.currentIndexChanged.connect(self._destination_changed)
        self.preview_table.itemChanged.connect(self._preview_item_changed)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self._cap_initial_size_to_available_area()

    def selected_destination(self) -> str:
        """Return the raw tab name selected as the destination."""
        destination = self.destination_combo.currentData()
        if not isinstance(destination, str):
            raise RuntimeError("The selected import destination is unavailable.")
        return destination

    def selected_imports(self) -> tuple[SelectedUrlImport, ...]:
        """Return checked, valid preview rows in their original source order."""
        selected: list[SelectedUrlImport] = []
        if not self._has_review:
            return ()

        for row, candidate in enumerate(self._candidates):
            include_item = self.preview_table.item(row, 0)
            name_item = self.preview_table.item(row, 2)
            if (
                include_item is None
                or name_item is None
                or include_item.checkState() != Qt.CheckState.Checked
                or candidate.normalized_url is None
            ):
                continue
            name = name_item.text().strip()
            if not name:
                continue
            selected.append(
                SelectedUrlImport(
                    source_line=candidate.source_line,
                    name=name,
                    url=candidate.normalized_url,
                    status=candidate.status,
                )
            )
        return tuple(selected)

    def accept(self) -> None:
        if not self._selection_is_valid():
            return
        super().accept()

    def _load_text_file(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load URL list",
            "",
            "Text files (*.txt);;All files (*)",
        )
        if not file_name:
            return

        try:
            with Path(file_name).open("rb") as stream:
                payload = stream.read(_MAX_LOADED_FILE_BYTES + 1)
        except OSError:
            QMessageBox.warning(
                self,
                "Load URL list",
                "Could not read the selected UTF-8 text file.",
            )
            return

        bom_size = len(_UTF8_BOM) if payload.startswith(_UTF8_BOM) else 0
        if len(payload) - bom_size > MAX_IMPORT_TEXT_BYTES:
            QMessageBox.warning(
                self,
                "Load URL list",
                "The selected text file is too large to review.",
            )
            return
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeError:
            QMessageBox.warning(
                self,
                "Load URL list",
                "Could not read the selected UTF-8 text file.",
            )
            return

        if self.url_editor.toPlainText():
            response = QMessageBox.question(
                self,
                "Replace URL list?",
                "Replace the URLs currently in the editor?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return

        self.url_editor.setPlainText(text)
        self.url_editor.setFocus()

    def _invalidate_review(self) -> None:
        self._has_review = False
        self._candidates = ()
        self.preview_table.setRowCount(0)
        self.status_label.setText("URLs changed. Choose Review URLs to update preview.")
        self._update_import_button()

    def _destination_changed(self, _index: int) -> None:
        if self._has_review:
            self._review_urls()

    def _review_urls(self) -> None:
        destination_index = self.destination_combo.currentIndex()
        destination = self._destinations[destination_index]
        other_urls = (
            url
            for index, other_destination in enumerate(self._destinations)
            if index != destination_index
            for url in other_destination.urls
        )
        plan = plan_url_import(
            self.url_editor.toPlainText(),
            target_tab_urls=destination.urls,
            other_tab_urls=other_urls,
        )

        self._has_review = plan.is_valid_batch
        self._candidates = plan.candidates
        self.preview_table.blockSignals(True)
        try:
            self.preview_table.setRowCount(len(plan.candidates))
            for row, candidate in enumerate(plan.candidates):
                self._populate_preview_row(row, candidate)
        finally:
            self.preview_table.blockSignals(False)

        if plan.batch_error is not None:
            self.status_label.setText(self._batch_error_text(plan.batch_error))
        elif not plan.candidates:
            self.status_label.setText("No nonblank URL rows to review.")
        else:
            counts = plan.counts
            self.status_label.setText(
                f"Reviewed {counts.total} rows: {counts.ready} ready, "
                f"{counts.invalid} invalid, "
                f"{counts.duplicate_in_batch} duplicate in this import, "
                f"{counts.duplicate_in_target_tab} duplicate on the destination tab, "
                f"and {counts.duplicate_on_other_tab} duplicate on another tab."
            )
        self._update_import_button()

    def _populate_preview_row(self, row: int, candidate: UrlImportCandidate) -> None:
        valid = candidate.normalized_url is not None

        include_item = QTableWidgetItem()
        if valid:
            include_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            checked_by_default = candidate.status in {
                UrlImportStatus.READY,
                UrlImportStatus.DUPLICATE_ON_OTHER_TAB,
            }
            include_item.setCheckState(
                Qt.CheckState.Checked if checked_by_default else Qt.CheckState.Unchecked
            )
        else:
            include_item.setFlags(Qt.ItemFlag.NoItemFlags)
            include_item.setCheckState(Qt.CheckState.Unchecked)
            include_item.setToolTip("Invalid URLs cannot be imported.")
        include_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_table.setItem(row, 0, include_item)

        line_item = self._readonly_item(str(candidate.source_line))
        line_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_table.setItem(row, 1, line_item)

        name_item = QTableWidgetItem(candidate.fallback_name or "")
        if valid:
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
            )
        else:
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.preview_table.setItem(row, 2, name_item)

        url_item = self._readonly_item(candidate.normalized_url or "")
        self.preview_table.setItem(
            row,
            3,
            url_item,
        )
        status_item = self._readonly_item(self._status_text(candidate))
        self.preview_table.setItem(row, 4, status_item)

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    @staticmethod
    def _status_text(candidate: UrlImportCandidate) -> str:
        if candidate.status is UrlImportStatus.INVALID:
            reason = candidate.invalid_reason
            if reason is None:
                return "Invalid"
            return f"Invalid — {_INVALID_REASON_TEXT[reason]}"
        text = _STATUS_TEXT[candidate.status]
        if (
            candidate.status is UrlImportStatus.DUPLICATE_IN_BATCH
            and candidate.duplicate_of_line is not None
        ):
            return f"{text} (matches line {candidate.duplicate_of_line})"
        return text

    @staticmethod
    def _batch_error_text(error: UrlImportBatchError) -> str:
        if error is UrlImportBatchError.TEXT_TOO_LARGE:
            return "The URL list is too large to review."
        return "The URL list contains too many nonblank rows to review."

    def _preview_item_changed(self, _item: QTableWidgetItem) -> None:
        self._update_import_button()

    def _selection_is_valid(self) -> bool:
        if not self._has_review:
            return False
        checked_count = 0
        for row, candidate in enumerate(self._candidates):
            if candidate.normalized_url is None:
                continue
            include_item = self.preview_table.item(row, 0)
            name_item = self.preview_table.item(row, 2)
            if (
                include_item is not None
                and include_item.checkState() == Qt.CheckState.Checked
            ):
                checked_count += 1
                if name_item is None or not name_item.text().strip():
                    return False
        return checked_count > 0

    def _update_import_button(self) -> None:
        count = self._checked_row_count()
        text = f"Import {count} Tiles"
        self.selection_summary.setText(text)
        button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setText(text)
            button.setEnabled(self._selection_is_valid())

    def _checked_row_count(self) -> int:
        if not self._has_review:
            return 0
        checked_count = 0
        for row, candidate in enumerate(self._candidates):
            if candidate.normalized_url is None:
                continue
            include_item = self.preview_table.item(row, 0)
            if (
                include_item is not None
                and include_item.checkState() == Qt.CheckState.Checked
            ):
                checked_count += 1
        return checked_count

    def _cap_initial_size_to_available_area(self) -> None:
        screen = _primary_screen()
        parent = self.parentWidget()
        if parent is not None:
            parent_window = parent.window().windowHandle()
            if parent_window is not None:
                screen = parent_window.screen()

        if screen is None:
            self.resize(
                self._FALLBACK_INITIAL_WIDTH,
                self._FALLBACK_INITIAL_HEIGHT,
            )
            return

        available = screen.availableGeometry()
        hint = self.sizeHint()
        initial_width, initial_height = _bounded_initial_size(
            hint_width=hint.width(),
            hint_height=hint.height(),
            available_width=available.width(),
            available_height=available.height(),
            fallback_width=self._FALLBACK_INITIAL_WIDTH,
            fallback_height=self._FALLBACK_INITIAL_HEIGHT,
            margin=self._AVAILABLE_AREA_MARGIN,
        )
        self.resize(initial_width, initial_height)
