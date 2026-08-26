from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.content_index import create_content_index_plan
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import DocumentMetadata
from marketing_knowledge_agent.stable_record_authority import (
    ACTIVATION_STATUS_NOT_ACTIVATED,
    ALIAS_BINDING_UNCHANGED,
    ASSET_REVIEW_NOT_IN_SCOPE,
    AUTHORITY_COLUMNS,
    AUTHORITY_RECORD_STATUS_CONTINUATION,
    AUTHORITY_RECORD_STATUS_NEW,
    AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
    LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY,
    LEGACY_SOURCE_SCHEME_ROW_V1,
    MANIFEST_FILENAME,
    PACKAGE_MATERIALIZED_FIELD,
    PACKAGE_STATE_FIELD,
    PAYLOAD_CHANGE_NONE_RECORDED,
    PRODUCTION_REINDEX_AUTHORIZED_FIELD,
    RECEIPT_FILENAME,
    RECORD_IDENTITY_SCHEME,
    REGISTRY_FILENAME,
    ROW_V1_RETIRED_FIELD,
    ROW_V1_STATUS_RETAINED,
    STABLE_RECORD_V2_ACTIVATED_FIELD,
    AuthorityEvidencePins,
    StableRecordAuthority,
    compute_content_digest,
    compute_manifest_hash,
    compute_receipt_hash,
    qualify_legacy_record_id,
    write_authority_package,
)
from marketing_knowledge_agent.stable_record_shadow import (
    ShadowResolutionStatus,
    StableRecordShadowError,
    load_stable_record_shadow,
)


WORKBOOK_SHA = "a" * 64
OTHER_WORKBOOK_SHA = "b" * 64
SHEET = "商家夥伴案例資料庫"


@pytest.fixture
def authority_package(tmp_path: Path) -> Path:
    rows = (
        _authority_row("MKA-MC-00001", legacy_row="10"),
        _authority_row("MKA-MC-00121", legacy_row=""),
    )
    pins = AuthorityEvidencePins(
        proposal_registry_sha256="1" * 64,
        proposal_crosswalk_sha256="2" * 64,
        proposal_content_digest="3" * 64,
        proposal_manifest_hash="4" * 64,
        decision_artifact_sha256="5" * 64,
        reviewer="Synthetic Reviewer",
        reviewed_at="2026-08-24",
    )
    authority = StableRecordAuthority(
        rows=rows,
        pins=pins,
        record_count=2,
        identity_continuation_count=1,
        new_identity_count=1,
        confidence_counts={"HIGH": 1, "NEW": 1},
        asset_review_required_ids=(),
        alias_decision_required_ids=(),
        payload_change_ids=(),
        stable_id_set_digest=_stable_id_set_digest(row["stable_record_id"] for row in rows),
    )
    output = tmp_path / "authority"
    write_authority_package(authority, output, created_at="2026-08-24T00:00:00+00:00")
    return output


def test_valid_materialized_not_activated_authority_loads_and_resolves(authority_package: Path):
    resolver = _load(authority_package)

    resolution = resolver.resolve(
        record_type="merchant_case", source_sheet=SHEET, source_row=10
    )

    assert resolution.status is ShadowResolutionStatus.RESOLVED
    assert resolution.stable_record_id == "MKA-MC-00001"
    assert resolver.resolve_stable_record_id("MKA-MC-00121").status is (
        ShadowResolutionStatus.AUTHORITY_ONLY_NO_LEGACY_BINDING
    )


def test_external_manifest_pin_is_required_and_wrong_pin_fails(authority_package: Path):
    with pytest.raises(StableRecordShadowError, match="expected manifest hash"):
        load_stable_record_shadow(
            authority_dir=authority_package,
            expected_manifest_hash="",
            row_v1_workbook_sha256=WORKBOOK_SHA,
        )

    with pytest.raises(StableRecordShadowError, match="external manifest hash"):
        load_stable_record_shadow(
            authority_dir=authority_package,
            expected_manifest_hash="f" * 64,
            row_v1_workbook_sha256=WORKBOOK_SHA,
        )


def test_activated_or_row_v1_retired_package_fails_closed(authority_package: Path):
    manifest = _manifest(authority_package)
    manifest["activation_status"] = "activated"
    manifest[STABLE_RECORD_V2_ACTIVATED_FIELD] = True
    _reseal_package(authority_package, manifest=manifest)
    with pytest.raises(StableRecordShadowError, match="not_activated"):
        _load(authority_package)

    manifest["activation_status"] = ACTIVATION_STATUS_NOT_ACTIVATED
    manifest[STABLE_RECORD_V2_ACTIVATED_FIELD] = False
    manifest[ROW_V1_RETIRED_FIELD] = True
    rows = _rows(authority_package)
    rows[0]["row_v1_status"] = "retired"
    _reseal_package(authority_package, manifest=manifest, rows=rows)
    with pytest.raises(StableRecordShadowError, match="row_v1_retired|retained_not_retired"):
        _load(authority_package)


@pytest.mark.parametrize("mutation", ["duplicate_stable_id", "duplicate_legacy_id", "invalid_stable_id"])
def test_hostile_authority_rows_fail_closed(authority_package: Path, mutation: str):
    rows = _rows(authority_package)
    if mutation == "duplicate_stable_id":
        rows[1]["stable_record_id"] = rows[0]["stable_record_id"]
    elif mutation == "duplicate_legacy_id":
        rows[1].update(_legacy_fields("10"))
        rows[1]["authority_record_status"] = AUTHORITY_RECORD_STATUS_CONTINUATION
        rows[1]["identity_origin"] = "legacy_row_v1_continuation"
    else:
        rows[0]["stable_record_id"] = "MKA-MC-1"
    _reseal_package(authority_package, rows=rows)

    with pytest.raises(StableRecordShadowError):
        _load(authority_package)


def test_manifest_row_classification_counts_must_match_bindings(authority_package: Path):
    manifest = _manifest(authority_package)
    manifest["identity_continuation_count"] = 2
    manifest["new_identity_count"] = 0
    _reseal_package(authority_package, manifest=manifest, recompute_row_counts=False)

    with pytest.raises(StableRecordShadowError, match="continuation_count"):
        _load(authority_package)


def test_wrong_workbook_lineage_never_falls_back_to_bare_sheet_row(authority_package: Path):
    resolver = load_stable_record_shadow(
        authority_dir=authority_package,
        expected_manifest_hash=_manifest(authority_package)["manifest_hash"],
        row_v1_workbook_sha256=OTHER_WORKBOOK_SHA,
    )

    resolution = resolver.resolve(
        record_type="merchant_case", source_sheet=SHEET, source_row=10
    )

    assert resolution.status is ShadowResolutionStatus.UNRESOLVED
    assert resolution.stable_record_id is None


def test_missing_workbook_lineage_fails_closed(authority_package: Path):
    with pytest.raises(StableRecordShadowError, match="workbook|lineage"):
        load_stable_record_shadow(
            authority_dir=authority_package,
            expected_manifest_hash=_manifest(authority_package)["manifest_hash"],
            row_v1_workbook_sha256="",
        )

    resolution = _load(authority_package).resolve(
        record_type="merchant_case", source_sheet=None, source_row=None
    )
    assert resolution.status is ShadowResolutionStatus.UNRESOLVED
    assert resolution.stable_record_id is None


def test_non_merchant_record_is_not_applicable(authority_package: Path):
    resolution = _load(authority_package).resolve(
        record_type="public_metric", source_sheet=SHEET, source_row=10
    )
    assert resolution.status is ShadowResolutionStatus.NOT_APPLICABLE
    assert resolution.stable_record_id is None


def test_document_metadata_stable_record_id_is_optional_validated_and_serialized():
    without_id = _metadata()
    assert without_id.stable_record_id is None
    assert without_id.metadata_dict()["stable_record_id"] is None

    with_id = _metadata(stable_record_id="MKA-MC-00001")
    assert with_id.metadata_dict()["stable_record_id"] == "MKA-MC-00001"
    assert DocumentMetadata(**with_id.metadata_dict()).stable_record_id == "MKA-MC-00001"

    with pytest.raises(ValidationError, match="stable_record_id"):
        _metadata(stable_record_id="MKA-MC-1")


def test_content_index_shadow_is_opt_in_and_preserves_document_and_chunk_ids(
    authority_package: Path, tmp_path: Path
):
    vault = _vault(tmp_path)
    merchant = vault / "MKA" / "merchant_cases" / "merchant.md"
    metric = vault / "MKA" / "public_metrics" / "metric.md"
    _write_synced(merchant, record_type="merchant_case", source_row=10)
    _write_synced(
        metric,
        record_type="public_metric",
        source_row=10,
        allowed_exposure_channels=["saleskits"],
    )
    vault_before = _tree_hashes(vault)

    default_plan = create_content_index_plan(vault)
    shadow_plan = create_content_index_plan(vault, stable_record_shadow=_load(authority_package))

    default_by_path = {item.path: item.document for item in default_plan.included}
    shadow_by_path = {item.path: item.document for item in shadow_plan.included}
    assert default_by_path.keys() == shadow_by_path.keys()
    assert default_by_path["merchant_cases/merchant.md"].metadata.stable_record_id is None
    assert shadow_by_path["merchant_cases/merchant.md"].metadata.stable_record_id == "MKA-MC-00001"
    assert shadow_by_path["public_metrics/metric.md"].metadata.stable_record_id is None
    assert {path: document.id for path, document in default_by_path.items()} == {
        path: document.id for path, document in shadow_by_path.items()
    }
    assert [chunk.id for chunk in chunk_documents(default_by_path.values())] == [
        chunk.id for chunk in chunk_documents(shadow_by_path.values())
    ]
    assert shadow_plan.stable_record_shadow_summary == {
        "authority_manifest_hash": _manifest(authority_package)["manifest_hash"],
        "authority_status": AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
        "shadow_mode": True,
        "merchant_records_seen": 1,
        "resolved": 1,
        "unresolved": 0,
        "authority_only": 1,
        "stable_record_v2_activated": False,
        "row_v1_retired": False,
    }
    assert _tree_hashes(vault) == vault_before


def test_sqlite_metadata_json_round_trip_preserves_shadow_id(authority_package: Path, tmp_path: Path):
    vault = _vault(tmp_path)
    _write_synced(
        vault / "MKA" / "merchant_cases" / "merchant.md",
        record_type="merchant_case",
        source_row=10,
    )
    document = create_content_index_plan(
        vault, stable_record_shadow=_load(authority_package)
    ).included[0].document
    chunks = chunk_documents([document])
    db_path = tmp_path / "temporary-content-index.sqlite"

    SQLiteIndex(db_path).rebuild([document], chunks)
    loaded = SQLiteIndex(db_path).load_chunks()

    assert loaded[0].chunk.metadata.stable_record_id == "MKA-MC-00001"
    assert loaded[0].chunk.document_id == document.id
    assert loaded[0].chunk.id == chunks[0].id


def test_shadow_loading_and_resolution_do_not_modify_authority(authority_package: Path):
    before = _tree_hashes(authority_package)
    resolver = _load(authority_package)
    resolver.resolve(record_type="merchant_case", source_sheet=SHEET, source_row=10)
    resolver.resolve_stable_record_id("MKA-MC-00121")
    assert _tree_hashes(authority_package) == before


def _load(authority_package: Path):
    return load_stable_record_shadow(
        authority_dir=authority_package,
        expected_manifest_hash=_manifest(authority_package)["manifest_hash"],
        row_v1_workbook_sha256=WORKBOOK_SHA,
    )


def _authority_row(stable_record_id: str, *, legacy_row: str) -> dict[str, str]:
    row = {column: "" for column in AUTHORITY_COLUMNS}
    row.update(
        {
            "stable_record_id": stable_record_id,
            "record_identity_scheme": RECORD_IDENTITY_SCHEME,
            "authority_record_status": (
                AUTHORITY_RECORD_STATUS_CONTINUATION
                if legacy_row
                else AUTHORITY_RECORD_STATUS_NEW
            ),
            "authority_status": AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
            "activation_status": ACTIVATION_STATUS_NOT_ACTIVATED,
            "identity_origin": (
                "legacy_row_v1_continuation" if legacy_row else "authority_workbook_new_record"
            ),
            "legacy_source_row_role": LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY,
            "authority_source_sheet": SHEET,
            "authority_source_row": "11" if legacy_row else "121",
            "authority_workbook_sha256": OTHER_WORKBOOK_SHA,
            "record_type": "merchant_case",
            "match_confidence": "HIGH" if legacy_row else "NEW",
            "match_evidence": "brand_match" if legacy_row else "authority_only_record",
            "human_decision": "approve_same_record" if legacy_row else "approve_new_record",
            "identity_scope": "identity_continuity_only" if legacy_row else "new_identity_only",
            "row_v1_status": ROW_V1_STATUS_RETAINED,
            "alias_binding_status": ALIAS_BINDING_UNCHANGED,
            "asset_review_status": ASSET_REVIEW_NOT_IN_SCOPE,
            "payload_change_status": PAYLOAD_CHANGE_NONE_RECORDED,
            "source_proposal_registry_sha256": "1" * 64,
            "source_proposal_crosswalk_sha256": "2" * 64,
            "source_proposal_content_digest": "3" * 64,
            "source_proposal_manifest_hash": "4" * 64,
            "source_decision_artifact_sha256": "5" * 64,
        }
    )
    if legacy_row:
        row.update(_legacy_fields(legacy_row))
    return row


def _legacy_fields(legacy_row: str) -> dict[str, str]:
    return {
        "legacy_source_record_id": qualify_legacy_record_id(
            WORKBOOK_SHA, SHEET, legacy_row
        ),
        "legacy_source_scheme": LEGACY_SOURCE_SCHEME_ROW_V1,
        "legacy_source_sheet": SHEET,
        "legacy_source_row": legacy_row,
        "legacy_workbook_sha256": WORKBOOK_SHA,
    }


def _stable_id_set_digest(stable_ids) -> str:
    return hashlib.sha256(("\n".join(sorted(set(stable_ids))) + "\n").encode()).hexdigest()


def _manifest(authority_package: Path) -> dict:
    return json.loads((authority_package / MANIFEST_FILENAME).read_text(encoding="utf-8"))


def _rows(authority_package: Path) -> list[dict[str, str]]:
    with (authority_package / REGISTRY_FILENAME).open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _reseal_package(
    authority_package: Path,
    *,
    manifest: dict | None = None,
    rows: list[dict[str, str]] | None = None,
    recompute_row_counts: bool = True,
) -> None:
    manifest = dict(manifest or _manifest(authority_package))
    rows = rows or _rows(authority_package)
    with (authority_package / REGISTRY_FILENAME).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUTHORITY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    registry_bytes = (authority_package / REGISTRY_FILENAME).read_bytes()
    manifest["registry_sha256"] = hashlib.sha256(registry_bytes).hexdigest()
    manifest["record_count"] = len(rows)
    if recompute_row_counts:
        manifest["identity_continuation_count"] = sum(
            row["authority_record_status"] == AUTHORITY_RECORD_STATUS_CONTINUATION for row in rows
        )
        manifest["new_identity_count"] = sum(
            row["authority_record_status"] == AUTHORITY_RECORD_STATUS_NEW for row in rows
        )
    manifest["stable_id_set_digest"] = _stable_id_set_digest(
        row["stable_record_id"] for row in rows
    )
    manifest.pop("content_digest", None)
    manifest.pop("manifest_hash", None)
    manifest["content_digest"] = compute_content_digest(manifest)

    receipt = json.loads((authority_package / RECEIPT_FILENAME).read_text(encoding="utf-8"))
    receipt["registry_sha256"] = manifest["registry_sha256"]
    receipt["manifest_content_digest"] = manifest["content_digest"]
    receipt["stable_id_set_digest"] = manifest["stable_id_set_digest"]
    receipt["record_count"] = manifest["record_count"]
    receipt["identity_continuation_count"] = manifest["identity_continuation_count"]
    receipt["new_identity_count"] = manifest["new_identity_count"]
    receipt["authority_status"] = manifest["authority_status"]
    receipt["activation_status"] = manifest["activation_status"]
    package_state = receipt[PACKAGE_STATE_FIELD]
    package_state[PACKAGE_MATERIALIZED_FIELD] = manifest[PACKAGE_MATERIALIZED_FIELD]
    package_state[STABLE_RECORD_V2_ACTIVATED_FIELD] = manifest[STABLE_RECORD_V2_ACTIVATED_FIELD]
    package_state[ROW_V1_RETIRED_FIELD] = manifest[ROW_V1_RETIRED_FIELD]
    package_state[PRODUCTION_REINDEX_AUTHORIZED_FIELD] = manifest[
        PRODUCTION_REINDEX_AUTHORIZED_FIELD
    ]
    receipt.pop("receipt_hash", None)
    receipt["receipt_hash"] = compute_receipt_hash(receipt)
    receipt_bytes = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    (authority_package / RECEIPT_FILENAME).write_bytes(receipt_bytes)

    manifest["receipt_sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
    manifest["manifest_hash"] = compute_manifest_hash(manifest)
    (authority_package / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _metadata(**overrides) -> DocumentMetadata:
    values = {
        "title": "Example",
        "source_type": "database",
        "record_type": "merchant_case",
        "publish_date": date(2026, 8, 26),
        "source_sheet": SHEET,
        "source_row": 10,
    }
    values.update(overrides)
    return DocumentMetadata(**values)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "MKA").mkdir(parents=True)
    return vault


def _write_synced(path: Path, **overrides) -> None:
    values = {
        "title": "Example",
        "source_type": "database",
        "record_type": "merchant_case",
        "status": "published",
        "publish_date": "2026-08-26",
        "source_sheet": SHEET,
        "source_row": 10,
        "data_classification": "internal",
        "can_quote_externally": False,
        "can_enter_content_index": True,
        "allowed_exposure_channels": [],
        "managed_by": "marketing-knowledge-agent",
    }
    values.update(overrides)
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                lines.extend(f"  - '{item}'" for item in value)
            else:
                lines.append(f"{key}: []")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: '{value}'")
    lines.extend(["---", "", "Known content for a temporary index.", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
