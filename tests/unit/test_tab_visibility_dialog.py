from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QScrollArea,
)

from tile_launcher import TabVisibilityDialog  # noqa: E402


@pytest.mark.unit
def test_tab_visibility_dialog_scrolls_many_tabs() -> None:
    try:
        _ = QApplication.instance() or QApplication([])
    except Exception:  # pragma: no cover - Qt may be missing platform plugins
        pytest.skip("Qt platform plugin not available")

    tabs = [f"Tab {index:02d}" for index in range(55)]
    dialog = TabVisibilityDialog(tabs, hidden=["Tab 03"])

    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.widgetResizable()

    checkboxes = scroll.widget().findChildren(QCheckBox)
    assert [checkbox.text() for checkbox in checkboxes] == tabs
    assert dialog.result_hidden() == ["Tab 03"]

    button_box = dialog.findChild(QDialogButtonBox)
    assert button_box is not None
    assert button_box not in scroll.findChildren(QDialogButtonBox)
