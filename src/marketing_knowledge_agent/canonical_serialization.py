"""Transient canonical serialization for Google source snapshots."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .sheets_contracts import SpreadsheetSnapshot


def serialize_source_snapshot(snapshot: SpreadsheetSnapshot) -> bytes:
    """Return deterministic in-memory bytes for a validated source snapshot.

    The returned bytes are fingerprint input, not a persistence-ready snapshot
    artifact. Incidental order for sheets, cells, and merge ranges is
    canonicalized. Semantic sequences such as text format runs retain their
    validated source order.
    """

    if not isinstance(snapshot, SpreadsheetSnapshot):
        raise TypeError("SOURCE_SNAPSHOT_TYPE_INVALID")

    primitive = _snapshot_primitive(snapshot)
    try:
        serialized = json.dumps(
            primitive,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("SOURCE_SNAPSHOT_NOT_CANONICALLY_SERIALIZABLE") from error
    return serialized.encode("utf-8")


def compute_source_fingerprint(snapshot: SpreadsheetSnapshot) -> str:
    """Return the SHA-256 identity of the Google source snapshot state."""

    digest = hashlib.sha256(serialize_source_snapshot(snapshot)).hexdigest()
    return f"sha256:{digest}"


def _snapshot_primitive(snapshot: SpreadsheetSnapshot) -> Any:
    primitive = _canonical_primitive(snapshot)
    sheets = primitive["sheets"]
    for sheet in sheets:
        sheet["cells"] = sorted(
            sheet["cells"],
            key=lambda cell: (cell["row_index"], cell["column_index"]),
        )
        sheet["merges"] = sorted(
            sheet["merges"],
            key=lambda merge: (
                merge["sheet_id"],
                merge["start_row_index"],
                merge["end_row_index"],
                merge["start_column_index"],
                merge["end_column_index"],
            ),
        )
    primitive["sheets"] = sorted(sheets, key=lambda sheet: sheet["sheet_id"])
    return primitive


def _canonical_primitive(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_primitive(_model_fields(value))
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("SOURCE_SNAPSHOT_MAPPING_KEY_INVALID")
        return {key: _canonical_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_primitive(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("SOURCE_SNAPSHOT_FLOAT_NON_FINITE")
        return value
    raise TypeError("SOURCE_SNAPSHOT_VALUE_TYPE_INVALID")


def _model_fields(model: BaseModel) -> Mapping[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(
            mode="python",
            by_alias=False,
            exclude_unset=False,
            exclude_defaults=False,
            exclude_none=False,
        )
    return model.dict(  # pragma: no cover - exercised only with Pydantic 1.x
        by_alias=False,
        exclude_unset=False,
        exclude_defaults=False,
        exclude_none=False,
    )


__all__ = ["compute_source_fingerprint", "serialize_source_snapshot"]
