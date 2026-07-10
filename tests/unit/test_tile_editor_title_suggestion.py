# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtCore")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRunnable  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

import tile_editor_dialog  # noqa: E402
from tile_editor_dialog import TileEditorDialog  # noqa: E402


pytestmark = pytest.mark.unit


class _FakeThreadPool:
    def __init__(self) -> None:
        self.runnables: list[QRunnable] = []

    def start(self, runnable: QRunnable) -> None:
        self.runnables.append(runnable)

    def run_next(self) -> None:
        runnable = self.runnables.pop(0)
        runnable.run()


class _FakeQThreadPool:
    pool: _FakeThreadPool

    @staticmethod
    def globalInstance() -> _FakeThreadPool:
        return _FakeQThreadPool.pool


class _Fetcher:
    def __init__(self, results: list[str | None | Exception]) -> None:
        self._results = results
        self.calls: list[str] = []

    def __call__(self, url: str) -> str | None:
        self.calls.append(url)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def app() -> QApplication:
    try:
        application = QApplication.instance()
        if application is None:
            application = QApplication([])
    except Exception:  # pragma: no cover - Qt may be missing platform plugins
        pytest.skip("Qt platform plugin not available")
    return cast(QApplication, application)


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _FakeThreadPool:
    pool = _FakeThreadPool()
    _FakeQThreadPool.pool = pool
    monkeypatch.setattr(tile_editor_dialog, "QThreadPool", _FakeQThreadPool)
    return pool


def _dialog(
    tmp_path: Path,
    fetcher: Callable[[str], str | None],
    *,
    tile: Any | None = None,
) -> TileEditorDialog:
    return TileEditorDialog(
        tabs=["Main"],
        browsers=[],
        icon_dir=tmp_path,
        fetch_favicon=lambda _url: None,
        fetch_title=fetcher,
        tile=tile,
    )


def _flush(app: QApplication) -> None:
    app.processEvents()


def _finish_lookup(app: QApplication, fake_pool: _FakeThreadPool) -> None:
    fake_pool.run_next()
    _flush(app)


def _type_name(dialog: TileEditorDialog, text: str) -> None:
    dialog.name_edit.setText(text)
    dialog.name_edit.textEdited.emit(text)


def _ok_enabled(dialog: TileEditorDialog) -> bool:
    button = dialog.button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert button is not None
    return button.isEnabled()


def test_add_dialog_starts_lookup_on_editing_finished_not_keystrokes(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    fetcher = _Fetcher(["Example"])
    dialog = _dialog(tmp_path, fetcher)

    dialog.url_edit.setText("exa")
    dialog.url_edit.setText("example.test")
    _flush(app)

    assert fake_pool.runnables == []
    assert fetcher.calls == []

    dialog.url_edit.editingFinished.emit()

    assert len(fake_pool.runnables) == 1
    _finish_lookup(app, fake_pool)
    assert fetcher.calls == ["https://example.test"]


def test_blank_untouched_name_receives_result(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["Example Title"]))

    dialog.url_edit.setText("example.test")
    dialog.url_edit.editingFinished.emit()
    fake_pool.run_next()
    assert dialog.name_edit.text() == ""
    _flush(app)

    assert dialog.name_edit.text() == "Example Title"
    assert _ok_enabled(dialog)


def test_typed_name_is_not_overwritten(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["Example Title"]))

    _type_name(dialog, "Mine")
    dialog.url_edit.setText("example.test")
    dialog.url_edit.editingFinished.emit()
    _flush(app)

    assert fake_pool.runnables == []
    assert dialog.name_edit.text() == "Mine"


def test_user_editing_while_lookup_pending_wins(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["Remote Title"]))

    dialog.url_edit.setText("example.test")
    dialog.url_edit.editingFinished.emit()
    _type_name(dialog, "Local Title")
    _finish_lookup(app, fake_pool)

    assert dialog.name_edit.text() == "Local Title"


def test_url_changes_invalidate_stale_results(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["First", "Second"]))

    dialog.url_edit.setText("example.test/one")
    dialog.url_edit.editingFinished.emit()
    dialog.url_edit.setText("example.test/two")
    dialog.url_edit.editingFinished.emit()

    _finish_lookup(app, fake_pool)
    assert dialog.name_edit.text() == ""

    _finish_lookup(app, fake_pool)
    assert dialog.name_edit.text() == "Second"


def test_automatic_suggestion_is_cleared_and_replaced_for_changed_url(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["First", "Second"]))

    dialog.url_edit.setText("example.test/one")
    dialog.url_edit.editingFinished.emit()
    _finish_lookup(app, fake_pool)
    assert dialog.name_edit.text() == "First"

    dialog.url_edit.setText("example.test/two")
    assert dialog.name_edit.text() == ""
    dialog.url_edit.editingFinished.emit()
    _finish_lookup(app, fake_pool)

    assert dialog.name_edit.text() == "Second"


def test_edit_dialog_never_starts_lookup(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    fetcher = _Fetcher(["Ignored"])
    tile = SimpleNamespace(
        name="Existing",
        url="https://example.test/",
        tab="Main",
        icon=None,
        browser=None,
        chrome_profile=None,
        open_target="tab",
    )
    dialog = _dialog(tmp_path, fetcher, tile=tile)

    dialog.url_edit.setText("example.test/changed")
    dialog.url_edit.editingFinished.emit()
    _flush(app)

    assert fake_pool.runnables == []
    assert fetcher.calls == []
    assert dialog.name_edit.text() == "Existing"


def test_malformed_url_never_escapes_from_editing_finished(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["Ignored"]))

    dialog.url_edit.setText("https://[bad/")
    dialog.url_edit.editingFinished.emit()
    _flush(app)

    assert fake_pool.runnables == []
    assert dialog.name_edit.text() == ""


@pytest.mark.parametrize("action", ["reject", "accept", "close"])
def test_dialog_completion_prevents_late_result(
    app: QApplication,
    fake_pool: _FakeThreadPool,
    tmp_path: Path,
    action: str,
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["Late"]))

    dialog.show()
    _flush(app)
    dialog.url_edit.setText("example.test")
    dialog.url_edit.editingFinished.emit()
    if action == "reject":
        dialog.reject()
    elif action == "accept":
        dialog.accept()
    else:
        dialog.close()
    _flush(app)

    assert not dialog.isVisible()
    _finish_lookup(app, fake_pool)

    assert dialog.name_edit.text() == ""


def test_failure_produces_no_modal_dialog_and_leaves_name_unchanged(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher([OSError("network")]))

    dialog.url_edit.setText("example.test")
    dialog.url_edit.editingFinished.emit()
    _finish_lookup(app, fake_pool)

    assert dialog.name_edit.text() == ""
    assert QApplication.activeModalWidget() is None


def test_existing_ok_button_behavior_remains_correct(
    app: QApplication, fake_pool: _FakeThreadPool, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, _Fetcher(["Remote"]))

    assert not _ok_enabled(dialog)

    dialog.url_edit.setText("example.test")
    assert not _ok_enabled(dialog)

    _type_name(dialog, "Manual")
    assert _ok_enabled(dialog)

    dialog.name_edit.clear()
    assert not _ok_enabled(dialog)

    dialog = _dialog(tmp_path, _Fetcher(["Remote"]))
    dialog.url_edit.setText("example.test")
    dialog.url_edit.editingFinished.emit()
    _finish_lookup(app, fake_pool)

    assert _ok_enabled(dialog)
