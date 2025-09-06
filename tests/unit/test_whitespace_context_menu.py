from __future__ import annotations

import os
import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from tile_launcher import Main  # noqa: E402


@pytest.mark.unit
def test_tab_viewport_has_context_menu_policy():
    try:
        _ = QApplication.instance() or QApplication([])
    except Exception:  # pragma: no cover - Qt may be missing plugins
        pytest.skip("Qt platform plugin not available")
    main = Main()
    scroll = main.tabs_widget.widget(0)
    assert isinstance(scroll, QScrollArea)
    vp = scroll.viewport()
    assert vp.contextMenuPolicy() == Qt.ContextMenuPolicy.DefaultContextMenu
