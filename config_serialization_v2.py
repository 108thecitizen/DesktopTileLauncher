# SPDX-License-Identifier: Apache-2.0
"""Dormant canonical serialization for complete schema-version 2 candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Final, TypeAlias

from config_migration import (
    PureEngineFailureCategory as _PureEngineFailureCategory,
    PureExecutionRejectionCategory as _PureExecutionRejectionCategory,
    PureExecutionStage as _PureExecutionStage,
)
from config_schema_v2 import validate_v2

MAX_V2_CANDIDATE_BYTES: Final = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SerializedV2Document:
    """Canonical UTF-8 bytes admitted by the inclusive candidate ceiling."""

    data: bytes = field(repr=False)
    byte_count: int


@dataclass(frozen=True, slots=True)
class V2SerializationRejected:
    """A candidate failed without retaining or exposing its contents."""

    category: _PureExecutionRejectionCategory | _PureEngineFailureCategory
    stage: _PureExecutionStage


V2SerializationResult: TypeAlias = SerializedV2Document | V2SerializationRejected


def serialize_v2(
    document: object,
) -> V2SerializationResult:
    """Validate and canonically serialize one complete schema-v2 candidate."""

    if not validate_v2(document):
        return V2SerializationRejected(
            _PureExecutionRejectionCategory.TARGET_VALIDATION_FAILURE,
            _PureExecutionStage.TARGET_VALIDATION,
        )
    try:
        serialized = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        return V2SerializationRejected(
            _PureEngineFailureCategory.SERIALIZATION_FAILURE,
            _PureExecutionStage.SERIALIZATION,
        )
    if len(serialized) > MAX_V2_CANDIDATE_BYTES:
        return V2SerializationRejected(
            _PureEngineFailureCategory.CANDIDATE_SIZE_LIMIT_EXCEEDED,
            _PureExecutionStage.SERIALIZATION,
        )
    return SerializedV2Document(serialized, len(serialized))


__all__ = [
    "MAX_V2_CANDIDATE_BYTES",
    "SerializedV2Document",
    "V2SerializationRejected",
    "V2SerializationResult",
    "serialize_v2",
]
