import csv
import hashlib
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.asset_review_validation import (
    ALLOWED_ASSET_REVIEW_DECISIONS,
    OUTPUT_FILENAMES,
    validate_asset_review_decisions,
)
from marketing_knowledge_agent.cli import main


def test_blank_decision_is_blocked(tmp_path):
    paths = _review_fixture(tmp_path, asset_url_decision="")

    summary = _validate(paths)

    assert summary["error_codes"]["missing_review_decision"] == 1
    assert summary["ready_for_apply_preview_count"] == 0


def test_invalid_decision_is_blocked(tmp_path):
    paths = _review_fixture(tmp_path, asset_url_decision="auto_approve")

    summary = _validate(paths)

    assert summary["error_codes"]["invalid_review_decision"] == 1
    assert set(ALLOWED_ASSET_REVIEW_DECISIONS) == {
        "approve",
        "reject",
        "needs_update",
        "exclude_asset",
        "manual_review",
    }


def test_missing_reviewer_and_invalid_reviewed_at_are_blocked(tmp_path):
    paths = _review_fixture(tmp_path, reviewer="", reviewed_at="17/07/2026")

    summary = _validate(paths)

    assert summary["error_codes"]["missing_reviewer"] == 2
    assert summary["error_codes"]["invalid_reviewed_at"] == 2


def test_unknown_duplicate_and_mismatched_identity_are_blocked(tmp_path):
    paths = _review_fixture(tmp_path)
    rows = _read_csv(paths["decisions"])
    duplicate = dict(rows[0])
    unknown = dict(rows[0], asset_id="sheet:r999:article", record_id="sheet:r999")
    rows[1]["record_id"] = "sheet:r999"
    _write_csv(paths["decisions"], rows + [duplicate, unknown])

    summary = _validate(paths)

    assert summary["error_codes"]["duplicate_asset_field"] == 1
    assert summary["error_codes"]["unknown_asset_id"] == 1
    assert summary["error_codes"]["mismatched_record_asset_pair"] >= 1


def test_approved_empty_url_and_modified_proposal_are_blocked(tmp_path):
    paths = _review_fixture(tmp_path)
    rows = _read_csv(paths["decisions"])
    rows[0]["proposed_value"] = ""
    _write_csv(paths["decisions"], rows)

    summary = _validate(paths)

    assert summary["error_codes"]["proposal_modified"] == 1
    assert summary["error_codes"]["approved_empty_value"] == 1


def test_exclude_asset_and_approve_is_a_conflict(tmp_path):
    paths = _review_fixture(
        tmp_path,
        asset_url_decision="exclude_asset",
        canonical_url_decision="approve",
        notes="Asset must be excluded.",
    )

    summary = _validate(paths)

    assert summary["conflict_count"] == 1
    assert summary["error_codes"]["exclude_approve_conflict"] == 1


def test_manual_review_and_excluded_asset_are_not_eligible(tmp_path):
    manual = _review_fixture(
        tmp_path / "manual",
        asset_url_decision="manual_review",
        canonical_url_decision="manual_review",
        notes="Needs owner confirmation.",
    )
    excluded = _review_fixture(
        tmp_path / "excluded",
        asset_url_decision="exclude_asset",
        canonical_url_decision="exclude_asset",
        notes="Not a content asset.",
    )

    manual_summary = _validate(manual)
    excluded_summary = _validate(excluded)

    assert manual_summary["manual_review_count"] == 1
    assert manual_summary["ready_for_apply_preview_count"] == 0
    assert excluded_summary["excluded_count"] == 1
    assert excluded_summary["ready_for_apply_preview_count"] == 0


def test_governance_only_asset_is_never_eligible(tmp_path):
    paths = _review_fixture(tmp_path, include_governance_asset=True)

    summary = _validate(paths)

    assert summary["governance_blocked_count"] == 1
    assert summary["ready_for_apply_preview_count"] == 1


def test_governance_only_asset_cannot_be_approved_by_url_decision(tmp_path):
    paths = _review_fixture(tmp_path, include_governance_asset=True)
    inventory = _read_csv(paths["inventory"])
    governance_id = next(row["asset_id"] for row in inventory if row["invalid_asset_value"])
    enrichment = _read_csv(paths["enrichment"])
    decisions = _read_csv(paths["decisions"])
    for row in enrichment:
        if row["asset_id"] == governance_id:
            row["proposed_value"] = "https://example.com/governance-only"
            row["source"] = "excel_hyperlink"
    for row in decisions:
        if row["asset_id"] == governance_id:
            row["proposed_value"] = "https://example.com/governance-only"
            row["source"] = "excel_hyperlink"
            row["review_decision"] = "approve"
    _write_csv(paths["enrichment"], enrichment)
    _write_csv(paths["decisions"], decisions)

    summary = _validate(paths)

    assert summary["error_codes"]["governance_approve_conflict"] == 2
    assert summary["governance_blocked_count"] == 1


def test_url_approval_does_not_infer_status_or_modify_sources(tmp_path):
    paths = _review_fixture(tmp_path)
    before = {key: _sha256(path) for key, path in paths.items() if key != "output"}

    summary = _validate(paths)

    after = {key: _sha256(path) for key, path in paths.items() if key != "output"}
    status_rows = _read_csv(paths["output"] / "review_decision_status.csv")
    assert summary["ready_for_apply_preview_count"] == 1
    assert before == after
    assert "publication_status" not in status_rows[0]
    assert "published_at" not in status_rows[0]


def test_decisions_are_preserved_exactly_in_status_report(tmp_path):
    paths = _review_fixture(
        tmp_path,
        asset_url_decision="approve",
        canonical_url_decision="reject",
        notes="Canonical target is not confirmed.",
    )

    _validate(paths)

    rows = _read_csv(paths["output"] / "review_decision_status.csv")
    assert rows[0]["asset_url_decision"] == "approve"
    assert rows[0]["canonical_url_decision"] == "reject"
    assert rows[0]["eligibility"] == "ready_for_apply_preview"


def test_restricted_match_is_blocked_and_redacted_from_reports(tmp_path):
    paths = _review_fixture(tmp_path)
    inventory = _read_csv(paths["inventory"])
    enrichment = _read_csv(paths["enrichment"])
    decisions = _read_csv(paths["decisions"])
    restricted_name = "Restricted Brand A"
    inventory[0]["brand_name"] = restricted_name
    for row in enrichment:
        row["brand_name"] = restricted_name
    for row in decisions:
        row["brand_name"] = restricted_name
    _write_csv(paths["inventory"], inventory)
    _write_csv(paths["enrichment"], enrichment)
    _write_csv(paths["decisions"], decisions)
    denylist = tmp_path / "restricted_customers.json"
    denylist.write_text(
        json.dumps([{"brand_name": restricted_name}], ensure_ascii=False),
        encoding="utf-8",
    )

    summary = validate_asset_review_decisions(
        decisions_path=paths["decisions"],
        inventory_path=paths["inventory"],
        enrichment_path=paths["enrichment"],
        output_dir=paths["output"],
        restricted_customers_path=denylist,
    )

    assert summary["error_codes"]["restricted_data_match"] == 2
    assert summary["ready_for_apply_preview_count"] == 0
    for filename in OUTPUT_FILENAMES:
        assert restricted_name not in (paths["output"] / filename).read_text(encoding="utf-8-sig")


def test_csv_formula_injection_is_blocked_and_not_emitted_as_formula(tmp_path):
    paths = _review_fixture(tmp_path, reviewer="=HYPERLINK(\"https://bad.example\")")

    summary = _validate(paths)

    assert summary["error_codes"]["unsafe_human_input"] == 2
    for filename in OUTPUT_FILENAMES:
        content = (paths["output"] / filename).read_text(encoding="utf-8-sig")
        assert "=HYPERLINK" not in content


def test_canonical_tracking_and_duplicate_targets_require_review(tmp_path):
    paths = _review_fixture(tmp_path, second_asset=True)
    rows = _read_csv(paths["decisions"])
    canonical_rows = [row for row in rows if row["field"] == "canonical_url"]
    canonical_rows[0]["proposed_value"] = "https://example.com/story?utm_source=review"
    canonical_rows[1]["proposed_value"] = "https://example.com/story?utm_source=review"
    enrichment = _read_csv(paths["enrichment"])
    for row in enrichment:
        if row["field"] == "canonical_url":
            row["proposed_value"] = "https://example.com/story?utm_source=review"
    _write_csv(paths["enrichment"], enrichment)
    _write_csv(paths["decisions"], rows)

    summary = _validate(paths)

    assert summary["warning_codes"]["canonical_tracking_parameters"] == 2
    assert summary["warning_codes"]["duplicate_canonical_url"] == 2
    assert summary["manual_review_count"] == 2


@pytest.mark.parametrize(
    "value",
    [
        "https://www.google.com/search?q=example",
        "file:///internal/content.md",
        "../../internal/content.md",
    ],
)
def test_approved_noncanonical_or_internal_url_is_blocked(tmp_path, value):
    paths = _review_fixture(tmp_path)
    rows = _read_csv(paths["decisions"])
    enrichment = _read_csv(paths["enrichment"])
    for row in rows:
        if row["field"] == "canonical_url":
            row["proposed_value"] = value
    for row in enrichment:
        if row["field"] == "canonical_url":
            row["proposed_value"] = value
    _write_csv(paths["enrichment"], enrichment)
    _write_csv(paths["decisions"], rows)

    summary = _validate(paths)

    assert summary["ready_for_apply_preview_count"] == 0
    assert summary["error_count"] >= 1


def test_out_of_scope_fields_do_not_require_decisions_or_enable_constraints(tmp_path):
    paths = _review_fixture(tmp_path)
    rows = _read_csv(paths["decisions"])
    enrichment = _read_csv(paths["enrichment"])
    extra = dict(rows[0], field="publication_status", proposed_value="unknown")
    for key in ("review_decision", "reviewer", "reviewed_at", "notes"):
        extra[key] = ""
    expected = {key: extra[key] for key in extra if key not in {"review_decision", "reviewer", "reviewed_at", "notes"}}
    rows.append(extra)
    enrichment.append(expected)
    _write_csv(paths["enrichment"], enrichment)
    _write_csv(paths["decisions"], rows)

    summary = _validate(paths)

    assert summary["error_count"] == 0
    assert summary["in_scope_review_rows"] == 2
    status = _read_csv(paths["output"] / "review_decision_status.csv")[0]
    assert "publication_status" not in status


def test_missing_and_unexpected_review_rows_are_reported(tmp_path):
    paths = _review_fixture(tmp_path)
    rows = _read_csv(paths["decisions"])
    removed = rows.pop()
    rows.append(dict(removed, field="unexpected_field"))
    _write_csv(paths["decisions"], rows)

    summary = _validate(paths)

    assert summary["missing_review_row_count"] == 1
    assert summary["unexpected_extra_row_count"] == 1


def test_validation_cli_writes_reports_without_apply(tmp_path, capsys):
    paths = _review_fixture(tmp_path)

    exit_code = main(
        [
            "validate-asset-review-decisions",
            "--decisions",
            str(paths["decisions"]),
            "--inventory",
            str(paths["inventory"]),
            "--enrichment",
            str(paths["enrichment"]),
            "--output",
            str(paths["output"]),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ready_for_apply_preview_count"] == 1
    assert set(path.name for path in paths["output"].iterdir()) == set(OUTPUT_FILENAMES)
    assert payload["formal_index_modified"] is False
    assert payload["vault_modified"] is False
    assert payload["decisions_applied"] is False


def _review_fixture(
    tmp_path: Path,
    *,
    asset_url_decision="approve",
    canonical_url_decision="approve",
    reviewer="Reviewer A",
    reviewed_at="2026-07-17",
    notes="",
    include_governance_asset=False,
    second_asset=False,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    inventory = [_inventory_row("sheet:r8:article", "sheet:r8", "article", "Example article")]
    if second_asset:
        inventory.append(_inventory_row("sheet:r9:video", "sheet:r9", "video", "Example video"))
    if include_governance_asset:
        inventory.append(
            _inventory_row(
                "sheet:r10:news",
                "sheet:r10",
                "news",
                "",
                invalid_asset_value="審核中",
            )
        )
    enrichment = []
    decisions = []
    for asset in inventory:
        invalid = bool(asset["invalid_asset_value"])
        for field in ("asset_url", "canonical_url"):
            proposed = "" if invalid else f"https://example.com/{asset['asset_id'].split(':')[-1]}"
            row = _enrichment_row(asset, field, proposed)
            enrichment.append(row)
            decision = asset_url_decision if field == "asset_url" else canonical_url_decision
            if invalid:
                decision = "exclude_asset"
            decisions.append(
                {
                    **row,
                    "review_decision": decision,
                    "reviewer": reviewer,
                    "reviewed_at": reviewed_at,
                    "notes": "Governance-only marker." if invalid else notes,
                }
            )
    paths = {
        "inventory": tmp_path / "asset_metadata_inventory.csv",
        "enrichment": tmp_path / "asset_metadata_enrichment_preview.csv",
        "decisions": tmp_path / "human_review_template.csv",
        "output": tmp_path / "validation",
    }
    _write_csv(paths["inventory"], inventory)
    _write_csv(paths["enrichment"], enrichment)
    _write_csv(paths["decisions"], decisions)
    return paths


def _inventory_row(asset_id, record_id, asset_type, title, invalid_asset_value=""):
    return {
        "record_id": record_id,
        "asset_id": asset_id,
        "record_type": "merchant_case",
        "brand_name": f"Merchant {record_id[-1]}",
        "asset_type": asset_type,
        "asset_title": title,
        "source_sheet": "sheet",
        "source_row": record_id.split("r")[-1],
        "invalid_asset_value": invalid_asset_value,
        "record_status": "published",
    }


def _enrichment_row(asset, field, proposed):
    return {
        "record_id": asset["record_id"],
        "asset_id": asset["asset_id"],
        "brand_name": asset["brand_name"],
        "asset_type": asset["asset_type"],
        "field": field,
        "existing_value": "",
        "proposed_value": proposed,
        "source": "excel_hyperlink" if proposed else "none",
        "source_location": "sheet!H8" if proposed else "",
        "provenance": "exact source URL; not yet human-approved" if proposed else "no evidence",
        "confidence": "high" if proposed else "none",
        "conflict_status": "none" if proposed else "missing_evidence",
        "review_required": "true" if proposed else "false",
        "reason": "candidate" if proposed else "no evidence",
        "proposed_decision": "approve_candidate" if proposed else "needs_source",
        "approved_for_index": "false",
    }


def _validate(paths):
    return validate_asset_review_decisions(
        decisions_path=paths["decisions"],
        inventory_path=paths["inventory"],
        enrichment_path=paths["enrichment"],
        output_dir=paths["output"],
    )


def _write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
