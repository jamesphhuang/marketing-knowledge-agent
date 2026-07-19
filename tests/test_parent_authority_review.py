import csv
import hashlib
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.parent_authority_review import (
    ParentAuthorityReviewError,
    prepare_parent_authority_review,
)


def test_real_authority_gap_is_recomputed_and_admin_resolutions_are_not_requeued(tmp_path):
    summary = prepare_parent_authority_review(**_real_paths(tmp_path))

    assert summary["merchant_parent_count"] == 120
    assert summary["legacy_merchant_authority_count"] == 24
    assert summary["original_authority_gap_count"] == 96
    assert summary["admin_resolution_in_original_gap_count"] == 0
    assert summary["requires_human_review_count"] == 96
    assert summary["batch_safe_cohort_count"] == 1
    assert summary["batch_safe_parent_count"] == 96
    assert summary["manual_review_count"] == 0

    queue = _read_csv(tmp_path / "out" / "baseline_parent_authority_review_template.csv")
    assert len(queue) == 96
    assert not {"商家夥伴案例資料庫:r7", "商家夥伴案例資料庫:r12", "商家夥伴案例資料庫:r30", "商家夥伴案例資料庫:r32", "商家夥伴案例資料庫:r122"} & {
        row["record_id"] for row in queue
    }


def test_existing_admin_metadata_is_preserved_and_unreviewed_rows_are_blank(tmp_path):
    prepare_parent_authority_review(**_real_paths(tmp_path))

    admin_rows = _read_csv(tmp_path / "out" / "existing_admin_resolution_coverage.csv")
    assert len(admin_rows) == 5
    assert {row["reviewer"] for row in admin_rows} == {"Admin"}
    assert {row["reviewed_at"] for row in admin_rows} == {"2026-07-18T00:33:08+08:00"}

    review_rows = _read_csv(tmp_path / "out" / "baseline_parent_authority_review_template.csv")
    assert all(row["final_review_decision"] == "" for row in review_rows)
    assert all(row["reviewer"] == "" for row in review_rows)
    assert all(row["reviewed_at"] == "" for row in review_rows)
    assert all(row["notes"] == "" for row in review_rows)


def test_invalid_existing_admin_review_metadata_fails_closed(tmp_path):
    paths = _real_paths(tmp_path)
    rows = _read_csv(paths["admin_resolutions_path"])
    rows[0]["reviewed_at"] = "2026-07-18"
    admin_path = tmp_path / "admin.csv"
    _write_csv(admin_path, rows)
    paths["admin_resolutions_path"] = admin_path

    with pytest.raises(ParentAuthorityReviewError, match="validation failed"):
        prepare_parent_authority_review(**paths)


def test_formal_presence_is_evidence_not_authority(tmp_path):
    prepare_parent_authority_review(**_real_paths(tmp_path))

    rows = _read_csv(tmp_path / "out" / "baseline_parent_authority_review_template.csv")
    assert all(row["current_vault_presence"] == "true" for row in rows)
    assert all(row["current_index_presence"] == "true" for row in rows)
    assert all(row["authority_status"] == "authority_missing" for row in rows)
    assert all(row["final_review_decision"] == "" for row in rows)


def test_duplicate_record_id_fails_closed(tmp_path):
    paths = _real_paths(tmp_path)
    rows = json.loads(paths["merchant_cases_path"].read_text(encoding="utf-8"))
    rows.append(dict(rows[0]))
    duplicate = tmp_path / "merchant_cases.json"
    duplicate.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    paths["merchant_cases_path"] = duplicate

    with pytest.raises(ParentAuthorityReviewError, match="duplicate record_id"):
        prepare_parent_authority_review(**paths)


def test_same_brand_multi_record_is_not_treated_as_duplicate_row(tmp_path):
    paths = _real_paths(tmp_path)
    merchants = json.loads(paths["merchant_cases_path"].read_text(encoding="utf-8"))
    source = next(row for row in merchants if row["source_row"] == 8)
    extra = dict(source)
    extra["source_row"] = 999
    extra["source_path"] = "商家夥伴案例資料庫:999"
    extra["same_brand_multiple_records"] = True
    extra["multi_interview_record"] = True
    merchants.append(extra)
    merchant_path = tmp_path / "merchant_cases.json"
    merchant_path.write_text(json.dumps(merchants, ensure_ascii=False), encoding="utf-8")
    paths["merchant_cases_path"] = merchant_path

    assets = _read_csv(paths["asset_inventory_path"])
    source_asset = next(row for row in assets if row["record_id"] == "商家夥伴案例資料庫:r8")
    extra_asset = dict(source_asset)
    extra_asset["record_id"] = "商家夥伴案例資料庫:r999"
    extra_asset["asset_id"] = "商家夥伴案例資料庫:r999:article"
    asset_path = tmp_path / "assets.csv"
    _write_csv(asset_path, [*assets, extra_asset])
    paths["asset_inventory_path"] = asset_path

    summary = prepare_parent_authority_review(**paths)
    rows = _read_csv(tmp_path / "out" / "authority_gap_reconciliation.csv")
    row = next(item for item in rows if item["record_id"] == "商家夥伴案例資料庫:r999")
    assert summary["duplicate_record_id_count"] == 0
    assert row["authority_status"] == "manual_investigation_required"
    assert row["reason"] != "duplicate row"


def test_mapping_conflict_is_manual_and_makes_cohort_not_batch_safe(tmp_path):
    paths = _real_paths(tmp_path)
    assets = _read_csv(paths["asset_inventory_path"])
    target = next(row for row in assets if row["record_id"] == "商家夥伴案例資料庫:r8")
    target["brand_name"] = "mismatched parent"
    asset_path = tmp_path / "assets.csv"
    _write_csv(asset_path, assets)
    paths["asset_inventory_path"] = asset_path

    summary = prepare_parent_authority_review(**paths)
    manual = _read_csv(tmp_path / "out" / "baseline_parent_manual_review_queue.csv")
    assert summary["manual_review_count"] == 1
    assert any(row["record_id"] == "商家夥伴案例資料庫:r8" for row in manual)
    assert all(row["batch_approval_safe"] == "false" for row in manual)
    groups = _read_csv(tmp_path / "out" / "baseline_parent_batch_review_groups.csv")
    assert any(row["batch_approval_safe"] == "false" for row in groups)


def test_different_governance_evidence_is_split_into_a_separate_cohort(tmp_path):
    paths = _real_paths(tmp_path)
    merchants = json.loads(paths["merchant_cases_path"].read_text(encoding="utf-8"))
    target = next(row for row in merchants if row["source_row"] == 8)
    target["can_quote_externally"] = False
    merchant_path = tmp_path / "merchant_cases.json"
    merchant_path.write_text(json.dumps(merchants, ensure_ascii=False), encoding="utf-8")
    paths["merchant_cases_path"] = merchant_path

    summary = prepare_parent_authority_review(**paths)
    groups = _read_csv(tmp_path / "out" / "baseline_parent_batch_review_groups.csv")

    assert summary["batch_safe_cohort_count"] == 2
    assert {(row["recommended_review_decision"], row["record_count"]) for row in groups} == {
        ("approve", "95"),
        ("approve_internal_only", "1"),
    }


def test_restricted_pending_excluded_and_url_decisions_are_conserved(tmp_path):
    paths = _real_paths(tmp_path)
    before = _sha256(paths["asset_url_decisions_path"])
    summary = prepare_parent_authority_review(**paths)

    assert summary["restricted_authority_count"] == 11
    assert summary["pending_authority_count"] == 7
    assert summary["excluded_parent_authority_count"] == 10
    assert summary["approved_url_field_count"] == 410
    assert _sha256(paths["asset_url_decisions_path"]) == before


def test_parent_recommendation_does_not_override_asset_hold(tmp_path):
    prepare_parent_authority_review(**_real_paths(tmp_path))

    admin_rows = _read_csv(tmp_path / "out" / "existing_admin_resolution_coverage.csv")
    r12 = next(row for row in admin_rows if row["record_id"] == "商家夥伴案例資料庫:r12")
    assert r12["proposed_review_decision"] == "approve_internal_only"
    assert r12["hold_asset_count"] == "1"
    assert r12["hold_asset_search_eligibility"] == "not_searchable"


def test_rerun_is_deterministic_and_protected_inputs_are_unchanged(tmp_path):
    paths = _real_paths(tmp_path)
    protected = {
        name: _sha256(path)
        for name, path in {
            "review": paths["review_decisions_path"],
            "vault": paths["formal_vault_path"],
            "db": paths["formal_db_path"],
            "asset_decisions": paths["asset_url_decisions_path"],
        }.items()
    }
    first = prepare_parent_authority_review(**paths)
    first_hashes = _directory_hashes(tmp_path / "out")
    second = prepare_parent_authority_review(**paths)

    assert first == second
    assert _directory_hashes(tmp_path / "out") == first_hashes
    assert protected == {
        name: _sha256(path)
        for name, path in {
            "review": paths["review_decisions_path"],
            "vault": paths["formal_vault_path"],
            "db": paths["formal_db_path"],
            "asset_decisions": paths["asset_url_decisions_path"],
        }.items()
    }
    assert not paths["decision_store_path"].exists()


def test_prepare_parent_authority_review_cli(tmp_path, capsys):
    args = ["prepare-parent-authority-review"]
    for option, value in _cli_paths(tmp_path).items():
        args.extend([option, str(value)])

    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["requires_human_review_count"] == 96
    assert payload["formal_data_modified"] is False


def _real_paths(tmp_path):
    root = Path(__file__).resolve().parents[1]
    return {
        "merchant_cases_path": root / "reports/excel_preview/merchant_cases.json",
        "review_decisions_path": root / "reports/excel_preview/review_decisions_template.csv",
        "admin_resolutions_path": root / "reports/missing_parent_resolution_preview/missing_parent_resolution_decisions.csv",
        "baseline_import_preview_path": root / "reports/governance_decision_store_plan/baseline_import_preview.csv",
        "decision_source_inventory_path": root / "reports/governance_decision_store_plan/decision_source_inventory.csv",
        "asset_inventory_path": root / "reports/asset_metadata_preview/asset_metadata_inventory.csv",
        "asset_resolution_path": root / "reports/missing_parent_resolution_preview/asset_eligibility_preview.csv",
        "asset_url_decisions_path": root / "reports/asset_metadata_preview/human_review_template.csv",
        "formal_vault_path": root / "obsidian_vault",
        "formal_db_path": root / ".mka/content_index.sqlite",
        "decision_store_path": root / "data/governance/governance_decisions.sqlite",
        "output_dir": tmp_path / "out",
    }


def _cli_paths(tmp_path):
    paths = _real_paths(tmp_path)
    return {
        "--merchant-cases": paths["merchant_cases_path"],
        "--review-decisions": paths["review_decisions_path"],
        "--admin-resolutions": paths["admin_resolutions_path"],
        "--baseline-import-preview": paths["baseline_import_preview_path"],
        "--decision-source-inventory": paths["decision_source_inventory_path"],
        "--asset-inventory": paths["asset_inventory_path"],
        "--asset-resolution": paths["asset_resolution_path"],
        "--asset-url-decisions": paths["asset_url_decisions_path"],
        "--vault": paths["formal_vault_path"],
        "--db": paths["formal_db_path"],
        "--decision-store": paths["decision_store_path"],
        "--output": paths["output_dir"],
    }


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path):
    path = Path(path)
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _directory_hashes(path):
    return {child.name: _sha256(child) for child in sorted(Path(path).iterdir()) if child.is_file()}
