from __future__ import annotations

import copy
import json

import pytest

pytest.importorskip("PySide6.QtWidgets")

import config_migration
import config_persistence
import config_schema
import tile_launcher
from tile_launcher import LauncherConfig, Main


_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
_MAIN_ID = "22222222-2222-4222-8222-222222222222"
_WORK_ID = "33333333-3333-4333-8333-333333333333"
_ARCHIVE_ID = "44444444-4444-4444-8444-444444444444"
_ROOT_EXTENSION = {
    config_schema.LEGACY_EXTENSION_NAMESPACE: {
        "retained": {"nested": ["value", 7, True]}
    }
}


def _runtime_config() -> LauncherConfig:
    identifiers = iter((_WORKSPACE_ID, _MAIN_ID))
    document = config_schema.build_native_v1(lambda: next(identifiers))
    application = document["application"]
    workspace = document["workspaces"][0]
    main_tab = document["tabs"][0]
    assert isinstance(application, dict)  # nosec B101
    assert isinstance(workspace, dict)  # nosec B101
    assert isinstance(main_tab, dict)  # nosec B101

    application["title"] = "Independent Application Title"
    workspace["name"] = "Custom Workspace"
    workspace["tab_order"] = [_MAIN_ID, _WORK_ID, _ARCHIVE_ID]
    document["tabs"] = [
        main_tab,
        {
            "id": _WORK_ID,
            "workspace_id": _WORKSPACE_ID,
            "name": "Work",
            "visibility": "visible",
            "extensions": {},
        },
        {
            "id": _ARCHIVE_ID,
            "workspace_id": _WORKSPACE_ID,
            "name": "Archive",
            "visibility": "hidden",
            "extensions": {},
        },
    ]
    document["tiles"] = [
        {
            "name": "Main A",
            "url": "https://main-a.example.test/",
            "tab_id": _MAIN_ID,
            "icon": None,
            "bg": "#F5F6FA",
            "browser": None,
            "chrome_profile": None,
            "open_target": "tab",
        },
        {
            "name": "Work X",
            "url": "https://work-x.example.test/",
            "tab_id": _WORK_ID,
            "icon": None,
            "bg": "#F5F6FA",
            "browser": None,
            "chrome_profile": None,
            "open_target": "tab",
        },
        {
            "name": "Main B",
            "url": "https://main-b.example.test/",
            "tab_id": _MAIN_ID,
            "icon": None,
            "bg": "#F5F6FA",
            "browser": None,
            "chrome_profile": None,
            "open_target": "tab",
        },
        {
            "name": "Archive Z",
            "url": "https://archive-z.example.test/",
            "tab_id": _ARCHIVE_ID,
            "icon": None,
            "bg": "#F5F6FA",
            "browser": None,
            "chrome_profile": None,
            "open_target": "tab",
        },
    ]
    document["extensions"] = copy.deepcopy(_ROOT_EXTENSION)
    assert config_schema.validate_v1(document)  # nosec B101
    return LauncherConfig.from_v1_mapping(document)


class _TabBar:
    def __init__(self, tab_ids: list[str]) -> None:
        self._tab_ids = tab_ids

    def count(self) -> int:
        return len(self._tab_ids)

    def tabData(self, index: int) -> str:
        return self._tab_ids[index]


class _TabsWidget:
    def __init__(self, tab_ids: list[str]) -> None:
        self._tab_bar = _TabBar(tab_ids)

    def tabBar(self) -> _TabBar:
        return self._tab_bar


class _AutoFitAction:
    def __init__(self, *, checked: bool) -> None:
        self.checked = checked
        self.signals_blocked = False
        self.block_calls: list[bool] = []
        self.set_checked_calls: list[tuple[bool, bool]] = []

    def blockSignals(self, blocked: bool) -> bool:
        previous = self.signals_blocked
        self.signals_blocked = blocked
        self.block_calls.append(blocked)
        return previous

    def setChecked(self, checked: bool) -> None:
        self.set_checked_calls.append((checked, self.signals_blocked))
        if not self.signals_blocked:
            raise AssertionError("Auto-fit rollback must not emit the toggled signal")
        self.checked = checked


class _RuntimeHarness:
    def __init__(self, cfg: LauncherConfig, *, current_tab: str = "Main") -> None:
        self.cfg = cfg
        self._current_tab = current_tab
        self._active_refresh = None
        self.tabs_widget = _TabsWidget([])
        self.rebuild_count = 0
        self.selected_tabs: list[str] = []
        self.populated_tabs: list[str] = []
        self._computed_columns = 17
        self.auto_fit_action = _AutoFitAction(checked=cfg.auto_fit)
        self.resize_calls: list[bool] = []

    def _selection_active(self) -> bool:
        return False

    def current_tab(self) -> str:
        return self._current_tab

    def _visible_tabs(self) -> list[str]:
        return [tab for tab in self.cfg.tabs if tab not in self.cfg.hidden_tabs]

    def rebuild(self) -> None:
        self.rebuild_count += 1

    def _set_current_tab_by_name(self, tab: str) -> None:
        if tab in self._visible_tabs():
            self._current_tab = tab
            self.selected_tabs.append(tab)

    def _populate_tab(self, tab: str) -> None:
        self.populated_tabs.append(tab)

    def resize_to_fit_tiles(self, *, snap_window: bool) -> None:
        self.resize_calls.append(snap_window)

    def set_visible_ids_after_move(self, tab_ids: list[str]) -> None:
        self.tabs_widget = _TabsWidget(tab_ids)


def _install_add_tile_dialog(monkeypatch, data: dict[str, object]) -> None:
    class TabCombo:
        def __init__(self) -> None:
            self.current_index: int | None = None

        def findText(self, text: str, _match_flag: object) -> int:
            return 1 if text == "Work" else -1

        def setCurrentIndex(self, index: int) -> None:
            self.current_index = index

    class AddTileDialog:
        def __init__(self, **_kwargs: object) -> None:
            self.data = data
            self.tab_combo = TabCombo()

        def exec(self):
            return tile_launcher.QDialog.DialogCode.Accepted

    monkeypatch.setattr(tile_launcher, "TileEditorDialog", AddTileDialog)
    monkeypatch.setattr(tile_launcher, "available_browsers", lambda: [])


@pytest.mark.unit
def test_hidden_tabs_load_and_save(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "title": "Launcher",
                "columns": 5,
                "tiles": [{"name": "t1", "url": "http://e", "tab": "Extra"}],
                "tabs": ["Main"],
                "hidden_tabs": ["Extra", "Unknown", 123, "Main"],
            }
        )
    )
    monkeypatch.setattr("tile_launcher.CFG_PATH", cfg_path)
    cfg = LauncherConfig.load()
    assert cfg.tabs == ["Main", "Extra"]
    assert cfg.hidden_tabs == ["Extra"]
    cfg.save()
    data = json.loads(cfg_path.read_text())
    assert data["schema_version"] == 1
    assert "hidden_tabs" not in data
    tabs = {tab["name"]: tab for tab in data["tabs"]}
    assert tabs["Main"]["visibility"] == "visible"
    assert tabs["Extra"]["visibility"] == "hidden"
    assert data["tiles"][0]["tab_id"] == tabs["Extra"]["id"]


@pytest.mark.unit
def test_current_v1_load_is_no_write_and_save_preserves_identity(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    identifiers = iter(
        (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        )
    )
    document = config_schema.build_native_v1(lambda: next(identifiers))
    workspace = document["workspaces"][0]
    assert isinstance(workspace, dict)
    workspace["name"] = "Custom Workspace"
    document["extensions"] = {
        config_schema.LEGACY_EXTENSION_NAMESPACE: {"retained": ["value"]}
    }
    original = json.dumps(
        document, ensure_ascii=False, separators=(", ", ": ")
    ).encode()
    cfg_path.write_bytes(original)
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)

    def forbid_write(*_args, **_kwargs):
        raise AssertionError("valid current v1 startup must not write")

    monkeypatch.setattr(tile_launcher, "atomic_write_bytes", forbid_write)
    cfg = LauncherConfig.load()

    assert cfg_path.read_bytes() == original
    assert cfg.workspace_id == "11111111-1111-4111-8111-111111111111"
    assert cfg.workspace_name == "Custom Workspace"
    assert cfg.tab_ids == {"Main": "22222222-2222-4222-8222-222222222222"}
    assert {tile.tab_id for tile in cfg.tiles} == {
        "22222222-2222-4222-8222-222222222222"
    }

    writes = []
    monkeypatch.setattr(
        tile_launcher,
        "atomic_write_bytes",
        lambda path, payload: writes.append((path, payload)),
    )
    cfg.save()
    assert len(writes) == 1
    saved = json.loads(writes[0][1])
    assert saved["application"]["default_workspace_id"] == cfg.workspace_id
    assert saved["workspaces"][0]["name"] == "Custom Workspace"
    assert saved["tabs"][0]["id"] == cfg.tab_ids["Main"]
    assert saved["extensions"] == document["extensions"]


@pytest.mark.unit
def test_missing_config_constructs_native_v1_and_restart_preserves_ids(
    tmp_path, monkeypatch
):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)

    created = LauncherConfig.load()
    original = cfg_path.read_bytes()
    document = json.loads(original)

    assert config_schema.validate_v1(document)
    assert created.title == "My Launcher"
    assert created.workspace_name == "Default Workspace"
    assert created.tabs == ["Main"]
    assert {tile.tab_id for tile in created.tiles} == {created.tab_ids["Main"]}
    created_ids = (created.workspace_id, dict(created.tab_ids))

    def forbid_write(*_args, **_kwargs):
        raise AssertionError("valid current v1 restart must not write")

    monkeypatch.setattr(tile_launcher, "atomic_write_bytes", forbid_write)
    monkeypatch.setattr(config_migration, "atomic_write_bytes", forbid_write)
    restarted = LauncherConfig.load()

    assert cfg_path.read_bytes() == original
    assert (restarted.workspace_id, restarted.tab_ids) == created_ids


@pytest.mark.unit
def test_q3_preserve_and_reset_installs_the_same_native_v1(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    original = b'{"malformed":'
    cfg_path.write_bytes(original)
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    monkeypatch.setattr(
        tile_launcher,
        "_prompt_config_recovery",
        lambda _category: tile_launcher._RecoveryChoice.PRESERVE_AND_RESET,
    )

    startup = tile_launcher._resolve_startup_configuration()

    assert isinstance(startup, tile_launcher._StartupReady)
    document = json.loads(cfg_path.read_bytes())
    assert config_schema.validate_v1(document)
    assert startup.config.to_v1_mapping() == document
    assert startup.config.title == "My Launcher"
    assert startup.config.workspace_name == "Default Workspace"
    recovery_files = list((tmp_path / "recovery").glob("config-*.recovery"))
    assert len(recovery_files) == 1
    assert recovery_files[0].read_bytes() == original


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure_mode", "expected_category"),
    (
        ("construction", "identity_allocation_failure"),
        ("validation", "validation_failure"),
        ("persistence", "persistence_failure"),
    ),
)
def test_missing_native_v1_failure_exits_safely_without_residue(
    tmp_path,
    monkeypatch,
    failure_mode,
    expected_category,
):
    cfg_path = tmp_path / "config.json"
    sensitive = r"C:\Users\private\native-config-secret.json"
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)

    if failure_mode == "construction":

        def fail_build(_allocator):
            raise config_schema.NativeV1ConstructionError

        monkeypatch.setattr(tile_launcher, "build_native_v1", fail_build)
    elif failure_mode == "validation":
        monkeypatch.setattr(
            tile_launcher,
            "build_native_v1",
            lambda _allocator: {"schema_version": 1},
        )
    else:

        def fail_write(_path, _payload):
            raise OSError(sensitive)

        monkeypatch.setattr(tile_launcher, "atomic_write_bytes", fail_write)

    breadcrumbs = []
    shown_errors = []
    monkeypatch.setattr(
        tile_launcher,
        "record_breadcrumb",
        lambda event, **fields: breadcrumbs.append((event, fields)),
    )
    monkeypatch.setattr(
        tile_launcher,
        "_show_migration_failure",
        shown_errors.append,
    )

    def forbid_recovery_prompt(_category):
        raise AssertionError("missing native state must not enter Q3 recovery")

    monkeypatch.setattr(
        tile_launcher, "_prompt_config_recovery", forbid_recovery_prompt
    )

    startup = tile_launcher._resolve_startup_configuration()

    assert isinstance(startup, tile_launcher._StartupExit)  # nosec B101
    assert startup.exit_code == 1  # nosec B101
    assert len(shown_errors) == 1  # nosec B101
    error = shown_errors[0]
    assert (
        error.notice_category is config_migration.StartupNoticeCategory.MIGRATION_FAILED
    )
    assert error.diagnostics == {  # nosec B101
        "failure_count": 1,
        "failure_kind": "native_configuration",
        "failure_category": expected_category,
    }
    assert error.__cause__ is None  # nosec B101
    assert error.__context__ is None  # nosec B101
    assert breadcrumbs == [("config_migration_exit", error.diagnostics)]  # nosec B101
    assert sensitive not in repr((shown_errors, breadcrumbs))  # nosec B101
    assert not cfg_path.exists()  # nosec B101
    assert list(tmp_path.rglob("*.recovery")) == []  # nosec B101
    assert list(tmp_path.rglob("*.failed-candidate")) == []  # nosec B101
    assert list(tmp_path.rglob("*.tmp")) == []  # nosec B101


@pytest.mark.unit
@pytest.mark.parametrize("failure_mode", ("construction", "serialization"))
def test_q3_native_reset_candidate_failure_is_controlled_and_non_mutating(
    tmp_path,
    monkeypatch,
    failure_mode,
):
    cfg_path = tmp_path / "config.json"
    original = b'{"malformed":'
    sensitive = r"C:\Users\private\reset-secret.json"
    cfg_path.write_bytes(original)
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    monkeypatch.setattr(
        tile_launcher,
        "_prompt_config_recovery",
        lambda _category: tile_launcher._RecoveryChoice.PRESERVE_AND_RESET,
    )

    if failure_mode == "construction":

        def fail_build(_allocator):
            raise config_schema.NativeV1ConstructionError

        monkeypatch.setattr(tile_launcher, "build_native_v1", fail_build)
    else:

        def fail_serialization(_config):
            raise ValueError(sensitive)

        monkeypatch.setattr(LauncherConfig, "_serialized_payload", fail_serialization)

    def forbid_preservation(*_args, **_kwargs):
        raise AssertionError("invalid reset candidates must not reach preservation")

    monkeypatch.setattr(tile_launcher, "preserve_and_reset", forbid_preservation)
    breadcrumbs = []
    shown_categories = []
    monkeypatch.setattr(
        tile_launcher,
        "record_breadcrumb",
        lambda event, **fields: breadcrumbs.append((event, fields)),
    )
    monkeypatch.setattr(
        tile_launcher,
        "_show_recovery_failure",
        shown_categories.append,
    )

    startup = tile_launcher._resolve_startup_configuration()

    assert isinstance(startup, tile_launcher._StartupExit)  # nosec B101
    assert startup.exit_code == 1  # nosec B101
    assert shown_categories == [  # nosec B101
        tile_launcher.RecoveryFailureCategory.RESET_FAILURE
    ]
    failure_events = [
        fields for event, fields in breadcrumbs if event == "config_recovery_failed"
    ]
    assert failure_events == [  # nosec B101
        {
            "recovery_copy_count": 0,
            "reset_count": 0,
            "failure_category": "reset_failure",
        }
    ]
    assert cfg_path.read_bytes() == original  # nosec B101
    assert sensitive not in repr((breadcrumbs, shown_categories))  # nosec B101
    assert list(tmp_path.rglob("*.recovery")) == []  # nosec B101
    assert list(tmp_path.rglob("*.failed-candidate")) == []  # nosec B101
    assert list(tmp_path.rglob("*.tmp")) == []  # nosec B101


@pytest.mark.unit
def test_runtime_tab_and_tile_actions_preserve_identity_and_strict_v1(
    tmp_path,
    monkeypatch,
):
    cfg_path = tmp_path / "config.json"
    cfg = _runtime_config()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    cfg.save()
    harness = _RuntimeHarness(cfg)

    real_atomic_write = tile_launcher.atomic_write_bytes
    writes = []

    def capture_write(path, payload):
        document = json.loads(payload)
        assert config_schema.validate_v1(document)  # nosec B101
        writes.append(document)
        real_atomic_write(path, payload)

    monkeypatch.setattr(tile_launcher, "atomic_write_bytes", capture_write)

    def assert_committed(expected_write_count):
        assert len(writes) == expected_write_count  # nosec B101
        runtime_document = harness.cfg.to_v1_mapping()
        persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert config_schema.validate_v1(runtime_document)  # nosec B101
        assert persisted == runtime_document == writes[-1]  # nosec B101
        application = persisted["application"]
        workspace = persisted["workspaces"][0]
        assert application["title"] == "Independent Application Title"  # nosec B101
        assert application["default_workspace_id"] == _WORKSPACE_ID  # nosec B101
        assert application["extensions"] == {}  # nosec B101
        assert workspace["id"] == _WORKSPACE_ID  # nosec B101
        assert workspace["name"] == "Custom Workspace"  # nosec B101
        assert workspace["tab_order"] == harness.cfg.tab_order  # nosec B101
        assert harness.cfg.tab_order == [  # nosec B101
            harness.cfg.tab_ids[name] for name in harness.cfg.tabs
        ]
        assert persisted["extensions"] == _ROOT_EXTENSION  # nosec B101
        assert harness.cfg.workspace_extensions == {}  # nosec B101
        assert all(value == {} for value in harness.cfg.tab_extensions.values())
        title_by_id = {tab_id: name for name, tab_id in harness.cfg.tab_ids.items()}
        assert all(  # nosec B101
            tile.tab_id in title_by_id and tile.tab == title_by_id[tile.tab_id]
            for tile in harness.cfg.tiles
        )
        assert harness.cfg.tab_ids["Main"] == _MAIN_ID  # nosec B101
        assert harness.cfg.tab_ids["Archive"] == _ARCHIVE_ID  # nosec B101
        if "Work" in harness.cfg.tab_ids:
            assert harness.cfg.tab_ids["Work"] == _WORK_ID  # nosec B101
        if "Focus" in harness.cfg.tab_ids:
            assert harness.cfg.tab_ids["Focus"] == _WORK_ID  # nosec B101

    tab_names = iter((("Later", True), ("Focus", True)))
    monkeypatch.setattr(
        tile_launcher.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: next(tab_names),
    )

    Main.add_tab(harness)
    assert_committed(1)
    later_id = harness.cfg.tab_ids["Later"]
    assert later_id not in {_WORKSPACE_ID, _MAIN_ID, _WORK_ID, _ARCHIVE_ID}

    harness._current_tab = "Work"
    Main.rename_tab(harness)
    assert_committed(2)
    assert "Work" not in harness.cfg.tab_ids  # nosec B101
    assert harness.cfg.tab_ids["Focus"] == _WORK_ID  # nosec B101
    assert (
        next(tile for tile in harness.cfg.tiles if tile.name == "Work X").tab == "Focus"
    )

    harness._current_tab = "Main"
    harness.set_visible_ids_after_move([_WORK_ID, _MAIN_ID, later_id])
    Main._on_tab_moved(harness, 0, 1)
    assert_committed(3)
    assert harness.cfg.tabs == ["Focus", "Main", "Archive", "Later"]  # nosec B101

    harness._current_tab = "Focus"
    Main.toggle_current_tab_visibility(harness)
    assert_committed(4)
    assert harness.cfg.hidden_tabs == ["Archive", "Focus"]  # nosec B101
    Main.toggle_current_tab_visibility(harness)
    assert_committed(5)
    assert harness.cfg.hidden_tabs == ["Archive"]  # nosec B101

    class VisibilityDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return tile_launcher.QDialog.DialogCode.Accepted

        def result_hidden(self):
            return ["Archive", "Later"]

    monkeypatch.setattr(tile_launcher, "TabVisibilityDialog", VisibilityDialog)
    Main.manage_tab_visibility(harness)
    assert_committed(6)
    assert harness.cfg.hidden_tabs == ["Archive", "Later"]  # nosec B101

    harness._current_tab = "Later"
    Main.toggle_current_tab_visibility(harness)
    assert_committed(7)
    assert harness.cfg.hidden_tabs == ["Archive"]  # nosec B101

    class EditDialog:
        data = {
            "name": "Main A edited",
            "url": "https://edited.example.test/",
            "tab": "Focus",
            "icon": None,
            "browser": "firefox",
            "chrome_profile": None,
            "open_target": "window",
        }

        def __init__(self, **_kwargs):
            pass

        def exec(self):
            return tile_launcher.QDialog.DialogCode.Accepted

    monkeypatch.setattr(tile_launcher, "TileEditorDialog", EditDialog)
    monkeypatch.setattr(tile_launcher, "available_browsers", lambda: [])
    target = next(tile for tile in harness.cfg.tiles if tile.name == "Main A")
    harness._current_tab = "Main"
    Main.edit_tile(harness, target)
    assert_committed(8)
    assert target.name == "Main A edited"  # nosec B101
    assert target.tab == "Focus" and target.tab_id == _WORK_ID  # nosec B101

    Main.change_tile_tab(harness, target, "Main")
    assert_committed(9)
    assert target.tab == "Main" and target.tab_id == _MAIN_ID  # nosec B101

    harness._current_tab = "Main"
    Main.move_tile(harness, "Main", 0, 1)
    assert_committed(10)
    assert [tile.name for tile in harness.cfg.tiles] == [  # nosec B101
        "Work X",
        "Main B",
        "Main A edited",
        "Archive Z",
    ]
    assert [  # nosec B101
        tile.name for tile in harness.cfg.tiles if tile.tab_id == _MAIN_ID
    ] == ["Main B", "Main A edited"]
    assert harness.populated_tabs == ["Main"]  # nosec B101

    main_b = next(tile for tile in harness.cfg.tiles if tile.name == "Main B")
    Main.duplicate_tile(harness, main_b)
    assert_committed(11)
    duplicates = [tile for tile in harness.cfg.tiles if tile.name == "Main B"]
    assert len(duplicates) == 2 and duplicates[0] is not duplicates[1]  # nosec B101
    assert duplicates[0].tab_id == duplicates[1].tab_id == _MAIN_ID  # nosec B101

    monkeypatch.setattr(
        tile_launcher.QMessageBox,
        "warning",
        lambda *_args, **_kwargs: tile_launcher.QMessageBox.StandardButton.Yes,
    )
    Main.remove_tile(harness, duplicates[1])
    assert_committed(12)
    assert [tile.name for tile in harness.cfg.tiles].count("Main B") == 1  # nosec B101

    harness._current_tab = "Later"
    monkeypatch.setattr(
        tile_launcher.QMessageBox,
        "question",
        lambda *_args, **_kwargs: tile_launcher.QMessageBox.StandardButton.Yes,
    )
    Main.delete_tab(harness)
    assert_committed(13)
    assert "Later" not in harness.cfg.tabs  # nosec B101
    assert later_id not in harness.cfg.tab_order  # nosec B101
    assert all(tile.tab_id != later_id for tile in harness.cfg.tiles)  # nosec B101


@pytest.mark.unit
def test_auto_fit_toggle_persists_one_complete_strict_v1_document(
    tmp_path,
    monkeypatch,
):
    cfg_path = tmp_path / "config.json"
    cfg = _runtime_config()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    cfg.save()
    baseline = copy.deepcopy(cfg.to_v1_mapping())
    harness = _RuntimeHarness(cfg, current_tab="Work")
    harness._computed_columns = 23
    harness.auto_fit_action.checked = False
    live_tiles = tuple(harness.cfg.tiles)
    writes = []
    real_atomic_write = tile_launcher.atomic_write_bytes

    def capture_write(path, payload):
        document = json.loads(payload)
        assert config_schema.validate_v1(document)  # nosec B101
        writes.append(document)
        real_atomic_write(path, payload)

    monkeypatch.setattr(tile_launcher, "atomic_write_bytes", capture_write)

    Main._toggle_auto_fit(harness, False)

    assert harness.cfg is cfg  # nosec B101
    assert all(  # nosec B101
        current is original
        for current, original in zip(harness.cfg.tiles, live_tiles, strict=True)
    )
    assert len(writes) == 1  # nosec B101
    persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert persisted == writes[0] == harness.cfg.to_v1_mapping()  # nosec B101
    assert config_schema.validate_v1(persisted)  # nosec B101
    expected = copy.deepcopy(baseline)
    expected["auto_fit"] = False
    assert persisted == expected  # nosec B101
    assert harness.cfg.auto_fit is False  # nosec B101
    assert harness._computed_columns == harness.cfg.columns  # nosec B101
    assert harness.auto_fit_action.checked is False  # nosec B101
    assert harness.auto_fit_action.block_calls == []  # nosec B101
    assert harness.auto_fit_action.set_checked_calls == []  # nosec B101
    assert harness.current_tab() == "Work"  # nosec B101
    assert harness.rebuild_count == 1  # nosec B101
    assert harness.resize_calls == [False]  # nosec B101
    assert [path.name for path in tmp_path.iterdir()] == ["config.json"]  # nosec B101


@pytest.mark.unit
def test_auto_fit_size_failure_rolls_back_live_and_action_state(
    tmp_path,
    monkeypatch,
):
    cfg_path = tmp_path / "private-config.json"
    cfg = _runtime_config()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    cfg.save()
    baseline_bytes = cfg_path.read_bytes()
    baseline_mapping = copy.deepcopy(cfg.to_v1_mapping())
    harness = _RuntimeHarness(cfg, current_tab="Work")
    harness._computed_columns = 23
    harness.auto_fit_action.checked = False
    live_tiles = tuple(harness.cfg.tiles)
    breadcrumbs = []
    messages = []
    monkeypatch.setattr(
        tile_launcher,
        "record_breadcrumb",
        lambda event, **fields: breadcrumbs.append((event, fields)),
    )
    monkeypatch.setattr(
        tile_launcher.QMessageBox,
        "critical",
        lambda parent, title, text: messages.append((parent, title, text)),
    )
    monkeypatch.setattr(tile_launcher, "MAX_CONFIG_BYTES", len(baseline_bytes))

    def forbid_atomic_write(*_args, **_kwargs):
        raise AssertionError("the oversized document must be rejected before writing")

    monkeypatch.setattr(tile_launcher, "atomic_write_bytes", forbid_atomic_write)

    Main._toggle_auto_fit(harness, False)

    assert harness.cfg is cfg  # nosec B101
    assert all(  # nosec B101
        current is original
        for current, original in zip(harness.cfg.tiles, live_tiles, strict=True)
    )
    assert harness.cfg.to_v1_mapping() == baseline_mapping  # nosec B101
    assert cfg_path.read_bytes() == baseline_bytes  # nosec B101
    assert harness.cfg.auto_fit is True  # nosec B101
    assert harness._computed_columns == 23  # nosec B101
    assert harness.auto_fit_action.checked is True  # nosec B101
    assert harness.auto_fit_action.block_calls == [True, False]  # nosec B101
    assert harness.auto_fit_action.set_checked_calls == [(True, True)]  # nosec B101
    assert harness.current_tab() == "Work"  # nosec B101
    assert harness.selected_tabs == ["Work"]  # nosec B101
    assert harness.rebuild_count == 1  # nosec B101
    assert harness.resize_calls == []  # nosec B101
    assert breadcrumbs == [  # nosec B101
        (
            "config_change_save_failed",
            {
                "failure_count": 1,
                "failure_category": "size_limit_exceeded",
                "operation": "auto_fit_toggle",
            },
        )
    ]
    assert messages == [  # nosec B101
        (
            None,
            "DesktopTileLauncher",
            "The change could not be saved. No changes were applied.",
        )
    ]
    assert "Work X" not in repr((breadcrumbs, messages))  # nosec B101
    assert "https://work-x.example.test/" not in repr(  # nosec B101
        (breadcrumbs, messages)
    )
    assert str(cfg_path) not in repr((breadcrumbs, messages))  # nosec B101
    assert list(tmp_path.rglob("*.tmp")) == []  # nosec B101
    assert [path.name for path in tmp_path.iterdir()] == [  # nosec B101
        "private-config.json"
    ]


@pytest.mark.unit
def test_add_tile_with_strict_v1_preserves_identity_and_extension_state(
    tmp_path,
    monkeypatch,
):
    cfg_path = tmp_path / "config.json"
    cfg = _runtime_config()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    cfg.save()
    baseline = copy.deepcopy(cfg.to_v1_mapping())
    baseline_tab_ids = dict(cfg.tab_ids)
    baseline_tab_order = list(cfg.tab_order)
    live_tiles = tuple(cfg.tiles)
    harness = _RuntimeHarness(cfg, current_tab="Work")
    writes = []
    breadcrumbs = []
    real_atomic_write = tile_launcher.atomic_write_bytes
    _install_add_tile_dialog(
        monkeypatch,
        {
            "name": "Work Y",
            "url": "https://work-y.example.test/",
            "tab": "Work",
            "icon": None,
            "browser": None,
            "chrome_profile": None,
            "open_target": "tab",
        },
    )

    def capture_write(path, payload):
        document = json.loads(payload)
        assert config_schema.validate_v1(document)  # nosec B101
        writes.append(document)
        real_atomic_write(path, payload)

    monkeypatch.setattr(tile_launcher, "atomic_write_bytes", capture_write)
    monkeypatch.setattr(
        tile_launcher,
        "record_breadcrumb",
        lambda event, **fields: breadcrumbs.append((event, fields)),
    )

    Main.add_tile(harness)

    assert harness.cfg is cfg  # nosec B101
    assert all(  # nosec B101
        current is original
        for current, original in zip(harness.cfg.tiles[:-1], live_tiles, strict=True)
    )
    assert cfg.workspace_id == _WORKSPACE_ID  # nosec B101
    assert cfg.tab_ids == baseline_tab_ids  # nosec B101
    assert cfg.tab_order == baseline_tab_order  # nosec B101
    assert cfg.tiles[-1].name == "Work Y"  # nosec B101
    assert cfg.tiles[-1].tab == "Work"  # nosec B101
    assert cfg.tiles[-1].tab_id == _WORK_ID  # nosec B101
    assert [tile.name for tile in cfg.tiles] == [  # nosec B101
        "Main A",
        "Work X",
        "Main B",
        "Archive Z",
        "Work Y",
    ]
    assert [tile.name for tile in cfg.tiles if tile.tab_id == _WORK_ID] == [  # nosec B101
        "Work X",
        "Work Y",
    ]
    assert len(writes) == 1  # nosec B101
    persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert persisted == writes[0] == cfg.to_v1_mapping()  # nosec B101
    assert config_schema.validate_v1(persisted)  # nosec B101
    assert {key: value for key, value in persisted.items() if key != "tiles"} == {
        key: value for key, value in baseline.items() if key != "tiles"
    }  # nosec B101
    assert persisted["tiles"][:-1] == baseline["tiles"]  # nosec B101
    assert persisted["tiles"][-1]["tab_id"] == _WORK_ID  # nosec B101
    assert harness.current_tab() == "Work"  # nosec B101
    assert harness.selected_tabs == ["Work"]  # nosec B101
    assert harness.rebuild_count == 1  # nosec B101
    assert breadcrumbs == [("tile_add", {})]  # nosec B101
    assert [path.name for path in tmp_path.iterdir()] == ["config.json"]  # nosec B101


@pytest.mark.unit
def test_add_tile_persistence_failure_restores_strict_v1_live_identity(
    tmp_path,
    monkeypatch,
):
    cfg_path = tmp_path / "private-config.json"
    cfg = _runtime_config()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    cfg.save()
    baseline_bytes = cfg_path.read_bytes()
    baseline_mapping = copy.deepcopy(cfg.to_v1_mapping())
    live_tiles = tuple(cfg.tiles)
    harness = _RuntimeHarness(cfg, current_tab="Work")
    sensitive_name = "Private work tile"
    sensitive_url = "https://private-work.example.test/account"
    sensitive_error = "C:\\private\\DesktopTileLauncher\\config.json"
    attempted_documents = []
    breadcrumbs = []
    messages = []
    real_atomic_write = tile_launcher.atomic_write_bytes
    _install_add_tile_dialog(
        monkeypatch,
        {
            "name": sensitive_name,
            "url": sensitive_url,
            "tab": "Work",
            "icon": None,
            "browser": None,
            "chrome_profile": None,
            "open_target": "tab",
        },
    )

    def capture_candidate(path, payload):
        document = json.loads(payload)
        assert config_schema.validate_v1(document)  # nosec B101
        attempted_documents.append(document)
        real_atomic_write(path, payload)

    def reject_replace(_source, _destination):
        raise OSError(sensitive_error)

    monkeypatch.setattr(tile_launcher, "atomic_write_bytes", capture_candidate)
    monkeypatch.setattr(config_persistence.os, "replace", reject_replace)
    monkeypatch.setattr(
        tile_launcher,
        "record_breadcrumb",
        lambda event, **fields: breadcrumbs.append((event, fields)),
    )
    monkeypatch.setattr(
        tile_launcher.QMessageBox,
        "critical",
        lambda parent, title, text: messages.append((parent, title, text)),
    )

    Main.add_tile(harness)

    assert len(attempted_documents) == 1  # nosec B101
    assert attempted_documents[0]["tiles"][-1]["tab_id"] == _WORK_ID  # nosec B101
    assert harness.cfg is cfg  # nosec B101
    assert len(harness.cfg.tiles) == len(live_tiles)  # nosec B101
    assert all(  # nosec B101
        current is original
        for current, original in zip(harness.cfg.tiles, live_tiles, strict=True)
    )
    assert harness.cfg.to_v1_mapping() == baseline_mapping  # nosec B101
    assert cfg_path.read_bytes() == baseline_bytes  # nosec B101
    assert harness.current_tab() == "Work"  # nosec B101
    assert harness.selected_tabs == ["Work"]  # nosec B101
    assert harness.rebuild_count == 1  # nosec B101
    assert breadcrumbs == [  # nosec B101
        (
            "config_change_save_failed",
            {
                "failure_count": 1,
                "failure_category": "persistence_failure",
                "operation": "tile_add",
            },
        )
    ]
    assert messages == [  # nosec B101
        (
            None,
            "DesktopTileLauncher",
            "The change could not be saved. No changes were applied.",
        )
    ]
    assert sensitive_name not in repr((breadcrumbs, messages))  # nosec B101
    assert sensitive_url not in repr((breadcrumbs, messages))  # nosec B101
    assert sensitive_error not in repr((breadcrumbs, messages))  # nosec B101
    assert str(cfg_path) not in repr((breadcrumbs, messages))  # nosec B101
    assert list(tmp_path.rglob("*.tmp")) == []  # nosec B101
    assert [path.name for path in tmp_path.iterdir()] == [  # nosec B101
        "private-config.json"
    ]


@pytest.mark.unit
def test_close_geometry_save_failures_restore_state_and_leave_no_residue(
    tmp_path,
    monkeypatch,
):
    cfg_path = tmp_path / "private-config.json"
    cfg = _runtime_config()
    cfg.window_x = 1
    cfg.window_y = 2
    cfg.window_w = 640
    cfg.window_h = 480
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    cfg.save()
    baseline_bytes = cfg_path.read_bytes()
    baseline_mapping = copy.deepcopy(cfg.to_v1_mapping())
    live_tiles = tuple(cfg.tiles)
    breadcrumbs = []
    monkeypatch.setattr(
        tile_launcher,
        "record_breadcrumb",
        lambda event, **fields: breadcrumbs.append((event, fields)),
    )

    with monkeypatch.context() as size_limit_patch:
        size_limit_patch.setattr(
            tile_launcher,
            "MAX_CONFIG_BYTES",
            len(baseline_bytes),
        )
        saved = tile_launcher._persist_close_geometry(
            cfg,
            x=1_000_000,
            y=2_000_000,
            width=3_000_000,
            height=4_000_000,
        )

    assert saved is False  # nosec B101
    assert cfg.to_v1_mapping() == baseline_mapping  # nosec B101
    assert cfg_path.read_bytes() == baseline_bytes  # nosec B101
    assert tuple(cfg.tiles) == live_tiles  # nosec B101
    assert all(  # nosec B101
        current is original
        for current, original in zip(cfg.tiles, live_tiles, strict=True)
    )

    sensitive_error = "C:\\private\\DesktopTileLauncher\\close-config.json"

    def reject_replace(_source, _destination):
        raise OSError(sensitive_error)

    with monkeypatch.context() as persistence_patch:
        persistence_patch.setattr(config_persistence.os, "replace", reject_replace)
        saved = tile_launcher._persist_close_geometry(
            cfg,
            x=10,
            y=20,
            width=800,
            height=600,
        )

    assert saved is False  # nosec B101
    assert cfg.to_v1_mapping() == baseline_mapping  # nosec B101
    assert cfg_path.read_bytes() == baseline_bytes  # nosec B101
    assert all(  # nosec B101
        current is original
        for current, original in zip(cfg.tiles, live_tiles, strict=True)
    )
    assert breadcrumbs == [  # nosec B101
        (
            "geometry_save_failed",
            {
                "failure_count": 1,
                "failure_category": "size_limit_exceeded",
            },
        ),
        (
            "geometry_save_failed",
            {
                "failure_count": 1,
                "failure_category": "persistence_failure",
            },
        ),
    ]
    assert sensitive_error not in repr(breadcrumbs)  # nosec B101
    assert str(cfg_path) not in repr(breadcrumbs)  # nosec B101
    assert list(tmp_path.rglob("*.tmp")) == []  # nosec B101
    assert [path.name for path in tmp_path.iterdir()] == [  # nosec B101
        "private-config.json"
    ]


@pytest.mark.unit
def test_runtime_size_and_validation_failures_roll_back_live_identity_graph(
    tmp_path,
    monkeypatch,
):
    cfg_path = tmp_path / "config.json"
    cfg = _runtime_config()
    monkeypatch.setattr(tile_launcher, "CFG_PATH", cfg_path)
    cfg.save()
    baseline_bytes = cfg_path.read_bytes()
    baseline_mapping = copy.deepcopy(cfg.to_v1_mapping())
    harness = _RuntimeHarness(cfg)
    live_config = harness.cfg
    live_main_tile = harness.cfg.tiles[0]
    breadcrumbs = []
    messages = []
    monkeypatch.setattr(
        tile_launcher,
        "record_breadcrumb",
        lambda event, **fields: breadcrumbs.append((event, fields)),
    )
    monkeypatch.setattr(
        tile_launcher.QMessageBox,
        "critical",
        lambda parent, title, text: messages.append((parent, title, text)),
    )

    monkeypatch.setattr(tile_launcher, "MAX_CONFIG_BYTES", len(baseline_bytes))
    Main.duplicate_tile(harness, harness.cfg.tiles[0])

    assert harness.cfg is live_config  # nosec B101
    assert harness.cfg.tiles[0] is live_main_tile  # nosec B101
    assert harness.cfg.to_v1_mapping() == baseline_mapping  # nosec B101
    assert cfg_path.read_bytes() == baseline_bytes  # nosec B101
    assert breadcrumbs[-1] == (  # nosec B101
        "config_change_save_failed",
        {
            "failure_count": 1,
            "failure_category": "size_limit_exceeded",
            "operation": "tile_duplicate",
        },
    )
    assert list(tmp_path.rglob("*.tmp")) == []  # nosec B101

    sensitive_name = "Private renamed workspace tab"
    harness._current_tab = "Work"
    live_work_tile = next(tile for tile in harness.cfg.tiles if tile.tab == "Work")
    monkeypatch.setattr(
        tile_launcher.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: (sensitive_name, True),
    )

    def reject_save(_config):
        raise ValueError("invalid_schema_v1_runtime_state")

    monkeypatch.setattr(LauncherConfig, "save", reject_save)
    Main.rename_tab(harness)

    assert harness.cfg is live_config  # nosec B101
    assert any(tile is live_work_tile for tile in harness.cfg.tiles)  # nosec B101
    assert live_work_tile.tab == "Work"  # nosec B101
    assert live_work_tile.tab_id == _WORK_ID  # nosec B101
    assert harness.cfg.to_v1_mapping() == baseline_mapping  # nosec B101
    assert cfg_path.read_bytes() == baseline_bytes  # nosec B101
    assert breadcrumbs[-1] == (  # nosec B101
        "config_change_save_failed",
        {
            "failure_count": 1,
            "failure_category": "validation_failure",
            "operation": "tab_rename",
        },
    )
    assert len(messages) == 2  # nosec B101
    assert sensitive_name not in repr((breadcrumbs, messages))  # nosec B101


@pytest.mark.unit
def test_runtime_utf8_encoding_failure_is_fixed_and_context_free(
    monkeypatch,
) -> None:
    cfg = _runtime_config()
    monkeypatch.setattr(LauncherConfig, "serialize", lambda _config: "\ud800")

    with pytest.raises(ValueError) as exc_info:
        cfg._serialized_payload()

    assert exc_info.value.args == ("invalid_schema_v1_runtime_state",)  # nosec B101
    assert exc_info.value.__cause__ is None  # nosec B101
    assert exc_info.value.__context__ is None  # nosec B101
