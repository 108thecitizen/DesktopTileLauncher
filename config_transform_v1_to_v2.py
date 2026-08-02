# SPDX-License-Identifier: Apache-2.0
"""Checked dormant composition of the pure schema-v1-to-v2 transform.

Logical candidate construction remains isolated in :mod:`config_migration_v2`
for the future migration-engine adapter.  This module composes that stage with
the existing complete schema-v2 validator, canonical serializer, and inclusive
candidate-size ceiling without registering or persisting schema version 2.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

import config_migration_v2 as construction
import config_schema as v1
import config_schema_v2 as v2
import config_serialization_v2 as serialization

V1ToV2TransformResult: TypeAlias = (
    v2.Root | serialization.V2SerializationRejected | None
)


def transform_v1_to_v2(
    document: Mapping[str, v1.JsonValue],
) -> V1ToV2TransformResult:
    """Return one serializer-admissible candidate or a content-free rejection."""

    candidate = construction.migrate_v1_to_v2(document)
    if candidate is None:
        return None
    serialized = serialization.serialize_v2(candidate)
    if isinstance(serialized, serialization.V2SerializationRejected):
        return serialized
    return candidate


__all__ = ["V1ToV2TransformResult", "transform_v1_to_v2"]
