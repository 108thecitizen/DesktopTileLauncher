# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import pathlib
import tempfile

import pytest


def _resolved_env_path(name: str) -> pathlib.Path:
    return pathlib.Path(os.environ[name]).resolve()


@pytest.mark.unit
def test_unit_tests_use_repo_local_runtime_paths(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    runtime_root = (repo_root / ".pytest_cache" / "test-runtime").resolve()

    assert tmp_path.resolve().is_relative_to(runtime_root)  # nosec B101
    assert pathlib.Path(tempfile.gettempdir()).resolve().is_relative_to(runtime_root)  # nosec B101

    for name in (
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
    ):
        assert _resolved_env_path(name).is_relative_to(runtime_root)  # nosec B101
