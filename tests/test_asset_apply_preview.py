import csv
import hashlib
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.asset_apply_preview import (
    AssetApplyPreviewError,
    OUTPUT_FILENAMES,
    generate_asset_apply_preview,
)
from marketing_knowledge_agent.asset_review_validation import validate_asset_review_decisions
from marketing_knowledge_agent.cli import main


def test_only_ready_assets_are_included_and_governance_blocked_are_separate(tmp_path):
    paths = _fixture(tmp_path)

    summary = _generate(paths)

    preview = _read_csv(paths["output"] / "asset_apply_preview.csv")
    blocked = _read_csv(paths["output"] / "asset_apply_preview_blocked.csv")
    assert summary["eligible_asset_count"] == 1
    assert summary["governance_blocked_asset_count"] == 1
    assert len(preview) == 2
    assert {row["asset_id"] for row in preview} == {"sheet:r8:article"}
    assert len(blocked) == 2
    assert {row["asset_id"] for row in blocked} == {"sheet:r9:video"}


def test_restricted_match_fails_closed_without_sensitive_report_values(tmp_path):
    paths = _fixture(tmp_path)
    restricted = tmp_path / "restricted_customers.json"
    restricted.write_text(
        json.dumps([{"brand_name": "Merchant A"}]),
        encoding="utf-8",
    )
    _refresh_validation(paths, restricted_customers_path=restricted)

    summary = generate_asset_apply_preview(
        decisions_path=paths["decisions"],
        inventory_path=paths["inventory"],
        enrichment_path=paths["enrichment"],
        validation_dir=paths["validation"],
        output_dir=paths["output"],
        restricted_customers_path=restricted,
        vault_path=paths["vault"],
        db_path=paths["db"],
        workbook_path=paths["workbook"],
    )

    blocked_text = (paths["output"] / "asset_apply_preview_blocked.csv").read_text(
        encoding="utf-8-sig"
    )
    assert "Merchant A" not in blocked_text
    assert "https://example.com/article" not in blocked_text
    assert "Reviewer A" not in blocked_text
    assert summary["error_count"] >= 1
    assert summary["preview_row_count"] == 0


def test_unsupported_fields_are_ignored_by_apply_contract(tmp_path):
    paths = _fixture(tmp_path, include_unsupported=True)

    summary = _generate(paths)

    rows = _read_csv(paths["output"] / "asset_apply_preview.csv")
    assert summary["approved_field_count"] == 2
    assert {row["field"] for row in rows} == {"asset_url", "canonical_url"}
    assert all("publication_status" not in row for row in rows)


def test_asset_url_and_canonical_url_create_independent_diffs(tmp_path):
    paths = _fixture(tmp_path)

    _generate(paths)

    rows = {row["field"]: row for row in _read_csv(paths["output"] / "asset_apply_preview.csv")}
    assert rows["asset_url"]["proposed_value"].endswith("/asset")
    assert rows["canonical_url"]["proposed_value"].endswith("/canonical")
    assert rows["asset_url"]["action"] == "add"
    assert rows["canonical_url"]["action"] == "add"


def test_url_approval_does_not_infer_publication_or_other_fields(tmp_path):
    paths = _fixture(tmp_path)

    _generate(paths)

    rows = _read_csv(paths["output"] / "asset_apply_preview.csv")
    forbidden = {
        "published_at",
        "publication_status",
        "interview_date",
        "interview_status",
        "review_status",
        "partner_name",
    }
    assert all(forbidden.isdisjoint(row) for row in rows)


def test_preview_does_not_modify_sources_vault_index_or_workbook(tmp_path):
    paths = _fixture(tmp_path)
    before = {key: _sha256(path) for key, path in paths.items() if path.is_file()}

    summary = _generate(paths)

    after = {key: _sha256(path) for key, path in paths.items() if key in before}
    assert before == after
    assert summary["vault_modified"] is False
    assert summary["formal_index_modified"] is False
    assert summary["source_files_modified"] is False
    assert summary["decisions_applied"] is False


def test_asset_and_record_identity_remain_stable(tmp_path):
    paths = _fixture(tmp_path)

    _generate(paths)

    rows = _read_csv(paths["output"] / "asset_apply_preview.csv")
    assert {row["asset_id"] for row in rows} == {"sheet:r8:article"}
    assert {row["record_id"] for row in rows} == {"sheet:r8"}
    assert all(row["brand_name"] == "Merchant A" for row in rows)
    assert all(row["asset_type"] == "article" for row in rows)
    assert all(row["asset_title"] == "Example article" for row in rows)


def test_duplicate_canonical_url_blocks_preview(tmp_path):
    paths = _fixture(tmp_path, second_ready=True)
    decisions = _read_csv(paths["decisions"])
    enrichment = _read_csv(paths["enrichment"])
    shared = "https://example.com/shared"
    for row in decisions:
        if row["field"] == "canonical_url" and row["asset_id"] != "sheet:r9:video":
            row["proposed_value"] = shared
    for row in enrichment:
        if row["field"] == "canonical_url" and row["asset_id"] != "sheet:r9:video":
            row["proposed_value"] = shared
    # The second ready fixture uses r10, while r9 remains governance-only.
    for rows in (decisions, enrichment):
        for row in rows:
            if row["asset_id"] == "sheet:r10:podcast" and row["field"] == "canonical_url":
                row["proposed_value"] = shared
    _write_csv(paths["decisions"], decisions)
    _write_csv(paths["enrichment"], enrichment)
    _refresh_validation(paths)

    summary = _generate(paths)

    assert summary["preview_row_count"] == 0
    assert summary["error_count"] >= 1
    assert "duplicate_canonical_url" in summary["error_codes"]


@pytest.mark.parametrize(
    "value",
    ["not-a-url", "file:///internal.md", "../../internal.md", "https://www.google.com/search?q=x"],
)
def test_malformed_search_or_internal_url_is_blocked(tmp_path, value):
    paths = _fixture(tmp_path)
    _replace_proposal(paths, "asset_url", value)

    summary = _generate(paths)

    assert summary["preview_row_count"] == 0
    assert summary["error_count"] >= 1


def test_empty_approved_value_is_blocked(tmp_path):
    paths = _fixture(tmp_path)
    _replace_proposal(paths, "asset_url", "")

    summary = _generate(paths)

    assert summary["preview_row_count"] == 0
    assert summary["error_codes"]["approved_empty_value"] >= 1


def test_preview_rerun_is_deterministic(tmp_path):
    paths = _fixture(tmp_path)

    first = _generate(paths)
    first_hashes = _output_hashes(paths["output"])
    second = _generate(paths)
    second_hashes = _output_hashes(paths["output"])

    assert first == second
    assert first_hashes == second_hashes


def test_rollback_plan_covers_every_proposed_change(tmp_path):
    paths = _fixture(tmp_path)

    summary = _generate(paths)

    rollback = (paths["output"] / "asset_apply_rollback_plan.md").read_text(encoding="utf-8")
    assert f"Proposed field changes: {summary['preview_row_count']}" in rollback
    assert "pre-apply manifest" in rollback
    assert "atomic" in rollback
    assert "formal index" in rollback


def test_cli_is_explicitly_dry_run_and_writes_only_preview(tmp_path, capsys):
    paths = _fixture(tmp_path)

    exit_code = main(
        [
            "apply-asset-review-decisions",
            "--dry-run",
            "--decisions",
            str(paths["decisions"]),
            "--inventory",
            str(paths["inventory"]),
            "--enrichment",
            str(paths["enrichment"]),
            "--validation-dir",
            str(paths["validation"]),
            "--output",
            str(paths["output"]),
            "--vault",
            str(paths["vault"]),
            "--db",
            str(paths["db"]),
            "--workbook",
            str(paths["workbook"]),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is True
    assert set(path.name for path in paths["output"].iterdir()) == set(OUTPUT_FILENAMES)
    assert payload["preview_row_count"] == 2


def test_apply_preview_module_has_no_formal_write_imports():
    source = Path("src/marketing_knowledge_agent/asset_apply_preview.py").read_text(encoding="utf-8")

    for forbidden in ("apply_review_decisions", "obsidian_sync", "indexing", "SQLiteIndex", "sqlite3"):
        assert forbidden not in source


def test_output_cannot_be_written_inside_protected_vault(tmp_path):
    paths = _fixture(tmp_path)
    vault_dir = tmp_path / "formal-vault"
    vault_dir.mkdir()

    with pytest.raises(AssetApplyPreviewError):
        generate_asset_apply_preview(
            decisions_path=paths["decisions"],
            inventory_path=paths["inventory"],
            enrichment_path=paths["enrichment"],
            validation_dir=paths["validation"],
            output_dir=vault_dir / "preview",
            vault_path=vault_dir,
            db_path=paths["db"],
            workbook_path=paths["workbook"],
        )


def _fixture(tmp_path, *, include_unsupported=False, second_ready=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    inventory = [
        _inventory("sheet:r8:article", "sheet:r8", "article", "Example article", "Merchant A"),
        _inventory("sheet:r9:video", "sheet:r9", "video", "", "Merchant B", "審核中"),
    ]
    if second_ready:
        inventory.append(_inventory("sheet:r10:podcast", "sheet:r10", "podcast", "Example audio", "Merchant C"))
    enrichment = []
    decisions = []
    for asset in inventory:
        blocked = bool(asset["invalid_asset_value"])
        for field, suffix in (("asset_url", "asset"), ("canonical_url", "canonical")):
            proposed = "" if blocked else f"https://example.com/{asset['asset_type']}/{suffix}"
            row = _enrichment(asset, field, proposed)
            enrichment.append(row)
            decisions.append(
                {
                    **row,
                    "review_decision": "exclude_asset" if blocked else "approve",
                    "reviewer": "Reviewer A",
                    "reviewed_at": "2026-07-17",
                    "notes": "Governance evidence only." if blocked else "",
                }
            )
        if include_unsupported:
            row = _enrichment(asset, "publication_status", "unknown")
            enrichment.append(row)
            decisions.append({**row, "review_decision": "", "reviewer": "", "reviewed_at": "", "notes": ""})
    paths = {
        "inventory": tmp_path / "asset_metadata_inventory.csv",
        "enrichment": tmp_path / "asset_metadata_enrichment_preview.csv",
        "decisions": tmp_path / "human_review_template.csv",
        "validation": tmp_path / "validation",
        "output": tmp_path / "apply_preview",
        "vault": tmp_path / "vault_sentinel.md",
        "db": tmp_path / "index_sentinel.sqlite",
        "workbook": tmp_path / "workbook_sentinel.xlsx",
    }
    _write_csv(paths["inventory"], inventory)
    _write_csv(paths["enrichment"], enrichment)
    _write_csv(paths["decisions"], decisions)
    paths["vault"].write_text("vault unchanged", encoding="utf-8")
    paths["db"].write_bytes(b"index unchanged")
    paths["workbook"].write_bytes(b"workbook unchanged")
    _refresh_validation(paths)
    return paths


def _refresh_validation(paths, *, restricted_customers_path=None):
    validate_asset_review_decisions(
        decisions_path=paths["decisions"],
        inventory_path=paths["inventory"],
        enrichment_path=paths["enrichment"],
        output_dir=paths["validation"],
        restricted_customers_path=restricted_customers_path,
    )


def _generate(paths):
    return generate_asset_apply_preview(
        decisions_path=paths["decisions"],
        inventory_path=paths["inventory"],
        enrichment_path=paths["enrichment"],
        validation_dir=paths["validation"],
        output_dir=paths["output"],
        vault_path=paths["vault"],
        db_path=paths["db"],
        workbook_path=paths["workbook"],
    )


def _replace_proposal(paths, field, value):
    decisions = _read_csv(paths["decisions"])
    enrichment = _read_csv(paths["enrichment"])
    for rows in (decisions, enrichment):
        for row in rows:
            if row["asset_id"] == "sheet:r8:article" and row["field"] == field:
                row["proposed_value"] = value
    _write_csv(paths["decisions"], decisions)
    _write_csv(paths["enrichment"], enrichment)
    _refresh_validation(paths)


def _inventory(asset_id, record_id, asset_type, title, brand, invalid=""):
    return {
        "record_id": record_id,
        "asset_id": asset_id,
        "record_type": "merchant_case",
        "brand_name": brand,
        "asset_type": asset_type,
        "asset_title": title,
        "source_sheet": "sheet",
        "source_row": record_id.split("r")[-1],
        "asset_source_field": f"{asset_type}_title",
        "excel_cell": "sheet!H8",
        "source_urls": "[]",
        "asset_url": "",
        "canonical_url": "",
        "asset_published_at": "",
        "asset_publication_status": "unknown",
        "interview_date": "",
        "interview_status": "",
        "review_status": "",
        "partner_name": "",
        "internal_file_path": "",
        "vault_present": "false",
        "sqlite_present": "false",
        "record_publish_date": "2026-07-01",
        "record_status": "published",
        "record_review_decision": "approve",
        "invalid_asset_value": invalid,
        "evidence_sources": "[]",
    }


def _enrichment(asset, field, proposed):
    return {
        "record_id": asset["record_id"],
        "asset_id": asset["asset_id"],
        "brand_name": asset["brand_name"],
        "asset_type": asset["asset_type"],
        "field": field,
        "existing_value": "",
        "proposed_value": proposed,
        "source": "excel_hyperlink" if proposed and proposed != "unknown" else "none",
        "source_location": "sheet!H8" if proposed and proposed != "unknown" else "",
        "provenance": "review candidate" if proposed and proposed != "unknown" else "no evidence",
        "confidence": "high" if proposed and proposed != "unknown" else "none",
        "conflict_status": "none" if proposed and proposed != "unknown" else "missing_evidence",
        "review_required": "true" if proposed and proposed != "unknown" else "false",
        "reason": "candidate" if proposed and proposed != "unknown" else "no evidence",
        "proposed_decision": "approve_candidate" if proposed and proposed != "unknown" else "needs_source",
        "approved_for_index": "false",
    }


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _output_hashes(output):
    return {
        path.name: _sha256(path)
        for path in sorted(output.iterdir())
        if path.is_file() and not path.name.startswith("._")
    }
