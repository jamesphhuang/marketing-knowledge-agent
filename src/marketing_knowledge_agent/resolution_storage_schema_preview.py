from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence, Tuple

from .frontmatter import parse_markdown_with_frontmatter


OUTPUT_FILENAMES = (
    "resolution_storage_schema_summary.md",
    "authoritative_storage_assessment.md",
    "managed_parent_schema_preview.md",
    "managed_asset_schema_preview.md",
    "sqlite_schema_migration_preview.sql",
    "sqlite_schema_design_decision.md",
    "parent_round_trip_validation.csv",
    "asset_round_trip_validation.csv",
    "parent_sync_plan.csv",
    "excluded_parent_sync_report.csv",
    "temporary_migration_validation.md",
    "schema_backward_compatibility.md",
    "schema_rollback_plan.md",
    "next_resolution_apply_prerequisites.md",
)

PARENT_SYNC_RECORD_IDS = {
    "商家夥伴案例資料庫:r7",
    "商家夥伴案例資料庫:r12",
    "商家夥伴案例資料庫:r32",
    "商家夥伴案例資料庫:r122",
}
EXCLUDED_PARENT_RECORD_ID = "商家夥伴案例資料庫:r30"
ALLOWED_INDEX_ELIGIBILITY = {"include", "hold", "exclude"}
ALLOWED_SEARCH_ELIGIBILITY = {
    "searchable",
    "searchable_internal",
    "not_searchable",
    "excluded",
}
ELIGIBILITY_PAIRS = {
    ("include", "searchable"),
    ("include", "searchable_internal"),
    ("hold", "not_searchable"),
    ("exclude", "excluded"),
}

SQLITE_SCHEMA = """PRAGMA foreign_keys = ON;

CREATE TABLE source_records (
    record_id TEXT PRIMARY KEY,
    brand_name TEXT NOT NULL,
    merchant_handle TEXT,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('merchant', 'partner', 'other')),
    governance_eligibility TEXT NOT NULL
);

CREATE TABLE source_record_aliases (
    record_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    provenance TEXT NOT NULL,
    PRIMARY KEY (record_id, normalized_alias),
    FOREIGN KEY (record_id) REFERENCES source_records(record_id) ON DELETE CASCADE
);

CREATE INDEX idx_source_record_aliases_exact
    ON source_record_aliases(normalized_alias);

CREATE TABLE content_assets (
    asset_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_title TEXT NOT NULL,
    asset_url TEXT,
    canonical_url TEXT,
    asset_index_eligibility TEXT NOT NULL
        CHECK (asset_index_eligibility IN ('include', 'hold', 'exclude')),
    asset_search_eligibility TEXT NOT NULL
        CHECK (asset_search_eligibility IN
            ('searchable', 'searchable_internal', 'not_searchable', 'excluded')),
    eligibility_reason TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    can_external_reference INTEGER NOT NULL CHECK (can_external_reference IN (0, 1)),
    FOREIGN KEY (record_id) REFERENCES source_records(record_id) ON DELETE RESTRICT,
    CHECK (
        (asset_index_eligibility = 'include'
            AND asset_search_eligibility IN ('searchable', 'searchable_internal'))
        OR (asset_index_eligibility = 'hold'
            AND asset_search_eligibility = 'not_searchable')
        OR (asset_index_eligibility = 'exclude'
            AND asset_search_eligibility = 'excluded')
    )
);

CREATE INDEX idx_content_assets_record_id ON content_assets(record_id);
CREATE INDEX idx_content_assets_search_eligibility
    ON content_assets(asset_search_eligibility);
CREATE INDEX idx_content_assets_type ON content_assets(asset_type);
"""


class ResolutionStorageSchemaError(ValueError):
    """Raised when the storage preview cannot prove a fail-closed migration."""


@dataclass(frozen=True)
class ManagedParentAliasMetadata:
    record_id: str
    search_aliases: Tuple[str, ...]
    search_alias_reviewed_by: str
    search_alias_reviewed_at: str
    search_alias_provenance: str

    def __post_init__(self) -> None:
        _require_text(self.record_id, "record_id")
        _require_text(self.search_alias_reviewed_by, "search_alias_reviewed_by")
        _require_text(self.search_alias_reviewed_at, "search_alias_reviewed_at")
        _require_text(self.search_alias_provenance, "search_alias_provenance")
        if self.search_alias_reviewed_by != "Admin":
            raise ResolutionStorageSchemaError("search_alias_reviewed_by must be Admin")
        _validate_timestamp(self.search_alias_reviewed_at, "search_alias_reviewed_at")
        normalized = [_normalize_alias(alias) for alias in self.search_aliases]
        if any(not alias for alias in normalized):
            raise ResolutionStorageSchemaError("search_aliases cannot contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ResolutionStorageSchemaError("search_aliases must be unique after normalization")
        for value in (self.record_id, *self.search_aliases, self.search_alias_reviewed_by,
                      self.search_alias_reviewed_at, self.search_alias_provenance):
            _validate_round_trip_scalar(value)


@dataclass(frozen=True)
class AssetEligibilityMetadata:
    asset_id: str
    record_id: str
    asset_index_eligibility: str
    asset_search_eligibility: str
    eligibility_reason: str
    reviewed_by: str
    reviewed_at: str

    def __post_init__(self) -> None:
        for field, value in (
            ("asset_id", self.asset_id),
            ("record_id", self.record_id),
            ("eligibility_reason", self.eligibility_reason),
            ("reviewed_by", self.reviewed_by),
            ("reviewed_at", self.reviewed_at),
        ):
            _require_text(value, field)
            _validate_round_trip_scalar(value)
        if self.asset_index_eligibility not in ALLOWED_INDEX_ELIGIBILITY:
            raise ResolutionStorageSchemaError("invalid asset_index_eligibility")
        if self.asset_search_eligibility not in ALLOWED_SEARCH_ELIGIBILITY:
            raise ResolutionStorageSchemaError("invalid asset_search_eligibility")
        if (self.asset_index_eligibility, self.asset_search_eligibility) not in ELIGIBILITY_PAIRS:
            raise ResolutionStorageSchemaError("asset index/search eligibility conflict")
        if self.reviewed_by != "Admin":
            raise ResolutionStorageSchemaError("reviewed_by must be Admin")
        _validate_timestamp(self.reviewed_at, "reviewed_at")


def render_managed_parent(metadata: ManagedParentAliasMetadata) -> str:
    lines = [
        "---",
        f'record_id: "{metadata.record_id}"',
        "search_aliases:",
    ]
    lines.extend(f'  - "{alias}"' for alias in metadata.search_aliases)
    lines.extend(
        [
            f'search_alias_reviewed_by: "{metadata.search_alias_reviewed_by}"',
            f'search_alias_reviewed_at: "{metadata.search_alias_reviewed_at}"',
            f'search_alias_provenance: "{metadata.search_alias_provenance}"',
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def validate_managed_parent_round_trip(text: str) -> ManagedParentAliasMetadata:
    metadata, _ = parse_markdown_with_frontmatter(text)
    aliases = metadata.get("search_aliases")
    if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
        raise ResolutionStorageSchemaError("search_aliases must be a string list")
    parsed = ManagedParentAliasMetadata(
        record_id=_required_metadata_text(metadata, "record_id"),
        search_aliases=tuple(aliases),
        search_alias_reviewed_by=_required_metadata_text(metadata, "search_alias_reviewed_by"),
        search_alias_reviewed_at=_required_metadata_text(metadata, "search_alias_reviewed_at"),
        search_alias_provenance=_required_metadata_text(metadata, "search_alias_provenance"),
    )
    if render_managed_parent(parsed) != text:
        raise ResolutionStorageSchemaError("managed parent serialization is not deterministic")
    return parsed


def render_managed_asset(metadata: AssetEligibilityMetadata) -> str:
    lines = [
        "---",
        f'asset_id: "{metadata.asset_id}"',
        f'record_id: "{metadata.record_id}"',
        f'asset_index_eligibility: "{metadata.asset_index_eligibility}"',
        f'asset_search_eligibility: "{metadata.asset_search_eligibility}"',
        f'eligibility_reason: "{metadata.eligibility_reason}"',
        f'reviewed_by: "{metadata.reviewed_by}"',
        f'reviewed_at: "{metadata.reviewed_at}"',
        "---",
        "",
    ]
    return "\n".join(lines)


def validate_managed_asset_round_trip(text: str) -> AssetEligibilityMetadata:
    metadata, _ = parse_markdown_with_frontmatter(text)
    parsed = AssetEligibilityMetadata(
        asset_id=_required_metadata_text(metadata, "asset_id"),
        record_id=_required_metadata_text(metadata, "record_id"),
        asset_index_eligibility=_required_metadata_text(metadata, "asset_index_eligibility"),
        asset_search_eligibility=_required_metadata_text(metadata, "asset_search_eligibility"),
        eligibility_reason=_required_metadata_text(metadata, "eligibility_reason"),
        reviewed_by=_required_metadata_text(metadata, "reviewed_by"),
        reviewed_at=_required_metadata_text(metadata, "reviewed_at"),
    )
    if render_managed_asset(parsed) != text:
        raise ResolutionStorageSchemaError("managed asset serialization is not deterministic")
    return parsed


def create_temporary_sqlite_migration(
    db_path: Path,
    *,
    parents: Sequence[Mapping[str, object]],
    aliases: Sequence[Mapping[str, object]],
    assets: Sequence[Mapping[str, object]],
) -> dict:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.executescript(SQLITE_SCHEMA)
        connection.commit()
        rollback_verified = _verify_transaction_rollback(connection)
        try:
            connection.execute("BEGIN")
            connection.executemany(
                "INSERT INTO source_records VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        row["record_id"],
                        row["brand_name"],
                        _nullable_text(row.get("merchant_handle")),
                        row["entity_type"],
                        row["governance_eligibility"],
                    )
                    for row in parents
                ],
            )
            connection.executemany(
                "INSERT INTO source_record_aliases VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        row["record_id"],
                        row["alias"],
                        row["normalized_alias"],
                        row["reviewed_by"],
                        row["reviewed_at"],
                        row["provenance"],
                    )
                    for row in aliases
                ],
            )
            connection.executemany(
                "INSERT INTO content_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        row["asset_id"],
                        row["record_id"],
                        row["asset_type"],
                        row["asset_title"],
                        _nullable_text(row.get("asset_url")),
                        _nullable_text(row.get("canonical_url")),
                        row["asset_index_eligibility"],
                        row["asset_search_eligibility"],
                        row["eligibility_reason"],
                        row["reviewed_by"],
                        row["reviewed_at"],
                        int(bool(row["can_external_reference"])),
                    )
                    for row in assets
                ],
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ResolutionStorageSchemaError(f"foreign key or schema constraint failed: {exc}") from exc

        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if foreign_key_errors:
            raise ResolutionStorageSchemaError("foreign key validation failed")
        same_record_collision = _verify_same_record_alias_collision(connection, aliases)
        multi_record_match_count = _verify_multi_record_alias_policy(connection, parents, aliases)
        counts = {
            "source_record_count": connection.execute(
                "SELECT COUNT(*) FROM source_records"
            ).fetchone()[0],
            "alias_count": connection.execute(
                "SELECT COUNT(*) FROM source_record_aliases"
            ).fetchone()[0],
            "asset_count": connection.execute(
                "SELECT COUNT(*) FROM content_assets"
            ).fetchone()[0],
        }
    finally:
        connection.close()

    read_only_reopen = _verify_read_only_reopen(db_path, counts)
    return {
        **counts,
        "foreign_key_errors": foreign_key_errors,
        "same_record_alias_collision_blocked": same_record_collision,
        "multi_record_alias_match_count": multi_record_match_count,
        "read_only_reopen": read_only_reopen,
        "rollback_verified": rollback_verified,
    }


def generate_resolution_storage_schema_preview(
    *,
    resolution_dir: Path,
    parent_records_path: Path,
    review_decisions_path: Path,
    asset_apply_preview_path: Path,
    asset_blocked_preview_path: Path,
    vault_path: Path,
    db_path: Path,
    output_dir: Path,
) -> dict:
    paths = [
        Path(resolution_dir) / "parent_decision_preview.csv",
        Path(resolution_dir) / "asset_eligibility_preview.csv",
        Path(resolution_dir) / "search_alias_preview.csv",
        Path(parent_records_path),
        Path(review_decisions_path),
        Path(asset_apply_preview_path),
        Path(asset_blocked_preview_path),
        Path(vault_path),
        Path(db_path),
    ]
    for path in paths:
        if not path.exists():
            raise ResolutionStorageSchemaError(f"required input does not exist: {path}")
    _assert_preview_output(Path(output_dir), paths)
    protected_before = {str(path): _hash_path(path) for path in paths[3:]}

    parent_decisions = _read_csv(paths[0])
    asset_decisions = _read_csv(paths[1])
    alias_decisions = _read_csv(paths[2])
    parent_records = _read_json_list(Path(parent_records_path))
    apply_rows = _read_csv(Path(asset_apply_preview_path))
    blocked_rows = _read_csv(Path(asset_blocked_preview_path))

    _validate_authoritative_resolution_inputs(parent_decisions, asset_decisions, alias_decisions)
    parent_by_id = {
        _record_id(row): row
        for row in parent_records
        if _record_id(row) in PARENT_SYNC_RECORD_IDS | {EXCLUDED_PARENT_RECORD_ID}
    }
    expected_ids = PARENT_SYNC_RECORD_IDS | {EXCLUDED_PARENT_RECORD_ID}
    if set(parent_by_id) != expected_ids:
        raise ResolutionStorageSchemaError("the five resolution parent records are not conserved")

    parent_round_trip, asset_round_trip, sqlite_result = _run_temporary_validation(
        parent_decisions,
        asset_decisions,
        alias_decisions,
        parent_by_id,
    )
    counts = _reconcile_global_counts(apply_rows, blocked_rows, asset_decisions)
    parent_sync_rows = _parent_sync_rows(parent_decisions, parent_by_id)
    excluded_rows = _excluded_parent_rows(parent_decisions, parent_by_id)
    if len(parent_sync_rows) != 4 or len(excluded_rows) != 1:
        raise ResolutionStorageSchemaError("parent sync/exclusion conservation failed")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_known_outputs(output_dir)
    _write_csv(output_dir / OUTPUT_FILENAMES[6], parent_round_trip)
    _write_csv(output_dir / OUTPUT_FILENAMES[7], asset_round_trip)
    _write_csv(output_dir / OUTPUT_FILENAMES[8], parent_sync_rows)
    _write_csv(output_dir / OUTPUT_FILENAMES[9], excluded_rows)
    (output_dir / OUTPUT_FILENAMES[0]).write_text(
        _summary_markdown(counts, sqlite_result), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[1]).write_text(
        _authoritative_storage_markdown(), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[2]).write_text(
        _parent_schema_markdown(), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[3]).write_text(
        _asset_schema_markdown(), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[4]).write_text(SQLITE_SCHEMA, encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[5]).write_text(
        _sqlite_decision_markdown(), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[10]).write_text(
        _temporary_validation_markdown(sqlite_result, parent_round_trip, asset_round_trip),
        encoding="utf-8",
    )
    (output_dir / OUTPUT_FILENAMES[11]).write_text(
        _backward_compatibility_markdown(), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[12]).write_text(
        _rollback_markdown(), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[13]).write_text(
        _prerequisites_markdown(), encoding="utf-8"
    )

    protected_after = {str(path): _hash_path(path) for path in paths[3:]}
    if protected_before != protected_after:
        raise ResolutionStorageSchemaError("a protected formal/input path changed during preview")
    generated = sorted(
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and not path.name.startswith("._")
    )
    if generated != sorted(OUTPUT_FILENAMES):
        raise ResolutionStorageSchemaError("preview output set does not match the schema contract")

    return {
        "managed_parent_schema_ready": True,
        "managed_asset_schema_ready": True,
        "parent_round_trip_count": len(parent_round_trip),
        "asset_round_trip_count": len(asset_round_trip),
        "temporary_sqlite_created": True,
        "temporary_sqlite_foreign_key_errors": sqlite_result["foreign_key_errors"],
        "temporary_sqlite_read_only_reopen": sqlite_result["read_only_reopen"],
        "temporary_sqlite_rollback_verified": sqlite_result["rollback_verified"],
        "alias_storage": "source_record_aliases",
        "authoritative_parent_decision_store": "not_ready_append_only_store_required",
        "parent_sync_count": len(parent_sync_rows),
        "excluded_parent_sync_count": len(excluded_rows),
        **counts,
        "plan_id_generated": False,
        "formal_data_modified": False,
        "output_dir": str(output_dir),
    }


def _run_temporary_validation(parent_decisions, asset_decisions, alias_decisions, parent_by_id):
    aliases_by_record = {}
    for row in alias_decisions:
        aliases_by_record.setdefault(row["record_id"], []).append(row["alias"])
    reviewed_at_by_record = {row["record_id"]: row["reviewed_at"] for row in parent_decisions}
    parent_round_trip = []
    asset_round_trip = []
    sqlite_parents = []
    sqlite_aliases = []
    sqlite_assets = []
    with tempfile.TemporaryDirectory(prefix="mka-resolution-schema-") as temp_name:
        root = Path(temp_name)
        parent_dir = root / "managed_parents"
        asset_dir = root / "managed_assets"
        parent_dir.mkdir()
        asset_dir.mkdir()
        for row in parent_decisions:
            record_id = row["record_id"]
            aliases = tuple(aliases_by_record.get(record_id, []))
            parent_metadata = ManagedParentAliasMetadata(
                record_id=record_id,
                search_aliases=aliases,
                search_alias_reviewed_by="Admin",
                search_alias_reviewed_at=row["reviewed_at"],
                search_alias_provenance=(
                    "Admin-approved source-record exact aliases"
                    if aliases
                    else "Admin-reviewed; no source-record aliases approved"
                ),
            )
            text = render_managed_parent(parent_metadata)
            path = parent_dir / f"parent-{_source_row(record_id)}.md"
            path.write_text(text, encoding="utf-8")
            parsed = validate_managed_parent_round_trip(path.read_text(encoding="utf-8"))
            parent_round_trip.append(
                {
                    "record_id": record_id,
                    "temporary_path": path.name,
                    "alias_count": len(parsed.search_aliases),
                    "aliases_preserved": "true",
                    "audit_fields_preserved": "true",
                    "deterministic": "true",
                    "validation_status": "passed",
                }
            )
            parent = parent_by_id[record_id]
            sqlite_parents.append(
                {
                    "record_id": record_id,
                    "brand_name": row["brand_name"],
                    "merchant_handle": row.get("merchant_handle", ""),
                    "entity_type": row["entity_type"],
                    "governance_eligibility": (
                        "excluded" if record_id == EXCLUDED_PARENT_RECORD_ID else "included"
                    ),
                }
            )
        for row in alias_decisions:
            sqlite_aliases.append(
                {
                    "record_id": row["record_id"],
                    "alias": row["alias"],
                    "normalized_alias": _normalize_alias(row["alias"]),
                    "reviewed_by": "Admin",
                    "reviewed_at": row["reviewed_at"],
                    "provenance": "Admin-approved source-record exact alias",
                }
            )
        parent_external = {
            row["record_id"]: _as_bool(row["proposed_can_external_reference"])
            for row in parent_decisions
        }
        for row in asset_decisions:
            metadata = AssetEligibilityMetadata(
                asset_id=row["asset_id"],
                record_id=row["record_id"],
                asset_index_eligibility=row["proposed_asset_index_eligibility"],
                asset_search_eligibility=row["proposed_asset_search_eligibility"],
                eligibility_reason=row["eligibility_reason"],
                reviewed_by="Admin",
                reviewed_at=row["reviewed_at"],
            )
            text = render_managed_asset(metadata)
            path = asset_dir / f"asset-{hashlib.sha256(metadata.asset_id.encode()).hexdigest()[:16]}.md"
            path.write_text(text, encoding="utf-8")
            parsed = validate_managed_asset_round_trip(path.read_text(encoding="utf-8"))
            asset_round_trip.append(
                {
                    "record_id": parsed.record_id,
                    "asset_id": parsed.asset_id,
                    "asset_index_eligibility": parsed.asset_index_eligibility,
                    "asset_search_eligibility": parsed.asset_search_eligibility,
                    "eligibility_fields_preserved": "true",
                    "audit_fields_preserved": "true",
                    "deterministic": "true",
                    "validation_status": "passed",
                }
            )
            sqlite_assets.append(
                {
                    "asset_id": row["asset_id"],
                    "record_id": row["record_id"],
                    "asset_type": row["asset_type"],
                    "asset_title": row["asset_title"],
                    "asset_url": row.get("asset_url", ""),
                    "canonical_url": row.get("canonical_url", ""),
                    "asset_index_eligibility": row["proposed_asset_index_eligibility"],
                    "asset_search_eligibility": row["proposed_asset_search_eligibility"],
                    "eligibility_reason": row["eligibility_reason"],
                    "reviewed_by": "Admin",
                    "reviewed_at": row["reviewed_at"],
                    "can_external_reference": parent_external[row["record_id"]],
                }
            )
        sqlite_result = create_temporary_sqlite_migration(
            root / "candidate.sqlite",
            parents=sqlite_parents,
            aliases=sqlite_aliases,
            assets=sqlite_assets,
        )
    return parent_round_trip, asset_round_trip, sqlite_result


def _validate_authoritative_resolution_inputs(parents, assets, aliases):
    if {row["record_id"] for row in parents} != PARENT_SYNC_RECORD_IDS | {EXCLUDED_PARENT_RECORD_ID}:
        raise ResolutionStorageSchemaError("resolution parent input must contain the exact five records")
    if len(parents) != 5:
        raise ResolutionStorageSchemaError("duplicate resolution parent decision")
    expected_decisions = {
        "商家夥伴案例資料庫:r7": "approve",
        "商家夥伴案例資料庫:r12": "approve_internal_only",
        "商家夥伴案例資料庫:r30": "exclude",
        "商家夥伴案例資料庫:r32": "approve",
        "商家夥伴案例資料庫:r122": "approve",
    }
    for row in parents:
        if row["reviewer"] != "Admin" or row["proposed_review_decision"] != expected_decisions[row["record_id"]]:
            raise ResolutionStorageSchemaError("parent decisions must match Admin authority")
        _validate_timestamp(row["reviewed_at"], "reviewed_at")
    if len(assets) != 10 or len({row["asset_id"] for row in assets}) != 10:
        raise ResolutionStorageSchemaError("asset eligibility identities are not conserved")
    for row in assets:
        AssetEligibilityMetadata(
            asset_id=row["asset_id"],
            record_id=row["record_id"],
            asset_index_eligibility=row["proposed_asset_index_eligibility"],
            asset_search_eligibility=row["proposed_asset_search_eligibility"],
            eligibility_reason=row["eligibility_reason"],
            reviewed_by=row["reviewer"],
            reviewed_at=row["reviewed_at"],
        )
    expected_aliases = {("商家夥伴案例資料庫:r32", "slp"), ("商家夥伴案例資料庫:r32", "shopline payments")}
    actual_aliases = {(row["record_id"], _normalize_alias(row["alias"])) for row in aliases}
    if actual_aliases != expected_aliases or any(row["reviewer"] != "Admin" for row in aliases):
        raise ResolutionStorageSchemaError("source-record alias decisions do not match Admin authority")


def _reconcile_global_counts(apply_rows, blocked_rows, asset_decisions):
    apply_by_asset = {}
    for row in apply_rows:
        if row.get("review_decision") != "approve":
            continue
        apply_by_asset.setdefault(row["asset_id"], set()).add(row["field"])
    if any(fields != {"asset_url", "canonical_url"} for fields in apply_by_asset.values()):
        raise ResolutionStorageSchemaError("eligible asset URL field pair is incomplete")
    if len(apply_by_asset) != 206:
        raise ResolutionStorageSchemaError("expected 206 pre-resolution eligible assets")
    excluded_asset_id = f"{EXCLUDED_PARENT_RECORD_ID}:article"
    if excluded_asset_id not in apply_by_asset:
        raise ResolutionStorageSchemaError("taken-down asset is missing from original eligible preview")
    final_eligible_ids = set(apply_by_asset) - {excluded_asset_id}
    held = {
        row["asset_id"]
        for row in asset_decisions
        if row["proposed_asset_index_eligibility"] == "hold"
    }
    blocked_ids = {row["asset_id"] for row in blocked_rows}
    final_excluded_ids = (blocked_ids - held) | {excluded_asset_id}
    if len(final_eligible_ids) != 205 or len(held) != 1 or len(final_excluded_ids) != 16:
        raise ResolutionStorageSchemaError("205/1/16 asset conservation failed")
    if final_eligible_ids & held or final_eligible_ids & final_excluded_ids or held & final_excluded_ids:
        raise ResolutionStorageSchemaError("asset eligibility buckets overlap")
    if len(final_eligible_ids | held | final_excluded_ids) != 222:
        raise ResolutionStorageSchemaError("222 asset identities are not conserved")
    return {
        "eligible_asset_count": 205,
        "hold_asset_count": 1,
        "excluded_asset_count": 16,
        "approved_url_field_count": 410,
        "asset_identity_count": 222,
        "new_asset_identity_count": 0,
        "lost_asset_identity_count": 0,
    }


def _parent_sync_rows(parent_decisions, parent_by_id):
    rows = []
    for row in sorted(parent_decisions, key=lambda item: _source_row(item["record_id"])):
        if row["record_id"] not in PARENT_SYNC_RECORD_IDS:
            continue
        parent = parent_by_id[row["record_id"]]
        rows.append(
            {
                "record_id": row["record_id"],
                "brand_name": row["brand_name"],
                "source_sheet": parent["source_sheet"],
                "source_row": parent["source_row"],
                "entity_type": row["entity_type"],
                "merchant_handle": row.get("merchant_handle", ""),
                "proposed_review_decision": row["proposed_review_decision"],
                "deterministic_target": f"MKA/managed/parents/merchant-case-r{parent['source_row']}.md",
                "temporary_fixture_only": "true",
                "sync_action": "future_sync_after_separate_confirmation",
                "reason": row["reason"],
            }
        )
    return rows


def _excluded_parent_rows(parent_decisions, parent_by_id):
    row = next(item for item in parent_decisions if item["record_id"] == EXCLUDED_PARENT_RECORD_ID)
    parent = parent_by_id[EXCLUDED_PARENT_RECORD_ID]
    return [
        {
            "record_id": EXCLUDED_PARENT_RECORD_ID,
            "brand_name": row["brand_name"],
            "source_sheet": parent["source_sheet"],
            "source_row": parent["source_row"],
            "proposed_review_decision": "exclude",
            "sync_action": "excluded_from_sync",
            "asset_action": "exclude_child_assets",
            "reason": row["reason"],
        }
    ]


def _summary_markdown(counts, sqlite_result):
    return f"""# Resolution Storage Schema Preview Summary

## 結論

Parent Alias 與 Asset Eligibility schema 已在 temporary fixtures 中完成 deterministic round-trip。SQLite migration 只在暫存資料庫驗證，沒有建立可 confirm／execute 的 PLAN_ID，也沒有修改正式資料。

## Schema Support

- Managed Parent Alias schema: ready for a future separately confirmed migration
- Managed Asset Eligibility schema: ready for a future separately confirmed migration
- Parent round-trip fixtures: 5
- Asset round-trip fixtures: 10
- Temporary SQLite FK errors: {sqlite_result['foreign_key_errors']}
- Read-only reopen: {str(sqlite_result['read_only_reopen']).lower()}
- Rollback verified: {str(sqlite_result['rollback_verified']).lower()}

## Conservation

- Eligible assets: {counts['eligible_asset_count']}
- Hold assets: {counts['hold_asset_count']}
- Excluded / governance-blocked assets: {counts['excluded_asset_count']}
- Approved URL fields: {counts['approved_url_field_count']}
- Asset identities: {counts['asset_identity_count']}
- New / lost identities: {counts['new_asset_identity_count']} / {counts['lost_asset_identity_count']}
- Parent sync candidates: 4
- Excluded parent sync records: 1

## Safety

- Formal Vault modified: false
- Formal SQLite modified: false
- Review decisions modified: false
- Executable PLAN_ID generated: false
- `resolution-plan-a878e6d1036bac96`: **DO NOT CONFIRM**
- `asset-plan-07cd12338615c961`: **DO NOT CONFIRM**
"""


def _authoritative_storage_markdown():
    return """# Authoritative Storage Assessment

## 判定

`reports/excel_preview/review_decisions_template.csv` is **not sufficient** as the formal Source of Truth. It is a derived, Git-ignored operational review artifact that can be regenerated and has no append-only history, immutable event identity, or durable audit-chain guarantee.

## 建議的正式 Decision Store

在未來獨立 migration 中建立 `.mka/governance_decisions.sqlite`，採 append-only `decision_events`：event_id、subject_type、subject_id、decision_type、decision_value、reviewer、reviewed_at、reason、source_checksum、previous_event_hash、event_hash。禁止原地 UPDATE/DELETE；更正以新事件 supersede 舊事件，並納入備份與 rollback manifest。

Managed Vault frontmatter 與 content index 只是經核准 decision events 的 materialized projections，不是權威審核來源。CSV 可作匯入 transport，但每次匯入必須驗證 checksum、完整性與 reviewer authority。

本 Sprint 沒有建立該正式 store，只完成 schema assessment 與 temporary validation。
"""


def _parent_schema_markdown():
    return """# Managed Parent Schema Preview

## Frontmatter Fields

```yaml
search_aliases:
  - "SLP"
  - "SHOPLINE Payments"
search_alias_reviewed_by: "Admin"
search_alias_reviewed_at: "ISO-8601 timestamp with timezone"
search_alias_provenance: "Admin-approved source-record exact aliases"
```

- Alias is source-record level and is never copied to child assets.
- Matching is Unicode-normalized, case-insensitive exact matching; fuzzy matching is forbidden.
- Non-empty aliases require all three audit fields.
- Duplicate normalized aliases on the same record fail closed.
- Unknown fields or malformed timestamps fail closed in the migration validator.
"""


def _asset_schema_markdown():
    return """# Managed Asset Schema Preview

## Frontmatter Fields

```yaml
asset_index_eligibility: "include | hold | exclude"
asset_search_eligibility: "searchable | searchable_internal | not_searchable | excluded"
eligibility_reason: "Admin-reviewed reason"
reviewed_by: "Admin"
reviewed_at: "ISO-8601 timestamp with timezone"
```

## Fail-closed Pairing

- include -> searchable or searchable_internal
- hold -> not_searchable
- exclude -> excluded

Any other enum or pairing is rejected. Parent approval never overrides a child hold/exclude. These fields are governance eligibility, not publication_status or review_status query fields.
"""


def _sqlite_decision_markdown():
    return """# SQLite Schema Design Decision

## Chosen Design

Use normalized `source_record_aliases` and `content_assets` tables linked to `source_records` by foreign keys.

`source_record_aliases.normalized_alias` has a non-unique lookup index. This intentionally permits one exact alias to resolve to multiple legitimate source records. `(record_id, normalized_alias)` is the primary key, so the same alias cannot silently overwrite or duplicate itself on one record.

## Rejected Alternative

Storing aliases only inside `documents.metadata_json` makes collision inspection, foreign-key validation and exact multi-record lookup harder to audit. JSON may remain a read projection for compatibility, but it is not the authoritative alias store.

`content_tags` remain parent source-record metadata and are not copied to `content_assets`.
"""


def _temporary_validation_markdown(sqlite_result, parent_rows, asset_rows):
    return f"""# Temporary Migration Validation

- Temporary managed parent directory: created and destroyed inside `TemporaryDirectory`
- Temporary managed asset directory: created and destroyed inside `TemporaryDirectory`
- Temporary SQLite database: created and destroyed inside `TemporaryDirectory`
- Parent deterministic round-trips: {len(parent_rows)} passed
- Asset deterministic round-trips: {len(asset_rows)} passed
- SQLite source records: {sqlite_result['source_record_count']}
- SQLite aliases: {sqlite_result['alias_count']}
- SQLite content assets: {sqlite_result['asset_count']}
- Foreign-key errors: {sqlite_result['foreign_key_errors']}
- Same-record alias collision blocked: {str(sqlite_result['same_record_alias_collision_blocked']).lower()}
- Exact alias allowed across legitimate records in isolated savepoint: {sqlite_result['multi_record_alias_match_count']} matches
- Read-only reopen: {str(sqlite_result['read_only_reopen']).lower()}
- Transaction rollback: {str(sqlite_result['rollback_verified']).lower()}
- Formal targets changed: false
"""


def _backward_compatibility_markdown():
    return """# Schema Backward Compatibility

- Existing parent Markdown without alias fields remains readable; it means no approved aliases, not an inferred alias.
- Existing asset Markdown remains readable but is not eligible for asset-level search until an approved eligibility projection exists.
- Existing `documents` and `chunks` tables are unchanged by this preview.
- Future migration should add normalized tables alongside existing index tables, backfill only approved rows, validate joins, then switch readers behind an explicit version gate.
- Rollback keeps old readers on the current schema and removes the candidate DB/temporary files; no reverse inference is needed.
"""


def _rollback_markdown():
    return """# Schema Rollback Plan

1. Before any future apply, checksum the formal Vault namespace, formal SQLite DB and authoritative decision event store.
2. Build all parent/asset projections and SQLite tables in temporary sibling paths.
3. Reparse every Markdown file; reopen SQLite read-only; validate FK, eligibility pairing, alias collision and 205/1/16/410 conservation.
4. Stop readers and atomically swap only after an independent human confirmation.
5. On any failure, restore the pre-apply Vault and DB snapshots, verify their checksums, and retain the failed audit manifest.
6. Never delete or overwrite decision events; rollback only materialized projections.

This Sprint exercised transaction rollback in a temporary SQLite database only.
"""


def _prerequisites_markdown():
    return """# Next Resolution Apply Prerequisites

- [ ] Approve and implement the append-only authoritative Decision Store.
- [ ] Approve the managed parent and managed asset frontmatter migration contract.
- [ ] Implement parser/serializer integration behind a schema version without changing current ingestion semantics.
- [ ] Implement normalized `source_records`, `source_record_aliases` and `content_assets` migration in a candidate DB.
- [ ] Revalidate four parent sync candidates and one excluded parent against fresh checksums.
- [ ] Recompute 205 eligible / 1 hold / 16 excluded / 410 URL fields from authoritative inputs.
- [ ] Create a new, separately reviewed plan only after the storage contracts are implemented.
- [ ] Keep `resolution-plan-a878e6d1036bac96` and `asset-plan-07cd12338615c961` DO NOT CONFIRM; this preview generated no PLAN_ID.
"""


def _verify_transaction_rollback(connection):
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO source_records VALUES (?, ?, ?, ?, ?)",
        ("__rollback_test__", "rollback", None, "other", "excluded"),
    )
    connection.rollback()
    return connection.execute(
        "SELECT COUNT(*) FROM source_records WHERE record_id = '__rollback_test__'"
    ).fetchone()[0] == 0


def _verify_same_record_alias_collision(connection, aliases):
    if not aliases:
        return True
    row = aliases[0]
    connection.execute("SAVEPOINT alias_same_record")
    try:
        connection.execute(
            "INSERT INTO source_record_aliases VALUES (?, ?, ?, ?, ?, ?)",
            (
                row["record_id"], row["alias"], row["normalized_alias"],
                row["reviewed_by"], row["reviewed_at"], row["provenance"],
            ),
        )
    except sqlite3.IntegrityError:
        connection.execute("ROLLBACK TO alias_same_record")
        connection.execute("RELEASE alias_same_record")
        return True
    connection.execute("ROLLBACK TO alias_same_record")
    connection.execute("RELEASE alias_same_record")
    return False


def _verify_multi_record_alias_policy(connection, parents, aliases):
    if not aliases or len(parents) < 2:
        return 1 if aliases else 0
    alias = aliases[0]
    second_record = next(row["record_id"] for row in parents if row["record_id"] != alias["record_id"])
    connection.execute("SAVEPOINT alias_multi_record")
    connection.execute(
        "INSERT INTO source_record_aliases VALUES (?, ?, ?, ?, ?, ?)",
        (
            second_record, alias["alias"], alias["normalized_alias"],
            alias["reviewed_by"], alias["reviewed_at"], alias["provenance"],
        ),
    )
    count = connection.execute(
        "SELECT COUNT(*) FROM source_record_aliases WHERE normalized_alias = ?",
        (alias["normalized_alias"],),
    ).fetchone()[0]
    connection.execute("ROLLBACK TO alias_multi_record")
    connection.execute("RELEASE alias_multi_record")
    return count


def _verify_read_only_reopen(path, expected_counts):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        actual = {
            "source_record_count": connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0],
            "alias_count": connection.execute("SELECT COUNT(*) FROM source_record_aliases").fetchone()[0],
            "asset_count": connection.execute("SELECT COUNT(*) FROM content_assets").fetchone()[0],
        }
        try:
            connection.execute("INSERT INTO source_records VALUES ('x','x',NULL,'other','x')")
        except sqlite3.OperationalError:
            write_blocked = True
        else:
            write_blocked = False
        return actual == expected_counts and write_blocked
    finally:
        connection.close()


def _required_metadata_text(metadata, field):
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ResolutionStorageSchemaError(f"{field} is required")
    return value


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ResolutionStorageSchemaError(f"{field} is required")


def _validate_timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ResolutionStorageSchemaError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResolutionStorageSchemaError(f"{field} must include a timezone")


def _validate_round_trip_scalar(value):
    if any(character in value for character in ('\n', '\r', '"', "'", "\\")):
        raise ResolutionStorageSchemaError("frontmatter scalar is outside the deterministic subset")


def _normalize_alias(value):
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _nullable_text(value):
    text = "" if value is None else str(value).strip()
    return text or None


def _record_id(row):
    return f"{row.get('source_sheet')}:{'r'}{row.get('source_row')}"


def _source_row(record_id):
    match = re.search(r":r(\d+)$", record_id)
    if not match:
        raise ResolutionStorageSchemaError(f"invalid record_id: {record_id}")
    return int(match.group(1))


def _as_bool(value):
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ResolutionStorageSchemaError(f"CSV has no header: {path}")
        return list(reader)


def _read_json_list(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResolutionStorageSchemaError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ResolutionStorageSchemaError(f"JSON must be an object array: {path}")
    return payload


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["record_id"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _clear_known_outputs(output_dir):
    for name in OUTPUT_FILENAMES:
        for path in (output_dir / name, output_dir / f"._{name}"):
            if path.exists():
                path.unlink()


def _assert_preview_output(output_dir, protected_paths):
    output = output_dir.resolve()
    lowered = {part.casefold() for part in output.parts}
    if ".mka" in lowered or "obsidian_vault" in lowered:
        raise ResolutionStorageSchemaError("output cannot be inside formal Vault or .mka")
    for path in protected_paths:
        protected = path.resolve()
        if output == protected or output in protected.parents or protected in output.parents:
            raise ResolutionStorageSchemaError("output must be separate from protected inputs")


def _hash_path(path):
    path = Path(path)
    if not path.exists():
        return "missing"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if not child.is_file() or child.name.startswith("._"):
            continue
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()
