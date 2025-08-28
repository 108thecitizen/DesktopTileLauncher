import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QInputDialog  # noqa: E402

from tile_launcher import Main  # noqa: E402


def test_tile_visible_on_first_add(tmp_path, monkeypatch):
    monkeypatch.setattr("tile_launcher.CFG_DIR", tmp_path)
    monkeypatch.setattr("tile_launcher.CFG_PATH", tmp_path / "config.json")
    icons = tmp_path / "icons"
    icons.mkdir()
    monkeypatch.setattr("tile_launcher.ICON_DIR", icons)

    app = QApplication([])
    main = Main()
    start = main._grids["Main"].count()

    text_vals = [("Foo", True), ("https://example.com", True)]
    item_vals = [("main", True), ("Default", True)]

    def fake_getText(*args, **kwargs):  # pragma: no cover - simple
        return text_vals.pop(0)

    def fake_getItem(*args, **kwargs):  # pragma: no cover - simple
        return item_vals.pop(0)

    monkeypatch.setattr(QInputDialog, "getText", fake_getText)
    monkeypatch.setattr(QInputDialog, "getItem", fake_getItem)
    monkeypatch.setattr("tile_launcher.available_browsers", lambda: [])

    main.add_tile()

    assert main._grids["Main"].count() == start + 1
    assert any(t.name == "Foo" for t in main.cfg.tiles if t.tab.lower() == "main")

    app.quit()
