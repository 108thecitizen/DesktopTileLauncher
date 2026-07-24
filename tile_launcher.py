# tile_launcher.py
# Minimal desktop launcher: tile grid that opens URLs in the default browser.
# Windows/Mac/Linux.  Requires: Python 3.10+  pip install PySide6
# encoding changed
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess  # nosec B404: used to launch local apps; inputs validated & shell=False
import sys
import threading
import urllib.parse
import webbrowser
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeAlias, cast
from uuid import UUID, uuid4

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
    qWarning,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QContextMenuEvent,
    QFont,
    QIcon,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QStyle,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import debug_scaffold
from config_migration import (
    ConfigurationMigrationError,
    ImplicitLegacyLoaded,
    LegacyNormalizationSaveFailed,
    MigrationCommitted,
    PRODUCTION_REGISTRY,
    StartupFailureRoute,
    StartupNoticeCategory,
    VersionedCurrent,
    guarded_legacy_normalization_save,
    load_startup_configuration,
    startup_failure_route,
    startup_notice_message,
)
from config_persistence import atomic_write_bytes
from config_recovery import (
    MAX_CONFIG_BYTES,
    ConfigLoadFailureCategory,
    ConfigMissing,
    ConfigRecoveryRequired,
    ConfigurationLoadError,
    RawConfigLoaded,
    RecoveryFailed,
    RecoveryFailureCategory,
    preserve_and_reset,
    recovery_exit_diagnostics,
    recovery_required_diagnostics,
    recovery_result_diagnostics,
    validate_legacy_mapping,
)
from config_schema import (
    DEFAULT_WORKSPACE_NAME,
    JsonObject,
    JsonValue,
    NativeV1ConstructionError,
    Uuid4Allocator,
    build_native_v1,
    validate_v1,
)
from debug_scaffold import (
    record_breadcrumb,
    sanitize_launch_command,
    sanitize_log_extra,
    sanitize_url,
)
from tile_metadata_refresh import (
    OpaqueToken,
    OperationGuard,
    RefreshResult,
    TileSnapshot,
    create_batch_staging_directory,
    fetch_favicon as fetch_favicon_to_directory,
    guess_domain as metadata_guess_domain,
    merge_refresh_result,
    run_metadata_refresh,
    select_all_for_active_tab,
    snapshot_matches,
    summarize_refresh_results,
)
from tile_editor_dialog import TileEditorDialog
from url_import_dialog import ImportDestination, UrlImportDialog
from tab_order import (
    TabOrderState,
    add_tab as add_tab_to_order,
    delete_tab as delete_tab_from_order,
    move_visible_tab,
    normalize_tab_order,
    rename_tab as rename_tab_in_order,
)
from browser_chrome_win import (
    is_windows_default_browser_chrome,
    is_chrome_path,
    launch_chrome_with_profile,
)

APP_NAME = "TileLauncher"


def app_dirs():
    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    cfg = base / APP_NAME
    icons = cfg / "icons"
    cfg.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    return cfg, icons


CFG_DIR, ICON_DIR = app_dirs()
CFG_PATH = CFG_DIR / "config.json"


def _find_browser(paths: Iterable[Path | str]) -> str | None:
    """Return first existing executable from a list of candidate paths."""
    for entry in paths:
        if isinstance(entry, Path):
            if entry.exists():
                return str(entry)
        else:
            found = shutil.which(entry)
            if found:
                return found
    return None


def available_browsers() -> list[str]:
    """
    Return a list of locally available browser names.

    Robust to environments where webbrowser._tryorder is None and to
    cross‑platform path quirks. Always returns a list (possibly empty).
    """
    _raw = getattr(webbrowser, "_tryorder", None)
    try_order: Iterable[str] = _raw if isinstance(_raw, (list, tuple, set)) else []
    seen: set[str] = set()
    browsers: list[str] = []

    # Include any working controllers that stdlib already knows about.
    for name in try_order:
        try:
            webbrowser.get(name)
        except webbrowser.Error:
            continue
        if name not in seen:
            browsers.append(name)
            seen.add(name)

    candidates: dict[str, list[Path | str]] = {
        "brave": [
            "brave",
            "brave-browser",
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            Path("C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe"),
            Path(
                "C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe"
            ),
        ],
        "firefox": [
            "firefox",
            Path("/Applications/Firefox.app/Contents/MacOS/firefox"),
            Path("C:/Program Files/Mozilla Firefox/firefox.exe"),
            Path("C:/Program Files (x86)/Mozilla Firefox/firefox.exe"),
        ],
        "chrome": [
            "chrome",
            "google-chrome",
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        ],
        "edge": [
            "msedge",
            "microsoft-edge",
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ],
        "safari": [
            Path("/Applications/Safari.app/Contents/MacOS/Safari"),
        ],
    }

    for name, paths in candidates.items():
        if name in seen:
            continue
        ok = True
        try:
            webbrowser.get(name)
        except webbrowser.Error:
            ok = False

        if not ok:
            exe = _find_browser(paths)
            if exe:
                webbrowser.register(name, None, webbrowser.BackgroundBrowser(exe))
                ok = True

        # On macOS, Safari is a standard system browser; ensure it's present.
        if not ok and sys.platform == "darwin" and name == "safari":
            safari_exe = "/Applications/Safari.app/Contents/MacOS/Safari"
            webbrowser.register(
                "safari", None, webbrowser.BackgroundBrowser(safari_exe)
            )
            ok = True

        if ok and name not in seen:
            browsers.append(name)
            seen.add(name)

    return sorted(browsers)


def _resolve_controller_exe(name: str | None) -> str | None:
    """
    Resolve a browser controller to an executable name without calling webbrowser.get
    for common controllers. Falls back to the provided name.
    """
    if not name:
        return None

    # Common controllers across platforms
    mapping = {
        "firefox": "firefox",
        "chrome": "chrome",
        "google-chrome": "google-chrome",
        "chromium": "chromium",
        "edge": "msedge",
        "msedge": "msedge",
        "brave": "brave",
        # Safari is special on mac; you probably build a different command for it elsewhere.
        "safari": "safari",
    }

    exe = mapping.get(name.lower())
    if exe:
        return exe

    # Last resort: assume it's on PATH. Do not call webbrowser.get here.
    return name


def _normalize_url(raw: str) -> str:
    """Ensure the URL has a scheme; if missing, prepend https://."""
    s = (raw or "").strip()
    if not s:
        return ""
    parsed = urllib.parse.urlparse(s)
    return s if parsed.scheme else f"https://{s}"


@dataclass
class Tile:
    name: str
    url: str
    tab: str = "Main"
    icon: Optional[str] = None  # path to png/ico
    bg: str = "#F5F6FA"  # background color (CSS)
    browser: Optional[str] = None  # webbrowser name
    chrome_profile: Optional[str] = None  # e.g. "Default", "Profile 1"
    open_target: Literal["tab", "window"] = "tab"
    tab_id: str | None = None


NativeConfigurationFailureCategory: TypeAlias = Literal[
    "identity_allocation_failure",
    "validation_failure",
    "persistence_failure",
]


def _native_configuration_error(
    category: NativeConfigurationFailureCategory,
) -> ConfigurationMigrationError:
    return ConfigurationMigrationError(
        StartupNoticeCategory.MIGRATION_FAILED,
        {
            "failure_count": 1,
            "failure_kind": "native_configuration",
            "failure_category": category,
        },
    )


RuntimeChangeOperation: TypeAlias = Literal[
    "auto_fit_toggle",
    "tab_reorder",
    "tile_reorder",
    "tile_add",
    "tile_edit",
    "tile_duplicate",
    "tile_remove",
    "tile_move",
    "tab_add",
    "tab_rename",
    "tab_delete",
    "tab_visibility_toggle",
    "tab_visibility_manage",
]

RuntimeSaveFailureCategory: TypeAlias = Literal[
    "validation_failure",
    "size_limit_exceeded",
    "persistence_failure",
]


def _runtime_save_failure_category(
    error: OSError | ValueError,
) -> RuntimeSaveFailureCategory:
    if isinstance(error, OSError):
        return "persistence_failure"
    if error.args == ("schema_v1_size_limit_exceeded",):
        return "size_limit_exceeded"
    return "validation_failure"


@dataclass
class LauncherConfig:
    title: str = "Launcher"
    columns: int = 5
    tiles: list["Tile"] = field(default_factory=list)
    tabs: list[str] = field(default_factory=lambda: ["Main"])
    hidden_tabs: list[str] = field(default_factory=list)
    tab_ids: dict[str, str] = field(default_factory=dict)
    tab_order: list[str] = field(default_factory=list)
    auto_fit: bool = True
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    window_w: Optional[int] = None
    window_h: Optional[int] = None
    workspace_id: str = ""
    workspace_name: str = DEFAULT_WORKSPACE_NAME
    application_extensions: JsonObject = field(default_factory=dict)
    workspace_extensions: JsonObject = field(default_factory=dict)
    tab_extensions: dict[str, JsonObject] = field(default_factory=dict)
    extensions: JsonObject = field(default_factory=dict)
    _identity_ready: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self._identity_ready:
            enforce_tab_invariants(self)

    @classmethod
    def from_legacy_mapping(cls, data: dict[str, object]) -> "LauncherConfig":
        """Construct the current unversioned model with its existing behavior."""
        legacy = cast(dict[str, Any], data)
        tiles = [Tile(**tile) for tile in legacy.get("tiles", [])]
        raw_tabs = legacy.get("tabs") or []
        tabs: list[str] = []
        for tab in raw_tabs:
            if isinstance(tab, str) and tab not in tabs:
                tabs.append(tab)
        for tile in tiles:
            if tile.tab not in tabs:
                tabs.append(tile.tab)
        hidden_raw = legacy.get("hidden_tabs") or []
        hidden_tabs = [tab for tab in hidden_raw if isinstance(tab, str)]
        cfg = cls(
            title=legacy.get("title", "Launcher"),
            columns=legacy.get("columns", 5),
            tiles=tiles,
            tabs=tabs,
            hidden_tabs=hidden_tabs,
            auto_fit=legacy.get("auto_fit", True),
            window_x=legacy.get("window_x"),
            window_y=legacy.get("window_y"),
            window_w=legacy.get("window_w"),
            window_h=legacy.get("window_h"),
            _identity_ready=True,
        )
        enforce_tab_invariants(
            cfg,
            raw_tab_ids=legacy.get("tab_ids"),
            raw_tab_order=legacy.get("tab_order"),
        )
        return cfg

    @classmethod
    def from_v1_mapping(
        cls,
        data: Mapping[str, JsonValue],
    ) -> "LauncherConfig":
        """Construct validated v1 state without legacy repair or ID allocation."""

        if not validate_v1(data):
            raise ValueError("invalid_schema_v1_runtime_state")
        document = cast(dict[str, Any], deepcopy(dict(data)))
        application = cast(dict[str, Any], document["application"])
        workspace = cast(list[dict[str, Any]], document["workspaces"])[0]
        raw_tabs = cast(list[dict[str, Any]], document["tabs"])
        tabs_by_id = {cast(str, tab["id"]): tab for tab in raw_tabs}
        tab_order = cast(list[str], workspace["tab_order"])
        tabs = [cast(str, tabs_by_id[tab_id]["name"]) for tab_id in tab_order]
        tab_ids = {tabs_by_id[tab_id]["name"]: tab_id for tab_id in tab_order}
        hidden_tabs = [
            cast(str, tabs_by_id[tab_id]["name"])
            for tab_id in tab_order
            if tabs_by_id[tab_id]["visibility"] == "hidden"
        ]
        title_by_id = {cast(str, tab["id"]): cast(str, tab["name"]) for tab in raw_tabs}
        tiles = [
            Tile(
                name=cast(str, raw_tile["name"]),
                url=cast(str, raw_tile["url"]),
                tab=title_by_id[cast(str, raw_tile["tab_id"])],
                icon=cast(str | None, raw_tile["icon"]),
                bg=cast(str, raw_tile["bg"]),
                browser=cast(str | None, raw_tile["browser"]),
                chrome_profile=cast(str | None, raw_tile["chrome_profile"]),
                open_target=cast(Literal["tab", "window"], raw_tile["open_target"]),
                tab_id=cast(str, raw_tile["tab_id"]),
            )
            for raw_tile in cast(list[dict[str, Any]], document["tiles"])
        ]
        return cls(
            title=cast(str, application["title"]),
            columns=cast(int, document["columns"]),
            tiles=tiles,
            tabs=tabs,
            hidden_tabs=hidden_tabs,
            tab_ids=cast(dict[str, str], tab_ids),
            tab_order=list(tab_order),
            auto_fit=cast(bool, document["auto_fit"]),
            window_x=cast(int | None, document["window_x"]),
            window_y=cast(int | None, document["window_y"]),
            window_w=cast(int | None, document["window_w"]),
            window_h=cast(int | None, document["window_h"]),
            workspace_id=cast(str, workspace["id"]),
            workspace_name=cast(str, workspace["name"]),
            application_extensions=cast(JsonObject, application["extensions"]),
            workspace_extensions=cast(JsonObject, workspace["extensions"]),
            tab_extensions={
                tab_id: cast(JsonObject, tabs_by_id[tab_id]["extensions"])
                for tab_id in tab_order
            },
            extensions=cast(JsonObject, document["extensions"]),
            _identity_ready=True,
        )

    @classmethod
    def first_run(
        cls,
        id_factory: Uuid4Allocator = uuid4,
    ) -> "LauncherConfig":
        """Return one validated native-v1 friendly configuration."""

        return cls.from_v1_mapping(build_native_v1(id_factory))

    @staticmethod
    def load(
        *,
        on_existing_legacy: (
            Callable[["LauncherConfig", RawConfigLoaded], None] | None
        ) = None,
    ) -> "LauncherConfig":
        result = load_startup_configuration(
            CFG_PATH,
            _construct_legacy_configuration,
            PRODUCTION_REGISTRY,
            legacy_validator=validate_legacy_mapping,
        )
        if isinstance(result, ConfigMissing):
            cfg: LauncherConfig | None = None
            failure_category: NativeConfigurationFailureCategory | None = None
            try:
                cfg = LauncherConfig.first_run()
                cfg.save()
            except NativeV1ConstructionError:
                failure_category = "identity_allocation_failure"
            except ValueError:
                failure_category = "validation_failure"
            except OSError:
                failure_category = "persistence_failure"
            if failure_category is not None:
                raise _native_configuration_error(failure_category)
            if cfg is None:
                raise _native_configuration_error("validation_failure")
            return cfg
        if isinstance(result, ConfigRecoveryRequired):
            if startup_failure_route(result) is StartupFailureRoute.Q3_RECOVERY:
                raise ConfigurationLoadError(result.category, result.snapshot)
            raise ConfigurationMigrationError.unexpected_success()
        if isinstance(result, ImplicitLegacyLoaded):
            cfg = result.value
            if on_existing_legacy is not None:
                on_existing_legacy(cfg, result.raw)
            return cfg
        if isinstance(result, (VersionedCurrent, MigrationCommitted)):
            return LauncherConfig.from_v1_mapping(result.document)
        if startup_failure_route(result) is StartupFailureRoute.EXIT_ONLY:
            raise ConfigurationMigrationError.from_outcome(result)
        raise ConfigurationMigrationError.unexpected_success()

    def serialize(self) -> str:
        """Serialize one complete, strictly validated schema-version 1 document."""

        document = self.to_v1_mapping()
        serialized: str | None = None
        try:
            serialized = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        except (TypeError, ValueError, OverflowError, UnicodeError):
            serialized = None
        if serialized is None:
            raise ValueError("invalid_schema_v1_runtime_state")
        return serialized

    def to_v1_mapping(self) -> JsonObject:
        """Return current state as v1 without repairing or regenerating identity."""

        tabs: list[JsonValue] = []
        tiles: list[JsonValue] = []
        invalid_state = False
        try:
            if (
                not self.tabs
                or len(set(self.tabs)) != len(self.tabs)
                or set(self.tab_ids) != set(self.tabs)
                or len(self.tab_order) != len(self.tabs)
                or set(self.tab_order) != set(self.tab_ids.values())
                or len(set(self.tab_order)) != len(self.tab_order)
                or not set(self.hidden_tabs).issubset(self.tabs)
                or len(set(self.hidden_tabs)) != len(self.hidden_tabs)
                or len(self.hidden_tabs) == len(self.tabs)
                or set(self.tab_extensions) != set(self.tab_order)
            ):
                raise ValueError
            title_by_id = {tab_id: title for title, tab_id in self.tab_ids.items()}
            ordered_titles = [title_by_id[tab_id] for tab_id in self.tab_order]
            if ordered_titles != self.tabs:
                raise ValueError

            tabs = [
                {
                    "id": tab_id,
                    "workspace_id": self.workspace_id,
                    "name": title_by_id[tab_id],
                    "visibility": (
                        "hidden"
                        if title_by_id[tab_id] in self.hidden_tabs
                        else "visible"
                    ),
                    "extensions": deepcopy(self.tab_extensions[tab_id]),
                }
                for tab_id in self.tab_order
            ]
            for tile in self.tiles:
                if tile.tab_id is None or title_by_id.get(tile.tab_id) != tile.tab:
                    raise ValueError
                tiles.append(
                    {
                        "name": tile.name,
                        "url": tile.url,
                        "tab_id": tile.tab_id,
                        "icon": tile.icon,
                        "bg": tile.bg,
                        "browser": tile.browser,
                        "chrome_profile": tile.chrome_profile,
                        "open_target": tile.open_target,
                    }
                )
        except (KeyError, RecursionError, TypeError, ValueError):
            invalid_state = True
        if invalid_state:
            raise ValueError("invalid_schema_v1_runtime_state")

        document: JsonObject | None = None
        try:
            document = {
                "schema_version": 1,
                "application": {
                    "title": self.title,
                    "default_workspace_id": self.workspace_id,
                    "extensions": deepcopy(self.application_extensions),
                },
                "workspaces": [
                    {
                        "id": self.workspace_id,
                        "name": self.workspace_name,
                        "tab_order": list(self.tab_order),
                        "extensions": deepcopy(self.workspace_extensions),
                    }
                ],
                "tabs": tabs,
                "tiles": tiles,
                "columns": self.columns,
                "auto_fit": self.auto_fit,
                "window_x": self.window_x,
                "window_y": self.window_y,
                "window_w": self.window_w,
                "window_h": self.window_h,
                "extensions": deepcopy(self.extensions),
            }
        except (RecursionError, TypeError, ValueError):
            document = None
        if document is None or not validate_v1(document):
            raise ValueError("invalid_schema_v1_runtime_state")
        return document

    def save(self) -> None:
        payload = self._serialized_payload()
        atomic_write_bytes(CFG_PATH, payload)

    def _serialized_payload(self) -> bytes:
        payload: bytes | None = None
        try:
            payload = self.serialize().encode("utf-8")
        except UnicodeError:
            payload = None
        if payload is None:
            raise ValueError("invalid_schema_v1_runtime_state")
        if len(payload) > MAX_CONFIG_BYTES:
            raise ValueError("schema_v1_size_limit_exceeded")
        return payload


@dataclass(frozen=True, slots=True)
class _RuntimeChangeSnapshot:
    live: object = field(repr=False)
    state: object = field(repr=False)
    tiles: tuple[object, ...] = field(repr=False)


def _runtime_change_snapshot(config: LauncherConfig) -> _RuntimeChangeSnapshot:
    return _RuntimeChangeSnapshot(
        live=config,
        state=deepcopy(config),
        tiles=tuple(config.tiles),
    )


def _restore_tile(tile: Tile, saved: Tile) -> None:
    tile.name = saved.name
    tile.url = saved.url
    tile.tab = saved.tab
    tile.icon = saved.icon
    tile.bg = saved.bg
    tile.browser = saved.browser
    tile.chrome_profile = saved.chrome_profile
    tile.open_target = saved.open_target
    tile.tab_id = saved.tab_id


def _restore_runtime_change(snapshot: _RuntimeChangeSnapshot) -> LauncherConfig:
    live = snapshot.live
    state = snapshot.state
    if not isinstance(live, LauncherConfig) or not isinstance(state, LauncherConfig):
        return cast(LauncherConfig, state)

    original_tiles = cast(tuple[Tile, ...], snapshot.tiles)
    for tile, saved in zip(original_tiles, state.tiles, strict=True):
        _restore_tile(tile, saved)
    live.title = state.title
    live.columns = state.columns
    live.tiles = list(original_tiles)
    live.tabs = state.tabs
    live.hidden_tabs = state.hidden_tabs
    live.tab_ids = state.tab_ids
    live.tab_order = state.tab_order
    live.auto_fit = state.auto_fit
    live.window_x = state.window_x
    live.window_y = state.window_y
    live.window_w = state.window_w
    live.window_h = state.window_h
    live.workspace_id = state.workspace_id
    live.workspace_name = state.workspace_name
    live.application_extensions = state.application_extensions
    live.workspace_extensions = state.workspace_extensions
    live.tab_extensions = state.tab_extensions
    live.extensions = state.extensions
    live._identity_ready = state._identity_ready
    return live


def _persist_close_geometry(
    config: LauncherConfig,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
) -> bool:
    previous_geometry = (
        config.window_x,
        config.window_y,
        config.window_w,
        config.window_h,
    )
    config.window_x = x
    config.window_y = y
    config.window_w = width
    config.window_h = height
    try:
        config.save()
    except (OSError, ValueError) as error:
        (
            config.window_x,
            config.window_y,
            config.window_w,
            config.window_h,
        ) = previous_geometry
        record_breadcrumb(
            "geometry_save_failed",
            failure_count=1,
            failure_category=_runtime_save_failure_category(error),
        )
        return False

    record_breadcrumb(
        "geometry_saved",
        w=config.window_w,
        h=config.window_h,
        x=config.window_x,
        y=config.window_y,
    )
    return True


def _construct_legacy_configuration(data: dict[str, object]) -> LauncherConfig:
    validate_legacy_mapping(data)
    return LauncherConfig.from_legacy_mapping(data)


def _guarded_existing_legacy_save(
    config: LauncherConfig,
    loaded: RawConfigLoaded,
) -> None:
    result = guarded_legacy_normalization_save(
        CFG_PATH,
        loaded,
        config.serialize(),
    )
    if isinstance(result, LegacyNormalizationSaveFailed):
        raise ConfigurationMigrationError.from_outcome(result)


def _tab_order_state(cfg: LauncherConfig) -> TabOrderState:
    return TabOrderState(
        tabs=list(cfg.tabs),
        tab_ids=dict(cfg.tab_ids),
        tab_order=list(cfg.tab_order),
    )


def _apply_tab_order_state(cfg: LauncherConfig, state: TabOrderState) -> None:
    previous_extensions = cfg.tab_extensions
    cfg.tabs = state.tabs
    cfg.tab_ids = state.tab_ids
    cfg.tab_order = state.tab_order
    cfg.tab_extensions = {
        tab_id: deepcopy(previous_extensions.get(tab_id, {}))
        for tab_id in state.tab_order
    }


def _canonicalized_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _reserved_tab_identity_hints(
    raw_tab_ids: object,
    raw_tab_order: object,
) -> set[str]:
    reserved: set[str] = set()
    if isinstance(raw_tab_ids, dict):
        values: Iterable[object] = raw_tab_ids.values()
        reserved.update(
            candidate
            for value in values
            if (candidate := _canonicalized_uuid(value)) is not None
        )
    if isinstance(raw_tab_order, list):
        reserved.update(
            candidate
            for value in raw_tab_order
            if (candidate := _canonicalized_uuid(value)) is not None
        )
    return reserved


def _ensure_workspace_identity(
    cfg: LauncherConfig,
    raw_tab_ids: object,
    raw_tab_order: object,
) -> None:
    reserved = _reserved_tab_identity_hints(raw_tab_ids, raw_tab_order)
    workspace_id = _canonicalized_uuid(cfg.workspace_id)
    if workspace_id is not None and workspace_id not in reserved:
        cfg.workspace_id = workspace_id
        return
    while True:
        workspace_id = str(uuid4())
        if workspace_id not in reserved:
            cfg.workspace_id = workspace_id
            return


def enforce_tab_invariants(
    cfg: LauncherConfig,
    *,
    raw_tab_ids: object | None = None,
    raw_tab_order: object | None = None,
) -> None:
    """Ensure tab-related invariants for a configuration.

    - ``cfg.tabs`` is a de-duplicated, non-empty list of strings.
    - Each tab has one stable ID in a canonical full-tab order.
    - ``cfg.hidden_tabs`` is a subset of ``cfg.tabs`` and does not hide all tabs.
    - Every tile's ``tab`` exists in ``cfg.tabs``; invalid entries are remapped to
      the first tab.
    """

    clean_tabs: list[str] = []
    for t in cfg.tabs:
        if isinstance(t, str) and t not in clean_tabs:
            clean_tabs.append(t)
    if not clean_tabs:
        clean_tabs = ["Main"]

    saved_tab_ids = cfg.tab_ids if raw_tab_ids is None else raw_tab_ids
    saved_tab_order = cfg.tab_order if raw_tab_order is None else raw_tab_order
    _ensure_workspace_identity(cfg, saved_tab_ids, saved_tab_order)
    state = normalize_tab_order(
        clean_tabs,
        saved_tab_ids,
        saved_tab_order,
        blocked_ids=(cfg.workspace_id,),
    )
    _apply_tab_order_state(cfg, state)

    first_tab = cfg.tabs[0]
    valid_tabs = set(cfg.tabs)
    title_by_id = {tab_id: title for title, tab_id in cfg.tab_ids.items()}
    for tile in cfg.tiles:
        canonical_tile_id = _canonicalized_uuid(tile.tab_id)
        if canonical_tile_id in title_by_id:
            tile.tab_id = canonical_tile_id
            tile.tab = title_by_id[canonical_tile_id]
        elif tile.tab in valid_tabs:
            tile.tab_id = cfg.tab_ids[tile.tab]
        else:
            tile.tab = first_tab
            tile.tab_id = cfg.tab_ids[first_tab]

    clean_hidden: list[str] = []
    for t in cfg.hidden_tabs:
        if t in valid_tabs and t not in clean_hidden:
            clean_hidden.append(t)
    cfg.hidden_tabs = clean_hidden
    if len(cfg.hidden_tabs) >= len(cfg.tabs):
        cfg.hidden_tabs = [t for t in cfg.hidden_tabs if t != first_tab]
    cfg._identity_ready = True


def _config_with_imported_tiles(
    cfg: LauncherConfig, imported_tiles: Iterable[Tile]
) -> LauncherConfig:
    """Return a detached configuration with imported tiles appended in order."""
    return replace(
        cfg,
        tiles=[replace(tile) for tile in cfg.tiles] + list(imported_tiles),
        tabs=list(cfg.tabs),
        hidden_tabs=list(cfg.hidden_tabs),
        tab_ids=dict(cfg.tab_ids),
        tab_order=list(cfg.tab_order),
        application_extensions=deepcopy(cfg.application_extensions),
        workspace_extensions=deepcopy(cfg.workspace_extensions),
        tab_extensions=deepcopy(cfg.tab_extensions),
        extensions=deepcopy(cfg.extensions),
    )


def _tab_id_for_runtime_name(config: object, tab_name: str) -> str | None:
    """Resolve identity while preserving lightweight direct-construction seams."""

    tab_ids = getattr(config, "tab_ids", None)
    if not isinstance(tab_ids, dict):
        return None
    tab_id = tab_ids.get(tab_name)
    return tab_id if isinstance(tab_id, str) else None


@dataclass
class FitResult:
    columns: int
    rows_visible: int
    need_vscroll: bool
    window_w: int
    window_h: int


def compute_grid_fit(
    avail_w: int,
    avail_h: int,
    tile_w: int,
    tile_h: int,
    spacing: int,
    margins_lr: int,
    margins_tb: int,
    frame_w: int,
    frame_h: int,
    qstyle_scrollbar_extent: Optional[int],
    total_tiles_on_tab: int,
    columns_hint: Optional[int],
) -> FitResult:
    """Compute a snap-to-grid fit using only full tiles.

    Width/height refer to the *outer* window size including the frame.
    The algorithm snaps to full tiles, optionally respecting a hint for the
    number of columns when ``columns_hint`` is provided.
    """

    scrollbar_extent = qstyle_scrollbar_extent or 16

    unit_w = tile_w + spacing
    unit_h = tile_h + spacing

    usable_w = avail_w - frame_w - margins_lr
    usable_h = avail_h - frame_h - margins_tb

    max_cols = max(1, (usable_w + spacing) // unit_w)
    if columns_hint is not None:
        columns = max(1, min(columns_hint, max_cols))
    else:
        columns = max_cols

    rows_fit = max(1, (usable_h + spacing) // unit_h)
    rows_required = (total_tiles_on_tab + columns - 1) // columns
    need_vscroll = rows_required > rows_fit

    if need_vscroll:
        usable_w -= scrollbar_extent
        max_cols = max(1, (usable_w + spacing) // unit_w)
        if columns_hint is not None:
            columns = max(1, min(columns_hint, max_cols))
        else:
            columns = max_cols
        rows_fit = max(1, (usable_h + spacing) // unit_h)
        rows_required = (total_tiles_on_tab + columns - 1) // columns
        need_vscroll = rows_required > rows_fit

    rows_visible = min(rows_fit, rows_required)

    width = columns * tile_w + max(0, columns - 1) * spacing + margins_lr + frame_w
    height = (
        rows_visible * tile_h
        + max(0, rows_visible - 1) * spacing
        + margins_tb
        + frame_h
    )

    width = min(width, avail_w)
    height = min(height, avail_h)

    return FitResult(columns, rows_visible, need_vscroll, int(width), int(height))


# ---------------------------------------------------------------------------
# Fit policy shim used by unit tests.
# Pure functions — no Qt or side effects.
# ---------------------------------------------------------------------------


class FitPolicy(Enum):
    """When is snap‑to‑fit allowed automatically?"""

    ALWAYS = auto()
    ON_STARTUP = auto()
    OFF = auto()


class FitTrigger(Enum):
    """What caused us to consider fitting?"""

    SHOW = auto()  # first show
    RESIZE = auto()  # user dragged resize
    MOVE = auto()  # window moved / screen change
    MANUAL = auto()  # explicit user command (always allowed)


def _coerce_policy(v: "FitPolicy | str") -> FitPolicy:
    if isinstance(v, FitPolicy):
        return v
    s = str(v).lower()
    if s == "always":
        return FitPolicy.ALWAYS
    if s in {"on_startup", "startup"}:
        return FitPolicy.ON_STARTUP
    return FitPolicy.OFF


def _coerce_trigger(v: "FitTrigger | str") -> FitTrigger:
    if isinstance(v, FitTrigger):
        return v
    s = str(v).lower()
    if s == "show":
        return FitTrigger.SHOW
    if s == "resize":
        return FitTrigger.RESIZE
    if s in {"move", "screen", "screen_change", "screenchanged"}:
        return FitTrigger.MOVE
    return FitTrigger.MANUAL


def should_fit(
    policy: "FitPolicy | str", did_snap: bool, trigger: "FitTrigger | str"
) -> bool:
    """
    Decide whether to snap the outer window to a computed fit, given a policy,
    whether we've already snapped once this session (did_snap), and the trigger.
    """
    p = _coerce_policy(policy)
    t = _coerce_trigger(trigger)

    # Manual "fit now" is always allowed.
    if t == FitTrigger.MANUAL:
        return True

    # Policy OFF: never snap automatically.
    if p == FitPolicy.OFF:
        return False

    # Policy ON_STARTUP: snap only on the very first show.
    if p == FitPolicy.ON_STARTUP:
        return (t == FitTrigger.SHOW) and (not did_snap)

    # Policy ALWAYS: snap on show/move/resize.
    return t in {FitTrigger.SHOW, FitTrigger.MOVE, FitTrigger.RESIZE}


def _auto_fit_columns(n_tiles: int, current_cols: int) -> int:
    """
    Derive a stable column count from total tiles (startup-time auto-fit).

    Simple rule: at least ceil(sqrt(n_tiles)).
    This yields 7 for 37 tiles (the unit test expectation).
    """
    if n_tiles <= 0:
        return current_cols
    return max(
        current_cols, int(math.ceil(math.sqrt(n_tiles)))
    )  # monotonic, idempotent


def guess_domain(url: str) -> str:
    return metadata_guess_domain(url)


def fetch_favicon(url: str, size: int = 128) -> Optional[Path]:
    """Try to save a favicon PNG using Google's s2 service."""
    return fetch_favicon_to_directory(
        url,
        output_directory=ICON_DIR,
        size=size,
    )


def letter_icon(text: str, size: int = 92, bg: str = "#F5F6FA") -> QIcon:
    """Generate a round icon with the first letter of the name."""
    ch = (text or "?").strip()[0].upper()
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # background circle
    color = QColor(bg)
    p.setBrush(color)
    p.setPen(QColor("#D6D8E1"))
    p.drawEllipse(1, 1, size - 2, size - 2)
    # letter
    font = QFont()
    font.setBold(True)
    font.setPointSize(int(size * 0.45))
    p.setFont(font)
    p.setPen(QColor("#222"))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, ch)
    p.end()
    return QIcon(pix)


class TabVisibilityDialog(QDialog):
    _AVAILABLE_AREA_MARGIN = 48
    _FALLBACK_MAX_INITIAL_HEIGHT = 640
    _MIN_INITIAL_HEIGHT = 260

    def __init__(
        self,
        tabs: list[str],
        hidden: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Tab Visibility")
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        self._boxes: list[tuple[str, QCheckBox]] = []

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        list_widget = QWidget(scroll)
        list_layout = QVBoxLayout(list_widget)
        for tab in tabs:
            cb = QCheckBox(tab)
            cb.setChecked(tab not in hidden)
            list_layout.addWidget(cb)
            self._boxes.append((tab, cb))
        scroll.setWidget(list_widget)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if save_button is not None:
            save_button.setText("Save")
            save_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._cap_initial_size_to_available_area()

    def result_hidden(self) -> list[str]:
        return [tab for tab, cb in self._boxes if not cb.isChecked()]

    def _cap_initial_size_to_available_area(self) -> None:
        screen = None
        parent = self.parentWidget()
        if parent is not None:
            parent_window = parent.window().windowHandle()
            if parent_window is not None:
                screen = parent_window.screen()
        if screen is None:
            own_window = self.windowHandle()
            if own_window is not None:
                screen = own_window.screen()
        if screen is None:
            screen = QApplication.primaryScreen()

        max_height = self._FALLBACK_MAX_INITIAL_HEIGHT
        if screen is not None:
            available = screen.availableGeometry()
            max_height = max(
                self._MIN_INITIAL_HEIGHT,
                available.height() - self._AVAILABLE_AREA_MARGIN,
            )

        hint = self.sizeHint()
        self.resize(hint.width(), min(hint.height(), max_height))


class TileButton(QToolButton):
    def __init__(
        self,
        tile: Tile,
        index: int,
        on_open: Callable[[Tile], None],
        on_edit: Callable[[Tile], None],
        on_remove: Callable[[Tile], None],
        on_duplicate: Callable[[Tile], None],
        on_move: Callable[[int, int], None],
        on_change_tab: Callable[[Tile, str], None],
        tabs: list[str],
        *,
        selection_token: OpaqueToken | None = None,
        selected: bool = False,
        on_toggle_selection: Callable[[OpaqueToken, Tile], None] | None = None,
    ) -> None:
        super().__init__()
        selection_mode = selection_token is not None
        if selection_mode and on_toggle_selection is None:
            raise ValueError("Selection-mode tiles require a selection callback.")
        self.tile = tile
        self.index = index
        self.on_open = on_open
        self.on_edit = on_edit
        self.on_remove = on_remove
        self.on_duplicate = on_duplicate
        self.on_move = on_move
        self.on_change_tab = on_change_tab
        self.tabs = tabs
        self.selection_token = selection_token
        self.on_toggle_selection = on_toggle_selection
        self.selection_mode = selection_mode
        self._drag_start_pos: QPoint | None = None

        self.setCheckable(selection_mode)
        self.setChecked(selection_mode and selected)
        self._update_selection_presentation()
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIcon(self._icon_for_tile())
        self.setIconSize(QSize(72, 72))
        self.setFixedSize(150, 140)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(not selection_mode)
        self._apply_style()

        self.clicked.connect(self._handle_click)

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
        QToolButton {{
            background: {self.tile.bg};
            border: 1px solid #E3E5EE;
            border-radius: 12px;
            padding-top: 10px;
        }}
        QToolButton:hover {{
            border-color: #C7CAD8;
        }}
        QToolButton:pressed {{
            background: #ECEEF6;
        }}
        QToolButton:checked {{
            background: #DCEBFF;
            border: 4px solid #0B57D0;
            padding-top: 7px;
        }}
        QToolButton:checked:hover,
        QToolButton:checked:pressed {{
            background: #DCEBFF;
            border-color: #0B57D0;
        }}
        QToolButton:focus,
        QToolButton:focus:hover,
        QToolButton:focus:pressed,
        QToolButton:checked:focus,
        QToolButton:checked:focus:hover,
        QToolButton:checked:focus:pressed {{
            border: 4px dashed #111827;
            padding-top: 7px;
        }}
        """)

    def _update_selection_presentation(self) -> None:
        if not self.selection_mode:
            self.setText(self.tile.name)
            self.setAccessibleName(self.tile.name)
            self.setAccessibleDescription("Open this tile.")
            return
        selected = self.isChecked()
        self.setText(f"✓ {self.tile.name}" if selected else self.tile.name)
        state = "selected" if selected else "not selected"
        self.setAccessibleName(f"{self.tile.name}, {state}")
        self.setAccessibleDescription("Toggle this tile's selection.")

    def _icon_for_tile(self) -> QIcon:
        if self.tile.icon and Path(self.tile.icon).exists():
            return QIcon(self.tile.icon)
        return letter_icon(self.tile.name, 92, self.tile.bg)

    def _handle_click(self, _checked: bool = False) -> None:
        if self.selection_mode:
            token = self.selection_token
            callback = self.on_toggle_selection
            if token is None or callback is None:
                return
            callback(token, self.tile)
            self._update_selection_presentation()
            return
        self.on_open(self.tile)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        if self.selection_mode:
            event.accept()
            return
        m = QMenu(self)
        m.addAction("Open", lambda: self.on_open(self.tile))
        m.addSeparator()
        m.addAction("Edit…", lambda: self.on_edit(self.tile))
        m.addAction("Duplicate", lambda: self.on_duplicate(self.tile))
        m.addAction("Remove", lambda: self.on_remove(self.tile))
        assign = m.addMenu("Assign to Tab")
        for name in self.tabs:
            if name != self.tile.tab:
                assign.addAction(name, lambda n=name: self.on_change_tab(self.tile, n))
        m.exec(event.globalPos())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.selection_mode:
            self._drag_start_pos = None
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.selection_mode:
            self._drag_start_pos = None
            super().mouseMoveEvent(event)
            return
        if self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if (
            event.position().toPoint() - self._drag_start_pos
        ).manhattanLength() < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self.index))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start_pos = None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.selection_mode:
            event.ignore()
            return
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self.selection_mode:
            event.ignore()
            return
        if event.mimeData().hasText():
            from_idx = int(event.mimeData().text())
            self.on_move(from_idx, self.index)
            event.acceptProposedAction()


@dataclass
class LaunchPlan:
    """Plan describing how a tile's URL should be opened."""

    browser_name: str | None
    open_target: Literal["tab", "window"]
    profile: str | None
    command: list[str] | None
    controller: str | None
    new: int | None


def build_launch_plan(tile: Tile) -> LaunchPlan:
    """Return the launch strategy for *tile*.

    When falling back to :mod:`webbrowser`, ``new=1`` opens a new window and
    ``new=2`` opens a new tab, per the standard library's semantics.
    """
    target = getattr(tile, "open_target", "tab")
    if tile.browser:
        lowered = tile.browser.lower()
        exe_resolved = _resolve_controller_exe(tile.browser)
        exe = exe_resolved or tile.browser
        record_breadcrumb("launch_exe_resolved", exe=exe, browser=tile.browser)

        if "chrome" in lowered or "edge" in lowered:
            args = [exe]
            if tile.chrome_profile and "chrome" in lowered:
                args.append(f"--profile-directory={tile.chrome_profile}")
            if target == "window":
                args.append("--new-window")
            args.append(tile.url)
            return LaunchPlan(
                tile.browser, target, tile.chrome_profile, args, tile.browser, None
            )

        if "firefox" in lowered:
            args = [
                exe,
                "--new-window" if target == "window" else "--new-tab",
                tile.url,
            ]
            return LaunchPlan(
                tile.browser, target, tile.chrome_profile, args, tile.browser, None
            )

        new_flag = 1 if target == "window" else 2
        return LaunchPlan(
            tile.browser, target, tile.chrome_profile, None, tile.browser, new_flag
        )

    new_flag = 1 if target == "window" else 2
    return LaunchPlan(None, target, tile.chrome_profile, None, "default", new_flag)


def _tile_uses_chrome(tile: Tile) -> bool:
    """Return True if the given tile will launch using Google Chrome."""
    if sys.platform != "win32":
        return False
    chosen = getattr(tile, "browser", None)
    if chosen:
        as_str = str(chosen)
        return is_chrome_path(as_str) or "chrome" in as_str.lower()
    return is_windows_default_browser_chrome()


_REFRESH_STAGING_PREFIX = "refresh-"


def _owned_refresh_directory(path: Path) -> Path | None:
    """Return a validated, directly managed refresh directory."""
    try:
        icon_directory = ICON_DIR.resolve()
        candidate = path.resolve()
    except (OSError, RuntimeError):
        return None
    if candidate.parent != icon_directory or not candidate.name.startswith(
        _REFRESH_STAGING_PREFIX
    ):
        return None
    return candidate


def _remove_refresh_directory(path: Path) -> bool:
    """Remove only a validated batch directory beneath managed icon storage."""
    candidate = _owned_refresh_directory(path)
    if candidate is None:
        return False
    try:
        if candidate.exists():
            shutil.rmtree(candidate)
        return not candidate.exists()
    except OSError:
        return False


def _resolved_staged_icon(batch_directory: Path, icon_path: Path) -> Path | None:
    batch = _owned_refresh_directory(batch_directory)
    if batch is None or icon_path.is_symlink():
        return None
    try:
        icon = icon_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not icon.is_file() or batch not in icon.parents:
        return None
    return icon


def _prune_refresh_directory(
    batch_directory: Path,
    retained_icons: Iterable[Path],
) -> bool:
    """Keep only referenced regular files in one quiescent refresh batch."""
    batch = _owned_refresh_directory(batch_directory)
    if batch is None or not batch.is_dir():
        return False

    retained: set[Path] = set()
    retained_directories: set[Path] = set()
    for icon_path in retained_icons:
        icon = _resolved_staged_icon(batch, icon_path)
        if icon is None:
            return False
        retained.add(icon)
        parent = icon.parent
        while parent != batch:
            retained_directories.add(parent)
            parent = parent.parent

    try:
        entries = sorted(
            batch.rglob("*"),
            key=lambda entry: len(entry.parts),
            reverse=True,
        )
        for entry in entries:
            if entry.is_symlink():
                entry.unlink()
            elif entry.is_file():
                if entry.resolve() not in retained:
                    entry.unlink()
            elif entry.is_dir() and entry.resolve() not in retained_directories:
                entry.rmdir()
            elif not entry.is_dir():
                return False

        if not retained:
            batch.rmdir()
            return not batch.exists()

        for entry in batch.rglob("*"):
            if entry.is_symlink():
                return False
            if entry.is_file() and entry.resolve() not in retained:
                return False
            if not entry.is_file() and not entry.is_dir():
                return False
        return all(icon.is_file() for icon in retained)
    except (OSError, RuntimeError):
        return False


class _MetadataRefreshSignals(QObject):
    finished = Signal(object, object, object)


class _MetadataRefreshRunnable(QRunnable):
    def __init__(
        self,
        operation_token: OpaqueToken,
        snapshots: tuple[TileSnapshot, ...],
        batch_directory: Path,
        cancellation: threading.Event,
        signals: _MetadataRefreshSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._operation_token = operation_token
        self._snapshots = snapshots
        self._batch_directory = batch_directory
        self._cancellation = cancellation
        self._signals = signals

    @Slot()
    def run(self) -> None:
        results: tuple[RefreshResult, ...] | None = None
        error_type: str | None = None
        try:
            results = run_metadata_refresh(
                self._snapshots,
                output_directory=self._batch_directory,
                cancellation=self._cancellation,
            )
        except Exception as exc:
            error_type = type(exc).__name__ or "Exception"
        finally:
            self._signals.finished.emit(
                self._operation_token,
                results,
                error_type,
            )


@dataclass(frozen=True)
class _ActiveRefresh:
    token: OpaqueToken
    tab_id: str = field(repr=False)
    tab_name: str = field(repr=False)
    snapshots: tuple[TileSnapshot, ...] = field(repr=False)
    tiles_by_token: dict[OpaqueToken, Tile] = field(repr=False)
    batch_directory: Path = field(repr=False)
    cancellation: threading.Event = field(repr=False)


class Main(QMainWindow):
    def __init__(self, config: LauncherConfig) -> None:
        super().__init__()
        self.cfg = config
        self._fit_guard = False
        self._rebuilding = False
        self._closing = False
        self._close_ready = False
        self._operation_guard = OperationGuard()
        self._active_refresh: _ActiveRefresh | None = None
        self._metadata_refresh_pool = QThreadPool(self)
        self._metadata_refresh_pool.setMaxThreadCount(1)
        self._metadata_refresh_signals = _MetadataRefreshSignals(self)
        self._metadata_refresh_signals.finished.connect(
            self._on_metadata_refresh_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._metadata_close_poll = QTimer(self)
        self._metadata_close_poll.setSingleShot(True)
        self._metadata_close_poll.setInterval(25)
        self._metadata_close_poll.timeout.connect(self._poll_metadata_refresh_close)
        self._selection_tab_id: str | None = None
        self._selection_tab_name: str | None = None
        self._selection_tiles: dict[OpaqueToken, Tile] = {}
        self._selection_tokens_by_identity: dict[int, OpaqueToken] = {}
        self._selected_tokens: set[OpaqueToken] = set()

        # -------- Startup auto-fit: materialize columns on config when enabled --------
        if self.cfg.auto_fit:
            wanted = _auto_fit_columns(len(self.cfg.tiles), self.cfg.columns)
            if wanted != self.cfg.columns:
                self.cfg.columns = wanted
            self._computed_columns = self.cfg.columns
        else:
            self._computed_columns = self.cfg.columns
            # Backwards‑compatibility heuristic for fixed columns.
            if len(self.cfg.tiles) > 36 and self.cfg.columns < 7:
                self.cfg.columns = 7
            elif len(self.cfg.tiles) > 25 and self.cfg.columns < 6:
                self.cfg.columns = 6

        self.setWindowTitle(self.cfg.title)

        width, height = 900, 600
        self.resize(width, height)

        screen = (
            self.windowHandle().screen()
            if self.windowHandle() is not None
            else QApplication.primaryScreen()
        )
        avail = screen.availableGeometry() if screen is not None else None

        if not self.cfg.auto_fit:
            applied = False
            if self.cfg.window_w and self.cfg.window_h and avail is not None:
                w = min(int(self.cfg.window_w), avail.width())
                h = min(int(self.cfg.window_h), avail.height())
                self.resize(w, h)
                if self.cfg.window_x is not None and self.cfg.window_y is not None:
                    x = max(
                        avail.left(), min(int(self.cfg.window_x), avail.right() - w)
                    )
                    y = max(
                        avail.top(), min(int(self.cfg.window_y), avail.bottom() - h)
                    )
                    self.move(x, y)
                record_breadcrumb("geometry_restore", w=w, h=h)
                applied = True
            if not applied and len(self.cfg.tiles) > 25:
                cols = max(6, self.cfg.columns)
                tile_w, spacing, margins = 150, 12, 32
                needed_width = margins + cols * tile_w + (cols - 1) * spacing
                if avail is not None:
                    w = min(needed_width, avail.width())
                    h = min(height, avail.height())
                    self.resize(w, h)
                else:
                    self.resize(needed_width, height)

        # toolbar and menus
        self.toolbar = QToolBar()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self.add_action = QAction("➕ Add", self)
        self.add_action.triggered.connect(self.add_tile)
        self.toolbar.addAction(self.add_action)
        self.import_action = QAction("Import URLs…", self)
        self.import_action.triggered.connect(lambda: self.import_urls())
        self.toolbar.addAction(self.import_action)
        self.select_tiles_action = QAction("Select tiles", self)
        self.select_tiles_action.triggered.connect(self.enter_selection_mode)
        self.toolbar.addAction(self.select_tiles_action)

        self.selection_count_label = QLabel("0 selected", self)
        self.selection_count_label.setAccessibleName("Selected tile count")
        self.selection_count_label.setContentsMargins(8, 0, 8, 0)
        self.selection_count_action = self.toolbar.addWidget(self.selection_count_label)
        self.select_all_action = QAction("Select all", self)
        self.select_all_action.triggered.connect(self.select_all_tiles)
        self.toolbar.addAction(self.select_all_action)
        self.clear_selection_action = QAction("Clear selection", self)
        self.clear_selection_action.triggered.connect(self.clear_tile_selection)
        self.toolbar.addAction(self.clear_selection_action)
        self.refresh_metadata_action = QAction("Refresh names and icons", self)
        self.refresh_metadata_action.triggered.connect(self.refresh_selected_metadata)
        self.toolbar.addAction(self.refresh_metadata_action)
        self.done_selection_action = QAction("Done", self)
        self.done_selection_action.triggered.connect(self.exit_selection_mode)
        self.toolbar.addAction(self.done_selection_action)
        self._selection_toolbar_actions = (
            self.selection_count_action,
            self.select_all_action,
            self.clear_selection_action,
            self.refresh_metadata_action,
            self.done_selection_action,
        )
        for action in self._selection_toolbar_actions:
            action.setVisible(False)

        tab_menu = self.menuBar().addMenu("Tabs")
        self.add_tab_action = QAction("Add Tab", self)
        self.add_tab_action.triggered.connect(self.add_tab)
        tab_menu.addAction(self.add_tab_action)
        self.rename_tab_action = QAction("Rename Tab", self)
        self.rename_tab_action.triggered.connect(self.rename_tab)
        tab_menu.addAction(self.rename_tab_action)
        self.delete_tab_action = QAction("Delete Tab", self)
        self.delete_tab_action.triggered.connect(self.delete_tab)
        tab_menu.addAction(self.delete_tab_action)
        self.toggle_tab_action = QAction(self)
        self.toggle_tab_action.triggered.connect(self.toggle_current_tab_visibility)
        tab_menu.addAction(self.toggle_tab_action)
        self.manage_tab_visibility_action = QAction("Manage Tab Visibility…", self)
        self.manage_tab_visibility_action.triggered.connect(self.manage_tab_visibility)
        tab_menu.addAction(self.manage_tab_visibility_action)
        self._tab_mutation_actions = (
            self.add_tab_action,
            self.rename_tab_action,
            self.delete_tab_action,
            self.manage_tab_visibility_action,
        )

        view_menu = self.menuBar().addMenu("View")
        self.auto_fit_action = QAction("Auto-fit Tiles to Display", self)
        self.auto_fit_action.setCheckable(True)
        self.auto_fit_action.setChecked(self.cfg.auto_fit)
        self.auto_fit_action.toggled.connect(self._toggle_auto_fit)
        view_menu.addAction(self.auto_fit_action)

        debug_menu = self.menuBar().addMenu("Debug")
        debug_menu.addAction("Raise Exception", self._debug_raise)
        debug_menu.addAction("Qt Warning", lambda: qWarning("test"))

        self.tabs_widget = QTabWidget()
        self.tabs_widget.setMovable(True)
        self.tabs_widget.tabBar().tabMoved.connect(self._on_tab_moved)
        self.tabs_widget.currentChanged.connect(self._on_current_tab_changed)
        self.setCentralWidget(self.tabs_widget)

        self._tab_viewports: set[QWidget] = set()

        self.rebuild()

        wh = self.windowHandle()
        if wh is not None:
            wh.screenChanged.connect(
                lambda _s: QTimer.singleShot(
                    0, lambda: self.resize_to_fit_tiles(snap_window=self.cfg.auto_fit)
                )
            )

    def _visible_tabs(self) -> list[str]:
        return [t for t in self.cfg.tabs if t not in self.cfg.hidden_tabs]

    def _enforce_tab_invariants(self) -> None:
        enforce_tab_invariants(self.cfg)

    def _selection_active(self) -> bool:
        return self._selection_tab_id is not None

    def _tab_id_at(self, index: int) -> str | None:
        if index < 0:
            return None
        raw_id = self.tabs_widget.tabBar().tabData(index)
        return raw_id if isinstance(raw_id, str) else None

    def _on_current_tab_changed(self, index: int) -> None:
        if self._rebuilding:
            return
        if (
            self._selection_active()
            and self._tab_id_at(index) != self._selection_tab_id
        ):
            self._exit_selection_mode(repopulate=True)
        self._update_toggle_tab_action()
        self._update_selection_controls()
        QTimer.singleShot(
            0, lambda: self.resize_to_fit_tiles(snap_window=self.cfg.auto_fit)
        )

    def enter_selection_mode(self) -> None:
        if self._selection_active() or self._active_refresh is not None:
            return
        tab_name = self.current_tab()
        tab_id = self._tab_id_at(self.tabs_widget.currentIndex())
        if tab_id is None:
            return

        self._selection_tab_id = tab_id
        self._selection_tab_name = tab_name
        self._selection_tiles = {}
        self._selection_tokens_by_identity = {}
        for tile in self.cfg.tiles:
            if tile.tab_id != tab_id:
                continue
            identity = id(tile)
            if identity in self._selection_tokens_by_identity:
                continue
            token = OpaqueToken()
            self._selection_tiles[token] = tile
            self._selection_tokens_by_identity[identity] = token
        self._selected_tokens.clear()
        self._update_selection_controls()
        if tab_name in self._grids:
            self._populate_tab(tab_name)

    def exit_selection_mode(self) -> None:
        if self._active_refresh is not None:
            return
        self._exit_selection_mode(repopulate=True)

    def _exit_selection_mode(self, *, repopulate: bool) -> None:
        tab_name = self._selection_tab_name
        self._selection_tab_id = None
        self._selection_tab_name = None
        self._selection_tiles.clear()
        self._selection_tokens_by_identity.clear()
        self._selected_tokens.clear()
        self._update_selection_controls()
        if repopulate and tab_name is not None and tab_name in self._grids:
            self._populate_tab(tab_name)

    def _selection_token_for_tile(self, tile: Tile) -> OpaqueToken | None:
        token = self._selection_tokens_by_identity.get(id(tile))
        if token is None or self._selection_tiles.get(token) is not tile:
            return None
        return token

    def _toggle_tile_selection(self, token: OpaqueToken, tile: Tile) -> None:
        if self._active_refresh is not None:
            return
        selection_tab = self._selection_tab_name
        selection_tab_id = self._selection_tab_id
        if (
            not self._selection_active()
            or selection_tab is None
            or selection_tab_id is None
            or self._selection_tiles.get(token) is not tile
            or tile.tab_id != selection_tab_id
            or not any(candidate is tile for candidate in self.cfg.tiles)
        ):
            if selection_tab is not None and selection_tab in self._grids:
                self._populate_tab(selection_tab)
            return
        if token in self._selected_tokens:
            self._selected_tokens.remove(token)
        else:
            self._selected_tokens.add(token)
        self._update_selection_controls()

    @staticmethod
    def _snapshot_tile(token: OpaqueToken, tile: Tile) -> TileSnapshot:
        return TileSnapshot(
            token=token,
            url=tile.url,
            name=tile.name,
            tab=tile.tab_id or "",
            icon=tile.icon,
            bg=tile.bg,
            browser=tile.browser,
            chrome_profile=tile.chrome_profile,
            open_target=tile.open_target,
        )

    def _selected_tile_snapshots(self) -> tuple[TileSnapshot, ...]:
        return tuple(
            self._snapshot_tile(token, tile)
            for token, tile in self._selection_tiles.items()
            if token in self._selected_tokens
        )

    def select_all_tiles(self) -> None:
        if not self._selection_active() or self._active_refresh is not None:
            return
        tab_name = self._selection_tab_name
        tab_id = self._selection_tab_id
        if tab_name is None or tab_id is None:
            return
        snapshots = tuple(
            self._snapshot_tile(token, tile)
            for token, tile in self._selection_tiles.items()
        )
        self._selected_tokens = set(select_all_for_active_tab(snapshots, tab_id))
        self._update_selection_controls()
        if tab_name is not None and tab_name in self._grids:
            self._populate_tab(tab_name)

    def clear_tile_selection(self) -> None:
        if not self._selection_active() or self._active_refresh is not None:
            return
        self._selected_tokens.clear()
        self._update_selection_controls()
        tab_name = self._selection_tab_name
        if tab_name is not None and tab_name in self._grids:
            self._populate_tab(tab_name)

    def refresh_selected_metadata(self) -> None:
        if (
            self._closing
            or not self._selection_active()
            or self._active_refresh is not None
            or self._metadata_refresh_pool.activeThreadCount() > 0
        ):
            return
        confirmed_tokens = frozenset(self._selected_tokens)
        selected_count = len(confirmed_tokens)
        if selected_count == 0:
            return

        response = QMessageBox.warning(
            self,
            "Refresh names and icons?",
            (
                f"Refresh names and icons for {selected_count} selected "
                f"{'tile' if selected_count == 1 else 'tiles'}?\n\n"
                "Successful results replace the current name or icon, including "
                "custom values. A failed lookup keeps that field unchanged."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        if (
            self._closing
            or not self._selection_active()
            or self._active_refresh is not None
            or self._metadata_refresh_pool.activeThreadCount() > 0
            or self._selected_tokens != confirmed_tokens
        ):
            return

        snapshots = self._selected_tile_snapshots()
        tab_id = self._selection_tab_id
        tab_name = self._selection_tab_name
        if not snapshots or tab_id is None or tab_name is None:
            return

        operation_token = OpaqueToken()
        if not self._operation_guard.start(operation_token):
            return
        try:
            batch_directory = create_batch_staging_directory(ICON_DIR)
        except OSError as exc:
            self._operation_guard.finish(operation_token)
            error_type = type(exc).__name__ or "OSError"
            record_breadcrumb(
                "metadata_refresh_failed",
                selected_count=selected_count,
                error_type=error_type,
                cleanup_ok=True,
            )
            QMessageBox.warning(
                self,
                "Refresh failed",
                "The refresh could not be started. No tiles were changed.",
            )
            return

        cancellation = threading.Event()
        runnable = _MetadataRefreshRunnable(
            operation_token,
            snapshots,
            batch_directory,
            cancellation,
            self._metadata_refresh_signals,
        )
        operation = _ActiveRefresh(
            token=operation_token,
            tab_id=tab_id,
            tab_name=tab_name,
            snapshots=snapshots,
            tiles_by_token={
                snapshot.token: self._selection_tiles[snapshot.token]
                for snapshot in snapshots
            },
            batch_directory=batch_directory,
            cancellation=cancellation,
        )
        self._active_refresh = operation
        self._update_selection_controls()
        record_breadcrumb(
            "metadata_refresh_started",
            selected_count=selected_count,
        )
        try:
            self._metadata_refresh_pool.start(runnable)
        except RuntimeError as exc:
            error_type = type(exc).__name__ or "RuntimeError"
            cleanup_ok = _remove_refresh_directory(batch_directory)
            self._operation_guard.finish(operation_token)
            self._active_refresh = None
            self._update_selection_controls()
            record_breadcrumb(
                "metadata_refresh_failed",
                selected_count=selected_count,
                error_type=error_type,
                cleanup_ok=cleanup_ok,
            )
            QMessageBox.warning(
                self,
                "Refresh failed",
                "The refresh could not be started. No tiles were changed.",
            )

    def _refresh_completion_matches(
        self,
        operation: _ActiveRefresh,
        results: tuple[RefreshResult, ...],
    ) -> bool:
        if (
            self._selection_tab_id != operation.tab_id
            or self._selection_tab_name != operation.tab_name
            or self._tab_id_at(self.tabs_widget.currentIndex()) != operation.tab_id
            or len(results) != len(operation.snapshots)
            or len(self._selected_tokens) != len(operation.snapshots)
        ):
            return False
        if any(
            result.token is not snapshot.token
            for snapshot, result in zip(operation.snapshots, results, strict=True)
        ):
            return False
        for snapshot in operation.snapshots:
            tile = operation.tiles_by_token.get(snapshot.token)
            if (
                tile is None
                or snapshot.token not in self._selected_tokens
                or not any(candidate is tile for candidate in self.cfg.tiles)
                or not snapshot_matches(
                    snapshot,
                    self._snapshot_tile(snapshot.token, tile),
                )
            ):
                return False
        return True

    def _detached_configuration(self) -> tuple[LauncherConfig, dict[int, Tile]]:
        tiles_by_identity: dict[int, Tile] = {}
        detached_tiles: list[Tile] = []
        for tile in self.cfg.tiles:
            identity = id(tile)
            detached = tiles_by_identity.get(identity)
            if detached is None:
                detached = replace(tile)
                tiles_by_identity[identity] = detached
            detached_tiles.append(detached)
        return (
            replace(
                self.cfg,
                tiles=detached_tiles,
                tabs=list(self.cfg.tabs),
                hidden_tabs=list(self.cfg.hidden_tabs),
                tab_ids=dict(self.cfg.tab_ids),
                tab_order=list(self.cfg.tab_order),
                application_extensions=deepcopy(self.cfg.application_extensions),
                workspace_extensions=deepcopy(self.cfg.workspace_extensions),
                tab_extensions=deepcopy(self.cfg.tab_extensions),
                extensions=deepcopy(self.cfg.extensions),
            ),
            tiles_by_identity,
        )

    def _retire_metadata_refresh(
        self,
        operation: _ActiveRefresh,
        *,
        remove_staging: bool,
    ) -> bool:
        cleanup_ok = True
        if remove_staging:
            cleanup_ok = _remove_refresh_directory(operation.batch_directory)
        self._operation_guard.finish(operation.token)
        if self._active_refresh is operation:
            self._active_refresh = None
        return cleanup_ok

    def _schedule_metadata_refresh_close_poll(self) -> None:
        if (
            self._closing
            and not self._close_ready
            and not self._metadata_close_poll.isActive()
        ):
            self._metadata_close_poll.start()

    @Slot()
    def _poll_metadata_refresh_close(self) -> None:
        if not self._closing or self._close_ready:
            return
        if (
            self._active_refresh is not None
            or self._metadata_refresh_pool.activeThreadCount() > 0
        ):
            self._schedule_metadata_refresh_close_poll()
            return
        self._close_ready = True
        self.close()

    def _metadata_refresh_failed(
        self,
        operation: _ActiveRefresh,
        error_type: str,
    ) -> None:
        safe_error_type = error_type if error_type.isidentifier() else "RefreshError"
        cleanup_ok = self._retire_metadata_refresh(
            operation,
            remove_staging=True,
        )
        self._update_selection_controls()
        record_breadcrumb(
            "metadata_refresh_failed",
            selected_count=len(operation.snapshots),
            error_type=safe_error_type,
            cleanup_ok=cleanup_ok,
        )
        QMessageBox.warning(
            self,
            "Refresh failed",
            "The refresh could not be completed. No tiles were changed.",
        )

    @Slot(object, object, object)
    def _on_metadata_refresh_finished(
        self,
        operation_token: object,
        results_payload: object,
        error_payload: object,
    ) -> None:
        operation = self._active_refresh
        if operation is None or operation.token is not operation_token:
            return

        if (
            self._closing
            or operation.cancellation.is_set()
            or not self._operation_guard.is_current(operation.token)
        ):
            cleanup_ok = self._retire_metadata_refresh(
                operation,
                remove_staging=True,
            )
            record_breadcrumb(
                "metadata_refresh_cancelled",
                selected_count=len(operation.snapshots),
                cleanup_ok=cleanup_ok,
            )
            if self._closing:
                self._schedule_metadata_refresh_close_poll()
            else:
                self._update_selection_controls()
            return

        if error_payload is not None:
            error_type = (
                error_payload if isinstance(error_payload, str) else "WorkerError"
            )
            self._metadata_refresh_failed(operation, error_type)
            return
        if not isinstance(results_payload, tuple) or not all(
            isinstance(result, RefreshResult) for result in results_payload
        ):
            self._metadata_refresh_failed(operation, "InvalidWorkerResult")
            return

        results = cast(tuple[RefreshResult, ...], results_payload)
        if any(result.cancelled for result in results):
            self._metadata_refresh_failed(operation, "CancelledRefresh")
            return
        if not self._refresh_completion_matches(operation, results):
            self._metadata_refresh_failed(operation, "StaleTileState")
            return

        candidate, detached_by_identity = self._detached_configuration()
        retained_icons: set[Path] = set()
        changed_tiles = 0
        name_updates = 0
        icon_updates = 0
        for snapshot, result in zip(operation.snapshots, results, strict=True):
            original = operation.tiles_by_token[snapshot.token]
            detached = detached_by_identity.get(id(original))
            if detached is None:
                self._metadata_refresh_failed(operation, "StaleTileState")
                return
            merged = merge_refresh_result(snapshot, result)
            refreshed_icon = merged.icon
            if merged.icon_changed:
                if refreshed_icon is None:
                    self._metadata_refresh_failed(operation, "InvalidIconResult")
                    return
                staged_icon = _resolved_staged_icon(
                    operation.batch_directory,
                    Path(refreshed_icon),
                )
                if staged_icon is None:
                    self._metadata_refresh_failed(operation, "InvalidIconResult")
                    return
                refreshed_icon = str(staged_icon)
                retained_icons.add(staged_icon)
            detached.name = merged.name
            detached.icon = refreshed_icon
            if merged.changed:
                changed_tiles += 1
            name_updates += int(merged.name_changed)
            icon_updates += int(merged.icon_changed)

        diagnostics = summarize_refresh_results(results)
        if changed_tiles == 0:
            cleanup_ok = self._retire_metadata_refresh(
                operation,
                remove_staging=True,
            )
            self._update_selection_controls()
            record_breadcrumb(
                "metadata_refresh_no_changes",
                selected_count=len(operation.snapshots),
                title_errors=diagnostics.title_errors,
                favicon_errors=diagnostics.favicon_errors,
                cleanup_ok=cleanup_ok,
            )
            if cleanup_ok:
                QMessageBox.information(
                    self,
                    "Nothing updated",
                    "No names or icons were updated. Existing values were kept.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Refresh failed",
                    "The refresh could not be completed. No tiles were changed.",
                )
            return

        if not _prune_refresh_directory(
            operation.batch_directory,
            retained_icons,
        ):
            self._metadata_refresh_failed(operation, "StagingPruneError")
            return

        try:
            candidate.save()
        except Exception as exc:
            # Keep every persistence failure on the detached side of the transaction.
            self._metadata_refresh_failed(
                operation,
                type(exc).__name__ or "SaveError",
            )
            return

        selected_tab = operation.tab_name
        self._retire_metadata_refresh(operation, remove_staging=False)
        self.cfg = candidate
        self._exit_selection_mode(repopulate=False)
        self.rebuild()
        self._set_current_tab_by_name(selected_tab)
        record_breadcrumb(
            "metadata_refresh_completed",
            selected_count=len(operation.snapshots),
            changed_tiles=changed_tiles,
            name_updates=name_updates,
            icon_updates=icon_updates,
            title_errors=diagnostics.title_errors,
            favicon_errors=diagnostics.favicon_errors,
        )
        QMessageBox.information(
            self,
            "Refresh complete",
            (
                f"Updated {changed_tiles} "
                f"{'tile' if changed_tiles == 1 else 'tiles'}: "
                f"{name_updates} {'name' if name_updates == 1 else 'names'} and "
                f"{icon_updates} {'icon' if icon_updates == 1 else 'icons'}."
            ),
        )

    def _update_selection_controls(self) -> None:
        selecting = self._selection_active()
        busy = self._active_refresh is not None
        selected_count = len(self._selected_tokens)
        total_count = len(self._selection_tiles)

        for action in (self.add_action, self.import_action, self.select_tiles_action):
            action.setVisible(not selecting)
        for action in self._selection_toolbar_actions:
            action.setVisible(selecting)

        self.add_action.setEnabled(not selecting and not busy)
        self.import_action.setEnabled(not selecting and not busy)
        active_tab_id = self.cfg.tab_ids[self.current_tab()]
        active_tab_has_tiles = any(
            tile.tab_id == active_tab_id for tile in self.cfg.tiles
        )
        self.select_tiles_action.setEnabled(not busy and active_tab_has_tiles)
        self.selection_count_label.setText(
            f"Refreshing {selected_count}…" if busy else f"{selected_count} selected"
        )
        self.select_all_action.setEnabled(
            selecting and not busy and selected_count < total_count
        )
        self.clear_selection_action.setEnabled(
            selecting and not busy and selected_count > 0
        )
        self.refresh_metadata_action.setEnabled(
            selecting and not busy and selected_count > 0
        )
        self.done_selection_action.setEnabled(selecting and not busy)

        for action in self._tab_mutation_actions:
            action.setEnabled(not selecting and not busy)
        self._update_toggle_tab_action()
        self.auto_fit_action.setEnabled(not selecting and not busy)
        self.tabs_widget.tabBar().setMovable(not selecting and not busy)
        self.tabs_widget.setEnabled(not busy)

    def _save_runtime_change(
        self,
        previous: _RuntimeChangeSnapshot,
        *,
        operation: RuntimeChangeOperation,
        restore_tab: str,
        restore_before_rebuild: Callable[[], None] | None = None,
    ) -> bool:
        failure_category: RuntimeSaveFailureCategory | None = None
        try:
            self.cfg.save()
        except (OSError, ValueError) as error:
            failure_category = _runtime_save_failure_category(error)
        if failure_category is None:
            return True

        self.cfg = _restore_runtime_change(previous)
        if restore_before_rebuild is not None:
            restore_before_rebuild()
        self.rebuild()
        self._set_current_tab_by_name(restore_tab)
        record_breadcrumb(
            "config_change_save_failed",
            failure_count=1,
            failure_category=failure_category,
            operation=operation,
        )
        parent = self if isinstance(self, QWidget) else None
        QMessageBox.critical(
            parent,
            "DesktopTileLauncher",
            "The change could not be saved. No changes were applied.",
        )
        return False

    def _on_tab_moved(self, from_index: int, to_index: int) -> None:
        if self._selection_active() or self._active_refresh is not None:
            return
        tab_bar = self.tabs_widget.tabBar()
        visible_ids_after = [tab_bar.tabData(index) for index in range(tab_bar.count())]
        hidden_ids = [self.cfg.tab_ids.get(title) for title in self.cfg.hidden_tabs]
        updated_order = move_visible_tab(
            self.cfg.tab_order,
            hidden_ids,
            from_index,
            to_index,
            visible_ids_after,
        )
        if updated_order == self.cfg.tab_order:
            return

        if len(updated_order) != len(self.cfg.tab_order) or set(updated_order) != set(
            self.cfg.tab_order
        ):
            return
        title_by_id = {tab_id: title for title, tab_id in self.cfg.tab_ids.items()}
        try:
            ordered_titles = [title_by_id[tab_id] for tab_id in updated_order]
        except KeyError:
            return
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        self.cfg.tab_order = updated_order
        self.cfg.tabs = ordered_titles
        Main._save_runtime_change(
            self,
            previous,
            operation="tab_reorder",
            restore_tab=restore_tab,
        )

    def _set_current_tab_by_name(self, name: str) -> None:
        vis = self._visible_tabs()
        if name in vis:
            self.tabs_widget.setCurrentIndex(vis.index(name))
        elif vis:
            self.tabs_widget.setCurrentIndex(0)

    def _update_toggle_tab_action(self) -> None:
        name = self.current_tab()
        hidden = name in self.cfg.hidden_tabs
        self.toggle_tab_action.setText(
            "Show Current Tab" if hidden else "Hide Current Tab"
        )
        visible = self._visible_tabs()
        allow_hide = not hidden and len(visible) > 1
        self.toggle_tab_action.setEnabled(hidden or allow_hide)
        if self._selection_active() or self._active_refresh is not None:
            self.toggle_tab_action.setEnabled(False)

    def _toggle_auto_fit(self, checked: bool) -> None:
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        previous_auto_fit = self.cfg.auto_fit
        previous_computed_columns = self._computed_columns
        self.cfg.auto_fit = checked
        if not checked:
            self._computed_columns = self.cfg.columns

        def restore_auto_fit_state() -> None:
            self._computed_columns = previous_computed_columns
            signals_were_blocked = self.auto_fit_action.blockSignals(True)
            try:
                self.auto_fit_action.setChecked(previous_auto_fit)
            finally:
                self.auto_fit_action.blockSignals(signals_were_blocked)

        if not Main._save_runtime_change(
            self,
            previous,
            operation="auto_fit_toggle",
            restore_tab=restore_tab,
            restore_before_rebuild=restore_auto_fit_state,
        ):
            return
        self.rebuild()
        self.resize_to_fit_tiles(snap_window=checked)

    # -------- UI building --------
    def showEvent(self, event: QShowEvent) -> None:  # noqa: D401
        super().showEvent(event)
        record_breadcrumb("window_shown")

    def rebuild(self) -> None:
        if self._selection_active():
            self._exit_selection_mode(repopulate=False)
        self._rebuilding = True
        try:
            self.tabs_widget.clear()
            self._grids: dict[str, QGridLayout] = {}
            self._tab_viewports.clear()
            tab_bar = self.tabs_widget.tabBar()
            for tab in self._visible_tabs():
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                container = QWidget()
                grid = QGridLayout(container)
                grid.setSpacing(12)
                grid.setContentsMargins(16, 16, 16, 16)
                scroll.setWidget(container)
                self._wire_tab_whitespace_menu(scroll)
                tab_index = self.tabs_widget.addTab(scroll, tab)
                tab_bar.setTabData(tab_index, self.cfg.tab_ids[tab])
                self._grids[tab] = grid
                self._populate_tab(tab)
        finally:
            self._rebuilding = False
        QTimer.singleShot(
            0, lambda: self.resize_to_fit_tiles(snap_window=self.cfg.auto_fit)
        )
        self._update_toggle_tab_action()
        self._update_selection_controls()

    def _populate_tab(self, tab: str) -> None:
        grid = self._grids[tab]
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w:
                w.setEnabled(False)
                w.hide()
                w.deleteLater()

        if self.cfg.auto_fit:
            cols = max(1, int(self._computed_columns))
        else:
            cols = max(1, int(self.cfg.columns))
        r = c = 0
        tab_id = self.cfg.tab_ids[tab]
        tab_tiles = [t for t in self.cfg.tiles if t.tab_id == tab_id]
        all_tabs = list(self.cfg.tabs)
        for idx, tile in enumerate(tab_tiles):

            def move(f: int, t: int, tab_name: str = tab) -> None:
                self.move_tile(tab_name, f, t)

            selection_token = None
            if tab == self._selection_tab_name:
                selection_token = self._selection_token_for_tile(tile)
            btn = TileButton(
                tile,
                idx,
                on_open=self.open_tile,
                on_edit=self.edit_tile,
                on_remove=self.remove_tile,
                on_duplicate=self.duplicate_tile,
                on_move=move,
                on_change_tab=self.change_tile_tab,
                tabs=all_tabs,
                selection_token=selection_token,
                selected=selection_token in self._selected_tokens,
                on_toggle_selection=(
                    self._toggle_tile_selection if selection_token is not None else None
                ),
            )
            grid.addWidget(btn, r, c)
            c += 1
            if c >= cols:
                c = 0
                r += 1

    def _wire_tab_whitespace_menu(self, scroll: QScrollArea) -> None:
        """Install a context menu handler on the scroll area's viewport."""
        vp = scroll.viewport()
        vp.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        vp.installEventFilter(self)
        self._tab_viewports.add(vp)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        try:
            if event.type() == QEvent.Type.ContextMenu and obj in self._tab_viewports:
                if self._selection_active():
                    return True
                cme = cast(QContextMenuEvent, event)
                # Use the event's globalPos() to avoid touching the (possibly deleted) widget.
                global_pos = cme.globalPos()
                if not self._is_over_tile(global_pos):
                    self._show_whitespace_menu(global_pos)
                    return True
                return False
            return super().eventFilter(obj, event)
        except Exception:
            # Log and allow Qt to continue processing.
            record_breadcrumb(
                "event_filter_error",
                etype=int(event.type()) if hasattr(event, "type") else None,
            )
            logging.getLogger(__name__).exception(
                "eventFilter error",
                extra=sanitize_log_extra({"event": "event_filter_error"}),
            )
            return False

    def _is_over_tile(self, global_pos: QPoint) -> bool:
        w = QApplication.widgetAt(global_pos)
        while w:
            if isinstance(w, TileButton):
                return True
            w = w.parentWidget()
        return False

    def _show_whitespace_menu(self, global_pos: QPoint) -> None:
        if self._selection_active():
            return
        menu = QMenu(self)
        act = menu.addAction("Add Tile…")
        act.triggered.connect(lambda: self.add_tile(self.current_tab()))
        import_action = menu.addAction("Import URLs…")
        import_action.triggered.connect(lambda: self.import_urls(self.current_tab()))
        menu.exec(global_pos)

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: D401
        super().moveEvent(event)
        # No snap on ordinary moves; screenChanged signal handles monitor transitions.

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D401
        super().resizeEvent(event)
        if self.cfg.auto_fit and not self._fit_guard:
            # Reflow the grid to the new size but DO NOT snap the window back.
            QTimer.singleShot(0, lambda: self.resize_to_fit_tiles(snap_window=False))

    def resize_to_fit_tiles(self, *, snap_window: bool = True) -> None:
        if self._fit_guard:
            return
        self._fit_guard = True
        try:
            tile_w, tile_h = 150, 140
            current = self.current_tab()
            grid = self._grids.get(current)
            if grid is None:
                return
            spacing = grid.spacing()
            margins = grid.contentsMargins()
            margins_lr = margins.left() + margins.right()
            margins_tb = margins.top() + margins.bottom()
            current_tab_id = self.cfg.tab_ids[current]
            tile_count = len([t for t in self.cfg.tiles if t.tab_id == current_tab_id])

            screen = (
                self.windowHandle().screen()
                if self.windowHandle() is not None
                else QApplication.screenAt(self.frameGeometry().center())
            ) or QApplication.primaryScreen()

            frame_w = self.frameGeometry().width() - self.geometry().width()
            frame_h = self.frameGeometry().height() - self.geometry().height()
            try:
                sb_w = self.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, self)
            except Exception:
                sb_w = 16
            if sb_w <= 0:
                sb_w = 16

            columns_hint = None if self.cfg.auto_fit else self.cfg.columns

            if snap_window:
                avail_w = screen.availableGeometry().width()
                avail_h = screen.availableGeometry().height()
            else:
                # Manual reflow: use the current *outer* window size as the budget.
                avail_w = self.frameGeometry().width()
                avail_h = self.frameGeometry().height()

            result = compute_grid_fit(
                avail_w,
                avail_h,
                tile_w,
                tile_h,
                spacing,
                margins_lr,
                margins_tb,
                frame_w,
                frame_h,
                sb_w,
                tile_count,
                columns_hint,
            )

            if self.cfg.auto_fit and result.columns != self._computed_columns:
                self._computed_columns = result.columns
                self._populate_tab(current)

            record_breadcrumb(
                "fit_compute",
                screen=getattr(screen, "name", lambda: "unknown")(),
                avail_w=avail_w,
                avail_h=avail_h,
                tiles=tile_count,
                hint_cols=columns_hint,
                cols=result.columns,
                rows_visible=result.rows_visible,
                need_vscroll=result.need_vscroll,
                snap_window=snap_window,
            )

            if snap_window:
                self.resize(result.window_w, result.window_h)
            record_breadcrumb(
                "fit_apply", window_w=result.window_w, window_h=result.window_h
            )

            if snap_window and tile_count > 0 and result.need_vscroll:
                self.move(self.x(), screen.availableGeometry().top())
        finally:
            self._fit_guard = False

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        operation = self._active_refresh
        pool_active = self._metadata_refresh_pool.activeThreadCount() > 0
        if not self._close_ready and (
            self._closing or operation is not None or pool_active
        ):
            self._closing = True
            if operation is not None:
                operation.cancellation.set()
            self._operation_guard.invalidate()
            self.setEnabled(False)
            event.ignore()
            self._schedule_metadata_refresh_close_poll()
            return

        self._closing = True
        try:
            g = self.normalGeometry() if self.isMaximized() else self.frameGeometry()
            _persist_close_geometry(
                self.cfg,
                x=int(g.x()),
                y=int(g.y()),
                width=int(g.width()),
                height=int(g.height()),
            )
        finally:
            super().closeEvent(event)

    def current_tab(self) -> str:
        idx = self.tabs_widget.currentIndex()
        vis = self._visible_tabs()
        if idx < 0:
            return vis[0] if vis else (self.cfg.tabs[0] if self.cfg.tabs else "Main")
        return self.tabs_widget.tabText(idx)

    # -------- actions --------
    def open_tile(self, tile: Tile) -> None:
        if isinstance(self, Main) and (
            self._selection_active() or self._active_refresh is not None
        ):
            return
        logger = logging.getLogger(__name__)
        plan = build_launch_plan(tile)
        url = sanitize_url(tile.url)
        sanitized_command = sanitize_launch_command(plan.command)
        record_breadcrumb(
            "launch_attempt",
            url=url,
            browser=plan.browser_name or "default",
            open_target=plan.open_target,
        )
        record_breadcrumb(
            "launch_plan",
            browser=plan.browser_name or "default",
            open_target=plan.open_target,
            command=sanitized_command,
            controller=plan.controller,
            new=plan.new,
        )
        logger.info(
            "browser_launch_attempt",
            extra=sanitize_log_extra(
                {
                    "event": "browser_launch_attempt",
                    "browser": plan.browser_name or "default",
                    "flags": sanitized_command[1:-1] if sanitized_command else [],
                    "profile": plan.profile,
                    "open_target": plan.open_target,
                    "url": url,
                    "platform": sys.platform,
                    "pid": os.getpid(),
                }
            ),
        )

        # --- Windows explicit Chrome special-case (always try profile/CLI first) ---
        if (
            sys.platform == "win32"
            and plan.browser_name
            and "chrome" in plan.browser_name.lower()
        ):
            profile_dir = tile.chrome_profile or "Default"
            ok = False
            try:
                ok = launch_chrome_with_profile(tile.url, profile_dir, plan.open_target)
            except OSError as exc:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name,
                    open_target=plan.open_target,
                    profile=profile_dir,
                    error=str(exc),
                )
            if ok:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name,
                    open_target=plan.open_target,
                    profile=profile_dir,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
                return
            record_breadcrumb(
                "launch_path",
                path="chrome_profile_cli",
                browser=plan.browser_name,
                open_target=plan.open_target,
                profile=profile_dir,
                fallback=True,
            )

        # --- Windows default Chrome special-case (only when needed) ---
        # Use Chrome CLI only if Chrome is default *and* we need a specific profile
        # or a guaranteed new window. Otherwise fall through to webbrowser.open.
        if (
            sys.platform == "win32"
            and plan.browser_name is None
            and is_windows_default_browser_chrome()
            and (
                getattr(tile, "chrome_profile", None) is not None
                or plan.open_target == "window"
            )
        ):
            profile_dir = tile.chrome_profile or "Default"
            ok = False
            try:
                ok = launch_chrome_with_profile(tile.url, profile_dir, plan.open_target)
            except OSError as exc:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    profile=profile_dir,
                    error=str(exc),
                )
            if ok:
                record_breadcrumb(
                    "launch_path",
                    path="chrome_profile_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    profile=profile_dir,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
                return
            record_breadcrumb(
                "launch_path",
                path="chrome_profile_cli",
                browser=plan.browser_name or "default",
                open_target=plan.open_target,
                profile=profile_dir,
                fallback=True,
            )

        # --- Explicit controller CLI path (firefox/chrome/edge, etc.) ---
        if plan.command:
            try:
                debug_scaffold.last_launch_command = (
                    " ".join(sanitized_command) if sanitized_command else None
                )
                subprocess.Popen(plan.command, close_fds=True)  # nosec B603
                record_breadcrumb(
                    "launch_path",
                    path="browser_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    cmd=sanitized_command,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
                return
            except OSError as exc:
                record_breadcrumb(
                    "launch_path",
                    path="browser_cli",
                    browser=plan.browser_name or "default",
                    open_target=plan.open_target,
                    cmd=sanitized_command,
                    error=str(exc),
                )
                logger.error(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {
                            "event": "browser_launch_result",
                            "ok": False,
                            "error": str(exc),
                        }
                    ),
                )

        # --- Default browser path: use webbrowser.open directly (no webbrowser.get) ---
        if plan.browser_name is None or plan.controller == "default":
            new_flag = plan.new or (1 if plan.open_target == "window" else 2)
            try:
                webbrowser.open(tile.url, new=new_flag)
                record_breadcrumb(
                    "launch_path",
                    path="webbrowser_module",
                    browser="default",
                    open_target=plan.open_target,
                    new=new_flag,
                )
                record_breadcrumb("launch_result", ok=True, url=url)
                logger.info(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {"event": "browser_launch_result", "ok": True}
                    ),
                )
            except Exception as exc:  # very rare; keep behavior consistent
                record_breadcrumb(
                    "launch_path",
                    path="webbrowser_module",
                    browser="default",
                    open_target=plan.open_target,
                    new=new_flag,
                    error=str(exc),
                )
                record_breadcrumb("launch_result", ok=False, url=url)
                logger.error(
                    "browser_launch_result",
                    extra=sanitize_log_extra(
                        {
                            "event": "browser_launch_result",
                            "ok": False,
                            "error": str(exc),
                        }
                    ),
                )
                parent = self if isinstance(self, QWidget) else None
                QMessageBox.warning(
                    parent, "Failed to launch browser", f"Could not open {url}."
                )
            return

        # --- Named controller fallback (non-CLI): use webbrowser.get(name) ---
        controller_name = plan.controller or getattr(tile, "browser", None) or "default"
        record_breadcrumb("launch_fallback_controller", controller=controller_name)
        try:
            browser_obj = webbrowser.get(controller_name)
            if (plan.new or 0) == 2 and hasattr(browser_obj, "open_new_tab"):
                browser_obj.open_new_tab(tile.url)
            elif (plan.new or 0) == 1 and hasattr(browser_obj, "open_new"):
                browser_obj.open_new(tile.url)
            else:
                browser_obj.open(tile.url, new=plan.new or 0)
            record_breadcrumb(
                "launch_path",
                path="webbrowser",
                browser=plan.browser_name or controller_name,
                open_target=plan.open_target,
                controller=controller_name,
                new=plan.new or 0,
            )
            record_breadcrumb("launch_result", ok=True, url=url)
            logger.info(
                "browser_launch_result",
                extra=sanitize_log_extra(
                    {"event": "browser_launch_result", "ok": True}
                ),
            )
        except webbrowser.Error as exc:
            record_breadcrumb(
                "launch_path",
                path="webbrowser",
                browser=plan.browser_name or controller_name,
                open_target=plan.open_target,
                controller=controller_name,
                new=plan.new or 0,
                error=str(exc),
            )
            record_breadcrumb("launch_result", ok=False, url=url)
            logger.error(
                "browser_launch_result",
                extra=sanitize_log_extra(
                    {
                        "event": "browser_launch_result",
                        "ok": False,
                        "error": str(exc),
                    }
                ),
            )
            parent = self if isinstance(self, QWidget) else None
            QMessageBox.warning(
                parent,
                "Failed to launch browser",
                f"Could not open {url} in {controller_name}.",
            )

    def move_tile(self, tab: str, from_idx: int, to_idx: int) -> None:
        if self._selection_active() or self._active_refresh is not None:
            return
        if from_idx == to_idx:
            return
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        tab_id = self.cfg.tab_ids[tab]
        indices = [i for i, t in enumerate(self.cfg.tiles) if t.tab_id == tab_id]
        tile = self.cfg.tiles.pop(indices[from_idx])
        insert_at = indices[to_idx]
        self.cfg.tiles.insert(insert_at, tile)
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tile_reorder",
            restore_tab=restore_tab,
        ):
            return
        self._populate_tab(tab)

    def add_tile(self, default_tab: str | None = None) -> None:
        dlg = TileEditorDialog(
            tabs=self.cfg.tabs,
            browsers=available_browsers(),
            icon_dir=ICON_DIR,
            fetch_favicon=fetch_favicon,
            parent=self,
        )
        tab_name = default_tab or self.current_tab()
        idx = dlg.tab_combo.findText(tab_name, Qt.MatchFlag.MatchExactly)
        if idx >= 0:
            dlg.tab_combo.setCurrentIndex(idx)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.data:
            data = dlg.data
            previous = _runtime_change_snapshot(self.cfg)
            restore_tab = self.current_tab()
            self.cfg.tiles.append(
                Tile(
                    name=cast(str, data["name"]),
                    url=cast(str, data["url"]),
                    icon=data["icon"],
                    bg="#F5F6FA",
                    tab=cast(str, data["tab"]),
                    browser=data["browser"],
                    chrome_profile=data["chrome_profile"],
                    open_target=cast(str, data["open_target"]),
                    tab_id=_tab_id_for_runtime_name(
                        self.cfg,
                        cast(str, data["tab"]),
                    ),
                )
            )
            if not Main._save_runtime_change(
                self,
                previous,
                operation="tile_add",
                restore_tab=restore_tab,
            ):
                return
            self.rebuild()
            self._set_current_tab_by_name(cast(str, data["tab"]))
            record_breadcrumb("tile_add")

    def import_urls(self, default_tab: str | None = None) -> None:
        previous_visible_tab = self.current_tab()
        destinations = tuple(
            ImportDestination(
                name=tab,
                urls=tuple(
                    tile.url
                    for tile in self.cfg.tiles
                    if tile.tab_id == self.cfg.tab_ids[tab]
                ),
                hidden=tab in self.cfg.hidden_tabs,
            )
            for tab in self.cfg.tabs
        )
        dialog = UrlImportDialog(
            destinations=destinations,
            default_destination=default_tab or self.current_tab(),
            parent=self,
        )

        while dialog.exec() == QDialog.DialogCode.Accepted:
            destination = dialog.selected_destination()
            selections = tuple(
                sorted(dialog.selected_imports(), key=lambda item: item.source_line)
            )
            if destination not in self.cfg.tabs or not selections:
                return

            imported_tiles = [
                Tile(
                    name=selection.name,
                    url=selection.url,
                    tab=destination,
                    icon=None,
                    bg="#F5F6FA",
                    browser=None,
                    chrome_profile=None,
                    open_target="tab",
                    tab_id=self.cfg.tab_ids[destination],
                )
                for selection in selections
            ]
            candidate = _config_with_imported_tiles(self.cfg, imported_tiles)
            failure_category: RuntimeSaveFailureCategory | None = None
            try:
                candidate.save()
            except (OSError, ValueError) as exc:
                failure_category = _runtime_save_failure_category(exc)
            if failure_category is not None:
                record_breadcrumb(
                    "url_import_save_failed",
                    imported_count=len(imported_tiles),
                    failure_category=failure_category,
                )
                QMessageBox.critical(
                    self,
                    "Import URLs",
                    "Could not save the imported tiles. No tiles were imported.",
                )
                continue

            self.cfg = candidate
            self.rebuild()
            destination_hidden = destination in self.cfg.hidden_tabs
            if not destination_hidden:
                self._set_current_tab_by_name(destination)
            else:
                self._set_current_tab_by_name(previous_visible_tab)
            record_breadcrumb(
                "url_import_complete",
                imported_count=len(imported_tiles),
                destination_hidden=destination_hidden,
            )
            return

    def edit_tile(self, tile: Tile) -> None:
        dlg = TileEditorDialog(
            tabs=self.cfg.tabs,
            browsers=available_browsers(),
            icon_dir=ICON_DIR,
            fetch_favicon=fetch_favicon,
            tile=tile,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.data:
            data = dlg.data
            previous = _runtime_change_snapshot(self.cfg)
            restore_tab = self.current_tab()
            tile.name = cast(str, data["name"])
            tile.url = cast(str, data["url"])
            tile.icon = data["icon"]
            tile.tab = cast(str, data["tab"])
            tile.tab_id = self.cfg.tab_ids[tile.tab]
            tile.browser = data["browser"]
            tile.chrome_profile = data["chrome_profile"]
            tile.open_target = cast(str, data["open_target"])
            if not Main._save_runtime_change(
                self,
                previous,
                operation="tile_edit",
                restore_tab=restore_tab,
            ):
                return
            self.rebuild()
            self._set_current_tab_by_name(tile.tab)

    def duplicate_tile(self, tile: Tile) -> None:
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        new_tile = replace(tile)
        idx = self.cfg.tiles.index(tile)
        self.cfg.tiles.insert(idx + 1, new_tile)
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tile_duplicate",
            restore_tab=restore_tab,
        ):
            return
        self.rebuild()
        self._set_current_tab_by_name(tile.tab)

    def remove_tile(self, tile: Tile) -> None:
        ok = QMessageBox.warning(
            self,
            "Remove tile?",
            f"Remove “{tile.name}” from the launcher?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok == QMessageBox.StandardButton.Yes:
            previous = _runtime_change_snapshot(self.cfg)
            restore_tab = self.current_tab()
            self.cfg.tiles = [t for t in self.cfg.tiles if t is not tile]
            if not Main._save_runtime_change(
                self,
                previous,
                operation="tile_remove",
                restore_tab=restore_tab,
            ):
                return
            self.rebuild()

    def change_tile_tab(self, tile: Tile, new_tab: str) -> None:
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        tile.tab = new_tab
        tile.tab_id = self.cfg.tab_ids[new_tab]
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tile_move",
            restore_tab=restore_tab,
        ):
            return
        self.rebuild()
        self._set_current_tab_by_name(new_tab)

    def add_tab(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Tab", "Tab name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.cfg.tabs:
            QMessageBox.warning(
                self, "Tab exists", "A tab with that name exists already."
            )
            return
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        state = add_tab_to_order(
            _tab_order_state(self.cfg),
            name,
            blocked_ids=(self.cfg.workspace_id,),
        )
        _apply_tab_order_state(self.cfg, state)
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tab_add",
            restore_tab=restore_tab,
        ):
            return
        self.rebuild()
        self._set_current_tab_by_name(name)

    def rename_tab(self) -> None:
        current = self.current_tab()
        name, ok = QInputDialog.getText(self, "Rename Tab", "Tab name:", text=current)
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.cfg.tabs:
            QMessageBox.warning(
                self, "Tab exists", "A tab with that name exists already."
            )
            return
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        renamed_tab_id = self.cfg.tab_ids[current]
        state = rename_tab_in_order(_tab_order_state(self.cfg), current, name)
        _apply_tab_order_state(self.cfg, state)
        for t in self.cfg.tiles:
            if t.tab_id == renamed_tab_id:
                t.tab = name
        if current in self.cfg.hidden_tabs:
            hidx = self.cfg.hidden_tabs.index(current)
            self.cfg.hidden_tabs[hidx] = name
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tab_rename",
            restore_tab=restore_tab,
        ):
            return
        self.rebuild()
        self._set_current_tab_by_name(name)

    def delete_tab(self) -> None:
        current = self.current_tab()
        if len(self.cfg.tabs) == 1:
            QMessageBox.warning(self, "Not allowed", "At least one tab must exist.")
            record_breadcrumb("tab_action_blocked", action="delete", reason="last_tab")
            return
        ok = QMessageBox.question(
            self,
            "Delete Tab",
            f"Delete tab '{current}' and all its tiles?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        deleted_tab_id = self.cfg.tab_ids[current]
        state = delete_tab_from_order(_tab_order_state(self.cfg), current)
        _apply_tab_order_state(self.cfg, state)
        self.cfg.tiles = [t for t in self.cfg.tiles if t.tab_id != deleted_tab_id]
        self.cfg.hidden_tabs = [t for t in self.cfg.hidden_tabs if t != current]
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tab_delete",
            restore_tab=restore_tab,
        ):
            return
        self.rebuild()

    def toggle_current_tab_visibility(self) -> None:
        name = self.current_tab()
        hidden = name in self.cfg.hidden_tabs
        if not hidden and len(self._visible_tabs()) == 1:
            QMessageBox.warning(
                self, "Not allowed", "At least one tab must remain visible."
            )
            record_breadcrumb(
                "tab_action_blocked",
                action="hide",
                reason="last_visible",
            )
            return
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        if hidden:
            self.cfg.hidden_tabs.remove(name)
        else:
            self.cfg.hidden_tabs.append(name)
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tab_visibility_toggle",
            restore_tab=restore_tab,
        ):
            return
        self.rebuild()
        self._set_current_tab_by_name(name)
        record_breadcrumb(
            "tab_visibility_toggle_single",
            visible=name not in self.cfg.hidden_tabs,
        )

    def manage_tab_visibility(self) -> None:
        dlg = TabVisibilityDialog(self.cfg.tabs, self.cfg.hidden_tabs, self)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            hidden = dlg.result_hidden()
            if (
                len(hidden) == len(self.cfg.tabs)
                or len(set(hidden)) != len(hidden)
                or not set(hidden).issubset(self.cfg.tabs)
            ):
                QMessageBox.warning(
                    self,
                    "Not allowed",
                    "At least one tab must remain visible.",
                )
                continue
            break
        previous = _runtime_change_snapshot(self.cfg)
        restore_tab = self.current_tab()
        self.cfg.hidden_tabs = hidden
        if not Main._save_runtime_change(
            self,
            previous,
            operation="tab_visibility_manage",
            restore_tab=restore_tab,
        ):
            return
        self.rebuild()
        vis = self._visible_tabs()
        if vis:
            self._set_current_tab_by_name(vis[0])
        record_breadcrumb(
            "tab_visibility_apply",
            hidden_count=len(self.cfg.hidden_tabs),
            visible_count=len(self._visible_tabs()),
        )

    def _debug_raise(self) -> None:
        raise RuntimeError("Test exception")


class _RecoveryChoice(Enum):
    EXIT = auto()
    PRESERVE_AND_RESET = auto()


@dataclass(frozen=True, slots=True)
class _StartupReady:
    config: LauncherConfig = field(repr=False)


@dataclass(frozen=True, slots=True)
class _StartupExit:
    exit_code: int


_LOAD_FAILURE_MESSAGES: dict[ConfigLoadFailureCategory, str] = {
    ConfigLoadFailureCategory.FILE_READ_FAILURE: (
        "The saved configuration could not be read safely."
    ),
    ConfigLoadFailureCategory.SIZE_LIMIT_EXCEEDED: (
        "The saved configuration exceeds the 4 MiB parsing safety limit."
    ),
    ConfigLoadFailureCategory.INVALID_UTF8: (
        "The saved configuration is not valid UTF-8 text."
    ),
    ConfigLoadFailureCategory.MALFORMED_JSON: (
        "The saved configuration contains malformed JSON."
    ),
    ConfigLoadFailureCategory.NON_OBJECT_ROOT: (
        "The saved configuration does not contain a JSON object."
    ),
    ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE: (
        "The saved configuration is incompatible with the current format."
    ),
}

_RECOVERY_FAILURE_MESSAGES: dict[RecoveryFailureCategory, str] = {
    RecoveryFailureCategory.SOURCE_UNAVAILABLE: (
        "The saved configuration could not be safely reopened for preservation."
    ),
    RecoveryFailureCategory.SOURCE_CHANGED: (
        "The saved configuration changed after it was inspected. No reset was performed."
    ),
    RecoveryFailureCategory.RECOVERY_DIRECTORY_FAILURE: (
        "The private recovery location could not be prepared safely."
    ),
    RecoveryFailureCategory.RECOVERY_COPY_FAILURE: (
        "A complete recovery copy could not be written."
    ),
    RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE: (
        "The recovery copy could not be verified."
    ),
    RecoveryFailureCategory.RESET_FAILURE: (
        "The preserved configuration could not be atomically reset."
    ),
}


def _prompt_config_recovery(
    category: ConfigLoadFailureCategory,
) -> _RecoveryChoice:
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("DesktopTileLauncher")
    box.setText("The saved configuration could not be loaded.")
    box.setInformativeText(
        f"{_LOAD_FAILURE_MESSAGES[category]} "
        "Exit leaves it unchanged. Preserve and Reset first keeps an exact recovery copy."
    )
    exit_button = box.addButton("Exit", QMessageBox.ButtonRole.RejectRole)
    reset_button = box.addButton(
        "Preserve and Reset",
        QMessageBox.ButtonRole.DestructiveRole,
    )
    box.setDefaultButton(exit_button)
    box.setEscapeButton(exit_button)
    exit_button.setFocus()
    box.exec()
    if box.clickedButton() is reset_button:
        return _RecoveryChoice.PRESERVE_AND_RESET
    return _RecoveryChoice.EXIT


def _show_recovery_failure(category: RecoveryFailureCategory) -> None:
    QMessageBox.critical(
        None,
        "DesktopTileLauncher",
        f"{_RECOVERY_FAILURE_MESSAGES[category]} The application will exit.",
    )


def _show_migration_failure(error: ConfigurationMigrationError) -> None:
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("DesktopTileLauncher")
    box.setText(startup_notice_message(error.notice_category))
    exit_button = box.addButton(
        "Exit",
        QMessageBox.ButtonRole.RejectRole,
    )
    box.setDefaultButton(exit_button)
    box.setEscapeButton(exit_button)
    exit_button.setFocus()
    box.exec()


def _resolve_startup_configuration() -> _StartupReady | _StartupExit:
    try:
        config = LauncherConfig.load(
            on_existing_legacy=_guarded_existing_legacy_save,
        )
    except ConfigurationMigrationError as error:
        record_breadcrumb("config_migration_exit", **error.diagnostics)
        _show_migration_failure(error)
        return _StartupExit(1)
    except ConfigurationLoadError as error:
        category = error.category
        snapshot = error.snapshot
    else:
        return _StartupReady(config)

    record_breadcrumb(
        "config_recovery_required",
        **recovery_required_diagnostics(category),
    )
    if _prompt_config_recovery(category) is _RecoveryChoice.EXIT:
        record_breadcrumb(
            "config_recovery_exit",
            **recovery_exit_diagnostics(category),
        )
        return _StartupExit(0)

    config: LauncherConfig | None = None
    reset_payload: bytes | None = None
    try:
        config = LauncherConfig.first_run()
        reset_payload = config._serialized_payload()
    except ValueError:
        reset_payload = None
    if config is None or reset_payload is None:
        recovery = RecoveryFailed(RecoveryFailureCategory.RESET_FAILURE)
    else:
        recovery = preserve_and_reset(
            CFG_PATH,
            snapshot,
            reset_payload.decode("utf-8"),
        )
    if isinstance(recovery, RecoveryFailed):
        record_breadcrumb(
            "config_recovery_failed",
            **recovery_result_diagnostics(recovery),
        )
        _show_recovery_failure(recovery.category)
        return _StartupExit(1)

    record_breadcrumb(
        "config_recovery_succeeded",
        **recovery_result_diagnostics(recovery),
    )
    if config is None:
        return _StartupExit(1)
    return _StartupReady(config)


def main() -> int:
    app = QApplication(sys.argv)
    debug_scaffold.install_debug_scaffold(app, app_name="DesktopTileLauncher")
    startup = _resolve_startup_configuration()
    if isinstance(startup, _StartupExit):
        return startup.exit_code
    mw = Main(startup.config)
    mw.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
