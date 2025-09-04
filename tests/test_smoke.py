import importlib
import importlib.util
from pathlib import Path


def _import_main_module():
    # Try common module names first (adjust this list if your module name differs)
    for name in (
        "desktoptilelauncher",
        "desktop_tile_launcher",
        "DesktopTileLauncher",
        "app",
        "main",
    ):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            pass

    # Fallback: try common entrypoint filenames at repo root
    repo_root = Path(__file__).resolve().parents[1]
    for filename in ("DesktopTileLauncher.py", "app.py", "main.py"):
        candidate = repo_root / filename
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("app_under_test", candidate)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            assert spec and spec.loader, f"Could not load spec for {candidate}"  # nosec B101

            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod

    raise AssertionError(
        "Smoke test could not import your app. "
        "Add your actual module name to the candidates list in tests/test_smoke.py."
    )


def test_app_imports_without_errors():
    mod = _import_main_module()
    assert mod is not None  # nosec B101

