from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from .governance import metadata_allows_written_external_use
from .models import GeneratedAnswer, SearchFilters
from .pipeline import ask_index
from .query_planning import FIELD_REGISTRY
from .query_gating import RESTRICTED_QUERY_REFUSAL
from .slack_output_preview_reports import write_slack_output_preview_reports
from .slack_presentation import escape_mrkdwn_url, url_is_mrkdwn_safe
from .structured_results import ASSET_FIELDS


SAMPLE_QUERIES = (
    "三風製麵",
    "提供我三風製麵的內容",
    "dachun",
    "居家生活",
    "居家生活 影片",
    "2025 年採訪的品牌",
    "dachun Podcast",
    "數位轉型",
    "莉朵花藝",
    "已上線的影片",
    "2025 居家生活 Podcast",
)
VARIANTS = ("concise", "standard", "detailed")
ALLOWED_OVERLAY_FIELDS = {"asset_url", "canonical_url"}
MAX_DISPLAY_ENTITIES = 5
MAX_DISPLAY_ASSETS = 10
MAX_TITLE_CHARS = 160
# The tracked manifest is the only authority for "approved" asset URL artifacts. A row that says
# "approve" is never sufficient on its own: authority comes from the tracked manifest identity, the
# cryptographic binding of each artifact, the recorded provenance of the human-reviewed sources the
# bundle was derived from, and the runtime governance still enforced on every asset.
#
# The bundle is runtime package data, not a test fixture: it ships inside the installed package, so
# a wheel/non-editable deployment carries the same authority a source checkout does.
#
# It is a deliberately sanitized projection, never a copy of the human-review source. The reviewed
# reports stay local and ignored; only the minimum needed to attach an approved URL to one asset is
# packaged. tools/build_approved_asset_url_authority.py derives it.
AUTHORITY_PACKAGE_RELATIVE_DIR = "authority/approved_asset_urls"
AUTHORITY_MANIFEST_FILENAME = "manifest.json"
APPROVED_ASSET_URL_VALUES = "approved_urls.csv"
# The packaged authority carries approved URLs only. A blocked-asset inventory is deliberately NOT
# shipped: it is redundant for URL enrichment (an approved URL and a blocked asset are disjoint by
# construction, enforced at build time) and its identities would be the only payload those rows
# carry, which makes them a pure disclosure of which assets are restricted. Blocked assets stay
# excluded at build time and remain governed at runtime by the external-use contract below.
APPROVED_ASSET_URL_INPUTS = (APPROVED_ASSET_URL_VALUES,)
# Strict positive allowlist. Runtime rejects any artifact whose header is not exactly this, so a
# new authority column cannot reach a deployment without being reviewed first.
APPROVED_ASSET_URL_VALUE_COLUMNS = ("asset_identity", "field", "url")
# Domain-separated join identity, derived ONLY from fields that are already published to the same
# external audience as the URL itself: the entity's public name, the asset's public title and the
# asset type. All three are rendered next to the link in the Slack reply for exactly the assets that
# receive one, so the identity discloses nothing the approved link does not already disclose.
#
# This is PSEUDONYMOUS_PUBLIC_DERIVED, not anonymous. It is enumerable by anyone who already holds
# the same public brand/title/type triple -- and that is the point: recovering a preimage teaches
# the holder nothing they did not have to know in order to attempt it. It deliberately excludes the
# source workbook sheet, the source row, record_id and source_location, whose tiny, highly
# structured domain made the previous derivation recoverable by brute force.
APPROVED_ASSET_IDENTITY_NAMESPACE = "mka:approved-asset-url:v2:public"
ASSET_IDENTITY_LENGTH = 32

# --- index binding -------------------------------------------------------------------------
#
# The identity above is a pure function of mutable index content, so a static authority read
# against a drifted index can attach the WRONG asset's URL: if one asset's indexed title becomes
# another already-approved asset's title, it computes that asset's identity and inherits its link.
# Omitting a link when the authority is stale is acceptable; attaching someone else's is not.
#
# The authority is therefore valid only against the exact content-identity surface it was built
# against. The build records one aggregate digest over that surface; the runtime recomputes it from
# the live index and refuses all enrichment on mismatch.
#
# Surface scope: every asset in the index whose public identity appears in the approved authority
# (option B). Restricting it to authority-relevant assets keeps unrelated index churn from
# switching links off, and costs nothing in safety -- an asset whose identity is not in the
# authority cannot receive a URL, and the moment it drifts into an approved identity it joins the
# surface and changes the digest. An approved asset absent from the index (r30) is simply never in
# the surface, at build or at runtime, so it stays deterministic and harmless.
INDEX_BINDING_NAMESPACE = "mka:approved-asset-url-index-binding:v1"
INDEX_BINDING_SURFACE = "indexed_approved_assets"
# Each input earns its place by a failure it alone detects:
#   source_record_id  two assets swapping titles leaves the multiset of public triples unchanged;
#                     only the coordinate->identity mapping reveals the reassignment.
#   entity_name       part of the join identity; a rename must invalidate.
#   title             part of the join identity; the mutable field that caused this regression.
#   asset_type        part of the join identity; a type change must not resolve cross-type.
INDEX_BINDING_INPUTS = ("source_record_id", "entity_name", "title", "asset_type")


def approved_asset_identity(entity_name: object, title: object, asset_type: object) -> str:
    """Derive the runtime join identity from the asset's externally published fields.

    Returns "" when any component is missing, so an incomplete asset fails closed into "no URL"
    instead of colliding with the identity of a differently-shaped one.
    """
    components = [
        _identity_component(entity_name),
        _identity_component(title),
        _identity_component(asset_type),
    ]
    if not all(components):
        return ""
    payload = ":".join([APPROVED_ASSET_IDENTITY_NAMESPACE, *components]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:ASSET_IDENTITY_LENGTH]


def _identity_component(value: object) -> str:
    """Normalise one identity component and make the separator unambiguous.

    NFC only: two spellings of the same string must join, but case and width differences must not
    be folded together, because a false join attaches one asset's approved URL to another asset.
    Escaping ":" and "\\" keeps ("a:b", "c") distinct from ("a", "b:c").
    """
    text = unicodedata.normalize("NFC", _text(value))
    return text.replace("\\", "\\\\").replace(":", "\\:")


class SlackOutputPreviewError(ValueError):
    """Raised when a Slack output preview cannot be built without unsafe assumptions."""


class ApprovedAssetUrlAuthorityError(SlackOutputPreviewError):
    """Raised when approved asset URL artifacts are not bound to the tracked authority manifest."""


class ApprovedAssetUrlIndexBindingError(ApprovedAssetUrlAuthorityError):
    """Raised when the packaged authority is not bound to the live content index."""


def index_asset_surface(db_path: Path) -> List[Tuple[str, str, str, str]]:
    """Read the canonical (record, entity, title, type) surface straight from the content index.

    Deliberately re-derived from the stored document metadata rather than from a retrieval result:
    the binding must cover the whole index, not whatever one query happened to match, and it must
    be reproducible identically by the build tool and by the runtime.
    """
    path = Path(db_path)
    if path.is_symlink() or not path.is_file():
        raise ApprovedAssetUrlIndexBindingError("content index is not available for binding")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ApprovedAssetUrlIndexBindingError("content index cannot be opened") from exc
    try:
        rows = connection.execute("SELECT id, title, metadata_json FROM documents").fetchall()
    except sqlite3.Error as exc:
        raise ApprovedAssetUrlIndexBindingError("content index cannot be read") from exc
    finally:
        connection.close()

    surface: List[Tuple[str, str, str, str]] = []
    for document_id, document_title, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError as exc:
            raise ApprovedAssetUrlIndexBindingError("content index metadata is unreadable") from exc
        if not isinstance(metadata, Mapping) or metadata.get("record_type") != "merchant_case":
            continue
        source_sheet = metadata.get("source_sheet")
        source_row = metadata.get("source_row")
        record_id = (
            f"{source_sheet}:r{source_row}"
            if source_sheet and source_row is not None
            else _text(document_id)
        )
        entity_name = _text(
            metadata.get("brand_name")
            or metadata.get("metric_name")
            or metadata.get("title")
            or document_title
        )
        for asset_type, field_name, _label in ASSET_FIELDS:
            title = _text(metadata.get(field_name))
            if title:
                surface.append((record_id, entity_name, title, asset_type))
    return surface


def compute_index_binding_digest(
    db_path: Path, approved_identities: Iterable[str]
) -> Tuple[str, int]:
    """Return the aggregate binding digest and the number of bound assets.

    One digest over the whole sorted surface, never a per-record hash: recovering any single
    source coordinate would require guessing every other record's coordinate and free-text title
    simultaneously, so this cannot be dictionary-attacked the way per-row hashes could.
    """
    approved = set(approved_identities)
    bound = sorted(
        row
        for row in index_asset_surface(db_path)
        if approved_asset_identity(row[1], row[2], row[3]) in approved
    )
    digest = hashlib.sha256()
    digest.update(f"{INDEX_BINDING_NAMESPACE}:{INDEX_BINDING_SURFACE}:{len(bound)}\n".encode("utf-8"))
    for record_id, entity_name, title, asset_type in bound:
        line = ":".join(
            _identity_component(part) for part in (record_id, entity_name, title, asset_type)
        )
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest(), len(bound)


@dataclass(frozen=True)
class AssetUrlRecord:
    record_id: str
    asset_id: str
    field: str
    proposed_value: str
    # Review attestation exists only on the local governance path. The packaged authority proves
    # human review through manifest provenance instead of carrying reviewer identity.
    reviewer: str = ""
    reviewed_at: str = ""


@dataclass(frozen=True)
class AssetLookup:
    """Everything a URL lookup may use, split by whether it is safe to publish.

    ``record_id``/``asset_id`` are internal source coordinates: the local governance overlay joins
    on them, and the packaged authority must never see them. ``entity_name``/``title``/``asset_type``
    are published alongside the link itself, and are what the packaged authority joins on.
    """

    record_id: str
    asset_id: str
    entity_name: str
    title: str
    asset_type: str


@dataclass
class AssetUrlOverlay:
    values: Dict[Tuple[str, str, str], AssetUrlRecord] = field(default_factory=dict)
    blocked_asset_ids: Set[str] = field(default_factory=set)
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    # True when this overlay came from the packaged authority, which is keyed on the public-derived
    # identity. The local governance overlay keeps joining on its own source coordinates.
    identity_hashed: bool = False

    def url(self, lookup: "AssetLookup", field_name: str) -> Optional[str]:
        """Resolve one approved URL for one asset, under whichever identity this overlay uses."""
        if self.identity_hashed:
            identity = approved_asset_identity(
                lookup.entity_name, lookup.title, lookup.asset_type
            )
            if not identity:
                return None
            key = (identity, identity, field_name)
        else:
            key = (lookup.record_id, lookup.asset_id, field_name)
        record = self.values.get(key)
        return record.proposed_value if record else None

    def value(self, record_id: str, asset_id: str, field_name: str) -> Optional[str]:
        """Source-coordinate lookup, for the local governance overlay only."""
        if self.identity_hashed:
            raise SlackOutputPreviewError(
                "the packaged authority has no source-coordinate identity; use url(AssetLookup, ...)"
            )
        record = self.values.get((record_id, asset_id, field_name))
        return record.proposed_value if record else None

    def is_blocked(self, asset_id: str) -> bool:
        """Local governance blocking. The packaged authority ships no blocked inventory.

        It does not need one: a blocked asset never carries an approved URL (enforced when the
        bundle is built), so it simply has nothing to resolve, and every asset still passes the
        external-use governance contract before any URL is attached.
        """
        return asset_id in self.blocked_asset_ids


@dataclass
class SlackPreviewBundle:
    payload: dict
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    result_identity: Tuple[Tuple[str, str, str], ...] = ()
    backend_citations: Tuple[dict, ...] = ()


def load_pinned_approved_asset_url_overlay() -> AssetUrlOverlay:
    """Load approved asset URLs from repository-pinned, hash-verified artifacts.

    It deliberately takes no arguments: the packaged authority directory, the manifest, the artifact
    names and the expected hashes are all fixed, so a caller cannot substitute writable CSV files
    that self-attest as approved. Any failure raises ApprovedAssetUrlAuthorityError.

    The packaged rows carry no approval columns to trust: a row is present only because the build
    tool proved it satisfied the whole eligibility contract against hash-verified reviewed sources,
    and the manifest records those source hashes. Adding a row means breaking an artifact hash,
    which means editing the tracked manifest, which changes its bytes and self-integrity hash.

    This checks the authority artifacts only. It does NOT check that the authority still matches the
    content index, so the Slack runtime must not call it directly -- use
    load_index_bound_approved_asset_url_overlay, which adds that check.
    """
    (approved_bytes,) = _authorized_asset_url_inputs()
    # Parsing the verified bytes, rather than re-reading the paths, keeps the artifact that was
    # hashed and the artifact that is parsed identical even under a concurrent rewrite.
    return _build_packaged_asset_url_overlay(
        _parse_pinned_csv(approved_bytes, APPROVED_ASSET_URL_VALUE_COLUMNS),
    )


def load_index_bound_approved_asset_url_overlay(db_path: Path) -> AssetUrlOverlay:
    """Load the packaged authority and refuse it unless it still binds to this content index.

    This is the only entry point the Slack runtime may use. The authority itself is still not
    caller-supplied; the single argument is the content index the answer was produced from, which
    the caller already holds. A substituted index cannot forge approvals -- it can only fail the
    binding, which fails enrichment closed.
    """
    overlay = load_pinned_approved_asset_url_overlay()
    _verify_index_binding(Path(db_path), overlay)
    return overlay


def _verify_index_binding(db_path: Path, overlay: AssetUrlOverlay) -> None:
    """Recompute the binding digest for this index and compare it with the pinned one.

    WHEN_BINDING_IS_VERIFIED   on every feature-ON enrichment attempt, with no exceptions. There is
                               deliberately no memo: a previously successful verification never
                               lets a later request skip this check.
    WHY_NO_CACHE               any cache must be keyed on something cheaper than the content it
                               stands for, and no such key is sound here. A stat fingerprint of the
                               main database file is stable across a WAL commit -- the writer
                               appends to "-wal" and the main file's size and mtime do not move --
                               so a memo keyed on it serves a stale "verified" answer while
                               retrieval already sees the new content, which is exactly how one
                               asset ends up wearing another asset's approved URL. Recomputing
                               costs a few milliseconds; getting this wrong costs a wrong link.
    HOW_INDEX_CHANGE_IS_SEEN   the digest is recomputed from the effective committed content on
                               each attempt, through a fresh read-only connection that observes
                               WAL-visible commits, so any change is seen the moment it is
                               committed rather than when the file's metadata happens to move.
    FAILURE_BEHAVIOR           ApprovedAssetUrlIndexBindingError, which the Slack path already
                               treats as "no URL enrichment, normal answer, payload-free audit".
    """
    expected = _pinned_index_binding_digest()
    actual, _bound = compute_index_binding_digest(db_path, {key[0] for key in overlay.values})
    if actual != expected:
        # Deliberately carries no digest, path or index detail: callers audit a stable code only.
        raise ApprovedAssetUrlIndexBindingError(
            "packaged authority is not bound to the current content index"
        )


def _pinned_index_binding_digest() -> str:
    """Read the binding digest out of the self-integrity-checked manifest."""
    manifest = _verified_authority_manifest_payload(_authority_root())
    binding = manifest.get("index_binding")
    if not isinstance(binding, Mapping):
        raise ApprovedAssetUrlIndexBindingError("tracked authority manifest has no index binding")
    digest = binding.get("digest")
    if (
        binding.get("namespace") != INDEX_BINDING_NAMESPACE
        or binding.get("surface") != INDEX_BINDING_SURFACE
        or list(binding.get("inputs") or ()) != list(INDEX_BINDING_INPUTS)
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ApprovedAssetUrlIndexBindingError("tracked authority index binding is malformed")
    return digest



def _authorized_asset_url_inputs() -> Tuple[bytes, ...]:
    root = _authority_root()
    manifest = _verified_authority_manifest(root)
    payloads = []
    for relative in APPROVED_ASSET_URL_INPUTS:
        entry = manifest.get(relative)
        if entry is None:
            raise ApprovedAssetUrlAuthorityError(
                "approved asset URL input is not covered by the tracked authority manifest"
            )
        payloads.append(_verified_pinned_bytes(root / relative, entry))
    return tuple(payloads)


def _build_packaged_asset_url_overlay(
    approved_rows: Sequence[Mapping[str, object]],
) -> AssetUrlOverlay:
    """Project the sanitized packaged rows onto the overlay the Slack runtime already consumes."""
    values: Dict[Tuple[str, str, str], AssetUrlRecord] = {}
    for row in approved_rows:
        identity = _text(row.get("asset_identity"))
        field_name = _text(row.get("field"))
        url = _text(row.get("url"))
        if not _valid_identity(identity):
            raise ApprovedAssetUrlAuthorityError("approved asset identity is malformed")
        if field_name not in ALLOWED_OVERLAY_FIELDS:
            raise ApprovedAssetUrlAuthorityError("approved URL row is outside the URL contract")
        if not _safe_http_url(url):
            raise ApprovedAssetUrlAuthorityError("approved URL is not safe for Slack")
        key = (identity, identity, field_name)
        if key in values:
            raise ApprovedAssetUrlAuthorityError("approved URL rows contain a duplicate identity")
        values[key] = AssetUrlRecord(
            record_id=identity,
            asset_id=identity,
            field=field_name,
            proposed_value=url,
        )
    return AssetUrlOverlay(values=values, identity_hashed=True)


def _valid_identity(value: str) -> bool:
    return len(value) == ASSET_IDENTITY_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _asset_lookup(entity, asset) -> AssetLookup:
    """Collect both identity shapes for one asset, so either overlay can resolve it."""
    record_id = _text(getattr(asset, "source_record_id", ""))
    return AssetLookup(
        record_id=record_id,
        asset_id=f"{record_id}:{_text(getattr(asset, 'asset_type', ''))}",
        entity_name=_text(getattr(entity, "entity_name", "")),
        title=_text(getattr(asset, "title", "")),
        asset_type=_text(getattr(asset, "asset_type", "")),
    )


def _authority_root() -> Path:
    """Resolve the packaged authority directory from the module location, never from the CWD.

    The bundle sits inside the installed package, so this resolves identically for a source
    checkout, an editable install and an unpacked wheel. A deployment that shipped without the
    package data fails closed here rather than falling back to any writable location.
    """
    root = Path(__file__).resolve().parent / AUTHORITY_PACKAGE_RELATIVE_DIR
    manifest = root / AUTHORITY_MANIFEST_FILENAME
    if manifest.is_symlink() or not manifest.is_file():
        raise ApprovedAssetUrlAuthorityError(
            "packaged authority manifest is not installed with the module"
        )
    return root


def _verified_authority_manifest_payload(root: Path) -> Dict[str, object]:
    """Read the tracked manifest and enforce its self-integrity hash before anything reads it."""
    try:
        payload = json.loads((root / AUTHORITY_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovedAssetUrlAuthorityError("tracked authority manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
    stored_hash = payload.get("manifest_hash")
    body = {key: value for key, value in payload.items() if key != "manifest_hash"}
    if not isinstance(stored_hash, str) or stored_hash != _hash_json(body):
        raise ApprovedAssetUrlAuthorityError(
            "tracked authority manifest failed its self-integrity contract"
        )
    return payload


def _verified_authority_manifest(root: Path) -> Dict[str, Mapping[str, object]]:
    """Parse the tracked manifest using its existing schema and enforce its self-integrity hash."""
    payload = _verified_authority_manifest_payload(root)
    inputs = payload.get("inputs")
    if not isinstance(inputs, list):
        raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
    entries: Dict[str, Mapping[str, object]] = {}
    for entry in inputs:
        if not isinstance(entry, Mapping):
            raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
        relative = entry.get("relative_path")
        if not isinstance(relative, str) or not relative:
            raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
        if relative in entries:
            raise ApprovedAssetUrlAuthorityError(
                "tracked authority manifest contains a duplicate input"
            )
        entries[relative] = entry
    return entries


def _verified_pinned_bytes(path: Path, entry: Mapping[str, object]) -> bytes:
    expected_sha256 = entry.get("expected_sha256")
    expected_size = entry.get("expected_size")
    if (
        entry.get("input_type") != "file"
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
    ):
        raise ApprovedAssetUrlAuthorityError(
            "approved asset URL input has no usable pinned file hash"
        )
    if path.is_symlink() or not path.is_file():
        raise ApprovedAssetUrlAuthorityError("approved asset URL input is missing")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ApprovedAssetUrlAuthorityError("approved asset URL input is unreadable") from exc
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ApprovedAssetUrlAuthorityError(
            "approved asset URL input does not match its pinned hash"
        )
    return payload


def _parse_pinned_csv(payload: bytes, expected_columns: Sequence[str]) -> List[dict]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApprovedAssetUrlAuthorityError("approved asset URL input is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise ApprovedAssetUrlAuthorityError("approved asset URL input has no CSV header")
    # Strict allowlist: an unreviewed extra column must fail closed, not ride along unnoticed.
    if tuple(reader.fieldnames) != tuple(expected_columns):
        raise ApprovedAssetUrlAuthorityError(
            "approved asset URL input header is not the reviewed authority schema"
        )
    return list(reader)


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_asset_url_overlay(
    apply_preview_path: Path,
    blocked_preview_path: Path,
    decisions_path: Path,
) -> AssetUrlOverlay:
    return _build_asset_url_overlay(
        _read_csv(Path(apply_preview_path)),
        _read_csv(Path(blocked_preview_path)),
        _read_csv(Path(decisions_path)),
    )


def _build_asset_url_overlay(
    apply_rows: Sequence[Mapping[str, object]],
    blocked_rows: Sequence[Mapping[str, object]],
    decision_rows: Sequence[Mapping[str, object]],
) -> AssetUrlOverlay:
    errors: List[dict] = []
    warnings: List[dict] = []
    decisions: Dict[Tuple[str, str, str], dict] = {}

    for row in decision_rows:
        field_name = _text(row.get("field"))
        if field_name not in ALLOWED_OVERLAY_FIELDS:
            continue
        key = _row_key(row)
        if not all(key):
            _issue(errors, "invalid_decision_join_key", "Decision row lacks record/asset/field identity.")
        elif key in decisions:
            _issue(errors, "duplicate_decision_join_key", "Decision rows contain a duplicate identity.")
        else:
            decisions[key] = row

    blocked_asset_ids = {
        _text(row.get("asset_id"))
        for row in blocked_rows
        if _text(row.get("action")) == "blocked" and _text(row.get("asset_id"))
    }
    values: Dict[Tuple[str, str, str], AssetUrlRecord] = {}
    for row in apply_rows:
        key = _row_key(row)
        record_id, asset_id, field_name = key
        if field_name not in ALLOWED_OVERLAY_FIELDS:
            _issue(errors, "unsupported_overlay_field", "Apply Preview contains a field outside the URL contract.")
            continue
        if not all(key):
            _issue(errors, "invalid_apply_join_key", "Apply Preview row lacks record/asset/field identity.")
            continue
        if key in values:
            _issue(errors, "duplicate_apply_join_key", "Apply Preview contains a duplicate identity.")
            continue
        if asset_id in blocked_asset_ids:
            _issue(errors, "blocked_asset_in_apply_preview", "A governance-blocked asset entered proposed Apply.")
            continue
        if (
            _text(row.get("eligibility")) != "ready_for_apply_preview"
            or _text(row.get("review_decision")) != "approve"
            or _text(row.get("governance_status")) != "eligible"
            or _text(row.get("action")) not in {"add", "update", "no_change"}
        ):
            _issue(errors, "ineligible_apply_row", "Apply Preview row does not satisfy the approved eligibility contract.")
            continue
        decision = decisions.get(key)
        if decision is None:
            _issue(errors, "missing_human_decision", "Apply Preview row has no matching human decision.")
            continue
        proposed_value = _text(row.get("proposed_value"))
        if not _decision_matches(row, decision):
            _issue(errors, "decision_apply_mismatch", "Apply Preview differs from the approved human decision.")
            continue
        if not _text(row.get("reviewer")) or not _valid_iso_date(_text(row.get("reviewed_at"))):
            _issue(errors, "invalid_review_attestation", "Approved URL lacks a valid reviewer or reviewed_at date.")
            continue
        if not _safe_http_url(proposed_value):
            _issue(errors, "invalid_overlay_url", "Approved URL is not safe for an offline Slack preview.")
            continue
        values[key] = AssetUrlRecord(
            record_id=record_id,
            asset_id=asset_id,
            field=field_name,
            proposed_value=proposed_value,
            reviewer=_text(row.get("reviewer")),
            reviewed_at=_text(row.get("reviewed_at")),
        )

    return AssetUrlOverlay(
        values=values,
        blocked_asset_ids=blocked_asset_ids,
        errors=errors,
        warnings=warnings,
    )


def apply_approved_asset_url_overlay(answer, overlay: AssetUrlOverlay) -> int:
    """Attach approved asset-level URLs to an externally safe structured answer."""
    generated = getattr(answer, "generated", answer)
    structured = getattr(generated, "structured_result", None)
    if structured is None or overlay.errors:
        return 0

    citations = {
        citation.label: citation
        for citation in getattr(generated, "citations", [])
        if citation.can_quote_externally
        and citation.data_classification == "public"
        and metadata_allows_written_external_use(citation)
    }
    applied = 0
    for entity in structured.matched_entities:
        for asset in entity.assets:
            if overlay.is_blocked(f"{asset.source_record_id}:{asset.asset_type}"):
                continue
            citation = citations.get(asset.citation_label)
            if (
                citation is None
                or citation.title != asset.title
                or citation.source_sheet != asset.source_sheet
                or citation.source_row != asset.source_row
                or citation.chunk_id.rsplit(":", 1)[-1] != asset.asset_type
            ):
                continue
            # Only now, once this asset's citation has passed the full external-use contract, may
            # its published fields be used to derive the packaged authority's join identity.
            lookup = _asset_lookup(entity, asset)
            value = (
                overlay.url(lookup, "canonical_url")
                or overlay.url(lookup, "asset_url")
            )
            if not value or not _safe_http_url(value):
                continue
            asset.url = value
            citation.canonical_url = value
            applied += 1
    return applied


def build_slack_preview_payload(
    query: str,
    answer: GeneratedAnswer,
    overlay: AssetUrlOverlay,
) -> SlackPreviewBundle:
    errors = list(overlay.errors)
    warnings = list(overlay.warnings)
    structured = answer.structured_result
    if structured is None:
        if answer.answer == RESTRICTED_QUERY_REFUSAL:
            return SlackPreviewBundle(
                payload=_empty_payload("[restricted query]", "restricted_refusal"),
                warnings=list(overlay.warnings),
            )
        _issue(errors, "missing_structured_result", "Preview requires StructuredRetrievalResult.")
        return SlackPreviewBundle(
            payload=_empty_payload(query, "missing_structured_result"),
            errors=errors,
            warnings=warnings,
        )

    citation_by_label = {
        citation.label: citation
        for citation in answer.citations
        if citation.can_quote_externally and citation.data_classification == "public"
    }
    entities = []
    citations = []
    backend_citations = []
    result_identity: List[Tuple[str, str, str]] = []
    for entity in structured.matched_entities:
        assets = []
        for asset in entity.assets:
            record_id = asset.source_record_id
            asset_id = f"{record_id}:{asset.asset_type}"
            identity = (record_id, asset_id, asset.asset_type)
            if overlay.is_blocked(asset_id):
                _issue(
                    warnings,
                    "governance_blocked_asset",
                    "A governance-blocked asset was omitted from the preview.",
                    query=query,
                )
                continue
            citation = citation_by_label.get(asset.citation_label)
            if citation is None:
                _issue(
                    warnings,
                    "citation_not_external",
                    "An asset without an external-safe citation was omitted.",
                    query=query,
                )
                continue
            if (
                citation.title != asset.title
                or citation.source_sheet != asset.source_sheet
                or citation.source_row != asset.source_row
            ):
                _issue(
                    errors,
                    "citation_asset_mismatch",
                    "Citation does not map one-to-one to the structured asset.",
                    query=query,
                )
                continue
            lookup = _asset_lookup(entity, asset)
            display_url = overlay.url(lookup, "asset_url")
            canonical_url = overlay.url(lookup, "canonical_url")
            if display_url is None:
                _issue(
                    warnings,
                    "url_overlay_missing",
                    "Asset identity has no approved display URL; the title remains visible without a link.",
                    query=query,
                    field="asset_url",
                )
            elif not _safe_slack_url(display_url):
                _issue(
                    warnings,
                    "url_not_slack_safe",
                    "Approved URL cannot be embedded safely and was omitted from display.",
                    query=query,
                    field="asset_url",
                )
                display_url = None
            source = _safe_source(asset.source_sheet, asset.source_row)
            asset_payload = {
                "asset_type": asset.asset_type,
                "asset_type_label": _asset_label(asset.asset_type),
                "title": asset.title,
                "url": display_url,
                "external_usage": asset.external_usage_status,
                "source": source,
                "citation_label": asset.citation_label,
            }
            assets.append(asset_payload)
            citations.append(
                {
                    "label": citation.label,
                    "title": citation.title,
                    "source": source,
                    "can_quote_externally": True,
                }
            )
            backend_citations.append(
                {
                    "label": citation.label,
                    "record_id": record_id,
                    "asset_id": asset_id,
                    "canonical_url": canonical_url,
                }
            )
            result_identity.append(identity)
            if canonical_url is None:
                _issue(
                    warnings,
                    "canonical_overlay_missing",
                    "Asset identity has no approved canonical URL for backend citation metadata.",
                    query=query,
                    field="canonical_url",
                )
        if assets:
            entities.append(
                {
                    "entity_type": entity.entity_type,
                    "entity_name": entity.entity_name,
                    "merchant_handle": entity.merchant_handle,
                    "sales_category_lv1": entity.sales_category_lv1,
                    "sales_category_lv2": entity.sales_category_lv2,
                    "interview_year": entity.interview_year,
                    "assets": assets,
                }
            )

    constraints = _safe_constraints(structured.query_plan)
    total_assets = sum(len(entity["assets"]) for entity in entities)
    abstain_kind = _abstain_kind(structured, total_assets)
    payload = {
        "query": query,
        "query_type": _query_type(structured.query_plan),
        "resolved_handle": _resolved_handle(structured.query_plan),
        "applied_constraints": constraints,
        "operator": structured.query_plan.get("operator") or "AND",
        "entities": entities,
        "total_entities": len(entities),
        "total_assets": total_assets,
        "citations": _dedupe_dicts(citations, "label"),
        "abstained": bool(abstain_kind),
        "abstain_kind": abstain_kind,
        "unsupported_labels": _constraint_labels(structured.unsupported_constraints),
    }
    return SlackPreviewBundle(
        payload=payload,
        errors=errors,
        warnings=warnings,
        result_identity=tuple(result_identity),
        backend_citations=tuple(backend_citations),
    )


def render_slack_preview(payload: Mapping[str, object], variant: str) -> str:
    if variant not in VARIANTS:
        raise SlackOutputPreviewError(f"unsupported preview variant: {variant}")
    if payload.get("abstained"):
        return _render_abstention(payload)

    entities, displayed_assets = _display_subset(payload.get("entities") or [])
    lines = [_result_header(payload, len(entities), displayed_assets)]
    condition_lines = _render_conditions(payload.get("applied_constraints") or [])
    if condition_lines:
        lines.extend(["", *condition_lines])
    if variant == "concise":
        lines.extend(_render_concise(entities))
    elif variant == "standard":
        lines.extend(_render_standard(entities))
    else:
        lines.extend(_render_detailed(entities))
    lines.extend(_limit_notice(payload, entities, displayed_assets))
    return "\n".join(line for line in lines if line is not None).strip()


def preview_slack_query(
    query: str,
    variant: str,
    db_path: Path,
    apply_preview_path: Path,
    blocked_preview_path: Path,
    decisions_path: Path,
    restricted_customers_path: Path,
    *,
    ask_fn: Callable = ask_index,
) -> str:
    overlay = load_asset_url_overlay(
        Path(apply_preview_path),
        Path(blocked_preview_path),
        Path(decisions_path),
    )
    if overlay.errors:
        raise SlackOutputPreviewError("asset URL overlay did not pass the preview contract")
    with TemporaryDirectory(prefix="mka-slack-preview-audit-") as temp_dir:
        answer = ask_fn(
            query,
            Path(db_path),
            filters=SearchFilters(intent="external"),
            limit=250,
            restricted_customers_path=Path(restricted_customers_path),
            provider_name="mock",
            audit_log_path=Path(temp_dir) / "query_audit.csv",
        )
    bundle = build_slack_preview_payload(query, answer, overlay)
    if bundle.errors:
        raise SlackOutputPreviewError("structured result did not pass the preview contract")
    return render_slack_preview(bundle.payload, variant)


def generate_slack_output_preview(
    queries: Sequence[str],
    db_path: Path,
    apply_preview_path: Path,
    blocked_preview_path: Path,
    decisions_path: Path,
    restricted_customers_path: Path,
    output_dir: Path,
    *,
    vault_path: Optional[Path] = None,
    workbook_path: Optional[Path] = None,
    ask_fn: Callable = ask_index,
) -> dict:
    queries = [str(query).strip() for query in queries if str(query).strip()]
    if not queries:
        raise SlackOutputPreviewError("at least one preview query is required")
    paths = [
        Path(db_path),
        Path(apply_preview_path),
        Path(blocked_preview_path),
        Path(decisions_path),
        Path(restricted_customers_path),
    ]
    if vault_path is not None:
        paths.append(Path(vault_path))
    if workbook_path is not None:
        paths.append(Path(workbook_path))
    _assert_safe_output(Path(output_dir), paths)
    before_hashes = {str(path): _hash_path(path) for path in paths}
    overlay = load_asset_url_overlay(
        Path(apply_preview_path),
        Path(blocked_preview_path),
        Path(decisions_path),
    )
    bundles = []
    errors = list(overlay.errors)
    warnings = list(overlay.warnings)
    if not errors:
        with TemporaryDirectory(prefix="mka-slack-preview-audit-") as temp_dir:
            audit_path = Path(temp_dir) / "query_audit.csv"
            for query in queries:
                answer = ask_fn(
                    query,
                    Path(db_path),
                    filters=SearchFilters(intent="external"),
                    limit=250,
                    restricted_customers_path=Path(restricted_customers_path),
                    provider_name="mock",
                    audit_log_path=audit_path,
                )
                bundle = build_slack_preview_payload(query, answer, overlay)
                bundles.append(bundle)
                errors.extend(bundle.errors)
                warnings.extend(bundle.warnings)

    after_hashes = {str(path): _hash_path(path) for path in paths}
    source_files_modified = before_hashes != after_hashes
    if source_files_modified:
        _issue(errors, "protected_source_modified", "A protected source changed during preview generation.")

    payloads = [bundle.payload for bundle in bundles]
    summary = {
        "conclusion": (
            "C. Requires fixes before format decision"
            if errors
            else "A. Ready for output format decision"
        ),
        "preview_only": True,
        "query_count": len(queries),
        "payload_count": len(payloads),
        "approved_overlay_field_count": len(overlay.values),
        "approved_overlay_asset_count": len({key[1] for key in overlay.values}),
        "governance_blocked_asset_count": len(overlay.blocked_asset_ids),
        "result_asset_count": sum(payload["total_assets"] for payload in payloads),
        "backend_citation_count": sum(len(bundle.backend_citations) for bundle in bundles),
        "abstained_query_count": sum(bool(payload["abstained"]) for payload in payloads),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "source_files_modified": source_files_modified,
        "slack_api_called": False,
        "slack_token_read": False,
        "asset_urls_applied": False,
        "query_constraints_enabled": [],
        "variants": list(VARIANTS),
        "variant_result_sets_identical": True,
    }
    write_slack_output_preview_reports(
        Path(output_dir),
        summary,
        payloads,
        errors,
        warnings,
    )
    return summary


def _render_abstention(payload: Mapping[str, object]) -> str:
    kind = payload.get("abstain_kind")
    query = _slack_text(payload.get("query"))
    if kind == "unsupported_constraint":
        labels = "、".join(_slack_text(value) for value in payload.get("unsupported_labels") or [])
        target = f"「{labels}」" if labels else "其中一個條件"
        return (
            f"目前資料尚不支援以{target}進行可靠篩選，因此未執行此次搜尋。\n\n"
            "目前可使用品牌名稱、Handle、Sales Category、採訪年份、內容標籤及內容類型進行搜尋。"
        )
    if kind == "restricted_refusal":
        return RESTRICTED_QUERY_REFUSAL
    if kind == "zero_intersection":
        conditions = "、".join(
            f"{_slack_text(item.get('label'))}：{_slack_text(item.get('value'))}"
            for item in payload.get("applied_constraints") or []
        )
        return f"找不到同時符合所有條件（{conditions}）且可供 Slack 使用的內容。"
    if kind == "governance_filtered":
        return "目前找不到符合條件且可供 Slack 對外使用的內容。"
    return (
        f"目前找不到與「{query}」精確匹配且可供 Slack 使用的內容。\n\n"
        "請確認品牌名稱、Merchant Handle，或該資料是否已進入正式內容索引。"
    )


def _result_header(payload: Mapping[str, object], displayed_entities: int, displayed_assets: int) -> str:
    total_entities = int(payload.get("total_entities") or 0)
    total_assets = int(payload.get("total_assets") or 0)
    entities = payload.get("entities") or []
    resolved_handle = payload.get("resolved_handle")
    if resolved_handle and len(entities) == 1:
        return (
            f"找到 Handle「{_slack_text(resolved_handle)}」對應品牌："
            f"{_slack_text(entities[0].get('entity_name'))}\n共 {total_assets} 筆內容："
        )
    if payload.get("query_type") == "brand" and len(entities) == 1:
        return f"找到「{_slack_text(entities[0].get('entity_name'))}」相關內容，共 {total_assets} 筆："
    if total_entities > displayed_entities or total_assets > displayed_assets:
        return (
            f"共找到 {total_entities} 個品牌、{total_assets} 筆內容；"
            f"目前顯示 {displayed_entities} 個品牌、{displayed_assets} 筆內容。"
        )
    return f"共找到 {total_entities} 個品牌、{total_assets} 筆內容。"


def _render_conditions(constraints: Sequence[Mapping[str, object]]) -> List[str]:
    visible = [item for item in constraints if item.get("field") not in {"entity_name", "merchant_handle"}]
    if not visible:
        return []
    return ["已套用搜尋條件："] + [
        f"- {_slack_text(item.get('label'))}：{_slack_text(item.get('value'))}" for item in visible
    ]


def _render_concise(entities: Sequence[Mapping[str, object]]) -> List[str]:
    lines: List[str] = []
    multiple = len(entities) > 1
    for entity in entities:
        if multiple:
            lines.extend(["", _slack_text(entity.get("entity_name"))])
        for asset in entity.get("assets") or []:
            title = _display_title(asset.get("title"))
            link = _slack_link(asset.get("url"), title)
            lines.extend(
                [
                    "",
                    f"• {_slack_text(asset.get('asset_type_label'))}｜{link}",
                    f"  對外引用：{'可' if asset.get('external_usage') == '可對外引用' else '不可'}",
                ]
            )
    sources = _unique_sources(entities)
    if sources:
        lines.extend(["", "資料來源：" + "、".join(sources)])
    return lines


def _render_standard(entities: Sequence[Mapping[str, object]]) -> List[str]:
    lines: List[str] = []
    number = 1
    multiple = len(entities) > 1
    for entity in entities:
        if multiple:
            lines.extend(["", _slack_text(entity.get("entity_name"))])
        for asset in entity.get("assets") or []:
            lines.extend(
                [
                    "",
                    f"{number}. {_slack_text(asset.get('asset_type_label'))}",
                    f"標題：{_display_title(asset.get('title'))}",
                    f"查看內容：{_slack_link(asset.get('url'), '開啟' + _slack_text(asset.get('asset_type_label')))}",
                    f"對外引用：{_slack_text(asset.get('external_usage'))}",
                ]
            )
            number += 1
    sources = _unique_sources(entities)
    if sources:
        lines.extend(["", "資料來源：" + "、".join(sources)])
    return lines


def _render_detailed(entities: Sequence[Mapping[str, object]]) -> List[str]:
    lines: List[str] = []
    for entity in entities:
        lines.extend(
            [
                "",
                f"品牌：{_slack_text(entity.get('entity_name'))}",
                f"Sales Category LV1：{_slack_text(entity.get('sales_category_lv1')) or '資料未提供'}",
                f"Sales Category LV2：{_slack_text(entity.get('sales_category_lv2')) or '資料未提供'}",
            ]
        )
        if entity.get("merchant_handle"):
            lines.append(f"Handle：{_slack_text(entity.get('merchant_handle'))}")
        for asset in entity.get("assets") or []:
            lines.extend(
                [
                    "",
                    f"• 內容類型：{_slack_text(asset.get('asset_type_label'))}",
                    f"  標題：{_display_title(asset.get('title'))}",
                    f"  查看內容：{_slack_link(asset.get('url'), '開啟' + _slack_text(asset.get('asset_type_label')))}",
                    f"  對外引用：{_slack_text(asset.get('external_usage'))}",
                ]
            )
        sources = _unique_sources([entity])
        if sources:
            lines.append("資料來源：" + "、".join(sources))
    return lines


def _limit_notice(
    payload: Mapping[str, object],
    displayed_entities: Sequence[Mapping[str, object]],
    displayed_assets: int,
) -> List[str]:
    total_entities = int(payload.get("total_entities") or 0)
    total_assets = int(payload.get("total_assets") or 0)
    if total_entities <= len(displayed_entities) and total_assets <= displayed_assets:
        return []
    lines = [""]
    if total_entities > len(displayed_entities):
        lines.append(f"目前只顯示前 {len(displayed_entities)} 個品牌。")
    if total_assets > displayed_assets:
        lines.append(f"目前只顯示前 {displayed_assets} 筆內容。")
    lines.append("請使用品牌名稱或內容類型縮小範圍；完整檢索集合未被改變。")
    return lines


def _display_subset(entities: Sequence[Mapping[str, object]]) -> Tuple[List[dict], int]:
    selected = []
    asset_count = 0
    for entity in entities[:MAX_DISPLAY_ENTITIES]:
        remaining = MAX_DISPLAY_ASSETS - asset_count
        if remaining <= 0:
            break
        assets = list(entity.get("assets") or [])[:remaining]
        if not assets:
            continue
        selected.append({**entity, "assets": assets})
        asset_count += len(assets)
    return selected, asset_count


def _safe_constraints(plan: Mapping[str, object]) -> List[dict]:
    result = []
    for constraint in plan.get("supported_constraints") or plan.get("hard_filters") or []:
        if constraint.get("support_status", "supported") != "supported":
            continue
        field_name = _text(constraint.get("field"))
        definition = FIELD_REGISTRY.get(field_name)
        label = constraint.get("output_label") or (definition.output_label if definition else field_name)
        result.append(
            {
                "field": field_name,
                "label": str(label),
                "value": _constraint_value(field_name, constraint.get("value"), constraint.get("operator")),
            }
        )
    return result


def _constraint_labels(constraints: Iterable[Mapping[str, object]]) -> List[str]:
    labels = []
    for constraint in constraints:
        field_name = _text(constraint.get("field"))
        definition = FIELD_REGISTRY.get(field_name)
        label = constraint.get("output_label") or (definition.output_label if definition else field_name)
        if label and label not in labels:
            labels.append(str(label))
    return labels


def _constraint_value(field_name: str, value: object, operator: object) -> str:
    if operator == "range" and isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}～{value[1]}"
    if field_name == "asset_type":
        return _asset_label(_text(value))
    if field_name == "external_usage_status":
        return "可對外引用" if value else "不可對外引用"
    return _text(value)


def _query_type(plan: Mapping[str, object]) -> str:
    fields = {
        _text(item.get("field"))
        for item in plan.get("supported_constraints") or plan.get("hard_filters") or []
    }
    if "merchant_handle" in fields:
        return "handle"
    if fields & {"entity_name", "merchant_name"}:
        return "brand"
    if fields & {"sales_category_lv1", "sales_category_lv2"}:
        return "category"
    if "interview_year" in fields:
        return "year"
    if "content_tags" in fields:
        return "tag"
    if "asset_type" in fields:
        return "asset_type"
    return "lookup"


def _resolved_handle(plan: Mapping[str, object]) -> Optional[str]:
    for constraint in plan.get("supported_constraints") or plan.get("hard_filters") or []:
        if constraint.get("field") == "merchant_handle":
            return _text(constraint.get("value")) or None
    return None


def _abstain_kind(structured, total_assets: int) -> Optional[str]:
    if (
        structured.unsupported_constraints
        or structured.abstain_reason in {
            "unsupported_hard_constraint",
            "ambiguous_hard_constraint",
            "invalid_hard_constraint",
        }
    ):
        return "unsupported_constraint"
    if total_assets:
        return None
    if structured.total_assets and not total_assets:
        return "governance_filtered"
    if structured.abstain_reason == "no_constraint_intersection":
        return "zero_intersection"
    return "no_exact_match"


def _safe_source(source_sheet: object, source_row: object) -> str:
    sheet = _slack_text(source_sheet) or "未知來源"
    row = str(source_row) if source_row is not None else "?"
    return f"{sheet} r{row}"


def _display_title(value: object) -> str:
    title = _slack_text(value) or "資料未提供"
    if len(title) <= MAX_TITLE_CHARS:
        return title
    head = MAX_TITLE_CHARS - 25
    return f"{title[:head]}…{title[-24:]}"


def _slack_link(url: object, label: str) -> str:
    value = _text(url)
    if not value or not _safe_slack_url(value):
        return f"{_slack_text(label)}（連結未提供）"
    return f"<{escape_mrkdwn_url(value)}|{_slack_text(label)}>"


def _slack_text(value: object) -> str:
    return _text(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unique_sources(entities: Sequence[Mapping[str, object]]) -> List[str]:
    sources = []
    for entity in entities:
        for asset in entity.get("assets") or []:
            source = _slack_text(asset.get("source"))
            if source and source not in sources:
                sources.append(source)
    return sources


def _safe_http_url(value: str) -> bool:
    if not value or not url_is_mrkdwn_safe(value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not (parsed.username or parsed.password)
    )


def _valid_iso_date(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _safe_slack_url(value: str) -> bool:
    return _safe_http_url(value)


def _decision_matches(apply_row: Mapping[str, object], decision: Mapping[str, object]) -> bool:
    return all(
        _text(apply_row.get(column)) == _text(decision.get(column))
        for column in (
            "record_id",
            "asset_id",
            "brand_name",
            "asset_type",
            "field",
            "proposed_value",
            "review_decision",
            "reviewer",
            "reviewed_at",
            "provenance",
            "source_location",
        )
    )


def _row_key(row: Mapping[str, object]) -> Tuple[str, str, str]:
    return (
        _text(row.get("record_id")),
        _text(row.get("asset_id")),
        _text(row.get("field")),
    )


def _asset_label(value: str) -> str:
    return {
        "article": "文章",
        "video": "影片",
        "podcast": "Podcast",
        "news": "新聞",
        "other": "其他",
    }.get(value, "其他")


def _dedupe_dicts(values: Sequence[dict], key: str) -> List[dict]:
    result = []
    seen = set()
    for value in values:
        marker = value.get(key)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _empty_payload(query: str, reason: str) -> dict:
    return {
        "query": query,
        "query_type": "lookup",
        "resolved_handle": None,
        "applied_constraints": [],
        "operator": "AND",
        "entities": [],
        "total_entities": 0,
        "total_assets": 0,
        "citations": [],
        "abstained": True,
        "abstain_kind": reason,
        "unsupported_labels": [],
    }


def _issue(
    issues: List[dict],
    code: str,
    message: str,
    *,
    query: str = "",
    field: str = "",
) -> None:
    issues.append({"code": code, "query": query, "field": field, "message": message})


def _read_csv(path: Path) -> List[dict]:
    if not path.is_file():
        raise SlackOutputPreviewError(f"required preview input does not exist: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SlackOutputPreviewError(f"preview input has no CSV header: {path}")
        return list(reader)


def _assert_safe_output(output_dir: Path, source_paths: Sequence[Path]) -> None:
    output = output_dir.resolve()
    for source in source_paths:
        resolved = source.resolve()
        if output == resolved or output in resolved.parents or resolved in output.parents:
            raise SlackOutputPreviewError("output directory must not overwrite or contain a protected source")
    parts = {part.casefold() for part in output.parts}
    if ".mka" in parts or "obsidian_vault" in parts:
        raise SlackOutputPreviewError("Slack preview cannot write to the formal index or Obsidian Vault")


def _hash_path(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return "missing"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
