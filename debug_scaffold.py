"""Debug and crash handling utilities for DesktopTileLauncher.

This module provides structured JSON logging, a lightweight breadcrumb
ring buffer and helpers for gathering crash context and creating crash
bundles.  The functions are intentionally conservative so that unit
tests can import the module even when PySide6/Qt is not available.
"""

from __future__ import annotations

from collections import deque
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import signal
import subprocess
import sys
import threading
import traceback
import urllib.parse
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Deque, Dict, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PySide6.QtCore import QtMsgType, QMessageLogContext, qInstallMessageHandler
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
    )
else:  # runtime import guarded for environments without PySide6
    try:  # pragma: no cover - best effort
        from PySide6.QtCore import QtMsgType, QMessageLogContext, qInstallMessageHandler
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QHBoxLayout,
            QLabel,
            QMessageBox,
            QPushButton,
            QTextEdit,
            QVBoxLayout,
        )
    except Exception:  # pragma: no cover - headless environments
        QtMsgType = QMessageLogContext = QApplication = QDialog = QHBoxLayout = (
            QLabel
        ) = QMessageBox = QPushButton = QTextEdit = QVBoxLayout = Any  # type: ignore[misc]

        def qInstallMessageHandler(*_: Any, **__: Any) -> None:  # type: ignore[misc]
            return


# Logging configuration ----------------------------------------------------
LOG_MAX_BYTES = 1_048_576  # 1 MB
LOG_BACKUPS = 5
BREADCRUMB_LIMIT = 100

# ring buffer for recent breadcrumbs
_breadcrumbs: Deque[dict[str, Any]] = deque(maxlen=BREADCRUMB_LIMIT)

# Last command executed to launch a browser.  Populated by tile_launcher.
last_launch_command: str | None = None


class JsonFormatter(logging.Formatter):
    """Minimal JSON line formatter."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: Dict[str, Any] = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }

        msg = record.getMessage()
        if msg:
            payload["message"] = msg

        for key, value in record.__dict__.items():
            if key in {
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "process",
                "processName",
                "message",
            }:
                continue
            payload[key] = value

        if record.exc_info:
            exc_type, exc, tb = record.exc_info
            payload["exc_type"] = getattr(exc_type, "__name__", str(exc_type))
            payload["exc_msg"] = str(exc)
            payload["trace"] = "".join(traceback.format_exception(exc_type, exc, tb))

        return json.dumps(payload, ensure_ascii=False)


def record_breadcrumb(event: str, **fields: Any) -> None:
    """Add a small breadcrumb to the ring buffer and log at DEBUG."""

    entry: Dict[str, Any] = {"ts": datetime.utcnow().isoformat(), "event": event}
    entry.update(fields)
    _breadcrumbs.append(entry)
    logging.getLogger("breadcrumb").debug("", extra=entry)


def get_breadcrumbs() -> list[dict[str, Any]]:
    """Return a copy of the current breadcrumb ring."""

    return list(_breadcrumbs)


SENSITIVE_KEYS = {"token", "code", "session", "auth", "key", "password"}


def sanitize_url(url: str) -> str:
    """Strip credentials and sensitive query parameters from *url*."""

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return url
    netloc = parsed.netloc.split("@")[-1]
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [(k, "REDACTED" if k.lower() in SENSITIVE_KEYS else v) for k, v in query]
    new_query = urllib.parse.urlencode(redacted)
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, new_query, parsed.fragment)
    )


def _log_dir(app_name: str) -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = base / app_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def collect_runtime_context(app: QApplication | None) -> dict[str, Any]:
    """Gather a snapshot of the runtime environment."""

    ctx: dict[str, Any] = {
        "app_name": app.applicationName() if app else "DesktopTileLauncher",
        "os": {
            "name": platform.system(),
            "release": platform.release(),
        },
        "python": platform.python_version(),
    }

    version_file = Path("version_info.txt")
    if version_file.exists():
        ctx["version"] = version_file.read_text(encoding="utf-8").strip()

    try:
        import importlib

        pyside6 = importlib.import_module("PySide6")
        ctx["pyside6"] = getattr(pyside6, "__version__", "unknown")
        qtcore = importlib.import_module("PySide6.QtCore")
        ctx["qt"] = getattr(qtcore, "qVersion")()
    except Exception:  # pragma: no cover - PySide6 may be absent
        pass

    if app:
        try:
            screen = app.primaryScreen()
            if screen:
                geom = screen.geometry()
                ctx["screen"] = {
                    "width": geom.width(),
                    "height": geom.height(),
                    "dpr": screen.devicePixelRatio(),
                }
        except Exception:  # pragma: no cover - best effort
            pass

    try:
        from tile_launcher import available_browsers

        ctx["available_browsers"] = available_browsers()
    except Exception:  # pragma: no cover - import cycle in tests
        ctx["available_browsers"] = []

    try:
        browser = webbrowser.get()
        ctx["default_browser"] = getattr(browser, "name", None)
    except Exception:  # pragma: no cover
        ctx["default_browser"] = None

    ctx["last_launch_command"] = last_launch_command
    ctx["breadcrumbs"] = get_breadcrumbs()[-20:]
    return ctx


def create_crash_bundle(log_dir: Path, context: dict[str, Any]) -> Path:
    """Zip logs and *context* into a timestamped crash bundle."""

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    bundle = log_dir / f"crash-{ts}.zip"
    crash_json = log_dir / "crash.json"
    crash_json.write_text(json.dumps(context, indent=2), encoding="utf-8")
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in log_dir.glob("debug.log*"):
            zf.write(path, path.name)
        fh_path = log_dir / "faulthandler.log"
        if fh_path.exists():
            zf.write(fh_path, fh_path.name)
        zf.write(crash_json, crash_json.name)
    return bundle


class CrashDialog(QDialog):  # pragma: no cover - GUI code
    """Simple crash dialog with helpful actions."""

    def __init__(
        self,
        app: QApplication,
        app_name: str,
        exc: BaseException,
        context: dict[str, Any],
        log_dir: Path,
    ) -> None:
        super().__init__()
        self._context = context
        self._log_dir = log_dir
        self.setWindowTitle(app_name)

        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        summary = str(exc)

        logging.getLogger(__name__).error(
            "Uncaught exception",
            extra={
                "event": "uncaught_exception",
                "exc_type": type(exc).__name__,
                "exc_msg": summary,
                "trace": trace,
                "context": context,
            },
        )

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(summary))

        self._details = QTextEdit(json.dumps(context, indent=2)[: 64 * 1024])
        self._details.setReadOnly(True)
        self._details.setVisible(False)
        layout.addWidget(self._details)

        toggle = QPushButton("Technical details")
        toggle.setCheckable(True)
        toggle.toggled.connect(self._details.setVisible)
        layout.addWidget(toggle)

        buttons = QHBoxLayout()
        copy_btn = QPushButton("Copy Details")
        copy_btn.clicked.connect(self.copy_details)
        buttons.addWidget(copy_btn)

        open_btn = QPushButton("Open Log Folder")
        open_btn.clicked.connect(self.open_logs)
        buttons.addWidget(open_btn)

        bundle_btn = QPushButton("Create Crash Bundle")
        bundle_btn.clicked.connect(self.create_bundle)
        buttons.addWidget(bundle_btn)

        report_btn = QPushButton("Report Bug")
        report_btn.clicked.connect(self.report_bug)
        buttons.addWidget(report_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)

        layout.addLayout(buttons)

    # ---- button handlers -------------------------------------------------
    def copy_details(self) -> None:
        QApplication.clipboard().setText(
            json.dumps(self._context, indent=2, ensure_ascii=False)
        )

    def open_logs(self) -> None:
        path = str(self._log_dir)
        if sys.platform.startswith("win"):
            os.startfile(path)  # nosec B605 - open local folder
        elif sys.platform == "darwin":
            subprocess.call(["open", path])  # nosec B603
        else:
            subprocess.call(["xdg-open", path])  # nosec B603

    def create_bundle(self) -> None:
        bundle = create_crash_bundle(self._log_dir, self._context)
        QMessageBox.information(self, "Crash bundle", f"Saved to {bundle}")

    def report_bug(self) -> None:
        title = urllib.parse.quote_plus("Crash report")
        body = urllib.parse.quote_plus("Please paste crash details here.")
        webbrowser.open(
            "https://github.com/108thecitizen/DesktopTileLauncher/issues/new?"
            f"title={title}&body={body}"
        )


def install_debug_scaffold(
    app: QApplication, app_name: str = "DesktopTileLauncher"
) -> None:
    """Install global exception hooks and Qt message handler."""

    app.setApplicationName(app_name)
    log_dir = _log_dir(app_name)
    log_path = log_dir / "debug.log"

    handler = RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)

    fh_path = log_dir / "faulthandler.log"
    fh_file = open(fh_path, "a", encoding="utf-8")
# Enable faulthandler to write tracebacks on fatal errors (best-effort).
try:
    faulthandler.enable(fh_file)
except Exception:
    # In some frozen builds or limited environments, faulthandler may not be fully available.
    pass

# Optionally register a user-triggered signal (if supported) to dump traces on demand.
for _sig in ("SIGUSR1", "SIGUSR2", "SIGBREAK"):
    signum = getattr(signal, _sig, None)
    if signum is not None and hasattr(faulthandler, "register"):
        try:
            faulthandler.register(signum, fh_file)  # type: ignore[attr-defined]
        except Exception:
            pass


    def _fatal_marker(signum: int, _frame: Any) -> None:
        root_logger.error(
            "fatal_signal", extra={"event": "fatal_signal", "signal": signum}
        )

for _sig in ("SIGSEGV", "SIGABRT"):
    signum = getattr(signal, _sig, None)
    if signum is not None:
        try:
            signal.signal(signum, _fatal_marker)
        except Exception:
            pass

    def handle_exception(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        context = collect_runtime_context(app)
        dlg = CrashDialog(app, app_name, exc, context, log_dir)
        dlg.exec()

    sys.excepthook = handle_exception

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        exc = args.exc_value or RuntimeError("Uncaught thread exception")
        handle_exception(args.exc_type, exc, args.exc_traceback)

    threading.excepthook = thread_hook

    def unraisable_hook(args: sys.UnraisableHookArgs) -> None:
        root_logger.warning(
            "Unraisable exception: %s", args.exc_value, extra={"event": "unraisable"}
        )

    sys.unraisablehook = unraisable_hook

    if qInstallMessageHandler is not None:

        def qt_message_handler(
            mode: QtMsgType, context: QMessageLogContext, message: str
        ) -> None:
            level_map = {
                QtMsgType.QtDebugMsg: logging.DEBUG,
                QtMsgType.QtInfoMsg: logging.INFO,
                QtMsgType.QtWarningMsg: logging.WARNING,
                QtMsgType.QtCriticalMsg: logging.ERROR,
                QtMsgType.QtFatalMsg: logging.CRITICAL,
            }
            root_logger.log(level_map.get(mode, logging.INFO), message)
            if mode == QtMsgType.QtFatalMsg:
                raise SystemExit(message)

        qInstallMessageHandler(qt_message_handler)

    record_breadcrumb("app_start")
