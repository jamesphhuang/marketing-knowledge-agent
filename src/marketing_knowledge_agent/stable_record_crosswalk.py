"""Stable-record crosswalk proposal builder (M1, proposal-only).

Merchant-case review decisions are still keyed by Excel row coordinate (``row_v1``). A coordinate
states *where* a record sat, never *which* record it was, so a single inserted row silently
re-points every downstream decision at a different merchant. ``record_identity_lineage`` turns
that into a loud refusal; this module is the first step of the migration that removes the hazard
at the source, by proposing a durable ``stable_record_id`` for every merchant-case record.

What this module produces is a **proposal**, never an authority:

* It mints ``MKA-MC-#####`` identifiers into a disposable output directory.
* Every row it emits carries ``review_status=pending``. Nothing here is approved, and a HIGH
  confidence match is still pending.
* The manifest it writes declares ``authority_status=proposal_only``. It never declares
  ``record_identity_scheme_version=stable_record_v2``; until cutover, ``row_v1`` remains the only
  authoritative mutation key and ``stable_record_id`` may only be shadow-observed.

It reads two workbooks and (optionally, read-only) the governance decision store. It writes
nothing except the proposal directory it is given.

Design constraints worth stating because violating them is silently destructive:

Fail closed, never guess.
    ``brand + year`` is *migration candidate discovery only* — never a stable identity key, never
    a runtime join key. Where that key is ambiguous on either side, or where handles conflict, the
    record is reported AMBIGUOUS rather than resolved by picking the first, nearest, or
    row-adjacent candidate. Row shift is recorded as secondary diagnostic evidence and is never
    allowed to establish a match.

Identity change and payload change are different things.
    A record whose video moved from "審核中" to a real URL is the *same record*. Payload changes
    are reported in ``payload_change_fields`` and deliberately do not lower identity confidence
    unless the field that moved is itself identity evidence.

Assignment happens once.
    Stable IDs are derived from frozen legacy lineage, ordered by that derivation, and assigned in
    that order. They are never renumbered because a brand, handle, or row moved. ``verify_existing``
    re-derives a proposal and refuses any regeneration that would move an already-issued ID.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .excel_ingestion import INVALID_ASSET_VALUES, NULL_LIKE_VALUES, SHEET_MERCHANT_CASES
from .excel_preview import (
    EXPECTED_HEADER_ROWS,
    EXPECTED_SHEET_HEADERS,
    _normalized_headers_for,
    _table_rows,
    read_xlsx_workbook,
)


# --- versioned contracts ----------------------------------------------------------------------

SCHEMA_VERSION = 1

# Bumping this is a statement that evidence compares differently than it did before, which can move
# records between confidence classes. It is recorded on every crosswalk row.
NORMALIZATION_VERSION = "stable-record-evidence-normalization/v1"

MIGRATION_VERSION = "stable-record-crosswalk/m1-2026-08-21"

# The proposal is not the successor authority. A consumer that finds this value must treat every
# identifier in the directory as unissued.
AUTHORITY_STATUS_PROPOSAL_ONLY = "proposal_only"
RECORD_IDENTITY_SCHEME_STATUS_NOT_ACTIVATED = "not_activated"
PROPOSED_SUCCESSOR_SCHEME = "stable_record_v2"

# Only a directory whose manifest states this is loadable. A run interrupted before publication
# leaves no manifest at all, so a half-written directory can never be mistaken for a proposal.
PROPOSAL_STATE_COMPLETE = "complete"

REGISTRY_FILENAME = "stable_record_registry.csv"
CROSSWALK_FILENAME = "crosswalk_row_v1_to_v2.csv"
MANIFEST_FILENAME = "manifest.json"


# --- stable identifier format -----------------------------------------------------------------

STABLE_ID_PREFIX = "MKA-MC-"
STABLE_ID_DIGITS = 5
STABLE_ID_RE = re.compile(r"^MKA-MC-\d{5}$")
STABLE_ID_MAX = 10 ** STABLE_ID_DIGITS - 1

RECORD_TYPE_MERCHANT_CASE = "merchant_case"
LIFECYCLE_STATE_ACTIVE = "active"
ISSUANCE_STATUS_PROPOSED = "proposed"
REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_REJECTED = "rejected"
REVIEW_STATUSES = frozenset({REVIEW_STATUS_PENDING, REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED})


def format_stable_id(number: int) -> str:
    """Render the ``number``-th issued identifier.

    The identifier encodes a sequence position and nothing else. No brand, handle, year, row,
    title, industry, or status is recoverable from it, which is the whole point: an identifier
    that encodes evidence has to be reissued when the evidence changes.
    """
    if number < 1 or number > STABLE_ID_MAX:
        raise StableRecordCrosswalkError(
            f"stable id sequence {number} is outside the representable range 1..{STABLE_ID_MAX}"
        )
    return f"{STABLE_ID_PREFIX}{number:0{STABLE_ID_DIGITS}d}"


# --- seed derivation --------------------------------------------------------------------------

# The migration namespace is frozen. It is written as a literal so it can never drift with a code
# change, while ``SEED_NAMESPACE_IDENTIFIER`` records the preimage it came from so the literal stays
# auditable: ``uuid5(NAMESPACE_URL, SEED_NAMESPACE_IDENTIFIER)``. The preimage is an ``mka:`` URI,
# matching the namespace-string convention ``record_identity_lineage`` already uses.
# ``test_seed_namespace_literal_matches_its_derivation`` re-derives it.
SEED_NAMESPACE_IDENTIFIER = "mka:stable-record-identity:migration-namespace:v1"
SEED_NAMESPACE_UUID = uuid.UUID("ae183891-b891-5f58-8b4c-ec2683545797")

SEED_ALGORITHM = (
    "uuid5(migration_namespace, f'{workbook_sha256}:{source_sheet}:{source_row}'); legacy seeds "
    "use the legacy workbook sha256 and authority-only records use the authority workbook sha256; "
    "identifiers are assigned by ascending uuid5 value within each class, legacy class first."
)


def seed_uuid(workbook_sha256: str, source_sheet: str, source_row: int) -> uuid.UUID:
    """Derive the ordering seed for one record.

    This value is *seed derivation evidence*, not the identifier. It fixes the order in which
    identifiers are handed out; the identifier itself is the position in that order.
    """
    return uuid.uuid5(SEED_NAMESPACE_UUID, f"{workbook_sha256}:{source_sheet}:{source_row}")


# --- merchant field vocabulary ------------------------------------------------------------------

# Aligned position-for-position with ``EXPECTED_SHEET_HEADERS[SHEET_MERCHANT_CASES]`` so a header
# that legitimately varies ("商家 / 夥伴名稱" vs "商家/夥伴名稱") still resolves to one canonical
# field name. ``test_merchant_field_order_matches_preflight_headers`` guards the alignment.
MERCHANT_FIELD_ORDER = (
    "interview_year",
    "merchant_status",
    "brand_name",
    "merchant_handle",
    "sales_category_lv1",
    "sales_category_lv2",
    "content_tags",
    "article",
    "video",
    "podcast",
    "news",
    "notes",
)

# The three fields the match is made of. A change here is an identity change and is reported as a
# conflict; a change anywhere else is a payload change and must not move confidence.
IDENTITY_EVIDENCE_FIELDS = ("brand_name", "interview_year", "merchant_handle")

# Named to match the decision store's existing asset subjects (``…:r12:video``), so an asset review
# candidate reported here addresses the same field a reviewer already knows.
ASSET_FIELDS = ("article", "video", "podcast", "news")


# --- confidence vocabulary ----------------------------------------------------------------------

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"
CONFIDENCE_UNMATCHED = "UNMATCHED"
CONFIDENCE_NEW = "NEW"

CONFIDENCE_VALUES = (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_LOW,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_UNMATCHED,
    CONFIDENCE_NEW,
)

# Confidence classes that describe evidence too weak, too contradictory, or too absent to bind.
# They may exist in a proposal; they may never carry an approval.
NON_APPROVABLE_CONFIDENCES = frozenset(
    {CONFIDENCE_LOW, CONFIDENCE_AMBIGUOUS, CONFIDENCE_UNMATCHED}
)

# Confidence classes that assert a legacy record and an authority record are the same record.
BOUND_CONFIDENCES = frozenset({CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW})


class StableRecordCrosswalkError(ValueError):
    """Raised when a crosswalk proposal cannot be built, written, or trusted."""


# --- evidence normalization: stable-record-evidence-normalization/v1 ----------------------------
#
# Every comparison in this module runs on normalized evidence. The rules are deliberately narrow:
# they repair representation, never meaning. No fuzzy matching, no embeddings, no LLM matching, no
# 繁簡 conversion, no punctuation stripping — a migration that guesses which merchant it is looking
# at is worse than one that refuses.


def normalize_evidence_year(value: object) -> Optional[str]:
    """Normalize an interview year to its canonical digit string.

    The same year reaches this function as ``2025``, ``2025.0``, ``"2025"``, and ``"2025.0"``
    depending on how the cell was typed and which workbook it came from — the 20260708 legacy
    workbook stores ``"2026"`` where the 2026-08-21 authority workbook stores ``"2026.0"``. Under
    ``str(raw)`` those are different years and all 120 records look like identity changes, so this
    reads the value numerically instead.

    Returns ``None`` when the value cannot be read as a whole number, which surfaces as an evidence
    problem rather than as a silent non-match.
    """
    if value is None:
        return None
    # bool is an int subclass; a boolean year is a data problem, not the year 1.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None

    text = _collapse_whitespace(str(value))
    if not text or text.casefold() in NULL_LIKE_VALUES:
        return None
    try:
        numeric = float(text)
    except ValueError:
        return None
    if numeric != int(numeric):
        return None
    return str(int(numeric))


def normalize_evidence_brand(value: object) -> Optional[str]:
    """Normalize a brand name: strip, and collapse internal whitespace runs.

    Nothing else. Two brands that differ by a character differ, and this module says so.
    """
    if value is None:
        return None
    return _collapse_whitespace(str(value)) or None


def normalize_evidence_handle(value: object) -> Optional[str]:
    """Normalize a merchant handle, or report it missing.

    Placeholders reuse ``excel_ingestion.NULL_LIKE_VALUES`` — the set this repo already treats as
    "no value" when ingesting these same cells. Treating "-" or "n/a" as a real handle would let
    two unrelated records match on a placeholder they happen to share.
    """
    if value is None:
        return None
    text = _collapse_whitespace(str(value)).casefold()
    if not text or text in NULL_LIKE_VALUES:
        return None
    return text


def normalize_payload_cell(value: object) -> str:
    """Normalize a non-identity cell for change detection.

    Whitespace-only differences are not payload changes. Everything else is: this comparison is
    intentionally literal, so it can over-report a reformatted cell but can never hide a changed
    one. Payload changes do not move identity confidence, so over-reporting is the safe direction.
    """
    if value is None:
        return ""
    return _collapse_whitespace(str(value))


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def is_valid_asset_value(value: object) -> bool:
    """Report whether an asset cell names an actual asset.

    Reuses ``excel_ingestion.INVALID_ASSET_VALUES``, which already encodes this repo's reviewed
    position that "審核中", "已下架", "暫時下架" and the null-likes are not assets.
    """
    text = normalize_payload_cell(value)
    return bool(text) and text.casefold() not in INVALID_ASSET_VALUES


# --- workbook evidence --------------------------------------------------------------------------


@dataclass(frozen=True)
class MerchantRecordEvidence:
    """One merchant-case row, as migration evidence only.

    ``brand``/``year``/``handle`` are normalized discovery evidence. They are not an identity key
    and must never become one — see the module docstring.
    """

    source_sheet: str
    source_row: int
    brand: Optional[str]
    year: Optional[str]
    handle: Optional[str]
    fields: Mapping[str, str]

    @property
    def source_key(self) -> str:
        return f"{self.source_sheet}:{self.source_row}"


@dataclass(frozen=True)
class WorkbookEvidence:
    sha256: str
    source_sheet: str
    records: Tuple[MerchantRecordEvidence, ...]


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_merchant_evidence(
    workbook_path: Path,
    expected_sha256: str,
    source_sheet: str = SHEET_MERCHANT_CASES,
) -> WorkbookEvidence:
    """Read one workbook's merchant sheet as migration evidence, or refuse.

    Every refusal here is a refusal to produce a partial proposal. A workbook that is missing, is a
    symlink to somewhere else, is not a readable xlsx, hashes differently than the migration was
    adjudicated against, lacks the merchant sheet, or carries a different header shape, stops the
    run before a single row is emitted.
    """
    workbook_path = Path(workbook_path)

    # Checked before ``exists()``: a symlink to a missing target should report the symlink.
    if workbook_path.is_symlink():
        raise StableRecordCrosswalkError(
            f"workbook {workbook_path} is a symlink; migration evidence must be a regular file so "
            "the hash that is checked is the hash that is read"
        )
    if not workbook_path.exists():
        raise StableRecordCrosswalkError(f"workbook {workbook_path} does not exist")
    if not workbook_path.is_file():
        raise StableRecordCrosswalkError(f"workbook {workbook_path} is not a regular file")

    expected = (expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise StableRecordCrosswalkError(
            f"expected sha256 for {workbook_path} must be 64 hex characters; got {expected_sha256!r}"
        )

    actual = hash_file(workbook_path)
    if actual != expected:
        raise StableRecordCrosswalkError(
            f"workbook lineage mismatch for {workbook_path}: expected sha256 {expected}, "
            f"actual sha256 {actual}; this proposal was adjudicated against a different workbook"
        )

    try:
        sheets = read_xlsx_workbook(workbook_path)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise StableRecordCrosswalkError(
            f"workbook {workbook_path} could not be read as an xlsx workbook: {exc}"
        ) from exc

    if source_sheet not in sheets:
        raise StableRecordCrosswalkError(
            f"workbook {workbook_path} has no sheet named {source_sheet!r}; "
            f"available sheets: {sorted(sheets)}"
        )

    rows = sheets[source_sheet]
    header_row = EXPECTED_HEADER_ROWS[source_sheet]
    header_map = _merchant_header_map(rows, header_row, workbook_path, source_sheet)

    records = []
    for source_row, row in _table_rows(rows, header_row=header_row, start_row=header_row + 1):
        fields = {
            canonical: normalize_payload_cell(row.get(header))
            for header, canonical in header_map.items()
        }
        records.append(
            MerchantRecordEvidence(
                source_sheet=source_sheet,
                source_row=source_row,
                brand=normalize_evidence_brand(row.get(_header_for(header_map, "brand_name"))),
                year=normalize_evidence_year(row.get(_header_for(header_map, "interview_year"))),
                handle=normalize_evidence_handle(
                    row.get(_header_for(header_map, "merchant_handle"))
                ),
                fields=fields,
            )
        )

    if not records:
        raise StableRecordCrosswalkError(
            f"workbook {workbook_path} sheet {source_sheet!r} contains no merchant records"
        )

    return WorkbookEvidence(sha256=actual, source_sheet=source_sheet, records=tuple(records))


def _merchant_header_map(
    rows: Sequence[Sequence[object]],
    header_row: int,
    workbook_path: Path,
    source_sheet: str,
) -> Dict[str, str]:
    """Map this workbook's actual header cells onto canonical field names, or refuse.

    Reuses ``excel_preview.EXPECTED_SHEET_HEADERS``, so the accepted spellings are the ones this
    repo already preflights on, rather than a second opinion that could drift from it.
    """
    expected = EXPECTED_SHEET_HEADERS[source_sheet]
    actual = _normalized_headers_for(rows, header_row)
    if len(actual) != len(expected) or any(
        header not in accepted for header, accepted in zip(actual, expected)
    ):
        raise StableRecordCrosswalkError(
            f"workbook {workbook_path} sheet {source_sheet!r} header row {header_row} does not "
            f"match the expected merchant header; expected "
            f"{[' | '.join(item) for item in expected]}, actual {actual}"
        )
    return {header: canonical for header, canonical in zip(actual, MERCHANT_FIELD_ORDER)}


def _header_for(header_map: Mapping[str, str], canonical: str) -> str:
    for header, name in header_map.items():
        if name == canonical:
            return header
    raise StableRecordCrosswalkError(f"merchant header map is missing {canonical!r}")


# --- matching -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordMatch:
    """One reconciled record: a legacy row, an authority row, or a bound pair."""

    confidence: str
    legacy: Optional[MerchantRecordEvidence]
    authority: Optional[MerchantRecordEvidence]
    evidence: Tuple[str, ...]
    conflict_fields: Tuple[str, ...] = ()
    payload_change_fields: Tuple[str, ...] = ()
    asset_review_candidate_fields: Tuple[str, ...] = ()
    diagnostics: Tuple[str, ...] = ()


def match_records(legacy: WorkbookEvidence, authority: WorkbookEvidence) -> List[RecordMatch]:
    """Reconcile legacy rows against authority rows, refusing wherever evidence is not decisive.

    Discovery is ``brand + year``. Handles decide confidence but never create a candidate, because
    a record whose handle changed is still the same record and a record whose handle is missing
    still has to be reconcilable.

    There is no positional fallback of any kind. A legacy row with no candidate is UNMATCHED, and a
    legacy row whose candidate is not unique on either side is AMBIGUOUS — even when a row-shift
    pattern would make one of them "obvious". Row shift is recorded in ``diagnostics`` and is never
    read back as match evidence.
    """
    legacy_index = _index_by_discovery_key(legacy.records)
    authority_index = _index_by_discovery_key(authority.records)

    matches: List[RecordMatch] = []
    bound_authority_rows = set()
    # Discovery keys where a legacy record was refused. An authority row left unbound by one of
    # these is not the same thing as a genuinely new merchant, and saying so matters: a reviewer who
    # reads "authority_only_record" on a contested row could mint a second identity for a merchant
    # that already has one.
    contested_keys = set()

    for record in sorted(legacy.records, key=lambda item: item.source_row):
        key = (record.brand, record.year)

        if record.brand is None or record.year is None:
            matches.append(
                RecordMatch(
                    confidence=CONFIDENCE_UNMATCHED,
                    legacy=record,
                    authority=None,
                    evidence=("legacy_discovery_evidence_incomplete",),
                    conflict_fields=tuple(
                        name
                        for name, value in (("brand_name", record.brand), ("interview_year", record.year))
                        if value is None
                    ),
                )
            )
            continue

        candidates = authority_index.get(key, ())
        legacy_siblings = legacy_index.get(key, ())

        if not candidates:
            contested_keys.add(key)
            matches.append(
                RecordMatch(
                    confidence=CONFIDENCE_UNMATCHED,
                    legacy=record,
                    authority=None,
                    evidence=("brand_year_no_authority_candidate",),
                )
            )
            continue

        if len(candidates) > 1 or len(legacy_siblings) > 1:
            contested_keys.add(key)
            matches.append(
                RecordMatch(
                    confidence=CONFIDENCE_AMBIGUOUS,
                    legacy=record,
                    authority=None,
                    evidence=(
                        "brand_year_multiple_candidates",
                        f"legacy_candidates={len(legacy_siblings)}",
                        f"authority_candidates={len(candidates)}",
                    ),
                    conflict_fields=("brand_name", "interview_year"),
                )
            )
            continue

        candidate = candidates[0]
        legacy_handle, authority_handle = record.handle, candidate.handle

        if legacy_handle is not None and authority_handle is not None:
            if legacy_handle != authority_handle:
                # Both sides assert a handle and they disagree. That is contradictory identity
                # evidence, not a payload edit, so it is refused rather than downgraded.
                contested_keys.add(key)
                matches.append(
                    RecordMatch(
                        confidence=CONFIDENCE_AMBIGUOUS,
                        legacy=record,
                        authority=None,
                        evidence=("brand_match", "year_match", "handle_conflict"),
                        conflict_fields=("merchant_handle",),
                        diagnostics=_row_diagnostics(record, candidate),
                    )
                )
                continue
            confidence = CONFIDENCE_HIGH
            evidence = ("brand_match", "year_match", "handle_both_present", "handle_match")
        else:
            confidence = CONFIDENCE_MEDIUM
            evidence = (
                "brand_match",
                "year_match",
                "legacy_handle_missing" if legacy_handle is None else "legacy_handle_present",
                "authority_handle_missing" if authority_handle is None else "authority_handle_present",
            )

        bound_authority_rows.add(candidate.source_row)
        matches.append(
            RecordMatch(
                confidence=confidence,
                legacy=record,
                authority=candidate,
                evidence=evidence,
                payload_change_fields=_payload_change_fields(record, candidate),
                asset_review_candidate_fields=_asset_review_candidate_fields(record, candidate),
                diagnostics=_row_diagnostics(record, candidate),
            )
        )

    for record in sorted(authority.records, key=lambda item: item.source_row):
        if record.source_row in bound_authority_rows:
            continue
        contested = (record.brand, record.year) in contested_keys
        matches.append(
            RecordMatch(
                confidence=CONFIDENCE_NEW,
                legacy=None,
                authority=record,
                evidence=("authority_only_record",),
                asset_review_candidate_fields=_asset_review_candidate_fields(None, record),
                # A contested row still takes a fresh proposed identifier — binding it would be the
                # guess this module refuses to make — but the report says why it is unbound, so the
                # reviewer resolves an ambiguity instead of rubber-stamping a new merchant.
                diagnostics=(
                    ("unbound_after_contested_legacy_candidate",)
                    if contested
                    else ("authority_only_record",)
                ),
            )
        )

    return matches


def _index_by_discovery_key(
    records: Sequence[MerchantRecordEvidence],
) -> Dict[Tuple[Optional[str], Optional[str]], Tuple[MerchantRecordEvidence, ...]]:
    index: Dict[Tuple[Optional[str], Optional[str]], List[MerchantRecordEvidence]] = {}
    for record in records:
        if record.brand is None or record.year is None:
            continue
        index.setdefault((record.brand, record.year), []).append(record)
    return {key: tuple(sorted(value, key=lambda item: item.source_row)) for key, value in index.items()}


def _payload_change_fields(
    legacy: MerchantRecordEvidence, authority: MerchantRecordEvidence
) -> Tuple[str, ...]:
    """Report the NON-identity workbook fields that moved between the two workbooks.

    Identity evidence is excluded by construction: a record only reaches here because its identity
    evidence reconciled, and a difference in identity evidence is a conflict, not a payload change.
    """
    return tuple(
        name
        for name in MERCHANT_FIELD_ORDER
        if name not in IDENTITY_EVIDENCE_FIELDS
        and legacy.fields.get(name, "") != authority.fields.get(name, "")
    )


def _asset_review_candidate_fields(
    legacy: Optional[MerchantRecordEvidence], authority: MerchantRecordEvidence
) -> Tuple[str, ...]:
    """Report asset fields that newly name a real asset.

    These are candidates for asset review and nothing more. This module never approves, revokes, or
    re-pins an asset URL, and never touches the approved URL authority.
    """
    return tuple(
        name
        for name in ASSET_FIELDS
        if is_valid_asset_value(authority.fields.get(name, ""))
        and (legacy is None or not is_valid_asset_value(legacy.fields.get(name, "")))
    )


def _row_diagnostics(
    legacy: MerchantRecordEvidence, authority: MerchantRecordEvidence
) -> Tuple[str, ...]:
    """Secondary diagnostic evidence only. Never read back as match evidence."""
    shift = authority.source_row - legacy.source_row
    return (f"row_shift={shift:+d}",)


# --- identifier assignment --------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedRecord:
    stable_record_id: str
    record_type: str
    seed_derivation_digest: str
    match: RecordMatch


def assign_stable_ids(
    matches: Sequence[RecordMatch],
    legacy_sha256: str,
    authority_sha256: str,
) -> List[ProposedRecord]:
    """Mint the initial proposal identifiers.

    Legacy-seeded records are ordered by ``uuid5`` over frozen legacy lineage and numbered from 1.
    Authority-only records are numbered after them, ordered by ``uuid5`` over authority lineage.

    The ordering depends only on workbook lineage — never on the order ``matches`` arrives in, never
    on row position, never on brand or handle. That is what makes the assignment reproducible and
    what stops a later brand edit from renumbering the batch.
    """
    legacy_seeded = []
    authority_seeded = []

    for match in matches:
        if match.legacy is not None:
            legacy_seeded.append(
                (
                    seed_uuid(legacy_sha256, match.legacy.source_sheet, match.legacy.source_row),
                    match,
                )
            )
        elif match.authority is not None:
            authority_seeded.append(
                (
                    seed_uuid(
                        authority_sha256, match.authority.source_sheet, match.authority.source_row
                    ),
                    match,
                )
            )
        else:  # pragma: no cover - a match with neither side is not constructible
            raise StableRecordCrosswalkError("match has neither a legacy nor an authority record")

    ordered = sorted(legacy_seeded, key=lambda item: item[0]) + sorted(
        authority_seeded, key=lambda item: item[0]
    )

    proposed = []
    for sequence, (seed, match) in enumerate(ordered, start=1):
        proposed.append(
            ProposedRecord(
                stable_record_id=format_stable_id(sequence),
                record_type=RECORD_TYPE_MERCHANT_CASE,
                seed_derivation_digest=str(seed),
                match=match,
            )
        )
    return proposed


# --- proposal assembly --------------------------------------------------------------------------

REGISTRY_COLUMNS = (
    "stable_record_id",
    "record_type",
    "lifecycle_state",
    "issuance_batch",
    "issuance_status",
    "seed_derivation_digest",
    "review_status",
)

CROSSWALK_COLUMNS = (
    "stable_record_id",
    "record_type",
    "legacy_source_sheet",
    "legacy_source_row",
    "authority_source_sheet",
    "authority_source_row",
    "legacy_workbook_sha256",
    "authority_workbook_sha256",
    "brand_name_at_migration",
    "merchant_handle_at_migration",
    "interview_year_at_migration",
    "match_confidence",
    "match_evidence",
    "match_evidence_normalization",
    "payload_change_fields",
    "conflict_fields",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "migration_version",
    "notes",
)


@dataclass(frozen=True)
class CrosswalkProposal:
    registry_rows: Tuple[Dict[str, str], ...]
    crosswalk_rows: Tuple[Dict[str, str], ...]
    legacy_sha256: str
    authority_sha256: str
    migration_version: str
    legacy_record_count: int
    authority_record_count: int
    confidence_counts: Mapping[str, int]
    reconciliation: Mapping[str, int]
    payload_changed_record_count: int
    asset_review_candidate_count: int
    asset_review_candidate_field_count: int
    asset_review_candidates: Tuple[Mapping[str, object], ...]


def build_crosswalk_proposal(
    legacy: WorkbookEvidence,
    authority: WorkbookEvidence,
    migration_version: str = MIGRATION_VERSION,
) -> CrosswalkProposal:
    """Reconcile, mint, and lay out the proposal rows. Writes nothing."""
    matches = match_records(legacy, authority)
    proposed = assign_stable_ids(matches, legacy.sha256, authority.sha256)

    registry_rows = []
    crosswalk_rows = []
    confidence_counts = {name: 0 for name in CONFIDENCE_VALUES}
    unchanged = shifted = matched = 0
    payload_changed = 0
    asset_candidates: List[Dict[str, object]] = []
    asset_candidate_fields = 0

    for record in proposed:
        match = record.match
        confidence_counts[match.confidence] += 1

        registry_rows.append(
            {
                "stable_record_id": record.stable_record_id,
                "record_type": record.record_type,
                "lifecycle_state": LIFECYCLE_STATE_ACTIVE,
                "issuance_batch": migration_version,
                "issuance_status": ISSUANCE_STATUS_PROPOSED,
                "seed_derivation_digest": record.seed_derivation_digest,
                # M1 is a proposal. Every row is pending, including every HIGH row.
                "review_status": REVIEW_STATUS_PENDING,
            }
        )

        legacy_record = match.legacy
        authority_record = match.authority
        # Migration-time evidence is quoted from whichever side is authoritative for it: the
        # authority workbook where the record still exists there, the legacy workbook otherwise.
        evidence_source = authority_record or legacy_record

        if legacy_record is not None and authority_record is not None:
            matched += 1
            if legacy_record.source_row == authority_record.source_row:
                unchanged += 1
            else:
                shifted += 1

        if match.payload_change_fields:
            payload_changed += 1
        if match.asset_review_candidate_fields:
            asset_candidate_fields += len(match.asset_review_candidate_fields)
            asset_candidates.append(
                {
                    "stable_record_id": record.stable_record_id,
                    "match_confidence": match.confidence,
                    "fields": list(match.asset_review_candidate_fields),
                }
            )

        crosswalk_rows.append(
            {
                "stable_record_id": record.stable_record_id,
                "record_type": record.record_type,
                # A NEW record has no legacy binding at all, so every legacy column stays empty.
                "legacy_source_sheet": legacy_record.source_sheet if legacy_record else "",
                "legacy_source_row": str(legacy_record.source_row) if legacy_record else "",
                "authority_source_sheet": authority_record.source_sheet if authority_record else "",
                "authority_source_row": str(authority_record.source_row) if authority_record else "",
                "legacy_workbook_sha256": legacy.sha256 if legacy_record else "",
                "authority_workbook_sha256": authority.sha256 if authority_record else "",
                "brand_name_at_migration": (evidence_source.brand or "") if evidence_source else "",
                "merchant_handle_at_migration": (evidence_source.handle or "") if evidence_source else "",
                "interview_year_at_migration": (evidence_source.year or "") if evidence_source else "",
                "match_confidence": match.confidence,
                "match_evidence": ";".join(match.evidence),
                "match_evidence_normalization": NORMALIZATION_VERSION,
                "payload_change_fields": ";".join(match.payload_change_fields),
                "conflict_fields": ";".join(match.conflict_fields),
                "review_status": REVIEW_STATUS_PENDING,
                "reviewed_by": "",
                "reviewed_at": "",
                "migration_version": migration_version,
                "notes": ";".join(match.diagnostics),
            }
        )

    registry_rows.sort(key=lambda row: row["stable_record_id"])
    crosswalk_rows.sort(key=lambda row: row["stable_record_id"])

    return CrosswalkProposal(
        registry_rows=tuple(registry_rows),
        crosswalk_rows=tuple(crosswalk_rows),
        legacy_sha256=legacy.sha256,
        authority_sha256=authority.sha256,
        migration_version=migration_version,
        legacy_record_count=len(legacy.records),
        authority_record_count=len(authority.records),
        confidence_counts=confidence_counts,
        reconciliation={
            "matched_legacy_records": matched,
            "unchanged_rows": unchanged,
            "shifted_rows": shifted,
            "legacy_unmatched_records": confidence_counts[CONFIDENCE_UNMATCHED],
            "ambiguous_records": confidence_counts[CONFIDENCE_AMBIGUOUS],
            "authority_only_records": confidence_counts[CONFIDENCE_NEW],
        },
        payload_changed_record_count=payload_changed,
        asset_review_candidate_count=len(asset_candidates),
        asset_review_candidate_field_count=asset_candidate_fields,
        asset_review_candidates=tuple(asset_candidates),
    )


# --- validation ---------------------------------------------------------------------------------

# The complete vocabulary a match may cite. Anything outside it is rejected, which is what stops a
# later change from quietly introducing positional evidence: "row_shift=+1" is a diagnostic and is
# not a member of this set, so a match that tried to justify itself by row position fails closed.
MATCH_EVIDENCE_TOKENS = frozenset(
    {
        "brand_match",
        "year_match",
        "handle_both_present",
        "handle_match",
        "handle_conflict",
        "legacy_handle_missing",
        "legacy_handle_present",
        "authority_handle_missing",
        "authority_handle_present",
        "brand_year_no_authority_candidate",
        "brand_year_multiple_candidates",
        "legacy_discovery_evidence_incomplete",
        "authority_only_record",
    }
)
_COUNT_EVIDENCE_RE = re.compile(r"^(?:legacy|authority)_candidates=\d+$")


@dataclass(frozen=True)
class MigrationExpectation:
    """Migration-specific expected counts.

    These pin *this* migration's adjudicated reconciliation. They are counts and workbook hashes
    only: no merchant name is hard-coded into the validator, because a validator that knows the
    merchant roster leaks it and goes stale the moment the roster changes.
    """

    migration_version: str
    legacy_workbook_sha256: str
    authority_workbook_sha256: str
    legacy_record_count: int
    authority_record_count: int
    matched_legacy_records: int
    unchanged_rows: int
    shifted_rows: int
    confidence_counts: Mapping[str, int]


M1_EXPECTATION = MigrationExpectation(
    migration_version=MIGRATION_VERSION,
    legacy_workbook_sha256="9cbd93f1a754eb28aa358d74215445c5ffa3b1100dd947000aa9bed1b5c4ad2c",
    authority_workbook_sha256="7e6ecffc2d4ad9b931c8abc2d75345305c0a93026ecb1cb10841aa5e8c6597a3",
    legacy_record_count=120,
    authority_record_count=121,
    matched_legacy_records=120,
    unchanged_rows=1,
    shifted_rows=119,
    confidence_counts={
        CONFIDENCE_HIGH: 105,
        CONFIDENCE_MEDIUM: 15,
        CONFIDENCE_LOW: 0,
        CONFIDENCE_AMBIGUOUS: 0,
        CONFIDENCE_UNMATCHED: 0,
        CONFIDENCE_NEW: 1,
    },
)


def validate_proposal(
    registry_rows: Sequence[Mapping[str, str]],
    crosswalk_rows: Sequence[Mapping[str, str]],
    legacy_sha256: str,
    authority_sha256: str,
) -> None:
    """Fail closed on any structurally unsound proposal.

    Every check here answers a way the proposal could bind two different merchants to one identity,
    hand one merchant two identities, or carry an approval its evidence does not support.
    """
    errors: List[str] = []

    _validate_columns(registry_rows, REGISTRY_COLUMNS, "registry", errors)
    _validate_columns(crosswalk_rows, CROSSWALK_COLUMNS, "crosswalk", errors)
    if errors:
        raise StableRecordCrosswalkError("crosswalk proposal validation failed:\n" + "\n".join(errors))

    registry_ids = [row["stable_record_id"] for row in registry_rows]
    crosswalk_ids = [row["stable_record_id"] for row in crosswalk_rows]

    for label, values in (("registry", registry_ids), ("crosswalk", crosswalk_ids)):
        for value in values:
            if not STABLE_ID_RE.match(value):
                errors.append(f"{label}: stable_record_id {value!r} does not match {STABLE_ID_RE.pattern}")
        _report_duplicates(values, f"{label}: duplicate stable_record_id", errors)

    # One stable ID may describe exactly one record, and every issued ID must be registered. A
    # crosswalk row without a registry row is an identity that was bound but never issued.
    if set(registry_ids) != set(crosswalk_ids):
        only_registry = sorted(set(registry_ids) - set(crosswalk_ids))
        only_crosswalk = sorted(set(crosswalk_ids) - set(registry_ids))
        errors.append(
            "registry and crosswalk do not describe the same identities; "
            f"registry-only={only_registry}, crosswalk-only={only_crosswalk}"
        )

    registry_by_id = {row["stable_record_id"]: row for row in registry_rows}

    legacy_keys: List[str] = []
    authority_keys: List[str] = []

    for row in crosswalk_rows:
        stable_id = row["stable_record_id"]
        confidence = row["match_confidence"]
        review_status = row["review_status"]

        if confidence not in CONFIDENCE_VALUES:
            errors.append(f"{stable_id}: match_confidence {confidence!r} is not one of {CONFIDENCE_VALUES}")
        if review_status not in REVIEW_STATUSES:
            errors.append(f"{stable_id}: review_status {review_status!r} is not one of {sorted(REVIEW_STATUSES)}")

        if row["match_evidence_normalization"] != NORMALIZATION_VERSION:
            errors.append(
                f"{stable_id}: match_evidence_normalization {row['match_evidence_normalization']!r} "
                f"is not {NORMALIZATION_VERSION!r}"
            )

        evidence = [token for token in row["match_evidence"].split(";") if token]
        if not evidence:
            errors.append(f"{stable_id}: match_evidence is empty")
        for token in evidence:
            if token not in MATCH_EVIDENCE_TOKENS and not _COUNT_EVIDENCE_RE.match(token):
                errors.append(
                    f"{stable_id}: match_evidence token {token!r} is not a recognised evidence "
                    "token; positional or ad-hoc evidence is not accepted"
                )

        registry_row = registry_by_id.get(stable_id)
        if registry_row is not None and registry_row["record_type"] != row["record_type"]:
            errors.append(
                f"{stable_id}: record_type disagrees between registry "
                f"({registry_row['record_type']!r}) and crosswalk ({row['record_type']!r})"
            )

        has_legacy = bool(row["legacy_source_row"])
        has_authority = bool(row["authority_source_row"])

        if confidence == CONFIDENCE_NEW:
            # A NEW record must carry no legacy binding of any kind, or it is silently claiming to
            # inherit an existing record's decisions.
            leaked = [
                name
                for name in (
                    "legacy_source_sheet",
                    "legacy_source_row",
                    "legacy_workbook_sha256",
                )
                if row[name]
            ]
            if leaked:
                errors.append(f"{stable_id}: NEW record carries legacy binding fields {leaked}")
            if not has_authority:
                errors.append(f"{stable_id}: NEW record has no authority binding")
        elif confidence in BOUND_CONFIDENCES:
            if not (has_legacy and has_authority):
                errors.append(
                    f"{stable_id}: {confidence} record must bind both a legacy and an authority row"
                )
        else:  # AMBIGUOUS / UNMATCHED
            if has_authority:
                errors.append(
                    f"{stable_id}: {confidence} record must not bind an authority row; evidence was "
                    "not decisive"
                )

        if has_legacy:
            if row["legacy_workbook_sha256"] != legacy_sha256:
                errors.append(
                    f"{stable_id}: legacy_workbook_sha256 {row['legacy_workbook_sha256']!r} does not "
                    f"match the legacy workbook {legacy_sha256!r}"
                )
            legacy_keys.append(f"{row['legacy_source_sheet']}:{row['legacy_source_row']}")
        if has_authority:
            if row["authority_workbook_sha256"] != authority_sha256:
                errors.append(
                    f"{stable_id}: authority_workbook_sha256 {row['authority_workbook_sha256']!r} does "
                    f"not match the authority workbook {authority_sha256!r}"
                )
            authority_keys.append(f"{row['authority_source_sheet']}:{row['authority_source_row']}")

        if review_status == REVIEW_STATUS_PENDING:
            stamped = [name for name in ("reviewed_by", "reviewed_at") if row[name]]
            if stamped:
                errors.append(f"{stable_id}: pending row carries review stamps {stamped}")
        elif review_status == REVIEW_STATUS_APPROVED:
            missing = [name for name in ("reviewed_by", "reviewed_at") if not row[name]]
            if missing:
                errors.append(f"{stable_id}: approved row is missing {missing}")
            if confidence in NON_APPROVABLE_CONFIDENCES:
                errors.append(
                    f"{stable_id}: {confidence} record cannot be approved; its evidence does not "
                    "establish which record it is"
                )
            if confidence in BOUND_CONFIDENCES and not evidence:
                errors.append(f"{stable_id}: approved mapping has no supporting evidence")

        if registry_row is not None and registry_row["review_status"] != review_status:
            errors.append(
                f"{stable_id}: review_status disagrees between registry "
                f"({registry_row['review_status']!r}) and crosswalk ({review_status!r})"
            )

    # One legacy record may map to at most one stable ID, and one authority record likewise.
    _report_duplicates(legacy_keys, "legacy source key bound to more than one stable_record_id", errors)
    _report_duplicates(
        authority_keys, "authority source key bound to more than one stable_record_id", errors
    )

    for row in registry_rows:
        if row["lifecycle_state"] != LIFECYCLE_STATE_ACTIVE:
            errors.append(
                f"{row['stable_record_id']}: lifecycle_state {row['lifecycle_state']!r} is not "
                f"{LIFECYCLE_STATE_ACTIVE!r}"
            )
        if row["issuance_status"] != ISSUANCE_STATUS_PROPOSED:
            errors.append(
                f"{row['stable_record_id']}: issuance_status {row['issuance_status']!r} is not "
                f"{ISSUANCE_STATUS_PROPOSED!r}; this directory is a proposal, not an issued registry"
            )
        if not row["seed_derivation_digest"]:
            errors.append(f"{row['stable_record_id']}: seed_derivation_digest is empty")

    if errors:
        raise StableRecordCrosswalkError("crosswalk proposal validation failed:\n" + "\n".join(errors))


def validate_expectation(proposal: CrosswalkProposal, expectation: MigrationExpectation) -> None:
    """Check a built proposal against the counts this migration was adjudicated with."""
    errors = []
    checks = (
        ("migration_version", proposal.migration_version, expectation.migration_version),
        ("legacy_workbook_sha256", proposal.legacy_sha256, expectation.legacy_workbook_sha256),
        ("authority_workbook_sha256", proposal.authority_sha256, expectation.authority_workbook_sha256),
        ("legacy_record_count", proposal.legacy_record_count, expectation.legacy_record_count),
        ("authority_record_count", proposal.authority_record_count, expectation.authority_record_count),
        (
            "matched_legacy_records",
            proposal.reconciliation["matched_legacy_records"],
            expectation.matched_legacy_records,
        ),
        ("unchanged_rows", proposal.reconciliation["unchanged_rows"], expectation.unchanged_rows),
        ("shifted_rows", proposal.reconciliation["shifted_rows"], expectation.shifted_rows),
    )
    for name, actual, expected in checks:
        if actual != expected:
            errors.append(f"{name}: expected {expected!r}, actual {actual!r}")

    for name, expected in expectation.confidence_counts.items():
        actual = proposal.confidence_counts.get(name, 0)
        if actual != expected:
            errors.append(f"confidence_counts[{name}]: expected {expected}, actual {actual}")

    if errors:
        raise StableRecordCrosswalkError(
            "crosswalk proposal does not match the adjudicated migration expectation:\n"
            + "\n".join(errors)
        )


def _validate_columns(
    rows: Sequence[Mapping[str, str]],
    columns: Sequence[str],
    label: str,
    errors: List[str],
) -> None:
    if not rows:
        errors.append(f"{label}: proposal contains no rows")
        return
    expected = set(columns)
    for index, row in enumerate(rows):
        if set(row) != expected:
            errors.append(
                f"{label}: row {index} column set {sorted(row)} does not match {sorted(expected)}"
            )
            return


def _report_duplicates(values: Sequence[str], message: str, errors: List[str]) -> None:
    seen = set()
    duplicates = sorted({value for value in values if value in seen or seen.add(value)})
    if duplicates:
        errors.append(f"{message}: {duplicates}")


# --- serialization ------------------------------------------------------------------------------


def render_csv(rows: Sequence[Mapping[str, str]], columns: Sequence[str]) -> bytes:
    """Render rows to deterministic CSV bytes.

    Fixed column order, ``\\n`` terminators, no BOM: the same proposal renders to the same bytes on
    every platform and every run, which is what the determinism requirement is checked against.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_manifest(
    proposal: CrosswalkProposal,
    registry_bytes: bytes,
    crosswalk_bytes: bytes,
    created_at: Optional[str] = None,
) -> Dict[str, object]:
    """Build the private proposal manifest.

    Two digests, because they answer different questions:

    ``content_digest``
        Covers everything the proposal *is* and deliberately excludes ``created_at``. Two runs over
        the same inputs produce the same ``content_digest``, so it is the value a reproducibility
        check compares.

    ``manifest_hash``
        Covers the whole manifest including ``created_at``, so the file is self-verifying against
        tampering. It legitimately differs between runs and must never be used as a reproducibility
        signal.

    The manifest carries no token, no secret, and no merchant roster.
    """
    stable_ids = sorted(row["stable_record_id"] for row in proposal.registry_rows)

    body: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        # This directory is a proposal. It is not the stable_record_v2 authority, it does not
        # activate a record identity scheme, and nothing in it is issued or approved.
        "authority_status": AUTHORITY_STATUS_PROPOSAL_ONLY,
        "record_identity_scheme_status": RECORD_IDENTITY_SCHEME_STATUS_NOT_ACTIVATED,
        "proposed_successor_scheme": PROPOSED_SUCCESSOR_SCHEME,
        "proposal_state": PROPOSAL_STATE_COMPLETE,
        "migration_version": proposal.migration_version,
        "legacy_workbook_sha256": proposal.legacy_sha256,
        "authority_workbook_sha256": proposal.authority_sha256,
        "normalization_version": NORMALIZATION_VERSION,
        "seed_namespace_identifier": SEED_NAMESPACE_IDENTIFIER,
        "seed_namespace_uuid": str(SEED_NAMESPACE_UUID),
        "seed_algorithm": SEED_ALGORITHM,
        "registry_filename": REGISTRY_FILENAME,
        "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
        "registry_record_count": len(proposal.registry_rows),
        "crosswalk_filename": CROSSWALK_FILENAME,
        "crosswalk_sha256": hashlib.sha256(crosswalk_bytes).hexdigest(),
        "crosswalk_record_count": len(proposal.crosswalk_rows),
        "stable_id_min": stable_ids[0] if stable_ids else "",
        "stable_id_max": stable_ids[-1] if stable_ids else "",
        "stable_id_count": len(stable_ids),
        "legacy_record_count": proposal.legacy_record_count,
        "authority_record_count": proposal.authority_record_count,
        "confidence_counts": dict(proposal.confidence_counts),
        "reconciliation": dict(proposal.reconciliation),
        "new_record_count": proposal.confidence_counts[CONFIDENCE_NEW],
        "payload_changed_record_count": proposal.payload_changed_record_count,
        "asset_review_candidate_count": proposal.asset_review_candidate_count,
        "asset_review_candidate_field_count": proposal.asset_review_candidate_field_count,
        # Machine-readable, identifier-keyed: it names which proposed records a reviewer must look
        # at, without restating the merchant roster.
        "asset_review_candidates": [dict(item) for item in proposal.asset_review_candidates],
    }

    body["content_digest"] = hashlib.sha256(_canonical_json(body)).hexdigest()
    body["created_at"] = created_at or datetime.now(timezone.utc).isoformat()
    body["manifest_hash"] = hashlib.sha256(_canonical_json(body)).hexdigest()
    return body


def manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    return json.dumps(dict(manifest), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


# --- publication --------------------------------------------------------------------------------


def write_proposal(
    proposal: CrosswalkProposal,
    output_dir: Path,
    created_at: Optional[str] = None,
) -> Dict[str, object]:
    """Validate, then publish the proposal as a whole directory or not at all.

    Every byte is built and validated in memory first, staged into a sibling temporary directory,
    and moved into place with a single rename. A run that dies partway leaves the staging directory
    behind and the output path untouched, so there is no window in which a half-written directory
    could be read as a proposal.
    """
    output_dir = Path(output_dir)

    validate_proposal(
        proposal.registry_rows,
        proposal.crosswalk_rows,
        proposal.legacy_sha256,
        proposal.authority_sha256,
    )

    registry_bytes = render_csv(proposal.registry_rows, REGISTRY_COLUMNS)
    crosswalk_bytes = render_csv(proposal.crosswalk_rows, CROSSWALK_COLUMNS)
    manifest = build_manifest(proposal, registry_bytes, crosswalk_bytes, created_at=created_at)

    if output_dir.exists():
        if not output_dir.is_dir():
            raise StableRecordCrosswalkError(f"proposal output path {output_dir} is not a directory")
        existing = sorted(item.name for item in output_dir.iterdir())
        if existing:
            raise StableRecordCrosswalkError(
                f"proposal output directory {output_dir} is not empty (contains {existing}); "
                "refusing to overwrite an existing proposal — use verify-existing to compare "
                "against it, or choose a new output directory"
            )

    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging.", dir=parent))
    try:
        (staging / REGISTRY_FILENAME).write_bytes(registry_bytes)
        (staging / CROSSWALK_FILENAME).write_bytes(crosswalk_bytes)
        (staging / MANIFEST_FILENAME).write_bytes(manifest_bytes(manifest))
        if output_dir.exists():
            output_dir.rmdir()
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return manifest


def load_proposal(output_dir: Path) -> Tuple[Dict[str, object], List[Dict[str, str]], List[Dict[str, str]]]:
    """Load a published proposal, refusing anything that is not a complete one."""
    output_dir = Path(output_dir)
    manifest_path = output_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise StableRecordCrosswalkError(
            f"{output_dir} has no {MANIFEST_FILENAME}; it is not a published proposal"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StableRecordCrosswalkError(f"{manifest_path} could not be read: {exc}") from exc

    if manifest.get("proposal_state") != PROPOSAL_STATE_COMPLETE:
        raise StableRecordCrosswalkError(
            f"{manifest_path} declares proposal_state={manifest.get('proposal_state')!r}; "
            f"only {PROPOSAL_STATE_COMPLETE!r} proposals may be loaded"
        )
    if manifest.get("authority_status") != AUTHORITY_STATUS_PROPOSAL_ONLY:
        raise StableRecordCrosswalkError(
            f"{manifest_path} declares authority_status={manifest.get('authority_status')!r}; "
            f"this loader only accepts {AUTHORITY_STATUS_PROPOSAL_ONLY!r}"
        )

    registry = _read_csv(output_dir / manifest["registry_filename"], REGISTRY_COLUMNS)
    crosswalk = _read_csv(output_dir / manifest["crosswalk_filename"], CROSSWALK_COLUMNS)

    for label, path, rows_digest in (
        ("registry", output_dir / manifest["registry_filename"], manifest["registry_sha256"]),
        ("crosswalk", output_dir / manifest["crosswalk_filename"], manifest["crosswalk_sha256"]),
    ):
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != rows_digest:
            raise StableRecordCrosswalkError(
                f"{label} file {path} sha256 {actual} does not match the manifest ({rows_digest})"
            )

    return manifest, registry, crosswalk


def _read_csv(path: Path, columns: Sequence[str]) -> List[Dict[str, str]]:
    if not path.is_file():
        raise StableRecordCrosswalkError(f"proposal file {path} is missing")
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(columns):
            raise StableRecordCrosswalkError(
                f"proposal file {path} columns {reader.fieldnames} do not match {list(columns)}"
            )
        return [dict(row) for row in reader]


def verify_existing_proposal(proposal: CrosswalkProposal, output_dir: Path) -> Dict[str, object]:
    """Re-derive a proposal and refuse any regeneration that would move an issued identifier.

    Once a proposal has been reviewed, its identifiers are fixed. Regeneration may only ever
    reproduce them. This compares the freshly derived assignment against the published one and
    reports every drift rather than rewriting the file — a silent rewrite is exactly the failure
    this check exists to prevent.
    """
    manifest, registry, crosswalk = load_proposal(output_dir)

    errors: List[str] = []

    for name, actual, expected in (
        ("migration_version", proposal.migration_version, manifest.get("migration_version")),
        ("legacy_workbook_sha256", proposal.legacy_sha256, manifest.get("legacy_workbook_sha256")),
        (
            "authority_workbook_sha256",
            proposal.authority_sha256,
            manifest.get("authority_workbook_sha256"),
        ),
        ("normalization_version", NORMALIZATION_VERSION, manifest.get("normalization_version")),
        ("seed_namespace_uuid", str(SEED_NAMESPACE_UUID), manifest.get("seed_namespace_uuid")),
    ):
        if actual != expected:
            errors.append(f"{name}: regenerated {actual!r}, published {expected!r}")

    published_seed = {row["stable_record_id"]: row["seed_derivation_digest"] for row in registry}
    regenerated_seed = {
        row["stable_record_id"]: row["seed_derivation_digest"] for row in proposal.registry_rows
    }

    for stable_id in sorted(set(published_seed) | set(regenerated_seed)):
        published = published_seed.get(stable_id)
        regenerated = regenerated_seed.get(stable_id)
        if published is None:
            errors.append(f"{stable_id}: newly derived identifier is absent from the published proposal")
        elif regenerated is None:
            errors.append(f"{stable_id}: published identifier is absent from the regenerated proposal")
        elif published != regenerated:
            errors.append(
                f"{stable_id}: seed derivation moved (published {published}, regenerated "
                f"{regenerated}); an issued stable_record_id may never be recomputed onto a "
                "different record"
            )

    published_binding = {
        row["stable_record_id"]: (
            row["legacy_source_sheet"],
            row["legacy_source_row"],
            row["authority_source_sheet"],
            row["authority_source_row"],
        )
        for row in crosswalk
    }
    regenerated_binding = {
        row["stable_record_id"]: (
            row["legacy_source_sheet"],
            row["legacy_source_row"],
            row["authority_source_sheet"],
            row["authority_source_row"],
        )
        for row in proposal.crosswalk_rows
    }
    for stable_id in sorted(set(published_binding) & set(regenerated_binding)):
        if published_binding[stable_id] != regenerated_binding[stable_id]:
            errors.append(
                f"{stable_id}: record binding moved (published {published_binding[stable_id]}, "
                f"regenerated {regenerated_binding[stable_id]})"
            )

    if errors:
        raise StableRecordCrosswalkError(
            f"regenerated proposal does not reproduce {output_dir}:\n" + "\n".join(errors)
        )

    registry_bytes = render_csv(proposal.registry_rows, REGISTRY_COLUMNS)
    crosswalk_bytes = render_csv(proposal.crosswalk_rows, CROSSWALK_COLUMNS)
    regenerated_manifest = build_manifest(
        proposal, registry_bytes, crosswalk_bytes, created_at=str(manifest.get("created_at", ""))
    )

    return {
        "output_dir": str(output_dir),
        "verified": True,
        "stable_id_count": len(regenerated_seed),
        "published_content_digest": manifest.get("content_digest"),
        "regenerated_content_digest": regenerated_manifest["content_digest"],
        "content_digest_matches": manifest.get("content_digest")
        == regenerated_manifest["content_digest"],
    }


# --- read-only decision impact analysis ---------------------------------------------------------
#
# Everything below observes. The decision store is opened through a read-only SQLite URI so the
# connection physically cannot write, no projection is created, and no decision is rewritten. The
# output is an analysis report.
#
# Record grain and event grain are different populations and are never mixed: one record can carry
# several decision events, so projecting record confidence onto events produces different counts by
# construction. The record-grain counts are the migration's counts; the event-grain counts describe
# how much review history would have to be re-pointed, and are validation-only.

SUBJECT_ROW = "row"
SUBJECT_ROW_FIELD = "row_field"
SUBJECT_ROW_ALIAS = "row_alias"
UNKNOWN_SUBJECT_FORMAT = "UNKNOWN_SUBJECT_FORMAT"

ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION = True


def _subject_patterns(source_sheet: str) -> Sequence[Tuple[str, "re.Pattern[str]"]]:
    sheet = re.escape(source_sheet)
    return (
        (SUBJECT_ROW, re.compile(rf"^{sheet}:r(?P<row>\d+)$")),
        (SUBJECT_ROW_FIELD, re.compile(rf"^{sheet}:r(?P<row>\d+):(?P<field>.+)$")),
        (SUBJECT_ROW_ALIAS, re.compile(rf"^{sheet}:r(?P<row>\d+)\|(?P<alias>.+)$")),
    )


def classify_decision_subject(
    subject_id: str, source_sheet: str = SHEET_MERCHANT_CASES
) -> Tuple[str, Optional[int], Optional[str]]:
    """Classify one row-bound decision subject.

    Returns ``(format, row, qualifier)``. A subject that names the merchant sheet but matches no
    known shape is returned as ``UNKNOWN_SUBJECT_FORMAT`` rather than skipped: an unrecognised
    row-bound subject is review history this migration has not accounted for, and quietly dropping
    it would understate the impact.
    """
    for name, pattern in _subject_patterns(source_sheet):
        match = pattern.match(subject_id)
        if match:
            groups = match.groupdict()
            qualifier = groups.get("field") or groups.get("alias")
            return name, int(groups["row"]), qualifier
    return UNKNOWN_SUBJECT_FORMAT, None, None


def analyze_decision_impact(
    decision_store_path: Path,
    crosswalk_rows: Sequence[Mapping[str, str]],
    source_sheet: str = SHEET_MERCHANT_CASES,
) -> Dict[str, object]:
    """Summarise, read-only, how much row-bound review history this migration would re-point.

    This creates no projection and writes nothing. It exists so the size and shape of the decision
    surface is known before anyone proposes to migrate it.
    """
    decision_store_path = Path(decision_store_path)
    if not decision_store_path.is_file():
        raise StableRecordCrosswalkError(f"decision store {decision_store_path} does not exist")

    by_legacy_row: Dict[int, Mapping[str, str]] = {}
    for row in crosswalk_rows:
        if row["legacy_source_sheet"] == source_sheet and row["legacy_source_row"]:
            by_legacy_row[int(row["legacy_source_row"])] = row

    # ``mode=ro`` is the guarantee: the connection cannot write even if a later change tried to.
    uri = f"file:{decision_store_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        subjects = [
            str(value)
            for (value,) in connection.execute("SELECT subject_id FROM decision_events")
            if value is not None
        ]
    except sqlite3.Error as exc:
        raise StableRecordCrosswalkError(
            f"decision store {decision_store_path} could not be read: {exc}"
        ) from exc
    finally:
        connection.close()

    format_counts = {SUBJECT_ROW: 0, SUBJECT_ROW_FIELD: 0, SUBJECT_ROW_ALIAS: 0}
    confidence_counts = {name: 0 for name in CONFIDENCE_VALUES}
    unknown_subjects: List[str] = []
    unresolved_subjects: List[str] = []
    alias_bindings: List[Dict[str, object]] = []
    shifted = unchanged = total = 0

    for subject_id in sorted(subjects):
        if source_sheet not in subject_id:
            continue
        total += 1
        subject_format, row_number, qualifier = classify_decision_subject(subject_id, source_sheet)
        if subject_format == UNKNOWN_SUBJECT_FORMAT:
            unknown_subjects.append(subject_id)
            continue

        format_counts[subject_format] += 1
        crosswalk_row = by_legacy_row.get(row_number)
        if crosswalk_row is None:
            unresolved_subjects.append(subject_id)
            continue

        confidence_counts[crosswalk_row["match_confidence"]] += 1
        if crosswalk_row["authority_source_row"] == crosswalk_row["legacy_source_row"]:
            unchanged += 1
        else:
            shifted += 1

        if subject_format == SUBJECT_ROW_ALIAS:
            alias_bindings.append(
                {
                    "legacy_source_row": row_number,
                    "alias": qualifier,
                    "proposed_stable_record_id": crosswalk_row["stable_record_id"],
                    "match_confidence": crosswalk_row["match_confidence"],
                }
            )

    return {
        "grain": "decision_event",
        "decision_store": str(decision_store_path),
        "row_bound_event_count": total,
        "subject_format_counts": format_counts,
        "event_confidence_counts": confidence_counts,
        "shifted_events": shifted,
        "unchanged_events": unchanged,
        # Surfaced, never ignored. Either is a reason to refuse M1 completeness.
        "unknown_subject_formats": unknown_subjects,
        "unresolved_row_subjects": unresolved_subjects,
        "complete": not unknown_subjects and not unresolved_subjects,
        # Reported only. This module does not modify search_alias_projection.json, add an alias,
        # re-point one, split SLP, or change SHOPLINE Payments semantics.
        "alias_bindings": alias_bindings,
        "alias_rebinding_requires_separate_decision": ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION,
    }


# --- orchestration ------------------------------------------------------------------------------


def generate_stable_record_crosswalk_proposal(
    legacy_workbook: Path,
    authority_workbook: Path,
    output_dir: Path,
    expectation: MigrationExpectation = M1_EXPECTATION,
    decision_store: Optional[Path] = None,
    verify_existing: bool = False,
    created_at: Optional[str] = None,
) -> Dict[str, object]:
    """Build a stable-record crosswalk proposal, or refuse.

    Nothing outside ``output_dir`` is written. The decision store, when supplied, is read through a
    read-only connection for impact analysis only.
    """
    legacy = load_merchant_evidence(legacy_workbook, expectation.legacy_workbook_sha256)
    authority = load_merchant_evidence(authority_workbook, expectation.authority_workbook_sha256)

    proposal = build_crosswalk_proposal(legacy, authority, migration_version=expectation.migration_version)
    validate_proposal(
        proposal.registry_rows,
        proposal.crosswalk_rows,
        proposal.legacy_sha256,
        proposal.authority_sha256,
    )
    validate_expectation(proposal, expectation)

    if verify_existing:
        verification = verify_existing_proposal(proposal, output_dir)
        manifest, _, _ = load_proposal(output_dir)
    else:
        verification = None
        manifest = write_proposal(proposal, output_dir, created_at=created_at)

    summary: Dict[str, object] = {
        "output_dir": str(Path(output_dir)),
        "authority_status": AUTHORITY_STATUS_PROPOSAL_ONLY,
        "record_identity_scheme_status": RECORD_IDENTITY_SCHEME_STATUS_NOT_ACTIVATED,
        "migration_version": proposal.migration_version,
        "normalization_version": NORMALIZATION_VERSION,
        "legacy_workbook_sha256": proposal.legacy_sha256,
        "authority_workbook_sha256": proposal.authority_sha256,
        "record_grain": {
            "grain": "merchant_case_record",
            "legacy_records": proposal.legacy_record_count,
            "authority_records": proposal.authority_record_count,
            "confidence_counts": dict(proposal.confidence_counts),
            "reconciliation": dict(proposal.reconciliation),
        },
        "payload_changed_record_count": proposal.payload_changed_record_count,
        "asset_review_candidate_count": proposal.asset_review_candidate_count,
        "asset_review_candidate_field_count": proposal.asset_review_candidate_field_count,
        "stable_id_min": manifest["stable_id_min"],
        "stable_id_max": manifest["stable_id_max"],
        "stable_id_count": manifest["stable_id_count"],
        "content_digest": manifest["content_digest"],
        "manifest_hash": manifest["manifest_hash"],
        "verification": verification,
    }

    if decision_store is not None:
        summary["event_grain"] = analyze_decision_impact(
            decision_store, proposal.crosswalk_rows, source_sheet=legacy.source_sheet
        )

    return summary
