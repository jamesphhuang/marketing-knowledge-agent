"""WP0.4a — Row-V1 workbook lineage guard.

Merchant-case review decisions are still keyed by Excel row coordinate. Until
``stable_record_id`` exists, applying them against a workbook whose rows have shifted silently
re-points every decision at a different merchant. These tests pin the two halves of the remedy:
read-only analysis of any workbook stays available, and every path that binds or mutates on row
identity fails closed — before it writes.
"""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from marketing_knowledge_agent.apply_review_decisions import apply_review_decisions
from marketing_knowledge_agent.excel_preview import generate_excel_preview
from marketing_knowledge_agent.obsidian_sync import create_sync_plan
from marketing_knowledge_agent.record_identity_lineage import (
    APPLY_LINEAGE_FILENAME,
    EVIDENCE_PINNED_LEGACY_APPLY_SURFACE,
    EVIDENCE_PINNED_PREVIEW_PAYLOAD,
    LINEAGE_MATCH,
    LINEAGE_MISMATCH,
    LINEAGE_UNBOUND,
    LINEAGE_UNSUPPORTED_SCHEME,
    PREVIEW_LINEAGE_FILENAME,
    RECORD_IDENTITY_SCHEME_VERSION,
    RowV1LineageContractError,
    RowV1LineageError,
    apply_row_identity_surface_digest,
    apply_row_identity_surface_entries,
    load_lineage_contract,
    resolve_apply_lineage,
    resolve_preview_lineage,
)
from marketing_knowledge_agent.review_decision_validation import validate_review_decisions
from test_apply_review_decisions import (
    _all_review_rows,
    _write_apply_preview_fixture,
    _write_decisions,
)
from test_excel_preview import _preview_workbook_sheets, _write_xlsx


REPO_ROOT = Path(__file__).resolve().parents[1]
OLD_LINEAGE_WORKBOOK = (
    REPO_ROOT
    / "reports/excel_preview/MKT 內容產出資料庫_店家_夥伴案例_對外數據-20260708.xlsx"
)
AUTHORITY_WORKBOOK_20260821 = Path(
    "/Volumes/T7/MKA Authority/Search Taxonomy/2026-08-21/"
    "MKA_Search_Taxonomy_Authority_2026-08-21.xlsx"
)
AUTHORITY_WORKBOOK_20260821_SHA256 = (
    "7e6ecffc2d4ad9b931c8abc2d75345305c0a93026ecb1cb10841aa5e8c6597a3"
)
LIVE_PREVIEW_DIR = REPO_ROOT / "reports/excel_preview"
LIVE_APPLY_DIR = REPO_ROOT / "reports/excel_preview/apply_preview"

# Every artifact a mutation would touch. §9 requires all of them to be untouched on refusal.
PROTECTED_ARTIFACTS = (
    REPO_ROOT / "data/governance/governance_decisions.sqlite",
    REPO_ROOT / "obsidian_vault/MKA",
    REPO_ROOT / ".mka/content_index.sqlite",
    REPO_ROOT / ".mka/search_alias_projection.json",
    REPO_ROOT
    / "src/marketing_knowledge_agent/authority/approved_asset_urls/manifest.json",
)


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"local-only lineage input is absent: {path}")
    return path


def _pinned_workbook() -> dict:
    return dict(load_lineage_contract()["lineage_workbook"])


def _write_declaration(preview_dir: Path, workbook: dict, scheme=RECORD_IDENTITY_SCHEME_VERSION):
    payload = {"record_identity_scheme_version": scheme, "workbook": workbook}
    (preview_dir / PREVIEW_LINEAGE_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _reviewed_decisions(path: Path) -> Path:
    """Decisions that otherwise apply cleanly, so lineage is the only thing that can block."""
    _write_decisions(path, _all_review_rows())
    return path


def _snapshot(paths):
    state = {}
    for path in paths:
        if path.is_file():
            state[path] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            entries = sorted(
                (item.relative_to(path).as_posix(), item.stat().st_size)
                for item in path.rglob("*")
                if item.is_file()
            )
            state[path] = ("dir", hashlib.sha256(repr(entries).encode("utf-8")).hexdigest())
    return state


# --- TEST 1: the pinned lineage passes ------------------------------------------------------


def test_old_lineage_workbook_and_current_row_decisions_pass(tmp_path):
    workbook = _require(OLD_LINEAGE_WORKBOOK)
    preview_dir = tmp_path / "preview"
    generate_excel_preview(workbook, preview_dir)

    status = resolve_preview_lineage(preview_dir)
    assert status["state"] == LINEAGE_MATCH
    assert status["actual_workbook_sha256"] == _pinned_workbook()["sha256"]


def test_live_production_preview_and_apply_dirs_are_lineage_bound():
    preview_status = resolve_preview_lineage(_require(LIVE_PREVIEW_DIR))
    assert preview_status["state"] == LINEAGE_MATCH
    # The production preview predates this guard, so it is proven by the pinned payload rather
    # than by a declaration it could not have written.
    assert preview_status["evidence"] == EVIDENCE_PINNED_PREVIEW_PAYLOAD

    apply_status = resolve_apply_lineage(_require(LIVE_APPLY_DIR))
    assert apply_status["state"] == LINEAGE_MATCH
    assert apply_status["evidence"] == EVIDENCE_PINNED_LEGACY_APPLY_SURFACE


# --- TEST 2: the 2026-08-21 authority workbook is a mismatch --------------------------------


def test_authority_workbook_20260821_is_a_lineage_mismatch(tmp_path):
    workbook = _require(AUTHORITY_WORKBOOK_20260821)
    preview_dir = tmp_path / "preview"
    summary = generate_excel_preview(workbook, preview_dir)
    # The workbook that inserted 有風造識 at r8 and pushed 三風製麵 to r9.
    assert summary["sheet_counts"]["商家夥伴案例資料庫"] == 121

    status = resolve_preview_lineage(preview_dir)
    assert status["state"] == LINEAGE_MISMATCH
    assert status["actual_workbook_sha256"] == AUTHORITY_WORKBOOK_20260821_SHA256
    assert status["expected_workbook"]["sha256"] == _pinned_workbook()["sha256"]


# --- TEST 3: read-only analysis is never blocked ---------------------------------------------


def test_read_only_preview_and_validation_survive_a_lineage_mismatch(tmp_path):
    workbook = tmp_path / "other.xlsx"
    _write_xlsx(workbook, _preview_workbook_sheets())
    preview_dir = tmp_path / "preview"

    # excel-preview itself must never fail closed: analysing a new workbook is how a rebinding
    # gets designed in the first place.
    summary = generate_excel_preview(workbook, preview_dir)
    assert summary["sheet_counts"]["商家夥伴案例資料庫"] >= 1
    assert (preview_dir / "merchant_cases.json").is_file()
    assert resolve_preview_lineage(preview_dir)["state"] == LINEAGE_MISMATCH

    decisions_preview = _write_apply_preview_fixture(tmp_path / "decisions_preview")
    _write_declaration(decisions_preview, {**_pinned_workbook(), "sha256": "f" * 64})
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")

    validation_summary = validate_review_decisions(
        decisions, tmp_path / "validation.md", preview_dir=decisions_preview
    )
    assert validation_summary["error_count"] == 0
    assert validation_summary["row_v1_lineage"]["state"] == LINEAGE_MISMATCH

    report = (tmp_path / "validation.md").read_text(encoding="utf-8")
    assert "## Record Identity Lineage" in report
    assert "LINEAGE_MISMATCH" in report


# --- TEST 4: apply is blocked, and blocked before any write ---------------------------------


def test_apply_is_blocked_before_any_write_on_lineage_mismatch(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    _write_declaration(preview_dir, {**_pinned_workbook(), "sha256": AUTHORITY_WORKBOOK_20260821_SHA256})
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")
    output_dir = tmp_path / "apply_preview"

    before = _snapshot(PROTECTED_ARTIFACTS)
    with pytest.raises(RowV1LineageError) as excinfo:
        apply_review_decisions(decisions, preview_dir, output_dir)

    message = str(excinfo.value)
    assert LINEAGE_MISMATCH in message
    assert _pinned_workbook()["sha256"] in message
    assert AUTHORITY_WORKBOOK_20260821_SHA256 in message
    assert RECORD_IDENTITY_SCHEME_VERSION in message
    assert "stable_record_id" in message

    assert not output_dir.exists()
    assert _snapshot(PROTECTED_ARTIFACTS) == before


def test_apply_refusal_leaves_an_existing_output_directory_untouched(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")
    output_dir = tmp_path / "apply_preview"

    apply_review_decisions(decisions, preview_dir, output_dir)
    good = _snapshot([output_dir])
    assert (output_dir / APPLY_LINEAGE_FILENAME).is_file()

    _write_declaration(preview_dir, {**_pinned_workbook(), "sha256": "0" * 64})
    with pytest.raises(RowV1LineageError):
        apply_review_decisions(decisions, preview_dir, output_dir)
    assert _snapshot([output_dir]) == good


def test_sync_is_blocked_before_any_vault_write_on_lineage_mismatch(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")
    apply_dir = tmp_path / "apply_preview"
    apply_review_decisions(decisions, preview_dir, apply_dir)

    binding = json.loads((apply_dir / APPLY_LINEAGE_FILENAME).read_text(encoding="utf-8"))
    binding["workbook"]["sha256"] = AUTHORITY_WORKBOOK_20260821_SHA256
    (apply_dir / APPLY_LINEAGE_FILENAME).write_text(
        json.dumps(binding, ensure_ascii=False), encoding="utf-8"
    )

    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "MKA").mkdir()
    before = _snapshot([vault, *PROTECTED_ARTIFACTS])

    with pytest.raises(RowV1LineageError) as excinfo:
        create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")
    assert "sync-obsidian plan" in str(excinfo.value)
    assert not (tmp_path / "sync").exists()
    assert _snapshot([vault, *PROTECTED_ARTIFACTS]) == before


def test_apply_stamps_a_binding_the_sync_path_verifies(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")
    apply_dir = tmp_path / "apply_preview"
    apply_review_decisions(decisions, preview_dir, apply_dir)

    binding = json.loads((apply_dir / APPLY_LINEAGE_FILENAME).read_text(encoding="utf-8"))
    assert binding["record_identity_scheme_version"] == RECORD_IDENTITY_SCHEME_VERSION
    assert binding["workbook"]["sha256"] == _pinned_workbook()["sha256"]
    assert binding["row_identity_surface_digest"] == apply_row_identity_surface_digest(
        apply_row_identity_surface_entries(apply_dir)
    )
    assert resolve_apply_lineage(apply_dir)["state"] == LINEAGE_MATCH


def test_a_binding_copied_from_another_apply_preview_fails_closed(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")
    source_dir = tmp_path / "apply_a"
    apply_review_decisions(decisions, preview_dir, source_dir)

    other_dir = tmp_path / "apply_b"
    shutil.copytree(source_dir, other_dir)
    stray = other_dir / "approved_vault_preview" / "merchant_cases"
    stray.mkdir(parents=True, exist_ok=True)
    next(iter(sorted(stray.glob("*.md")))).unlink()

    status = resolve_apply_lineage(other_dir)
    assert status["state"] == LINEAGE_MISMATCH
    assert "surface digest" in status["detail"]


# --- TEST 5 / TEST 6: shape is not identity --------------------------------------------------


def test_a_different_workbook_with_the_same_row_count_is_blocked(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    # Identical merchant record count and row range; only the bytes differ.
    _write_declaration(preview_dir, {**_pinned_workbook(), "sha256": "a" * 64})
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")

    status = resolve_preview_lineage(preview_dir)
    assert status["state"] == LINEAGE_MISMATCH
    assert status["detail"] == "workbook sha256 differs; merchant sheet shape is unchanged"
    with pytest.raises(RowV1LineageError):
        apply_review_decisions(decisions, preview_dir, tmp_path / "out")


def test_a_modified_workbook_keeping_filename_and_sheets_is_blocked(tmp_path):
    source = _require(OLD_LINEAGE_WORKBOOK)
    modified = tmp_path / source.name
    _rewrite_first_shared_string(source, modified)
    assert modified.name == source.name
    assert hashlib.sha256(modified.read_bytes()).hexdigest() != _pinned_workbook()["sha256"]

    preview_dir = tmp_path / "preview"
    generate_excel_preview(modified, preview_dir)
    declared = json.loads((preview_dir / PREVIEW_LINEAGE_FILENAME).read_text(encoding="utf-8"))
    # Same file name, same sheet, same header row: only the content moved.
    assert declared["workbook"]["filename"] == _pinned_workbook()["filename"]
    assert declared["workbook"]["merchant_sheet_name"] == _pinned_workbook()["merchant_sheet_name"]
    assert (
        declared["workbook"]["merchant_header_fingerprint"]
        == _pinned_workbook()["merchant_header_fingerprint"]
    )
    assert resolve_preview_lineage(preview_dir)["state"] == LINEAGE_MISMATCH


# --- TEST 7: an unprovable lineage is not a passing lineage ----------------------------------


def test_missing_lineage_binding_fails_closed_for_the_mutation_path(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    (preview_dir / PREVIEW_LINEAGE_FILENAME).unlink()
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")

    status = resolve_preview_lineage(preview_dir)
    assert status["state"] == LINEAGE_UNBOUND
    with pytest.raises(RowV1LineageError) as excinfo:
        apply_review_decisions(decisions, preview_dir, tmp_path / "out")
    assert LINEAGE_UNBOUND in str(excinfo.value)
    assert not (tmp_path / "out").exists()


def test_missing_apply_binding_fails_closed_for_the_sync_path(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")
    apply_dir = tmp_path / "apply_preview"
    apply_review_decisions(decisions, preview_dir, apply_dir)
    (apply_dir / APPLY_LINEAGE_FILENAME).unlink()

    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "MKA").mkdir()

    assert resolve_apply_lineage(apply_dir)["state"] == LINEAGE_UNBOUND
    with pytest.raises(RowV1LineageError):
        create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")


# --- TEST 8: an unknown identity scheme is not row_v1 ----------------------------------------


def test_unknown_record_identity_scheme_version_fails_closed(tmp_path):
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    _write_declaration(preview_dir, _pinned_workbook(), scheme="stable_record_v2")
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")

    status = resolve_preview_lineage(preview_dir)
    assert status["state"] == LINEAGE_UNSUPPORTED_SCHEME
    assert status["declared_scheme_version"] == "stable_record_v2"
    with pytest.raises(RowV1LineageError) as excinfo:
        apply_review_decisions(decisions, preview_dir, tmp_path / "out")
    assert "stable_record_v2" in str(excinfo.value)
    assert not (tmp_path / "out").exists()


# --- the contract itself ---------------------------------------------------------------------


def test_contract_is_packaged_and_self_verifying(monkeypatch, tmp_path):
    contract = load_lineage_contract()
    assert contract["record_identity_scheme_version"] == RECORD_IDENTITY_SCHEME_VERSION
    assert contract["lineage_workbook"]["sha256"] == (
        "9cbd93f1a754eb28aa358d74215445c5ffa3b1100dd947000aa9bed1b5c4ad2c"
    )

    import marketing_knowledge_agent.record_identity_lineage as module

    tampered_root = tmp_path / "authority"
    tampered_root.mkdir()
    payload = dict(contract)
    payload["lineage_workbook"] = {**payload["lineage_workbook"], "sha256": "b" * 64}
    (tampered_root / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(module, "_contract_root", lambda: tampered_root)
    with pytest.raises(RowV1LineageContractError):
        load_lineage_contract()


def test_apply_output_assigns_no_successor_identity_key(tmp_path):
    """WP0.4a establishes a lineage binding only; identity migration is a later work package."""
    from marketing_knowledge_agent.frontmatter import parse_markdown_with_frontmatter

    successor_key = "stable_record_id"
    preview_dir = _write_apply_preview_fixture(tmp_path / "preview")
    decisions = _reviewed_decisions(tmp_path / "decisions.csv")
    apply_dir = tmp_path / "apply_preview"
    apply_review_decisions(decisions, preview_dir, apply_dir)

    for path in sorted(apply_dir.rglob("*.md")):
        metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        assert successor_key not in metadata
    for path in sorted(apply_dir.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for record in payload if isinstance(payload, list) else [payload]:
            if isinstance(record, dict):
                assert successor_key not in record

    binding = json.loads((apply_dir / APPLY_LINEAGE_FILENAME).read_text(encoding="utf-8"))
    assert binding["record_identity_scheme_version"] == RECORD_IDENTITY_SCHEME_VERSION


def _rewrite_first_shared_string(source: Path, target: Path) -> None:
    """Copy a workbook, changing one cell value, keeping every name and sheet identical."""
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        payloads = {name: archive.read(name) for name in names}
    shared = payloads["xl/sharedStrings.xml"].decode("utf-8")
    marker = "<t>"
    start = shared.index(marker) + len(marker)
    end = shared.index("</t>", start)
    payloads["xl/sharedStrings.xml"] = (
        shared[:start] + "LINEAGE PROBE" + shared[end:]
    ).encode("utf-8")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, payloads[name])
