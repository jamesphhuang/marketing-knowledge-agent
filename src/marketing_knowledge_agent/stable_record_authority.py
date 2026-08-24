"""Stable-record authority materialization engine (M3D, materialization-only).

The M1 crosswalk proposal (``stable_record_crosswalk``) minted an identifier for every
merchant-case record and declared every one of them *pending*. A human then reviewed all 121 and
recorded a decision per record in a separate artifact. This module is the step that turns those
two independent pieces of evidence into a single **authority package**: the durable statement of
which ``MKA-MC-#####`` identity each merchant-case record has, and on whose decision.

Three distinctions are load-bearing, and every one of them is enforced rather than documented.

Materialized is not activated.
    A published authority package declares ``authority_status=materialized_not_activated`` and
    ``activation_status=not_activated``. It records that the identity set exists and was approved;
    it does not say any runtime reads it. ``row_v1`` remains the only authoritative mutation key
    until a separate activation work package says otherwise, so every row also carries
    ``row_v1_status=retained_not_retired``. "The authority exists" and "the runtime uses the
    authority" are different facts, and a consumer that conflates them would retire ``row_v1``
    on the strength of a file that never claimed that.

Identity is not content.
    ``approve_same_record`` and ``approve_new_record`` speak only to *which record this is*.
    Neither approves an asset, a URL, a payload change, or a search alias. Where the human review
    flagged one of those, the flag is carried through as an explicit *deferral*
    (``asset_review_status=required_separately``,
    ``alias_binding_status=requires_separate_decision``), never resolved. This module performs
    zero alias mutations and zero asset mutations by construction: it has no code path that can
    write either.

A row coordinate is not an identity.
    ``商家夥伴案例資料庫:r32`` names two different merchants depending on which workbook lineage
    is asking. A bare ``sheet:row`` key is therefore never a durable identity here. Legacy lineage
    is recorded as a **qualified** ``row_v1:<workbook_sha256>:<sheet>:r<row>``, and the bare
    ``legacy_source_row`` column is retained with an explicit role of
    ``audit_metadata_only_not_an_authority_key``.

Why external pins are mandatory rather than merely supported
------------------------------------------------------------
The proposal manifest is self-sealed: ``load_proposal`` recomputes both seals and refuses a
manifest that does not reproduce them. That defends against corruption and hand edits. It does
**not** defend against an operator who rewrites the CSVs and re-derives every seal afterwards,
because the result is internally consistent by construction — the loader has nothing left to
compare against. Re-deriving the seals from whatever is on disk today and accepting the result
therefore proves only that the directory is well-formed, never that it is the directory a human
reviewed.

So every entry point here takes an :class:`AuthorityEvidencePins` and compares the loaded evidence
against it. The pins arrive from outside the artifact — from the governance record of what was
reviewed — and are the only thing an attacker who controls the directory cannot re-derive. They
are supplied per call and never baked in as module constants: a pin hard-coded here would be a
statement that one particular 2026 review is the only one this engine can ever materialize.

The evidence binds three ways, which is deliberate. The proposal seals itself; the external pins
state what the reviewer saw; and the decision artifact independently records the proposal hashes
it was written against. All three must agree, so substituting any single one of them fails.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .stable_record_crosswalk import STABLE_ID_RE, load_proposal, render_csv


# --- versioned contracts ------------------------------------------------------------------------

SCHEMA_VERSION = 1

# Bumping this states that the authority rows mean something different than they did before. It is
# recorded in the manifest and on nothing else, because a schema version belongs to the package.
AUTHORITY_SCHEMA_VERSION = "stable-record-authority/v1"

MATERIALIZATION_VERSION = "stable-record-authority-materialization/m3d"

RECORD_IDENTITY_SCHEME = "stable_record_v2"
RECORD_TYPE_MERCHANT_CASE = "merchant_case"

# The package asserts that the identity set exists and was approved. It asserts nothing about what
# reads it. See the module docstring.
AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED = "materialized_not_activated"
ACTIVATION_STATUS_NOT_ACTIVATED = "not_activated"

AUTHORITY_RECORD_STATUS_CONTINUATION = "approved_identity_continuation"
AUTHORITY_RECORD_STATUS_NEW = "approved_new_identity"

IDENTITY_ORIGIN_LEGACY_CONTINUATION = "legacy_row_v1_continuation"
IDENTITY_ORIGIN_AUTHORITY_NEW = "authority_workbook_new_record"

DECISION_APPROVE_SAME_RECORD = "approve_same_record"
DECISION_APPROVE_NEW_RECORD = "approve_new_record"
# Exactly two decisions may be materialized. Everything else — pending, rejected,
# needs_more_information, or a value this module has never heard of — is a refusal rather than a
# skip, because a record silently absent from an authority is indistinguishable from one that was
# never proposed.
MATERIALIZABLE_DECISIONS = frozenset({DECISION_APPROVE_SAME_RECORD, DECISION_APPROVE_NEW_RECORD})

LEGACY_BINDING_LEGACY_BOUND = "legacy_bound"
LEGACY_BINDING_AUTHORITY_ONLY = "authority_only"

IDENTITY_SCOPE_CONTINUITY = "identity_continuity_only"
IDENTITY_SCOPE_NEW = "new_identity_only"

LEGACY_SOURCE_SCHEME_ROW_V1 = "row_v1"
# The bare row coordinate is kept so an auditor can find the source cell. Naming its role in the
# row itself is what stops a downstream consumer from joining on it.
LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY = "audit_metadata_only_not_an_authority_key"

ROW_V1_STATUS_RETAINED = "retained_not_retired"

ALIAS_BINDING_UNCHANGED = "unchanged_no_alias_decision_in_scope"
ALIAS_BINDING_REQUIRES_SEPARATE_DECISION = "requires_separate_decision"

ASSET_REVIEW_NOT_IN_SCOPE = "not_in_scope"
ASSET_REVIEW_REQUIRED_SEPARATELY = "required_separately"

PAYLOAD_CHANGE_NONE_RECORDED = "no_payload_change_recorded"
PAYLOAD_CHANGE_PRESENT_NOT_APPROVED = "payload_change_present_content_not_approved"

REGISTRY_FILENAME = "stable_record_registry.csv"
MANIFEST_FILENAME = "manifest.json"
RECEIPT_FILENAME = "materialization_receipt.json"

# The canonical destination a future activation work package would publish to. It is a *name*, not
# a target: nothing in this module creates it, and :func:`write_authority_package` refuses it
# unless a caller explicitly authorizes that destination.
PRODUCTION_AUTHORITY_RELPATH: Tuple[str, ...] = (
    "data",
    "identity",
    "authority",
    "stable_record_v2",
)


# --- field-specific parsing contract --------------------------------------------------------------
#
# The two source artifacts delimit multi-valued fields differently, and the difference is silent:
# splitting ``"A|B"`` on ``";"`` yields one token that happens to be a string, not an error. A
# single generic splitter applied to both fields therefore reads
# ``PAYLOAD_CHANGE_PRESENT|ASSET_REVIEW_REQUIRED_SEPARATELY`` as one unrecognized flag and drops
# the asset deferral entirely — the record still materializes, and the boundary it was supposed to
# preserve is gone. Each field gets its own parser, and each parser refuses the *other* field's
# delimiter outright, so a future artifact that changes separator fails loudly instead of silently
# collapsing to a single token.

MATCH_EVIDENCE_DELIMITER = ";"
SPECIAL_FLAGS_DELIMITER = "|"

FLAG_PAYLOAD_CHANGE_PRESENT = "PAYLOAD_CHANGE_PRESENT"
FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY = "ASSET_REVIEW_REQUIRED_SEPARATELY"
FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION = "ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION"

# An unrecognized flag is a refusal, not a pass-through. Every known flag maps to a boundary this
# module must *not* cross; a flag it cannot interpret may name a boundary it is about to cross
# unknowingly, and there is no safe default for that.
KNOWN_SPECIAL_FLAGS = frozenset(
    {
        FLAG_PAYLOAD_CHANGE_PRESENT,
        FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY,
        FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION,
    }
)

_WHITESPACE_RE = re.compile(r"\s+")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StableRecordAuthorityError(ValueError):
    """Raised when authority materialization cannot proceed safely.

    Every raise site is a refusal to publish. There is no partial-success return value: an
    authority that omitted the records it could not decide would be indistinguishable from one
    that was never asked about them.
    """


def _collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _split_field(
    value: object,
    *,
    delimiter: str,
    field_name: str,
    foreign_delimiters: Sequence[str],
) -> Tuple[str, ...]:
    """Split one delimited field, refusing any other artifact's delimiter."""
    text = "" if value is None else str(value)
    for foreign in foreign_delimiters:
        if foreign in text:
            raise StableRecordAuthorityError(
                f"{field_name} value {text!r} contains {foreign!r}, which is another artifact's "
                f"delimiter; {field_name} is delimited by {delimiter!r} and its semantics here "
                "are ambiguous"
            )
    if not text.strip():
        return ()
    tokens = [_collapse_whitespace(part) for part in text.split(delimiter)]
    return tuple(token for token in tokens if token)


def parse_match_evidence(value: object) -> Tuple[str, ...]:
    """Parse the M1 crosswalk ``match_evidence`` field (``;``-delimited)."""
    return _split_field(
        value,
        delimiter=MATCH_EVIDENCE_DELIMITER,
        field_name="match_evidence",
        foreign_delimiters=(SPECIAL_FLAGS_DELIMITER,),
    )


def parse_special_flags(value: object) -> Tuple[str, ...]:
    """Parse the M2 decision ``special_flags`` field (``|``-delimited).

    Fails closed on an unrecognized flag and on a repeated one. A duplicate is not merely
    redundant: it means the artifact was assembled by something that did not treat the field as a
    set, and the rest of its contents cannot be assumed well-formed either.
    """
    tokens = _split_field(
        value,
        delimiter=SPECIAL_FLAGS_DELIMITER,
        field_name="special_flags",
        foreign_delimiters=(MATCH_EVIDENCE_DELIMITER,),
    )
    unknown = sorted({token for token in tokens if token not in KNOWN_SPECIAL_FLAGS})
    if unknown:
        raise StableRecordAuthorityError(
            f"special_flags declares unrecognized flag(s) {unknown}; known flags are "
            f"{sorted(KNOWN_SPECIAL_FLAGS)}. A flag this engine cannot interpret may name a "
            "boundary it would otherwise cross, so it is refused rather than ignored"
        )
    duplicates = sorted({token for token in tokens if tokens.count(token) > 1})
    if duplicates:
        raise StableRecordAuthorityError(f"special_flags repeats flag(s) {duplicates}")
    return tokens


# --- external evidence pins ---------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorityEvidencePins:
    """The externally supplied statement of *which* evidence a human reviewed.

    Supplied by the caller from the governance record, never read out of the artifacts these pins
    are used to check. The four M1 values and the M2 decisions hash are required; the remaining M2
    companion artifacts and the off-volume backup manifest are optional, and each one supplied is
    additionally enforced.

    ``proposal_manifest_hash`` covers the proposal manifest including its ``created_at``, so
    pinning it binds the exact publication as well as its contents.
    """

    proposal_registry_sha256: str
    proposal_crosswalk_sha256: str
    proposal_content_digest: str
    proposal_manifest_hash: str
    decision_artifact_sha256: str
    reviewer: str
    reviewed_at: str
    decision_manifest_sha256: str = ""
    decision_apply_preview_sha256: str = ""
    decision_reissue_receipt_sha256: str = ""
    backup_manifest_sha256: str = ""

    def __post_init__(self) -> None:
        required = (
            "proposal_registry_sha256",
            "proposal_crosswalk_sha256",
            "proposal_content_digest",
            "proposal_manifest_hash",
            "decision_artifact_sha256",
        )
        for name in required:
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
                raise StableRecordAuthorityError(
                    f"evidence pin {name}={value!r} is not a sha256 hexdigest; an unpinned "
                    "materialization would accept whatever is on disk"
                )
        optional = (
            "decision_manifest_sha256",
            "decision_apply_preview_sha256",
            "decision_reissue_receipt_sha256",
            "backup_manifest_sha256",
        )
        for name in optional:
            value = getattr(self, name)
            if value and (not isinstance(value, str) or not _SHA256_HEX_RE.match(value)):
                raise StableRecordAuthorityError(
                    f"evidence pin {name}={value!r} is set but is not a sha256 hexdigest"
                )
        if not isinstance(self.reviewer, str) or not self.reviewer.strip():
            raise StableRecordAuthorityError(
                "evidence pin reviewer is empty; the contract requires an explicit reviewer so "
                "attribution is never inferred from a filesystem or Git identity"
            )
        if not isinstance(self.reviewed_at, str) or not _ISO_DATE_RE.match(self.reviewed_at):
            raise StableRecordAuthorityError(
                f"evidence pin reviewed_at={self.reviewed_at!r} is not an ISO YYYY-MM-DD date"
            )


def _require_pin(actual: str, expected: str, label: str) -> None:
    if not hmac.compare_digest(str(actual), str(expected)):
        raise StableRecordAuthorityError(
            f"{label} is {actual!r} but the external evidence pin declares {expected!r}; "
            "the artifact on disk is not the one that was reviewed"
        )


# --- M1 proposal evidence -------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedProposal:
    """An M1 proposal that has proved both its own seals and its external pins."""

    manifest: Mapping[str, object]
    registry_rows: Tuple[Mapping[str, str], ...]
    crosswalk_rows: Tuple[Mapping[str, str], ...]

    @property
    def asset_review_candidate_ids(self) -> Tuple[str, ...]:
        candidates = self.manifest.get("asset_review_candidates") or ()
        ids = []
        for item in candidates:
            if isinstance(item, Mapping) and isinstance(item.get("stable_record_id"), str):
                ids.append(item["stable_record_id"])
        return tuple(sorted(ids))


def load_proposal_evidence(proposal_dir: Path, pins: AuthorityEvidencePins) -> LoadedProposal:
    """Load the M1 proposal and bind it to the external pins.

    Two checks, in this order and never only the first:

    1. ``load_proposal`` proves the directory is a complete, self-consistent proposal — both
       manifest seals reproduce, each CSV matches its declared sha256, the columns are exact.
    2. The declared values are compared against the pins the caller supplied.

    Step 1 alone passes for any directory whose seals were re-derived after an edit, which is
    exactly the artifact a materializer must not accept. Step 2 alone would trust hashes read out
    of a file nothing has verified.
    """
    manifest, registry_rows, crosswalk_rows = load_proposal(Path(proposal_dir))

    for label, field_name, expected in (
        ("proposal registry_sha256", "registry_sha256", pins.proposal_registry_sha256),
        ("proposal crosswalk_sha256", "crosswalk_sha256", pins.proposal_crosswalk_sha256),
        ("proposal content_digest", "content_digest", pins.proposal_content_digest),
        ("proposal manifest_hash", "manifest_hash", pins.proposal_manifest_hash),
    ):
        _require_pin(str(manifest.get(field_name, "")), expected, label)

    return LoadedProposal(
        manifest=dict(manifest),
        registry_rows=tuple(dict(row) for row in registry_rows),
        crosswalk_rows=tuple(dict(row) for row in crosswalk_rows),
    )


# --- M2 human decision evidence ---------------------------------------------------------------------

DECISION_COLUMNS: Tuple[str, ...] = (
    "stable_record_id",
    "match_confidence",
    "review_scope",
    "identity_review_decision",
    "decision_source",
    "decision_channel",
    "reviewer",
    "reviewer_source",
    "reviewer_attribution_status",
    "reviewed_at",
    "reviewed_at_source",
    "review_note",
    "batch_id",
    "legacy_binding",
    "identity_scope",
    "special_flags",
    "source_excel_cell_state",
    "proposal_registry_sha256",
    "proposal_crosswalk_sha256",
    "proposal_content_digest",
    "review_package_workbook_sha256",
    "supersedes_artifact",
)


@dataclass(frozen=True)
class HumanDecision:
    """One human identity decision, parsed under the typed field contract."""

    stable_record_id: str
    match_confidence: str
    review_scope: str
    identity_review_decision: str
    decision_source: str
    decision_channel: str
    reviewer: str
    reviewed_at: str
    legacy_binding: str
    identity_scope: str
    special_flags: Tuple[str, ...]
    proposal_registry_sha256: str
    proposal_crosswalk_sha256: str
    proposal_content_digest: str

    @property
    def requires_asset_review(self) -> bool:
        return FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY in self.special_flags

    @property
    def requires_alias_decision(self) -> bool:
        return FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION in self.special_flags

    @property
    def has_payload_change(self) -> bool:
        return FLAG_PAYLOAD_CHANGE_PRESENT in self.special_flags


def parse_decision_rows(rows: Sequence[Mapping[str, str]]) -> Tuple[HumanDecision, ...]:
    """Turn decision CSV rows into typed decisions, refusing anything ambiguous."""
    decisions: List[HumanDecision] = []
    for index, row in enumerate(rows):
        if set(row) != set(DECISION_COLUMNS):
            raise StableRecordAuthorityError(
                f"decision row {index} column set {sorted(row)} does not match "
                f"{sorted(DECISION_COLUMNS)}"
            )
        stable_id = str(row["stable_record_id"]).strip()
        if not STABLE_ID_RE.match(stable_id):
            raise StableRecordAuthorityError(
                f"decision row {index} stable_record_id {stable_id!r} does not match "
                f"{STABLE_ID_RE.pattern}"
            )
        decisions.append(
            HumanDecision(
                stable_record_id=stable_id,
                match_confidence=str(row["match_confidence"]).strip(),
                review_scope=str(row["review_scope"]).strip(),
                identity_review_decision=str(row["identity_review_decision"]).strip(),
                decision_source=str(row["decision_source"]).strip(),
                decision_channel=str(row["decision_channel"]).strip(),
                reviewer=_collapse_whitespace(str(row["reviewer"])),
                reviewed_at=str(row["reviewed_at"]).strip(),
                legacy_binding=str(row["legacy_binding"]).strip(),
                identity_scope=str(row["identity_scope"]).strip(),
                special_flags=parse_special_flags(row["special_flags"]),
                proposal_registry_sha256=str(row["proposal_registry_sha256"]).strip(),
                proposal_crosswalk_sha256=str(row["proposal_crosswalk_sha256"]).strip(),
                proposal_content_digest=str(row["proposal_content_digest"]).strip(),
            )
        )
    return tuple(decisions)


def load_decision_artifact(
    decisions_path: Path, pins: AuthorityEvidencePins
) -> Tuple[HumanDecision, ...]:
    """Read the M2 final decision CSV, binding its bytes to the external pin first.

    The hash is checked before the bytes are parsed, so a substituted artifact is refused rather
    than interpreted.
    """
    decisions_path = Path(decisions_path)
    if not decisions_path.is_file():
        raise StableRecordAuthorityError(f"decision artifact {decisions_path} is missing")
    raw = decisions_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    _require_pin(actual, pins.decision_artifact_sha256, f"decision artifact {decisions_path} sha256")

    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    if reader.fieldnames != list(DECISION_COLUMNS):
        raise StableRecordAuthorityError(
            f"decision artifact {decisions_path} columns {reader.fieldnames} do not match "
            f"{list(DECISION_COLUMNS)}"
        )
    return parse_decision_rows([dict(row) for row in reader])


def verify_companion_artifacts(
    artifact_dir: Path, pins: AuthorityEvidencePins
) -> Dict[str, str]:
    """Verify each optional M2 companion artifact the caller pinned.

    A pin that is set must be satisfiable: a missing file is a refusal, not a skipped check.
    """
    artifact_dir = Path(artifact_dir)
    verified: Dict[str, str] = {}
    for filename, expected in (
        ("human_review_decisions_manifest.json", pins.decision_manifest_sha256),
        ("apply_preview.json", pins.decision_apply_preview_sha256),
        ("reissue_receipt.json", pins.decision_reissue_receipt_sha256),
    ):
        if not expected:
            continue
        path = artifact_dir / filename
        if not path.is_file():
            raise StableRecordAuthorityError(
                f"companion artifact {path} is pinned but missing; a pinned check may not be "
                "skipped"
            )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        _require_pin(actual, expected, f"companion artifact {path} sha256")
        verified[filename] = actual
    return verified


# --- authority row schema -------------------------------------------------------------------------

AUTHORITY_COLUMNS: Tuple[str, ...] = (
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


def qualify_legacy_record_id(workbook_sha256: str, sheet: str, row: object) -> str:
    """Render the qualified legacy identity ``row_v1:<workbook_sha256>:<sheet>:r<row>``.

    The workbook hash is what makes the key durable. Without it, ``商家夥伴案例資料庫:r32``
    designates a different merchant in each of the two workbook lineages, and a downstream join on
    the bare key silently attributes one merchant's decision to another.
    """
    if not _SHA256_HEX_RE.match(str(workbook_sha256)):
        raise StableRecordAuthorityError(
            f"legacy workbook sha256 {workbook_sha256!r} is not a sha256 hexdigest; an unqualified "
            "row key is not a durable identity"
        )
    sheet_text = _collapse_whitespace(str(sheet))
    if not sheet_text:
        raise StableRecordAuthorityError("legacy source sheet is empty; the row key cannot be qualified")
    row_text = str(row).strip()
    if not row_text.isdigit() or int(row_text) < 1:
        raise StableRecordAuthorityError(
            f"legacy source row {row!r} is not a positive integer row coordinate"
        )
    return f"{LEGACY_SOURCE_SCHEME_ROW_V1}:{workbook_sha256}:{sheet_text}:r{int(row_text)}"


# --- materialization ------------------------------------------------------------------------------


@dataclass(frozen=True)
class StableRecordAuthority:
    """The in-memory authority package: rows plus the counts a manifest must bind."""

    rows: Tuple[Mapping[str, str], ...]
    pins: AuthorityEvidencePins
    record_count: int
    identity_continuation_count: int
    new_identity_count: int
    confidence_counts: Mapping[str, int]
    asset_review_required_ids: Tuple[str, ...]
    alias_decision_required_ids: Tuple[str, ...]
    payload_change_ids: Tuple[str, ...]
    stable_id_set_digest: str
    source_proposal_manifest: Mapping[str, object] = field(repr=False, default_factory=dict)

    @property
    def stable_record_ids(self) -> Tuple[str, ...]:
        return tuple(row["stable_record_id"] for row in self.rows)


def _stable_id_set_digest(stable_ids: Iterable[str]) -> str:
    """Digest the identity *set*, order-independently.

    Sorting before hashing is what makes this a statement about which identities exist rather than
    about the order a particular run happened to emit them in.
    """
    body = "\n".join(sorted(set(stable_ids))) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_stable_record_authority(
    proposal: LoadedProposal,
    human_decisions: Sequence[HumanDecision],
    evidence_pins: AuthorityEvidencePins,
) -> StableRecordAuthority:
    """Reconcile the proposal against the human decisions and lay out the authority rows.

    Writes nothing, mutates neither input, and never writes a decision back into the proposal:
    the two artifacts are independent evidence and must stay independently auditable. Every
    disagreement between them is a refusal — there is no reconciliation rule that prefers one
    side, because either side being wrong means the identity binding is unknown.
    """
    crosswalk_by_id: Dict[str, Mapping[str, str]] = {}
    for row in proposal.crosswalk_rows:
        stable_id = row["stable_record_id"]
        if stable_id in crosswalk_by_id:
            raise StableRecordAuthorityError(
                f"proposal crosswalk declares {stable_id} more than once; one identifier may "
                "describe exactly one record"
            )
        crosswalk_by_id[stable_id] = row

    registry_by_id: Dict[str, Mapping[str, str]] = {}
    for row in proposal.registry_rows:
        stable_id = row["stable_record_id"]
        if stable_id in registry_by_id:
            raise StableRecordAuthorityError(
                f"proposal registry declares {stable_id} more than once; one identifier may "
                "describe exactly one record"
            )
        registry_by_id[stable_id] = row

    if set(registry_by_id) != set(crosswalk_by_id):
        only_registry = sorted(set(registry_by_id) - set(crosswalk_by_id))
        only_crosswalk = sorted(set(crosswalk_by_id) - set(registry_by_id))
        raise StableRecordAuthorityError(
            "proposal registry and crosswalk do not describe the same identities; "
            f"registry-only={only_registry}, crosswalk-only={only_crosswalk}"
        )

    decisions_by_id: Dict[str, HumanDecision] = {}
    for decision in human_decisions:
        if decision.stable_record_id in decisions_by_id:
            raise StableRecordAuthorityError(
                f"human decisions declare {decision.stable_record_id} more than once; a record "
                "with two decisions has no decision"
            )
        decisions_by_id[decision.stable_record_id] = decision

    unknown = sorted(set(decisions_by_id) - set(crosswalk_by_id))
    if unknown:
        raise StableRecordAuthorityError(
            f"human decisions reference stable IDs the proposal never issued: {unknown}"
        )
    missing = sorted(set(crosswalk_by_id) - set(decisions_by_id))
    if missing:
        raise StableRecordAuthorityError(
            f"proposal records have no human decision: {missing}; an undecided record may not be "
            "materialized and may not be silently dropped"
        )

    proposal_asset_candidates = set(proposal.asset_review_candidate_ids)

    rows: List[Dict[str, str]] = []
    confidence_counts: Dict[str, int] = {}
    asset_ids: List[str] = []
    alias_ids: List[str] = []
    payload_ids: List[str] = []
    continuations = 0
    new_identities = 0

    for stable_id in sorted(crosswalk_by_id):
        crosswalk = crosswalk_by_id[stable_id]
        registry = registry_by_id[stable_id]
        decision = decisions_by_id[stable_id]

        if decision.identity_review_decision not in MATERIALIZABLE_DECISIONS:
            raise StableRecordAuthorityError(
                f"{stable_id} carries identity_review_decision="
                f"{decision.identity_review_decision!r}; only "
                f"{sorted(MATERIALIZABLE_DECISIONS)} may be materialized"
            )

        # The decision artifact independently records which proposal it was written against. It
        # must agree with the external pins, or the human reviewed a different proposal than the
        # one being materialized.
        for label, actual, expected in (
            ("registry_sha256", decision.proposal_registry_sha256, evidence_pins.proposal_registry_sha256),
            ("crosswalk_sha256", decision.proposal_crosswalk_sha256, evidence_pins.proposal_crosswalk_sha256),
            ("content_digest", decision.proposal_content_digest, evidence_pins.proposal_content_digest),
        ):
            _require_pin(actual, expected, f"{stable_id} decision-declared proposal {label}")

        if decision.reviewer != evidence_pins.reviewer:
            raise StableRecordAuthorityError(
                f"{stable_id} records reviewer {decision.reviewer!r} but the contract pins "
                f"{evidence_pins.reviewer!r}; reviewer attribution is never inferred"
            )
        if decision.reviewed_at != evidence_pins.reviewed_at:
            raise StableRecordAuthorityError(
                f"{stable_id} records reviewed_at {decision.reviewed_at!r} but the contract pins "
                f"{evidence_pins.reviewed_at!r}"
            )

        if decision.match_confidence != crosswalk["match_confidence"]:
            raise StableRecordAuthorityError(
                f"{stable_id} match_confidence disagrees: proposal says "
                f"{crosswalk['match_confidence']!r}, decision says {decision.match_confidence!r}"
            )

        legacy_sheet = str(crosswalk["legacy_source_sheet"]).strip()
        legacy_row = str(crosswalk["legacy_source_row"]).strip()
        legacy_sha = str(crosswalk["legacy_workbook_sha256"]).strip()
        proposal_is_legacy_bound = bool(legacy_row)

        # A legacy-bound record continues an existing identity; an authority-only record creates
        # one. Approving the wrong kind is the failure that quietly merges two merchants or splits
        # one, so the two artifacts must agree on which kind this is before the decision is read.
        if proposal_is_legacy_bound != (decision.legacy_binding == LEGACY_BINDING_LEGACY_BOUND):
            raise StableRecordAuthorityError(
                f"{stable_id} legacy binding disagrees: the proposal "
                f"{'has' if proposal_is_legacy_bound else 'has no'} legacy lineage but the "
                f"decision declares legacy_binding={decision.legacy_binding!r}"
            )
        if decision.legacy_binding not in (LEGACY_BINDING_LEGACY_BOUND, LEGACY_BINDING_AUTHORITY_ONLY):
            raise StableRecordAuthorityError(
                f"{stable_id} declares legacy_binding={decision.legacy_binding!r}, which is "
                f"neither {LEGACY_BINDING_LEGACY_BOUND!r} nor {LEGACY_BINDING_AUTHORITY_ONLY!r}"
            )

        if proposal_is_legacy_bound:
            if decision.identity_review_decision != DECISION_APPROVE_SAME_RECORD:
                raise StableRecordAuthorityError(
                    f"{stable_id} is legacy-bound but carries "
                    f"{decision.identity_review_decision!r}; approving a legacy-bound record as a "
                    "new identity would orphan every decision already bound to its predecessor"
                )
            if decision.identity_scope != IDENTITY_SCOPE_CONTINUITY:
                raise StableRecordAuthorityError(
                    f"{stable_id} is legacy-bound but declares identity_scope="
                    f"{decision.identity_scope!r}; expected {IDENTITY_SCOPE_CONTINUITY!r}"
                )
            authority_record_status = AUTHORITY_RECORD_STATUS_CONTINUATION
            identity_origin = IDENTITY_ORIGIN_LEGACY_CONTINUATION
            legacy_record_id = qualify_legacy_record_id(legacy_sha, legacy_sheet, legacy_row)
            legacy_scheme = LEGACY_SOURCE_SCHEME_ROW_V1
            continuations += 1
        else:
            if decision.identity_review_decision != DECISION_APPROVE_NEW_RECORD:
                raise StableRecordAuthorityError(
                    f"{stable_id} has no legacy lineage but carries "
                    f"{decision.identity_review_decision!r}; approving it as a continuation would "
                    "assert a predecessor that does not exist"
                )
            if decision.identity_scope != IDENTITY_SCOPE_NEW:
                raise StableRecordAuthorityError(
                    f"{stable_id} is authority-only but declares identity_scope="
                    f"{decision.identity_scope!r}; expected {IDENTITY_SCOPE_NEW!r}"
                )
            if legacy_sheet or legacy_sha:
                raise StableRecordAuthorityError(
                    f"{stable_id} has no legacy row but declares partial legacy lineage "
                    f"(sheet={legacy_sheet!r}, workbook={legacy_sha!r})"
                )
            authority_record_status = AUTHORITY_RECORD_STATUS_NEW
            identity_origin = IDENTITY_ORIGIN_AUTHORITY_NEW
            legacy_record_id = ""
            legacy_scheme = ""
            new_identities += 1

        # Flags are cross-checked against the proposal's own evidence wherever the proposal has an
        # opinion. A payload or asset flag that the proposal does not corroborate means the two
        # artifacts describe different records.
        proposal_payload_change = bool(str(crosswalk["payload_change_fields"]).strip())
        if decision.has_payload_change != proposal_payload_change:
            raise StableRecordAuthorityError(
                f"{stable_id} payload-change evidence disagrees: the proposal records "
                f"payload_change_fields={crosswalk['payload_change_fields']!r} but the decision "
                f"{'carries' if decision.has_payload_change else 'omits'} "
                f"{FLAG_PAYLOAD_CHANGE_PRESENT}"
            )
        proposal_asset_candidate = stable_id in proposal_asset_candidates
        if decision.requires_asset_review != proposal_asset_candidate:
            raise StableRecordAuthorityError(
                f"{stable_id} asset-review evidence disagrees: the proposal "
                f"{'lists' if proposal_asset_candidate else 'does not list'} it as an asset review "
                f"candidate but the decision "
                f"{'carries' if decision.requires_asset_review else 'omits'} "
                f"{FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY}"
            )

        if decision.requires_asset_review:
            asset_ids.append(stable_id)
        if decision.requires_alias_decision:
            alias_ids.append(stable_id)
        if decision.has_payload_change:
            payload_ids.append(stable_id)

        confidence_counts[decision.match_confidence] = (
            confidence_counts.get(decision.match_confidence, 0) + 1
        )

        rows.append(
            {
                "stable_record_id": stable_id,
                "record_identity_scheme": RECORD_IDENTITY_SCHEME,
                "authority_record_status": authority_record_status,
                "authority_status": AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
                "activation_status": ACTIVATION_STATUS_NOT_ACTIVATED,
                "identity_origin": identity_origin,
                "legacy_source_record_id": legacy_record_id,
                "legacy_source_scheme": legacy_scheme,
                "legacy_source_sheet": legacy_sheet,
                "legacy_source_row": legacy_row,
                # Stated on every row, including the rows that have no legacy coordinate at all,
                # so the column can never be read as an authority key by omission.
                "legacy_source_row_role": LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY,
                "legacy_workbook_sha256": legacy_sha,
                "authority_source_sheet": str(crosswalk["authority_source_sheet"]).strip(),
                "authority_source_row": str(crosswalk["authority_source_row"]).strip(),
                "authority_workbook_sha256": str(crosswalk["authority_workbook_sha256"]).strip(),
                "record_type": str(registry["record_type"]).strip(),
                "match_confidence": decision.match_confidence,
                "match_evidence": MATCH_EVIDENCE_DELIMITER.join(
                    parse_match_evidence(crosswalk["match_evidence"])
                ),
                "match_evidence_normalization": str(
                    crosswalk["match_evidence_normalization"]
                ).strip(),
                "human_decision": decision.identity_review_decision,
                "identity_scope": decision.identity_scope,
                "review_scope": decision.review_scope,
                "decision_source": decision.decision_source,
                "decision_channel": decision.decision_channel,
                "reviewer": decision.reviewer,
                "reviewed_at": decision.reviewed_at,
                "seed_derivation_digest": str(registry["seed_derivation_digest"]).strip(),
                "row_v1_status": ROW_V1_STATUS_RETAINED,
                "alias_binding_status": (
                    ALIAS_BINDING_REQUIRES_SEPARATE_DECISION
                    if decision.requires_alias_decision
                    else ALIAS_BINDING_UNCHANGED
                ),
                "asset_review_status": (
                    ASSET_REVIEW_REQUIRED_SEPARATELY
                    if decision.requires_asset_review
                    else ASSET_REVIEW_NOT_IN_SCOPE
                ),
                "payload_change_status": (
                    PAYLOAD_CHANGE_PRESENT_NOT_APPROVED
                    if decision.has_payload_change
                    else PAYLOAD_CHANGE_NONE_RECORDED
                ),
                "special_flags": SPECIAL_FLAGS_DELIMITER.join(decision.special_flags),
                "source_proposal_registry_sha256": evidence_pins.proposal_registry_sha256,
                "source_proposal_crosswalk_sha256": evidence_pins.proposal_crosswalk_sha256,
                "source_proposal_content_digest": evidence_pins.proposal_content_digest,
                "source_proposal_manifest_hash": evidence_pins.proposal_manifest_hash,
                "source_decision_artifact_sha256": evidence_pins.decision_artifact_sha256,
            }
        )

    authority = StableRecordAuthority(
        rows=tuple(rows),
        pins=evidence_pins,
        record_count=len(rows),
        identity_continuation_count=continuations,
        new_identity_count=new_identities,
        confidence_counts=dict(sorted(confidence_counts.items())),
        asset_review_required_ids=tuple(sorted(asset_ids)),
        alias_decision_required_ids=tuple(sorted(alias_ids)),
        payload_change_ids=tuple(sorted(payload_ids)),
        stable_id_set_digest=_stable_id_set_digest(row["stable_record_id"] for row in rows),
        source_proposal_manifest=dict(proposal.manifest),
    )
    validate_authority(authority)
    return authority


def validate_authority(authority: StableRecordAuthority) -> None:
    """Fail closed on any structurally unsound authority.

    Runs on every build and again inside the writer, before a single byte reaches disk.
    """
    errors: List[str] = []

    if not authority.rows:
        raise StableRecordAuthorityError("authority contains no records; refusing to publish an empty authority")

    # Shape first, and as an immediate raise rather than a collected error: every check below
    # indexes columns by name, so continuing past a row whose shape is unknown would surface a
    # KeyError instead of a refusal — and a caller failing closed on this module's error type
    # would not catch it.
    for index, row in enumerate(authority.rows):
        if set(row) != set(AUTHORITY_COLUMNS):
            raise StableRecordAuthorityError(
                f"authority validation failed:\nrow {index} column set {sorted(row)} does not "
                f"match {sorted(AUTHORITY_COLUMNS)}"
            )

    seen: Dict[str, int] = {}
    for index, row in enumerate(authority.rows):
        stable_id = row["stable_record_id"]
        seen[stable_id] = seen.get(stable_id, 0) + 1
        if not STABLE_ID_RE.match(stable_id):
            errors.append(f"row {index} stable_record_id {stable_id!r} is malformed")
        if row["record_identity_scheme"] != RECORD_IDENTITY_SCHEME:
            errors.append(f"{stable_id}: record_identity_scheme must be {RECORD_IDENTITY_SCHEME!r}")
        if row["authority_status"] != AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED:
            errors.append(
                f"{stable_id}: authority_status must be "
                f"{AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED!r}"
            )
        if row["activation_status"] != ACTIVATION_STATUS_NOT_ACTIVATED:
            errors.append(f"{stable_id}: activation_status must be {ACTIVATION_STATUS_NOT_ACTIVATED!r}")
        if row["row_v1_status"] != ROW_V1_STATUS_RETAINED:
            errors.append(f"{stable_id}: row_v1_status must be {ROW_V1_STATUS_RETAINED!r}")
        if row["legacy_source_row_role"] != LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY:
            errors.append(
                f"{stable_id}: legacy_source_row_role must be "
                f"{LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY!r} on every row"
            )
        status = row["authority_record_status"]
        if status not in (AUTHORITY_RECORD_STATUS_CONTINUATION, AUTHORITY_RECORD_STATUS_NEW):
            errors.append(f"{stable_id}: unknown authority_record_status {status!r}")
        if status == AUTHORITY_RECORD_STATUS_CONTINUATION:
            expected_prefix = f"{LEGACY_SOURCE_SCHEME_ROW_V1}:"
            if not row["legacy_source_record_id"].startswith(expected_prefix):
                errors.append(
                    f"{stable_id}: a continuation must carry a qualified legacy identity "
                    f"beginning {expected_prefix!r}, found {row['legacy_source_record_id']!r}"
                )
        elif row["legacy_source_record_id"]:
            errors.append(
                f"{stable_id}: a new identity must not carry a legacy identity "
                f"({row['legacy_source_record_id']!r})"
            )

    duplicates = sorted(stable_id for stable_id, count in seen.items() if count > 1)
    if duplicates:
        errors.append(f"duplicate stable_record_id: {duplicates}")

    ids = [row["stable_record_id"] for row in authority.rows]
    if ids != sorted(ids):
        errors.append("authority rows are not in ascending stable_record_id order")

    if authority.record_count != len(authority.rows):
        errors.append(
            f"record_count {authority.record_count} does not match the {len(authority.rows)} rows"
        )
    if authority.identity_continuation_count + authority.new_identity_count != len(authority.rows):
        errors.append(
            f"continuation ({authority.identity_continuation_count}) + new "
            f"({authority.new_identity_count}) does not account for {len(authority.rows)} rows"
        )
    recomputed = _stable_id_set_digest(ids)
    if recomputed != authority.stable_id_set_digest:
        errors.append(
            f"stable_id_set_digest {authority.stable_id_set_digest} does not match the rows "
            f"({recomputed})"
        )

    # The boundary invariant, stated as an assertion rather than a comment: a record whose review
    # deferred the alias decision is still an identity continuation, and this package still records
    # zero alias mutations. Only the deferral is carried.
    for row in authority.rows:
        flags = parse_special_flags(row["special_flags"])
        alias_expected = (
            ALIAS_BINDING_REQUIRES_SEPARATE_DECISION
            if FLAG_ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION in flags
            else ALIAS_BINDING_UNCHANGED
        )
        if row["alias_binding_status"] != alias_expected:
            errors.append(
                f"{row['stable_record_id']}: alias_binding_status {row['alias_binding_status']!r} "
                f"does not follow from special_flags {list(flags)}"
            )
        asset_expected = (
            ASSET_REVIEW_REQUIRED_SEPARATELY
            if FLAG_ASSET_REVIEW_REQUIRED_SEPARATELY in flags
            else ASSET_REVIEW_NOT_IN_SCOPE
        )
        if row["asset_review_status"] != asset_expected:
            errors.append(
                f"{row['stable_record_id']}: asset_review_status {row['asset_review_status']!r} "
                f"does not follow from special_flags {list(flags)}"
            )

    if errors:
        raise StableRecordAuthorityError("authority validation failed:\n" + "\n".join(errors))


# --- manifest and receipt -------------------------------------------------------------------------
#
# Sealed the same way the proposal is, and for the same reasons: ``content_digest`` covers the
# semantic body and excludes ``created_at`` so two runs over identical inputs agree, and
# ``manifest_hash`` covers the whole file including ``created_at`` so the file is self-verifying.
# The exclusion list is deliberate — a field added later is covered by ``content_digest``
# automatically, and a field silently escaping the digest is the failure worth engineering against.

MANIFEST_CONTENT_DIGEST_FIELD = "content_digest"
MANIFEST_HASH_FIELD = "manifest_hash"
MANIFEST_CREATED_AT_FIELD = "created_at"

MANIFEST_CONTENT_DIGEST_EXCLUDED_FIELDS = frozenset(
    {MANIFEST_CREATED_AT_FIELD, MANIFEST_CONTENT_DIGEST_FIELD, MANIFEST_HASH_FIELD}
)
MANIFEST_HASH_EXCLUDED_FIELDS = frozenset({MANIFEST_HASH_FIELD})


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def content_digest_body(manifest: Mapping[str, object]) -> Dict[str, object]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in MANIFEST_CONTENT_DIGEST_EXCLUDED_FIELDS
    }


def compute_content_digest(manifest: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(content_digest_body(manifest))).hexdigest()


def compute_manifest_hash(manifest: Mapping[str, object]) -> str:
    body = {key: value for key, value in manifest.items() if key not in MANIFEST_HASH_EXCLUDED_FIELDS}
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def verify_authority_manifest_integrity(
    manifest: Mapping[str, object], source: str = MANIFEST_FILENAME
) -> None:
    """Recompute both seals and refuse a manifest that does not reproduce them."""
    if not isinstance(manifest, Mapping):
        raise StableRecordAuthorityError(f"{source} is not a JSON object")
    for field_name in (MANIFEST_CONTENT_DIGEST_FIELD, MANIFEST_HASH_FIELD):
        if field_name not in manifest:
            raise StableRecordAuthorityError(
                f"{source} declares no {field_name}; an unsealed manifest is never loadable"
            )
        declared = manifest[field_name]
        if not isinstance(declared, str) or not _SHA256_HEX_RE.match(declared):
            raise StableRecordAuthorityError(
                f"{source} declares a malformed {field_name} ({declared!r})"
            )
    for field_name, recomputed in (
        (MANIFEST_CONTENT_DIGEST_FIELD, compute_content_digest(manifest)),
        (MANIFEST_HASH_FIELD, compute_manifest_hash(manifest)),
    ):
        if not hmac.compare_digest(recomputed, str(manifest[field_name])):
            raise StableRecordAuthorityError(
                f"{source} {field_name} does not match its contents (declared "
                f"{manifest[field_name]}, recomputed {recomputed}); the manifest has been modified "
                "since it was published"
            )


def build_authority_manifest(
    authority: StableRecordAuthority,
    registry_bytes: bytes,
    created_at: Optional[str] = None,
) -> Dict[str, object]:
    """Build the authority manifest.

    Every mutation counter below is a zero this module can prove rather than promise: it holds no
    handle to an alias projection, an asset authority, a vault, a decision store, or a content
    index, and imports none of them.
    """
    pins = authority.pins
    stable_ids = sorted(row["stable_record_id"] for row in authority.rows)

    body: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "authority_schema_version": AUTHORITY_SCHEMA_VERSION,
        "materialization_version": MATERIALIZATION_VERSION,
        "record_identity_scheme": RECORD_IDENTITY_SCHEME,
        # Materialized is not activated. Both statements are made explicitly so no consumer has to
        # infer the second from the first.
        "authority_status": AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
        "activation_status": ACTIVATION_STATUS_NOT_ACTIVATED,
        "authority_materialized": True,
        "stable_record_v2_activated": False,
        "row_v1_retired": False,
        "registry_filename": REGISTRY_FILENAME,
        "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
        "registry_columns": list(AUTHORITY_COLUMNS),
        "record_count": authority.record_count,
        "identity_continuation_count": authority.identity_continuation_count,
        "new_identity_count": authority.new_identity_count,
        "confidence_counts": dict(authority.confidence_counts),
        "stable_id_min": stable_ids[0],
        "stable_id_max": stable_ids[-1],
        "stable_id_set_digest": authority.stable_id_set_digest,
        # Record grain, stated because the decision store holds a different one. Row-bound decision
        # events are not identities and were never expanded into any.
        "grain": "merchant_case_record",
        "source_proposal": {
            "registry_sha256": pins.proposal_registry_sha256,
            "crosswalk_sha256": pins.proposal_crosswalk_sha256,
            "content_digest": pins.proposal_content_digest,
            "manifest_hash": pins.proposal_manifest_hash,
        },
        "source_decision_artifact": {
            "decisions_sha256": pins.decision_artifact_sha256,
            "decisions_manifest_sha256": pins.decision_manifest_sha256,
            "apply_preview_sha256": pins.decision_apply_preview_sha256,
            "reissue_receipt_sha256": pins.decision_reissue_receipt_sha256,
        },
        "backup_manifest_sha256": pins.backup_manifest_sha256,
        "reviewer": pins.reviewer,
        "review_date": pins.reviewed_at,
        "match_evidence_delimiter": MATCH_EVIDENCE_DELIMITER,
        "special_flags_delimiter": SPECIAL_FLAGS_DELIMITER,
        "asset_review_required_records": list(authority.asset_review_required_ids),
        "alias_decision_required_records": list(authority.alias_decision_required_ids),
        "payload_change_records": list(authority.payload_change_ids),
        "alias_mutations": 0,
        "asset_mutations": 0,
        "vault_mutations": 0,
        "decision_store_mutations": 0,
        "content_index_mutations": 0,
        "approved_url_authority_mutations": 0,
        "row_v1_authority_mutations": 0,
        "proposal_mutations": 0,
        "decision_artifact_mutations": 0,
        "production_reindex_authorized": False,
        "contains_merchant_roster": False,
    }

    body[MANIFEST_CONTENT_DIGEST_FIELD] = compute_content_digest(body)
    body[MANIFEST_CREATED_AT_FIELD] = created_at or datetime.now(timezone.utc).isoformat()
    body[MANIFEST_HASH_FIELD] = compute_manifest_hash(body)
    return body


def build_materialization_receipt(
    authority: StableRecordAuthority,
    manifest: Mapping[str, object],
) -> Dict[str, object]:
    """Build the audit receipt. It is evidence, never a runtime input.

    It deliberately records no destination path. The receipt is published *inside* the directory
    it describes, so the path would be redundant, and embedding an absolute one would both make
    the bundle non-relocatable and make the same evidence render different bytes depending on
    where it was written.
    """
    return {
        "artifact_type": "stable_record_authority_materialization_receipt",
        "materialization_version": MATERIALIZATION_VERSION,
        "authority_schema_version": AUTHORITY_SCHEMA_VERSION,
        "is_authority": True,
        "is_activated": False,
        "authority_status": AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
        "activation_status": ACTIVATION_STATUS_NOT_ACTIVATED,
        "record_count": authority.record_count,
        "identity_continuation_count": authority.identity_continuation_count,
        "new_identity_count": authority.new_identity_count,
        "new_identity_records": [
            row["stable_record_id"]
            for row in authority.rows
            if row["authority_record_status"] == AUTHORITY_RECORD_STATUS_NEW
        ],
        "stable_id_set_digest": authority.stable_id_set_digest,
        "manifest_content_digest": manifest.get(MANIFEST_CONTENT_DIGEST_FIELD),
        "manifest_hash": manifest.get(MANIFEST_HASH_FIELD),
        "registry_sha256": manifest.get("registry_sha256"),
        "deferred_decisions": {
            "alias_rebinding_requires_separate_decision": list(
                authority.alias_decision_required_ids
            ),
            "asset_review_required_separately": list(authority.asset_review_required_ids),
            "payload_change_present_content_not_approved": list(authority.payload_change_ids),
        },
        "boundaries": {
            "alias_mutations": 0,
            "asset_mutations": 0,
            "vault_mutations": 0,
            "decision_store_mutations": 0,
            "content_index_mutations": 0,
            "approved_url_authority_mutations": 0,
            "row_v1_authority_mutations": 0,
            "proposal_mutations": 0,
            "decision_artifact_mutations": 0,
        },
        "gate_state": {
            "AUTHORITY_MATERIALIZED": "YES",
            "STABLE_RECORD_V2_ACTIVATED": "NO",
            "ROW_V1_RETIRED": "NO",
            "PRODUCTION_REINDEX_AUTHORIZED": "NO",
            "ALIAS_REBINDING_REQUIRES_SEPARATE_DECISION": "YES",
        },
    }


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


# --- publication ------------------------------------------------------------------------------------


def _is_production_authority_path(path: Path) -> bool:
    parts = tuple(Path(path).resolve().parts)
    return parts[-len(PRODUCTION_AUTHORITY_RELPATH) :] == PRODUCTION_AUTHORITY_RELPATH


def write_authority_package(
    authority: StableRecordAuthority,
    output_dir: Path,
    created_at: Optional[str] = None,
    *,
    authorize_production_destination: bool = False,
) -> Dict[str, object]:
    """Validate, then publish the authority as a whole directory or not at all.

    Order matters and is the contract:

    1. the destination is checked — an existing non-empty directory is a refusal, and the canonical
       production path is a refusal unless explicitly authorized. Nothing is created yet, so a
       refused destination does not even leave a parent directory behind
    2. the authority is validated
    3. every byte is built in memory, and the manifest's own seals are re-verified against the
       bytes that will actually be written
    4. the bytes are staged into a sibling temporary directory
    5. a single :func:`os.replace` moves the staging directory into place

    A run that dies at any point before step 5 leaves the destination untouched, so no reader can
    ever observe a partially written authority. The staging directory is removed on failure.

    ``authorize_production_destination`` defaults to ``False`` so that publishing the real
    authority is an explicit act by a caller that intends it, not a side effect of passing a path.
    """
    output_dir = Path(output_dir)

    if _is_production_authority_path(output_dir) and not authorize_production_destination:
        raise StableRecordAuthorityError(
            f"{output_dir} is the canonical production authority destination "
            f"({'/'.join(PRODUCTION_AUTHORITY_RELPATH)}); publishing there requires "
            "authorize_production_destination=True and a governance decision that authorizes "
            "materialization"
        )

    if output_dir.exists():
        if not output_dir.is_dir():
            raise StableRecordAuthorityError(f"authority output path {output_dir} is not a directory")
        existing = sorted(item.name for item in output_dir.iterdir())
        if existing:
            raise StableRecordAuthorityError(
                f"authority output directory {output_dir} is not empty (contains {existing}); "
                "refusing to overwrite an existing authority"
            )

    validate_authority(authority)

    registry_bytes = render_csv(authority.rows, AUTHORITY_COLUMNS)
    manifest = build_authority_manifest(authority, registry_bytes, created_at=created_at)
    verify_authority_manifest_integrity(manifest)
    receipt = build_materialization_receipt(authority, manifest)

    manifest_payload = _json_bytes(manifest)
    receipt_payload = _json_bytes(receipt)

    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging.", dir=parent))
    try:
        (staging / REGISTRY_FILENAME).write_bytes(registry_bytes)
        (staging / MANIFEST_FILENAME).write_bytes(manifest_payload)
        (staging / RECEIPT_FILENAME).write_bytes(receipt_payload)
        if output_dir.exists():
            output_dir.rmdir()
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return manifest


def load_authority_package(output_dir: Path) -> Tuple[Dict[str, object], List[Dict[str, str]]]:
    """Load a published authority, refusing anything that is not a complete and intact one.

    Ordered like the proposal loader, and for the same reason: nothing the manifest *says* may
    influence a decision before the manifest has proved it is the file that was published.
    """
    output_dir = Path(output_dir)
    manifest_path = output_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise StableRecordAuthorityError(
            f"{output_dir} has no {MANIFEST_FILENAME}; it is not a published authority"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StableRecordAuthorityError(f"{manifest_path} could not be read: {exc}") from exc

    verify_authority_manifest_integrity(manifest, source=str(manifest_path))

    if manifest.get("authority_status") != AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED:
        raise StableRecordAuthorityError(
            f"{manifest_path} declares authority_status={manifest.get('authority_status')!r}; "
            f"this loader only accepts {AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED!r}"
        )

    declared_name = manifest.get("registry_filename")
    if (
        not isinstance(declared_name, str)
        or not declared_name
        or Path(declared_name).name != declared_name
    ):
        raise StableRecordAuthorityError(
            f"{manifest_path} declares registry_filename={declared_name!r}; an authority file must "
            "be named by a plain filename inside the authority directory"
        )

    registry_path = output_dir / declared_name
    if not registry_path.is_file():
        raise StableRecordAuthorityError(f"authority file {registry_path} is missing")
    actual = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    declared_digest = manifest.get("registry_sha256")
    if (
        not isinstance(declared_digest, str)
        or not _SHA256_HEX_RE.match(declared_digest)
        or not hmac.compare_digest(actual, declared_digest)
    ):
        raise StableRecordAuthorityError(
            f"authority file {registry_path} sha256 {actual} does not match the manifest "
            f"({declared_digest})"
        )

    with open(registry_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(AUTHORITY_COLUMNS):
            raise StableRecordAuthorityError(
                f"authority file {registry_path} columns {reader.fieldnames} do not match "
                f"{list(AUTHORITY_COLUMNS)}"
            )
        rows = [dict(row) for row in reader]

    return manifest, rows


# --- end-to-end materialization ----------------------------------------------------------------------


def materialize_stable_record_authority(
    proposal_dir: Path,
    decisions_path: Path,
    evidence_pins: AuthorityEvidencePins,
    output_dir: Optional[Path] = None,
    created_at: Optional[str] = None,
    *,
    verify_companions: bool = True,
    authorize_production_destination: bool = False,
) -> Tuple[StableRecordAuthority, Optional[Dict[str, object]]]:
    """Load both evidence artifacts, build the authority, and optionally publish it.

    With ``output_dir=None`` this is a complete dry run: everything is loaded, pinned, reconciled,
    and validated, and nothing is written. That is the mode a reviewer uses to confirm what *would*
    be published without authorizing it.
    """
    proposal = load_proposal_evidence(Path(proposal_dir), evidence_pins)
    decisions_path = Path(decisions_path)
    decisions = load_decision_artifact(decisions_path, evidence_pins)
    if verify_companions:
        verify_companion_artifacts(decisions_path.parent, evidence_pins)

    authority = build_stable_record_authority(proposal, decisions, evidence_pins)

    if output_dir is None:
        return authority, None

    manifest = write_authority_package(
        authority,
        Path(output_dir),
        created_at=created_at,
        authorize_production_destination=authorize_production_destination,
    )
    return authority, manifest
