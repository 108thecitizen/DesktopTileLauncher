from __future__ import annotations

import inspect
import sys
from typing import Type

from PySide6.QtWidgets import QApplication, QMainWindow
import tile_launcher  # your app's main window class lives here


def _find_main_window() -> Type[QMainWindow]:
    """Find the first QMainWindow subclass exported by tile_launcher."""
    for name in dir(tile_launcher):
        obj = getattr(tile_launcher, name)
        try:
            if inspect.isclass(obj) and issubclass(obj, QMainWindow):
                return obj  # first QMainWindow subclass found
        except Exception:
            continue
    raise RuntimeError("No QMainWindow subclass found in tile_launcher")


def main() -> int:
    app = QApplication(sys.argv)
    Win = _find_main_window()
    win = Win()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
