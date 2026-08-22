"""Tests for the stable-record crosswalk proposal builder.

Two populations of test live here and they are deliberately kept apart.

The **hermetic** tests build their own synthetic workbooks and cover every piece of logic that
decides identity: evidence normalization, the match algorithm, identifier assignment, and the
validators. They never touch a production file, they carry no real merchant name, and they run
everywhere. If the real workbooks are missing, the identity logic is still fully covered.

The **migration** tests pin this specific M1 reconciliation against the two frozen workbooks. They
skip when those workbooks are not present on the machine, and they assert only counts, hashes, and
field names — never a merchant roster, because this file is tracked and the roster is not public.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from marketing_knowledge_agent.excel_ingestion import SHEET_MERCHANT_CASES
from marketing_knowledge_agent.excel_preview import EXPECTED_SHEET_HEADERS
from marketing_knowledge_agent.stable_record_crosswalk import (
    AUTHORITY_STATUS_PROPOSAL_ONLY,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NEW,
    CONFIDENCE_UNMATCHED,
    CROSSWALK_COLUMNS,
    CROSSWALK_FILENAME,
    M1_EXPECTATION,
    MANIFEST_FILENAME,
    MERCHANT_FIELD_ORDER,
    NORMALIZATION_VERSION,
    PROPOSAL_STATE_COMPLETE,
    REGISTRY_COLUMNS,
    REGISTRY_FILENAME,
    SEED_NAMESPACE_IDENTIFIER,
    SEED_NAMESPACE_UUID,
    STABLE_ID_RE,
    StableRecordCrosswalkError,
    UNKNOWN_SUBJECT_FORMAT,
    analyze_decision_impact,
    assign_stable_ids,
    build_crosswalk_proposal,
    build_manifest,
    classify_decision_subject,
    format_stable_id,
    generate_stable_record_crosswalk_proposal,
    hash_file,
    load_merchant_evidence,
    load_proposal,
    match_records,
    normalize_evidence_brand,
    normalize_evidence_handle,
    normalize_evidence_year,
    render_csv,
    validate_expectation,
    validate_proposal,
    verify_existing_proposal,
    write_proposal,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_WORKBOOK = (
    REPO_ROOT
    / "reports/excel_preview/MKT 內容產出資料庫_店家_夥伴案例_對外數據-20260708.xlsx"
)
AUTHORITY_WORKBOOK = Path(
    "/Volumes/T7/MKA Authority/Search Taxonomy/2026-08-21/"
    "MKA_Search_Taxonomy_Authority_2026-08-21.xlsx"
)
DECISION_STORE = REPO_ROOT / "data/governance/governance_decisions.sqlite"

requires_migration_workbooks = pytest.mark.skipif(
    not (LEGACY_WORKBOOK.is_file() and AUTHORITY_WORKBOOK.is_file()),
    reason="the frozen M1 migration workbooks are not present on this machine",
)


# --- synthetic workbook fixtures ------------------------------------------------------------------

MERCHANT_HEADERS = [accepted[0] for accepted in EXPECTED_SHEET_HEADERS[SHEET_MERCHANT_CASES]]


def merchant_row(
    brand,
    year="2025",
    handle="",
    status="現有商家",
    lv1="其他",
    lv2="其他",
    tags="",
    article="-",
    video="-",
    podcast="-",
    news="-",
    notes="",
):
    """One merchant sheet row, in workbook column order."""
    return [year, status, brand, handle, lv1, lv2, tags, article, video, podcast, news, notes]


def write_merchant_workbook(path: Path, rows, headers=None) -> str:
    """Write a synthetic workbook whose merchant sheet starts at the real header row, return its sha."""
    sheet = [[] for _ in range(5)]
    sheet.append(list(headers if headers is not None else MERCHANT_HEADERS))
    sheet.extend(rows)
    _write_xlsx(path, {SHEET_MERCHANT_CASES: sheet})
    return hash_file(path)


def load_pair(tmp_path, legacy_rows, authority_rows):
    legacy_path = tmp_path / "legacy.xlsx"
    authority_path = tmp_path / "authority.xlsx"
    legacy_sha = write_merchant_workbook(legacy_path, legacy_rows)
    authority_sha = write_merchant_workbook(authority_path, authority_rows)
    return (
        load_merchant_evidence(legacy_path, legacy_sha),
        load_merchant_evidence(authority_path, authority_sha),
    )


def confidence_by_brand(matches):
    return {
        (match.legacy or match.authority).brand: match.confidence for match in matches
    }


def _write_xlsx(path: Path, sheets: dict) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(
                f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
                for index, name in enumerate(sheets, start=1)
            )
            + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + "</Relationships>",
        )
        for index, rows in enumerate(sheets.values(), start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))


def _sheet_xml(rows):
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            if value is None or value == "":
                continue
            ref = f"{_column_letters(col_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(row_xml) + "</sheetData></worksheet>"
    )


def _column_letters(index):
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


# --- contract constants ---------------------------------------------------------------------------


def test_seed_namespace_literal_matches_its_derivation():
    """The frozen namespace literal must still be what its documented preimage produces."""
    assert SEED_NAMESPACE_UUID == uuid.uuid5(uuid.NAMESPACE_URL, SEED_NAMESPACE_IDENTIFIER)


def test_merchant_field_order_matches_preflight_headers():
    """Canonical field names stay aligned with the header list they are positionally mapped from."""
    assert len(MERCHANT_FIELD_ORDER) == len(EXPECTED_SHEET_HEADERS[SHEET_MERCHANT_CASES])


def test_stable_id_format_encodes_only_a_sequence():
    assert format_stable_id(1) == "MKA-MC-00001"
    assert format_stable_id(42) == "MKA-MC-00042"
    assert format_stable_id(121) == "MKA-MC-00121"
    assert STABLE_ID_RE.match(format_stable_id(99999))
    with pytest.raises(StableRecordCrosswalkError):
        format_stable_id(0)
    with pytest.raises(StableRecordCrosswalkError):
        format_stable_id(100000)


# --- (I) evidence normalization: year ---------------------------------------------------------------


@pytest.mark.parametrize("value", [2025, 2025.0, "2025", "2025.0", " 2025 ", "2025.00"])
def test_year_normalizes_int_float_and_string_forms_to_one_value(value):
    """The same year arrives typed four different ways; all four must compare equal.

    This is the case ``str(raw)`` gets wrong: the legacy workbook stores "2026" where the authority
    workbook stores "2026.0", so a string comparison reports every record as an identity change.
    """
    assert normalize_evidence_year(value) == "2025"


@pytest.mark.parametrize("value", [None, "", "-", "n/a", "2025.5", "不明", True, False])
def test_year_that_cannot_be_read_as_a_whole_number_is_surfaced_as_missing(value):
    assert normalize_evidence_year(value) is None


def test_year_normalization_is_not_string_coercion():
    """Guard the specific defect: 2025.0 and "2025" must not be distinguishable by year evidence."""
    assert str(2025.0) != str(2025)
    assert normalize_evidence_year(2025.0) == normalize_evidence_year(2025) == "2025"


# --- evidence normalization: brand -----------------------------------------------------------------


def test_brand_normalization_strips_and_collapses_whitespace_only():
    assert normalize_evidence_brand("  測試品牌  ") == "測試品牌"
    assert normalize_evidence_brand("Alpha  &\tBeta") == "Alpha & Beta"
    assert normalize_evidence_brand("") is None
    assert normalize_evidence_brand(None) is None


def test_brand_normalization_does_not_guess():
    """No punctuation stripping, no case folding, no 繁簡 conversion: different brands stay different."""
    assert normalize_evidence_brand("A.B") != normalize_evidence_brand("AB")
    assert normalize_evidence_brand("Casebrand") != normalize_evidence_brand("casebrand")


# --- (J) evidence normalization: handle -------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "-", "null", "none", "nan", "n/a", "  ", " - ", "N/A", "NULL"])
def test_handle_placeholders_normalize_to_missing(value):
    """A placeholder is not an alias. Treating "-" as a handle would match unrelated records."""
    assert normalize_evidence_handle(value) is None


def test_handle_normalization_casefolds():
    assert normalize_evidence_handle("  MyShop ") == "myshop"
    assert normalize_evidence_handle("MYSHOP") == normalize_evidence_handle("myshop")


# --- matching contract ------------------------------------------------------------------------------


def test_high_requires_brand_year_and_two_matching_present_handles(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", year="2025", handle="alpha")],
        [merchant_row("Alpha", year="2025.0", handle="ALPHA")],
    )
    matches = match_records(legacy, authority)
    assert confidence_by_brand(matches) == {"Alpha": CONFIDENCE_HIGH}
    assert "handle_match" in matches[0].evidence


def test_medium_when_a_handle_is_missing_on_either_side(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha"), merchant_row("Beta", handle="-")],
        [merchant_row("Alpha", handle="-"), merchant_row("Beta", handle="-")],
    )
    assert confidence_by_brand(match_records(legacy, authority)) == {
        "Alpha": CONFIDENCE_MEDIUM,
        "Beta": CONFIDENCE_MEDIUM,
    }


# --- (K) handle conflict ------------------------------------------------------------------------------


def test_handle_conflict_is_ambiguous_and_binds_nothing(tmp_path):
    """Two present handles that disagree are contradictory identity evidence, not a payload edit."""
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha-renamed")],
    )
    matches = match_records(legacy, authority)
    conflicted = [match for match in matches if match.confidence == CONFIDENCE_AMBIGUOUS]
    assert len(conflicted) == 1
    assert conflicted[0].authority is None, "an ambiguous record must not bind an authority row"
    assert conflicted[0].conflict_fields == ("merchant_handle",)
    # The unbound authority row is reported as NEW rather than silently dropped.
    assert [match.confidence for match in matches if match.legacy is None] == [CONFIDENCE_NEW]


# --- (L) duplicate discovery key -----------------------------------------------------------------------


def test_duplicate_brand_year_on_the_authority_side_is_ambiguous(tmp_path):
    """Two authority candidates for one legacy record resolves to neither of them.

    The legacy record binds nothing, and both authority rows surface as NEW rather than one being
    silently absorbed — which is what picking the first or nearest candidate would have done.
    """
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha"), merchant_row("Alpha", handle="alpha2")],
    )
    matches = match_records(legacy, authority)
    legacy_match = next(match for match in matches if match.legacy is not None)
    assert legacy_match.confidence == CONFIDENCE_AMBIGUOUS
    assert legacy_match.authority is None
    assert "brand_year_multiple_candidates" in legacy_match.evidence
    assert [match.confidence for match in matches if match.legacy is None] == [
        CONFIDENCE_NEW,
        CONFIDENCE_NEW,
    ]


def test_duplicate_brand_year_on_the_legacy_side_is_ambiguous(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha"), merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha")],
    )
    matches = match_records(legacy, authority)
    assert [match.confidence for match in matches if match.legacy is not None] == [
        CONFIDENCE_AMBIGUOUS,
        CONFIDENCE_AMBIGUOUS,
    ]


def test_no_authority_candidate_is_unmatched_not_guessed(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Gone", handle="gone")],
        [merchant_row("Other", handle="other")],
    )
    matches = match_records(legacy, authority)
    assert confidence_by_brand(matches) == {"Gone": CONFIDENCE_UNMATCHED, "Other": CONFIDENCE_NEW}


def test_match_never_falls_back_to_row_position(tmp_path):
    """A record that only lines up positionally must not be matched by that.

    Both rows sit at the same coordinate and are the only rows in their workbook, which is exactly
    the shape a positional or nearest-row fallback would resolve. It must stay UNMATCHED.
    """
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", year="2025", handle="alpha")],
        [merchant_row("Renamed", year="2025", handle="alpha")],
    )
    matches = match_records(legacy, authority)
    assert confidence_by_brand(matches) == {"Alpha": CONFIDENCE_UNMATCHED, "Renamed": CONFIDENCE_NEW}


def test_row_shift_is_diagnostic_only_and_never_match_evidence(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Inserted", handle="new"), merchant_row("Alpha", handle="alpha")],
    )
    match = next(item for item in match_records(legacy, authority) if item.confidence == CONFIDENCE_HIGH)
    assert match.diagnostics == ("row_shift=+1",)
    assert not any("row" in token for token in match.evidence)


# --- (M, N) identity vs payload change ---------------------------------------------------------------


def test_payload_only_change_does_not_change_the_identity_match(tmp_path):
    """A record whose video moved from "審核中" to a real URL is still the same record.

    This is the shape the M1 migration actually contains: identity evidence untouched, one
    non-identity asset cell moved. Confidence must stay HIGH.
    """
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha", video="審核中", notes="old")],
        [merchant_row("Alpha", handle="alpha", video="https://youtu.be/abc", notes="new")],
    )
    match = match_records(legacy, authority)[0]
    assert match.confidence == CONFIDENCE_HIGH
    assert match.payload_change_fields == ("video", "notes")


def test_payload_change_fields_lists_only_non_identity_fields(tmp_path):
    """Identity evidence never appears as a payload change, even when its representation moved."""
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", year="2025", handle="alpha", status="現有商家")],
        [merchant_row("Alpha", year="2025.0", handle="ALPHA", status="已結束")],
    )
    match = match_records(legacy, authority)[0]
    assert match.payload_change_fields == ("merchant_status",)
    assert "interview_year" not in match.payload_change_fields
    assert "merchant_handle" not in match.payload_change_fields


def test_unchanged_record_reports_no_payload_change(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha", video="審核中")],
        [merchant_row("Alpha", handle="alpha", video="審核中")],
    )
    assert match_records(legacy, authority)[0].payload_change_fields == ()


def test_asset_review_candidates_are_reported_not_approved(tmp_path):
    """A newly-valid asset is a review candidate. Nothing here approves or pins it."""
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha", video="審核中", article="已下架")],
        [merchant_row("Alpha", handle="alpha", video="https://youtu.be/abc", article="已下架")],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    assert proposal.asset_review_candidate_count == 1
    assert proposal.asset_review_candidates[0]["fields"] == ["video"]
    assert proposal.asset_review_candidate_field_count == 1


def test_new_record_assets_are_all_review_candidates(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [
            merchant_row("Alpha", handle="alpha"),
            merchant_row("Fresh", handle="-", article="An article", video="A video"),
        ],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    candidate = next(
        item for item in proposal.asset_review_candidates if item["match_confidence"] == CONFIDENCE_NEW
    )
    assert candidate["fields"] == ["article", "video"]


# --- (G, H) deterministic identifier assignment ------------------------------------------------------


def test_stable_id_assignment_is_reproducible_across_runs(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(6)],
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(6)],
    )
    first = build_crosswalk_proposal(legacy, authority)
    second = build_crosswalk_proposal(legacy, authority)
    assert render_csv(first.registry_rows, REGISTRY_COLUMNS) == render_csv(
        second.registry_rows, REGISTRY_COLUMNS
    )
    assert render_csv(first.crosswalk_rows, CROSSWALK_COLUMNS) == render_csv(
        second.crosswalk_rows, CROSSWALK_COLUMNS
    )


def test_reordering_the_input_records_does_not_move_any_stable_id(tmp_path):
    """Assignment is derived from workbook lineage, not from the order records arrive in."""
    import dataclasses

    legacy, authority = load_pair(
        tmp_path,
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(8)],
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(8)],
    )
    baseline = build_crosswalk_proposal(legacy, authority)

    shuffled_legacy = dataclasses.replace(legacy, records=tuple(reversed(legacy.records)))
    shuffled_authority = dataclasses.replace(authority, records=tuple(reversed(authority.records)))
    assert shuffled_legacy.records != legacy.records, "the reorder must actually change the input"

    reordered = build_crosswalk_proposal(shuffled_legacy, shuffled_authority)
    assert render_csv(reordered.crosswalk_rows, CROSSWALK_COLUMNS) == render_csv(
        baseline.crosswalk_rows, CROSSWALK_COLUMNS
    )


def test_assignment_does_not_depend_on_match_list_order(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(5)],
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(5)],
    )
    matches = match_records(legacy, authority)
    forward = assign_stable_ids(matches, legacy.sha256, authority.sha256)
    backward = assign_stable_ids(list(reversed(matches)), legacy.sha256, authority.sha256)

    def binding(records):
        return {item.stable_record_id: item.match.legacy.source_row for item in records}

    assert binding(forward) == binding(backward)


def test_authority_only_records_are_numbered_after_the_legacy_seed_batch(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(4)],
        [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(4)]
        + [merchant_row("Fresh", handle="fresh")],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    new_row = next(
        row for row in proposal.crosswalk_rows if row["match_confidence"] == CONFIDENCE_NEW
    )
    assert new_row["stable_record_id"] == "MKA-MC-00005"
    assert new_row["legacy_source_row"] == ""


def test_a_brand_rename_cannot_silently_renumber_a_published_proposal(tmp_path):
    """Editing business evidence changes the workbook lineage, and that must fail closed.

    Stable IDs are seeded from a frozen legacy workbook, so any edit to that workbook — including a
    brand rename — changes its sha256 and therefore the whole derivation. The protection against
    renumbering is not that the seed survives such an edit; it is that regeneration is compared
    against the published proposal and refuses to rewrite it. A silent renumber is the failure
    mode, and this asserts it cannot happen.
    """
    original_rows = [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(5)]
    legacy, authority = load_pair(tmp_path, original_rows, list(original_rows))
    published = build_crosswalk_proposal(legacy, authority)
    output = tmp_path / "proposal"
    write_proposal(published, output)
    before = (output / REGISTRY_FILENAME).read_bytes()

    renamed_rows = [
        merchant_row("Zzz Renamed" if index == 0 else f"Brand{index}", handle=f"h{index}")
        for index in range(5)
    ]
    assert renamed_rows != original_rows, "the rename must actually change the workbook"

    renamed_dir = tmp_path / "renamed"
    renamed_dir.mkdir()
    renamed_legacy, renamed_authority = load_pair(renamed_dir, renamed_rows, list(renamed_rows))
    assert renamed_legacy.sha256 != legacy.sha256, "the rename must change the workbook lineage"
    regenerated = build_crosswalk_proposal(renamed_legacy, renamed_authority)

    with pytest.raises(StableRecordCrosswalkError, match="does not reproduce"):
        verify_existing_proposal(regenerated, output)
    assert (output / REGISTRY_FILENAME).read_bytes() == before, "a refused verification must not rewrite"


# --- (T) atomic publication ---------------------------------------------------------------------------


def synthetic_proposal(tmp_path, legacy_rows=None, authority_rows=None):
    legacy_rows = legacy_rows or [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(4)]
    authority_rows = authority_rows or list(legacy_rows)
    legacy, authority = load_pair(tmp_path, legacy_rows, authority_rows)
    return build_crosswalk_proposal(legacy, authority), legacy, authority


def test_write_proposal_publishes_a_complete_directory(tmp_path):
    proposal, _, _ = synthetic_proposal(tmp_path)
    output = tmp_path / "proposal"
    manifest = write_proposal(proposal, output)

    assert sorted(item.name for item in output.iterdir()) == sorted(
        [REGISTRY_FILENAME, CROSSWALK_FILENAME, MANIFEST_FILENAME]
    )
    assert manifest["proposal_state"] == PROPOSAL_STATE_COMPLETE
    assert manifest["authority_status"] == AUTHORITY_STATUS_PROPOSAL_ONLY
    assert manifest["record_identity_scheme_status"] == "not_activated"
    load_proposal(output)


def test_write_proposal_refuses_a_non_empty_output_directory(tmp_path):
    proposal, _, _ = synthetic_proposal(tmp_path)
    output = tmp_path / "proposal"
    write_proposal(proposal, output)
    with pytest.raises(StableRecordCrosswalkError, match="not empty"):
        write_proposal(proposal, output)


def test_write_proposal_leaves_no_staging_directory_behind(tmp_path):
    proposal, _, _ = synthetic_proposal(tmp_path)
    output = tmp_path / "nested" / "proposal"
    write_proposal(proposal, output)
    assert [item.name for item in output.parent.iterdir()] == ["proposal"]


def test_partial_output_is_not_accepted_as_a_proposal(tmp_path):
    """Every incomplete shape a reader could mistake for a proposal must be refused."""
    proposal, _, _ = synthetic_proposal(tmp_path)
    output = tmp_path / "proposal"
    write_proposal(proposal, output)

    manifest_path = output / MANIFEST_FILENAME
    original = manifest_path.read_bytes()
    manifest_path.unlink()
    with pytest.raises(StableRecordCrosswalkError, match="not a published proposal"):
        load_proposal(output)
    manifest_path.write_bytes(original)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["proposal_state"] == PROPOSAL_STATE_COMPLETE
    payload["proposal_state"] = "incomplete"
    assert payload["proposal_state"] != PROPOSAL_STATE_COMPLETE
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StableRecordCrosswalkError, match="proposal_state"):
        load_proposal(output)

    manifest_path.write_bytes(original)
    registry_path = output / REGISTRY_FILENAME
    before = registry_path.read_bytes()
    truncated = b"\n".join(before.split(b"\n")[:2]) + b"\n"
    assert truncated != before, "the truncation must actually shorten the file"
    registry_path.write_bytes(truncated)
    with pytest.raises(StableRecordCrosswalkError, match="does not match the manifest"):
        load_proposal(output)

    registry_path.write_bytes(before)
    registry_path.unlink()
    with pytest.raises(StableRecordCrosswalkError, match="missing"):
        load_proposal(output)


def test_a_proposal_directory_never_claims_successor_authority(tmp_path):
    """The manifest must not be readable as an activated stable_record_v2 authority."""
    proposal, _, _ = synthetic_proposal(tmp_path)
    output = tmp_path / "proposal"
    manifest = write_proposal(proposal, output)
    assert "record_identity_scheme_version" not in manifest
    text = (output / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert '"record_identity_scheme_version"' not in text
    assert manifest["proposed_successor_scheme"] == "stable_record_v2"
    assert manifest["record_identity_scheme_status"] == "not_activated"


# --- hostile validator matrix --------------------------------------------------------------------------
#
# Every mutation below asserts ``before != after`` before asserting the refusal. A mutation that set a
# field to the value it already held would prove nothing while looking green.


@pytest.fixture
def valid_rows(tmp_path):
    legacy_rows = [merchant_row(f"Brand{index}", handle=f"h{index}") for index in range(4)]
    authority_rows = list(legacy_rows) + [merchant_row("Fresh", handle="fresh")]
    legacy, authority = load_pair(tmp_path, legacy_rows, authority_rows)
    proposal = build_crosswalk_proposal(legacy, authority)
    validate_proposal(
        proposal.registry_rows, proposal.crosswalk_rows, proposal.legacy_sha256, proposal.authority_sha256
    )
    return (
        [dict(row) for row in proposal.registry_rows],
        [dict(row) for row in proposal.crosswalk_rows],
        proposal.legacy_sha256,
        proposal.authority_sha256,
    )


def mutate(row, field, value):
    """Apply a mutation that is guaranteed to change the field it targets."""
    before = row[field]
    assert before != value, f"hostile mutation of {field!r} is a no-op ({before!r})"
    row[field] = value
    assert row[field] != before
    return before


def test_duplicate_stable_id_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(registry[1], "stable_record_id", registry[0]["stable_record_id"])
    mutate(crosswalk[1], "stable_record_id", crosswalk[0]["stable_record_id"])
    with pytest.raises(StableRecordCrosswalkError, match="duplicate stable_record_id"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_duplicate_authority_binding_is_rejected(valid_rows):
    """One authority record may not be claimed by two stable identities."""
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[1], "authority_source_row", crosswalk[0]["authority_source_row"])
    with pytest.raises(StableRecordCrosswalkError, match="authority source key"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_duplicate_legacy_binding_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[1], "legacy_source_row", crosswalk[0]["legacy_source_row"])
    with pytest.raises(StableRecordCrosswalkError, match="legacy source key"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_wrong_workbook_sha_on_a_crosswalk_row_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[0], "legacy_workbook_sha256", "0" * 64)
    with pytest.raises(StableRecordCrosswalkError, match="legacy_workbook_sha256"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_wrong_authority_sha_on_a_crosswalk_row_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[0], "authority_workbook_sha256", "1" * 64)
    with pytest.raises(StableRecordCrosswalkError, match="authority_workbook_sha256"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_high_confidence_is_not_auto_approvable_without_review_stamps(valid_rows):
    """HIGH is evidence, not approval. An approved row without a reviewer is refused."""
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    row = next(item for item in crosswalk if item["match_confidence"] == CONFIDENCE_HIGH)
    mutate(row, "review_status", "approved")
    registry_row = next(item for item in registry if item["stable_record_id"] == row["stable_record_id"])
    mutate(registry_row, "review_status", "approved")
    with pytest.raises(StableRecordCrosswalkError, match="missing"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_ambiguous_record_cannot_be_approved_even_with_review_stamps(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    row = crosswalk[0]
    mutate(row, "match_confidence", CONFIDENCE_AMBIGUOUS)
    mutate(row, "authority_source_row", "")
    mutate(row, "authority_source_sheet", "")
    mutate(row, "authority_workbook_sha256", "")
    mutate(row, "review_status", "approved")
    mutate(row, "reviewed_by", "reviewer@example.com")
    mutate(row, "reviewed_at", "2026-08-22T00:00:00+00:00")
    registry_row = next(item for item in registry if item["stable_record_id"] == row["stable_record_id"])
    mutate(registry_row, "review_status", "approved")
    with pytest.raises(StableRecordCrosswalkError, match="cannot be approved"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_pending_row_carrying_review_stamps_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    assert crosswalk[0]["review_status"] == "pending"
    mutate(crosswalk[0], "reviewed_by", "reviewer@example.com")
    with pytest.raises(StableRecordCrosswalkError, match="review stamps"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_new_row_carrying_a_legacy_binding_is_rejected(valid_rows):
    """A NEW record must never inherit an existing record's decisions through a legacy binding."""
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    row = next(item for item in crosswalk if item["match_confidence"] == CONFIDENCE_NEW)
    mutate(row, "legacy_source_sheet", SHEET_MERCHANT_CASES)
    mutate(row, "legacy_source_row", "8")
    mutate(row, "legacy_workbook_sha256", legacy_sha)
    with pytest.raises(StableRecordCrosswalkError, match="NEW record carries legacy binding"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_positional_match_evidence_is_rejected(valid_rows):
    """Row position may never be cited as match evidence, even if a future change tried to."""
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[0], "match_evidence", "row_shift=+1")
    with pytest.raises(StableRecordCrosswalkError, match="not a recognised evidence token"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_bound_row_without_evidence_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[0], "match_evidence", "")
    with pytest.raises(StableRecordCrosswalkError, match="match_evidence is empty"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_malformed_stable_id_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(registry[0], "stable_record_id", "MKA-MC-1")
    mutate(crosswalk[0], "stable_record_id", "MKA-MC-1")
    with pytest.raises(StableRecordCrosswalkError, match="does not match"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_unknown_confidence_value_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[0], "match_confidence", "PROBABLY")
    with pytest.raises(StableRecordCrosswalkError, match="match_confidence"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_unknown_review_status_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(crosswalk[0], "review_status", "signed-off")
    with pytest.raises(StableRecordCrosswalkError, match="review_status"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_registry_and_crosswalk_must_describe_the_same_identities(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    removed = registry.pop()
    assert removed["stable_record_id"] not in {row["stable_record_id"] for row in registry}
    with pytest.raises(StableRecordCrosswalkError, match="same identities"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_record_type_disagreement_between_registry_and_crosswalk_is_rejected(valid_rows):
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(registry[0], "record_type", "public_metric")
    with pytest.raises(StableRecordCrosswalkError, match="record_type disagrees"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_registry_claiming_issued_status_is_rejected(valid_rows):
    """A proposal directory may not present itself as an issued registry."""
    registry, crosswalk, legacy_sha, authority_sha = valid_rows
    mutate(registry[0], "issuance_status", "issued")
    with pytest.raises(StableRecordCrosswalkError, match="issuance_status"):
        validate_proposal(registry, crosswalk, legacy_sha, authority_sha)


def test_expectation_mismatch_is_rejected(tmp_path):
    """The M1 count expectation must actually bite when the reconciliation differs."""
    import dataclasses

    proposal, _, _ = synthetic_proposal(tmp_path)
    expectation = dataclasses.replace(
        M1_EXPECTATION,
        migration_version=proposal.migration_version,
        legacy_workbook_sha256=proposal.legacy_sha256,
        authority_workbook_sha256=proposal.authority_sha256,
        legacy_record_count=4,
        authority_record_count=4,
        matched_legacy_records=4,
        unchanged_rows=4,
        shifted_rows=0,
        confidence_counts={
            CONFIDENCE_HIGH: 4,
            CONFIDENCE_MEDIUM: 0,
            "LOW": 0,
            CONFIDENCE_AMBIGUOUS: 0,
            CONFIDENCE_UNMATCHED: 0,
            CONFIDENCE_NEW: 0,
        },
    )
    validate_expectation(proposal, expectation)

    drifted = dataclasses.replace(expectation, unchanged_rows=3)
    assert drifted.unchanged_rows != expectation.unchanged_rows
    with pytest.raises(StableRecordCrosswalkError, match="unchanged_rows"):
        validate_expectation(proposal, drifted)


# --- (S) input safety ------------------------------------------------------------------------------------


def test_missing_workbook_is_refused(tmp_path):
    with pytest.raises(StableRecordCrosswalkError, match="does not exist"):
        load_merchant_evidence(tmp_path / "absent.xlsx", "0" * 64)


def test_symlinked_workbook_is_refused(tmp_path):
    real = tmp_path / "real.xlsx"
    sha = write_merchant_workbook(real, [merchant_row("Alpha", handle="alpha")])
    link = tmp_path / "link.xlsx"
    link.symlink_to(real)
    with pytest.raises(StableRecordCrosswalkError, match="symlink"):
        load_merchant_evidence(link, sha)


def test_wrong_workbook_sha_is_refused(tmp_path):
    path = tmp_path / "book.xlsx"
    write_merchant_workbook(path, [merchant_row("Alpha", handle="alpha")])
    with pytest.raises(StableRecordCrosswalkError, match="workbook lineage mismatch"):
        load_merchant_evidence(path, "a" * 64)


def test_malformed_expected_sha_is_refused(tmp_path):
    path = tmp_path / "book.xlsx"
    write_merchant_workbook(path, [merchant_row("Alpha", handle="alpha")])
    with pytest.raises(StableRecordCrosswalkError, match="64 hex characters"):
        load_merchant_evidence(path, "not-a-hash")


def test_unreadable_xlsx_is_refused(tmp_path):
    path = tmp_path / "book.xlsx"
    path.write_bytes(b"this is not a zip archive")
    with pytest.raises(StableRecordCrosswalkError, match="could not be read as an xlsx"):
        load_merchant_evidence(path, hash_file(path))


def test_missing_merchant_sheet_is_refused(tmp_path):
    path = tmp_path / "book.xlsx"
    _write_xlsx(path, {"Some Other Sheet": [[], [], [], [], [], MERCHANT_HEADERS]})
    with pytest.raises(StableRecordCrosswalkError, match="has no sheet named"):
        load_merchant_evidence(path, hash_file(path))


def test_renamed_merchant_header_is_refused(tmp_path):
    path = tmp_path / "book.xlsx"
    headers = list(MERCHANT_HEADERS)
    before = headers[3]
    headers[3] = "Merchant Handle"
    assert headers[3] != before
    sha = write_merchant_workbook(path, [merchant_row("Alpha", handle="alpha")], headers=headers)
    with pytest.raises(StableRecordCrosswalkError, match="does not match the expected merchant header"):
        load_merchant_evidence(path, sha)


def test_empty_merchant_sheet_is_refused(tmp_path):
    path = tmp_path / "book.xlsx"
    sha = write_merchant_workbook(path, [])
    with pytest.raises(StableRecordCrosswalkError, match="no merchant records"):
        load_merchant_evidence(path, sha)


# --- (23) issued identifiers never move ------------------------------------------------------------------


def test_verify_existing_accepts_an_identical_regeneration(tmp_path):
    proposal, _, _ = synthetic_proposal(tmp_path)
    output = tmp_path / "proposal"
    write_proposal(proposal, output)
    result = verify_existing_proposal(proposal, output)
    assert result["verified"] is True
    assert result["content_digest_matches"] is True


def test_verify_existing_refuses_a_regeneration_that_moves_a_stable_id(tmp_path):
    """Once an identifier is published it may only ever be reproduced, never recomputed elsewhere."""
    proposal, _, _ = synthetic_proposal(tmp_path)
    output = tmp_path / "proposal"
    write_proposal(proposal, output)

    registry_path = output / REGISTRY_FILENAME
    rows = list(csv.DictReader(registry_path.open(encoding="utf-8")))
    before = rows[0]["seed_derivation_digest"]
    rows[0]["seed_derivation_digest"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "a-different-record"))
    assert rows[0]["seed_derivation_digest"] != before
    registry_path.write_bytes(render_csv(rows, REGISTRY_COLUMNS))

    manifest_path = output / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["registry_sha256"] = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StableRecordCrosswalkError, match="seed derivation moved"):
        verify_existing_proposal(proposal, output)


# --- (21) manifest determinism ------------------------------------------------------------------------------


def test_content_digest_excludes_created_at_while_manifest_hash_covers_it(tmp_path):
    proposal, _, _ = synthetic_proposal(tmp_path)
    registry_bytes = render_csv(proposal.registry_rows, REGISTRY_COLUMNS)
    crosswalk_bytes = render_csv(proposal.crosswalk_rows, CROSSWALK_COLUMNS)

    early = build_manifest(proposal, registry_bytes, crosswalk_bytes, created_at="2026-01-01T00:00:00+00:00")
    late = build_manifest(proposal, registry_bytes, crosswalk_bytes, created_at="2026-12-31T23:59:59+00:00")

    assert early["created_at"] != late["created_at"]
    assert early["content_digest"] == late["content_digest"]
    assert early["manifest_hash"] != late["manifest_hash"]


def test_manifest_carries_no_merchant_roster(tmp_path):
    """The manifest reports counts and identifiers, never the merchant list."""
    proposal, _, _ = synthetic_proposal(
        tmp_path,
        legacy_rows=[merchant_row("SecretBrandName", handle="secret")],
        authority_rows=[merchant_row("SecretBrandName", handle="secret")],
    )
    manifest = build_manifest(
        proposal,
        render_csv(proposal.registry_rows, REGISTRY_COLUMNS),
        render_csv(proposal.crosswalk_rows, CROSSWALK_COLUMNS),
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert "SecretBrandName" not in json.dumps(manifest, ensure_ascii=False)


# --- (U) decision subject formats --------------------------------------------------------------------------


def write_decision_store(path: Path, subject_ids) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE decision_events (subject_id TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO decision_events (subject_id) VALUES (?)",
            [(value,) for value in subject_ids],
        )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.mark.parametrize(
    "subject_id,expected_format,expected_row,expected_qualifier",
    [
        (f"{SHEET_MERCHANT_CASES}:r7", "row", 7, None),
        (f"{SHEET_MERCHANT_CASES}:r12:video", "row_field", 12, "video"),
        (f"{SHEET_MERCHANT_CASES}:r32|slp", "row_alias", 32, "slp"),
    ],
)
def test_known_row_bound_subject_formats_are_classified(
    subject_id, expected_format, expected_row, expected_qualifier
):
    assert classify_decision_subject(subject_id) == (
        expected_format,
        expected_row,
        expected_qualifier,
    )


@pytest.mark.parametrize(
    "subject_id",
    [
        f"{SHEET_MERCHANT_CASES}:7",
        f"{SHEET_MERCHANT_CASES}#r7",
        f"{SHEET_MERCHANT_CASES}:row7",
        f"{SHEET_MERCHANT_CASES}:rABC",
    ],
)
def test_unrecognised_row_bound_subject_format_is_surfaced(subject_id):
    """An unknown row-bound shape is review history this migration has not accounted for."""
    assert classify_decision_subject(subject_id)[0] == UNKNOWN_SUBJECT_FORMAT


def test_unknown_subject_format_fails_migration_completeness(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha")],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    store = write_decision_store(
        tmp_path / "decisions.sqlite",
        [f"{SHEET_MERCHANT_CASES}:r7", f"{SHEET_MERCHANT_CASES}:7"],
    )
    impact = analyze_decision_impact(store, proposal.crosswalk_rows)
    assert impact["unknown_subject_formats"] == [f"{SHEET_MERCHANT_CASES}:7"]
    assert impact["complete"] is False


def test_subject_pointing_at_an_unreconciled_row_fails_completeness(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha")],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    store = write_decision_store(tmp_path / "decisions.sqlite", [f"{SHEET_MERCHANT_CASES}:r999"])
    impact = analyze_decision_impact(store, proposal.crosswalk_rows)
    assert impact["unresolved_row_subjects"] == [f"{SHEET_MERCHANT_CASES}:r999"]
    assert impact["complete"] is False


def test_alias_subjects_are_reported_and_flagged_as_a_separate_decision(tmp_path):
    """Alias parentage is reported against the proposed identifier and rebound by nobody here."""
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha")],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    store = write_decision_store(tmp_path / "decisions.sqlite", [f"{SHEET_MERCHANT_CASES}:r7|slp"])
    impact = analyze_decision_impact(store, proposal.crosswalk_rows)
    assert impact["alias_bindings"] == [
        {
            "legacy_source_row": 7,
            "alias": "slp",
            "proposed_stable_record_id": "MKA-MC-00001",
            "match_confidence": CONFIDENCE_HIGH,
        }
    ]
    assert impact["alias_rebinding_requires_separate_decision"] is True


def test_decision_store_is_opened_read_only(tmp_path):
    """The impact analysis must not be able to write, whatever it is asked to do."""
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha")],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    store = write_decision_store(tmp_path / "decisions.sqlite", [f"{SHEET_MERCHANT_CASES}:r7"])
    before = hash_file(store)
    analyze_decision_impact(store, proposal.crosswalk_rows)
    assert hash_file(store) == before


# --- (V) record grain and event grain stay separate ------------------------------------------------------------


def test_record_confidence_counts_are_not_event_confidence_counts(tmp_path):
    """One record carrying several decision events must not inflate the record-grain counts.

    Two records, one HIGH and one MEDIUM, carrying five events between them: the record grain is
    1/1 and the event grain is 3/2. Reporting either set as the other is the confusion this
    separation exists to prevent.
    """
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha"), merchant_row("Beta", handle="-")],
        [merchant_row("Alpha", handle="alpha"), merchant_row("Beta", handle="-")],
    )
    proposal = build_crosswalk_proposal(legacy, authority)
    assert proposal.confidence_counts[CONFIDENCE_HIGH] == 1
    assert proposal.confidence_counts[CONFIDENCE_MEDIUM] == 1

    store = write_decision_store(
        tmp_path / "decisions.sqlite",
        [
            f"{SHEET_MERCHANT_CASES}:r7",
            f"{SHEET_MERCHANT_CASES}:r7:article",
            f"{SHEET_MERCHANT_CASES}:r7:video",
            f"{SHEET_MERCHANT_CASES}:r8",
            f"{SHEET_MERCHANT_CASES}:r8:article",
        ],
    )
    impact = analyze_decision_impact(store, proposal.crosswalk_rows)
    assert impact["grain"] == "decision_event"
    assert impact["row_bound_event_count"] == 5
    assert impact["event_confidence_counts"][CONFIDENCE_HIGH] == 3
    assert impact["event_confidence_counts"][CONFIDENCE_MEDIUM] == 2
    assert impact["event_confidence_counts"][CONFIDENCE_HIGH] != proposal.confidence_counts[CONFIDENCE_HIGH]


# --- (A-F) the frozen M1 migration -------------------------------------------------------------------------------
#
# Counts, hashes and field names only. This file is tracked; the merchant roster is not.


@pytest.fixture(scope="module")
def m1_proposal(tmp_path_factory):
    output = tmp_path_factory.mktemp("m1") / "proposal"
    summary = generate_stable_record_crosswalk_proposal(
        legacy_workbook=LEGACY_WORKBOOK,
        authority_workbook=AUTHORITY_WORKBOOK,
        output_dir=output,
        decision_store=DECISION_STORE if DECISION_STORE.is_file() else None,
        created_at="2026-08-22T00:00:00+00:00",
    )
    manifest, registry, crosswalk = load_proposal(output)
    return summary, manifest, registry, crosswalk, output


@requires_migration_workbooks
def test_m1_workbook_lineage_is_the_frozen_one():
    """(A) The migration is adjudicated against exactly these two workbooks."""
    assert hash_file(LEGACY_WORKBOOK) == M1_EXPECTATION.legacy_workbook_sha256
    assert hash_file(AUTHORITY_WORKBOOK) == M1_EXPECTATION.authority_workbook_sha256


@requires_migration_workbooks
def test_m1_reconciles_120_legacy_records_onto_121_authority_records(m1_proposal):
    """(B) 120 -> 121, every legacy record accounted for, one authority-only record."""
    summary = m1_proposal[0]
    grain = summary["record_grain"]
    assert grain["grain"] == "merchant_case_record"
    assert grain["legacy_records"] == 120
    assert grain["authority_records"] == 121
    assert grain["reconciliation"]["matched_legacy_records"] == 120
    assert grain["reconciliation"]["legacy_unmatched_records"] == 0


@requires_migration_workbooks
def test_m1_has_119_shifted_rows_and_1_unchanged_row(m1_proposal):
    """(C) Row shift is a diagnostic; these are its counts, not its authority."""
    reconciliation = m1_proposal[0]["record_grain"]["reconciliation"]
    assert reconciliation["shifted_rows"] == 119
    assert reconciliation["unchanged_rows"] == 1


@requires_migration_workbooks
def test_m1_record_grain_confidence_is_105_high_and_15_medium(m1_proposal):
    """(D, F) Record grain. Not 110/29 — that is the event grain."""
    counts = m1_proposal[0]["record_grain"]["confidence_counts"]
    assert counts[CONFIDENCE_HIGH] == 105
    assert counts[CONFIDENCE_MEDIUM] == 15
    assert counts["LOW"] == 0
    assert counts[CONFIDENCE_AMBIGUOUS] == 0
    assert counts[CONFIDENCE_UNMATCHED] == 0


@requires_migration_workbooks
def test_m1_has_exactly_one_authority_only_record_numbered_last(m1_proposal):
    """(E) The one new record takes the next issued identifier and inherits no legacy identity."""
    _, manifest, _, crosswalk, _ = m1_proposal
    new_rows = [row for row in crosswalk if row["match_confidence"] == CONFIDENCE_NEW]
    assert len(new_rows) == 1
    assert new_rows[0]["stable_record_id"] == "MKA-MC-00121"
    assert new_rows[0]["legacy_source_sheet"] == ""
    assert new_rows[0]["legacy_source_row"] == ""
    assert new_rows[0]["legacy_workbook_sha256"] == ""
    assert manifest["new_record_count"] == 1


@requires_migration_workbooks
def test_m1_issues_121_contiguous_identifiers(m1_proposal):
    _, manifest, registry, _, _ = m1_proposal
    assert manifest["stable_id_count"] == 121
    assert manifest["stable_id_min"] == "MKA-MC-00001"
    assert manifest["stable_id_max"] == "MKA-MC-00121"
    ids = sorted(row["stable_record_id"] for row in registry)
    assert ids == [format_stable_id(number) for number in range(1, 122)]
    assert len(set(ids)) == 121


@requires_migration_workbooks
def test_m1_leaves_every_row_pending(m1_proposal):
    """(O, P) M1 is a proposal. No row is approved, including every HIGH row."""
    _, _, registry, crosswalk, _ = m1_proposal
    assert {row["review_status"] for row in registry} == {"pending"}
    assert {row["review_status"] for row in crosswalk} == {"pending"}
    assert not any(row["reviewed_by"] or row["reviewed_at"] for row in crosswalk)
    assert {row["issuance_status"] for row in registry} == {"proposed"}
    assert {row["lifecycle_state"] for row in registry} == {"active"}
    assert {row["record_type"] for row in registry} == {"merchant_case"}
    assert {row["match_evidence_normalization"] for row in crosswalk} == {NORMALIZATION_VERSION}


@requires_migration_workbooks
def test_m1_detects_exactly_one_payload_change_and_it_is_a_video(m1_proposal):
    """(N) One matched record changed a non-identity field between the workbooks: its video.

    Asserted by count and field name rather than by merchant, so this tracked test states the fact
    without restating the roster.
    """
    summary, _, _, crosswalk, _ = m1_proposal
    changed = [row for row in crosswalk if row["payload_change_fields"]]
    assert len(changed) == 1
    assert changed[0]["payload_change_fields"] == "video"
    assert changed[0]["match_confidence"] == CONFIDENCE_HIGH, "a payload change must not lower confidence"
    assert summary["payload_changed_record_count"] == 1


@requires_migration_workbooks
def test_m1_reports_asset_review_candidates_without_approving_them(m1_proposal):
    summary, manifest, _, _, _ = m1_proposal
    assert summary["asset_review_candidate_count"] == 2
    assert summary["asset_review_candidate_field_count"] == 3
    assert sorted(
        field for item in manifest["asset_review_candidates"] for field in item["fields"]
    ) == ["article", "video", "video"]


@requires_migration_workbooks
def test_m1_event_grain_is_139_events_and_differs_from_record_grain(m1_proposal):
    """(V) 110/29 belongs to the event grain and never to the record confidence counts."""
    summary = m1_proposal[0]
    if "event_grain" not in summary:
        pytest.skip("the production decision store is not present on this machine")
    event = summary["event_grain"]
    assert event["row_bound_event_count"] == 139
    assert event["event_confidence_counts"][CONFIDENCE_HIGH] == 110
    assert event["event_confidence_counts"][CONFIDENCE_MEDIUM] == 29
    assert event["shifted_events"] == 135
    assert event["unchanged_events"] == 4
    assert event["unknown_subject_formats"] == []
    assert event["complete"] is True

    record = summary["record_grain"]["confidence_counts"]
    assert (record[CONFIDENCE_HIGH], record[CONFIDENCE_MEDIUM]) == (105, 15)
    assert (record[CONFIDENCE_HIGH], record[CONFIDENCE_MEDIUM]) != (110, 29)


@requires_migration_workbooks
def test_m1_declares_itself_a_proposal_and_not_an_authority(m1_proposal):
    summary, manifest, _, _, _ = m1_proposal
    assert summary["authority_status"] == AUTHORITY_STATUS_PROPOSAL_ONLY
    assert manifest["authority_status"] == AUTHORITY_STATUS_PROPOSAL_ONLY
    assert manifest["record_identity_scheme_status"] == "not_activated"
    assert "record_identity_scheme_version" not in manifest


@requires_migration_workbooks
def test_m1_regenerates_to_identical_bytes(m1_proposal, tmp_path):
    """(21) Same inputs, a fresh empty output directory, identical proposal bytes."""
    _, manifest, _, _, published = m1_proposal
    rerun = tmp_path / "rerun"
    generate_stable_record_crosswalk_proposal(
        legacy_workbook=LEGACY_WORKBOOK,
        authority_workbook=AUTHORITY_WORKBOOK,
        output_dir=rerun,
        created_at="2999-01-01T00:00:00+00:00",
    )
    for name in (REGISTRY_FILENAME, CROSSWALK_FILENAME):
        assert (rerun / name).read_bytes() == (published / name).read_bytes()

    rerun_manifest = json.loads((rerun / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert rerun_manifest["created_at"] != manifest["created_at"]
    assert rerun_manifest["content_digest"] == manifest["content_digest"]


@requires_migration_workbooks
def test_m1_verify_existing_reproduces_the_published_proposal(m1_proposal):
    """(23) Regeneration against the published proposal must not move a single identifier."""
    _, _, _, _, published = m1_proposal
    summary = generate_stable_record_crosswalk_proposal(
        legacy_workbook=LEGACY_WORKBOOK,
        authority_workbook=AUTHORITY_WORKBOOK,
        output_dir=published,
        verify_existing=True,
    )
    assert summary["verification"]["verified"] is True
    assert summary["verification"]["content_digest_matches"] is True
    assert summary["verification"]["stable_id_count"] == 121


def test_unbound_authority_row_after_an_ambiguity_is_distinguished_from_a_new_merchant(tmp_path):
    """A contested row must not read as a fresh merchant.

    When a handle conflict refuses the binding, the authority row is left unbound and still takes a
    proposed identifier — but its diagnostic says it was contested, so a reviewer resolves the
    ambiguity rather than minting a second identity for a merchant that already has one.
    """
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", handle="alpha")],
        [merchant_row("Alpha", handle="alpha-renamed"), merchant_row("Genuinely New", handle="new")],
    )
    matches = match_records(legacy, authority)
    unbound = {
        match.authority.brand: match.diagnostics
        for match in matches
        if match.confidence == CONFIDENCE_NEW
    }
    assert unbound["Alpha"] == ("unbound_after_contested_legacy_candidate",)
    assert unbound["Genuinely New"] == ("authority_only_record",)


def test_unmatched_legacy_record_marks_its_key_contested(tmp_path):
    legacy, authority = load_pair(
        tmp_path,
        [merchant_row("Alpha", year="2025", handle="alpha")],
        [merchant_row("Alpha", year="2026", handle="alpha")],
    )
    matches = match_records(legacy, authority)
    assert [match.confidence for match in matches] == [CONFIDENCE_UNMATCHED, CONFIDENCE_NEW]
    # Different years mean different discovery keys, so the authority row is genuinely unrelated.
    assert matches[1].diagnostics == ("authority_only_record",)
