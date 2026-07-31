import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.missing_parent_diagnostic import (
    DIAGNOSTIC_OUTPUT_FILENAMES,
    diagnose_missing_formal_parents,
)


def test_diagnostic_classifies_all_five_outcomes(tmp_path):
    paths = _fixture(tmp_path)

    summary = _diagnose(paths)

    rows = _read_csv(paths["output"] / "missing_parent_classification.csv")
    by_id = {row["record_id"]: row for row in rows}
    assert summary["missing_parent_count"] == 5
    assert summary["orphan_asset_count"] == 5
    assert by_id["sheet:r1"]["classification_code"] == "A"
    assert by_id["sheet:r1"]["classification"] == "approved_but_not_synced"
    assert by_id["sheet:r2"]["classification_code"] == "B"
    assert by_id["sheet:r2"]["classification"] == "intentionally_excluded"
    assert by_id["sheet:r3"]["classification_code"] == "C"
    assert by_id["sheet:r3"]["classification"] == "restricted_or_internal_only"
    assert by_id["sheet:r4"]["classification_code"] == "D"
    assert by_id["sheet:r4"]["classification"] == "parent_mapping_error"
    assert by_id["sheet:r5"]["classification_code"] == "E"
    assert by_id["sheet:r5"]["classification"] == "requires_human_review"


def test_recommendation_is_unique_and_sync_requires_all_gates(tmp_path):
    paths = _fixture(tmp_path)

    _diagnose(paths)

    rows = _read_csv(paths["output"] / "missing_parent_recommended_actions.csv")
    assert {row["record_id"]: row["recommended_action"] for row in rows} == {
        "sheet:r1": "sync_parent",
        "sheet:r2": "exclude_child_assets",
        "sheet:r3": "keep_blocked",
        "sheet:r4": "remap_child_assets",
        "sheet:r5": "manual_review",
    }
    sync_row = next(row for row in rows if row["recommended_action"] == "sync_parent")
    assert sync_row["sync_gate_passed"] == "true"


def test_asset_url_approval_does_not_override_parent_exclusion(tmp_path):
    paths = _fixture(tmp_path)

    _diagnose(paths)

    rows = _read_csv(paths["output"] / "missing_parent_records.csv")
    excluded = next(row for row in rows if row["record_id"] == "sheet:r2")
    assert excluded["asset_url_review_status"] == "approved"
    assert excluded["review_decision"] == "exclude"
    assert excluded["classification"] == "intentionally_excluded"


def test_tags_only_safe_for_approved_external_parent(tmp_path):
    paths = _fixture(tmp_path)

    _diagnose(paths)

    rows = _read_csv(paths["output"] / "missing_parent_tag_validation.csv")
    by_id = {row["record_id"]: row for row in rows}
    assert by_id["sheet:r1"]["content_tags_source"] == "excel_preview_parent"
    assert json.loads(by_id["sheet:r1"]["content_tags_json"]) == ["source-tag"]
    assert by_id["sheet:r1"]["safe_for_external_use"] == "true"
    for record_id in ("sheet:r2", "sheet:r3", "sheet:r4", "sheet:r5"):
        assert by_id[record_id]["safe_for_external_use"] == "false"
        assert by_id[record_id]["resolved_tags_json"] == "[]"


def test_orphan_asset_report_preserves_both_approved_urls(tmp_path):
    paths = _fixture(tmp_path)

    _diagnose(paths)

    rows = _read_csv(paths["output"] / "orphan_assets.csv")
    assert len(rows) == 5
    assert all(row["asset_url"].startswith("https://example.com/") for row in rows)
    assert all(row["canonical_url"].startswith("https://example.com/") for row in rows)


def test_diagnostic_rechecks_vault_and_sqlite_read_only(tmp_path):
    paths = _fixture(tmp_path)
    protected = _protected_hashes(paths)

    summary = _diagnose(paths)

    assert summary["formal_vault_modified"] is False
    assert summary["formal_sqlite_modified"] is False
    assert summary["source_files_modified"] is False
    assert _protected_hashes(paths) == protected


def test_reports_are_complete_and_deterministic(tmp_path):
    paths = _fixture(tmp_path)

    first = _diagnose(paths)
    first_hashes = _output_hashes(paths["output"])
    second = _diagnose(paths)
    second_hashes = _output_hashes(paths["output"])

    assert first == second
    assert first_hashes == second_hashes
    assert {path.name for path in paths["output"].iterdir()} == set(
        DIAGNOSTIC_OUTPUT_FILENAMES
    )


def test_cli_runs_diagnostic_without_apply_or_sync(tmp_path, capsys):
    paths = _fixture(tmp_path)

    exit_code = main(
        [
            "diagnose-missing-formal-parents",
            "--join-validation",
            str(paths["joins"]),
            "--apply-preview",
            str(paths["apply"]),
            "--parent-records",
            str(paths["parents"]),
            "--review-decisions",
            str(paths["decisions"]),
            "--restricted-customers",
            str(paths["restricted"]),
            "--vault",
            str(paths["vault"]),
            "--db",
            str(paths["db"]),
            "--output",
            str(paths["output"]),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["diagnostic_only"] is True
    assert payload["repairs_applied"] is False


def _fixture(tmp_path):
    paths = {
        "joins": tmp_path / "asset_source_record_join_validation.csv",
        "apply": tmp_path / "asset_apply_preview.csv",
        "parents": tmp_path / "merchant_cases.json",
        "decisions": tmp_path / "review_decisions_template.csv",
        "restricted": tmp_path / "restricted_customers.json",
        "vault": tmp_path / "obsidian_vault",
        "db": tmp_path / "content_index.sqlite",
        "output": tmp_path / "diagnostic",
    }
    parents = [
        _parent(1, "Brand One", "brand1", "public", True, True),
        _parent(2, "Brand Two", "brand2", "public", False, False),
        _parent(3, "Brand Three", "brand3", "internal", True, False),
        _parent(5, "Brand Five", "brand5", "public", True, True),
    ]
    paths["parents"].write_text(json.dumps(parents), encoding="utf-8")
    decisions = [
        _decision(1, "approve", True, True, True),
        _decision(2, "exclude", True, False, False),
        _decision(3, "approve_internal_only", True, True, False),
        _decision(5, "needs_update", True, True, True),
    ]
    _write_csv(paths["decisions"], decisions)
    joins = []
    apply_rows = []
    for row in range(1, 6):
        record_id = f"sheet:r{row}"
        asset_id = f"{record_id}:article"
        joins.append(
            {
                "asset_id": asset_id,
                "record_id": record_id,
                "preview_parent_status": "unique_match" if row != 4 else "missing",
                "formal_vault_parent_status": "missing",
                "formal_sqlite_parent_status": "missing",
                "identity_status": "match" if row != 4 else "mismatch",
                "reason": "fixture",
            }
        )
        for field in ("asset_url", "canonical_url"):
            apply_rows.append(
                {
                    "record_id": record_id,
                    "asset_id": asset_id,
                    "brand_name": f"Brand {row}",
                    "asset_type": "article",
                    "asset_title": f"Article {row}",
                    "field": field,
                    "current_value": "",
                    "proposed_value": f"https://example.com/{row}/{field}",
                    "review_decision": "approve",
                    "reviewer": "Reviewer",
                    "reviewed_at": "2026-07-17",
                    "provenance": "fixture",
                    "source_location": f"sheet:r{row}",
                    "eligibility": "ready_for_apply_preview",
                    "governance_status": "eligible",
                    "action": "add",
                    "reason": "fixture",
                }
            )
    _write_csv(paths["joins"], joins)
    _write_csv(paths["apply"], apply_rows)
    paths["restricted"].write_text("[]", encoding="utf-8")
    (paths["vault"] / ".obsidian").mkdir(parents=True)
    (paths["vault"] / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    with sqlite3.connect(paths["db"]) as connection:
        connection.execute(
            "CREATE TABLE documents (id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)"
        )
    return paths


def _parent(row, brand, handle, classification, can_index, can_quote):
    return {
        "record_type": "merchant_case",
        "source_sheet": "sheet",
        "source_row": row,
        "brand_name": brand,
        "merchant_handle": handle,
        "data_classification": classification,
        "can_enter_content_index": can_index,
        "can_quote_externally": can_quote,
        "content_tags": ["source-tag"],
        "governance_issue_types": [],
        "governance_risk_reasons": [],
        "status": "active",
    }


def _decision(row, decision, can_vault, can_index, can_quote):
    return {
        "source_sheet": "sheet",
        "source_row": row,
        "record_type": "merchant_case",
        "brand_name": f"Brand {row}",
        "merchant_handle": f"brand{row}",
        "review_decision": decision,
        "can_enter_vault": str(can_vault).lower(),
        "can_enter_content_index": str(can_index).lower(),
        "can_quote_externally": str(can_quote).lower(),
        "final_status": "approved" if decision == "approve" else "review_required",
        "issue_type": "",
        "governance_issue_types": "",
        "governance_risk_reasons": "",
        "reviewer": "Reviewer",
        "reviewed_at": "2026-07-17",
        "notes": "fixture",
    }


def _diagnose(paths):
    return diagnose_missing_formal_parents(
        join_validation_path=paths["joins"],
        apply_preview_path=paths["apply"],
        parent_records_path=paths["parents"],
        review_decisions_path=paths["decisions"],
        restricted_customers_path=paths["restricted"],
        vault_path=paths["vault"],
        db_path=paths["db"],
        output_dir=paths["output"],
    )


def _write_csv(path, rows):
    rows = list(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _protected_hashes(paths):
    return {
        key: _hash_path(paths[key])
        for key in (
            "joins",
            "apply",
            "parents",
            "decisions",
            "restricted",
            "vault",
            "db",
        )
    }


def _output_hashes(path):
    return {
        child.name: hashlib.sha256(child.read_bytes()).hexdigest()
        for child in sorted(path.iterdir())
    }


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()
