# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol, TypeVar, cast


class _SyncableTextStream(Protocol):
    name: str

    def write(self, text: str, /) -> int: ...

    def flush(self) -> None: ...

    def fileno(self) -> int: ...


class _SyncableBinaryStream(Protocol):
    name: str

    def write(self, data: bytes, /) -> int: ...

    def flush(self) -> None: ...

    def fileno(self) -> int: ...


_StreamT = TypeVar("_StreamT", _SyncableTextStream, _SyncableBinaryStream)


def _write_and_sync(stream: _SyncableTextStream, text: str) -> None:
    stream.write(text)
    stream.flush()
    os.fsync(stream.fileno())


def _write_bytes_and_sync(stream: _SyncableBinaryStream, data: bytes) -> None:
    if stream.write(data) != len(data):
        raise OSError("short atomic byte write")
    stream.flush()
    os.fsync(stream.fileno())


def _atomic_write(
    path: Path,
    temporary: AbstractContextManager[_StreamT],
    write_and_sync: Callable[[_StreamT], None],
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    temporary_path: Path | None = None
    try:
        with temporary as stream:
            temporary_path = Path(stream.name)
            write_and_sync(stream)

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
    temporary = cast(
        AbstractContextManager[_SyncableTextStream],
        tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ),
    )

    def write_and_sync(stream: _SyncableTextStream) -> None:
        _write_and_sync(stream, text)

    _atomic_write(path, temporary, write_and_sync, before_replace=before_replace)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    """Replace *path* with exact bytes synced in a guarded sibling file."""
    temporary = cast(
        AbstractContextManager[_SyncableBinaryStream],
        tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ),
    )

    def write_and_sync(stream: _SyncableBinaryStream) -> None:
        _write_bytes_and_sync(stream, data)

    _atomic_write(path, temporary, write_and_sync, before_replace=before_replace)
