# SPDX-License-Identifier: Apache-2.0
"""Qt-free configuration migration engine and guarded transaction coordinator.

The pure engine is isolated from the filesystem.  The coordinator preserves the
source before invoking it, proves ownership before each replacement, and either
commits a verified candidate or restores the exact preserved bytes.  Callback
exceptions are reduced to fixed categories; their objects and text are never kept.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, Generic, Protocol, TypeAlias, TypeVar, cast

from config_persistence import atomic_write_bytes, atomic_write_text
from config_recovery import (
    MAX_CONFIG_BYTES,
    CandidateRetained,
    CandidateRetentionFailed,
    CandidateRetentionFailureCategory,
    ConfigLoadFailureCategory,
    ConfigMissing,
    ConfigRecoveryRequired,
    LegacyConstructionFailure,
    PreservedBytesRead,
    RawConfigLoaded,
    RecoveryFailureCategory,
    RecoveryVerificationFailed,
    SourcePreservationFailed,
    load_raw_config,
    preserve_source,
    read_preserved_bytes,
    retain_failed_candidate,
    reverify_preserved_source,
    reverify_source_bytes,
    verify_preserved_artifact,
)

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
DiagnosticValue: TypeAlias = str | int
T = TypeVar("T")

_STEP_NAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}\Z", re.ASCII)


class VersionRejectionCategory(StrEnum):
    """Privacy-safe reasons a source version cannot enter migration."""

    MALFORMED_VERSION = "malformed_version"
    UNSUPPORTED_OLDER = "unsupported_older"
    UNSUPPORTED_NEWER = "unsupported_newer"


class RegistryRejectionCategory(StrEnum):
    """Developer-controlled migration registry defects."""

    INVALID_BOUNDS = "invalid_bounds"
    INVALID_STEP_ENDPOINT = "invalid_step_endpoint"
    DUPLICATE_STEP_SOURCE = "duplicate_step_source"
    STEP_GAP = "step_gap"
    UNSAFE_STEP_NAME = "unsafe_step_name"
    DUPLICATE_STEP_NAME = "duplicate_step_name"
    INVALID_VALIDATOR_VERSION = "invalid_validator_version"
    DUPLICATE_VALIDATOR_VERSION = "duplicate_validator_version"
    MISSING_VALIDATOR = "missing_validator"


class PureExecutionRejectionCategory(StrEnum):
    """Expected callback decisions that reject a document or step."""

    SOURCE_VALIDATION_FAILURE = "source_validation_failure"
    STEP_REJECTION = "step_rejection"
    INTERMEDIATE_VALIDATION_FAILURE = "intermediate_validation_failure"
    TARGET_VALIDATION_FAILURE = "target_validation_failure"


class PureEngineFailureCategory(StrEnum):
    """Curated failures in engine-owned, deterministic operations."""

    JSON_DETACHMENT_FAILURE = "json_detachment_failure"
    SERIALIZATION_FAILURE = "serialization_failure"
    CANDIDATE_SIZE_LIMIT_EXCEEDED = "candidate_size_limit_exceeded"


class PureEngineDefectCategory(StrEnum):
    """Sanitized violations of a migration callback contract."""

    CALLBACK_EXCEPTION = "callback_exception"
    INVALID_CALLBACK_RESULT = "invalid_callback_result"
    INVALID_STEP_OUTPUT = "invalid_step_output"
    UNEXPECTED_TARGET_VERSION = "unexpected_target_version"


class PureExecutionStage(StrEnum):
    """Safe stage names used by pure outcomes and diagnostics."""

    SOURCE_VALIDATION = "source_validation"
    STEP = "step"
    INTERMEDIATE_VALIDATION = "intermediate_validation"
    TARGET_VALIDATION = "target_validation"
    SERIALIZATION = "serialization"


class MigrationStartupRoute(StrEnum):
    """Qt-free routing foundation for a later startup coordinator."""

    LEGACY = "legacy"
    CURRENT = "current"
    MIGRATION_REQUIRED = "migration_required"
    MIGRATED = "migrated"
    EXIT_ONLY = "exit_only"


@dataclass(frozen=True, slots=True)
class ValidationAccepted:
    """A version validator accepted its detached document."""


@dataclass(frozen=True, slots=True)
class ValidationRejected:
    """A version validator deliberately rejected its detached document."""


ValidationDecision: TypeAlias = ValidationAccepted | ValidationRejected


@dataclass(frozen=True, slots=True)
class StepApplied:
    """A step produced a candidate for its declared target version."""

    document: Mapping[str, JsonValue] = field(repr=False)


@dataclass(frozen=True, slots=True)
class StepRejected:
    """A step deliberately declined to transform its source document."""


StepDecision: TypeAlias = StepApplied | StepRejected


class Validator(Protocol):
    """Validate detached JSON without raising expected validation errors."""

    def __call__(self, document: Mapping[str, JsonValue], /) -> ValidationDecision: ...


class StepTransform(Protocol):
    """Transform detached JSON or return a controlled rejection."""

    def __call__(self, document: Mapping[str, JsonValue], /) -> StepDecision: ...


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """One declared, consecutive migration step."""

    source_version: int
    target_version: int
    name: str
    transform: StepTransform = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class VersionValidator:
    """The validator registered for one supported version."""

    version: int
    validate: Validator = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RegistrySpec:
    """Untrusted registry declaration accepted by ``validate_registry``."""

    oldest_supported_version: int | None
    current_version: int | None
    steps: tuple[MigrationStep, ...]
    validators: tuple[VersionValidator, ...]


@dataclass(frozen=True, slots=True)
class ValidatedMigrationRegistry:
    """Opaque, internally consistent registry safe for pure execution."""

    oldest_supported_version: int | None
    current_version: int | None
    _steps: tuple[MigrationStep, ...] = field(repr=False, compare=False)
    _validators: tuple[VersionValidator, ...] = field(repr=False, compare=False)

    @property
    def is_empty(self) -> bool:
        """Return whether this is the production legacy-only registry."""

        return self.current_version is None


@dataclass(frozen=True, slots=True)
class RegistryReady:
    """A registry specification passed all structural checks."""

    registry: ValidatedMigrationRegistry = field(repr=False)


@dataclass(frozen=True, slots=True)
class RegistryRejected:
    """A registry specification failed a fixed structural check."""

    category: RegistryRejectionCategory


RegistryBuildResult: TypeAlias = RegistryReady | RegistryRejected


@dataclass(frozen=True, slots=True)
class ImplicitLegacyV0:
    """A missing ``schema_version`` identifies current legacy version zero."""

    version: int = 0


@dataclass(frozen=True, slots=True)
class ExplicitVersion:
    """A supported-shaped positive explicit version value."""

    version: int


@dataclass(frozen=True, slots=True)
class VersionRejected:
    """A malformed or unsupported version, safe for Exit-only handling."""

    category: VersionRejectionCategory
    version: int | None = None


VersionIdentification: TypeAlias = ImplicitLegacyV0 | ExplicitVersion | VersionRejected


@dataclass(frozen=True, slots=True)
class PureExecutionRejected:
    """An expected source, step, intermediate, or target rejection."""

    category: PureExecutionRejectionCategory
    stage: PureExecutionStage
    source_version: int
    target_version: int | None = None
    step_name: str | None = None


@dataclass(frozen=True, slots=True)
class PureEngineFailure:
    """A deterministic engine operation failed without exposing its input."""

    category: PureEngineFailureCategory
    stage: PureExecutionStage
    source_version: int | None = None
    target_version: int | None = None
    step_name: str | None = None


@dataclass(frozen=True, slots=True)
class PureEngineDefect:
    """A callback violated its contract; its exception and text are discarded."""

    category: PureEngineDefectCategory
    stage: PureExecutionStage
    source_version: int
    target_version: int | None = None
    step_name: str | None = None


PureProblem: TypeAlias = PureExecutionRejected | PureEngineFailure | PureEngineDefect


@dataclass(frozen=True, slots=True)
class LegacyV0Current:
    """Production legacy input remains on the existing constructor path."""

    document: JsonObject = field(repr=False)


@dataclass(frozen=True, slots=True)
class VersionedCurrent:
    """A versioned document passed its current-version validator."""

    version: int
    document: JsonObject = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedMigration:
    """Validated source ready for preservation before any step is invoked."""

    source_version: int
    target_version: int
    step_count: int
    _source_document: JsonObject = field(repr=False, compare=False)
    _steps: tuple[MigrationStep, ...] = field(repr=False, compare=False)
    _validators: tuple[VersionValidator, ...] = field(repr=False, compare=False)


PreparationResult: TypeAlias = (
    LegacyV0Current
    | VersionedCurrent
    | PreparedMigration
    | VersionRejected
    | PureProblem
)


@dataclass(frozen=True, slots=True)
class SerializedDocument:
    """Deterministic UTF-8 JSON admitted by the candidate size ceiling."""

    data: bytes = field(repr=False)
    byte_count: int


SerializationResult: TypeAlias = SerializedDocument | PureEngineFailure


@dataclass(frozen=True, slots=True)
class SerializedMigration:
    """A complete detached target and its deterministic serialized bytes."""

    source_version: int
    target_version: int
    step_count: int
    document: JsonObject = field(repr=False)
    serialized: bytes = field(repr=False)
    byte_count: int


ExecutionResult: TypeAlias = SerializedMigration | PureProblem


DocumentValidationResult: TypeAlias = ValidationAccepted | VersionRejected | PureProblem


class _JsonDetachmentError(Exception):
    """Internal marker that deliberately carries no source value or message."""


def _is_version_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _detach_json_value(value: object, active: set[int]) -> JsonValue:
    value_type = type(value)
    if value is None or value_type is bool or value_type is int or value_type is str:
        return cast(JsonScalar, value)
    if value_type is float:
        float_value = cast(float, value)
        if not math.isfinite(float_value):
            raise _JsonDetachmentError
        return float_value
    if value_type is list:
        marker = id(value)
        if marker in active:
            raise _JsonDetachmentError
        active.add(marker)
        try:
            return [
                _detach_json_value(item, active) for item in cast(list[object], value)
            ]
        finally:
            active.remove(marker)
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active:
            raise _JsonDetachmentError
        active.add(marker)
        try:
            detached: JsonObject = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _JsonDetachmentError
                detached[key] = _detach_json_value(item, active)
            return detached
        finally:
            active.remove(marker)
    raise _JsonDetachmentError


def _detach_json_object(value: object) -> JsonObject:
    try:
        detached = _detach_json_value(value, set())
    except _JsonDetachmentError:
        raise
    except Exception:
        raise _JsonDetachmentError from None
    if not isinstance(detached, dict):
        raise _JsonDetachmentError
    return detached


def identify_version(document: Mapping[str, JsonValue]) -> VersionIdentification:
    """Identify implicit v0 or a positive explicit version without coercion."""

    if "schema_version" not in document:
        return ImplicitLegacyV0()
    value = document["schema_version"]
    if not _is_version_int(value) or cast(int, value) <= 0:
        rejected_value = cast(int, value) if _is_version_int(value) else None
        return VersionRejected(
            VersionRejectionCategory.MALFORMED_VERSION,
            rejected_value,
        )
    return ExplicitVersion(cast(int, value))


def _bounds_are_valid(spec: RegistrySpec) -> bool:
    oldest = spec.oldest_supported_version
    current = spec.current_version
    if oldest is None or current is None:
        return (
            oldest is None
            and current is None
            and not spec.steps
            and not spec.validators
        )
    if not _is_version_int(oldest) or not _is_version_int(current):
        return False
    return oldest >= 0 and oldest <= current


def validate_registry(spec: RegistrySpec) -> RegistryBuildResult:
    """Validate bounds, consecutive steps, validators, and diagnostic names."""

    if not _bounds_are_valid(spec):
        return RegistryRejected(RegistryRejectionCategory.INVALID_BOUNDS)
    if spec.current_version is None:
        return RegistryReady(ValidatedMigrationRegistry(None, None, (), ()))

    oldest = cast(int, spec.oldest_supported_version)
    current = spec.current_version
    step_sources: set[int] = set()
    step_names: set[str] = set()
    for step in spec.steps:
        if (
            not _is_version_int(step.source_version)
            or not _is_version_int(step.target_version)
            or step.target_version != step.source_version + 1
            or step.source_version < oldest
            or step.target_version > current
        ):
            return RegistryRejected(RegistryRejectionCategory.INVALID_STEP_ENDPOINT)
        if step.source_version in step_sources:
            return RegistryRejected(RegistryRejectionCategory.DUPLICATE_STEP_SOURCE)
        step_sources.add(step.source_version)
        if (
            not isinstance(step.name, str)
            or _STEP_NAME_PATTERN.fullmatch(step.name) is None
        ):
            return RegistryRejected(RegistryRejectionCategory.UNSAFE_STEP_NAME)
        if step.name in step_names:
            return RegistryRejected(RegistryRejectionCategory.DUPLICATE_STEP_NAME)
        step_names.add(step.name)

    if len(step_sources) != current - oldest:
        return RegistryRejected(RegistryRejectionCategory.STEP_GAP)

    validator_versions: set[int] = set()
    for validator in spec.validators:
        if (
            not _is_version_int(validator.version)
            or validator.version < oldest
            or validator.version > current
        ):
            return RegistryRejected(RegistryRejectionCategory.INVALID_VALIDATOR_VERSION)
        if validator.version in validator_versions:
            return RegistryRejected(
                RegistryRejectionCategory.DUPLICATE_VALIDATOR_VERSION
            )
        validator_versions.add(validator.version)

    if len(validator_versions) != current - oldest + 1:
        return RegistryRejected(RegistryRejectionCategory.MISSING_VALIDATOR)

    steps = tuple(sorted(spec.steps, key=lambda step: step.source_version))
    validators = tuple(sorted(spec.validators, key=lambda validator: validator.version))
    return RegistryReady(ValidatedMigrationRegistry(oldest, current, steps, validators))


def _validator_for(
    validators: tuple[VersionValidator, ...], version: int
) -> VersionValidator | None:
    for validator in validators:
        if validator.version == version:
            return validator
    return None


def _run_validator(
    document: JsonObject,
    validators: tuple[VersionValidator, ...],
    *,
    version: int,
    stage: PureExecutionStage,
    source_version: int,
    target_version: int | None,
    step_name: str | None,
) -> PureProblem | None:
    try:
        callback_document = _detach_json_object(document)
    except _JsonDetachmentError:
        return PureEngineFailure(
            PureEngineFailureCategory.JSON_DETACHMENT_FAILURE,
            stage,
            source_version,
            target_version,
            step_name,
        )

    validator = _validator_for(validators, version)
    if validator is None:
        return PureEngineDefect(
            PureEngineDefectCategory.INVALID_CALLBACK_RESULT,
            stage,
            source_version,
            target_version,
            step_name,
        )
    try:
        decision: object = validator.validate(callback_document)
    except Exception:
        return PureEngineDefect(
            PureEngineDefectCategory.CALLBACK_EXCEPTION,
            stage,
            source_version,
            target_version,
            step_name,
        )

    if isinstance(decision, ValidationRejected):
        if stage is PureExecutionStage.SOURCE_VALIDATION:
            category = PureExecutionRejectionCategory.SOURCE_VALIDATION_FAILURE
        elif stage is PureExecutionStage.INTERMEDIATE_VALIDATION:
            category = PureExecutionRejectionCategory.INTERMEDIATE_VALIDATION_FAILURE
        else:
            category = PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE
        return PureExecutionRejected(
            category,
            stage,
            source_version,
            target_version,
            step_name,
        )
    if not isinstance(decision, ValidationAccepted):
        return PureEngineDefect(
            PureEngineDefectCategory.INVALID_CALLBACK_RESULT,
            stage,
            source_version,
            target_version,
            step_name,
        )
    return None


def prepare_migration(
    document: Mapping[str, JsonValue],
    registry: ValidatedMigrationRegistry,
) -> PreparationResult:
    """Identify and validate a source without running or preserving any step."""

    identity = identify_version(document)
    if isinstance(identity, VersionRejected):
        return identity

    source_version = identity.version
    if registry.is_empty:
        if isinstance(identity, ExplicitVersion):
            return VersionRejected(
                VersionRejectionCategory.UNSUPPORTED_NEWER,
                source_version,
            )
        try:
            return LegacyV0Current(_detach_json_object(document))
        except _JsonDetachmentError:
            return PureEngineFailure(
                PureEngineFailureCategory.JSON_DETACHMENT_FAILURE,
                PureExecutionStage.SOURCE_VALIDATION,
                source_version,
            )

    oldest = cast(int, registry.oldest_supported_version)
    current = cast(int, registry.current_version)
    if source_version < oldest:
        return VersionRejected(
            VersionRejectionCategory.UNSUPPORTED_OLDER,
            source_version,
        )
    if source_version > current:
        return VersionRejected(
            VersionRejectionCategory.UNSUPPORTED_NEWER,
            source_version,
        )

    try:
        source_document = _detach_json_object(document)
    except _JsonDetachmentError:
        return PureEngineFailure(
            PureEngineFailureCategory.JSON_DETACHMENT_FAILURE,
            PureExecutionStage.SOURCE_VALIDATION,
            source_version,
        )

    validation = _run_validator(
        source_document,
        registry._validators,
        version=source_version,
        stage=PureExecutionStage.SOURCE_VALIDATION,
        source_version=source_version,
        target_version=None,
        step_name=None,
    )
    if validation is not None:
        return validation
    if source_version == current:
        return VersionedCurrent(source_version, source_document)

    steps = tuple(
        step for step in registry._steps if step.source_version >= source_version
    )
    return PreparedMigration(
        source_version,
        current,
        len(steps),
        source_document,
        steps,
        registry._validators,
    )


def validate_document(
    document: Mapping[str, JsonValue],
    identity: ImplicitLegacyV0 | ExplicitVersion,
    registry: ValidatedMigrationRegistry,
    *,
    stage: PureExecutionStage = PureExecutionStage.SOURCE_VALIDATION,
) -> DocumentValidationResult:
    """Defensively validate one supported document without migration."""

    observed = identify_version(document)
    if isinstance(observed, VersionRejected):
        return observed
    if type(observed) is not type(identity) or observed.version != identity.version:
        return PureEngineDefect(
            PureEngineDefectCategory.UNEXPECTED_TARGET_VERSION,
            stage,
            identity.version,
            identity.version
            if stage is not PureExecutionStage.SOURCE_VALIDATION
            else None,
        )
    try:
        detached = _detach_json_object(document)
    except _JsonDetachmentError:
        return PureEngineFailure(
            PureEngineFailureCategory.JSON_DETACHMENT_FAILURE,
            stage,
            identity.version,
            identity.version
            if stage is not PureExecutionStage.SOURCE_VALIDATION
            else None,
        )
    if registry.is_empty:
        if isinstance(identity, ImplicitLegacyV0):
            return ValidationAccepted()
        return VersionRejected(
            VersionRejectionCategory.UNSUPPORTED_NEWER,
            identity.version,
        )
    oldest = cast(int, registry.oldest_supported_version)
    current = cast(int, registry.current_version)
    if identity.version < oldest:
        return VersionRejected(
            VersionRejectionCategory.UNSUPPORTED_OLDER,
            identity.version,
        )
    if identity.version > current:
        return VersionRejected(
            VersionRejectionCategory.UNSUPPORTED_NEWER,
            identity.version,
        )
    problem = _run_validator(
        detached,
        registry._validators,
        version=identity.version,
        stage=stage,
        source_version=identity.version,
        target_version=(
            identity.version
            if stage is not PureExecutionStage.SOURCE_VALIDATION
            else None
        ),
        step_name=None,
    )
    return problem if problem is not None else ValidationAccepted()


def serialize_deterministically(
    document: Mapping[str, JsonValue],
) -> SerializationResult:
    """Serialize strict JSON as UTF-8, LF-only bytes with no trailing newline."""

    try:
        detached = _detach_json_object(document)
        text = json.dumps(
            detached,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        serialized = text.encode("utf-8")
    except (_JsonDetachmentError, TypeError, ValueError, OverflowError, UnicodeError):
        return PureEngineFailure(
            PureEngineFailureCategory.SERIALIZATION_FAILURE,
            PureExecutionStage.SERIALIZATION,
        )
    if len(serialized) > MAX_CONFIG_BYTES:
        return PureEngineFailure(
            PureEngineFailureCategory.CANDIDATE_SIZE_LIMIT_EXCEEDED,
            PureExecutionStage.SERIALIZATION,
        )
    return SerializedDocument(serialized, len(serialized))


def execute_prepared_migration(prepared: PreparedMigration) -> ExecutionResult:
    """Run each detached consecutive step once, validate, and serialize."""

    try:
        candidate = _detach_json_object(prepared._source_document)
    except _JsonDetachmentError:
        return PureEngineFailure(
            PureEngineFailureCategory.JSON_DETACHMENT_FAILURE,
            PureExecutionStage.STEP,
            prepared.source_version,
            prepared.target_version,
        )

    for step in prepared._steps:
        try:
            step_input = _detach_json_object(candidate)
        except _JsonDetachmentError:
            return PureEngineFailure(
                PureEngineFailureCategory.JSON_DETACHMENT_FAILURE,
                PureExecutionStage.STEP,
                step.source_version,
                step.target_version,
                step.name,
            )
        try:
            decision: object = step.transform(step_input)
        except Exception:
            return PureEngineDefect(
                PureEngineDefectCategory.CALLBACK_EXCEPTION,
                PureExecutionStage.STEP,
                step.source_version,
                step.target_version,
                step.name,
            )
        if isinstance(decision, StepRejected):
            return PureExecutionRejected(
                PureExecutionRejectionCategory.STEP_REJECTION,
                PureExecutionStage.STEP,
                step.source_version,
                step.target_version,
                step.name,
            )
        if not isinstance(decision, StepApplied):
            return PureEngineDefect(
                PureEngineDefectCategory.INVALID_CALLBACK_RESULT,
                PureExecutionStage.STEP,
                step.source_version,
                step.target_version,
                step.name,
            )
        try:
            candidate = _detach_json_object(decision.document)
        except _JsonDetachmentError:
            return PureEngineDefect(
                PureEngineDefectCategory.INVALID_STEP_OUTPUT,
                PureExecutionStage.STEP,
                step.source_version,
                step.target_version,
                step.name,
            )

        candidate_version = candidate.get("schema_version")
        if (
            not _is_version_int(candidate_version)
            or candidate_version != step.target_version
        ):
            return PureEngineDefect(
                PureEngineDefectCategory.UNEXPECTED_TARGET_VERSION,
                PureExecutionStage.STEP,
                step.source_version,
                step.target_version,
                step.name,
            )

        validation_stage = (
            PureExecutionStage.TARGET_VALIDATION
            if step.target_version == prepared.target_version
            else PureExecutionStage.INTERMEDIATE_VALIDATION
        )
        validation = _run_validator(
            candidate,
            prepared._validators,
            version=step.target_version,
            stage=validation_stage,
            source_version=step.source_version,
            target_version=step.target_version,
            step_name=step.name,
        )
        if validation is not None:
            return validation

    serialized = serialize_deterministically(candidate)
    if isinstance(serialized, PureEngineFailure):
        return PureEngineFailure(
            serialized.category,
            serialized.stage,
            prepared.source_version,
            prepared.target_version,
        )
    return SerializedMigration(
        prepared.source_version,
        prepared.target_version,
        prepared.step_count,
        candidate,
        serialized.data,
        serialized.byte_count,
    )


class ConfigurationAuthority(StrEnum):
    """Best proven owner of the live configuration path at transaction exit."""

    ORIGINAL = "original"
    CANDIDATE = "candidate"
    RESTORED_ORIGINAL = "restored_original"
    EXTERNAL_CURRENT = "external_current"
    UNKNOWN = "unknown"


class TransactionFailureCategory(StrEnum):
    """Curated filesystem transaction failures with explicit precedence."""

    PRESERVATION_FAILURE = "preservation_failure"
    SOURCE_CHANGED = "source_changed"
    ORIGINAL_REVERIFICATION_FAILURE = "original_reverification_failure"
    CANDIDATE_WRITE_FAILURE = "candidate_write_failure"
    POST_WRITE_SOURCE_CHANGED = "post_write_source_changed"
    POST_WRITE_VALIDATION_FAILURE = "post_write_validation_failure"
    CANDIDATE_RETENTION_FAILURE = "candidate_retention_failure"
    ROLLBACK_SOURCE_CHANGED = "rollback_source_changed"
    ROLLBACK_ARTIFACT_FAILURE = "rollback_artifact_failure"
    ROLLBACK_WRITE_FAILURE = "rollback_write_failure"
    ROLLBACK_VERIFICATION_FAILURE = "rollback_verification_failure"


@dataclass(frozen=True, slots=True)
class MigrationCommitted:
    """A deterministic candidate was written, reloaded, and validated."""

    target_version: int
    byte_count: int
    document: JsonObject = field(repr=False)
    authority: ConfigurationAuthority = ConfigurationAuthority.CANDIDATE
    recovery_copy_count: int = 1
    failed_candidate_copy_count: int = 0
    rollback_count: int = 0


@dataclass(frozen=True, slots=True)
class MigrationAbortedAfterPreservation:
    """Pure execution stopped after the verified original was preserved."""

    problem: PureProblem
    authority: ConfigurationAuthority = ConfigurationAuthority.ORIGINAL
    recovery_copy_count: int = 1
    failed_candidate_copy_count: int = 0
    rollback_count: int = 0


@dataclass(frozen=True, slots=True)
class MigrationRolledBack:
    """A rejected post-write candidate was retained when possible and removed."""

    category: TransactionFailureCategory
    verification_problem: VersionRejected | PureProblem
    candidate_retention_category: CandidateRetentionFailureCategory | None = None
    authority: ConfigurationAuthority = ConfigurationAuthority.RESTORED_ORIGINAL
    recovery_copy_count: int = 1
    failed_candidate_copy_count: int = 0
    rollback_count: int = 1


@dataclass(frozen=True, slots=True)
class MigrationTransactionFailed:
    """The coordinator failed closed with category-only filesystem details."""

    category: TransactionFailureCategory
    authority: ConfigurationAuthority
    recovery_copy_count: int
    failed_candidate_copy_count: int = 0
    rollback_count: int = 0
    recovery_category: RecoveryFailureCategory | None = None
    candidate_retention_category: CandidateRetentionFailureCategory | None = None


MigrationTransactionResult: TypeAlias = (
    MigrationCommitted
    | MigrationAbortedAfterPreservation
    | MigrationRolledBack
    | MigrationTransactionFailed
)
TransactionProblem: TypeAlias = (
    MigrationAbortedAfterPreservation | MigrationRolledBack | MigrationTransactionFailed
)


class _GuardRefused(Exception):
    """Internal category-only refusal raised from an atomic-writer guard."""

    def __init__(
        self,
        category: TransactionFailureCategory,
        recovery_category: RecoveryFailureCategory | None = None,
    ) -> None:
        self.category = category
        self.recovery_category = recovery_category
        super().__init__(category.value)


def _pre_write_guard_category(
    category: RecoveryFailureCategory,
) -> TransactionFailureCategory:
    if category in (
        RecoveryFailureCategory.SOURCE_UNAVAILABLE,
        RecoveryFailureCategory.SOURCE_CHANGED,
    ):
        return TransactionFailureCategory.SOURCE_CHANGED
    return TransactionFailureCategory.ORIGINAL_REVERIFICATION_FAILURE


def _post_write_authority(result: object) -> ConfigurationAuthority:
    if isinstance(result, RawConfigLoaded):
        return ConfigurationAuthority.EXTERNAL_CURRENT
    return ConfigurationAuthority.UNKNOWN


def _coordinate_prepared_migration(
    config_path: Path,
    loaded: RawConfigLoaded,
    prepared: PreparedMigration,
    registry: ValidatedMigrationRegistry,
) -> MigrationTransactionResult:
    preservation = preserve_source(config_path, loaded.snapshot)
    if isinstance(preservation, SourcePreservationFailed):
        source_changed = preservation.category is RecoveryFailureCategory.SOURCE_CHANGED
        return MigrationTransactionFailed(
            category=(
                TransactionFailureCategory.SOURCE_CHANGED
                if source_changed
                else TransactionFailureCategory.PRESERVATION_FAILURE
            ),
            authority=(
                ConfigurationAuthority.UNKNOWN
                if source_changed
                else ConfigurationAuthority.ORIGINAL
            ),
            recovery_copy_count=preservation.recovery_copy_count,
            recovery_category=preservation.category,
        )
    preserved = preservation.source

    execution = execute_prepared_migration(prepared)
    if not isinstance(execution, SerializedMigration):
        original_check = reverify_preserved_source(preserved)
        if isinstance(original_check, RecoveryVerificationFailed):
            category = _pre_write_guard_category(original_check.category)
            return MigrationTransactionFailed(
                category=category,
                authority=(
                    ConfigurationAuthority.UNKNOWN
                    if category is TransactionFailureCategory.SOURCE_CHANGED
                    else ConfigurationAuthority.ORIGINAL
                ),
                recovery_copy_count=1,
                recovery_category=original_check.category,
            )
        return MigrationAbortedAfterPreservation(execution)

    candidate_bytes = execution.serialized
    if len(candidate_bytes) > MAX_CONFIG_BYTES:
        return MigrationAbortedAfterPreservation(
            PureEngineFailure(
                PureEngineFailureCategory.CANDIDATE_SIZE_LIMIT_EXCEEDED,
                PureExecutionStage.SERIALIZATION,
                prepared.source_version,
                prepared.target_version,
            )
        )

    def before_candidate_replace() -> None:
        verification = reverify_preserved_source(preserved)
        if isinstance(verification, RecoveryVerificationFailed):
            raise _GuardRefused(
                _pre_write_guard_category(verification.category),
                verification.category,
            )

    try:
        atomic_write_bytes(
            config_path,
            candidate_bytes,
            before_replace=before_candidate_replace,
        )
    except _GuardRefused as refusal:
        return MigrationTransactionFailed(
            category=refusal.category,
            authority=(
                ConfigurationAuthority.UNKNOWN
                if refusal.category is TransactionFailureCategory.SOURCE_CHANGED
                else ConfigurationAuthority.ORIGINAL
            ),
            recovery_copy_count=1,
            recovery_category=refusal.recovery_category,
        )
    except OSError:
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.CANDIDATE_WRITE_FAILURE,
            authority=ConfigurationAuthority.ORIGINAL,
            recovery_copy_count=1,
        )

    post_write = load_raw_config(config_path)
    if (
        not isinstance(post_write, RawConfigLoaded)
        or post_write.snapshot.source_is_redirected
        or post_write.source_bytes != candidate_bytes
    ):
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.POST_WRITE_SOURCE_CHANGED,
            authority=_post_write_authority(post_write),
            recovery_copy_count=1,
        )

    post_write_validation = validate_document(
        cast(Mapping[str, JsonValue], post_write.mapping),
        ExplicitVersion(prepared.target_version),
        registry,
        stage=PureExecutionStage.TARGET_VALIDATION,
    )
    if isinstance(post_write_validation, ValidationAccepted):
        final_candidate_check = reverify_source_bytes(
            config_path,
            post_write.snapshot,
            candidate_bytes,
        )
        if isinstance(final_candidate_check, RecoveryVerificationFailed):
            return MigrationTransactionFailed(
                category=TransactionFailureCategory.POST_WRITE_SOURCE_CHANGED,
                authority=ConfigurationAuthority.UNKNOWN,
                recovery_copy_count=1,
                recovery_category=final_candidate_check.category,
            )
        return MigrationCommitted(
            target_version=prepared.target_version,
            byte_count=len(candidate_bytes),
            document=execution.document,
        )

    retention = retain_failed_candidate(
        config_path,
        post_write.snapshot,
        candidate_bytes,
    )
    candidate_copy_count: int
    retention_category: CandidateRetentionFailureCategory | None
    if isinstance(retention, CandidateRetained):
        candidate_copy_count = retention.failed_candidate_copy_count
        retention_category = None
    else:
        candidate_copy_count = retention.failed_candidate_copy_count
        retention_category = retention.category

    candidate_verification = reverify_source_bytes(
        config_path,
        post_write.snapshot,
        candidate_bytes,
    )
    if isinstance(candidate_verification, RecoveryVerificationFailed):
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED,
            authority=ConfigurationAuthority.UNKNOWN,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            candidate_retention_category=retention_category,
        )

    artifact_verification = verify_preserved_artifact(preserved)
    if isinstance(artifact_verification, RecoveryVerificationFailed):
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_ARTIFACT_FAILURE,
            authority=ConfigurationAuthority.CANDIDATE,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            recovery_category=artifact_verification.category,
            candidate_retention_category=retention_category,
        )
    rollback_bytes = read_preserved_bytes(preserved)
    if not isinstance(rollback_bytes, PreservedBytesRead):
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_ARTIFACT_FAILURE,
            authority=ConfigurationAuthority.CANDIDATE,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            recovery_category=rollback_bytes.category,
            candidate_retention_category=retention_category,
        )

    def before_rollback_replace() -> None:
        candidate_check = reverify_source_bytes(
            config_path,
            post_write.snapshot,
            candidate_bytes,
        )
        if isinstance(candidate_check, RecoveryVerificationFailed):
            raise _GuardRefused(
                TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED,
                candidate_check.category,
            )
        artifact_check = verify_preserved_artifact(preserved)
        if isinstance(artifact_check, RecoveryVerificationFailed):
            raise _GuardRefused(
                TransactionFailureCategory.ROLLBACK_ARTIFACT_FAILURE,
                artifact_check.category,
            )

    try:
        atomic_write_bytes(
            config_path,
            rollback_bytes.data,
            before_replace=before_rollback_replace,
        )
    except _GuardRefused as refusal:
        authority = (
            ConfigurationAuthority.UNKNOWN
            if refusal.category is TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED
            else ConfigurationAuthority.CANDIDATE
        )
        return MigrationTransactionFailed(
            category=refusal.category,
            authority=authority,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            recovery_category=refusal.recovery_category,
            candidate_retention_category=retention_category,
        )
    except OSError:
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_WRITE_FAILURE,
            authority=ConfigurationAuthority.CANDIDATE,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            candidate_retention_category=retention_category,
        )

    restored = load_raw_config(config_path)
    restored_is_exact = (
        isinstance(restored, RawConfigLoaded)
        and not restored.snapshot.source_is_redirected
        and restored.source_bytes == rollback_bytes.data
    )
    if not restored_is_exact:
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_VERIFICATION_FAILURE,
            authority=_post_write_authority(restored),
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            rollback_count=1,
            candidate_retention_category=retention_category,
        )

    restored_loaded = cast(RawConfigLoaded, restored)
    restored_identity = identify_version(
        cast(Mapping[str, JsonValue], restored_loaded.mapping)
    )
    if isinstance(restored_identity, VersionRejected):
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_VERIFICATION_FAILURE,
            authority=ConfigurationAuthority.RESTORED_ORIGINAL,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            rollback_count=1,
            candidate_retention_category=retention_category,
        )
    restored_validation = validate_document(
        cast(Mapping[str, JsonValue], restored_loaded.mapping),
        restored_identity,
        registry,
    )
    final_source_check = reverify_source_bytes(
        config_path,
        restored_loaded.snapshot,
        rollback_bytes.data,
    )
    if isinstance(final_source_check, RecoveryVerificationFailed):
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED,
            authority=ConfigurationAuthority.UNKNOWN,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            rollback_count=1,
            recovery_category=final_source_check.category,
            candidate_retention_category=retention_category,
        )
    final_artifact_check = verify_preserved_artifact(preserved)
    if not isinstance(restored_validation, ValidationAccepted) or isinstance(
        final_artifact_check, RecoveryVerificationFailed
    ):
        return MigrationTransactionFailed(
            category=TransactionFailureCategory.ROLLBACK_VERIFICATION_FAILURE,
            authority=ConfigurationAuthority.RESTORED_ORIGINAL,
            recovery_copy_count=1,
            failed_candidate_copy_count=candidate_copy_count,
            rollback_count=1,
            recovery_category=(
                final_artifact_check.category
                if isinstance(final_artifact_check, RecoveryVerificationFailed)
                else None
            ),
            candidate_retention_category=retention_category,
        )

    rollback_category = (
        TransactionFailureCategory.CANDIDATE_RETENTION_FAILURE
        if isinstance(retention, CandidateRetentionFailed)
        else TransactionFailureCategory.POST_WRITE_VALIDATION_FAILURE
    )
    return MigrationRolledBack(
        category=rollback_category,
        verification_problem=post_write_validation,
        candidate_retention_category=retention_category,
        failed_candidate_copy_count=candidate_copy_count,
    )


LoadedMigrationResult: TypeAlias = (
    LegacyV0Current
    | VersionedCurrent
    | VersionRejected
    | PureProblem
    | MigrationTransactionResult
)


def coordinate_migration(
    config_path: Path,
    loaded: RawConfigLoaded,
    registry: ValidatedMigrationRegistry,
) -> LoadedMigrationResult:
    """Classify and source-validate before preserving only step-bearing inputs."""

    preparation = prepare_migration(
        cast(Mapping[str, JsonValue], loaded.mapping),
        registry,
    )
    if isinstance(preparation, PreparedMigration):
        return _coordinate_prepared_migration(
            config_path,
            loaded,
            preparation,
            registry,
        )
    return preparation


@dataclass(frozen=True, slots=True)
class ImplicitLegacyLoaded(Generic[T]):
    """One legacy value plus the raw evidence needed for its guarded save."""

    value: T = field(repr=False)
    raw: RawConfigLoaded = field(repr=False)


StartupConfigurationResult: TypeAlias = (
    ConfigMissing
    | ConfigRecoveryRequired
    | ImplicitLegacyLoaded[T]
    | VersionedCurrent
    | VersionRejected
    | PureProblem
    | MigrationTransactionResult
)


def load_startup_configuration(
    config_path: Path,
    legacy_constructor: Callable[[dict[str, object]], T],
    registry: ValidatedMigrationRegistry,
) -> StartupConfigurationResult[T]:
    """Perform exactly one initial bounded load before all classification."""

    loaded = load_raw_config(config_path)
    if not isinstance(loaded, RawConfigLoaded):
        return loaded

    identity = identify_version(cast(Mapping[str, JsonValue], loaded.mapping))
    if registry.is_empty and isinstance(identity, ImplicitLegacyV0):
        try:
            value = legacy_constructor(loaded.mapping)
        except LegacyConstructionFailure:
            return ConfigRecoveryRequired(
                ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE,
                loaded.snapshot,
            )
        return ImplicitLegacyLoaded(value, loaded)

    migration_result = coordinate_migration(config_path, loaded, registry)
    if not isinstance(migration_result, LegacyV0Current):
        return migration_result

    try:
        value = legacy_constructor(loaded.mapping)
    except LegacyConstructionFailure:
        return ConfigRecoveryRequired(
            ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE,
            loaded.snapshot,
        )
    return ImplicitLegacyLoaded(value, loaded)


class LegacyNormalizationSaveFailureCategory(StrEnum):
    """Fixed guarded-save failures for already-classified implicit v0."""

    SOURCE_CHANGED = "source_changed"
    PERSISTENCE_FAILURE = "persistence_failure"


@dataclass(frozen=True, slots=True)
class LegacyNormalizationSaved:
    """The existing canonical implicit-v0 save replaced its unchanged source."""


@dataclass(frozen=True, slots=True)
class LegacyNormalizationSaveFailed:
    """A guarded legacy normalization save aborted without retry or recovery."""

    category: LegacyNormalizationSaveFailureCategory


LegacyNormalizationSaveResult: TypeAlias = (
    LegacyNormalizationSaved | LegacyNormalizationSaveFailed
)


def guarded_legacy_normalization_save(
    config_path: Path,
    loaded: RawConfigLoaded,
    replacement_text: str,
) -> LegacyNormalizationSaveResult:
    """Save canonical legacy text only while the classified bytes remain current."""

    def before_replace() -> None:
        verification = reverify_source_bytes(
            config_path,
            loaded.snapshot,
            loaded.source_bytes,
        )
        if isinstance(verification, RecoveryVerificationFailed):
            raise _GuardRefused(TransactionFailureCategory.SOURCE_CHANGED)

    try:
        atomic_write_text(
            config_path,
            replacement_text,
            before_replace=before_replace,
        )
    except _GuardRefused:
        return LegacyNormalizationSaveFailed(
            LegacyNormalizationSaveFailureCategory.SOURCE_CHANGED
        )
    except OSError:
        return LegacyNormalizationSaveFailed(
            LegacyNormalizationSaveFailureCategory.PERSISTENCE_FAILURE
        )
    return LegacyNormalizationSaved()


class StartupFailureRoute(StrEnum):
    """Pure startup routing that keeps Q3 recovery separate from Q4 failures."""

    Q3_RECOVERY = "q3_recovery"
    EXIT_ONLY = "exit_only"


class StartupNoticeCategory(StrEnum):
    """Fixed inputs for the Exit-only startup message."""

    MALFORMED_VERSION = "malformed_version"
    UNSUPPORTED_VERSION = "unsupported_version"
    MIGRATION_FAILED = "migration_failed"
    CONFIG_CHANGED = "config_changed"


ExitOnlyFailure: TypeAlias = (
    VersionRejected
    | PureProblem
    | MigrationAbortedAfterPreservation
    | MigrationRolledBack
    | MigrationTransactionFailed
    | LegacyNormalizationSaveFailed
)
StartupFailureOutcome: TypeAlias = ConfigRecoveryRequired | ExitOnlyFailure


def startup_failure_route(outcome: StartupFailureOutcome) -> StartupFailureRoute:
    """Return the only startup prompt family permitted for an outcome."""

    if isinstance(outcome, ConfigRecoveryRequired):
        return StartupFailureRoute.Q3_RECOVERY
    return StartupFailureRoute.EXIT_ONLY


def startup_notice_category(outcome: ExitOnlyFailure) -> StartupNoticeCategory:
    """Reduce an Exit-only failure to one fixed, non-sensitive message input."""

    if isinstance(outcome, VersionRejected):
        if outcome.category is VersionRejectionCategory.MALFORMED_VERSION:
            return StartupNoticeCategory.MALFORMED_VERSION
        return StartupNoticeCategory.UNSUPPORTED_VERSION
    if isinstance(outcome, LegacyNormalizationSaveFailed):
        if outcome.category is LegacyNormalizationSaveFailureCategory.SOURCE_CHANGED:
            return StartupNoticeCategory.CONFIG_CHANGED
        return StartupNoticeCategory.MIGRATION_FAILED
    if isinstance(outcome, MigrationTransactionFailed) and outcome.category in (
        TransactionFailureCategory.SOURCE_CHANGED,
        TransactionFailureCategory.POST_WRITE_SOURCE_CHANGED,
        TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED,
    ):
        return StartupNoticeCategory.CONFIG_CHANGED
    return StartupNoticeCategory.MIGRATION_FAILED


_STARTUP_NOTICE_MESSAGES: Final[dict[StartupNoticeCategory, str]] = {
    StartupNoticeCategory.MALFORMED_VERSION: (
        "The saved configuration has an invalid schema version. "
        "It was left unchanged and the application will exit."
    ),
    StartupNoticeCategory.UNSUPPORTED_VERSION: (
        "The saved configuration uses an unsupported schema version. "
        "It was left unchanged and the application will exit."
    ),
    StartupNoticeCategory.MIGRATION_FAILED: (
        "The saved configuration could not be migrated safely. "
        "The application will exit."
    ),
    StartupNoticeCategory.CONFIG_CHANGED: (
        "The saved configuration changed while it was being processed. "
        "No further overwrite was attempted and the application will exit."
    ),
}


def startup_notice_message(category: StartupNoticeCategory) -> str:
    """Return fixed UI text that cannot contain configuration or exception data."""

    return _STARTUP_NOTICE_MESSAGES[category]


DiagnosticOutcome: TypeAlias = (
    RegistryRejected
    | VersionRejected
    | PureExecutionRejected
    | PureEngineFailure
    | PureEngineDefect
)


def migration_diagnostics(result: DiagnosticOutcome) -> dict[str, DiagnosticValue]:
    """Render only curated categories, versions, stages, and safe step names."""

    diagnostics: dict[str, DiagnosticValue] = {"failure_count": 1}
    if isinstance(result, RegistryRejected):
        diagnostics["failure_kind"] = "registry_rejection"
        diagnostics["failure_category"] = result.category.value
        return diagnostics
    if isinstance(result, VersionRejected):
        diagnostics["failure_kind"] = "version_rejection"
        diagnostics["failure_category"] = result.category.value
        if result.version is not None:
            diagnostics["source_version"] = result.version
        return diagnostics

    if isinstance(result, PureExecutionRejected):
        diagnostics["failure_kind"] = "execution_rejection"
    elif isinstance(result, PureEngineFailure):
        diagnostics["failure_kind"] = "engine_failure"
    else:
        diagnostics["failure_kind"] = "engine_defect"
    diagnostics["failure_category"] = result.category.value
    diagnostics["failure_stage"] = result.stage.value
    if result.source_version is not None:
        diagnostics["source_version"] = result.source_version
    if result.target_version is not None:
        diagnostics["target_version"] = result.target_version
    if result.step_name is not None:
        diagnostics["step_name"] = result.step_name
    return diagnostics


def transaction_diagnostics(
    result: TransactionProblem,
) -> dict[str, DiagnosticValue]:
    """Render transaction state using only categories, counts, and authority."""

    diagnostics: dict[str, DiagnosticValue]
    if isinstance(result, MigrationAbortedAfterPreservation):
        diagnostics = migration_diagnostics(result.problem)
        diagnostics["transaction_state"] = "aborted_after_preservation"
    else:
        diagnostics = {
            "failure_count": 1,
            "failure_kind": "transaction_failure",
            "failure_category": result.category.value,
        }
    diagnostics["configuration_authority"] = result.authority.value
    diagnostics["recovery_copy_count"] = result.recovery_copy_count
    diagnostics["failed_candidate_copy_count"] = result.failed_candidate_copy_count
    diagnostics["rollback_count"] = result.rollback_count
    if isinstance(result, MigrationRolledBack):
        if result.candidate_retention_category is not None:
            diagnostics["candidate_retention_category"] = (
                result.candidate_retention_category.value
            )
    elif isinstance(result, MigrationTransactionFailed):
        if result.recovery_category is not None:
            diagnostics["recovery_category"] = result.recovery_category.value
        if result.candidate_retention_category is not None:
            diagnostics["candidate_retention_category"] = (
                result.candidate_retention_category.value
            )
    return diagnostics


def startup_failure_diagnostics(
    outcome: ExitOnlyFailure,
) -> dict[str, DiagnosticValue]:
    """Return the only breadcrumb fields exposed by Exit-only startup failures."""

    if isinstance(
        outcome,
        (
            VersionRejected,
            PureExecutionRejected,
            PureEngineFailure,
            PureEngineDefect,
        ),
    ):
        return migration_diagnostics(outcome)
    if isinstance(
        outcome,
        (
            MigrationAbortedAfterPreservation,
            MigrationRolledBack,
            MigrationTransactionFailed,
        ),
    ):
        return transaction_diagnostics(outcome)
    return {
        "failure_count": 1,
        "failure_kind": "legacy_normalization_save",
        "failure_category": outcome.category.value,
    }


class ConfigurationMigrationError(Exception):
    """Safe exception boundary for direct ``LauncherConfig.load`` callers."""

    def __init__(
        self,
        notice_category: StartupNoticeCategory,
        diagnostics: Mapping[str, DiagnosticValue],
    ) -> None:
        self.notice_category = notice_category
        self.diagnostics: dict[str, DiagnosticValue] = dict(diagnostics)
        super().__init__(notice_category.value)

    @classmethod
    def from_outcome(
        cls,
        outcome: ExitOnlyFailure,
    ) -> ConfigurationMigrationError:
        """Create a sanitized exception after callback exception scopes have ended."""

        return cls(
            startup_notice_category(outcome),
            startup_failure_diagnostics(outcome),
        )

    @classmethod
    def unexpected_success(cls) -> ConfigurationMigrationError:
        """Fail safely if the dormant registry returns an unsupported app result."""

        return cls(
            StartupNoticeCategory.MIGRATION_FAILED,
            {
                "failure_count": 1,
                "failure_kind": "unexpected_migration_success",
            },
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(notice_category={self.notice_category.value!r})"

    def __str__(self) -> str:
        return self.notice_category.value


StartupResult: TypeAlias = PreparationResult | ExecutionResult | RegistryRejected


def migration_startup_route(result: StartupResult) -> MigrationStartupRoute:
    """Map pure outcomes without exposing any UI or Q3 recovery implementation."""

    if isinstance(result, LegacyV0Current):
        return MigrationStartupRoute.LEGACY
    if isinstance(result, VersionedCurrent):
        return MigrationStartupRoute.CURRENT
    if isinstance(result, PreparedMigration):
        return MigrationStartupRoute.MIGRATION_REQUIRED
    if isinstance(result, SerializedMigration):
        return MigrationStartupRoute.MIGRATED
    return MigrationStartupRoute.EXIT_ONLY


PRODUCTION_REGISTRY_SPEC: Final = RegistrySpec(None, None, (), ())
_production_registry_result = validate_registry(PRODUCTION_REGISTRY_SPEC)
if not isinstance(_production_registry_result, RegistryReady):
    raise RuntimeError("invalid production migration registry")
PRODUCTION_REGISTRY: Final = _production_registry_result.registry


__all__ = [
    "ConfigurationAuthority",
    "ConfigurationMigrationError",
    "DiagnosticOutcome",
    "DiagnosticValue",
    "DocumentValidationResult",
    "ExecutionResult",
    "ExitOnlyFailure",
    "ExplicitVersion",
    "ImplicitLegacyLoaded",
    "ImplicitLegacyV0",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "LegacyNormalizationSaveFailed",
    "LegacyNormalizationSaveFailureCategory",
    "LegacyNormalizationSaveResult",
    "LegacyNormalizationSaved",
    "LegacyV0Current",
    "LoadedMigrationResult",
    "MigrationAbortedAfterPreservation",
    "MigrationCommitted",
    "MigrationRolledBack",
    "MigrationStartupRoute",
    "MigrationStep",
    "MigrationTransactionFailed",
    "MigrationTransactionResult",
    "PRODUCTION_REGISTRY",
    "PRODUCTION_REGISTRY_SPEC",
    "PreparationResult",
    "PreparedMigration",
    "PureEngineDefect",
    "PureEngineDefectCategory",
    "PureEngineFailure",
    "PureEngineFailureCategory",
    "PureExecutionRejected",
    "PureExecutionRejectionCategory",
    "PureExecutionStage",
    "PureProblem",
    "RegistryBuildResult",
    "RegistryReady",
    "RegistryRejected",
    "RegistryRejectionCategory",
    "RegistrySpec",
    "SerializedDocument",
    "SerializedMigration",
    "SerializationResult",
    "StartupResult",
    "StartupConfigurationResult",
    "StartupFailureOutcome",
    "StartupFailureRoute",
    "StartupNoticeCategory",
    "StepApplied",
    "StepDecision",
    "StepRejected",
    "StepTransform",
    "ValidatedMigrationRegistry",
    "ValidationAccepted",
    "ValidationDecision",
    "ValidationRejected",
    "Validator",
    "VersionIdentification",
    "VersionRejected",
    "VersionRejectionCategory",
    "VersionValidator",
    "VersionedCurrent",
    "TransactionFailureCategory",
    "TransactionProblem",
    "coordinate_migration",
    "execute_prepared_migration",
    "guarded_legacy_normalization_save",
    "identify_version",
    "load_startup_configuration",
    "migration_diagnostics",
    "migration_startup_route",
    "prepare_migration",
    "serialize_deterministically",
    "startup_failure_diagnostics",
    "startup_failure_route",
    "startup_notice_category",
    "startup_notice_message",
    "transaction_diagnostics",
    "validate_document",
    "validate_registry",
]
