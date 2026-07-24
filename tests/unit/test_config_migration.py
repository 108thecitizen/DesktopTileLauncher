# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

import config_migration as migration
import config_recovery
import config_schema
from config_recovery import MAX_CONFIG_BYTES

pytestmark = pytest.mark.unit


def _accept(
    _document: Mapping[str, migration.JsonValue],
) -> migration.ValidationDecision:
    return migration.ValidationAccepted()


def _validated(spec: migration.RegistrySpec) -> migration.ValidatedMigrationRegistry:
    result = migration.validate_registry(spec)
    assert isinstance(result, migration.RegistryReady)  # nosec B101
    return result.registry


_EMPTY_REGISTRY = _validated(migration.RegistrySpec(None, None, (), ()))


def _native_v1_document() -> config_schema.JsonObject:
    identifiers = iter(
        (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        )
    )
    return config_schema.build_native_v1(lambda: next(identifiers))


def _single_step_registry(
    transform: migration.StepTransform,
    *,
    target_validator: migration.Validator = _accept,
) -> migration.ValidatedMigrationRegistry:
    return _validated(
        migration.RegistrySpec(
            0,
            1,
            (migration.MigrationStep(0, 1, "legacy_to_v1", transform),),
            (
                migration.VersionValidator(0, _accept),
                migration.VersionValidator(1, target_validator),
            ),
        )
    )


def _prepared_single_step(
    transform: migration.StepTransform,
    *,
    target_validator: migration.Validator = _accept,
) -> migration.PreparedMigration:
    result = migration.prepare_migration(
        {"title": "Legacy", "unknown": {"items": ["kept"]}},
        _single_step_registry(transform, target_validator=target_validator),
    )
    assert isinstance(result, migration.PreparedMigration)  # nosec B101
    return result


def _recovery_files(config_path: Path) -> list[Path]:
    return sorted((config_path.parent / "recovery").glob("config-*.recovery"))


def _failed_candidate_files(config_path: Path) -> list[Path]:
    return sorted((config_path.parent / "recovery").glob("config-*.failed-candidate"))


def _temporary_residue(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in (".partial", ".tmp")
    )


def _forbid_legacy_constructor(_mapping: dict[str, object]) -> object:
    raise AssertionError("migration result must not enter legacy construction")


def test_production_registry_prepares_exactly_v0_to_v1() -> None:
    source: migration.JsonObject = {
        "title": "Legacy",
        "unknown": {"private": "not rendered"},
    }

    result = migration.prepare_migration(source, migration.PRODUCTION_REGISTRY)

    assert isinstance(result, migration.PreparedMigration)  # nosec B101
    assert result.source_version == 0  # nosec B101
    assert result.target_version == 1  # nosec B101
    assert result.step_count == 1  # nosec B101
    assert (
        migration.migration_startup_route(result)
        is migration.MigrationStartupRoute.MIGRATION_REQUIRED
    )
    assert "private" not in repr(result)  # nosec B101
    assert source == {  # nosec B101
        "title": "Legacy",
        "unknown": {"private": "not rendered"},
    }


def test_production_nonfinite_unknown_fails_before_preservation_or_step(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"Legacy","ignored":NaN}'
    config_path.write_bytes(original)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
        legacy_validator=config_recovery.validate_legacy_mapping,
    )

    assert isinstance(result, migration.PureEngineFailure)  # nosec B101
    assert (  # nosec B101
        result.category is migration.PureEngineFailureCategory.JSON_DETACHMENT_FAILURE
    )
    assert result.stage is migration.PureExecutionStage.SOURCE_VALIDATION  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


@pytest.mark.parametrize(
    ("source", "expected_category"),
    [
        (
            b'{"schema_version":99,"future":true}',
            migration.VersionRejectionCategory.UNSUPPORTED_NEWER,
        ),
        (
            b'{"schema_version":0,"title":"invalid"}',
            migration.VersionRejectionCategory.MALFORMED_VERSION,
        ),
    ],
)
def test_production_unsupported_or_malformed_version_never_constructs_or_writes(
    tmp_path: Path,
    source: bytes,
    expected_category: migration.VersionRejectionCategory,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(source)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
    )

    assert isinstance(result, migration.VersionRejected)  # nosec B101
    assert result.category is expected_category  # nosec B101
    assert config_path.read_bytes() == source  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


@pytest.mark.parametrize(
    "value",
    [0, -1, True, False, 1.0, "1", None, [], {}],
)
def test_invalid_explicit_versions_are_malformed_without_coercion(
    value: object,
) -> None:
    document = cast(migration.JsonObject, {"schema_version": value})

    result = migration.identify_version(document)

    assert isinstance(result, migration.VersionRejected)  # nosec B101
    assert result.category is migration.VersionRejectionCategory.MALFORMED_VERSION
    assert (
        migration.migration_startup_route(result)
        is migration.MigrationStartupRoute.EXIT_ONLY
    )


@pytest.mark.parametrize("version", [1, 2, 10**30])
def test_positive_explicit_versions_are_identified_exactly(version: int) -> None:
    result = migration.identify_version({"schema_version": version})

    assert result == migration.ExplicitVersion(version)  # nosec B101


def test_production_accepts_strict_v1_and_rejects_explicit_v2() -> None:
    current = _native_v1_document()

    accepted = migration.prepare_migration(current, migration.PRODUCTION_REGISTRY)
    future = deepcopy(current)
    future["schema_version"] = 2
    rejected = migration.prepare_migration(future, migration.PRODUCTION_REGISTRY)

    assert isinstance(accepted, migration.VersionedCurrent)  # nosec B101
    assert accepted.version == 1  # nosec B101
    assert isinstance(rejected, migration.VersionRejected)  # nosec B101
    assert rejected.category is migration.VersionRejectionCategory.UNSUPPORTED_NEWER
    assert rejected.version == 2  # nosec B101


def test_production_v0_migrates_transactionally_without_legacy_construction(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = (
        b'{"title":"Legacy","tabs":["Main"],"tiles":[],"unknown":{"retained":true}}'
    )
    config_path.write_bytes(original)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
        legacy_validator=config_recovery.validate_legacy_mapping,
    )

    assert isinstance(result, migration.MigrationCommitted)  # nosec B101
    assert result.target_version == 1  # nosec B101
    assert config_schema.validate_v1(result.document)  # nosec B101
    extensions = cast(dict[str, object], result.document["extensions"])
    assert extensions == {  # nosec B101
        config_schema.LEGACY_EXTENSION_NAMESPACE: {"unknown": {"retained": True}}
    }
    serialized = migration.serialize_deterministically(result.document)
    assert isinstance(serialized, migration.SerializedDocument)  # nosec B101
    assert config_path.read_bytes() == serialized.data  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_production_known_incompatible_v0_uses_q3_recovery_without_artifacts(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"columns":true}'
    config_path.write_bytes(original)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
        legacy_validator=config_recovery.validate_legacy_mapping,
    )

    assert isinstance(result, config_recovery.ConfigRecoveryRequired)  # nosec B101
    assert (  # nosec B101
        result.category
        is config_recovery.ConfigLoadFailureCategory.LEGACY_CONSTRUCTION_FAILURE
    )
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_production_current_v1_startup_is_exact_no_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    document = _native_v1_document()
    original = json.dumps(document, ensure_ascii=False, separators=(", ", ": ")).encode(
        "utf-8"
    )
    config_path.write_bytes(original)
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
        legacy_validator=config_recovery.validate_legacy_mapping,
    )

    assert isinstance(result, migration.VersionedCurrent)  # nosec B101
    assert result.document == document  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_production_current_v1_with_lone_surrogate_is_exit_only_and_no_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    document = _native_v1_document()
    application = cast(dict[str, migration.JsonValue], document["application"])
    application["title"] = "\ud800"
    original = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    assert b"\\ud800" in original  # nosec B101
    config_path.write_bytes(original)
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
        legacy_validator=config_recovery.validate_legacy_mapping,
    )

    assert isinstance(result, migration.PureExecutionRejected)  # nosec B101
    assert (  # nosec B101
        result.category
        is migration.PureExecutionRejectionCategory.SOURCE_VALIDATION_FAILURE
    )
    assert result.stage is migration.PureExecutionStage.SOURCE_VALIDATION  # nosec B101
    assert (  # nosec B101
        migration.startup_failure_route(result)
        is migration.StartupFailureRoute.EXIT_ONLY
    )
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_production_empty_legacy_tab_rejects_after_exact_preservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"Legacy","tabs":[""],"tiles":[]}'
    config_path.write_bytes(original)
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
        legacy_validator=config_recovery.validate_legacy_mapping,
    )

    assert isinstance(  # nosec B101
        result, migration.MigrationAbortedAfterPreservation
    )
    assert isinstance(result.problem, migration.PureExecutionRejected)  # nosec B101
    assert (  # nosec B101
        result.problem.category
        is migration.PureExecutionRejectionCategory.STEP_REJECTION
    )
    assert result.problem.stage is migration.PureExecutionStage.STEP  # nosec B101
    assert result.problem.step_name == "legacy_to_v1"  # nosec B101
    assert result.authority is migration.ConfigurationAuthority.ORIGINAL  # nosec B101
    assert result.recovery_copy_count == 1  # nosec B101
    assert result.failed_candidate_copy_count == 0  # nosec B101
    assert result.rollback_count == 0  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_production_invalid_applied_output_uses_target_validation_after_preservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"Legacy","tabs":["Main"],"tiles":[]}'
    config_path.write_bytes(original)

    def invalid_target(
        _document: Mapping[str, config_schema.JsonValue],
    ) -> config_schema.JsonObject:
        return {"schema_version": 1}

    monkeypatch.setattr(migration, "migrate_v0_to_v1", invalid_target)
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
        legacy_validator=config_recovery.validate_legacy_mapping,
    )

    assert isinstance(  # nosec B101
        result, migration.MigrationAbortedAfterPreservation
    )
    assert isinstance(result.problem, migration.PureExecutionRejected)  # nosec B101
    assert (  # nosec B101
        result.problem.category
        is migration.PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE
    )
    assert (  # nosec B101
        result.problem.stage is migration.PureExecutionStage.TARGET_VALIDATION
    )
    assert result.problem.step_name == "legacy_to_v1"  # nosec B101
    assert result.authority is migration.ConfigurationAuthority.ORIGINAL  # nosec B101
    assert result.recovery_copy_count == 1  # nosec B101
    assert result.failed_candidate_copy_count == 0  # nosec B101
    assert result.rollback_count == 0  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def _step(
    source: int,
    target: int,
    name: str,
    transform: migration.StepTransform | None = None,
) -> migration.MigrationStep:
    return migration.MigrationStep(
        source,
        target,
        name,
        transform or (lambda document: migration.StepApplied(document)),
    )


def _validator(
    version: int, callback: migration.Validator = _accept
) -> migration.VersionValidator:
    return migration.VersionValidator(version, callback)


@pytest.mark.parametrize(
    ("spec", "category"),
    [
        (
            migration.RegistrySpec(None, 1, (), ()),
            migration.RegistryRejectionCategory.INVALID_BOUNDS,
        ),
        (
            migration.RegistrySpec(1, 0, (), ()),
            migration.RegistryRejectionCategory.INVALID_BOUNDS,
        ),
        (
            migration.RegistrySpec(0, 1, (_step(0, 2, "bad_endpoint"),), ()),
            migration.RegistryRejectionCategory.INVALID_STEP_ENDPOINT,
        ),
        (
            migration.RegistrySpec(
                0,
                1,
                (_step(0, 1, "first"), _step(0, 1, "second")),
                (),
            ),
            migration.RegistryRejectionCategory.DUPLICATE_STEP_SOURCE,
        ),
        (
            migration.RegistrySpec(0, 2, (_step(0, 1, "only_first"),), ()),
            migration.RegistryRejectionCategory.STEP_GAP,
        ),
        (
            migration.RegistrySpec(0, 1, (_step(0, 1, "Unsafe Name"),), ()),
            migration.RegistryRejectionCategory.UNSAFE_STEP_NAME,
        ),
        (
            migration.RegistrySpec(
                0,
                2,
                (_step(0, 1, "duplicate"), _step(1, 2, "duplicate")),
                (),
            ),
            migration.RegistryRejectionCategory.DUPLICATE_STEP_NAME,
        ),
        (
            migration.RegistrySpec(
                0,
                1,
                (_step(0, 1, "valid"),),
                (_validator(0), _validator(2)),
            ),
            migration.RegistryRejectionCategory.INVALID_VALIDATOR_VERSION,
        ),
        (
            migration.RegistrySpec(
                0,
                1,
                (_step(0, 1, "valid"),),
                (_validator(0), _validator(0)),
            ),
            migration.RegistryRejectionCategory.DUPLICATE_VALIDATOR_VERSION,
        ),
        (
            migration.RegistrySpec(
                0,
                1,
                (_step(0, 1, "valid"),),
                (_validator(0),),
            ),
            migration.RegistryRejectionCategory.MISSING_VALIDATOR,
        ),
    ],
)
def test_invalid_registry_shapes_return_curated_categories(
    spec: migration.RegistrySpec,
    category: migration.RegistryRejectionCategory,
) -> None:
    result = migration.validate_registry(spec)

    assert result == migration.RegistryRejected(category)  # nosec B101
    assert "lambda" not in repr(result)  # nosec B101
    assert migration.migration_diagnostics(result) == {  # nosec B101
        "failure_count": 1,
        "failure_kind": "registry_rejection",
        "failure_category": category.value,
    }


@pytest.mark.parametrize("unsafe_name", [b"bytes", 7, None, object()])
def test_registry_rejects_every_non_string_step_name(unsafe_name: object) -> None:
    spec = migration.RegistrySpec(
        0,
        1,
        (
            migration.MigrationStep(
                0,
                1,
                cast(str, unsafe_name),
                lambda document: migration.StepApplied(document),
            ),
        ),
        (_validator(0), _validator(1)),
    )

    result = migration.validate_registry(spec)

    assert result == migration.RegistryRejected(  # nosec B101
        migration.RegistryRejectionCategory.UNSAFE_STEP_NAME
    )


@pytest.mark.parametrize(
    ("spec", "category"),
    [
        (
            migration.RegistrySpec(0, 10**100, (), ()),
            migration.RegistryRejectionCategory.STEP_GAP,
        ),
        (
            migration.RegistrySpec(10**100, 10**100, (), ()),
            migration.RegistryRejectionCategory.MISSING_VALIDATOR,
        ),
    ],
)
def test_sparse_huge_registry_bounds_return_without_materializing_ranges(
    spec: migration.RegistrySpec,
    category: migration.RegistryRejectionCategory,
) -> None:
    result = migration.validate_registry(spec)

    assert result == migration.RegistryRejected(category)  # nosec B101


def test_current_version_is_validated_without_preparing_steps() -> None:
    calls: list[migration.JsonObject] = []

    def validate_current(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        calls.append(dict(document))
        return migration.ValidationAccepted()

    registry = _validated(
        migration.RegistrySpec(1, 1, (), (_validator(1, validate_current),))
    )
    source: migration.JsonObject = {"schema_version": 1, "name": "Current"}

    result = migration.prepare_migration(source, registry)

    assert isinstance(result, migration.VersionedCurrent)  # nosec B101
    assert result.document == source  # nosec B101
    assert calls == [source]  # nosec B101
    assert (
        migration.migration_startup_route(result)
        is migration.MigrationStartupRoute.CURRENT
    )


def test_current_version_validator_can_reject_before_any_step() -> None:
    def reject_current(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        return migration.ValidationRejected()

    registry = _validated(
        migration.RegistrySpec(1, 1, (), (_validator(1, reject_current),))
    )
    source: migration.JsonObject = {"schema_version": 1, "valid_json": True}
    original = deepcopy(source)

    result = migration.prepare_migration(source, registry)

    assert isinstance(result, migration.PureExecutionRejected)  # nosec B101
    assert (
        result.category
        is migration.PureExecutionRejectionCategory.SOURCE_VALIDATION_FAILURE
    )
    assert source == original  # nosec B101
    assert (
        migration.migration_startup_route(result)
        is migration.MigrationStartupRoute.EXIT_ONLY
    )


def _synthetic_run() -> tuple[
    migration.SerializedMigration,
    list[str],
    list[migration.JsonObject],
    migration.JsonObject,
]:
    events: list[str] = []
    returned_documents: list[migration.JsonObject] = []
    source: migration.JsonObject = {
        "title": "Café 東京",
        "unknown_top_level": {"items": ["preserved"]},
    }
    original = deepcopy(source)

    def validator(version: int) -> migration.Validator:
        def validate(
            document: Mapping[str, migration.JsonValue],
        ) -> migration.ValidationDecision:
            events.append(f"validate_{version}")
            if version == 0:
                assert "schema_version" not in document  # nosec B101
            else:
                assert document["schema_version"] == version  # nosec B101
            return migration.ValidationAccepted()

        return validate

    def migrate_0_to_1(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        events.append("step_0_to_1")
        assert document["unknown_top_level"] == {  # nosec B101
            "items": ["preserved"]
        }
        candidate = dict(document)
        candidate["schema_version"] = 1
        candidate["history"] = ["v1"]
        returned_documents.append(candidate)
        return migration.StepApplied(candidate)

    def migrate_1_to_2(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        events.append("step_1_to_2")
        candidate = dict(document)
        candidate["schema_version"] = 2
        candidate["history"] = ["v1", "v2"]
        returned_documents.append(candidate)
        return migration.StepApplied(candidate)

    registry = _validated(
        migration.RegistrySpec(
            0,
            2,
            (
                _step(1, 2, "v1_to_v2", migrate_1_to_2),
                _step(0, 1, "v0_to_v1", migrate_0_to_1),
            ),
            (
                _validator(2, validator(2)),
                _validator(0, validator(0)),
                _validator(1, validator(1)),
            ),
        )
    )

    prepared = migration.prepare_migration(source, registry)
    assert isinstance(prepared, migration.PreparedMigration)  # nosec B101
    assert prepared.source_version == 0  # nosec B101
    assert prepared.target_version == 2  # nosec B101
    assert prepared.step_count == 2  # nosec B101
    assert source == original  # nosec B101

    executed = migration.execute_prepared_migration(prepared)
    assert isinstance(executed, migration.SerializedMigration)  # nosec B101
    assert source == original  # nosec B101
    return executed, events, returned_documents, source


def test_synthetic_v0_to_v1_to_v2_is_consecutive_detached_and_deterministic() -> None:
    first, first_events, returned_documents, source = _synthetic_run()
    second, second_events, _, second_source = _synthetic_run()

    assert first_events == [  # nosec B101
        "validate_0",
        "step_0_to_1",
        "validate_1",
        "step_1_to_2",
        "validate_2",
    ]
    assert second_events == first_events  # nosec B101
    assert first.serialized == second.serialized  # nosec B101
    assert first.document == second.document  # nosec B101
    assert source == second_source  # nosec B101
    assert first.document["unknown_top_level"] == {  # nosec B101
        "items": ["preserved"]
    }
    assert "workspaces" not in first.document  # nosec B101
    assert "id" not in first.document  # nosec B101
    assert first.serialized.decode("utf-8") == json.dumps(  # nosec B101
        first.document,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    assert not first.serialized.endswith(b"\n")  # nosec B101
    assert b"\r\n" not in first.serialized  # nosec B101

    returned_documents[-1]["history"] = ["mutated after return"]
    assert first.document["history"] == ["v1", "v2"]  # nosec B101


def test_intermediate_validator_rejection_stops_before_later_step() -> None:
    calls: list[str] = []

    def first_step(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        calls.append("first_step")
        candidate = dict(document)
        candidate["schema_version"] = 1
        return migration.StepApplied(candidate)

    def reject_intermediate(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        calls.append("reject_intermediate")
        return migration.ValidationRejected()

    def forbidden_step(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        calls.append("forbidden_step")
        return migration.StepRejected()

    registry = _validated(
        migration.RegistrySpec(
            0,
            2,
            (
                _step(0, 1, "first", first_step),
                _step(1, 2, "forbidden", forbidden_step),
            ),
            (
                _validator(0),
                _validator(1, reject_intermediate),
                _validator(2),
            ),
        )
    )
    prepared = migration.prepare_migration({"title": "Legacy"}, registry)
    assert isinstance(prepared, migration.PreparedMigration)  # nosec B101

    result = migration.execute_prepared_migration(prepared)

    assert isinstance(result, migration.PureExecutionRejected)  # nosec B101
    assert (
        result.category
        is migration.PureExecutionRejectionCategory.INTERMEDIATE_VALIDATION_FAILURE
    )
    assert result.stage is migration.PureExecutionStage.INTERMEDIATE_VALIDATION
    assert calls == ["first_step", "reject_intermediate"]  # nosec B101


@pytest.mark.parametrize(
    ("transform", "expected_type", "expected_category"),
    [
        (
            lambda _document: migration.StepRejected(),
            migration.PureExecutionRejected,
            migration.PureExecutionRejectionCategory.STEP_REJECTION,
        ),
        (
            lambda _document: cast(migration.StepDecision, object()),
            migration.PureEngineDefect,
            migration.PureEngineDefectCategory.INVALID_CALLBACK_RESULT,
        ),
        (
            lambda _document: migration.StepApplied(
                cast(Mapping[str, migration.JsonValue], {"schema_version": {1}})
            ),
            migration.PureEngineDefect,
            migration.PureEngineDefectCategory.INVALID_STEP_OUTPUT,
        ),
        (
            lambda _document: migration.StepApplied({"schema_version": True}),
            migration.PureEngineDefect,
            migration.PureEngineDefectCategory.UNEXPECTED_TARGET_VERSION,
        ),
        (
            lambda _document: migration.StepApplied({"schema_version": 2}),
            migration.PureEngineDefect,
            migration.PureEngineDefectCategory.UNEXPECTED_TARGET_VERSION,
        ),
    ],
)
def test_step_contract_outcomes_are_distinct_and_curated(
    transform: migration.StepTransform,
    expected_type: type[object],
    expected_category: object,
) -> None:
    result = migration.execute_prepared_migration(_prepared_single_step(transform))

    assert isinstance(result, expected_type)  # nosec B101
    assert result.category is expected_category  # type: ignore[union-attr]  # nosec B101


def test_callback_exception_text_and_captured_source_are_not_exposed() -> None:
    secret = "https://example.test/?token=do-not-log"

    def explode(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        raise RuntimeError(secret)

    prepared = _prepared_single_step(explode)
    result = migration.execute_prepared_migration(prepared)

    assert isinstance(result, migration.PureEngineDefect)  # nosec B101
    assert result.category is migration.PureEngineDefectCategory.CALLBACK_EXCEPTION
    rendered = repr([prepared, result, migration.migration_diagnostics(result)])
    assert secret not in rendered  # nosec B101
    assert "RuntimeError" not in rendered  # nosec B101
    assert (
        migration.migration_startup_route(result)
        is migration.MigrationStartupRoute.EXIT_ONLY
    )


def test_target_validator_rejection_is_distinct_from_step_rejection() -> None:
    def valid_step(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        candidate = dict(document)
        candidate["schema_version"] = 1
        return migration.StepApplied(candidate)

    def reject_target(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        return migration.ValidationRejected()

    result = migration.execute_prepared_migration(
        _prepared_single_step(valid_step, target_validator=reject_target)
    )

    assert isinstance(result, migration.PureExecutionRejected)  # nosec B101
    assert (
        result.category
        is migration.PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE
    )
    assert result.stage is migration.PureExecutionStage.TARGET_VALIDATION


def test_deterministic_serializer_is_unicode_sorted_lf_and_no_final_newline() -> None:
    result = migration.serialize_deterministically(
        {"z": 2, "schema_version": 1, "a": "Café 東京"}
    )

    assert isinstance(result, migration.SerializedDocument)  # nosec B101
    assert result.data == (  # nosec B101
        '{\n  "a": "Café 東京",\n  "schema_version": 1,\n  "z": 2\n}'
    ).encode("utf-8")
    assert result.byte_count == len(result.data)  # nosec B101


def _candidate_with_serialized_size(size: int) -> migration.JsonObject:
    empty: migration.JsonObject = {"padding": "", "schema_version": 1}
    baseline = json.dumps(
        empty,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    assert size >= len(baseline)  # nosec B101
    return {"padding": "x" * (size - len(baseline)), "schema_version": 1}


def test_candidate_exactly_four_mib_is_accepted() -> None:
    result = migration.serialize_deterministically(
        _candidate_with_serialized_size(MAX_CONFIG_BYTES)
    )

    assert isinstance(result, migration.SerializedDocument)  # nosec B101
    assert result.byte_count == MAX_CONFIG_BYTES  # nosec B101


def test_candidate_four_mib_plus_one_is_curated_pre_write_failure() -> None:
    result = migration.serialize_deterministically(
        _candidate_with_serialized_size(MAX_CONFIG_BYTES + 1)
    )

    assert isinstance(result, migration.PureEngineFailure)  # nosec B101
    assert (
        result.category
        is migration.PureEngineFailureCategory.CANDIDATE_SIZE_LIMIT_EXCEEDED
    )
    assert result.stage is migration.PureExecutionStage.SERIALIZATION
    assert "padding" not in repr(result)  # nosec B101


def test_non_finite_candidate_is_rejected_without_rendering_value() -> None:
    result = migration.serialize_deterministically(
        {"schema_version": 1, "value": float("nan")}
    )

    assert isinstance(result, migration.PureEngineFailure)  # nosec B101
    assert result.category is migration.PureEngineFailureCategory.SERIALIZATION_FAILURE
    assert "nan" not in repr(result).lower()  # nosec B101


def test_real_filesystem_v0_to_v1_to_v2_transaction_is_ordered_and_deterministic(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    source: migration.JsonObject = {
        "title": "Café 東京",
        "unknown_top_level": {"items": ["preserved"]},
    }
    original = json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    config_path.write_bytes(original)
    events: list[str] = []

    def validate_0(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        events.append("validate_0")
        assert "schema_version" not in document  # nosec B101
        assert _recovery_files(config_path) == []  # nosec B101
        return migration.ValidationAccepted()

    def validate_version(
        version: int,
    ) -> migration.Validator:
        def validate(
            document: Mapping[str, migration.JsonValue],
        ) -> migration.ValidationDecision:
            events.append(f"validate_{version}")
            assert document["schema_version"] == version  # nosec B101
            return migration.ValidationAccepted()

        return validate

    def migrate_0_to_1(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        events.append("step_0_to_1")
        recovery_files = _recovery_files(config_path)
        assert len(recovery_files) == 1  # nosec B101
        assert recovery_files[0].read_bytes() == original  # nosec B101
        assert document["unknown_top_level"] == {  # nosec B101
            "items": ["preserved"]
        }
        candidate = dict(document)
        candidate["schema_version"] = 1
        candidate["history"] = ["v1"]
        return migration.StepApplied(candidate)

    def migrate_1_to_2(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        events.append("step_1_to_2")
        candidate = dict(document)
        candidate["schema_version"] = 2
        candidate["history"] = ["v1", "v2"]
        return migration.StepApplied(candidate)

    registry = _validated(
        migration.RegistrySpec(
            0,
            2,
            (
                _step(0, 1, "v0_to_v1", migrate_0_to_1),
                _step(1, 2, "v1_to_v2", migrate_1_to_2),
            ),
            (
                _validator(0, validate_0),
                _validator(1, validate_version(1)),
                _validator(2, validate_version(2)),
            ),
        )
    )

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        registry,
    )

    assert isinstance(result, migration.MigrationCommitted)  # nosec B101
    assert events == [  # nosec B101
        "validate_0",
        "step_0_to_1",
        "validate_1",
        "step_1_to_2",
        "validate_2",
        "validate_2",
    ]
    serialized = migration.serialize_deterministically(result.document)
    assert isinstance(serialized, migration.SerializedDocument)  # nosec B101
    assert config_path.read_bytes() == serialized.data  # nosec B101
    assert result.byte_count == len(serialized.data)  # nosec B101
    assert result.document["unknown_top_level"] == {  # nosec B101
        "items": ["preserved"]
    }
    assert "workspaces" not in result.document  # nosec B101
    assert "id" not in result.document  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_current_validator_rejection_creates_no_artifact_or_write(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{  "schema_version" : 1, "title" : "unchanged" }'
    config_path.write_bytes(original)

    def reject_current(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        return migration.ValidationRejected()

    registry = _validated(
        migration.RegistrySpec(1, 1, (), (_validator(1, reject_current),))
    )

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        registry,
    )

    assert isinstance(result, migration.PureExecutionRejected)  # nosec B101
    assert (
        result.category
        is migration.PureExecutionRejectionCategory.SOURCE_VALIDATION_FAILURE
    )
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_step_rejection_after_preservation_keeps_exact_original_and_no_temp(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{\r\n  "title": "Caf\xc3\xa9",\r\n  "unknown": true\r\n}'
    config_path.write_bytes(original)
    registry = _single_step_registry(lambda _document: migration.StepRejected())

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        registry,
    )

    assert isinstance(  # nosec B101
        result, migration.MigrationAbortedAfterPreservation
    )
    assert isinstance(result.problem, migration.PureExecutionRejected)  # nosec B101
    assert (
        result.problem.category
        is migration.PureExecutionRejectionCategory.STEP_REJECTION
    )
    assert result.recovery_copy_count == 1  # nosec B101
    assert result.failed_candidate_copy_count == 0  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def _size_candidate_registry(size: int) -> migration.ValidatedMigrationRegistry:
    candidate = _candidate_with_serialized_size(size)

    def create_sized_candidate(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        return migration.StepApplied(candidate)

    return _single_step_registry(create_sized_candidate)


def test_exact_maximum_candidate_commits_through_real_transaction(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    config_path.write_bytes(original)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _size_candidate_registry(MAX_CONFIG_BYTES),
    )

    assert isinstance(result, migration.MigrationCommitted)  # nosec B101
    assert result.byte_count == MAX_CONFIG_BYTES  # nosec B101
    assert len(config_path.read_bytes()) == MAX_CONFIG_BYTES  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_maximum_plus_one_candidate_stops_after_preservation_without_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{ "title": "legacy" }'
    config_path.write_bytes(original)
    writer_calls: list[tuple[Path, int]] = []

    def forbidden_writer(
        path: Path,
        data: bytes,
        *,
        before_replace: object = None,
    ) -> None:
        del before_replace
        writer_calls.append((path, len(data)))
        raise AssertionError("oversized candidate reached atomic writer")

    monkeypatch.setattr(migration, "atomic_write_bytes", forbidden_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _size_candidate_registry(MAX_CONFIG_BYTES + 1),
    )

    assert isinstance(  # nosec B101
        result, migration.MigrationAbortedAfterPreservation
    )
    assert isinstance(result.problem, migration.PureEngineFailure)  # nosec B101
    assert (
        result.problem.category
        is migration.PureEngineFailureCategory.CANDIDATE_SIZE_LIMIT_EXCEEDED
    )
    assert result.recovery_copy_count == 1  # nosec B101
    assert writer_calls == []  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_post_write_target_rejection_retains_candidate_and_rolls_back_exactly(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = (
        b'{\r\n  "unknown": "Caf\xc3\xa9",\r\n  "title": "Legacy",\r\n'
        b'  "items": [2, 1]\r\n}'
    )
    config_path.write_bytes(original)
    events: list[str] = []
    target_validation_count = 0

    def validate_legacy(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        events.append("validate_0")
        return migration.ValidationAccepted()

    def validate_target(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        nonlocal target_validation_count
        target_validation_count += 1
        events.append(f"validate_1_{target_validation_count}")
        if target_validation_count == 1:
            return migration.ValidationAccepted()
        return migration.ValidationRejected()

    def migrate_to_1(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        events.append("step_0_to_1")
        candidate = dict(document)
        candidate["schema_version"] = 1
        return migration.StepApplied(candidate)

    registry = _validated(
        migration.RegistrySpec(
            0,
            1,
            (_step(0, 1, "v0_to_v1", migrate_to_1),),
            (
                _validator(0, validate_legacy),
                _validator(1, validate_target),
            ),
        )
    )
    candidate_document: migration.JsonObject = cast(
        migration.JsonObject,
        json.loads(original.decode("utf-8")),
    )
    candidate_document["schema_version"] = 1
    expected_candidate = migration.serialize_deterministically(candidate_document)
    assert isinstance(expected_candidate, migration.SerializedDocument)  # nosec B101

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        registry,
    )

    assert isinstance(result, migration.MigrationRolledBack)  # nosec B101
    assert (
        result.category
        is migration.TransactionFailureCategory.POST_WRITE_VALIDATION_FAILURE
    )
    assert result.authority is migration.ConfigurationAuthority.RESTORED_ORIGINAL
    assert result.recovery_copy_count == 1  # nosec B101
    assert result.failed_candidate_copy_count == 1  # nosec B101
    assert result.rollback_count == 1  # nosec B101
    assert events == [  # nosec B101
        "validate_0",
        "step_0_to_1",
        "validate_1_1",
        "validate_1_2",
        "validate_0",
    ]
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert [path.read_bytes() for path in _failed_candidate_files(config_path)] == [
        expected_candidate.data
    ]
    assert _temporary_residue(tmp_path) == []  # nosec B101
    final_load = config_recovery.load_raw_config(config_path)
    assert isinstance(final_load, config_recovery.RawConfigLoaded)  # nosec B101
    assert final_load.source_bytes == original  # nosec B101
    assert final_load.mapping == json.loads(original.decode("utf-8"))  # nosec B101


def test_guarded_legacy_normalization_save_does_not_overwrite_future_race(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(b'{"title":"legacy"}')

    loaded = migration.load_startup_configuration(
        config_path,
        lambda mapping: dict(mapping),
        _EMPTY_REGISTRY,
    )
    assert isinstance(loaded, migration.ImplicitLegacyLoaded)  # nosec B101

    future_bytes = b'{ "schema_version": 99, "future": true }'
    config_path.write_bytes(future_bytes)
    result = migration.guarded_legacy_normalization_save(
        config_path,
        loaded.raw,
        '{"title":"normalized legacy"}',
    )

    assert isinstance(result, migration.LegacyNormalizationSaveFailed)  # nosec B101
    assert (
        result.category
        is migration.LegacyNormalizationSaveFailureCategory.SOURCE_CHANGED
    )
    assert config_path.read_bytes() == future_bytes  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_startup_routing_keeps_q3_recovery_separate_from_exit_only() -> None:
    q3 = config_recovery.ConfigRecoveryRequired(
        config_recovery.ConfigLoadFailureCategory.MALFORMED_JSON
    )
    defect = migration.PureEngineDefect(
        migration.PureEngineDefectCategory.CALLBACK_EXCEPTION,
        migration.PureExecutionStage.STEP,
        0,
        1,
        "safe_step",
    )
    exit_only: list[migration.ExitOnlyFailure] = [
        migration.VersionRejected(migration.VersionRejectionCategory.MALFORMED_VERSION),
        defect,
        migration.MigrationAbortedAfterPreservation(defect),
        migration.MigrationRolledBack(
            migration.TransactionFailureCategory.POST_WRITE_VALIDATION_FAILURE,
            defect,
            failed_candidate_copy_count=1,
        ),
        migration.MigrationTransactionFailed(
            migration.TransactionFailureCategory.SOURCE_CHANGED,
            migration.ConfigurationAuthority.EXTERNAL_CURRENT,
            1,
        ),
        migration.LegacyNormalizationSaveFailed(
            migration.LegacyNormalizationSaveFailureCategory.SOURCE_CHANGED
        ),
    ]

    assert (  # nosec B101
        migration.startup_failure_route(q3) is migration.StartupFailureRoute.Q3_RECOVERY
    )
    assert all(
        migration.startup_failure_route(outcome)
        is migration.StartupFailureRoute.EXIT_ONLY
        for outcome in exit_only
    )  # nosec B101


def test_transaction_callback_failure_is_private_in_every_rendering_layer(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    secret = "https://example.test/?token=never-render-this"
    config_path.write_text(
        json.dumps({"title": secret}, ensure_ascii=False),
        encoding="utf-8",
    )

    def explode(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        raise RuntimeError(secret)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _single_step_registry(explode),
    )
    assert isinstance(  # nosec B101
        result, migration.MigrationAbortedAfterPreservation
    )
    assert isinstance(result.problem, migration.PureEngineDefect)  # nosec B101

    transaction_fields = migration.transaction_diagnostics(result)
    startup_fields = migration.startup_failure_diagnostics(result)
    notice_category = migration.startup_notice_category(result)
    notice_message = migration.startup_notice_message(notice_category)
    error = migration.ConfigurationMigrationError.from_outcome(result)
    try:
        raise error
    except migration.ConfigurationMigrationError as caught:
        assert caught.__cause__ is None  # nosec B101
        assert caught.__context__ is None  # nosec B101
        rendered = repr(
            [
                result,
                result.problem,
                transaction_fields,
                startup_fields,
                notice_category,
                notice_message,
                repr(caught),
                str(caught),
                caught.diagnostics,
            ]
        )

    assert notice_category is migration.StartupNoticeCategory.MIGRATION_FAILED
    assert notice_message == (  # nosec B101
        "The saved configuration could not be migrated safely. "
        "The application will exit."
    )
    assert secret not in rendered  # nosec B101
    assert "RuntimeError" not in rendered  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def _forbidden_candidate_writer(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("candidate writer must not run")


def _file_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"file symlinks unavailable: {type(error).__name__}")


def _post_write_rejection_registry(
    source_validator: migration.Validator = _accept,
) -> migration.ValidatedMigrationRegistry:
    target_validation_count = 0

    def migrate_to_v1(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        candidate = dict(document)
        candidate["schema_version"] = 1
        return migration.StepApplied(candidate)

    def reject_second_target_validation(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        nonlocal target_validation_count
        target_validation_count += 1
        if target_validation_count == 1:
            return migration.ValidationAccepted()
        return migration.ValidationRejected()

    return _validated(
        migration.RegistrySpec(
            0,
            1,
            (_step(0, 1, "legacy_to_v1", migrate_to_v1),),
            (
                _validator(0, source_validator),
                _validator(1, reject_second_target_validation),
            ),
        )
    )


def _expected_v1_candidate(original: bytes) -> bytes:
    document = cast(migration.JsonObject, json.loads(original.decode("utf-8")))
    document["schema_version"] = 1
    serialized = migration.serialize_deterministically(document)
    assert isinstance(serialized, migration.SerializedDocument)  # nosec B101
    return serialized.data


def _assert_transaction_failure(
    result: object,
    *,
    category: migration.TransactionFailureCategory,
    authority: migration.ConfigurationAuthority,
    recovery_count: int,
    candidate_count: int,
    rollback_count: int,
) -> migration.MigrationTransactionFailed:
    assert isinstance(result, migration.MigrationTransactionFailed)  # nosec B101
    assert result.category is category  # nosec B101
    assert result.authority is authority  # nosec B101
    assert result.recovery_copy_count == recovery_count  # nosec B101
    assert result.failed_candidate_copy_count == candidate_count  # nosec B101
    assert result.rollback_count == rollback_count  # nosec B101
    return result


def test_unsupported_older_filesystem_config_is_classified_without_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{ "schema_version": 1, "title": "older" }'
    config_path.write_bytes(original)
    registry = _validated(migration.RegistrySpec(2, 2, (), (_validator(2),)))
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        registry,
    )

    assert isinstance(result, migration.VersionRejected)  # nosec B101
    assert result.category is migration.VersionRejectionCategory.UNSUPPORTED_OLDER
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_missing_startup_config_returns_missing_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        migration.PRODUCTION_REGISTRY,
    )

    assert isinstance(result, config_recovery.ConfigMissing)  # nosec B101
    assert list(tmp_path.iterdir()) == []  # nosec B101


def test_valid_current_filesystem_config_is_exact_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{  "schema_version" : 1, "title" : "current" }'
    config_path.write_bytes(original)
    registry = _validated(migration.RegistrySpec(1, 1, (), (_validator(1),)))
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        registry,
    )

    assert isinstance(result, migration.VersionedCurrent)  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


@pytest.mark.parametrize(
    ("rejected_version", "expected_stage"),
    [
        (1, migration.PureExecutionStage.INTERMEDIATE_VALIDATION),
        (2, migration.PureExecutionStage.TARGET_VALIDATION),
    ],
)
def test_filesystem_validation_rejection_after_preservation_never_writes_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rejected_version: int,
    expected_stage: migration.PureExecutionStage,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy","unknown":true}'
    config_path.write_bytes(original)

    def migrate(version: int) -> migration.StepTransform:
        def transform(
            document: Mapping[str, migration.JsonValue],
        ) -> migration.StepDecision:
            candidate = dict(document)
            candidate["schema_version"] = version
            return migration.StepApplied(candidate)

        return transform

    def validate(version: int) -> migration.Validator:
        def validator(
            _document: Mapping[str, migration.JsonValue],
        ) -> migration.ValidationDecision:
            if version == rejected_version:
                return migration.ValidationRejected()
            return migration.ValidationAccepted()

        return validator

    registry = _validated(
        migration.RegistrySpec(
            0,
            2,
            (
                _step(0, 1, "legacy_to_v1", migrate(1)),
                _step(1, 2, "v1_to_v2", migrate(2)),
            ),
            (
                _validator(0),
                _validator(1, validate(1)),
                _validator(2, validate(2)),
            ),
        )
    )
    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        registry,
    )

    assert isinstance(result, migration.MigrationAbortedAfterPreservation)  # nosec B101
    assert isinstance(result.problem, migration.PureExecutionRejected)  # nosec B101
    assert result.problem.stage is expected_stage  # nosec B101
    assert result.recovery_copy_count == 1  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_redirected_migration_source_fails_before_step_or_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.json"
    config_path = tmp_path / "config.json"
    original = b'{"title":"redirected legacy"}'
    target.write_bytes(original)
    _file_symlink_or_skip(config_path, target)
    step_calls = 0

    def transform(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        nonlocal step_calls
        step_calls += 1
        return migration.StepApplied(document)

    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)
    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _single_step_registry(transform),
    )

    failure = _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.PRESERVATION_FAILURE,
        authority=migration.ConfigurationAuthority.ORIGINAL,
        recovery_count=0,
        candidate_count=0,
        rollback_count=0,
    )
    assert (
        failure.recovery_category
        is config_recovery.RecoveryFailureCategory.SOURCE_UNAVAILABLE
    )
    assert step_calls == 0  # nosec B101
    assert target.read_bytes() == original  # nosec B101
    assert config_path.is_symlink()  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_guarded_legacy_normalization_succeeds_for_unchanged_regular_source(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_bytes(b'{"title":"legacy"}')
    loaded = migration.load_startup_configuration(
        config_path,
        lambda mapping: dict(mapping),
        _EMPTY_REGISTRY,
    )
    assert isinstance(loaded, migration.ImplicitLegacyLoaded)  # nosec B101

    result = migration.guarded_legacy_normalization_save(
        config_path,
        loaded.raw,
        '{"title":"normalized"}',
    )

    assert isinstance(result, migration.LegacyNormalizationSaved)  # nosec B101
    assert config_path.read_bytes() == b'{"title":"normalized"}'  # nosec B101
    assert _recovery_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_guarded_legacy_normalization_accepts_unchanged_redirected_source(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    config_path = tmp_path / "config.json"
    original = b'{"title":"redirected legacy"}'
    target.write_bytes(original)
    _file_symlink_or_skip(config_path, target)
    loaded = migration.load_startup_configuration(
        config_path,
        lambda mapping: dict(mapping),
        _EMPTY_REGISTRY,
    )
    assert isinstance(loaded, migration.ImplicitLegacyLoaded)  # nosec B101

    result = migration.guarded_legacy_normalization_save(
        config_path,
        loaded.raw,
        '{"title":"normalized"}',
    )

    assert isinstance(result, migration.LegacyNormalizationSaved)  # nosec B101
    assert config_path.read_bytes() == b'{"title":"normalized"}'  # nosec B101
    assert not config_path.is_symlink()  # nosec B101
    assert target.read_bytes() == original  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_unsafe_recovery_location_blocks_step_and_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    config_path.write_bytes(original)
    (tmp_path / "recovery").write_bytes(b"obstruction")
    step_calls = 0

    def transform(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        nonlocal step_calls
        step_calls += 1
        return migration.StepApplied(document)

    monkeypatch.setattr(migration, "atomic_write_bytes", _forbidden_candidate_writer)
    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _single_step_registry(transform),
    )

    failure = _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.PRESERVATION_FAILURE,
        authority=migration.ConfigurationAuthority.ORIGINAL,
        recovery_count=0,
        candidate_count=0,
        rollback_count=0,
    )
    assert (
        failure.recovery_category
        is config_recovery.RecoveryFailureCategory.RECOVERY_DIRECTORY_FAILURE
    )
    assert step_calls == 0  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_step_source_replacement_is_caught_by_candidate_guard(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    external = b'{"schema_version":99,"external":true}'
    config_path.write_bytes(original)

    def replace_source(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        config_path.write_bytes(external)
        candidate = dict(document)
        candidate["schema_version"] = 1
        return migration.StepApplied(candidate)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _single_step_registry(replace_source),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.SOURCE_CHANGED,
        authority=migration.ConfigurationAuthority.UNKNOWN,
        recovery_count=1,
        candidate_count=0,
        rollback_count=0,
    )
    assert config_path.read_bytes() == external  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_step_recovery_artifact_mutation_blocks_candidate_installation(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    config_path.write_bytes(original)

    def corrupt_recovery(
        document: Mapping[str, migration.JsonValue],
    ) -> migration.StepDecision:
        _recovery_files(config_path)[0].write_bytes(b"corrupt recovery")
        candidate = dict(document)
        candidate["schema_version"] = 1
        return migration.StepApplied(candidate)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _single_step_registry(corrupt_recovery),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.ORIGINAL_REVERIFICATION_FAILURE,
        authority=migration.ConfigurationAuthority.ORIGINAL,
        recovery_count=1,
        candidate_count=0,
        rollback_count=0,
    )
    assert config_path.read_bytes() == original  # nosec B101
    assert _recovery_files(config_path)[0].read_bytes() == b"corrupt recovery"
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_candidate_writer_failure_preserves_original_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    config_path.write_bytes(original)

    def fail_writer(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic candidate writer failure")

    monkeypatch.setattr(migration, "atomic_write_bytes", fail_writer)
    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _single_step_registry(
            lambda document: migration.StepApplied({**document, "schema_version": 1})
        ),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.CANDIDATE_WRITE_FAILURE,
        authority=migration.ConfigurationAuthority.ORIGINAL,
        recovery_count=1,
        candidate_count=0,
        rollback_count=0,
    )
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _temporary_residue(tmp_path) == []  # nosec B101


@pytest.mark.parametrize(
    ("fault_mode", "expected_authority"),
    [
        ("reload_failure", migration.ConfigurationAuthority.UNKNOWN),
        ("byte_mismatch", migration.ConfigurationAuthority.EXTERNAL_CURRENT),
    ],
)
def test_unproven_post_write_candidate_is_never_retained_or_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_mode: str,
    expected_authority: migration.ConfigurationAuthority,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    external = b'{"schema_version":77,"external":true}'
    config_path.write_bytes(original)
    real_load = migration.load_raw_config
    load_count = 0

    def load_with_fault(path: Path) -> config_recovery.RawConfigLoadResult:
        nonlocal load_count
        load_count += 1
        if load_count == 2:
            path.write_bytes(external)
            if fault_mode == "reload_failure":
                return config_recovery.ConfigRecoveryRequired(
                    config_recovery.ConfigLoadFailureCategory.FILE_READ_FAILURE
                )
        return real_load(path)

    monkeypatch.setattr(migration, "load_raw_config", load_with_fault)
    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _single_step_registry(
            lambda document: migration.StepApplied({**document, "schema_version": 1})
        ),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.POST_WRITE_SOURCE_CHANGED,
        authority=expected_authority,
        recovery_count=1,
        candidate_count=0,
        rollback_count=0,
    )
    assert config_path.read_bytes() == external  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_candidate_change_after_retention_blocks_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    external = b'{"schema_version":88,"external":true}'
    config_path.write_bytes(original)
    real_retain = migration.retain_failed_candidate

    def retain_then_replace(
        path: Path,
        snapshot: config_recovery.SourceSnapshot,
        candidate: bytes,
    ) -> config_recovery.CandidateRetentionResult:
        retained = real_retain(path, snapshot, candidate)
        path.write_bytes(external)
        return retained

    monkeypatch.setattr(migration, "retain_failed_candidate", retain_then_replace)
    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _post_write_rejection_registry(),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED,
        authority=migration.ConfigurationAuthority.UNKNOWN,
        recovery_count=1,
        candidate_count=1,
        rollback_count=0,
    )
    assert config_path.read_bytes() == external  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert len(_failed_candidate_files(config_path)) == 1  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


@pytest.mark.parametrize("fault_mode", ["artifact_verification", "artifact_read"])
def test_unverified_original_artifact_never_drives_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_mode: str,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    expected_candidate = _expected_v1_candidate(original)
    config_path.write_bytes(original)
    failure = config_recovery.RecoveryVerificationFailed(
        config_recovery.RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
    )
    if fault_mode == "artifact_verification":
        monkeypatch.setattr(
            migration, "verify_preserved_artifact", lambda _source: failure
        )
    else:
        monkeypatch.setattr(migration, "read_preserved_bytes", lambda _source: failure)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _post_write_rejection_registry(),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.ROLLBACK_ARTIFACT_FAILURE,
        authority=migration.ConfigurationAuthority.CANDIDATE,
        recovery_count=1,
        candidate_count=1,
        rollback_count=0,
    )
    assert config_path.read_bytes() == expected_candidate  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert len(_failed_candidate_files(config_path)) == 1  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_candidate_retention_failure_precedes_trigger_after_successful_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    config_path.write_bytes(original)
    monkeypatch.setattr(
        migration,
        "retain_failed_candidate",
        lambda *_args: config_recovery.CandidateRetentionFailed(
            config_recovery.CandidateRetentionFailureCategory.CANDIDATE_COPY_FAILURE
        ),
    )

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _post_write_rejection_registry(),
    )

    assert isinstance(result, migration.MigrationRolledBack)  # nosec B101
    assert (
        result.category
        is migration.TransactionFailureCategory.CANDIDATE_RETENTION_FAILURE
    )
    assert result.authority is migration.ConfigurationAuthority.RESTORED_ORIGINAL
    assert result.recovery_copy_count == 1  # nosec B101
    assert result.failed_candidate_copy_count == 0  # nosec B101
    assert result.rollback_count == 1  # nosec B101
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert _failed_candidate_files(config_path) == []  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_rollback_writer_failure_is_distinct_and_never_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    expected_candidate = _expected_v1_candidate(original)
    config_path.write_bytes(original)
    real_writer = migration.atomic_write_bytes
    writer_count = 0

    def fail_second_writer(
        path: Path,
        data: bytes,
        *,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
        nonlocal writer_count
        writer_count += 1
        if writer_count == 2:
            raise OSError("synthetic rollback writer failure")
        real_writer(path, data, before_replace=before_replace)

    monkeypatch.setattr(migration, "atomic_write_bytes", fail_second_writer)
    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _post_write_rejection_registry(),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.ROLLBACK_WRITE_FAILURE,
        authority=migration.ConfigurationAuthority.CANDIDATE,
        recovery_count=1,
        candidate_count=1,
        rollback_count=0,
    )
    assert config_path.read_bytes() == expected_candidate  # nosec B101
    assert len(_failed_candidate_files(config_path)) == 1  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


@pytest.mark.parametrize(
    ("fault_mode", "expected_authority"),
    [
        ("reload", migration.ConfigurationAuthority.UNKNOWN),
        ("validation", migration.ConfigurationAuthority.RESTORED_ORIGINAL),
        ("artifact", migration.ConfigurationAuthority.RESTORED_ORIGINAL),
    ],
)
def test_final_rollback_failures_are_distinct_and_never_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_mode: str,
    expected_authority: migration.ConfigurationAuthority,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    config_path.write_bytes(original)
    source_validation_count = 0

    def source_validator(
        _document: Mapping[str, migration.JsonValue],
    ) -> migration.ValidationDecision:
        nonlocal source_validation_count
        source_validation_count += 1
        if fault_mode == "validation" and source_validation_count == 2:
            return migration.ValidationRejected()
        return migration.ValidationAccepted()

    if fault_mode == "reload":
        real_load = migration.load_raw_config
        load_count = 0

        def fail_rollback_reload(path: Path) -> config_recovery.RawConfigLoadResult:
            nonlocal load_count
            load_count += 1
            if load_count == 3:
                return config_recovery.ConfigRecoveryRequired(
                    config_recovery.ConfigLoadFailureCategory.FILE_READ_FAILURE
                )
            return real_load(path)

        monkeypatch.setattr(migration, "load_raw_config", fail_rollback_reload)
    elif fault_mode == "artifact":
        real_verify = migration.verify_preserved_artifact
        verification_count = 0

        def fail_final_artifact(
            source: config_recovery.PreservedSource,
        ) -> config_recovery.RecoveryVerificationResult:
            nonlocal verification_count
            verification_count += 1
            if verification_count == 3:
                return config_recovery.RecoveryVerificationFailed(
                    config_recovery.RecoveryFailureCategory.RECOVERY_VERIFICATION_FAILURE
                )
            return real_verify(source)

        monkeypatch.setattr(migration, "verify_preserved_artifact", fail_final_artifact)

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _post_write_rejection_registry(source_validator),
    )

    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.ROLLBACK_VERIFICATION_FAILURE,
        authority=expected_authority,
        recovery_count=1,
        candidate_count=1,
        rollback_count=1,
    )
    assert config_path.read_bytes() == original  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert len(_failed_candidate_files(config_path)) == 1  # nosec B101
    assert _temporary_residue(tmp_path) == []  # nosec B101


def test_failure_diagnostics_follow_a_compact_privacy_allowlist() -> None:
    defect = migration.PureEngineDefect(
        migration.PureEngineDefectCategory.CALLBACK_EXCEPTION,
        migration.PureExecutionStage.STEP,
        0,
        1,
        "safe_step",
    )
    outcomes: list[migration.ExitOnlyFailure] = [
        migration.VersionRejected(
            migration.VersionRejectionCategory.UNSUPPORTED_NEWER,
            99,
        ),
        migration.PureExecutionRejected(
            migration.PureExecutionRejectionCategory.STEP_REJECTION,
            migration.PureExecutionStage.STEP,
            0,
            1,
            "safe_step",
        ),
        defect,
        migration.MigrationRolledBack(
            migration.TransactionFailureCategory.CANDIDATE_RETENTION_FAILURE,
            defect,
            config_recovery.CandidateRetentionFailureCategory.CANDIDATE_COPY_FAILURE,
            failed_candidate_copy_count=0,
        ),
        migration.MigrationTransactionFailed(
            migration.TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED,
            migration.ConfigurationAuthority.UNKNOWN,
            1,
            failed_candidate_copy_count=1,
            candidate_retention_category=(
                config_recovery.CandidateRetentionFailureCategory.SOURCE_CHANGED
            ),
        ),
        migration.LegacyNormalizationSaveFailed(
            migration.LegacyNormalizationSaveFailureCategory.SOURCE_CHANGED
        ),
    ]
    allowed_keys = {
        "failure_count",
        "failure_kind",
        "failure_category",
        "failure_stage",
        "source_version",
        "target_version",
        "step_name",
        "transaction_state",
        "configuration_authority",
        "recovery_copy_count",
        "failed_candidate_copy_count",
        "rollback_count",
        "candidate_retention_category",
        "recovery_category",
    }
    forbidden_fragments = (
        "https://",
        "private title",
        "C:\\private\\config.json",
        "RuntimeError",
        "sha256",
        ".recovery",
        ".failed-candidate",
    )

    rendered_parts: list[str] = []
    for outcome in outcomes:
        diagnostics = migration.startup_failure_diagnostics(outcome)
        assert set(diagnostics) <= allowed_keys  # nosec B101
        assert all(  # nosec B101
            isinstance(value, (str, int)) and not isinstance(value, bool)
            for value in diagnostics.values()
        )
        notice = migration.startup_notice_message(
            migration.startup_notice_category(outcome)
        )
        rendered_parts.extend((repr(outcome), repr(diagnostics), notice))

    candidate_retention = config_recovery.CandidateRetentionFailed(
        config_recovery.CandidateRetentionFailureCategory.CANDIDATE_COPY_FAILURE
    )
    rendered_parts.append(repr(candidate_retention))
    rendered = " ".join(rendered_parts)
    assert all(fragment not in rendered for fragment in forbidden_fragments)  # nosec B101


def test_rollback_writer_final_guard_rejects_late_candidate_ownership_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    original = b'{"title":"legacy"}'
    external = b'{"schema_version":73,"external":"late replacement"}'
    expected_candidate = _expected_v1_candidate(original)
    config_path.write_bytes(original)
    real_writer = migration.atomic_write_bytes
    atomic_writer_calls = 0

    def replace_before_final_rollback_guard(
        path: Path,
        data: bytes,
        *,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
        nonlocal atomic_writer_calls
        atomic_writer_calls += 1
        if atomic_writer_calls == 2:
            external_path = tmp_path / "external.json"
            external_path.write_bytes(external)
            external_path.replace(config_path)
        real_writer(path, data, before_replace=before_replace)

    monkeypatch.setattr(
        migration,
        "atomic_write_bytes",
        replace_before_final_rollback_guard,
    )

    result = migration.load_startup_configuration(
        config_path,
        _forbid_legacy_constructor,
        _post_write_rejection_registry(),
    )

    assert atomic_writer_calls == 2  # nosec B101
    _assert_transaction_failure(
        result,
        category=migration.TransactionFailureCategory.ROLLBACK_SOURCE_CHANGED,
        authority=migration.ConfigurationAuthority.UNKNOWN,
        recovery_count=1,
        candidate_count=1,
        rollback_count=0,
    )
    assert config_path.read_bytes() == external  # nosec B101
    assert [path.read_bytes() for path in _recovery_files(config_path)] == [original]
    assert [path.read_bytes() for path in _failed_candidate_files(config_path)] == [
        expected_candidate
    ]
    assert _temporary_residue(tmp_path) == []  # nosec B101
