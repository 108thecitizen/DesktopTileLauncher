# SPDX-License-Identifier: Apache-2.0
"""
Pytest bootstrap for headless/CI runs.

- Preserves existing behavior:
  * QT_QPA_PLATFORM=offscreen (don't require a display)
  * Add repo root to sys.path for imports

- Adds:
  * QT_OPENGL=software to avoid libGL/OpenGL driver lookups in headless CI
  * A safe XDG_RUNTIME_DIR with 0700 perms (Qt checks this)
  * Optional marker 'requires_opengl' you can use to skip GL-only tests when libGL isn't available
"""

import os
import sys
import tempfile
import pathlib
import ctypes
import pytest  # noqa: F401  # imported for pytest hooks below

# ---------- Headless-safe Qt defaults ----------
# Keep your original setting and add a software GL fallback.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

# Provide a writable runtime dir with correct perms so Qt doesn't complain.
try:
    uid = os.getuid()  # not present on Windows; handled below
except AttributeError:
    uid = 0
_xdg = pathlib.Path(tempfile.gettempdir()) / f"xdg-runtime-{uid}"
try:
    _xdg.mkdir(parents=True, exist_ok=True)
    # Some filesystems (e.g., Windows mounts) may not support chmod; ignore if it fails.
    _xdg.chmod(0o700)
except Exception:
    pass
os.environ.setdefault("XDG_RUNTIME_DIR", str(_xdg))

# ---------- Preserve your original sys.path tweak ----------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------- Optional: mark and skip GL-only tests in headless CI ----------
def _has_libgl() -> bool:
    """Detect presence of the OpenGL runtime."""
    try:
        ctypes.CDLL("libGL.so.1")
        return True
    except OSError:
        return False

def pytest_configure(config):
    # Register a marker so pytest doesn't warn about it if/when you use it.
    config.addinivalue_line(
        "markers",
        "requires_opengl: test depends on an OpenGL runtime (libGL); skip in headless CI",
    )

def pytest_runtest_setup(item):
    # Only skip tests you explicitly mark; nothing else is affected.
    if "requires_opengl" in item.keywords and not _has_libgl():
        pytest.skip("OpenGL runtime (libGL.so.1) not present in this environment")
