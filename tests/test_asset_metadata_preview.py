import csv
import json
from pathlib import Path

from marketing_knowledge_agent.asset_metadata_preview import (
    ASSET_METADATA_FIELD_REGISTRY,
    OUTPUT_FILENAMES,
    build_asset_inventory,
    build_enrichment_preview,
    canonical_url_candidate,
    generate_asset_metadata_preview,
    is_enrichment_index_eligible,
    parse_asset_date,
)
from marketing_knowledge_agent.cli import main


def test_asset_field_registry_distinguishes_level_source_and_execution_readiness():
    publication = ASSET_METADATA_FIELD_REGISTRY["publication_status"]
    partner = ASSET_METADATA_FIELD_REGISTRY["partner_name"]

    assert publication.value_scope == "asset_level"
    assert publication.authoritative_source == "publisher state or human-reviewed asset evidence"
    assert publication.auto_derivation_allowed is False
    assert publication.retrieval_index_eligible is False
    assert partner.value_scope == "record_level"
    assert partner.retrieval_index_eligible is False


def test_parent_status_does_not_pollute_asset_status():
    inventory = build_asset_inventory([_merchant(status="published")])
    rows = build_enrichment_preview(inventory)

    publication = _field_row(rows, "publication_status")
    assert publication["existing_value"] == "unknown"
    assert publication["proposed_value"] == "unknown"
    assert publication["source"] == "none"
    assert "parent" in publication["reason"]


def test_url_does_not_imply_published_status():
    inventory = build_asset_inventory(
        [_merchant()],
        workbook_hyperlinks={("商家夥伴案例資料庫", 8, "article"): ["https://example.com/story"]},
    )
    rows = build_enrichment_preview(inventory)

    assert _field_row(rows, "asset_url")["proposed_value"] == "https://example.com/story"
    assert _field_row(rows, "publication_status")["proposed_value"] == "unknown"


def test_missing_evidence_stays_unknown():
    rows = build_enrichment_preview(build_asset_inventory([_merchant()]))

    for field in ("asset_url", "canonical_url", "published_at", "publication_status", "interview_date"):
        row = _field_row(rows, field)
        assert row["proposed_value"] in {"", "unknown"}
        assert row["conflict_status"] == "missing_evidence"


def test_conflicting_urls_require_human_review():
    record = _merchant()
    record["article_url"] = "https://example.com/from-record"
    inventory = build_asset_inventory(
        [record],
        workbook_hyperlinks={
            ("商家夥伴案例資料庫", 8, "article"): ["https://example.com/from-workbook"]
        },
    )
    rows = build_enrichment_preview(inventory)

    canonical = _field_row(rows, "canonical_url")
    assert canonical["proposed_value"] == ""
    assert canonical["conflict_status"] == "conflicting_candidates"
    assert canonical["review_required"] is True


def test_complete_dates_parse_and_invalid_dates_are_rejected():
    assert parse_asset_date("2025-07-01") == "2025-07-01"
    assert parse_asset_date("2025/07/01") == "2025-07-01"
    assert parse_asset_date("2025.07.01") == "2025-07-01"
    assert parse_asset_date("2025-02-31") is None
    assert parse_asset_date("2025-07") is None


def test_invalid_explicit_date_is_preserved_as_conflict_not_proposed():
    record = _merchant()
    record["interview_date"] = "2025-02-31"
    rows = build_enrichment_preview(build_asset_inventory([record]))

    interview_date = _field_row(rows, "interview_date")
    assert interview_date["existing_value"] == "2025-02-31"
    assert interview_date["proposed_value"] == ""
    assert interview_date["conflict_status"] == "invalid_value"
    assert interview_date["review_required"] is True


def test_multiple_urls_with_same_canonical_target_are_not_a_conflict():
    result = canonical_url_candidate(
        [
            "https://example.com/story?utm_source=test",
            "https://example.com/story",
        ]
    )

    assert result["proposed_value"] == "https://example.com/story"
    assert result["conflict_status"] == "tracking_parameters_removed"
    assert result["review_required"] is True


def test_search_and_redirect_urls_are_not_canonical_candidates():
    result = canonical_url_candidate(["https://www.google.com/search?q=example"])

    assert result["proposed_value"] == ""
    assert result["conflict_status"] == "noncanonical_source_url"
    assert result["review_required"] is True


def test_review_decision_must_be_approved_before_index_eligibility():
    row = {
        "field": "asset_url",
        "proposed_value": "https://example.com/story",
        "conflict_status": "none",
        "review_decision": "",
    }

    assert is_enrichment_index_eligible(row) is False
    row["review_decision"] = "approve"
    assert is_enrichment_index_eligible(row) is True


def test_generate_preview_writes_required_files_without_mutating_inputs(tmp_path):
    preview_dir = tmp_path / "excel_preview"
    output_dir = tmp_path / "asset_preview"
    preview_dir.mkdir()
    merchant_path = preview_dir / "merchant_cases.json"
    merchant_path.write_text(json.dumps([_merchant()], ensure_ascii=False), encoding="utf-8")
    before = merchant_path.read_bytes()

    summary = generate_asset_metadata_preview(
        preview_dir=preview_dir,
        output_dir=output_dir,
        workbook_path=None,
        vault_path=None,
        db_path=None,
        decisions_path=None,
    )

    assert set(path.name for path in output_dir.iterdir()) == set(OUTPUT_FILENAMES)
    assert merchant_path.read_bytes() == before
    assert summary["asset_count"] == 1
    assert summary["formal_index_modified"] is False
    assert summary["vault_modified"] is False

    with (output_dir / "human_review_template.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert all(row["review_decision"] == "" for row in rows)


def test_asset_metadata_preview_cli_is_read_only_and_reports_summary(tmp_path, capsys):
    preview_dir = tmp_path / "excel_preview"
    output_dir = tmp_path / "asset_preview"
    preview_dir.mkdir()
    (preview_dir / "merchant_cases.json").write_text(
        json.dumps([_merchant()], ensure_ascii=False), encoding="utf-8"
    )

    exit_code = main(
        [
            "asset-metadata-preview",
            "--preview-dir",
            str(preview_dir),
            "--vault",
            str(tmp_path / "missing-vault"),
            "--db",
            str(tmp_path / "missing.sqlite"),
            "--decisions",
            str(tmp_path / "missing-decisions.csv"),
            "--output",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["asset_count"] == 1
    assert payload["formal_index_modified"] is False
    assert payload["vault_modified"] is False


def _merchant(status="published"):
    return {
        "record_type": "merchant_case",
        "source_sheet": "商家夥伴案例資料庫",
        "source_row": 8,
        "source_path": "商家夥伴案例資料庫:8",
        "brand_name": "Merchant A",
        "article_title": "Example article",
        "video_title": None,
        "podcast_title": None,
        "news_title": None,
        "status": status,
        "publish_date": "2026-07-01",
        "captured_date": "2026-07-01",
        "invalid_asset_values": {},
    }


def _field_row(rows, field):
    return next(row for row in rows if row["field"] == field)
