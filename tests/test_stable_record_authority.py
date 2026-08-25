"""Tests for the stable-record authority materialization engine.

The core tests are hermetic: they build a synthetic four-record proposal and a synthetic decision
artifact in ``tmp_path``, and touch no production evidence, no network, and no database. That is
deliberate — a safety check that only runs when a particular operator's disk is mounted is not a
safety check. The one test that reads the real M1/M2 evidence is marked and skips when the
evidence is absent.

The synthetic fixture mirrors the shape of the formal evidence rather than its contents, so each
of the three flag semantics is carried by a *different* record and cannot be conflated:

===============  ==========  ===============  =====================================================
synthetic id     confidence  binding          flags
===============  ==========  ===============  =====================================================
MKA-MC-00001     HIGH        legacy_bound     (none)
MKA-MC-00002     HIGH        legacy_bound     PAYLOAD_CHANGE_PRESENT|ASSET_REVIEW_REQUIRED_SEPARATELY
MKA-MC-00003     MEDIUM      legacy_bound     ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION
MKA-MC-00004     NEW         authority_only   ASSET_REVIEW_REQUIRED_SEPARATELY
===============  ==========  ===============  =====================================================
"""

from __future__ import annotations

import csv
import hashlib
import inspect
import io
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.stable_record_authority import (
    ACTIVATION_STATUS_NOT_ACTIVATED,
    ACTIVATION_TRUST_FIELD,
    ALIAS_BINDING_REQUIRES_SEPARATE_DECISION,
    ALIAS_BINDING_UNCHANGED,
    ASSET_REVIEW_NOT_IN_SCOPE,
    ASSET_REVIEW_REQUIRED_SEPARATELY,
    AUTHORITY_COLUMNS,
    AUTHORITY_OUTPUT_EXTERNAL_PIN_REQUIRED,
    AUTHORITY_RECORD_STATUS_CONTINUATION,
    AUTHORITY_RECORD_STATUS_NEW,
    AUTHORITY_SCHEMA_VERSION,
    AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
    BACKUP_EVIDENCE_NOT_SUPPLIED,
    BACKUP_EVIDENCE_VERIFIED,
    COMPANION_ARTIFACTS,
    DECISION_APPROVE_NEW_RECORD,
    DECISION_APPROVE_SAME_RECORD,
    DECISION_COLUMNS,
    FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION,
    FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,
    FLAG_PAYLOAD_CHANGE_PRESENT,
    IDENTITY_ORIGIN_AUTHORITY_NEW,
    IDENTITY_ORIGIN_LEGACY_CONTINUATION,
    LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY,
    LEGACY_SOURCE_SCHEME_ROW_V1,
    M3_BACKUP_GATE_NOT_ASSERTED,
    M3_BACKUP_GATE_PASS,
    MANIFEST_CONTENT_DIGEST_FIELD,
    MANIFEST_FILENAME,
    MANIFEST_HASH_FIELD,
    MANIFEST_RECEIPT_SHA256_FIELD,
    MATCH_EVIDENCE_DELIMITER,
    PACKAGE_MATERIALIZED_FIELD,
    PACKAGE_STATE_FIELD,
    PAYLOAD_CHANGE_NONE_RECORDED,
    PAYLOAD_CHANGE_PRESENT_NOT_APPROVED,
    PRODUCTION_AUTHORITY_RELPATH,
    PRODUCTION_REINDEX_AUTHORIZED_FIELD,
    RECEIPT_FILENAME,
    RECEIPT_HASH_FIELD,
    RECORD_IDENTITY_SCHEME,
    REGISTRY_FILENAME,
    ROW_V1_RETIRED_FIELD,
    ROW_V1_STATUS_RETAINED,
    SPECIAL_FLAGS_DELIMITER,
    STABLE_RECORD_V2_ACTIVATED_FIELD,
    AuthorityEvidencePins,
    StableRecordAuthorityError,
    build_stable_record_authority,
    compute_receipt_hash,
    load_authority_package,
    load_decision_artifact,
    load_proposal_evidence,
    materialize_stable_record_authority,
    parse_decision_rows,
    parse_match_evidence,
    parse_special_flags,
    qualify_legacy_record_id,
    read_decision_csv,
    verify_authority_manifest_integrity,
    verify_backup_evidence,
    verify_companion_artifacts,
    verify_receipt_integrity,
    verify_supplied_evidence,
    write_authority_package,
)
from marketing_knowledge_agent.stable_record_crosswalk import (
    NORMALIZATION_VERSION,
    CrosswalkProposal,
    StableRecordCrosswalkError,
    load_proposal,
    render_csv,
    write_proposal,
)


# --- expected outcome of the formal 2026-08-24 evidence ------------------------------------------
#
# Pinned here rather than derived, so the numbers a reviewer must confirm are visible in the test
# source whether or not the evidence is mounted. Nothing outside the formal integration test reads
# these, and no production hash is a module constant of the engine itself.

FORMAL_RECORD_COUNT = 121
FORMAL_IDENTITY_CONTINUATIONS = 120
FORMAL_NEW_IDENTITIES = 1
FORMAL_NEW_STABLE_RECORD_ID = "MKA-MC-00121"
FORMAL_ASSET_REVIEW_IDS = ("MKA-MC-00014", "MKA-MC-00121")
FORMAL_ALIAS_REVIEW_IDS = ("MKA-MC-00045",)
FORMAL_PAYLOAD_CHANGE_IDS = ("MKA-MC-00014",)
FORMAL_REVIEWER = "James Huang"
FORMAL_REVIEWED_AT = "2026-08-24"

_REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAL_PROPOSAL_DIR = _REPO_ROOT / "data/identity/proposals/stable-record-crosswalk-m1-2026-08-21"
FORMAL_DECISION_DIR = (
    _REPO_ROOT / "data/identity/reviews/stable-record-crosswalk-m2-2026-08-21/final-r1"
)
FORMAL_DECISIONS_PATH = FORMAL_DECISION_DIR / "human_review_decisions_final.csv"

# The M3 preflight backup lives off this volume by design, so its location is machine-specific and
# is supplied by the caller rather than derived. It is a *test* constant: the engine holds no
# production path and no production hash of its own.
FORMAL_BACKUP_DIR = (
    Path.home() / ".mka_identity_backup" / "stable-record-m3-preflight-2026-08-24"
)
FORMAL_BACKUP_MANIFEST_PATH = FORMAL_BACKUP_DIR / "backup_manifest.json"
FORMAL_BACKUP_MANIFEST_SHA256 = (
    "5f4ef010b109af5517159e82652d49876a46635317ae55ffeb876b2d2e8b1d11"
)

FORMAL_PINS = AuthorityEvidencePins(
    proposal_registry_sha256="5cbacc11813fc72ab9573a3a110eb65b04e4fde6536aa0c6a0bd7658056baf73",
    proposal_crosswalk_sha256="8bb5ca326a2d68ee8e50d7059868724737604320fe7c7fb5777f55e0d7eaae9a",
    proposal_content_digest="6155d2c06b045600077c2edfc192c287a231192ed91ac7f59ba98031244064ce",
    proposal_manifest_hash="0996bf8f221910b4730acbe16202e39d85c29c6fc56ad537e707a913e604c1f9",
    decision_artifact_sha256="3e5e52f8098e58fb587754803ad63d1e3c73d7ec06fa7f9880d89df2b27d4938",
    reviewer=FORMAL_REVIEWER,
    reviewed_at=FORMAL_REVIEWED_AT,
    decision_manifest_sha256="b44b0036ff8d3eac722437af62809bf19283da865fcb4ac723e77b818e01962a",
    decision_apply_preview_sha256="75afbef063599f886f526d0d9437068ae768ce15dadb018dcab91fd72410019c",
    decision_reissue_receipt_sha256="f54ac619a7f2ab420eb86de08bc39e2b2723e223b09ae0af15f8e12642577d6f",
    decision_companion_dir=str(FORMAL_DECISION_DIR),
    backup_manifest_sha256=FORMAL_BACKUP_MANIFEST_SHA256,
    backup_manifest_path=str(FORMAL_BACKUP_MANIFEST_PATH),
)

# The backup joins the skip condition because the pins now enforce it: a machine that has the M1
# and M2 evidence but not the backup would fail these tests rather than skip them, which reads as
# a regression in the engine instead of an absent artifact.
formal_evidence = pytest.mark.skipif(
    not (
        FORMAL_PROPOSAL_DIR.is_dir()
        and FORMAL_DECISIONS_PATH.is_file()
        and FORMAL_BACKUP_MANIFEST_PATH.is_file()
    ),
    reason="formal M1/M2/backup identity evidence is not present on this machine",
)


# --- synthetic fixtures ---------------------------------------------------------------------------

SYNTHETIC_LEGACY_SHA = "a" * 64
SYNTHETIC_AUTHORITY_SHA = "b" * 64
SYNTHETIC_SHEET = "商家夥伴案例資料庫"
SYNTHETIC_MIGRATION_VERSION = "stable-record-crosswalk/synthetic-test"
SYNTHETIC_REVIEWER = "Synthetic Reviewer"
SYNTHETIC_REVIEWED_AT = "2026-08-24"
SYNTHETIC_WORKBOOK_SHA_FIELD = "84ee4ad596c13b83f0bf0028d35f8b51085930d045081cc5918d643c527d2d16"

# id, confidence, legacy_row, authority_row, evidence, payload_change_fields, flags
_SYNTHETIC_SPEC = (
    ("MKA-MC-00001", "HIGH", "10", "11", "brand_match;year_match;handle_both_present;handle_match", "", ()),
    (
        "MKA-MC-00002",
        "HIGH",
        "12",
        "13",
        "brand_match;year_match;handle_both_present;handle_match",
        "video",
        (FLAG_PAYLOAD_CHANGE_PRESENT, FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY),
    ),
    (
        "MKA-MC-00003",
        "MEDIUM",
        "32",
        "33",
        "brand_match;year_match;legacy_handle_missing;authority_handle_missing",
        "",
        (FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION,),
    ),
    (
        "MKA-MC-00004",
        "NEW",
        "",
        "8",
        "authority_only_record",
        "",
        (FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,),
    ),
)


def _synthetic_proposal() -> CrosswalkProposal:
    registry_rows = []
    crosswalk_rows = []
    confidence_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "AMBIGUOUS": 0, "UNMATCHED": 0, "NEW": 0}
    asset_candidates = []
    payload_changed = 0

    for index, (stable_id, confidence, legacy_row, authority_row, evidence, payload, flags) in enumerate(
        _SYNTHETIC_SPEC
    ):
        confidence_counts[confidence] += 1
        if payload:
            payload_changed += 1
        if FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY in flags:
            asset_candidates.append(
                {
                    "stable_record_id": stable_id,
                    "fields": ["video"],
                    "match_confidence": confidence,
                }
            )
        registry_rows.append(
            {
                "stable_record_id": stable_id,
                "record_type": "merchant_case",
                "lifecycle_state": "active",
                "issuance_batch": SYNTHETIC_MIGRATION_VERSION,
                "issuance_status": "proposed",
                "seed_derivation_digest": f"0000000{index}-0000-5000-8000-00000000000{index}",
                "review_status": "pending",
            }
        )
        crosswalk_rows.append(
            {
                "stable_record_id": stable_id,
                "record_type": "merchant_case",
                "legacy_source_sheet": SYNTHETIC_SHEET if legacy_row else "",
                "legacy_source_row": legacy_row,
                "authority_source_sheet": SYNTHETIC_SHEET,
                "authority_source_row": authority_row,
                "legacy_workbook_sha256": SYNTHETIC_LEGACY_SHA if legacy_row else "",
                "authority_workbook_sha256": SYNTHETIC_AUTHORITY_SHA,
                "brand_name_at_migration": f"品牌{index}",
                "merchant_handle_at_migration": f"handle{index}",
                "interview_year_at_migration": "2026",
                "match_confidence": confidence,
                "match_evidence": evidence,
                "match_evidence_normalization": NORMALIZATION_VERSION,
                "payload_change_fields": payload,
                "conflict_fields": "",
                "review_status": "pending",
                "reviewed_by": "",
                "reviewed_at": "",
                "migration_version": SYNTHETIC_MIGRATION_VERSION,
                "notes": "",
            }
        )

    return CrosswalkProposal(
        registry_rows=tuple(registry_rows),
        crosswalk_rows=tuple(crosswalk_rows),
        legacy_sha256=SYNTHETIC_LEGACY_SHA,
        authority_sha256=SYNTHETIC_AUTHORITY_SHA,
        migration_version=SYNTHETIC_MIGRATION_VERSION,
        legacy_record_count=3,
        authority_record_count=4,
        confidence_counts=confidence_counts,
        reconciliation={
            "matched_legacy_records": 3,
            "authority_only_records": 1,
            "legacy_unmatched_records": 0,
            "ambiguous_records": 0,
            "shifted_rows": 3,
            "unchanged_rows": 0,
        },
        payload_changed_record_count=payload_changed,
        asset_review_candidate_count=len(asset_candidates),
        asset_review_candidate_field_count=len(asset_candidates),
        asset_review_candidates=tuple(asset_candidates),
    )


def _decision_row(stable_id, confidence, decision, binding, scope, flags, pins_source):
    return {
        "stable_record_id": stable_id,
        "match_confidence": confidence,
        "review_scope": "HIGH_BATCH" if confidence == "HIGH" else ("NEW_RECORD" if confidence == "NEW" else "INDIVIDUAL"),
        "identity_review_decision": decision,
        "decision_source": "explicit_user_confirmation_synthetic",
        "decision_channel": "chat_session_explicit_confirmation",
        "reviewer": SYNTHETIC_REVIEWER,
        "reviewer_source": "explicit_user_confirmation",
        "reviewer_attribution_status": "explicitly_confirmed_by_user",
        "reviewed_at": SYNTHETIC_REVIEWED_AT,
        "reviewed_at_source": "explicit_user_confirmation",
        "review_note": "synthetic fixture",
        "batch_id": "",
        "legacy_binding": binding,
        "identity_scope": scope,
        "special_flags": SPECIAL_FLAGS_DELIMITER.join(flags),
        "source_excel_cell_state": "未決定",
        "proposal_registry_sha256": pins_source["registry"],
        "proposal_crosswalk_sha256": pins_source["crosswalk"],
        "proposal_content_digest": pins_source["content_digest"],
        "review_package_workbook_sha256": SYNTHETIC_WORKBOOK_SHA_FIELD,
        "supersedes_artifact": "",
    }


def _synthetic_decision_rows(pins_source):
    rows = []
    for stable_id, confidence, legacy_row, _authority_row, _evidence, _payload, flags in _SYNTHETIC_SPEC:
        legacy_bound = bool(legacy_row)
        rows.append(
            _decision_row(
                stable_id,
                confidence,
                DECISION_APPROVE_SAME_RECORD if legacy_bound else DECISION_APPROVE_NEW_RECORD,
                "legacy_bound" if legacy_bound else "authority_only",
                "identity_continuity_only" if legacy_bound else "new_identity_only",
                flags,
                pins_source,
            )
        )
    return rows


@pytest.fixture
def evidence(tmp_path):
    """Publish a synthetic proposal and decision artifact, and return them with matching pins."""
    proposal_dir = tmp_path / "m1-proposal"
    manifest = write_proposal(_synthetic_proposal(), proposal_dir)

    pins_source = {
        "registry": manifest["registry_sha256"],
        "crosswalk": manifest["crosswalk_sha256"],
        "content_digest": manifest["content_digest"],
    }
    decision_rows = _synthetic_decision_rows(pins_source)
    decisions_path = tmp_path / "m2-final" / "human_review_decisions_final.csv"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_bytes(render_csv(decision_rows, DECISION_COLUMNS))

    pins = AuthorityEvidencePins(
        proposal_registry_sha256=manifest["registry_sha256"],
        proposal_crosswalk_sha256=manifest["crosswalk_sha256"],
        proposal_content_digest=manifest["content_digest"],
        proposal_manifest_hash=manifest["manifest_hash"],
        decision_artifact_sha256=hashlib.sha256(decisions_path.read_bytes()).hexdigest(),
        reviewer=SYNTHETIC_REVIEWER,
        reviewed_at=SYNTHETIC_REVIEWED_AT,
    )

    class _Evidence:
        def __init__(self):
            self.proposal_dir = proposal_dir
            self.decisions_path = decisions_path
            self.pins = pins
            self.pins_source = pins_source
            self.proposal_manifest = manifest

        def loaded_proposal(self, override_pins=None):
            return load_proposal_evidence(self.proposal_dir, override_pins or self.pins)

        def decisions(self, override_pins=None):
            return load_decision_artifact(self.decisions_path, override_pins or self.pins)

        def build(self, override_pins=None):
            active = override_pins or self.pins
            return build_stable_record_authority(
                self.loaded_proposal(self.pins), self.decisions(self.pins), active
            )

        def rewrite_decisions(self, rows):
            self.decisions_path.write_bytes(render_csv(rows, DECISION_COLUMNS))
            return self.repin()

        def write_raw_decisions(self, text):
            """Write the decision CSV verbatim, so row *shape* can be made hostile."""
            self.decisions_path.write_bytes(text.encode("utf-8"))
            return self.repin()

        def repin(self):
            digest = hashlib.sha256(self.decisions_path.read_bytes()).hexdigest()
            self.pins = AuthorityEvidencePins(
                proposal_registry_sha256=pins.proposal_registry_sha256,
                proposal_crosswalk_sha256=pins.proposal_crosswalk_sha256,
                proposal_content_digest=pins.proposal_content_digest,
                proposal_manifest_hash=pins.proposal_manifest_hash,
                decision_artifact_sha256=digest,
                reviewer=SYNTHETIC_REVIEWER,
                reviewed_at=SYNTHETIC_REVIEWED_AT,
            )
            return self.pins

    return _Evidence()


def _row_by_id(rows, stable_id):
    for row in rows:
        if row["stable_record_id"] == stable_id:
            return row
    raise AssertionError(f"{stable_id} not present")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pins_with(evidence, **overrides):
    """The fixture's pins with specific fields replaced, so one variable moves at a time."""
    base = dict(
        proposal_registry_sha256=evidence.pins.proposal_registry_sha256,
        proposal_crosswalk_sha256=evidence.pins.proposal_crosswalk_sha256,
        proposal_content_digest=evidence.pins.proposal_content_digest,
        proposal_manifest_hash=evidence.pins.proposal_manifest_hash,
        decision_artifact_sha256=evidence.pins.decision_artifact_sha256,
        reviewer=SYNTHETIC_REVIEWER,
        reviewed_at=SYNTHETIC_REVIEWED_AT,
    )
    base.update(overrides)
    return AuthorityEvidencePins(**base)


def _write_backup(tmp_path, content=b'{"backup": "synthetic m3 preflight"}\n', name="backup_manifest.json"):
    path = tmp_path / "backup" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _write_companions(evidence, *, filenames=None):
    """Write the companion artifacts beside the decision CSV and return their pins."""
    directory = evidence.decisions_path.parent
    wanted = filenames if filenames is not None else [name for name, _attr in COMPANION_ARTIFACTS]
    pins_kwargs = {"decision_companion_dir": str(directory)}
    for filename, attr in COMPANION_ARTIFACTS:
        if filename not in wanted:
            continue
        path = directory / filename
        path.write_bytes(json.dumps({"artifact": filename}, sort_keys=True).encode("utf-8") + b"\n")
        pins_kwargs[attr] = _sha256(path)
    return pins_kwargs


def _csv_line(fields):
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="").writerow(fields)
    return buffer.getvalue()


def _decision_fields(evidence, index=0, **overrides):
    """One valid decision row as a positional field list, optionally with values replaced."""
    row = dict(_synthetic_decision_rows(evidence.pins_source)[index])
    row.update(overrides)
    return [row[column] for column in DECISION_COLUMNS]


def _decision_csv(evidence, body_rows, header=None):
    """Render a decision CSV verbatim. ``None`` in ``body_rows`` emits a blank line."""
    lines = [_csv_line(list(header if header is not None else DECISION_COLUMNS))]
    for fields in body_rows:
        lines.append("" if fields is None else _csv_line(fields))
    return "\r\n".join(lines) + "\r\n"


def _bundle_bytes(output):
    return {path.name: path.read_bytes() for path in sorted(Path(output).iterdir())}


def _read_receipt(output):
    return json.loads((Path(output) / RECEIPT_FILENAME).read_text(encoding="utf-8"))


def _rewrite_receipt(output, receipt, *, reseal_receipt=False, reseal_manifest=False):
    """Write a modified receipt back, optionally re-deriving the seals an attacker could."""
    receipt = dict(receipt)
    if reseal_receipt:
        receipt.pop(RECEIPT_HASH_FIELD, None)
        receipt[RECEIPT_HASH_FIELD] = compute_receipt_hash(receipt)
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    (Path(output) / RECEIPT_FILENAME).write_bytes(payload)
    if reseal_manifest:
        manifest_path = Path(output) / MANIFEST_FILENAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[MANIFEST_RECEIPT_SHA256_FIELD] = hashlib.sha256(payload).hexdigest()
        manifest.pop(MANIFEST_HASH_FIELD, None)
        from marketing_knowledge_agent.stable_record_authority import compute_manifest_hash

        manifest[MANIFEST_HASH_FIELD] = compute_manifest_hash(manifest)
        manifest_path.write_bytes(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        )
    return payload


# --- delimiter / parsing contract (the M3C bug class) -----------------------------------------------


def test_match_evidence_and_special_flags_use_different_delimiters():
    assert MATCH_EVIDENCE_DELIMITER == ";"
    assert SPECIAL_FLAGS_DELIMITER == "|"
    assert MATCH_EVIDENCE_DELIMITER != SPECIAL_FLAGS_DELIMITER


def test_match_evidence_splits_on_semicolon():
    assert parse_match_evidence("a;b;c") == ("a", "b", "c")


def test_special_flags_splits_on_pipe():
    assert parse_special_flags(
        "PAYLOAD_CHANGE_PRESENT|ASSET_REVIEW_REQUIRED_SEPARATELY"
        "|ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION"
    ) == (
        FLAG_PAYLOAD_CHANGE_PRESENT,
        FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,
        FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION,
    )


@pytest.mark.parametrize("value", ["", "   ", None])
def test_both_parsers_read_an_empty_field_as_no_tokens(value):
    assert parse_match_evidence(value) == ()
    assert parse_special_flags(value) == ()


def test_both_parsers_read_a_single_value_as_one_token():
    assert parse_match_evidence("brand_match") == ("brand_match",)
    assert parse_special_flags(FLAG_PAYLOAD_CHANGE_PRESENT) == (FLAG_PAYLOAD_CHANGE_PRESENT,)


def test_both_parsers_normalize_whitespace():
    assert parse_match_evidence("  a ;\tb  ;  c  ") == ("a", "b", "c")
    assert parse_special_flags(f"  {FLAG_PAYLOAD_CHANGE_PRESENT}  ") == (FLAG_PAYLOAD_CHANGE_PRESENT,)
    assert parse_match_evidence("a;;b") == ("a", "b")


def test_a_generic_semicolon_split_would_have_lost_the_asset_deferral():
    """The exact defect the field-specific parsers exist to prevent.

    Splitting the pipe-delimited flags on ``;`` yields one token — not an error — so a record
    keeps materializing while the asset deferral it carried silently disappears.
    """
    packed = f"{FLAG_PAYLOAD_CHANGE_PRESENT}|{FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY}"
    naive = tuple(token for token in packed.split(MATCH_EVIDENCE_DELIMITER) if token)
    assert len(naive) == 1
    assert FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY not in naive

    assert parse_special_flags(packed) == (
        FLAG_PAYLOAD_CHANGE_PRESENT,
        FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,
    )


def test_special_flags_refuses_the_match_evidence_delimiter():
    with pytest.raises(StableRecordAuthorityError, match="another artifact's delimiter"):
        parse_special_flags(f"{FLAG_PAYLOAD_CHANGE_PRESENT};{FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY}")


def test_match_evidence_refuses_the_special_flags_delimiter():
    with pytest.raises(StableRecordAuthorityError, match="another artifact's delimiter"):
        parse_match_evidence("brand_match|year_match")


def test_an_unrecognized_special_flag_fails_closed():
    with pytest.raises(StableRecordAuthorityError, match="unrecognized flag"):
        parse_special_flags("SOME_FUTURE_FLAG")


def test_a_repeated_special_flag_fails_closed():
    with pytest.raises(StableRecordAuthorityError, match="repeats flag"):
        parse_special_flags(f"{FLAG_PAYLOAD_CHANGE_PRESENT}|{FLAG_PAYLOAD_CHANGE_PRESENT}")


# --- qualified legacy identity --------------------------------------------------------------------


def test_legacy_identity_is_qualified_by_workbook_lineage():
    key = qualify_legacy_record_id(SYNTHETIC_LEGACY_SHA, SYNTHETIC_SHEET, "32")
    assert key == f"{LEGACY_SOURCE_SCHEME_ROW_V1}:{SYNTHETIC_LEGACY_SHA}:{SYNTHETIC_SHEET}:r32"


def test_the_same_bare_row_key_under_two_lineages_yields_two_identities():
    """``商家夥伴案例資料庫:r32`` names a different merchant in each workbook lineage."""
    legacy = qualify_legacy_record_id(SYNTHETIC_LEGACY_SHA, SYNTHETIC_SHEET, "32")
    authority = qualify_legacy_record_id(SYNTHETIC_AUTHORITY_SHA, SYNTHETIC_SHEET, "32")
    assert legacy != authority
    assert legacy.endswith(f"{SYNTHETIC_SHEET}:r32") and authority.endswith(f"{SYNTHETIC_SHEET}:r32")


@pytest.mark.parametrize("workbook", ["", "not-a-hash", "A" * 64])
def test_an_unqualified_row_key_is_refused(workbook):
    with pytest.raises(StableRecordAuthorityError, match="not a sha256"):
        qualify_legacy_record_id(workbook, SYNTHETIC_SHEET, "32")


@pytest.mark.parametrize("row", ["", "0", "-3", "r32", "abc"])
def test_a_non_positive_row_coordinate_is_refused(row):
    with pytest.raises(StableRecordAuthorityError, match="positive integer row coordinate"):
        qualify_legacy_record_id(SYNTHETIC_LEGACY_SHA, SYNTHETIC_SHEET, row)


def test_every_row_declares_the_legacy_row_as_audit_metadata_only(evidence):
    authority = evidence.build()
    for row in authority.rows:
        assert row["legacy_source_row_role"] == LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY
    # Including the new identity, which has no legacy row at all: stating the role only where a
    # coordinate exists would let the column be read as an authority key by omission.
    assert _row_by_id(authority.rows, "MKA-MC-00004")["legacy_source_row"] == ""


def test_continuations_carry_a_qualified_legacy_identity_and_new_records_carry_none(evidence):
    authority = evidence.build()
    continuation = _row_by_id(authority.rows, "MKA-MC-00003")
    assert continuation["legacy_source_record_id"] == (
        f"{LEGACY_SOURCE_SCHEME_ROW_V1}:{SYNTHETIC_LEGACY_SHA}:{SYNTHETIC_SHEET}:r32"
    )
    assert continuation["legacy_source_scheme"] == LEGACY_SOURCE_SCHEME_ROW_V1

    new_record = _row_by_id(authority.rows, "MKA-MC-00004")
    assert new_record["legacy_source_record_id"] == ""
    assert new_record["legacy_source_scheme"] == ""


# --- schema and the materialized/activated distinction ----------------------------------------------


def test_the_authority_schema_carries_every_contract_column():
    required = {
        "stable_record_id",
        "authority_record_status",
        "identity_origin",
        "legacy_source_record_id",
        "legacy_source_row",
        "legacy_source_row_role",
        "authority_source_row",
        "match_confidence",
        "match_evidence",
        "human_decision",
        "reviewer",
        "reviewed_at",
        "source_proposal_registry_sha256",
        "source_proposal_crosswalk_sha256",
        "source_proposal_content_digest",
        "source_proposal_manifest_hash",
        "source_decision_artifact_sha256",
        "record_identity_scheme",
        "authority_status",
    }
    assert required <= set(AUTHORITY_COLUMNS)


def test_authority_columns_are_an_exact_pin():
    """A schema change must be adjudicated by a reviewer, not absorbed silently."""
    assert AUTHORITY_COLUMNS == (
        "stable_record_id",
        "record_identity_scheme",
        "authority_record_status",
        "authority_status",
        "activation_status",
        "identity_origin",
        "legacy_source_record_id",
        "legacy_source_scheme",
        "legacy_source_sheet",
        "legacy_source_row",
        "legacy_source_row_role",
        "legacy_workbook_sha256",
        "authority_source_sheet",
        "authority_source_row",
        "authority_workbook_sha256",
        "record_type",
        "match_confidence",
        "match_evidence",
        "match_evidence_normalization",
        "human_decision",
        "identity_scope",
        "review_scope",
        "decision_source",
        "decision_channel",
        "reviewer",
        "reviewed_at",
        "seed_derivation_digest",
        "row_v1_status",
        "alias_binding_status",
        "asset_review_status",
        "payload_change_status",
        "special_flags",
        "source_proposal_registry_sha256",
        "source_proposal_crosswalk_sha256",
        "source_proposal_content_digest",
        "source_proposal_manifest_hash",
        "source_decision_artifact_sha256",
    )


def test_a_materialized_authority_is_not_an_activated_one(evidence, tmp_path):
    authority = evidence.build()
    manifest = write_authority_package(authority, tmp_path / "authority")

    # Package-scoped naming: this says a bundle exists, never that the project's
    # AUTHORITY_MATERIALIZED governance gate moved.
    assert manifest[PACKAGE_MATERIALIZED_FIELD] is True
    assert "authority_materialized" not in manifest
    assert manifest[STABLE_RECORD_V2_ACTIVATED_FIELD] is False
    assert manifest[ROW_V1_RETIRED_FIELD] is False
    assert manifest["activation_status"] == ACTIVATION_STATUS_NOT_ACTIVATED
    assert manifest["authority_status"] == AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED

    for row in authority.rows:
        assert row["record_identity_scheme"] == RECORD_IDENTITY_SCHEME
        assert row["authority_status"] == AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED
        assert row["activation_status"] == ACTIVATION_STATUS_NOT_ACTIVATED
        assert row["row_v1_status"] == ROW_V1_STATUS_RETAINED


def test_the_manifest_binds_every_required_fact(evidence, tmp_path):
    authority = evidence.build()
    pins = evidence.pins
    manifest = write_authority_package(authority, tmp_path / "authority")

    assert manifest["authority_schema_version"] == AUTHORITY_SCHEMA_VERSION
    assert manifest["record_count"] == len(_SYNTHETIC_SPEC)
    assert manifest["stable_id_set_digest"] == authority.stable_id_set_digest
    assert manifest["source_proposal"] == {
        "registry_sha256": pins.proposal_registry_sha256,
        "crosswalk_sha256": pins.proposal_crosswalk_sha256,
        "content_digest": pins.proposal_content_digest,
        "manifest_hash": pins.proposal_manifest_hash,
    }
    assert manifest["source_decision_artifact"]["decisions_sha256"] == pins.decision_artifact_sha256
    assert manifest["reviewer"] == SYNTHETIC_REVIEWER
    assert manifest["review_date"] == SYNTHETIC_REVIEWED_AT
    assert manifest["match_evidence_delimiter"] == MATCH_EVIDENCE_DELIMITER
    assert manifest["special_flags_delimiter"] == SPECIAL_FLAGS_DELIMITER
    for counter in (
        "alias_mutations",
        "asset_mutations",
        "vault_mutations",
        "decision_store_mutations",
        "content_index_mutations",
        "approved_url_authority_mutations",
        "row_v1_authority_mutations",
        "proposal_mutations",
        "decision_artifact_mutations",
    ):
        assert manifest[counter] == 0, counter
    assert manifest["production_reindex_authorized"] is False
    assert isinstance(manifest["manifest_hash"], str) and len(manifest["manifest_hash"]) == 64


def test_a_verified_backup_is_recorded_as_verified(evidence, tmp_path):
    backup = _write_backup(tmp_path)
    pins = _pins_with(evidence, backup_manifest_sha256=_sha256(backup), backup_manifest_path=str(backup))

    authority = evidence.build(override_pins=pins)
    manifest = write_authority_package(authority, tmp_path / "authority")

    assert manifest["backup_evidence"]["backup_manifest_sha256"] == _sha256(backup)
    assert manifest["backup_evidence"]["backup_manifest_verified"] is True
    assert manifest["backup_evidence"]["verification_status"] == BACKUP_EVIDENCE_VERIFIED
    assert manifest["backup_evidence"]["m3_backup_gate"] == M3_BACKUP_GATE_PASS


def test_the_authority_manifest_seals_reproduce(evidence, tmp_path):
    authority = evidence.build()
    write_authority_package(authority, tmp_path / "authority")
    manifest, _rows = load_authority_package(tmp_path / "authority")
    verify_authority_manifest_integrity(manifest)


@pytest.mark.parametrize("field_name", ["record_count", "reviewer", "created_at", "content_digest"])
def test_an_edited_authority_manifest_is_refused(evidence, tmp_path, field_name):
    authority = evidence.build()
    output = tmp_path / "authority"
    write_authority_package(authority, output)

    manifest = json.loads((output / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest[field_name] = "tampered" if isinstance(manifest[field_name], str) else 999
    (output / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StableRecordAuthorityError, match="does not match its contents|malformed"):
        load_authority_package(output)


def test_an_edited_authority_csv_is_refused(evidence, tmp_path):
    authority = evidence.build()
    output = tmp_path / "authority"
    write_authority_package(authority, output)

    registry = output / REGISTRY_FILENAME
    registry.write_bytes(registry.read_bytes().replace(b"MKA-MC-00001", b"MKA-MC-09999"))

    with pytest.raises(StableRecordAuthorityError, match="does not match the manifest"):
        load_authority_package(output)


# --- record grain -----------------------------------------------------------------------------------


def test_the_authority_grain_is_the_record_not_the_decision_event(evidence, tmp_path):
    """Row-bound decision events are a different grain and are never expanded into identities."""
    authority = evidence.build()
    manifest = write_authority_package(authority, tmp_path / "authority")

    assert manifest["grain"] == "merchant_case_record"
    assert authority.record_count == len(_SYNTHETIC_SPEC)
    assert len(authority.rows) == len(set(authority.stable_record_ids))


def test_decision_events_are_never_expanded_into_identities(evidence):
    """The decision store holds a different grain and must not inflate the identity count.

    Against the formal evidence the two grains are 139 row-bound decision events versus 121
    records. Feeding the engine one record's worth of repeated events is the shape that would
    silently mint duplicate identities, so it is a refusal rather than a de-duplication: a
    materializer that quietly collapsed them would also quietly collapse two genuinely different
    records that happened to collide.
    """
    rows = _synthetic_decision_rows(evidence.pins_source)
    events = rows + [dict(rows[0]), dict(rows[0]), dict(rows[2])]
    assert len(events) > len(_SYNTHETIC_SPEC)

    with pytest.raises(StableRecordAuthorityError, match="more than once"):
        _build_from_rows(evidence, events)


def test_one_record_produces_exactly_one_authority_row(evidence):
    authority = evidence.build()
    counts = {}
    for row in authority.rows:
        counts[row["stable_record_id"]] = counts.get(row["stable_record_id"], 0) + 1
    assert set(counts.values()) == {1}


# --- alias boundary ---------------------------------------------------------------------------------


def test_the_alias_flagged_record_is_a_continuation_with_the_decision_deferred(evidence, tmp_path):
    authority = evidence.build()
    row = _row_by_id(authority.rows, "MKA-MC-00003")

    # Identity continuation is decided; the alias rebinding explicitly is not.
    assert row["authority_record_status"] == AUTHORITY_RECORD_STATUS_CONTINUATION
    assert row["human_decision"] == DECISION_APPROVE_SAME_RECORD
    assert row["alias_binding_status"] == ALIAS_BINDING_REQUIRES_SEPARATE_DECISION
    assert authority.alias_decision_required_ids == ("MKA-MC-00003",)

    manifest = write_authority_package(authority, tmp_path / "authority")
    assert manifest["alias_mutations"] == 0
    assert manifest["alias_decision_required_records"] == ["MKA-MC-00003"]


def test_records_without_the_alias_flag_declare_no_alias_decision_in_scope(evidence):
    authority = evidence.build()
    for stable_id in ("MKA-MC-00001", "MKA-MC-00002", "MKA-MC-00004"):
        assert _row_by_id(authority.rows, stable_id)["alias_binding_status"] == ALIAS_BINDING_UNCHANGED


def test_the_engine_imports_no_surface_it_promises_not_to_mutate():
    """The zero-mutation counters are provable, not promised.

    Asserted over the module's actual import graph rather than its text: the manifest deliberately
    *names* every surface it declares zero mutations against, so a substring search over the source
    would match those declarations and prove nothing.
    """
    import ast

    import marketing_knowledge_agent.stable_record_authority as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
            imported.update(alias.name for alias in node.names)

    for forbidden in (
        "search_aliases",
        "content_index",
        "obsidian_sync",
        "structured_results",
        "query_planning",
        "slack_interface",
        "slack_presentation",
        "governance",
        "sqlite3",
    ):
        assert forbidden not in imported, f"the materializer must not import {forbidden}"

    # The only sibling module it may reach is the proposal contract it reads.
    assert imported & {"stable_record_crosswalk"} == {"stable_record_crosswalk"}


# --- asset boundary ---------------------------------------------------------------------------------


def test_asset_flagged_records_defer_rather_than_approve(evidence, tmp_path):
    authority = evidence.build()
    assert authority.asset_review_required_ids == ("MKA-MC-00002", "MKA-MC-00004")

    for stable_id in authority.asset_review_required_ids:
        assert _row_by_id(authority.rows, stable_id)["asset_review_status"] == (
            ASSET_REVIEW_REQUIRED_SEPARATELY
        )

    manifest = write_authority_package(authority, tmp_path / "authority")
    assert manifest["asset_mutations"] == 0
    assert manifest["asset_review_required_records"] == ["MKA-MC-00002", "MKA-MC-00004"]


def test_records_without_the_asset_flag_declare_asset_review_out_of_scope(evidence):
    authority = evidence.build()
    for stable_id in ("MKA-MC-00001", "MKA-MC-00003"):
        assert _row_by_id(authority.rows, stable_id)["asset_review_status"] == ASSET_REVIEW_NOT_IN_SCOPE


def test_a_payload_change_is_not_a_content_approval(evidence):
    authority = evidence.build()
    assert authority.payload_change_ids == ("MKA-MC-00002",)
    row = _row_by_id(authority.rows, "MKA-MC-00002")
    assert row["payload_change_status"] == PAYLOAD_CHANGE_PRESENT_NOT_APPROVED
    assert row["human_decision"] == DECISION_APPROVE_SAME_RECORD
    assert _row_by_id(authority.rows, "MKA-MC-00001")["payload_change_status"] == (
        PAYLOAD_CHANGE_NONE_RECORDED
    )


def test_the_three_flag_semantics_stay_separate(evidence):
    """Payload change, asset review, and alias rebinding are three different deferrals."""
    authority = evidence.build()
    assert authority.payload_change_ids == ("MKA-MC-00002",)
    assert authority.asset_review_required_ids == ("MKA-MC-00002", "MKA-MC-00004")
    assert authority.alias_decision_required_ids == ("MKA-MC-00003",)

    # The record that carries two flags carries exactly those two, and the alias record carries
    # neither of them.
    assert parse_special_flags(_row_by_id(authority.rows, "MKA-MC-00002")["special_flags"]) == (
        FLAG_PAYLOAD_CHANGE_PRESENT,
        FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,
    )
    assert parse_special_flags(_row_by_id(authority.rows, "MKA-MC-00003")["special_flags"]) == (
        FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION,
    )


# --- roster non-duplication --------------------------------------------------------------------------


def test_the_authority_carries_identity_linkage_not_the_merchant_roster(evidence, tmp_path):
    authority = evidence.build()
    for forbidden in (
        "brand_name_at_migration",
        "merchant_handle_at_migration",
        "interview_year_at_migration",
    ):
        assert forbidden not in AUTHORITY_COLUMNS

    output = tmp_path / "authority"
    manifest = write_authority_package(authority, output)
    assert manifest["contains_merchant_roster"] is False

    published = (output / REGISTRY_FILENAME).read_text(encoding="utf-8")
    for index in range(len(_SYNTHETIC_SPEC)):
        assert f"品牌{index}" not in published
        assert f"handle{index}" not in published


# --- external evidence pins are load-bearing -----------------------------------------------------------


def test_both_refusal_layers_are_catchable_as_one(evidence, tmp_path):
    """A caller may fail closed on a single exception type.

    ``load_proposal_evidence`` refuses in two different layers — the proposal loader when the
    directory itself is unsound, this module when the pins disagree — so both must remain
    catchable together or a caller would have to know which layer fired to stay safe.
    """
    assert issubclass(StableRecordAuthorityError, ValueError)
    assert issubclass(StableRecordCrosswalkError, ValueError)

    empty = tmp_path / "not-a-proposal"
    empty.mkdir()
    with pytest.raises(ValueError):
        load_proposal_evidence(empty, evidence.pins)


def test_a_correctly_pinned_proposal_loads(evidence):
    proposal = evidence.loaded_proposal()
    assert len(proposal.crosswalk_rows) == len(_SYNTHETIC_SPEC)


def test_a_proposal_that_is_internally_valid_but_externally_repinned_is_refused(evidence, tmp_path):
    """The threat the self-seal cannot answer.

    An operator who rewrites the CSVs and re-derives every seal produces a directory the proposal
    loader accepts, because the loader has nothing left to compare against. This asserts both
    halves: the enforcing loader accepts it, and the pin check refuses it.
    """
    tampered = _synthetic_proposal()
    rows = [dict(row) for row in tampered.crosswalk_rows]
    rows[0]["brand_name_at_migration"] = "另一個品牌"
    tampered = CrosswalkProposal(
        registry_rows=tampered.registry_rows,
        crosswalk_rows=tuple(rows),
        legacy_sha256=tampered.legacy_sha256,
        authority_sha256=tampered.authority_sha256,
        migration_version=tampered.migration_version,
        legacy_record_count=tampered.legacy_record_count,
        authority_record_count=tampered.authority_record_count,
        confidence_counts=tampered.confidence_counts,
        reconciliation=tampered.reconciliation,
        payload_changed_record_count=tampered.payload_changed_record_count,
        asset_review_candidate_count=tampered.asset_review_candidate_count,
        asset_review_candidate_field_count=tampered.asset_review_candidate_field_count,
        asset_review_candidates=tampered.asset_review_candidates,
    )
    tampered_dir = tmp_path / "m1-repinned"
    write_proposal(tampered, tampered_dir)

    # The self-sealing loader is satisfied: every seal reproduces over the rewritten content.
    manifest, _registry, _crosswalk = load_proposal(tampered_dir)
    assert manifest["content_digest"] != evidence.pins.proposal_content_digest

    with pytest.raises(StableRecordAuthorityError, match="not the one that was reviewed"):
        load_proposal_evidence(tampered_dir, evidence.pins)


@pytest.mark.parametrize(
    "pin_field",
    [
        "proposal_registry_sha256",
        "proposal_crosswalk_sha256",
        "proposal_content_digest",
        "proposal_manifest_hash",
    ],
)
def test_a_wrong_m1_pin_is_refused(evidence, pin_field):
    values = {
        "proposal_registry_sha256": evidence.pins.proposal_registry_sha256,
        "proposal_crosswalk_sha256": evidence.pins.proposal_crosswalk_sha256,
        "proposal_content_digest": evidence.pins.proposal_content_digest,
        "proposal_manifest_hash": evidence.pins.proposal_manifest_hash,
        "decision_artifact_sha256": evidence.pins.decision_artifact_sha256,
        "reviewer": SYNTHETIC_REVIEWER,
        "reviewed_at": SYNTHETIC_REVIEWED_AT,
    }
    values[pin_field] = "d" * 64
    with pytest.raises(StableRecordAuthorityError, match="not the one that was reviewed"):
        load_proposal_evidence(evidence.proposal_dir, AuthorityEvidencePins(**values))


def test_a_wrong_m2_artifact_pin_is_refused(evidence):
    wrong = AuthorityEvidencePins(
        proposal_registry_sha256=evidence.pins.proposal_registry_sha256,
        proposal_crosswalk_sha256=evidence.pins.proposal_crosswalk_sha256,
        proposal_content_digest=evidence.pins.proposal_content_digest,
        proposal_manifest_hash=evidence.pins.proposal_manifest_hash,
        decision_artifact_sha256="e" * 64,
        reviewer=SYNTHETIC_REVIEWER,
        reviewed_at=SYNTHETIC_REVIEWED_AT,
    )
    with pytest.raises(StableRecordAuthorityError, match="not the one that was reviewed"):
        load_decision_artifact(evidence.decisions_path, wrong)


def test_a_substituted_decision_artifact_is_refused_before_it_is_parsed(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["identity_review_decision"] = "pending"
    evidence.decisions_path.write_bytes(render_csv(rows, DECISION_COLUMNS))

    with pytest.raises(StableRecordAuthorityError, match="not the one that was reviewed"):
        load_decision_artifact(evidence.decisions_path, evidence.pins)


def test_a_pinned_companion_artifact_that_is_missing_is_refused(evidence):
    pins = _pins_with(
        evidence,
        decision_manifest_sha256="f" * 64,
        decision_companion_dir=str(evidence.decisions_path.parent),
    )
    with pytest.raises(StableRecordAuthorityError, match="pinned but missing"):
        materialize_stable_record_authority(
            evidence.proposal_dir, evidence.decisions_path, pins
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"proposal_registry_sha256": "short"}, "not a sha256"),
        ({"proposal_content_digest": ""}, "not a sha256"),
        ({"decision_artifact_sha256": "G" * 64}, "not a sha256"),
        ({"reviewer": "   "}, "reviewer is empty"),
        ({"reviewed_at": "2026/08/24"}, "not an ISO"),
        ({"backup_manifest_sha256": "nope"}, "not a sha256"),
    ],
)
def test_a_malformed_pin_contract_is_refused(kwargs, message):
    base = {
        "proposal_registry_sha256": "1" * 64,
        "proposal_crosswalk_sha256": "2" * 64,
        "proposal_content_digest": "3" * 64,
        "proposal_manifest_hash": "4" * 64,
        "decision_artifact_sha256": "5" * 64,
        "reviewer": SYNTHETIC_REVIEWER,
        "reviewed_at": SYNTHETIC_REVIEWED_AT,
    }
    base.update(kwargs)
    with pytest.raises(StableRecordAuthorityError, match=message):
        AuthorityEvidencePins(**base)


def test_decisions_that_name_a_different_proposal_are_refused(evidence):
    rows = _synthetic_decision_rows(
        {
            "registry": evidence.pins_source["registry"],
            "crosswalk": evidence.pins_source["crosswalk"],
            "content_digest": "9" * 64,
        }
    )
    pins = evidence.rewrite_decisions(rows)
    with pytest.raises(StableRecordAuthorityError, match="decision-declared proposal content_digest"):
        build_stable_record_authority(
            evidence.loaded_proposal(pins), evidence.decisions(pins), pins
        )


# --- hostile decision matrix ---------------------------------------------------------------------------


def _build_from_rows(evidence, rows):
    pins = evidence.rewrite_decisions(rows)
    return build_stable_record_authority(
        evidence.loaded_proposal(pins), evidence.decisions(pins), pins
    )


def test_an_unknown_stable_id_in_the_decisions_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows.append(
        _decision_row(
            "MKA-MC-09999",
            "HIGH",
            DECISION_APPROVE_SAME_RECORD,
            "legacy_bound",
            "identity_continuity_only",
            (),
            evidence.pins_source,
        )
    )
    with pytest.raises(StableRecordAuthorityError, match="stable IDs the proposal never issued"):
        _build_from_rows(evidence, rows)


def test_a_missing_stable_id_in_the_decisions_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)[:-1]
    with pytest.raises(StableRecordAuthorityError, match="no human decision"):
        _build_from_rows(evidence, rows)


def test_a_duplicate_stable_id_in_the_decisions_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows.append(dict(rows[0]))
    with pytest.raises(StableRecordAuthorityError, match="more than once"):
        _build_from_rows(evidence, rows)


@pytest.mark.parametrize(
    "decision", ["pending", "rejected", "needs_more_information", "unresolved", ""]
)
def test_a_decision_that_is_not_an_approval_is_refused(evidence, decision):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["identity_review_decision"] = decision
    with pytest.raises(StableRecordAuthorityError, match="may be materialized"):
        _build_from_rows(evidence, rows)


def test_approving_a_legacy_bound_record_as_a_new_identity_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["identity_review_decision"] = DECISION_APPROVE_NEW_RECORD
    rows[0]["identity_scope"] = "new_identity_only"
    with pytest.raises(StableRecordAuthorityError, match="would orphan every decision"):
        _build_from_rows(evidence, rows)


def test_approving_an_authority_only_record_as_a_continuation_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[-1]["identity_review_decision"] = DECISION_APPROVE_SAME_RECORD
    rows[-1]["identity_scope"] = "identity_continuity_only"
    with pytest.raises(StableRecordAuthorityError, match="assert a predecessor that does not exist"):
        _build_from_rows(evidence, rows)


def test_a_legacy_binding_that_contradicts_the_proposal_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["legacy_binding"] = "authority_only"
    with pytest.raises(StableRecordAuthorityError, match="legacy binding disagrees"):
        _build_from_rows(evidence, rows)


def test_a_confidence_that_contradicts_the_proposal_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["match_confidence"] = "MEDIUM"
    with pytest.raises(StableRecordAuthorityError, match="match_confidence disagrees"):
        _build_from_rows(evidence, rows)


def test_a_reviewer_mismatch_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["reviewer"] = "Someone Else"
    with pytest.raises(StableRecordAuthorityError, match="reviewer attribution is never inferred"):
        _build_from_rows(evidence, rows)


def test_a_review_date_mismatch_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["reviewed_at"] = "2026-08-25"
    with pytest.raises(StableRecordAuthorityError, match="but the contract pins"):
        _build_from_rows(evidence, rows)


def test_an_asset_flag_the_proposal_does_not_corroborate_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["special_flags"] = FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY
    with pytest.raises(StableRecordAuthorityError, match="asset-review evidence disagrees"):
        _build_from_rows(evidence, rows)


def test_a_dropped_asset_flag_is_refused(evidence):
    """The M3C delimiter defect, caught at the reconciliation layer as well as the parser."""
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[1]["special_flags"] = FLAG_PAYLOAD_CHANGE_PRESENT
    with pytest.raises(StableRecordAuthorityError, match="asset-review evidence disagrees"):
        _build_from_rows(evidence, rows)


def test_a_payload_flag_the_proposal_does_not_corroborate_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["special_flags"] = FLAG_PAYLOAD_CHANGE_PRESENT
    with pytest.raises(StableRecordAuthorityError, match="payload-change evidence disagrees"):
        _build_from_rows(evidence, rows)


def test_a_malformed_special_flags_field_fails_closed(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["special_flags"] = "PAYLOAD_CHANGE_PRESENT;ASSET_REVIEW_REQUIRED_SEPARATELY"
    with pytest.raises(StableRecordAuthorityError, match="another artifact's delimiter"):
        _build_from_rows(evidence, rows)


def test_an_identity_scope_that_contradicts_the_binding_is_refused(evidence):
    rows = _synthetic_decision_rows(evidence.pins_source)
    rows[0]["identity_scope"] = "new_identity_only"
    with pytest.raises(StableRecordAuthorityError, match="expected 'identity_continuity_only'"):
        _build_from_rows(evidence, rows)


def test_a_malformed_stable_id_in_the_decisions_is_refused():
    rows = _synthetic_decision_rows(
        {"registry": "1" * 64, "crosswalk": "2" * 64, "content_digest": "3" * 64}
    )
    rows[0]["stable_record_id"] = "MC-1"
    with pytest.raises(StableRecordAuthorityError, match="does not match"):
        parse_decision_rows(rows)


def test_a_decision_row_with_the_wrong_columns_is_refused():
    rows = _synthetic_decision_rows(
        {"registry": "1" * 64, "crosswalk": "2" * 64, "content_digest": "3" * 64}
    )
    rows[0].pop("review_note")
    with pytest.raises(StableRecordAuthorityError, match="column set"):
        parse_decision_rows(rows)


def test_the_build_never_writes_a_decision_back_into_the_proposal(evidence):
    """The two artifacts are independent evidence and must stay independently auditable."""
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(evidence.proposal_dir.iterdir())
        if path.is_file()
    }
    proposal = evidence.loaded_proposal()
    crosswalk_before = [dict(row) for row in proposal.crosswalk_rows]

    evidence.build()

    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(evidence.proposal_dir.iterdir())
        if path.is_file()
    }
    assert before == after
    assert [dict(row) for row in proposal.crosswalk_rows] == crosswalk_before
    for row in proposal.crosswalk_rows:
        assert row["review_status"] == "pending"
        assert row["reviewed_by"] == "" and row["reviewed_at"] == ""


# --- atomic publication ---------------------------------------------------------------------------------


def test_publication_writes_the_whole_bundle(evidence, tmp_path):
    authority = evidence.build()
    output = tmp_path / "authority"
    write_authority_package(authority, output)

    assert sorted(item.name for item in output.iterdir()) == sorted(
        [REGISTRY_FILENAME, MANIFEST_FILENAME, RECEIPT_FILENAME]
    )


def test_the_bundle_does_not_duplicate_the_crosswalk_as_a_second_file(evidence, tmp_path):
    """A separate crosswalk file would restate columns the registry already carries."""
    authority = evidence.build()
    output = tmp_path / "authority"
    write_authority_package(authority, output)

    assert not (output / "stable_record_crosswalk.csv").exists()
    assert {"legacy_source_record_id", "authority_source_row"} <= set(AUTHORITY_COLUMNS)


def test_publication_refuses_a_non_empty_destination(evidence, tmp_path):
    authority = evidence.build()
    output = tmp_path / "authority"
    output.mkdir()
    (output / "stray.txt").write_text("existing", encoding="utf-8")

    with pytest.raises(StableRecordAuthorityError, match="is not empty"):
        write_authority_package(authority, output)


def test_publication_refuses_to_overwrite_an_existing_authority(evidence, tmp_path):
    authority = evidence.build()
    output = tmp_path / "authority"
    write_authority_package(authority, output)
    before = (output / REGISTRY_FILENAME).read_bytes()

    with pytest.raises(StableRecordAuthorityError, match="refusing to overwrite"):
        write_authority_package(authority, output)
    assert (output / REGISTRY_FILENAME).read_bytes() == before


def test_publication_leaves_no_staging_directory_behind(evidence, tmp_path):
    authority = evidence.build()
    write_authority_package(authority, tmp_path / "authority")
    assert sorted(item.name for item in tmp_path.iterdir() if item.name.startswith(".")) == []


def test_a_staging_failure_publishes_nothing(evidence, tmp_path, monkeypatch):
    authority = evidence.build()
    output = tmp_path / "authority"

    import marketing_knowledge_agent.stable_record_authority as module

    def _boom(src, dst):
        raise OSError("simulated failure during publication")

    monkeypatch.setattr(module.os, "replace", _boom)

    with pytest.raises(OSError, match="simulated failure"):
        write_authority_package(authority, output)

    assert not output.exists()
    assert [item.name for item in tmp_path.iterdir() if item.name.startswith(".authority")] == []


def test_an_invalid_authority_is_refused_before_any_byte_is_written(evidence, tmp_path):
    authority = evidence.build()
    broken_rows = [dict(row) for row in authority.rows]
    broken_rows[0]["activation_status"] = "activated"
    broken = _replace_rows(authority, broken_rows)
    output = tmp_path / "authority"
    with pytest.raises(StableRecordAuthorityError, match="activation_status must be"):
        write_authority_package(broken, output)
    assert not output.exists()


def _replace_rows(authority, rows):
    return type(authority)(
        rows=tuple(rows),
        pins=authority.pins,
        record_count=authority.record_count,
        identity_continuation_count=authority.identity_continuation_count,
        new_identity_count=authority.new_identity_count,
        confidence_counts=authority.confidence_counts,
        asset_review_required_ids=authority.asset_review_required_ids,
        alias_decision_required_ids=authority.alias_decision_required_ids,
        payload_change_ids=authority.payload_change_ids,
        stable_id_set_digest=authority.stable_id_set_digest,
    )


def test_a_row_with_the_wrong_shape_is_refused_as_this_modules_error(evidence, tmp_path):
    """A malformed row must produce a refusal, not a KeyError from a later check.

    Every column check indexes by name, so a caller failing closed on this module's error type
    would otherwise see an uncaught KeyError instead.
    """
    authority = evidence.build()
    rows = [dict(row) for row in authority.rows]
    rows[0].pop("special_flags")
    output = tmp_path / "authority"

    with pytest.raises(StableRecordAuthorityError, match="column set"):
        write_authority_package(_replace_rows(authority, rows), output)
    assert not output.exists()


def test_a_row_carrying_an_unknown_flag_is_refused_at_validation(evidence, tmp_path):
    authority = evidence.build()
    rows = [dict(row) for row in authority.rows]
    rows[0]["special_flags"] = "SOME_FUTURE_FLAG"
    output = tmp_path / "authority"

    with pytest.raises(StableRecordAuthorityError, match="unrecognized flag"):
        write_authority_package(_replace_rows(authority, rows), output)
    assert not output.exists()


def test_the_production_authority_destination_is_refused_by_default(evidence, tmp_path):
    """A tmp_path mirror of the canonical production path, never the path itself."""
    authority = evidence.build()
    mirror = tmp_path.joinpath(*PRODUCTION_AUTHORITY_RELPATH)

    with pytest.raises(StableRecordAuthorityError, match="canonical production authority destination"):
        write_authority_package(authority, mirror)
    assert not mirror.exists()

    write_authority_package(authority, mirror, authorize_production_destination=True)
    assert (mirror / MANIFEST_FILENAME).is_file()


def test_a_dry_run_materialization_writes_nothing(evidence, tmp_path):
    authority, manifest = materialize_stable_record_authority(
        evidence.proposal_dir, evidence.decisions_path, evidence.pins
    )
    assert manifest is None
    assert authority.record_count == len(_SYNTHETIC_SPEC)
    assert sorted(item.name for item in tmp_path.iterdir()) == ["m1-proposal", "m2-final"]


# --- determinism -------------------------------------------------------------------------------------------


def test_two_builds_over_the_same_evidence_are_byte_identical(evidence, tmp_path):
    created_at = "2026-08-24T00:00:00+00:00"
    first = tmp_path / "first"
    second = tmp_path / "second"

    materialize_stable_record_authority(
        evidence.proposal_dir, evidence.decisions_path, evidence.pins, first, created_at
    )
    materialize_stable_record_authority(
        evidence.proposal_dir, evidence.decisions_path, evidence.pins, second, created_at
    )

    for filename in (REGISTRY_FILENAME, MANIFEST_FILENAME, RECEIPT_FILENAME):
        assert (first / filename).read_bytes() == (second / filename).read_bytes(), filename


def test_a_volatile_timestamp_moves_only_the_outer_seal(evidence, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    _authority_a, manifest_a = materialize_stable_record_authority(
        evidence.proposal_dir,
        evidence.decisions_path,
        evidence.pins,
        first,
        "2026-08-24T00:00:00+00:00",
    )
    _authority_b, manifest_b = materialize_stable_record_authority(
        evidence.proposal_dir,
        evidence.decisions_path,
        evidence.pins,
        second,
        "2026-09-01T12:34:56+00:00",
    )

    # The identity set and everything semantic about it is unchanged...
    assert (first / REGISTRY_FILENAME).read_bytes() == (second / REGISTRY_FILENAME).read_bytes()
    assert manifest_a["content_digest"] == manifest_b["content_digest"]
    assert manifest_a["stable_id_set_digest"] == manifest_b["stable_id_set_digest"]
    # ...and only the seal that deliberately covers created_at moves.
    assert manifest_a["manifest_hash"] != manifest_b["manifest_hash"]


def test_the_stable_id_set_digest_is_order_independent(evidence):
    authority = evidence.build()
    reordered = list(reversed(authority.rows))
    from marketing_knowledge_agent.stable_record_authority import _stable_id_set_digest

    assert _stable_id_set_digest(row["stable_record_id"] for row in reordered) == (
        authority.stable_id_set_digest
    )


def test_rows_are_emitted_in_ascending_stable_id_order(evidence):
    authority = evidence.build()
    ids = list(authority.stable_record_ids)
    assert ids == sorted(ids)


# --- formal M1/M2 evidence -----------------------------------------------------------------------------------


@formal_evidence
def test_the_formal_evidence_materializes_to_the_reviewed_identity_set(tmp_path):
    """Semantic compatibility with the M3C preview, against the real M1 and M2 artifacts.

    Byte equality with the preview is not expected — this is the production schema and the preview
    was a scratch one. Every semantic decision must agree.
    """
    authority, manifest = materialize_stable_record_authority(
        FORMAL_PROPOSAL_DIR,
        FORMAL_DECISIONS_PATH,
        FORMAL_PINS,
        tmp_path / "authority",
        "2026-08-24T00:00:00+00:00",
    )

    assert authority.record_count == FORMAL_RECORD_COUNT
    assert authority.identity_continuation_count == FORMAL_IDENTITY_CONTINUATIONS
    assert authority.new_identity_count == FORMAL_NEW_IDENTITIES

    new_ids = [
        row["stable_record_id"]
        for row in authority.rows
        if row["authority_record_status"] == AUTHORITY_RECORD_STATUS_NEW
    ]
    assert new_ids == [FORMAL_NEW_STABLE_RECORD_ID]

    assert authority.asset_review_required_ids == FORMAL_ASSET_REVIEW_IDS
    assert authority.alias_decision_required_ids == FORMAL_ALIAS_REVIEW_IDS
    assert authority.payload_change_ids == FORMAL_PAYLOAD_CHANGE_IDS

    assert manifest["reviewer"] == FORMAL_REVIEWER
    assert manifest["review_date"] == FORMAL_REVIEWED_AT
    assert manifest["confidence_counts"] == {"HIGH": 105, "MEDIUM": 15, "NEW": 1}
    assert manifest["stable_record_v2_activated"] is False
    assert manifest["row_v1_retired"] is False


@formal_evidence
def test_the_formal_evidence_pins_the_three_flag_semantics_to_exact_records(tmp_path):
    authority, _manifest = materialize_stable_record_authority(
        FORMAL_PROPOSAL_DIR, FORMAL_DECISIONS_PATH, FORMAL_PINS
    )

    payload_and_asset = _row_by_id(authority.rows, "MKA-MC-00014")
    assert parse_special_flags(payload_and_asset["special_flags"]) == (
        FLAG_PAYLOAD_CHANGE_PRESENT,
        FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,
    )
    assert payload_and_asset["asset_review_status"] == ASSET_REVIEW_REQUIRED_SEPARATELY
    assert payload_and_asset["payload_change_status"] == PAYLOAD_CHANGE_PRESENT_NOT_APPROVED
    assert payload_and_asset["alias_binding_status"] == ALIAS_BINDING_UNCHANGED

    asset_only = _row_by_id(authority.rows, "MKA-MC-00121")
    assert parse_special_flags(asset_only["special_flags"]) == (
        FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,
    )
    assert asset_only["asset_review_status"] == ASSET_REVIEW_REQUIRED_SEPARATELY
    assert asset_only["payload_change_status"] == PAYLOAD_CHANGE_NONE_RECORDED
    assert asset_only["authority_record_status"] == AUTHORITY_RECORD_STATUS_NEW
    assert asset_only["identity_origin"] == IDENTITY_ORIGIN_AUTHORITY_NEW

    alias_only = _row_by_id(authority.rows, "MKA-MC-00045")
    assert parse_special_flags(alias_only["special_flags"]) == (
        FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION,
    )
    assert alias_only["alias_binding_status"] == ALIAS_BINDING_REQUIRES_SEPARATE_DECISION
    assert alias_only["asset_review_status"] == ASSET_REVIEW_NOT_IN_SCOPE
    assert alias_only["payload_change_status"] == PAYLOAD_CHANGE_NONE_RECORDED
    # Identity continuation is decided; the SLP alias target deliberately is not.
    assert alias_only["authority_record_status"] == AUTHORITY_RECORD_STATUS_CONTINUATION
    assert alias_only["identity_origin"] == IDENTITY_ORIGIN_LEGACY_CONTINUATION


@formal_evidence
def test_the_formal_legacy_identities_are_qualified_by_workbook_lineage(tmp_path):
    authority, _manifest = materialize_stable_record_authority(
        FORMAL_PROPOSAL_DIR, FORMAL_DECISIONS_PATH, FORMAL_PINS
    )
    row = _row_by_id(authority.rows, "MKA-MC-00045")
    legacy_workbook = "9cbd93f1a754eb28aa358d74215445c5ffa3b1100dd947000aa9bed1b5c4ad2c"
    assert row["legacy_source_record_id"] == (
        f"row_v1:{legacy_workbook}:商家夥伴案例資料庫:r32"
    )
    # The bare key alone is the ambiguous one, and it never appears as an identity.
    assert row["legacy_source_row"] == "32"
    assert row["legacy_source_row_role"] == LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY


@formal_evidence
def test_the_formal_evidence_is_refused_under_a_wrong_pin(tmp_path):
    wrong = AuthorityEvidencePins(
        proposal_registry_sha256=FORMAL_PINS.proposal_registry_sha256,
        proposal_crosswalk_sha256=FORMAL_PINS.proposal_crosswalk_sha256,
        proposal_content_digest="0" * 64,
        proposal_manifest_hash=FORMAL_PINS.proposal_manifest_hash,
        decision_artifact_sha256=FORMAL_PINS.decision_artifact_sha256,
        reviewer=FORMAL_REVIEWER,
        reviewed_at=FORMAL_REVIEWED_AT,
    )
    with pytest.raises(StableRecordAuthorityError, match="not the one that was reviewed"):
        materialize_stable_record_authority(FORMAL_PROPOSAL_DIR, FORMAL_DECISIONS_PATH, wrong)


# --- F1: a backup pin is a check, not a decoration ---------------------------------------------------
#
# The engine used to copy ``backup_manifest_sha256`` from the pins straight into the manifest. The
# published package then carried a hash nothing had ever compared against a file — indistinguishable,
# to the reader it exists for, from one that had been verified. These tests hold the two apart.


def test_a_backup_pin_without_a_location_is_refused(evidence):
    with pytest.raises(StableRecordAuthorityError, match="backup_manifest_path is empty"):
        _pins_with(evidence, backup_manifest_sha256="c" * 64)


def test_a_backup_location_without_a_pin_is_refused(evidence, tmp_path):
    backup = _write_backup(tmp_path)
    with pytest.raises(StableRecordAuthorityError, match="backup_manifest_sha256 is empty"):
        _pins_with(evidence, backup_manifest_path=str(backup))


def test_a_wrong_backup_pin_is_refused(evidence, tmp_path):
    backup = _write_backup(tmp_path)
    pins = _pins_with(
        evidence, backup_manifest_sha256="c" * 64, backup_manifest_path=str(backup)
    )
    with pytest.raises(StableRecordAuthorityError, match="backup manifest .* sha256"):
        evidence.build(override_pins=pins)


def test_a_backup_artifact_that_moved_after_pinning_is_refused(evidence, tmp_path):
    backup = _write_backup(tmp_path)
    pins = _pins_with(
        evidence, backup_manifest_sha256=_sha256(backup), backup_manifest_path=str(backup)
    )
    backup.unlink()
    with pytest.raises(StableRecordAuthorityError, match="pinned but missing"):
        evidence.build(override_pins=pins)


def test_a_backup_edited_after_pinning_is_refused(evidence, tmp_path):
    backup = _write_backup(tmp_path)
    pins = _pins_with(
        evidence, backup_manifest_sha256=_sha256(backup), backup_manifest_path=str(backup)
    )
    backup.write_bytes(b'{"backup": "a different backup entirely"}\n')
    with pytest.raises(StableRecordAuthorityError, match="backup manifest .* sha256"):
        evidence.build(override_pins=pins)


def test_verify_backup_evidence_returns_false_only_when_nothing_was_pinned(evidence, tmp_path):
    assert verify_backup_evidence(evidence.pins) is False

    backup = _write_backup(tmp_path)
    pins = _pins_with(
        evidence, backup_manifest_sha256=_sha256(backup), backup_manifest_path=str(backup)
    )
    assert verify_backup_evidence(pins) is True


def test_an_unpinned_backup_never_reads_as_a_passed_gate(evidence, tmp_path):
    """Absent evidence is recorded as absent. ``PASS`` by omission is the failure mode."""
    authority = evidence.build()
    output = tmp_path / "authority"
    manifest = write_authority_package(authority, output)

    assert manifest["backup_evidence"]["backup_manifest_verified"] is False
    assert manifest["backup_evidence"]["verification_status"] == BACKUP_EVIDENCE_NOT_SUPPLIED
    assert manifest["backup_evidence"]["m3_backup_gate"] == M3_BACKUP_GATE_NOT_ASSERTED
    assert manifest["backup_evidence"]["backup_manifest_sha256"] == ""

    receipt = _read_receipt(output)
    assert receipt["backup_evidence"]["backup_manifest_verified"] is False
    assert receipt["backup_evidence"]["m3_backup_gate"] == M3_BACKUP_GATE_NOT_ASSERTED


def test_a_wrong_backup_pin_is_refused_by_the_writer_too(evidence, tmp_path):
    """The writer is a public entry point, so it cannot rely on the builder having checked."""
    import dataclasses

    backup = _write_backup(tmp_path)
    good = _pins_with(
        evidence, backup_manifest_sha256=_sha256(backup), backup_manifest_path=str(backup)
    )
    authority = evidence.build(override_pins=good)

    backup.write_bytes(b'{"backup": "swapped after the build"}\n')
    output = tmp_path / "authority"
    with pytest.raises(StableRecordAuthorityError, match="backup manifest .* sha256"):
        write_authority_package(authority, output)
    assert not output.exists()

    unpinned_path = dataclasses.replace(
        authority, pins=_pins_with(evidence)
    )
    write_authority_package(unpinned_path, tmp_path / "unpinned")


def test_a_backup_pin_is_refused_at_every_entry_point(evidence, tmp_path):
    backup = _write_backup(tmp_path)
    pins = _pins_with(
        evidence, backup_manifest_sha256="d" * 64, backup_manifest_path=str(backup)
    )
    with pytest.raises(StableRecordAuthorityError, match="backup manifest .* sha256"):
        materialize_stable_record_authority(evidence.proposal_dir, evidence.decisions_path, pins)
    with pytest.raises(StableRecordAuthorityError, match="backup manifest .* sha256"):
        materialize_stable_record_authority(
            evidence.proposal_dir, evidence.decisions_path, pins, tmp_path / "authority"
        )
    assert not (tmp_path / "authority").exists()


# --- F8: a supplied pin cannot be skipped -------------------------------------------------------------


def test_there_is_no_parameter_that_skips_companion_verification():
    """The bypass this replaces defaulted to on, so every effective use of it published a lie."""
    parameters = inspect.signature(materialize_stable_record_authority).parameters
    assert "verify_companions" not in parameters


def test_passing_the_removed_bypass_is_an_error_rather_than_a_silent_no_op(evidence):
    with pytest.raises(TypeError):
        materialize_stable_record_authority(
            evidence.proposal_dir,
            evidence.decisions_path,
            evidence.pins,
            verify_companions=False,
        )


def test_a_companion_pin_without_a_location_is_refused(evidence):
    for _filename, attr in COMPANION_ARTIFACTS:
        with pytest.raises(StableRecordAuthorityError, match="decision_companion_dir is empty"):
            _pins_with(evidence, **{attr: "e" * 64})


def test_a_companion_location_without_a_pin_is_refused(evidence):
    with pytest.raises(StableRecordAuthorityError, match="no companion artifact is pinned"):
        _pins_with(evidence, decision_companion_dir=str(evidence.decisions_path.parent))


@pytest.mark.parametrize("filename, attr", list(COMPANION_ARTIFACTS))
def test_a_wrong_companion_pin_is_refused_in_every_api_combination(
    evidence, tmp_path, filename, attr
):
    kwargs = _write_companions(evidence)
    kwargs[attr] = "a" * 64
    pins = _pins_with(evidence, **kwargs)

    # 1. the builder
    with pytest.raises(StableRecordAuthorityError, match="companion artifact .* sha256"):
        build_stable_record_authority(
            evidence.loaded_proposal(evidence.pins), evidence.decisions(evidence.pins), pins
        )
    # 2. the dry run
    with pytest.raises(StableRecordAuthorityError, match="companion artifact .* sha256"):
        materialize_stable_record_authority(evidence.proposal_dir, evidence.decisions_path, pins)
    # 3. the publishing run
    with pytest.raises(StableRecordAuthorityError, match="companion artifact .* sha256"):
        materialize_stable_record_authority(
            evidence.proposal_dir, evidence.decisions_path, pins, tmp_path / "a"
        )
    # 4. the publishing run against the canonical destination, authorized
    with pytest.raises(StableRecordAuthorityError, match="companion artifact .* sha256"):
        materialize_stable_record_authority(
            evidence.proposal_dir,
            evidence.decisions_path,
            pins,
            tmp_path.joinpath(*PRODUCTION_AUTHORITY_RELPATH),
            authorize_production_destination=True,
        )
    # 5. the writer, handed an authority built while the artifact still matched
    import dataclasses

    good = _pins_with(evidence, **_write_companions(evidence))
    authority = evidence.build(override_pins=good)
    (evidence.decisions_path.parent / filename).write_bytes(b'{"artifact": "swapped"}\n')
    with pytest.raises(StableRecordAuthorityError, match="companion artifact .* sha256"):
        write_authority_package(dataclasses.replace(authority, pins=good), tmp_path / "b")

    assert not (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()
    assert not (tmp_path / "data").exists()


def test_every_supplied_companion_pin_is_reported_as_verified(evidence, tmp_path):
    pins = _pins_with(evidence, **_write_companions(evidence))
    authority = evidence.build(override_pins=pins)
    manifest = write_authority_package(authority, tmp_path / "authority")

    assert manifest["source_decision_artifact"]["companion_artifacts_verified"] == [
        name for name, _attr in COMPANION_ARTIFACTS
    ]


def test_a_partially_pinned_companion_set_reports_only_what_it_verified(evidence, tmp_path):
    only = COMPANION_ARTIFACTS[1][0]
    pins = _pins_with(evidence, **_write_companions(evidence, filenames=[only]))
    authority = evidence.build(override_pins=pins)
    manifest = write_authority_package(authority, tmp_path / "authority")

    assert manifest["source_decision_artifact"]["companion_artifacts_verified"] == [only]
    assert verify_companion_artifacts(pins) == (only,)


def test_verify_supplied_evidence_covers_exactly_what_was_pinned(evidence, tmp_path):
    backup = _write_backup(tmp_path)
    kwargs = _write_companions(evidence)
    kwargs.update(
        backup_manifest_sha256=_sha256(backup), backup_manifest_path=str(backup)
    )
    verified = verify_supplied_evidence(_pins_with(evidence, **kwargs))

    assert verified.backup_manifest_verified is True
    assert sorted(verified.companion_artifacts) == sorted(
        name for name, _attr in COMPANION_ARTIFACTS
    )

    bare = verify_supplied_evidence(_pins_with(evidence))
    assert bare.companion_artifacts == ()
    assert bare.backup_manifest_verified is False


def test_a_pinned_companion_directory_that_does_not_exist_is_refused(evidence, tmp_path):
    kwargs = _write_companions(evidence)
    kwargs["decision_companion_dir"] = str(tmp_path / "nowhere")
    pins = _pins_with(evidence, **kwargs)
    with pytest.raises(StableRecordAuthorityError, match="is not a directory"):
        evidence.build(override_pins=pins)


def _publish(evidence, tmp_path, name="authority", pins=None, **kwargs):
    authority = evidence.build(override_pins=pins)
    output = tmp_path / name
    write_authority_package(authority, output, **kwargs)
    return output


# --- F2: the production destination is a subtree, not a filename -----------------------------------
#
# The old guard compared only the trailing path components, so it answered "is this path *named*
# .../stable_record_v2?". A dated publication is never named that. Every case below is a directory
# an activation run would plausibly write to, and every one of them used to be unguarded.
#
# Every path here is a tmp_path mirror. This work package publishes nothing to the real tree.


@pytest.mark.parametrize(
    "descendant",
    [
        pytest.param((), id="the-canonical-root-itself"),
        pytest.param(("2026-08-24",), id="a-dated-child"),
        pytest.param(("v3",), id="a-version-child"),
        pytest.param(("2026-08-24", "batch-1", "rows"), id="a-deep-descendant"),
    ],
)
def test_the_production_root_and_every_descendant_are_refused(evidence, tmp_path, descendant):
    authority = evidence.build()
    target = tmp_path.joinpath(*PRODUCTION_AUTHORITY_RELPATH).joinpath(*descendant)

    with pytest.raises(
        StableRecordAuthorityError, match="canonical production authority destination"
    ):
        write_authority_package(authority, target)

    # The refusal precedes every mkdir, so a refused destination leaves no parent behind either.
    assert not (tmp_path / "data").exists()


def test_a_relative_production_descendant_is_refused(evidence, tmp_path, monkeypatch):
    """Resolution comes first, so a relative path cannot arrive there under another name."""
    authority = evidence.build()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        StableRecordAuthorityError, match="canonical production authority destination"
    ):
        write_authority_package(authority, Path(*PRODUCTION_AUTHORITY_RELPATH) / "2026-08-24")

    assert not (tmp_path / "data").exists()


def test_a_symlinked_production_descendant_is_refused(evidence, tmp_path):
    authority = evidence.build()
    mirror = tmp_path.joinpath(*PRODUCTION_AUTHORITY_RELPATH)
    mirror.mkdir(parents=True)
    link = tmp_path / "elsewhere"
    link.symlink_to(mirror, target_is_directory=True)

    with pytest.raises(
        StableRecordAuthorityError, match="canonical production authority destination"
    ):
        write_authority_package(authority, link / "2026-08-24")

    assert sorted(item.name for item in mirror.iterdir()) == []


@pytest.mark.parametrize(
    "leaf", ["stable_record_v2_preview", "stable_record_v3", "stable_record_v2_backup"]
)
def test_a_near_miss_of_the_production_name_is_not_refused(evidence, tmp_path, leaf):
    """Containment is compared by whole path component, so a neighbouring directory is not caught."""
    authority = evidence.build()
    target = tmp_path / "data" / "identity" / "authority" / leaf
    write_authority_package(authority, target)
    assert (target / MANIFEST_FILENAME).is_file()


def test_an_authorized_production_descendant_publishes(evidence, tmp_path):
    authority = evidence.build()
    target = tmp_path.joinpath(*PRODUCTION_AUTHORITY_RELPATH) / "2026-08-24"
    write_authority_package(authority, target, authorize_production_destination=True)
    assert (target / MANIFEST_FILENAME).is_file()


def test_the_real_production_authority_tree_does_not_exist():
    """This work package materializes nothing to the real tree, and nothing here creates it."""
    assert not _REPO_ROOT.joinpath(*PRODUCTION_AUTHORITY_RELPATH).exists()
    assert not (_REPO_ROOT / "data" / "identity" / "authority").exists()


# --- F3 and F4: the decision CSV has an exact row shape ---------------------------------------------
#
# ``csv.DictReader`` fails in two opposite and equally silent ways here. A short row is padded with
# ``restval``, so it passes a column-set check carrying ``None`` where a decision belongs; a long
# row lands under a ``None`` restkey, so reporting the mismatch raises ``TypeError`` instead of this
# module's error. Both are refusals now, and both are refusals of the same kind.


def test_the_decision_contract_is_exactly_twenty_two_columns():
    assert len(DECISION_COLUMNS) == 22


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda fields: fields[:-1], id="missing-one-trailing-field"),
        pytest.param(lambda fields: fields[:-2], id="missing-two-trailing-fields"),
        pytest.param(lambda fields: fields + ["extra"], id="one-extra-field"),
        pytest.param(lambda fields: fields + ["extra", "another"], id="two-extra-fields"),
        pytest.param(lambda fields: fields[:3], id="a-short-row"),
        pytest.param(lambda fields: fields + ["x"] * 5, id="a-long-row"),
        pytest.param(lambda fields: [], id="an-empty-row"),
    ],
)
def test_a_decision_row_of_the_wrong_width_is_refused(evidence, mutate):
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    rows[1] = mutate(rows[1])
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows))

    with pytest.raises(StableRecordAuthorityError, match="but the decision contract is exactly 22"):
        load_decision_artifact(evidence.decisions_path, pins)


def test_a_blank_line_between_decisions_is_refused(evidence):
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    rows.insert(1, None)
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows))

    with pytest.raises(StableRecordAuthorityError, match="but the decision contract is exactly 22"):
        load_decision_artifact(evidence.decisions_path, pins)


def test_an_extra_field_is_this_modules_refusal_and_not_a_typeerror(evidence):
    """The DictReader restkey of ``None`` made ``sorted(row)`` raise, which no caller catches."""
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    rows[0] = rows[0] + ["a twenty-third value"]
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows))

    with pytest.raises(StableRecordAuthorityError):
        load_decision_artifact(evidence.decisions_path, pins)


def test_a_truncated_row_is_not_quietly_completed(evidence):
    """A padded row used to materialize, carrying the literal string ``None`` as a decision."""
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    rows[0] = rows[0][:-2]
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows))

    with pytest.raises(StableRecordAuthorityError, match="short row leaves a decision undefined"):
        load_decision_artifact(evidence.decisions_path, pins)


def test_a_refusal_never_quotes_the_row_it_refused(evidence):
    """The artifact carries the merchant roster and free-text notes. A refusal is not a licence."""
    secret = "REVIEW-NOTE-DO-NOT-LEAK-某某品牌-2026"
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    rows[0] = _decision_fields(evidence, 0, review_note=secret) + ["extra"]
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows))

    with pytest.raises(StableRecordAuthorityError) as excinfo:
        load_decision_artifact(evidence.decisions_path, pins)
    assert secret not in str(excinfo.value)


def test_a_decision_header_with_a_renamed_column_is_refused(evidence):
    header = list(DECISION_COLUMNS)
    header[header.index("review_note")] = "reviewer_comment"
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows, header=header))

    with pytest.raises(StableRecordAuthorityError, match="do not match"):
        load_decision_artifact(evidence.decisions_path, pins)


def test_a_decision_header_in_the_wrong_order_is_refused(evidence):
    header = list(DECISION_COLUMNS)
    header[1], header[2] = header[2], header[1]
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows, header=header))

    with pytest.raises(StableRecordAuthorityError, match="exactly and in order"):
        load_decision_artifact(evidence.decisions_path, pins)


def test_a_decision_header_with_an_extra_column_is_refused(evidence):
    header = list(DECISION_COLUMNS) + ["reviewer_mood"]
    rows = [_decision_fields(evidence, index) + ["fine"] for index in range(len(_SYNTHETIC_SPEC))]
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows, header=header))

    with pytest.raises(StableRecordAuthorityError, match="do not match"):
        load_decision_artifact(evidence.decisions_path, pins)


def test_an_empty_decision_artifact_is_refused():
    with pytest.raises(StableRecordAuthorityError, match="is empty"):
        read_decision_csv(b"", source="synthetic")


def test_a_decision_artifact_with_a_header_and_no_rows_is_refused():
    header = _csv_line(list(DECISION_COLUMNS)).encode("utf-8") + b"\r\n"
    with pytest.raises(StableRecordAuthorityError, match="no decision rows"):
        read_decision_csv(header, source="synthetic")


def test_a_decision_artifact_that_is_not_utf8_is_refused():
    with pytest.raises(StableRecordAuthorityError, match="not valid UTF-8"):
        read_decision_csv(b"\xff\xfe not utf-8 at all", source="synthetic")


def test_an_exactly_shaped_decision_artifact_still_loads(evidence):
    rows = [_decision_fields(evidence, index) for index in range(len(_SYNTHETIC_SPEC))]
    pins = evidence.write_raw_decisions(_decision_csv(evidence, rows))

    decisions = load_decision_artifact(evidence.decisions_path, pins)
    assert [decision.stable_record_id for decision in decisions] == [
        spec[0] for spec in _SYNTHETIC_SPEC
    ]


# --- F5: the receipt is sealed, not merely present ---------------------------------------------------
#
# The receipt could previously be edited or deleted and the package would still load. It is the file
# that records what was materialized and under whose decision, so an unsealed one is an audit trail
# an operator can rewrite. It is now bound three ways: the manifest seals its bytes, it seals its own
# body, and the two are cross-checked for agreement.


def test_the_manifest_seals_the_receipt_bytes(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    manifest, _rows = load_authority_package(output)
    assert manifest[MANIFEST_RECEIPT_SHA256_FIELD] == _sha256(output / RECEIPT_FILENAME)


def test_a_deleted_receipt_is_refused(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    (output / RECEIPT_FILENAME).unlink()

    with pytest.raises(StableRecordAuthorityError, match="receipt .* is missing"):
        load_authority_package(output)


def test_a_receipt_whose_package_state_was_edited_is_refused(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    receipt = _read_receipt(output)
    receipt[PACKAGE_STATE_FIELD][STABLE_RECORD_V2_ACTIVATED_FIELD] = True
    _rewrite_receipt(output, receipt)

    with pytest.raises(StableRecordAuthorityError, match="does not match the manifest"):
        load_authority_package(output)


def test_a_receipt_whose_identity_digest_was_edited_is_refused(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    receipt = _read_receipt(output)
    receipt["stable_id_set_digest"] = "0" * 64
    _rewrite_receipt(output, receipt)

    with pytest.raises(StableRecordAuthorityError, match="does not match the manifest"):
        load_authority_package(output)


def test_a_receipt_resealed_only_to_itself_is_still_refused(evidence, tmp_path):
    """Re-deriving the receipt's own hash proves the file is well-formed, not that it is the one
    published. The manifest seals the bytes, and the bytes changed."""
    output = _publish(evidence, tmp_path)
    receipt = _read_receipt(output)
    receipt["record_count"] = 999
    _rewrite_receipt(output, receipt, reseal_receipt=True)

    with pytest.raises(StableRecordAuthorityError, match="does not match the manifest"):
        load_authority_package(output)


def test_a_manifest_whose_receipt_pointer_moved_without_a_reseal_is_refused(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    manifest_path = output / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[MANIFEST_RECEIPT_SHA256_FIELD] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StableRecordAuthorityError, match="does not match its contents"):
        load_authority_package(output)


def test_a_manifest_that_declares_no_receipt_pointer_is_refused(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    manifest_path = output / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop(MANIFEST_RECEIPT_SHA256_FIELD)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StableRecordAuthorityError, match="declares no receipt_sha256"):
        load_authority_package(output)


def test_a_receipt_and_manifest_resealed_together_must_still_agree(evidence, tmp_path):
    """The last defence: an operator who re-derives *both* seals still has to make the two files
    say the same thing about the same package."""
    output = _publish(evidence, tmp_path)
    receipt = _read_receipt(output)
    receipt[PACKAGE_STATE_FIELD][STABLE_RECORD_V2_ACTIVATED_FIELD] = True
    _rewrite_receipt(output, receipt, reseal_receipt=True, reseal_manifest=True)

    with pytest.raises(
        StableRecordAuthorityError, match="disagree on stable_record_v2_activated"
    ):
        load_authority_package(output)


def test_a_receipt_missing_its_package_state_is_refused(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    receipt = _read_receipt(output)
    receipt.pop(PACKAGE_STATE_FIELD)
    _rewrite_receipt(output, receipt, reseal_receipt=True, reseal_manifest=True)

    with pytest.raises(StableRecordAuthorityError, match="declares no package_state"):
        load_authority_package(output)


def test_the_receipt_self_seal_reproduces(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    verify_receipt_integrity(_read_receipt(output))


def test_a_receipt_without_a_self_seal_is_refused(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    receipt = _read_receipt(output)
    receipt.pop(RECEIPT_HASH_FIELD)

    with pytest.raises(StableRecordAuthorityError, match="missing or malformed receipt_hash"):
        verify_receipt_integrity(receipt)


def test_the_two_seals_are_not_mutually_recursive(evidence, tmp_path):
    """The receipt quotes the manifest's semantic identity; the manifest seals the receipt's bytes.

    If ``content_digest`` also covered ``receipt_sha256``, each value would be an input to the other
    and neither would have a fixed point. Excluding the pointer from the semantic digest is what
    breaks the cycle, and ``manifest_hash`` still covers it, so the receipt stays sealed.
    """
    from marketing_knowledge_agent.stable_record_authority import content_digest_body

    output = _publish(evidence, tmp_path)
    manifest = json.loads((output / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    receipt = _read_receipt(output)

    assert receipt["manifest_content_digest"] == manifest[MANIFEST_CONTENT_DIGEST_FIELD]
    assert MANIFEST_HASH_FIELD not in receipt
    assert MANIFEST_RECEIPT_SHA256_FIELD not in content_digest_body(manifest)
    assert MANIFEST_RECEIPT_SHA256_FIELD in manifest
    # The outer seal is computed over the pointer, so the receipt bytes are covered by it.
    manifest_without_pointer = dict(manifest)
    manifest_without_pointer[MANIFEST_RECEIPT_SHA256_FIELD] = "0" * 64
    from marketing_knowledge_agent.stable_record_authority import compute_manifest_hash

    assert compute_manifest_hash(manifest_without_pointer) != manifest[MANIFEST_HASH_FIELD]


def test_the_receipt_carries_no_destination_and_no_timestamp(evidence, tmp_path):
    output = _publish(evidence, tmp_path, name="a-very-distinctive-destination-name")
    payload = (output / RECEIPT_FILENAME).read_bytes()

    assert b"a-very-distinctive-destination-name" not in payload
    assert b"created_at" not in payload


# --- F6: package scope is not the project governance gate --------------------------------------------


def test_no_published_file_asserts_the_project_governance_gate(evidence, tmp_path):
    """``AUTHORITY_MATERIALIZED: YES`` names a project gate that is still NO, and only a governance
    decision moves it. A tmp bundle carrying that sentence would be read as the gate having flipped.
    """
    output = _publish(evidence, tmp_path)
    for name, payload in _bundle_bytes(output).items():
        assert b"AUTHORITY_MATERIALIZED" not in payload, name
        assert b"gate_state" not in payload, name


def test_the_package_state_is_named_for_the_package(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    state = _read_receipt(output)[PACKAGE_STATE_FIELD]

    assert state[PACKAGE_MATERIALIZED_FIELD] is True
    assert state[STABLE_RECORD_V2_ACTIVATED_FIELD] is False
    assert state[ROW_V1_RETIRED_FIELD] is False
    assert state[PRODUCTION_REINDEX_AUTHORIZED_FIELD] is False
    assert all(not key.isupper() for key in state)


def test_the_materialized_not_activated_semantics_survive_the_rename(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    manifest, rows = load_authority_package(output)

    assert manifest["authority_status"] == AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED
    assert manifest["activation_status"] == ACTIVATION_STATUS_NOT_ACTIVATED
    assert manifest[PACKAGE_MATERIALIZED_FIELD] is True
    assert manifest[STABLE_RECORD_V2_ACTIVATED_FIELD] is False
    assert manifest[ROW_V1_RETIRED_FIELD] is False
    assert manifest[PRODUCTION_REINDEX_AUTHORIZED_FIELD] is False
    for row in rows:
        assert row["authority_status"] == AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED
        assert row["row_v1_status"] == ROW_V1_STATUS_RETAINED


# --- F7: activation still needs an external pin, and the package says so ------------------------------


def test_the_package_records_activation_as_still_requiring_an_external_pin(evidence, tmp_path):
    output = _publish(evidence, tmp_path)
    manifest, _rows = load_authority_package(output)
    receipt = _read_receipt(output)

    for document in (manifest, receipt):
        trust = document[ACTIVATION_TRUST_FIELD]
        assert trust["self_validation_is_not_activation_trust"] is True
        assert trust["authority_output_external_pin"] == AUTHORITY_OUTPUT_EXTERNAL_PIN_REQUIRED


def test_loading_a_package_is_documented_as_not_an_activation_decision():
    """A future activation work package must not read a successful load as authorization."""
    doc = load_authority_package.__doc__ or ""
    assert "not an activation trust decision" in doc
    assert AUTHORITY_OUTPUT_EXTERNAL_PIN_REQUIRED in doc


def test_a_wholly_rewritten_package_still_loads_which_is_why_activation_needs_more(
    evidence, tmp_path
):
    """Stated as a test rather than a comment: self-validation cannot detect this, by construction.

    An operator who rebuilds all three files from altered inputs produces a package that passes
    every internal check, because there is nothing left inside the directory to disagree with. That
    is exactly the gap an external pin of the authority output closes, and it is still open.
    """
    output = _publish(evidence, tmp_path)
    manifest_before, _rows = load_authority_package(output)

    rebuilt = tmp_path / "rebuilt"
    authority = evidence.build()
    write_authority_package(authority, rebuilt, created_at="2031-01-01T00:00:00+00:00")
    manifest_after, _rows_after = load_authority_package(rebuilt)

    assert manifest_after[MANIFEST_HASH_FIELD] != manifest_before[MANIFEST_HASH_FIELD]
    # Both load. Only an external pin distinguishes them.
    assert manifest_after[MANIFEST_CONTENT_DIGEST_FIELD] == manifest_before[
        MANIFEST_CONTENT_DIGEST_FIELD
    ]


# --- determinism across the new schema -----------------------------------------------------------------


def test_all_three_files_are_byte_identical_across_destinations(evidence, tmp_path):
    created_at = "2026-08-24T00:00:00+00:00"
    first = tmp_path / "destination-alpha"
    second = tmp_path / "destination-beta"

    materialize_stable_record_authority(
        evidence.proposal_dir, evidence.decisions_path, evidence.pins, first, created_at
    )
    materialize_stable_record_authority(
        evidence.proposal_dir, evidence.decisions_path, evidence.pins, second, created_at
    )

    for filename in (REGISTRY_FILENAME, MANIFEST_FILENAME, RECEIPT_FILENAME):
        assert (first / filename).read_bytes() == (second / filename).read_bytes(), filename

    # The destination never enters the canonical bytes.
    for payload in _bundle_bytes(first).values():
        assert b"destination-alpha" not in payload
        assert str(first).encode("utf-8") not in payload


def test_a_volatile_timestamp_leaves_the_receipt_untouched(evidence, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    _a, manifest_a = materialize_stable_record_authority(
        evidence.proposal_dir,
        evidence.decisions_path,
        evidence.pins,
        first,
        "2026-08-24T00:00:00+00:00",
    )
    _b, manifest_b = materialize_stable_record_authority(
        evidence.proposal_dir,
        evidence.decisions_path,
        evidence.pins,
        second,
        "2026-09-01T12:34:56+00:00",
    )

    assert (first / RECEIPT_FILENAME).read_bytes() == (second / RECEIPT_FILENAME).read_bytes()
    assert manifest_a[MANIFEST_RECEIPT_SHA256_FIELD] == manifest_b[MANIFEST_RECEIPT_SHA256_FIELD]
    assert manifest_a[MANIFEST_CONTENT_DIGEST_FIELD] == manifest_b[MANIFEST_CONTENT_DIGEST_FIELD]
    assert manifest_a["stable_id_set_digest"] == manifest_b["stable_id_set_digest"]
    assert manifest_a["registry_sha256"] == manifest_b["registry_sha256"]
    # Only the seal that deliberately covers created_at moves.
    assert manifest_a[MANIFEST_HASH_FIELD] != manifest_b[MANIFEST_HASH_FIELD]


# --- formal evidence: the backup gate is re-verified ------------------------------------------------


@formal_evidence
def test_the_formal_backup_manifest_is_verified_before_publication(tmp_path):
    assert _sha256(FORMAL_BACKUP_MANIFEST_PATH) == FORMAL_BACKUP_MANIFEST_SHA256

    _authority, manifest = materialize_stable_record_authority(
        FORMAL_PROPOSAL_DIR,
        FORMAL_DECISIONS_PATH,
        FORMAL_PINS,
        tmp_path / "authority",
        "2026-08-24T00:00:00+00:00",
    )

    assert manifest["backup_evidence"]["backup_manifest_sha256"] == FORMAL_BACKUP_MANIFEST_SHA256
    assert manifest["backup_evidence"]["backup_manifest_verified"] is True
    assert manifest["backup_evidence"]["m3_backup_gate"] == M3_BACKUP_GATE_PASS
    assert manifest["source_decision_artifact"]["companion_artifacts_verified"] == [
        name for name, _attr in COMPANION_ARTIFACTS
    ]

    loaded, rows = load_authority_package(tmp_path / "authority")
    assert loaded[MANIFEST_HASH_FIELD] == manifest[MANIFEST_HASH_FIELD]
    assert len(rows) == FORMAL_RECORD_COUNT


@formal_evidence
def test_the_formal_evidence_is_refused_under_a_wrong_backup_pin(tmp_path):
    import dataclasses

    wrong = dataclasses.replace(FORMAL_PINS, backup_manifest_sha256="0" * 64)
    with pytest.raises(StableRecordAuthorityError, match="backup manifest .* sha256"):
        materialize_stable_record_authority(FORMAL_PROPOSAL_DIR, FORMAL_DECISIONS_PATH, wrong)


@formal_evidence
def test_the_formal_package_carries_no_governance_gate_assertion(tmp_path):
    materialize_stable_record_authority(
        FORMAL_PROPOSAL_DIR,
        FORMAL_DECISIONS_PATH,
        FORMAL_PINS,
        tmp_path / "authority",
        "2026-08-24T00:00:00+00:00",
    )
    for name, payload in _bundle_bytes(tmp_path / "authority").items():
        assert b"AUTHORITY_MATERIALIZED" not in payload, name
