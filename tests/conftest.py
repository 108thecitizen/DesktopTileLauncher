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

import ctypes
import os
import pathlib
import sys
import tempfile

import pytest  # noqa: F401  # imported for pytest hooks below

# ---------- Test-only path sandbox ----------
# Keep application config, logs, caches, and temp files out of the real user profile.
ROOT_PATH = pathlib.Path(__file__).resolve().parents[1]
TEST_RUNTIME_ROOT = ROOT_PATH / ".pytest_cache" / "test-runtime"
TEST_TEMP_ROOT = TEST_RUNTIME_ROOT / "temp"

for path in (
    TEST_RUNTIME_ROOT,
    TEST_TEMP_ROOT,
    TEST_RUNTIME_ROOT / "appdata",
    TEST_RUNTIME_ROOT / "localappdata",
    TEST_RUNTIME_ROOT / "xdg-config",
    TEST_RUNTIME_ROOT / "xdg-state",
    TEST_RUNTIME_ROOT / "xdg-cache",
):
    path.mkdir(parents=True, exist_ok=True)

_TEST_ENV_PATHS = {
    "APPDATA": TEST_RUNTIME_ROOT / "appdata",
    "LOCALAPPDATA": TEST_RUNTIME_ROOT / "localappdata",
    "TEMP": TEST_TEMP_ROOT,
    "TMP": TEST_TEMP_ROOT,
    "TMPDIR": TEST_TEMP_ROOT,
    "XDG_CONFIG_HOME": TEST_RUNTIME_ROOT / "xdg-config",
    "XDG_STATE_HOME": TEST_RUNTIME_ROOT / "xdg-state",
    "XDG_CACHE_HOME": TEST_RUNTIME_ROOT / "xdg-cache",
}
for name, path in _TEST_ENV_PATHS.items():
    os.environ[name] = str(path)

# tempfile caches its chosen temp dir, so force it to observe the test env above.
tempfile.tempdir = None

# ---------- Headless-safe Qt defaults ----------
# Keep your original setting and add a software GL fallback.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

# Provide a writable runtime dir with correct perms so Qt doesn't complain.
try:
    uid = os.getuid()  # not present on Windows; handled below
except AttributeError:
    uid = 0
_xdg = TEST_TEMP_ROOT / f"xdg-runtime-{uid}"
try:
    _xdg.mkdir(parents=True, exist_ok=True)
    # Some filesystems (e.g., Windows mounts) may not support chmod; ignore if it fails.
    _xdg.chmod(0o700)
except Exception:
    pass
os.environ["XDG_RUNTIME_DIR"] = str(_xdg)

# ---------- Preserve your original sys.path tweak ----------
ROOT = str(ROOT_PATH)
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
