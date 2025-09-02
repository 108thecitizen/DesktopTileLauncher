from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from debug_scaffold import install_debug_scaffold
from tile_launcher import APP_STATE, WindowManager


def main() -> None:
    app = QApplication(sys.argv)
    QApplication.setQuitOnLastWindowClosed(False)
    install_debug_scaffold(app, app_name="DesktopTileLauncher")
    manager = WindowManager(app, APP_STATE)
    manager.open_initial_windows()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

