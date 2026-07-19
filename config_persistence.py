# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class _SyncableTextStream(Protocol):
    def write(self, text: str, /) -> int: ...

    def flush(self) -> None: ...

    def fileno(self) -> int: ...


def _write_and_sync(stream: _SyncableTextStream, text: str) -> None:
    stream.write(text)
    stream.flush()
    os.fsync(stream.fileno())


def atomic_write_text(
    path: Path,
    text: str,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    """Replace *path* only after its complete text is synced in a sibling file.

    ``before_replace`` runs after the sibling file is fully written and synced,
    immediately before ``os.replace``. If the guard raises, this writer leaves
    the destination unreplaced and cleans up only its own temporary file.
    """
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            _write_and_sync(temporary, text)

        if before_replace is not None:
            before_replace()
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                # Preserve the original write or replace error if cleanup also fails.
                pass
