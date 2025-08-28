import importlib
import json
import sys
import types
from pathlib import Path

import pytest


class DummyTabs:
    def __init__(self):
        self.current_index = None
    def setCurrentIndex(self, idx):
        self.current_index = idx


def import_launcher(monkeypatch, tmp_path):
    class _Stub:
        def __init__(self, *a, **kw):
            pass

    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    qtwidgets.QApplication = _Stub
    qtwidgets.QGridLayout = _Stub
    qtwidgets.QInputDialog = types.SimpleNamespace(
        getText=lambda *a, **k: ("", False),
        getItem=lambda *a, **k: ("", False),
    )
    qtwidgets.QMainWindow = _Stub
    qtwidgets.QMenu = _Stub
    qtwidgets.QMessageBox = types.SimpleNamespace(
        critical=lambda *a, **k: None,
        question=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        StandardButton=types.SimpleNamespace(Yes=1, No=2),
    )
    qtwidgets.QScrollArea = _Stub
    qtwidgets.QTabWidget = _Stub
    qtwidgets.QToolBar = _Stub
    qtwidgets.QToolButton = _Stub
    qtwidgets.QFileDialog = types.SimpleNamespace(getOpenFileName=lambda *a, **k: ("", ""))
    qtwidgets.QWidget = _Stub

    qtgui = types.ModuleType("PySide6.QtGui")
    for name in (
        "QAction",
        "QColor",
        "QDrag",
        "QDragEnterEvent",
        "QDropEvent",
        "QFont",
        "QIcon",
        "QMouseEvent",
        "QPainter",
        "QPixmap",
    ):
        setattr(qtgui, name, _Stub)

    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.QMimeData = _Stub
    qtcore.QPoint = _Stub
    qtcore.QSize = _Stub
    qtcore.Qt = types.SimpleNamespace(ToolBarArea=types.SimpleNamespace(TopToolBarArea=0))
    qtcore.QTimer = types.SimpleNamespace(singleShot=lambda *a, **k: None)

    pyside6 = types.ModuleType("PySide6")
    pyside6.QtWidgets = qtwidgets
    pyside6.QtGui = qtgui
    pyside6.QtCore = qtcore

    monkeypatch.setitem(sys.modules, "PySide6", pyside6)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", qtgui)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    tl = importlib.import_module("tile_launcher")
    monkeypatch.setitem(sys.modules, "tile_launcher", tl)
    monkeypatch.setattr(tl, "CFG_PATH", tmp_path / "config.json")
    return tl


@pytest.fixture
def main(monkeypatch, tmp_path):
    tl = import_launcher(monkeypatch, tmp_path)
    main = tl.Main.__new__(tl.Main)
    main.cfg = tl.LauncherConfig(title="Launcher", tiles=[], tabs=["Main"])
    main.rebuild_called = 0
    def rebuild():
        main.rebuild_called += 1
    main.rebuild = rebuild
    main.tabs_widget = DummyTabs()
    return main, tl


def test_add_tile_defaults_to_system_browser(main, monkeypatch, tmp_path):
    main_obj, tl = main
    monkeypatch.setattr(tl, "fetch_favicon", lambda url: None)
    tile = main_obj.add_tile_data("Example", "example.com", "Main")
    assert tile.browser is None
    assert tile.url == "https://example.com"
    assert main_obj.rebuild_called == 1
    assert main_obj.tabs_widget.current_index == 0
    cfg = json.loads(Path(tmp_path / "config.json").read_text())
    assert cfg["tiles"][0]["url"] == "https://example.com"
    reloaded = tl.LauncherConfig.load()
    assert any(t.name == "Example" for t in reloaded.tiles)


def test_add_tile_validation(main, monkeypatch):
    main_obj, tl = main
    monkeypatch.setattr(tl, "fetch_favicon", lambda url: None)
    with pytest.raises(ValueError):
        main_obj.add_tile_data("", "https://example.com", "Main")
    with pytest.raises(ValueError):
        main_obj.add_tile_data("Example", "", "Main")
    with pytest.raises(ValueError):
        main_obj.add_tile_data("Example", "https://example.com", "")


def test_url_normalization(main, monkeypatch):
    main_obj, tl = main
    monkeypatch.setattr(tl, "fetch_favicon", lambda url: None)
    tile = main_obj.add_tile_data("Example", "example.com", "Main")
    assert tile.url == "https://example.com"
