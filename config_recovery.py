# SPDX-License-Identifier: Apache-2.0
"""Bounded, Qt-free loading and explicit recovery for ``config.json``.

Normal parsing admits at most four MiB of encoded JSON.  Explicit recovery may
stream a larger regular file with bounded memory after the user has chosen to
preserve it.  The final source comparison and ``os.replace`` are adjacent, but
portable Python does not provide a kernel-level compare-and-swap for file
contents; a hostile same-user writer can therefore race that final boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, Generic, Literal, Protocol, TypeAlias, TypeVar, cast

from config_persistence import atomic_write_text

MAX_CONFIG_BYTES: Final = 4 * 1024 * 1024
_IO_CHUNK_BYTES: Final = 64 * 1024
_MAX_NAME_ATTEMPTS: Final = 32
_TOKEN_PATTERN: Final = re.compile(r"[0-9a-f]{32}")
_LEGACY_TILE_FIELDS: Final = frozenset(
    {
        "name",
        "url",
        "tab",
        "icon",
        "bg",
        "browser",
        "chrome_profile",
        "open_target",
    }
)

T = TypeVar("T")
DiagnosticValue: TypeAlias = str | bool | int


class ConfigLoadFailureCategory(StrEnum):
    """Expected, privacy-safe reasons an existing configuration was rejected."""

    FILE_READ_FAILURE = "file_read_failure"
    SIZE_LIMIT_EXCEEDED = "size_limit_exceeded"
    INVALID_UTF8 = "invalid_utf8"
    MALFORMED_JSON = "malformed_json"
    NON_OBJECT_ROOT = "non_object_root"
    LEGACY_CONSTRUCTION_FAILURE = "legacy_construction_failure"


class RecoveryFailureCategory(StrEnum):
    """Expected, privacy-safe stages at which explicit recovery can fail."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_CHANGED = "source_changed"
    RECOVERY_DIRECTORY_FAILURE = "recovery_directory_failure"
    RECOVERY_COPY_FAILURE = "recovery_copy_failure"
    RECOVERY_VERIFICATION_FAILURE = "recovery_verification_failure"
    RESET_FAILURE = "reset_failure"


class CandidateRetentionFailureCategory(StrEnum):
    """Privacy-safe reasons an owned failed candidate could not be retained."""

    SOURCE_CHANGED = "source_changed"
    RECOVERY_DIRECTORY_FAILURE = "recovery_directory_failure"
    CANDIDATE_COPY_FAILURE = "candidate_copy_failure"
    CANDIDATE_VERIFICATION_FAILURE = "candidate_verification_failure"


class LegacyConstructionFailure(Exception):
    """Signal an expected incompatible shape in the current legacy object."""

    def __init__(self) -> None:
        super().__init__(ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE.value)


def _is_legacy_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_legacy_field(
    mapping: dict[str, object],
    name: str,
    predicate: Callable[[object], bool],
) -> None:
    if name in mapping and not predicate(mapping[name]):
        raise LegacyConstructionFailure


def _validate_legacy_tile(value: object) -> None:
    if not isinstance(value, dict):
        raise LegacyConstructionFailure
    if not set(value).issubset(_LEGACY_TILE_FIELDS):
        raise LegacyConstructionFailure

    for name in ("name", "url"):
        if name not in value or not isinstance(value[name], str):
            raise LegacyConstructionFailure
    for name in ("tab", "bg"):
        if name in value and not isinstance(value[name], str):
            raise LegacyConstructionFailure
    for name in ("icon", "browser", "chrome_profile"):
        field_value = value.get(name)
        if (
            name in value
            and field_value is not None
            and not isinstance(field_value, str)
        ):
            raise LegacyConstructionFailure
    if "open_target" in value and value["open_target"] not in ("tab", "window"):
        raise LegacyConstructionFailure


def validate_legacy_mapping(mapping: dict[str, object]) -> None:
    """Reject legacy values that cannot safely construct the current runtime model."""

    _require_legacy_field(mapping, "title", lambda value: isinstance(value, str))
    _require_legacy_field(mapping, "columns", _is_legacy_int)
    _require_legacy_field(mapping, "auto_fit", lambda value: isinstance(value, bool))
    for name in ("window_x", "window_y", "window_w", "window_h"):
        _require_legacy_field(
            mapping,
            name,
            lambda value: value is None or _is_legacy_int(value),
        )
    for name in ("tabs", "hidden_tabs"):
        _require_legacy_field(
            mapping,
            name,
            lambda value: value is None or isinstance(value, list),
        )

    if "tiles" not in mapping:
        return
    tiles = mapping["tiles"]
    if not isinstance(tiles, list):
        raise LegacyConstructionFailure
    for tile in tiles:
        _validate_legacy_tile(tile)


@dataclass(frozen=True, slots=True, repr=False)
class _FileFingerprint:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True, repr=False)
class SourceSnapshot:
    """Private evidence recorded by a successful or rejected bounded raw load."""

    path_fingerprint: _FileFingerprint = field(repr=False)
    target_fingerprint: _FileFingerprint = field(repr=False)
    content_fingerprint: _FileFingerprint = field(repr=False)
    evidence_byte_count: int
    evidence_sha256: bytes = field(repr=False)
    evidence_is_complete: bool
    source_is_redirected: bool


@dataclass(frozen=True, slots=True)
class RawConfigLoaded:
    """A bounded JSON object and the exact source evidence used to parse it."""

    mapping: dict[str, object] = field(repr=False)
    source_bytes: bytes = field(repr=False)
    snapshot: SourceSnapshot = field(repr=False)


@dataclass(frozen=True, slots=True)
class ConfigMissing:
    """The expected configuration path did not exist."""


@dataclass(frozen=True, slots=True)
class ConfigLoaded(Generic[T]):
    """A configuration was parsed and constructed successfully."""

    value: T = field(repr=False)


@dataclass(frozen=True, slots=True)
class ConfigRecoveryRequired:
    """An existing configuration could not be safely constructed."""

    category: ConfigLoadFailureCategory
    snapshot: SourceSnapshot | None = field(default=None, repr=False)


ConfigLoadResult: TypeAlias = ConfigMissing | ConfigLoaded[T] | ConfigRecoveryRequired
RawConfigLoadResult: TypeAlias = (
    ConfigMissing | RawConfigLoaded | ConfigRecoveryRequired
)


class ConfigurationLoadError(Exception):
    """Category-only exception for legacy callers of ``LauncherConfig.load``."""

    def __init__(
        self,
        category: ConfigLoadFailureCategory,
        snapshot: SourceSnapshot | None,
    ) -> None:
        self.category = category
        self.snapshot = snapshot
        super().__init__(category.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(category={self.category.value!r})"


@dataclass(frozen=True, slots=True)
class RecoverySucceeded:
    """A reset succeeded after one permanent recovery path was published."""

    recovery_copy_count: int = 1
    reset_count: int = 1


@dataclass(frozen=True, slots=True)
class RecoveryFailed:
    """A failed recovery, counting permanent paths published before failure."""

    category: RecoveryFailureCategory
    recovery_copy_count: int = 0
    reset_count: int = 0


RecoveryResult: TypeAlias = RecoverySucceeded | RecoveryFailed


@dataclass(frozen=True, slots=True, repr=False)
class _OpenedSource:
    descriptor: int
    path_fingerprint: _FileFingerprint
    target_fingerprint: _FileFingerprint
    content_fingerprint: _FileFingerprint
    redirected: bool


@dataclass(frozen=True, slots=True, repr=False)
class _VerifiedCopy:
    path: Path = field(repr=False)
    byte_count: int
    sha256: bytes = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False, init=False)
class PreservedSource:
    """Opaque authority for one independently verified original recovery copy."""

    _source_path: Path = field(repr=False)
    _source_snapshot: SourceSnapshot = field(repr=False)
    _artifact_path: Path = field(repr=False)
    _artifact_fingerprint: _FileFingerprint = field(repr=False)
    _byte_count: int = field(repr=False)
    _sha256: bytes = field(repr=False)

    def __new__(cls) -> PreservedSource:
        raise TypeError("PreservedSource handles are created by preserve_source")


def _new_preserved_source(
    source_path: Path,
    source_snapshot: SourceSnapshot,
    artifact_path: Path,
    artifact_fingerprint: _FileFingerprint,
    byte_count: int,
    sha256: bytes,
) -> PreservedSource:
    preserved = object.__new__(PreservedSource)
    object.__setattr__(preserved, "_source_path", source_path)
    object.__setattr__(preserved, "_source_snapshot", source_snapshot)
    object.__setattr__(preserved, "_artifact_path", artifact_path)
    object.__setattr__(preserved, "_artifact_fingerprint", artifact_fingerprint)
    object.__setattr__(preserved, "_byte_count", byte_count)
    object.__setattr__(preserved, "_sha256", sha256)
    return preserved


@dataclass(frozen=True, slots=True)
class SourcePreserved:
    source: PreservedSource = field(repr=False)
    recovery_copy_count: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class SourcePreservationFailed:
    category: RecoveryFailureCategory
    recovery_copy_count: Literal[0, 1] = 0


PreserveSourceResult: TypeAlias = SourcePreserved | SourcePreservationFailed


@dataclass(frozen=True, slots=True)
class RecoveryVerificationSucceeded:
    pass


@dataclass(frozen=True, slots=True)
class RecoveryVerificationFailed:
    category: RecoveryFailureCategory


RecoveryVerificationResult: TypeAlias = (
    RecoveryVerificationSucceeded | RecoveryVerificationFailed
)


@dataclass(frozen=True, slots=True)
class PreservedBytesRead:
    data: bytes = field(repr=False)


PreservedBytesResult: TypeAlias = PreservedBytesRead | RecoveryVerificationFailed


@dataclass(frozen=True, slots=True)
class CandidateRetained:
    failed_candidate_copy_count: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class CandidateRetentionFailed:
    category: CandidateRetentionFailureCategory
    failed_candidate_copy_count: Literal[0, 1] = 0


CandidateRetentionResult: TypeAlias = CandidateRetained | CandidateRetentionFailed


class _FileSafetyFailure(Exception):
    pass


class _SourceChanged(Exception):
    pass


class _GuardFailure(Exception):
    def __init__(self, category: RecoveryFailureCategory) -> None:
        self.category = category
        super().__init__(category.value)


class _BinaryStream(Protocol):
    def write(self, data: bytes, /) -> int: ...

    def flush(self) -> None: ...

    def fileno(self) -> int: ...


def recovery_required_diagnostics(
    category: ConfigLoadFailureCategory,
) -> dict[str, DiagnosticValue]:
    return {"failure_category": category.value, "failure_count": 1}


def recovery_exit_diagnostics(
    category: ConfigLoadFailureCategory,
) -> dict[str, DiagnosticValue]:
    return {"failure_category": category.value, "exit_count": 1}


def recovery_result_diagnostics(result: RecoveryResult) -> dict[str, DiagnosticValue]:
    fields: dict[str, DiagnosticValue] = {
        "recovery_copy_count": result.recovery_copy_count,
        "reset_count": result.reset_count,
    }
    if isinstance(result, RecoveryFailed):
        fields["failure_category"] = result.category.value
    return fields


def _fingerprint(metadata: os.stat_result) -> _FileFingerprint:
    return _FileFingerprint(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (
        isinstance(attributes, int)
        and isinstance(reparse_flag, int)
        and bool(attributes & reparse_flag)
    )


def _require_regular(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise _FileSafetyFailure


def _open_flags(*, no_follow: bool) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    if no_follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _open_source(path: Path) -> _OpenedSource:
    path_metadata = path.lstat()
    redirected = stat.S_ISLNK(path_metadata.st_mode) or _is_reparse_point(path_metadata)
    if not redirected:
        _require_regular(path_metadata)

    target_metadata = path.stat()
    _require_regular(target_metadata)
    descriptor = os.open(path, _open_flags(no_follow=not redirected))
    try:
        opened_metadata = os.fstat(descriptor)
        _require_regular(opened_metadata)
        if not os.path.samestat(target_metadata, opened_metadata):
            raise _SourceChanged
        if _fingerprint(path.lstat()) != _fingerprint(path_metadata):
            raise _SourceChanged
    except Exception:
        os.close(descriptor)
        raise

    return _OpenedSource(
        descriptor=descriptor,
        path_fingerprint=_fingerprint(path_metadata),
        target_fingerprint=_fingerprint(target_metadata),
        content_fingerprint=_fingerprint(opened_metadata),
        redirected=redirected,
    )


def _read_bounded(descriptor: int, maximum: int) -> bytes:
    content = bytearray()
    admitted = maximum + 1
    while len(content) < admitted:
        chunk = os.read(descriptor, min(_IO_CHUNK_BYTES, admitted - len(content)))
        if not chunk:
            break
        content.extend(chunk)
    return bytes(content)


def _source_still_matches(path: Path, opened: _OpenedSource) -> bool:
    try:
        opened_metadata = os.fstat(opened.descriptor)
        target_metadata = path.stat()
        return (
            _fingerprint(opened_metadata) == opened.content_fingerprint
            and _fingerprint(path.lstat()) == opened.path_fingerprint
            and _fingerprint(target_metadata) == opened.target_fingerprint
            and os.path.samestat(target_metadata, opened_metadata)
        )
    except OSError:
        return False


def load_raw_config(
    path: Path,
    *,
    max_bytes: int = MAX_CONFIG_BYTES,
) -> RawConfigLoadResult:
    """Read one bounded JSON object and retain its exact source evidence."""

    if not 0 < max_bytes <= MAX_CONFIG_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {MAX_CONFIG_BYTES}")

    try:
        path.lstat()
    except FileNotFoundError:
        return ConfigMissing()
    except OSError:
        return ConfigRecoveryRequired(ConfigLoadFailureCategory.FILE_READ_FAILURE)

    try:
        opened = _open_source(path)
        try:
            raw = _read_bounded(opened.descriptor, max_bytes)
            stable = _source_still_matches(path, opened)
        finally:
            os.close(opened.descriptor)
    except (OSError, _FileSafetyFailure, _SourceChanged):
        return ConfigRecoveryRequired(ConfigLoadFailureCategory.FILE_READ_FAILURE)

    if not stable:
        return ConfigRecoveryRequired(ConfigLoadFailureCategory.FILE_READ_FAILURE)

    oversized = len(raw) > max_bytes
    snapshot = SourceSnapshot(
        path_fingerprint=opened.path_fingerprint,
        target_fingerprint=opened.target_fingerprint,
        content_fingerprint=opened.content_fingerprint,
        evidence_byte_count=len(raw),
        evidence_sha256=hashlib.sha256(raw).digest(),
        evidence_is_complete=not oversized,
        source_is_redirected=opened.redirected,
    )
    if oversized:
        return ConfigRecoveryRequired(
            ConfigLoadFailureCategory.SIZE_LIMIT_EXCEEDED,
            snapshot,
        )

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ConfigRecoveryRequired(ConfigLoadFailureCategory.INVALID_UTF8, snapshot)

    try:
        parsed = json.loads(decoded)
    except (ValueError, RecursionError):
        return ConfigRecoveryRequired(
            ConfigLoadFailureCategory.MALFORMED_JSON, snapshot
        )

    if not isinstance(parsed, dict):
        return ConfigRecoveryRequired(
            ConfigLoadFailureCategory.NON_OBJECT_ROOT, snapshot
        )

    return RawConfigLoaded(cast(dict[str, object], parsed), raw, snapshot)


def load_config(
    path: Path,
    constructor: Callable[[dict[str, object]], T],
    *,
    max_bytes: int = MAX_CONFIG_BYTES,
) -> ConfigLoadResult[T]:
    """Read and construct an existing configuration without modifying it."""

    raw_result = load_raw_config(path, max_bytes=max_bytes)
    if not isinstance(raw_result, RawConfigLoaded):
        return raw_result

    try:
        value = constructor(raw_result.mapping)
    except LegacyConstructionFailure:
        return ConfigRecoveryRequired(
            ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE,
            raw_result.snapshot,
        )
    return ConfigLoaded(value)


def _snapshot_matches_opened(
    snapshot: SourceSnapshot,
    opened: _OpenedSource,
    *,
    allow_redirected: bool,
) -> bool:
    return (
        snapshot.source_is_redirected == opened.redirected
        and (allow_redirected or not opened.redirected)
        and opened.path_fingerprint == snapshot.path_fingerprint
        and opened.target_fingerprint == snapshot.target_fingerprint
        and opened.content_fingerprint == snapshot.content_fingerprint
    )


def _read_evidence(descriptor: int, byte_count: int) -> bytes:
    evidence = bytearray()
    while len(evidence) < byte_count:
        chunk = os.read(
            descriptor,
            min(_IO_CHUNK_BYTES, byte_count - len(evidence)),
        )
        if not chunk:
            break
        evidence.extend(chunk)
    return bytes(evidence)


def _open_matching_source(
    path: Path,
    snapshot: SourceSnapshot,
    *,
    allow_redirected: bool = False,
) -> _OpenedSource:
    opened = _open_source(path)
    try:
        if not _snapshot_matches_opened(
            snapshot,
            opened,
            allow_redirected=allow_redirected,
        ):
            raise _SourceChanged
        evidence = _read_evidence(opened.descriptor, snapshot.evidence_byte_count)
        if len(evidence) != snapshot.evidence_byte_count:
            raise _SourceChanged
        if hashlib.sha256(evidence).digest() != snapshot.evidence_sha256:
            raise _SourceChanged
        if snapshot.evidence_is_complete and os.read(opened.descriptor, 1):
            raise _SourceChanged
        if not _source_still_matches(path, opened):
            raise _SourceChanged
        os.lseek(opened.descriptor, 0, os.SEEK_SET)
        return opened
    except Exception:
        os.close(opened.descriptor)
        raise


def _resolved_recovery_directory(config_path: Path) -> Path:
    config_parent = config_path.parent.resolve(strict=True)
    recovery_directory = config_path.parent / "recovery"
    try:
        recovery_directory.mkdir(mode=0o700)
    except FileExistsError:
        pass

    metadata = recovery_directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
    ):
        raise _FileSafetyFailure
    resolved = recovery_directory.resolve(strict=True)
    if resolved.parent != config_parent or resolved.name != "recovery":
        raise _FileSafetyFailure
    if os.name != "nt":
        recovery_directory.chmod(0o700)
        if stat.S_IMODE(recovery_directory.stat().st_mode) & 0o077:
            raise _FileSafetyFailure
    return recovery_directory


def _new_token() -> str:
    return secrets.token_hex(16)


def _safe_token() -> str:
    token = _new_token()
    if _TOKEN_PATTERN.fullmatch(token) is None:
        raise _FileSafetyFailure
    return token


def _allocate_staging_file(recovery_directory: Path) -> tuple[Path, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(_MAX_NAME_ATTEMPTS):
        path = recovery_directory / f".config-{_safe_token()}.partial"
        if path.parent != recovery_directory:
            raise _FileSafetyFailure
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            continue
        try:
            if os.name != "nt":
                path.chmod(0o600)
        except OSError:
            os.close(descriptor)
            _cleanup_staging(path)
            raise
        return path, descriptor
    raise OSError("recovery staging name allocation failed")


def _write_chunk(stream: _BinaryStream, chunk: bytes) -> None:
    if stream.write(chunk) != len(chunk):
        raise OSError("short recovery write")


def _flush_and_sync(stream: _BinaryStream) -> None:
    stream.flush()
    os.fsync(stream.fileno())


def _copy_source(
    source: _OpenedSource,
    source_path: Path,
    destination_descriptor: int,
) -> tuple[int, bytes]:
    digest = hashlib.sha256()
    byte_count = 0
    with os.fdopen(destination_descriptor, "wb") as destination:
        remaining = source.content_fingerprint.size
        while remaining:
            chunk = os.read(source.descriptor, min(_IO_CHUNK_BYTES, remaining))
            if not chunk:
                raise _SourceChanged
            _write_chunk(destination, chunk)
            digest.update(chunk)
            byte_count += len(chunk)
            remaining -= len(chunk)
        if os.read(source.descriptor, 1):
            raise _SourceChanged
        _flush_and_sync(destination)
    if not _source_still_matches(source_path, source):
        raise _SourceChanged
    return byte_count, digest.digest()


def _hash_stable_path(
    path: Path,
    *,
    allow_redirected: bool = False,
) -> tuple[_FileFingerprint, int, bytes]:
    opened = _open_source(path)
    try:
        if opened.redirected and not allow_redirected:
            raise _FileSafetyFailure
        digest = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(opened.descriptor, _IO_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
        if not _source_still_matches(path, opened):
            raise _SourceChanged
        return opened.content_fingerprint, byte_count, digest.digest()
    finally:
        os.close(opened.descriptor)


def _cleanup_staging(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _publish_verified_copy(
    staging_path: Path,
    recovery_directory: Path,
    expected_count: int,
    expected_digest: bytes,
    *,
    final_suffix: Literal["recovery", "failed-candidate"] = "recovery",
) -> _VerifiedCopy:
    for _attempt in range(_MAX_NAME_ATTEMPTS):
        final_path = recovery_directory / f"config-{_safe_token()}.{final_suffix}"
        if final_path.parent != recovery_directory:
            raise _FileSafetyFailure
        try:
            if os.name == "nt":
                os.rename(staging_path, final_path)
            else:
                os.link(staging_path, final_path)
        except FileExistsError:
            continue
        # Windows rename does not replace an existing destination.  POSIX uses
        # a hard link because its rename operation would replace one.
        return _VerifiedCopy(final_path, expected_count, expected_digest)
    raise OSError("recovery filename allocation failed")


def _verify_source_and_copy(
    config_path: Path,
    snapshot: SourceSnapshot,
    verified: _VerifiedCopy,
) -> _FileFingerprint:
    try:
        opened = _open_matching_source(config_path, snapshot)
        try:
            source_digest = hashlib.sha256()
            source_count = 0
            while True:
                chunk = os.read(opened.descriptor, _IO_CHUNK_BYTES)
                if not chunk:
                    break
                source_digest.update(chunk)
                source_count += len(chunk)
            if not _source_still_matches(config_path, opened):
                raise _SourceChanged
        finally:
            os.close(opened.descriptor)
        if (
            source_count != verified.byte_count
            or source_digest.digest() != verified.sha256
        ):
            raise _SourceChanged
    except (OSError, _FileSafetyFailure, _SourceChanged):
        raise _GuardFailure(RecoveryFailureCategory.SOURCE_CHANGED) from None

    try:
        copy_fingerprint, copy_count, copy_digest = _hash_stable_path(verified.path)
        if copy_count != verified.byte_count or copy_digest != verified.sha256:
            raise _GuardFailure(RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE)
    except _GuardFailure:
        raise
    except (OSError, _FileSafetyFailure, _SourceChanged):
        raise _GuardFailure(
            RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
        ) from None
    return copy_fingerprint


def _preserve_source_with_suffix(
    config_path: Path,
    snapshot: SourceSnapshot,
    *,
    final_suffix: Literal["recovery", "failed-candidate"],
) -> PreserveSourceResult:
    try:
        source = _open_matching_source(config_path, snapshot)
    except (OSError, _FileSafetyFailure, _SourceChanged):
        return SourcePreservationFailed(RecoveryFailureCategory.SOURCE_CHANGED)

    staging_path: Path | None = None
    try:
        copy_outcome: tuple[Path, Path, int, bytes] | SourcePreservationFailed
        copy_outcome = SourcePreservationFailed(
            RecoveryFailureCategory.RECOVERY_COPY_FAILURE,
        )
        try:
            try:
                recovery_directory = _resolved_recovery_directory(config_path)
            except (OSError, RuntimeError, _FileSafetyFailure):
                copy_outcome = SourcePreservationFailed(
                    RecoveryFailureCategory.RECOVERY_DIRECTORY_FAILURE
                )
            else:
                try:
                    staging_path, destination_descriptor = _allocate_staging_file(
                        recovery_directory
                    )
                    byte_count, digest = _copy_source(
                        source,
                        config_path,
                        destination_descriptor,
                    )
                except _SourceChanged:
                    copy_outcome = SourcePreservationFailed(
                        RecoveryFailureCategory.SOURCE_CHANGED
                    )
                except (OSError, _FileSafetyFailure):
                    copy_outcome = SourcePreservationFailed(
                        RecoveryFailureCategory.RECOVERY_COPY_FAILURE
                    )
                else:
                    copy_outcome = (
                        staging_path,
                        recovery_directory,
                        byte_count,
                        digest,
                    )
        finally:
            try:
                os.close(source.descriptor)
            except OSError:
                if isinstance(copy_outcome, tuple):
                    copy_outcome = SourcePreservationFailed(
                        RecoveryFailureCategory.RECOVERY_COPY_FAILURE
                    )

        if isinstance(copy_outcome, SourcePreservationFailed):
            return copy_outcome
        staged_path, recovery_directory, byte_count, digest = copy_outcome

        try:
            _stage_fingerprint, verified_count, verified_digest = _hash_stable_path(
                staged_path
            )
        except (OSError, _FileSafetyFailure, _SourceChanged):
            return SourcePreservationFailed(
                RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
            )
        if verified_count != byte_count or verified_digest != digest:
            return SourcePreservationFailed(
                RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
            )

        try:
            verified = _publish_verified_copy(
                staged_path,
                recovery_directory,
                byte_count,
                digest,
                final_suffix=final_suffix,
            )
        except (OSError, _FileSafetyFailure, _SourceChanged):
            return SourcePreservationFailed(
                RecoveryFailureCategory.RECOVERY_COPY_FAILURE
            )

        _cleanup_staging(staged_path)
        staging_path = None

        try:
            artifact_fingerprint = _verify_source_and_copy(
                config_path,
                snapshot,
                verified,
            )
        except _GuardFailure as failure:
            return SourcePreservationFailed(
                failure.category,
                recovery_copy_count=1,
            )

        preserved = _new_preserved_source(
            config_path,
            snapshot,
            verified.path,
            artifact_fingerprint,
            verified.byte_count,
            verified.sha256,
        )
        return SourcePreserved(preserved)
    finally:
        _cleanup_staging(staging_path)


def preserve_source(
    config_path: Path,
    snapshot: SourceSnapshot | None,
) -> PreserveSourceResult:
    """Publish and independently verify one permanent copy of an original source."""

    if snapshot is None or snapshot.source_is_redirected:
        return SourcePreservationFailed(RecoveryFailureCategory.SOURCE_UNAVAILABLE)
    return _preserve_source_with_suffix(
        config_path,
        snapshot,
        final_suffix="recovery",
    )


def _verified_copy(preserved: PreservedSource) -> _VerifiedCopy:
    return _VerifiedCopy(
        preserved._artifact_path,
        preserved._byte_count,
        preserved._sha256,
    )


def reverify_preserved_source(
    preserved: PreservedSource,
) -> RecoveryVerificationResult:
    """Reverify the original live source first, then its permanent recovery copy."""

    try:
        artifact_fingerprint = _verify_source_and_copy(
            preserved._source_path,
            preserved._source_snapshot,
            _verified_copy(preserved),
        )
        if artifact_fingerprint != preserved._artifact_fingerprint:
            raise _GuardFailure(RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE)
    except _GuardFailure as failure:
        return RecoveryVerificationFailed(failure.category)
    return RecoveryVerificationSucceeded()


def verify_preserved_artifact(
    preserved: PreservedSource,
) -> RecoveryVerificationResult:
    """Reverify a permanent recovery artifact without consulting the replaced source."""

    try:
        fingerprint, byte_count, digest = _hash_stable_path(preserved._artifact_path)
    except (OSError, _FileSafetyFailure, _SourceChanged):
        return RecoveryVerificationFailed(
            RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
        )
    if (
        fingerprint != preserved._artifact_fingerprint
        or byte_count != preserved._byte_count
        or digest != preserved._sha256
    ):
        return RecoveryVerificationFailed(
            RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
        )
    return RecoveryVerificationSucceeded()


def read_preserved_bytes(preserved: PreservedSource) -> PreservedBytesResult:
    """Read exact rollback bytes from a stable, independently verified artifact."""

    if preserved._byte_count > MAX_CONFIG_BYTES:
        return RecoveryVerificationFailed(
            RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
        )

    try:
        opened = _open_source(preserved._artifact_path)
        try:
            if (
                opened.redirected
                or opened.content_fingerprint != preserved._artifact_fingerprint
            ):
                raise _SourceChanged
            data = _read_evidence(opened.descriptor, preserved._byte_count)
            if len(data) != preserved._byte_count or os.read(opened.descriptor, 1):
                raise _SourceChanged
            if not _source_still_matches(preserved._artifact_path, opened):
                raise _SourceChanged
        finally:
            os.close(opened.descriptor)
    except (OSError, _FileSafetyFailure, _SourceChanged):
        return RecoveryVerificationFailed(
            RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
        )

    if hashlib.sha256(data).digest() != preserved._sha256:
        return RecoveryVerificationFailed(
            RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
        )
    return PreservedBytesRead(data)


def reverify_source_bytes(
    path: Path,
    snapshot: SourceSnapshot,
    expected_bytes: bytes,
) -> RecoveryVerificationResult:
    """Prove that a path still contains the complete bytes represented by a snapshot.

    Unchanged redirected sources are accepted for guarded legacy normalization writes.
    Preservation itself continues to reject every redirected source.
    """

    if (
        not snapshot.evidence_is_complete
        or len(expected_bytes) != snapshot.evidence_byte_count
        or hashlib.sha256(expected_bytes).digest() != snapshot.evidence_sha256
    ):
        return RecoveryVerificationFailed(RecoveryFailureCategory.SOURCE_CHANGED)

    try:
        opened = _open_matching_source(path, snapshot, allow_redirected=True)
        try:
            actual = _read_evidence(opened.descriptor, len(expected_bytes))
            if actual != expected_bytes or os.read(opened.descriptor, 1):
                raise _SourceChanged
            if not _source_still_matches(path, opened):
                raise _SourceChanged
        finally:
            os.close(opened.descriptor)
    except (OSError, _FileSafetyFailure, _SourceChanged):
        return RecoveryVerificationFailed(RecoveryFailureCategory.SOURCE_CHANGED)
    return RecoveryVerificationSucceeded()


def _candidate_failure_category(
    category: RecoveryFailureCategory,
) -> CandidateRetentionFailureCategory:
    if category in (
        RecoveryFailureCategory.SOURCE_UNAVAILABLE,
        RecoveryFailureCategory.SOURCE_CHANGED,
    ):
        return CandidateRetentionFailureCategory.SOURCE_CHANGED
    if category is RecoveryFailureCategory.RECOVERY_DIRECTORY_FAILURE:
        return CandidateRetentionFailureCategory.RECOVERY_DIRECTORY_FAILURE
    if category is RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE:
        return CandidateRetentionFailureCategory.CANDIDATE_VERIFICATION_FAILURE
    return CandidateRetentionFailureCategory.CANDIDATE_COPY_FAILURE


def retain_failed_candidate(
    config_path: Path,
    candidate_snapshot: SourceSnapshot,
    expected_candidate_bytes: bytes,
) -> CandidateRetentionResult:
    """Retain only the exact failed candidate proven to belong to this transaction."""

    if candidate_snapshot.source_is_redirected or isinstance(
        reverify_source_bytes(
            config_path,
            candidate_snapshot,
            expected_candidate_bytes,
        ),
        RecoveryVerificationFailed,
    ):
        return CandidateRetentionFailed(
            CandidateRetentionFailureCategory.SOURCE_CHANGED
        )

    retained = _preserve_source_with_suffix(
        config_path,
        candidate_snapshot,
        final_suffix="failed-candidate",
    )
    if isinstance(retained, SourcePreservationFailed):
        return CandidateRetentionFailed(
            _candidate_failure_category(retained.category),
            failed_candidate_copy_count=retained.recovery_copy_count,
        )
    return CandidateRetained()


def preserve_and_reset(
    config_path: Path,
    snapshot: SourceSnapshot | None,
    replacement_text: str,
) -> RecoveryResult:
    """Preserve the detected source, verify it, then atomically install reset text."""

    preservation = preserve_source(config_path, snapshot)
    if isinstance(preservation, SourcePreservationFailed):
        return RecoveryFailed(
            preservation.category,
            recovery_copy_count=preservation.recovery_copy_count,
        )
    preserved = preservation.source

    def guard() -> None:
        verification = reverify_preserved_source(preserved)
        if isinstance(verification, RecoveryVerificationFailed):
            raise _GuardFailure(verification.category)

    try:
        atomic_write_text(config_path, replacement_text, before_replace=guard)
    except _GuardFailure as failure:
        return RecoveryFailed(
            failure.category,
            recovery_copy_count=1,
        )
    except OSError:
        return RecoveryFailed(
            RecoveryFailureCategory.RESET_FAILURE,
            recovery_copy_count=1,
        )
    return RecoverySucceeded()
