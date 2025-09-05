# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import zipfile

import pytest

from debug_scaffold import create_crash_bundle


@pytest.mark.unit
def test_create_crash_bundle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log_dir = tmp_path
    (log_dir / "debug.log").write_text("log")
    context = {"foo": "bar"}
    bundle = create_crash_bundle(log_dir, context)
    assert bundle.exists()  # nosec B101

    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
        assert "debug.log" in names  # nosec B101

        assert "crash.json" in names  # nosec B101

        crash_data = json.loads(zf.read("crash.json").decode("utf-8"))
        assert crash_data["foo"] == "bar"  # nosec B101
