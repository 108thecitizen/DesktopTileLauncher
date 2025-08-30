"""Debug scaffold for DesktopTileLauncher.

Provides unified logging for Qt and Python exceptions with a rotating file
handler and a modal error dialog for uncaught exceptions.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import traceback
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QtMsgType, qInstallMessageHandler, QMessageLogContext
from PySide6.QtWidgets import QApplication, QMessageBox

MAX_BYTES = 500 * 1024
BACKUPS = 5


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


def install_debug_scaffold(
    app: QApplication, app_name: str = "DesktopTileLauncher"
) -> None:
    """Install global exception and Qt log handlers."""
    app.setApplicationName(app_name)
    log_path = _log_dir(app_name) / "debug.log"

    handler = RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)

    def handle_exception(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        trace = "".join(traceback.format_exception(exc_type, exc, tb))
        root_logger.error("Uncaught exception:\n%s", trace)
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle(app_name)
        msg.setText(str(exc))
        msg.setDetailedText(trace)
        msg.exec()

    sys.excepthook = handle_exception

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
