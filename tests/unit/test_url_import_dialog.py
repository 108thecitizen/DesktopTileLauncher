# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
import os
import socket
import urllib.request
from pathlib import Path
from typing import Never

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

from url_import_dialog import (  # noqa: E402
    ImportDestination,
    UrlImportDialog,
    _bounded_initial_size,
)


pytestmark = pytest.mark.unit

_SENSITIVE_URL = "https://example.test/private?token=do-not-log#customer"
_SENSITIVE_NAME = "Confidential customer portal"


@pytest.fixture(autouse=True)
def _application() -> QApplication:
    try:
        instance = QApplication.instance()
        return instance if isinstance(instance, QApplication) else QApplication([])
    except Exception:  # pragma: no cover - Qt may be missing platform plugins
        pytest.skip("Qt platform plugin not available")


def _destinations() -> tuple[ImportDestination, ...]:
    return (
        ImportDestination(
            name="Main",
            urls=("https://example.test/already",),
        ),
        ImportDestination(
            name="Archive",
            urls=("https://example.test/on-other-tab",),
            hidden=True,
        ),
    )


def _dialog(*, default_destination: str = "Main") -> UrlImportDialog:
    return UrlImportDialog(
        destinations=_destinations(),
        default_destination=default_destination,
    )


def _import_button(dialog: UrlImportDialog) -> QPushButton:
    button = dialog.button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert button is not None
    return button


def _review_mixed_urls(dialog: UrlImportDialog) -> None:
    dialog.url_editor.setPlainText(
        "\n".join(
            [
                "https://example.test/new?x=1#fragment",
                "ftp://example.test/not-supported",
                "HTTPS://EXAMPLE.TEST:443/new?x=1#fragment",
                "https://example.test/already",
                "https://example.test/on-other-tab",
            ]
        )
    )
    dialog.review_button.click()


def test_mixed_review_has_required_rows_statuses_and_selection_defaults() -> None:
    dialog = _dialog()

    _review_mixed_urls(dialog)

    assert dialog.selected_destination() == "Main"
    assert dialog.preview_table.rowCount() == 5
    assert [dialog.preview_table.item(row, 1).text() for row in range(5)] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]

    include_items = [dialog.preview_table.item(row, 0) for row in range(5)]
    assert [item.checkState() for item in include_items] == [
        Qt.CheckState.Checked,
        Qt.CheckState.Unchecked,
        Qt.CheckState.Unchecked,
        Qt.CheckState.Unchecked,
        Qt.CheckState.Checked,
    ]

    invalid_include = include_items[1]
    assert not invalid_include.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert not invalid_include.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "Invalid" in dialog.preview_table.item(1, 4).text()
    assert "unsupported scheme" in dialog.preview_table.item(1, 4).text()

    for duplicate_row in (2, 3, 4):
        flags = include_items[duplicate_row].flags()
        assert flags & Qt.ItemFlag.ItemIsEnabled
        assert flags & Qt.ItemFlag.ItemIsUserCheckable
    assert "matches line 1" in dialog.preview_table.item(2, 4).text()
    assert "destination tab" in dialog.preview_table.item(3, 4).text()
    assert "review before importing" in dialog.preview_table.item(4, 4).text()

    assert [item.source_line for item in dialog.selected_imports()] == [1, 5]
    assert _import_button(dialog).text() == "Import 2 Tiles"
    assert _import_button(dialog).isEnabled()
    assert dialog.selection_summary.text() == "Import 2 Tiles"

    include_items[2].setCheckState(Qt.CheckState.Checked)
    include_items[3].setCheckState(Qt.CheckState.Checked)
    assert [item.source_line for item in dialog.selected_imports()] == [1, 3, 4, 5]
    assert _import_button(dialog).text() == "Import 4 Tiles"


def test_names_must_be_nonempty_and_selected_count_is_always_exact() -> None:
    dialog = _dialog()
    _review_mixed_urls(dialog)
    import_button = _import_button(dialog)

    first_name = dialog.preview_table.item(0, 2)
    first_name.setText("   ")

    assert import_button.text() == "Import 2 Tiles"
    assert dialog.selection_summary.text() == "Import 2 Tiles"
    assert not import_button.isEnabled()
    dialog.accept()
    assert not dialog.result()

    first_name.setText("  Renamed tile  ")
    dialog.preview_table.item(4, 0).setCheckState(Qt.CheckState.Unchecked)

    assert import_button.text() == "Import 1 Tiles"
    assert dialog.selection_summary.text() == "Import 1 Tiles"
    assert import_button.isEnabled()
    selected = dialog.selected_imports()
    assert len(selected) == 1
    assert selected[0].name == "Renamed tile"


def test_hidden_destination_is_labeled_but_raw_name_is_returned() -> None:
    dialog = _dialog(default_destination="Archive")

    assert dialog.destination_combo.currentText() == "Archive (hidden)"
    assert dialog.selected_destination() == "Archive"
    assert "hidden" in dialog.destination_combo.accessibleDescription().lower()


def test_editor_change_invalidates_stale_review_and_disables_import() -> None:
    dialog = _dialog()
    _review_mixed_urls(dialog)
    assert dialog.selected_imports()

    dialog.url_editor.appendPlainText("https://example.test/changed")

    assert dialog.preview_table.rowCount() == 0
    assert dialog.selected_imports() == ()
    assert dialog.selection_summary.text() == "Import 0 Tiles"
    assert not _import_button(dialog).isEnabled()
    assert "Review URLs" in dialog.status_label.text()


def test_load_utf8_bom_file_and_confirm_before_replacing_editor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "urls.txt"
    source.write_bytes(
        b"\xef\xbb\xbfhttps://example.test/one\r\nhttps://example.test/two\r\n"
    )
    dialog = _dialog()
    dialog.url_editor.setPlainText("https://example.test/original")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(source), "Text files (*.txt)"),
    )

    confirmations: list[object] = []

    def decline_replace(*_args: object, **_kwargs: object) -> object:
        confirmations.append(object())
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline_replace)
    dialog.load_button.click()

    assert len(confirmations) == 1
    assert dialog.url_editor.toPlainText() == "https://example.test/original"

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog.load_button.click()

    loaded = dialog.url_editor.toPlainText()
    assert not loaded.startswith("\ufeff")
    assert loaded.splitlines() == [
        "https://example.test/one",
        "https://example.test/two",
    ]
    assert dialog.preview_table.rowCount() == 0
    assert not _import_button(dialog).isEnabled()


def test_file_read_failure_is_generic_and_does_not_expose_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_path = tmp_path / "customer-token-do-not-expose"
    sensitive_path.mkdir()
    dialog = _dialog()
    original = "https://example.test/original"
    dialog.url_editor.setPlainText(original)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(sensitive_path), "Text files (*.txt)"),
    )
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, text, *_args, **_kwargs: warnings.append((title, text)),
    )

    with caplog.at_level(logging.DEBUG):
        dialog.load_button.click()

    assert dialog.url_editor.toPlainText() == original
    assert len(warnings) == 1
    assert "UTF-8 text file" in warnings[0][1]
    assert str(sensitive_path) not in repr(warnings)
    assert str(sensitive_path) not in caplog.text


def test_controls_and_table_are_keyboard_accessible_without_color_only_status() -> None:
    dialog = _dialog()
    _review_mixed_urls(dialog)

    assert dialog.isSizeGripEnabled()
    assert dialog.content_scroll.widgetResizable()
    for widget in (
        dialog.destination_combo,
        dialog.url_editor,
        dialog.preview_table,
        dialog.status_label,
        dialog.selection_summary,
    ):
        assert widget.accessibleName()

    labels = dialog.findChildren(QLabel)
    assert any(
        label.text() == "Destination tab:" and label.buddy() is dialog.destination_combo
        for label in labels
    )
    assert any(
        label.text() == "One URL per line" and label.buddy() is dialog.url_editor
        for label in labels
    )
    assert dialog.preview_table.horizontalHeaderItem(0).text() == "Include"
    assert dialog.preview_table.horizontalHeaderItem(2).text() == "Name"
    assert dialog.preview_table.horizontalHeaderItem(4).text() == "Status"
    assert (
        dialog.preview_table.editTriggers()
        & QAbstractItemView.EditTrigger.EditKeyPressed
    )

    # Invalid rows remain readable and keyboard-selectable even though their
    # Include cell cannot be toggled.
    for column in range(1, dialog.preview_table.columnCount()):
        item = dialog.preview_table.item(1, column)
        assert item.flags() & Qt.ItemFlag.ItemIsEnabled
        assert item.flags() & Qt.ItemFlag.ItemIsSelectable
    assert dialog.preview_table.item(1, 4).text().startswith("Invalid")
    assert "review before importing" in dialog.preview_table.item(4, 4).text()


def test_initial_size_is_bounded_for_a_small_high_dpi_work_area() -> None:
    width, height = _bounded_initial_size(
        hint_width=1100,
        hint_height=900,
        available_width=683,
        available_height=344,
        fallback_width=900,
        fallback_height=680,
        margin=48,
    )

    assert (width, height) == (635, 296)
    assert width < 683
    assert height < 344


def test_review_and_selection_are_offline_and_do_not_log_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_network(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    dialog = _dialog()

    with caplog.at_level(logging.DEBUG):
        dialog.url_editor.setPlainText(_SENSITIVE_URL)
        dialog.review_button.click()
        dialog.preview_table.item(0, 2).setText(_SENSITIVE_NAME)
        selected = dialog.selected_imports()

    assert len(selected) == 1
    assert selected[0].url == _SENSITIVE_URL
    assert selected[0].name == _SENSITIVE_NAME
    assert _SENSITIVE_URL not in caplog.text
    assert _SENSITIVE_NAME not in caplog.text
    assert _SENSITIVE_URL not in repr(selected)
    assert _SENSITIVE_NAME not in repr(selected)
