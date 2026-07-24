# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
import os
from dataclasses import dataclass

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

import tile_launcher  # noqa: E402
from tile_launcher import LauncherConfig, Main, Tile  # noqa: E402


pytestmark = pytest.mark.unit

_SENSITIVE_URL = "https://secret.example.test/private?token=do-not-log"
_SENSITIVE_NAME = "Confidential customer portal"
_SENSITIVE_PATH = r"C:\Users\private\Downloads\customer-urls.txt"


@dataclass(frozen=True)
class _Selection:
    source_line: int
    name: str
    url: str


class _Dialog:
    def __init__(
        self,
        *,
        destination: str,
        selections: tuple[_Selection, ...] = (),
        results: tuple[object, ...] = (tile_launcher.QDialog.DialogCode.Accepted,),
    ) -> None:
        self._destination = destination
        self._selections = selections
        self._results = list(results)
        self.constructor_args: tuple[object, ...] = ()
        self.constructor_kwargs: dict[str, object] = {}
        self.exec_count = 0
        self.selected_imports_count = 0

    def construct(self, *args: object, **kwargs: object) -> "_Dialog":
        self.constructor_args = args
        self.constructor_kwargs = kwargs
        return self

    def exec(self) -> object:
        self.exec_count += 1
        return self._results.pop(0)

    def selected_imports(self) -> tuple[_Selection, ...]:
        self.selected_imports_count += 1
        return self._selections

    def selected_destination(self) -> str:
        return self._destination


class _MainHarness:
    def __init__(self, cfg: LauncherConfig, *, current_tab: str = "Main") -> None:
        self.cfg = cfg
        self._current_tab = current_tab
        self.rebuild_count = 0
        self.selected_tabs: list[str] = []

    def current_tab(self) -> str:
        return self._current_tab

    def rebuild(self) -> None:
        self.rebuild_count += 1

    def _set_current_tab_by_name(self, tab: str) -> None:
        self.selected_tabs.append(tab)


def _config(*, hidden_tabs: list[str] | None = None) -> LauncherConfig:
    return LauncherConfig(
        title="Test Launcher",
        columns=4,
        tiles=[
            Tile(
                name="Existing",
                url="https://existing.example.test/",
                tab="Main",
                icon="existing.png",
                bg="#123456",
                browser="firefox",
                chrome_profile="Profile 1",
                open_target="window",
            )
        ],
        tabs=["Main", "Work", "Hidden"],
        hidden_tabs=list(hidden_tabs or []),
        tab_ids={"Main": "main-id", "Work": "work-id", "Hidden": "hidden-id"},
        tab_order=["main-id", "work-id", "hidden-id"],
        auto_fit=False,
        window_x=10,
        window_y=20,
        window_w=800,
        window_h=600,
        workspace_name="Custom Workspace",
        extensions={
            "io.github.108thecitizen.legacy": {"retained": {"source": "legacy"}}
        },
    )


def _install_dialog(
    monkeypatch: pytest.MonkeyPatch,
    dialog: _Dialog,
) -> None:
    monkeypatch.setattr(tile_launcher, "UrlImportDialog", dialog.construct)


def _capture_breadcrumbs(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, object]]]:
    captured: list[tuple[str, dict[str, object]]] = []

    def fake_record(event: str, **fields: object) -> None:
        captured.append((event, fields))

    monkeypatch.setattr(tile_launcher, "record_breadcrumb", fake_record)
    return captured


def test_import_saves_detached_candidate_once_then_commits_in_source_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _config()
    harness = _MainHarness(original, current_tab="Work")
    dialog = _Dialog(
        destination="Work",
        # Deliberately return review selections out of order. The integration layer
        # owns the source-order guarantee even if a dialog implementation changes.
        selections=(
            _Selection(8, "Second", "https://second.example.test/"),
            _Selection(3, "First", "https://first.example.test/"),
        ),
    )
    _install_dialog(monkeypatch, dialog)

    saved: list[LauncherConfig] = []

    def fake_save(candidate: LauncherConfig) -> None:
        # Saving is the commit boundary: the live object must still be untouched.
        assert harness.cfg is original
        assert original.tiles == [original.tiles[0]]
        assert len(original.tiles) == 1
        assert candidate is not original
        assert candidate.tiles is not original.tiles
        assert candidate.tiles[0] is not original.tiles[0]
        assert candidate.tabs is not original.tabs
        assert candidate.hidden_tabs is not original.hidden_tabs
        assert candidate.tab_ids is not original.tab_ids
        assert candidate.tab_order is not original.tab_order
        assert candidate.workspace_id == original.workspace_id
        assert candidate.workspace_name == "Custom Workspace"
        assert candidate.tab_extensions is not original.tab_extensions
        assert candidate.extensions is not original.extensions
        assert candidate.extensions == original.extensions
        saved.append(candidate)

    monkeypatch.setattr(LauncherConfig, "save", fake_save)

    Main.import_urls(harness)

    assert dialog.exec_count == 1
    assert dialog.selected_imports_count == 1
    assert dialog.constructor_kwargs["default_destination"] == "Work"
    destinations = dialog.constructor_kwargs["destinations"]
    assert [
        (destination.name, destination.urls, destination.hidden)
        for destination in destinations
    ] == [
        ("Main", ("https://existing.example.test/",), False),
        ("Work", (), False),
        ("Hidden", (), False),
    ]
    assert len(saved) == 1
    assert harness.cfg is saved[0]
    assert harness.rebuild_count == 1
    assert harness.selected_tabs == ["Work"]

    assert len(original.tiles) == 1
    imported = harness.cfg.tiles[1:]
    assert [(tile.name, tile.url) for tile in imported] == [
        ("First", "https://first.example.test/"),
        ("Second", "https://second.example.test/"),
    ]
    assert [tile.tab for tile in imported] == ["Work", "Work"]
    assert [tile.tab_id for tile in imported] == [
        harness.cfg.tab_ids["Work"],
        harness.cfg.tab_ids["Work"],
    ]
    for tile in imported:
        assert tile.icon is None
        assert tile.bg == "#F5F6FA"
        assert tile.browser is None
        assert tile.chrome_profile is None
        assert tile.open_target == "tab"


def test_import_into_hidden_tab_never_unhides_or_selects_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _config(hidden_tabs=["Hidden"])
    harness = _MainHarness(original)
    dialog = _Dialog(
        destination="Hidden",
        selections=(_Selection(1, "Hidden tile", "https://hidden.example.test/"),),
    )
    _install_dialog(monkeypatch, dialog)

    saved: list[LauncherConfig] = []
    monkeypatch.setattr(
        LauncherConfig, "save", lambda candidate: saved.append(candidate)
    )

    Main.import_urls(harness)

    assert len(saved) == 1
    assert harness.cfg is saved[0]
    destinations = dialog.constructor_kwargs["destinations"]
    hidden_destination = next(
        destination for destination in destinations if destination.name == "Hidden"
    )
    assert hidden_destination.hidden is True
    assert harness.cfg.hidden_tabs == ["Hidden"]
    assert original.hidden_tabs == ["Hidden"]
    assert harness.cfg.tiles[-1].tab == "Hidden"
    assert harness.rebuild_count == 1
    assert "Hidden" not in harness.selected_tabs


def test_refresh_detached_candidate_preserves_identity_and_extension_state() -> None:
    original = _config(hidden_tabs=["Hidden"])
    harness = _MainHarness(original)

    candidate, detached_by_identity = Main._detached_configuration(harness)

    assert candidate is not original
    assert candidate.workspace_id == original.workspace_id
    assert candidate.workspace_name == original.workspace_name
    assert candidate.tab_ids == original.tab_ids
    assert candidate.tab_order == original.tab_order
    assert candidate.hidden_tabs == original.hidden_tabs
    assert candidate.tab_extensions == original.tab_extensions
    assert candidate.extensions == original.extensions
    assert candidate.tab_extensions is not original.tab_extensions
    assert candidate.extensions is not original.extensions
    assert candidate.tiles[0].tab_id == original.tiles[0].tab_id
    assert detached_by_identity[id(original.tiles[0])] is candidate.tiles[0]


@pytest.mark.parametrize(
    ("failure", "expected_category"),
    (
        (OSError(_SENSITIVE_PATH), "persistence_failure"),
        (ValueError("invalid_schema_v1_runtime_state"), "validation_failure"),
        (ValueError("schema_v1_size_limit_exceeded"), "size_limit_exceeded"),
    ),
)
def test_save_failure_keeps_live_config_and_review_state_without_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_category: str,
) -> None:
    original = _config()
    original_snapshot = copy.deepcopy(original)
    harness = _MainHarness(original)
    dialog = _Dialog(
        destination="Work",
        selections=(_Selection(1, _SENSITIVE_NAME, _SENSITIVE_URL),),
        # The same dialog is shown again after the error, retaining its review.
        results=(
            tile_launcher.QDialog.DialogCode.Accepted,
            tile_launcher.QDialog.DialogCode.Rejected,
        ),
    )
    _install_dialog(monkeypatch, dialog)
    captured = _capture_breadcrumbs(monkeypatch)

    save_attempts: list[LauncherConfig] = []

    def fail_save(candidate: LauncherConfig) -> None:
        save_attempts.append(candidate)
        raise failure

    monkeypatch.setattr(LauncherConfig, "save", fail_save)
    errors: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        tile_launcher.QMessageBox,
        "critical",
        lambda parent, title, text: errors.append((parent, title, text)),
    )

    Main.import_urls(harness)

    assert len(save_attempts) == 1
    assert save_attempts[0] is not original
    assert harness.cfg is original
    assert harness.cfg == original_snapshot
    assert harness.rebuild_count == 0
    assert harness.selected_tabs == []
    assert dialog.exec_count == 2
    assert dialog.selected_imports_count == 1
    assert len(errors) == 1
    assert "No tiles were imported" in errors[0][2]
    assert _SENSITIVE_URL not in repr(errors)
    assert _SENSITIVE_NAME not in repr(errors)
    assert _SENSITIVE_PATH not in repr(errors)
    assert _SENSITIVE_URL not in repr(captured)
    assert _SENSITIVE_NAME not in repr(captured)
    assert _SENSITIVE_PATH not in repr(captured)
    assert captured == [
        (
            "url_import_save_failed",
            {"imported_count": 1, "failure_category": expected_category},
        )
    ]


def test_actual_over_limit_import_keeps_live_review_and_file_exact(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_path = tmp_path / "config.json"
    original = _config()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    original.save()
    baseline_bytes = cfg_path.read_bytes()
    original_snapshot = copy.deepcopy(original)
    harness = _MainHarness(original)
    dialog = _Dialog(
        destination="Work",
        selections=(_Selection(1, _SENSITIVE_NAME, _SENSITIVE_URL),),
        results=(
            tile_launcher.QDialog.DialogCode.Accepted,
            tile_launcher.QDialog.DialogCode.Rejected,
        ),
    )
    _install_dialog(monkeypatch, dialog)
    captured = _capture_breadcrumbs(monkeypatch)
    errors: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        tile_launcher.QMessageBox,
        "critical",
        lambda parent, title, text: errors.append((parent, title, text)),
    )
    monkeypatch.setattr(tile_launcher, "MAX_CONFIG_BYTES", len(baseline_bytes))

    Main.import_urls(harness)

    assert harness.cfg is original
    assert harness.cfg == original_snapshot
    assert cfg_path.read_bytes() == baseline_bytes
    assert harness.rebuild_count == 0
    assert harness.selected_tabs == []
    assert dialog.exec_count == 2
    assert dialog.selected_imports_count == 1
    assert captured == [
        (
            "url_import_save_failed",
            {"imported_count": 1, "failure_category": "size_limit_exceeded"},
        )
    ]
    assert len(errors) == 1
    assert "No tiles were imported" in errors[0][2]
    assert _SENSITIVE_URL not in repr((captured, errors))
    assert _SENSITIVE_NAME not in repr((captured, errors))
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_cancel_makes_no_config_or_ui_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _config()
    original_snapshot = copy.deepcopy(original)
    harness = _MainHarness(original)
    dialog = _Dialog(
        destination="Work",
        selections=(_Selection(1, "Ignored", "https://ignored.example.test/"),),
        results=(tile_launcher.QDialog.DialogCode.Rejected,),
    )
    _install_dialog(monkeypatch, dialog)

    saved: list[LauncherConfig] = []
    monkeypatch.setattr(
        LauncherConfig, "save", lambda candidate: saved.append(candidate)
    )

    Main.import_urls(harness, default_tab="Work")

    assert saved == []
    assert harness.cfg is original
    assert harness.cfg == original_snapshot
    assert harness.rebuild_count == 0
    assert harness.selected_tabs == []
    assert dialog.selected_imports_count == 0
    assert dialog.constructor_kwargs["default_destination"] == "Work"


def test_import_diagnostics_are_aggregate_and_exclude_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _config()
    harness = _MainHarness(original)
    dialog = _Dialog(
        destination="Work",
        selections=(_Selection(1, _SENSITIVE_NAME, _SENSITIVE_URL),),
    )
    _install_dialog(monkeypatch, dialog)
    monkeypatch.setattr(LauncherConfig, "save", lambda _candidate: None)
    captured = _capture_breadcrumbs(monkeypatch)

    Main.import_urls(harness)

    assert captured
    assert _SENSITIVE_URL not in repr(captured)
    assert _SENSITIVE_NAME not in repr(captured)
    assert _SENSITIVE_PATH not in repr(captured)
    for _event, fields in captured:
        assert {"url", "name", "input", "path", "file_path"}.isdisjoint(fields)
        assert all(
            isinstance(value, (bool, int, type(None))) for value in fields.values()
        )
