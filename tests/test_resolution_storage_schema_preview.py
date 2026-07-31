import csv
import json
import sqlite3
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.resolution_storage_schema_preview import (
    AssetEligibilityMetadata,
    ManagedParentAliasMetadata,
    ResolutionStorageSchemaError,
    create_temporary_sqlite_migration,
    generate_resolution_storage_schema_preview,
    render_managed_asset,
    render_managed_parent,
    validate_managed_asset_round_trip,
    validate_managed_parent_round_trip,
)


REVIEWED_AT = "2026-07-19T10:30:00+08:00"


def test_parent_alias_schema_round_trip_is_lossless_and_deterministic():
    metadata = ManagedParentAliasMetadata(
        record_id="Sheet:r32",
        search_aliases=("SLP", "SHOPLINE Payments"),
        search_alias_reviewed_by="Admin",
        search_alias_reviewed_at=REVIEWED_AT,
        search_alias_provenance="Admin-approved exact aliases",
    )

    first = render_managed_parent(metadata)
    parsed = validate_managed_parent_round_trip(first)
    second = render_managed_parent(parsed)

    assert parsed.search_aliases == ("SLP", "SHOPLINE Payments")
    assert parsed.search_alias_reviewed_by == "Admin"
    assert first == second


@pytest.mark.parametrize(
    "kwargs",
    [
        {"search_aliases": ("SLP", "slp")},
        {"search_alias_reviewed_by": "James Huang"},
        {"search_alias_reviewed_at": "2026-07-19"},
        {"search_alias_provenance": ""},
    ],
)
def test_parent_alias_schema_rejects_invalid_values(kwargs):
    values = {
        "record_id": "Sheet:r32",
        "search_aliases": ("SLP",),
        "search_alias_reviewed_by": "Admin",
        "search_alias_reviewed_at": REVIEWED_AT,
        "search_alias_provenance": "Admin-approved exact aliases",
    }
    values.update(kwargs)

    with pytest.raises(ResolutionStorageSchemaError):
        ManagedParentAliasMetadata(**values)


def test_asset_eligibility_schema_round_trip_is_lossless_and_deterministic():
    metadata = AssetEligibilityMetadata(
        asset_id="Sheet:r12:video",
        record_id="Sheet:r12",
        asset_index_eligibility="hold",
        asset_search_eligibility="not_searchable",
        eligibility_reason="Source evidence remains under review",
        reviewed_by="Admin",
        reviewed_at=REVIEWED_AT,
    )

    first = render_managed_asset(metadata)
    parsed = validate_managed_asset_round_trip(first)
    second = render_managed_asset(parsed)

    assert parsed.asset_index_eligibility == "hold"
    assert parsed.asset_search_eligibility == "not_searchable"
    assert first == second


@pytest.mark.parametrize(
    "index_eligibility,search_eligibility",
    [
        ("unknown", "not_searchable"),
        ("include", "excluded"),
        ("hold", "searchable"),
        ("exclude", "searchable_internal"),
    ],
)
def test_asset_eligibility_schema_fails_closed(index_eligibility, search_eligibility):
    with pytest.raises(ResolutionStorageSchemaError):
        AssetEligibilityMetadata(
            asset_id="Sheet:r12:video",
            record_id="Sheet:r12",
            asset_index_eligibility=index_eligibility,
            asset_search_eligibility=search_eligibility,
            eligibility_reason="Reviewed",
            reviewed_by="Admin",
            reviewed_at=REVIEWED_AT,
        )


def test_temporary_sqlite_migration_validates_fk_aliases_read_only_and_rollback(tmp_path):
    parents = [
        {
            "record_id": "Sheet:r32",
            "brand_name": "Tea Room",
            "merchant_handle": "",
            "entity_type": "merchant",
            "governance_eligibility": "included",
        },
        {
            "record_id": "Sheet:r99",
            "brand_name": "Second Legitimate Source",
            "merchant_handle": "second",
            "entity_type": "merchant",
            "governance_eligibility": "included",
        },
    ]
    aliases = [
        {
            "record_id": "Sheet:r32",
            "alias": "SLP",
            "normalized_alias": "slp",
            "reviewed_by": "Admin",
            "reviewed_at": REVIEWED_AT,
            "provenance": "Admin-approved exact alias",
        }
    ]
    assets = [
        {
            "asset_id": "Sheet:r32:article",
            "record_id": "Sheet:r32",
            "asset_type": "article",
            "asset_title": "Article",
            "asset_url": "https://example.com/article",
            "canonical_url": "https://example.com/article",
            "asset_index_eligibility": "include",
            "asset_search_eligibility": "searchable",
            "eligibility_reason": "Approved",
            "reviewed_by": "Admin",
            "reviewed_at": REVIEWED_AT,
            "can_external_reference": True,
        }
    ]

    result = create_temporary_sqlite_migration(
        tmp_path / "candidate.sqlite",
        parents=parents,
        aliases=aliases,
        assets=assets,
    )

    assert result["foreign_key_errors"] == 0
    assert result["read_only_reopen"] is True
    assert result["rollback_verified"] is True
    assert result["same_record_alias_collision_blocked"] is True
    assert result["multi_record_alias_match_count"] == 2
    connection = sqlite3.connect(f"file:{tmp_path / 'candidate.sqlite'}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM content_assets").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_record_aliases").fetchone()[0] == 1
    finally:
        connection.close()


def test_temporary_sqlite_rejects_unknown_parent(tmp_path):
    with pytest.raises(ResolutionStorageSchemaError, match="foreign key"):
        create_temporary_sqlite_migration(
            tmp_path / "candidate.sqlite",
            parents=[],
            aliases=[],
            assets=[
                {
                    "asset_id": "Sheet:r404:article",
                    "record_id": "Sheet:r404",
                    "asset_type": "article",
                    "asset_title": "Orphan",
                    "asset_url": "https://example.com/orphan",
                    "canonical_url": "https://example.com/orphan",
                    "asset_index_eligibility": "include",
                    "asset_search_eligibility": "searchable",
                    "eligibility_reason": "Invalid fixture",
                    "reviewed_by": "Admin",
                    "reviewed_at": REVIEWED_AT,
                    "can_external_reference": True,
                }
            ],
        )


def test_schema_preview_generates_new_reports_without_executable_plan(tmp_path):
    paths = _copy_real_inputs(tmp_path)
    summary = generate_resolution_storage_schema_preview(**paths)

    assert summary["managed_parent_schema_ready"] is True
    assert summary["managed_asset_schema_ready"] is True
    assert summary["eligible_asset_count"] == 205
    assert summary["hold_asset_count"] == 1
    assert summary["excluded_asset_count"] == 16
    assert summary["approved_url_field_count"] == 410
    assert summary["parent_sync_count"] == 4
    assert summary["excluded_parent_sync_count"] == 1
    assert summary["plan_id_generated"] is False
    assert summary["formal_data_modified"] is False
    assert len(list(paths["output_dir"].iterdir())) == 14

    sync_rows = _read_csv(paths["output_dir"] / "parent_sync_plan.csv")
    excluded_rows = _read_csv(paths["output_dir"] / "excluded_parent_sync_report.csv")
    assert {row["record_id"] for row in sync_rows} == {
        "商家夥伴案例資料庫:r7",
        "商家夥伴案例資料庫:r12",
        "商家夥伴案例資料庫:r32",
        "商家夥伴案例資料庫:r122",
    }
    assert [row["record_id"] for row in excluded_rows] == ["商家夥伴案例資料庫:r30"]

    assessment = (paths["output_dir"] / "authoritative_storage_assessment.md").read_text(
        encoding="utf-8"
    )
    assert "not sufficient" in assessment
    assert "append-only" in assessment
    assert not (paths["output_dir"] / "resolution_apply_manifest.json").exists()


def test_schema_preview_cli_is_preview_only(tmp_path, capsys):
    paths = _copy_real_inputs(tmp_path)
    exit_code = main(
        [
            "preview-resolution-storage-schema",
            "--resolution-dir",
            str(paths["resolution_dir"]),
            "--parent-records",
            str(paths["parent_records_path"]),
            "--review-decisions",
            str(paths["review_decisions_path"]),
            "--asset-apply-preview",
            str(paths["asset_apply_preview_path"]),
            "--asset-blocked-preview",
            str(paths["asset_blocked_preview_path"]),
            "--vault",
            str(paths["vault_path"]),
            "--db",
            str(paths["db_path"]),
            "--output",
            str(paths["output_dir"]),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["plan_id_generated"] is False
    assert payload["temporary_sqlite_created"] is True
    assert payload["formal_data_modified"] is False


def _copy_real_inputs(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    return {
        "resolution_dir": root / "reports/missing_parent_resolution_preview",
        "parent_records_path": root / "reports/excel_preview/merchant_cases.json",
        "review_decisions_path": root / "reports/excel_preview/review_decisions_template.csv",
        "asset_apply_preview_path": root
        / "reports/asset_metadata_apply_preview/asset_apply_preview.csv",
        "asset_blocked_preview_path": root
        / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv",
        "vault_path": root / "obsidian_vault",
        "db_path": root / ".mka/content_index.sqlite",
        "output_dir": tmp_path / "resolution_storage_schema_preview",
    }


def _read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
