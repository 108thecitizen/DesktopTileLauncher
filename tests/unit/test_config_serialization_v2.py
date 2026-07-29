# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import gc
import json
import logging
import weakref
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import NoReturn, cast
from unittest.mock import Mock

import pytest

import config_migration as migration
import config_schema_v2 as schema
import config_serialization_v2 as serialization

pytestmark = pytest.mark.unit

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "schema_v2"


class _InjectedSerializationError(Exception):
    """Weak-referenceable private failure used to prove exception disposal."""


class _FailingUtf8Text(str):
    """A dumps result whose UTF-8 conversion exercises the encoder failure route."""

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        raise _InjectedSerializationError(str(self))


def _load_fixture(name: str) -> schema.JsonObject:
    parsed = json.loads(
        (FIXTURE_ROOT / name).read_text(encoding="utf-8"),
        object_pairs_hook=schema.reject_duplicate_json_members,
    )
    if type(parsed) is not dict:
        raise AssertionError("fixture root must be an object")
    return cast(schema.JsonObject, parsed)


def _minimal() -> schema.JsonObject:
    return _load_fixture("minimal.json")


def _representative() -> schema.JsonObject:
    return _load_fixture("representative.json")


def _canonical_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")


def _reverse_object_insertions(value: object) -> object:
    if type(value) is dict:
        mapping = cast(dict[str, object], value)
        return {
            key: _reverse_object_insertions(item)
            for key, item in reversed(list(mapping.items()))
        }
    if type(value) is list:
        return [_reverse_object_insertions(item) for item in cast(list[object], value)]
    return value


def _candidate_with_canonical_size(size: int) -> schema.JsonObject:
    document = _minimal()
    document["extensions"] = {"padding": ""}
    baseline = _canonical_bytes(document)
    delta = size - len(baseline)
    if delta < 2:
        raise AssertionError("target must leave room for Unicode padding")
    padding = ("é" * (delta // 2)) + ("x" if delta % 2 else "")
    document["extensions"] = {"padding": padding}
    actual = _canonical_bytes(document)
    if len(actual) != size:
        raise AssertionError("candidate size construction is not exact")
    return document


def _assert_rejected(
    result: object,
    expected_category: (
        migration.PureExecutionRejectionCategory | migration.PureEngineFailureCategory
    ),
    expected_stage: migration.PureExecutionStage,
    sentinels: tuple[str, ...] = (),
) -> serialization.V2SerializationRejected:
    assert type(result) is serialization.V2SerializationRejected  # nosec B101
    rejection = cast(serialization.V2SerializationRejected, result)
    assert tuple(field.name for field in fields(rejection)) == (  # nosec B101
        "category",
        "stage",
    )
    assert type(rejection).__slots__ == ("category", "stage")  # nosec B101
    assert not hasattr(rejection, "__dict__")  # nosec B101
    assert {  # nosec B101
        name for name in dir(rejection) if not name.startswith("__")
    } == {"category", "stage"}
    assert rejection.category is expected_category  # nosec B101
    assert rejection.stage is expected_stage  # nosec B101
    rendered = f"{rejection!s} {rejection!r}"
    for sentinel in sentinels:
        assert sentinel not in rendered  # nosec B101
    return rejection


def _assert_silent_and_private(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    sentinels: tuple[str, ...],
) -> None:
    captured = capsys.readouterr()
    assert captured.out == ""  # nosec B101
    assert captured.err == ""  # nosec B101
    for sentinel in sentinels:
        assert sentinel not in caplog.text  # nosec B101
    for record in caplog.records:
        rendered = repr((record.msg, record.args, vars(record)))
        for sentinel in sentinels:
            assert sentinel not in rendered  # nosec B101


def test_validation_precedes_serialization(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "validation-rejection-private-4d76e94a"
    document = _minimal()
    document["unexpected"] = sentinel

    def forbidden_dumps(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("json.dumps must not receive an invalid candidate")

    monkeypatch.setattr(serialization.json, "dumps", forbidden_dumps)
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    result = serialization.serialize_v2(document)

    _assert_rejected(
        result,
        migration.PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE,
        migration.PureExecutionStage.TARGET_VALIDATION,
        (sentinel,),
    )
    _assert_silent_and_private(capsys, caplog, (sentinel,))


def test_serializer_uses_exact_dumps_call_and_utf8_framing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _representative()
    original = deepcopy(document)
    expected = _canonical_bytes(document)
    real_dumps = serialization.json.dumps
    dumps_spy = Mock(wraps=real_dumps)
    monkeypatch.setattr(serialization.json, "dumps", dumps_spy)

    result = serialization.serialize_v2(document)

    assert dumps_spy.call_count == 1  # nosec B101
    call = dumps_spy.call_args
    assert call is not None  # nosec B101
    assert call.args == (document,)  # nosec B101
    assert call.args[0] is document  # nosec B101
    assert call.kwargs == {  # nosec B101
        "ensure_ascii": False,
        "sort_keys": True,
        "indent": 2,
        "allow_nan": False,
    }
    assert isinstance(result, serialization.SerializedV2Document)  # nosec B101
    assert result.data == expected  # nosec B101
    assert result.byte_count == len(result.data)  # nosec B101
    assert result.data.startswith(b"{\n")  # nosec B101
    assert not result.data.startswith(b"\xef\xbb\xbf")  # nosec B101
    assert b"\r" not in result.data  # nosec B101
    assert not result.data.endswith(b"\n")  # nosec B101
    assert "Café 東京".encode() in result.data  # nosec B101
    assert document == original  # nosec B101


def test_repeated_and_equivalent_object_insertions_are_byte_deterministic() -> None:
    first = _representative()
    second = deepcopy(first)
    third = cast(schema.JsonObject, _reverse_object_insertions(deepcopy(first)))
    fourth = cast(
        schema.JsonObject,
        json.loads(
            json.dumps(first, ensure_ascii=False),
            object_pairs_hook=schema.reject_duplicate_json_members,
        ),
    )

    results = [
        serialization.serialize_v2(candidate)
        for candidate in (first, first, second, third, fourth)
    ]

    assert all(  # nosec B101
        isinstance(result, serialization.SerializedV2Document) for result in results
    )
    data = [cast(serialization.SerializedV2Document, result).data for result in results]
    assert len(set(data)) == 1  # nosec B101


def test_canonical_serialization_preserves_every_array_order_and_extension_value() -> (
    None
):
    document = _representative()

    result = serialization.serialize_v2(document)

    assert isinstance(result, serialization.SerializedV2Document)  # nosec B101
    decoded = json.loads(
        result.data,
        object_pairs_hook=schema.reject_duplicate_json_members,
    )
    assert decoded == document  # nosec B101
    assert decoded["extensions"]["root.example.test"]["ordered"] == [2, 1]  # nosec B101
    assert decoded["tabs"][0]["display_order"] == [  # nosec B101
        "40000000-0000-4000-8000-000000000002",
        "40000000-0000-4000-8000-000000000001",
        "40000000-0000-4000-8000-000000000003",
    ]
    assert decoded["tabs"][0]["kanban_order"]["new"] == [  # nosec B101
        "40000000-0000-4000-8000-000000000001"
    ]


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "nonfinite", "wrong_version", "dangling"],
)
def test_malformed_candidates_are_validation_rejections(mutation: str) -> None:
    document = _minimal()
    if mutation == "missing":
        del document["application"]
    elif mutation == "extra":
        document["unexpected"] = True
    elif mutation == "nonfinite":
        document["extensions"] = {"bad": float("nan")}
    elif mutation == "wrong_version":
        document["schema_version"] = 1
    else:
        cast(dict[str, object], document["application"])["default_workspace_id"] = (
            "90000000-0000-4000-8000-000000000001"
        )

    result = serialization.serialize_v2(document)

    _assert_rejected(
        result,
        migration.PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE,
        migration.PureExecutionStage.TARGET_VALIDATION,
    )


def test_deep_valid_extension_has_sanitized_encoder_failure() -> None:
    secret = "private-payload-fragment.example.test"
    document = _minimal()
    payload: dict[str, object] = {"leaf": secret}
    for _ in range(2_000):
        payload = {"nested": payload}
    document["extensions"] = cast(schema.Extensions, payload)
    assert schema.validate_v2(document)  # nosec B101

    result = serialization.serialize_v2(document)

    _assert_rejected(
        result,
        migration.PureEngineFailureCategory.SERIALIZATION_FAILURE,
        migration.PureExecutionStage.SERIALIZATION,
        (secret,),
    )


def test_injected_encoder_exception_is_sanitized_silent_and_not_retained(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = (
        "injected-exception-private-7ea43bbf",
        "injected-candidate-title-3c2eaa58",
        "https://injected-candidate-6cc8c73c.example.test/path",
        "injected-extension-key-0b7c29ef",
        "injected-extension-value-7e5b3391",
    )
    document = _representative()
    cast(dict[str, object], document["application"])["title"] = sentinels[1]
    resource = cast(list[dict[str, object]], document["resources"])[0]
    cast(dict[str, object], resource["target"])["url"] = sentinels[2]
    document["extensions"] = {sentinels[3]: sentinels[4]}
    exception_ref: weakref.ReferenceType[_InjectedSerializationError] | None = None

    def raising_dumps(*_args: object, **_kwargs: object) -> NoReturn:
        nonlocal exception_ref
        error = _InjectedSerializationError(sentinels[0])
        exception_ref = weakref.ref(error)
        raise error

    monkeypatch.setattr(serialization.json, "dumps", raising_dumps)
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    result = serialization.serialize_v2(document)
    gc.collect()

    _assert_rejected(
        result,
        migration.PureEngineFailureCategory.SERIALIZATION_FAILURE,
        migration.PureExecutionStage.SERIALIZATION,
        sentinels,
    )
    assert exception_ref is not None  # nosec B101
    assert exception_ref() is None  # nosec B101
    _assert_silent_and_private(capsys, caplog, sentinels)


def test_utf8_encoding_exception_uses_serialization_failure_mapping(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "utf8-encoding-private-00f0540f"
    document = _representative()

    def unencodable_dumps(*_args: object, **_kwargs: object) -> str:
        return _FailingUtf8Text(sentinel)

    monkeypatch.setattr(serialization.json, "dumps", unencodable_dumps)
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    result = serialization.serialize_v2(document)

    _assert_rejected(
        result,
        migration.PureEngineFailureCategory.SERIALIZATION_FAILURE,
        migration.PureExecutionStage.SERIALIZATION,
        (sentinel,),
    )
    _assert_silent_and_private(capsys, caplog, (sentinel,))


def test_exact_four_mib_unicode_candidate_is_accepted_by_encoded_byte_count() -> None:
    document = _candidate_with_canonical_size(serialization.MAX_V2_CANDIDATE_BYTES)
    canonical = _canonical_bytes(document)
    assert len(canonical.decode("utf-8")) < len(canonical)  # nosec B101

    result = serialization.serialize_v2(document)

    assert isinstance(result, serialization.SerializedV2Document)  # nosec B101
    assert result.byte_count == 4 * 1024 * 1024  # nosec B101
    assert len(result.data) == serialization.MAX_V2_CANDIDATE_BYTES  # nosec B101


def test_four_mib_plus_one_unicode_candidate_is_rejected() -> None:
    document = _candidate_with_canonical_size(serialization.MAX_V2_CANDIDATE_BYTES + 1)
    private_padding = cast(
        str, cast(dict[str, object], document["extensions"])["padding"]
    )
    assert len(private_padding) < len(private_padding.encode("utf-8"))  # nosec B101

    result = serialization.serialize_v2(document)

    _assert_rejected(
        result,
        migration.PureEngineFailureCategory.CANDIDATE_SIZE_LIMIT_EXCEEDED,
        migration.PureExecutionStage.SERIALIZATION,
        ("ééé", "padding"),
    )


def test_rejections_and_success_repr_do_not_expose_candidate_content(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = (
        "https://private.example.test/secret",
        "Private launcher title",
        "private-extension-content",
        "private-payload-fragment",
        "90000000-0000-4000-8000-000000008812",
        "private-extension-key-f05a52a2",
    )
    invalid = _minimal()
    application = cast(dict[str, object], invalid["application"])
    application["title"] = sentinels[1]
    application["default_workspace_id"] = sentinels[4]
    invalid["extensions"] = {
        sentinels[5]: {
            "url": sentinels[0],
            "extension": sentinels[2],
            "payload": sentinels[3],
        }
    }
    invalid["unexpected"] = sentinels[3]
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    caplog.clear()

    rejection = serialization.serialize_v2(invalid)
    success = serialization.serialize_v2(_representative())

    _assert_rejected(
        rejection,
        migration.PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE,
        migration.PureExecutionStage.TARGET_VALIDATION,
        sentinels,
    )
    assert isinstance(success, serialization.SerializedV2Document)  # nosec B101
    rendered = f"{rejection!r} {rejection!s} {success!r}"
    for sentinel in sentinels:
        assert sentinel not in rendered  # nosec B101
    assert "Café" not in repr(success)  # nosec B101
    assert "example.test" not in repr(success)  # nosec B101
    _assert_silent_and_private(capsys, caplog, sentinels)
