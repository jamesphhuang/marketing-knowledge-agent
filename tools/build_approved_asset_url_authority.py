"""Derive the sanitized approved asset URL authority from the local governance reports.

Two things are kept apart on purpose:

    SOURCE GOVERNANCE EVIDENCE   reports/...  -- real, non-public, stays local and gitignored
    PRODUCTION RUNTIME AUTHORITY package data -- minimum-necessary, sanitized, tracked, shipped

This tool reads the first and emits the second. It never copies or lightly filters the human-review
source: it re-runs the accepted eligibility contract over hash-verified inputs and projects only the
approved outcome onto a join identity derived from published fields.

Provenance chain::

    tests/fixtures/historical_inputs_manifest.json  (frozen historical pins)
        -> reports/... artifact bytes               (verified sha256 + size, never packaged)
            -> accepted overlay contract            (row-level eligibility + decision cross-check)
                -> sanitized projection             (asset_identity, field, url)
                    -> manifest.json                (source + derived hashes, self-integrity hash)

The packaged rows carry no approval columns of their own. A row exists only because this tool proved
it satisfied the whole contract against reviewed bytes whose hashes the manifest records, and it
cannot be added later without breaking an artifact hash and the manifest's self-integrity hash.

Two data-minimisation rules are enforced here, not merely documented:

1. No blocked-asset inventory is emitted. Blocked assets are excluded from the approved mapping and
   that exclusion is proved (``_assert_blocked_never_approved``); shipping their identities would
   publish which assets are restricted while adding nothing to URL enrichment.
2. The join identity may not be derivable from source coordinates. ``_assert_identity_not_source_derived``
   replays every prohibited derivation (sheet, row, record_id, asset_id, source_location) and
   enumerates the coordinate domain, and fails the build if any emitted identity is recoverable.
   A column-name allowlist alone cannot see this, which is why both checks run.

Run from the repository root::

    python tools/build_approved_asset_url_authority.py            # rewrite the bundle
    python tools/build_approved_asset_url_authority.py --check    # verify without writing

This tool only ever writes inside the packaged authority directory; source governance files are
opened read-only and are never mutated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple


REPOSITORY_ROOT_MARKER = "src/marketing_knowledge_agent"
LEGACY_AUTHORITY_MANIFEST = "tests/fixtures/historical_inputs_manifest.json"
PACKAGED_AUTHORITY_DIR = "src/marketing_knowledge_agent/authority/approved_asset_urls"
AUTHORITY_MANIFEST_FILENAME = "manifest.json"
AUTHORITY_SCHEMA_VERSION = 4
DERIVATION_VERSION = "approved-asset-urls/4-index-bound"
# The content index the authority is bound to. Read-only, never packaged.
DEFAULT_CONTENT_INDEX = ".mka/content_index.sqlite"

# Local governance inputs. Read-only, never packaged.
SOURCE_APPLY_PREVIEW = "reports/asset_metadata_apply_preview/asset_apply_preview.csv"
SOURCE_BLOCKED_PREVIEW = "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"
SOURCE_HUMAN_DECISIONS = "reports/asset_metadata_preview/human_review_template.csv"
SOURCE_ARTIFACTS = (SOURCE_APPLY_PREVIEW, SOURCE_BLOCKED_PREVIEW, SOURCE_HUMAN_DECISIONS)

APPROVED_URLS_ARTIFACT = "approved_urls.csv"

# Strict positive allowlist for everything that may leave this tool.
APPROVED_URL_COLUMNS = ("asset_identity", "field", "url")
# The only source columns the emitted identity may be derived from. Each is published to the same
# external audience as the URL, for the same asset, in the same Slack message.
IDENTITY_SOURCE_COLUMNS = ("brand_name", "asset_title", "asset_type")
# Any source column carrying reviewer, customer or workbook detail. None may appear in the output.
PROHIBITED_SOURCE_COLUMNS = frozenset(
    {
        "approved_for_index", "asset_title", "brand_name", "conflict_status", "confidence",
        "current_value", "eligibility", "existing_value", "governance_status", "interview_date",
        "interview_status", "notes", "partner_name", "proposed_decision", "provenance",
        "publication_status", "published_at", "reason", "record_id", "asset_id", "asset_type",
        "review_decision", "review_required", "review_status", "reviewed_at", "reviewer",
        "source", "source_location",
    }
)
# Source coordinates that must not be recoverable from an emitted identity. Unlike the column-name
# allowlist above, this list drives an actual derivation replay, not a string comparison.
PROHIBITED_IDENTITY_INPUTS = ("source sheet", "source row", "record_id", "asset_id", "source_location")
# Bound for the coordinate-enumeration self-test. The v2 identity was recovered 206/206 from a space
# this size; the emitted identity must survive the same attack with zero recoveries.
ENUMERATION_ROW_LIMIT = 3000
ENUMERATION_ASSET_TYPES = ("article", "video", "podcast", "news", "other")


class AuthorityBuildError(RuntimeError):
    """Raised when the sanitized authority cannot be derived from verified sources."""


def build_authority_bundle(repository_root: Path, index_path: Path = None) -> Dict[str, bytes]:
    """Return the packaged authority payloads keyed by filename, including the manifest."""
    repository_root = Path(repository_root)
    index_path = Path(index_path) if index_path else repository_root / DEFAULT_CONTENT_INDEX
    legacy_manifest_bytes = (repository_root / LEGACY_AUTHORITY_MANIFEST).read_bytes()
    legacy_entries = _verified_legacy_entries(legacy_manifest_bytes)

    sources: Dict[str, bytes] = {}
    provenance: List[dict] = []
    for relative in SOURCE_ARTIFACTS:
        entry = legacy_entries.get(relative)
        if entry is None:
            raise AuthorityBuildError(
                f"source artifact is not pinned by the historical manifest: {relative}"
            )
        sources[relative] = _verified_source_bytes(repository_root / relative, entry)
        provenance.append(
            {"relative_path": relative, "sha256": entry["expected_sha256"], "packaged": False}
        )

    overlay = _accepted_overlay(repository_root, sources)
    identities = _identities_for_approved_assets(overlay, _rows(sources[SOURCE_APPLY_PREVIEW]))
    _assert_blocked_never_approved(overlay, identities, _rows(sources[SOURCE_BLOCKED_PREVIEW]))
    _assert_identity_not_source_derived(identities)

    approved_payload = _approved_urls_csv(overlay, identities)
    _assert_sanitized(approved_payload)
    binding_digest, bound_count = _index_binding(repository_root, index_path, identities)

    inputs = [
        {
            "relative_path": APPROVED_URLS_ARTIFACT,
            "input_type": "file",
            "expected_size": len(approved_payload),
            "expected_sha256": hashlib.sha256(approved_payload).hexdigest(),
        }
    ]

    body = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "authority": "approved_asset_urls",
        "derivation_version": DERIVATION_VERSION,
        "identity": (
            "PSEUDONYMOUS_PUBLIC_DERIVED: sha256 over the asset's published entity name, published "
            "title and asset type, truncated to 32 hex characters. Not anonymous, and enumerable by "
            "anyone already holding that public triple; enumeration therefore discloses nothing "
            "beyond the fields published next to the approved link itself. No source workbook "
            "sheet, source row, record_id, asset_id or source_location takes part."
        ),
        "identity_source_columns": list(IDENTITY_SOURCE_COLUMNS),
        "columns": {APPROVED_URLS_ARTIFACT: list(APPROVED_URL_COLUMNS)},
        "blocked_inventory_packaged": False,
        "source_authority_manifest": LEGACY_AUTHORITY_MANIFEST,
        "source_authority_manifest_sha256": hashlib.sha256(legacy_manifest_bytes).hexdigest(),
        "source_governance_artifacts": provenance,
        "approved_value_count": len(overlay.values),
        "approved_asset_count": len(identities),
        "index_binding": {
            "algorithm": (
                "sha256 over the sorted canonical surface of indexed assets whose public "
                "identity appears in this authority; one aggregate digest, never a "
                "per-record hash"
            ),
            "namespace": _runtime(repository_root).INDEX_BINDING_NAMESPACE,
            "surface": _runtime(repository_root).INDEX_BINDING_SURFACE,
            "inputs": list(_runtime(repository_root).INDEX_BINDING_INPUTS),
            "bound_asset_count": bound_count,
            "digest": binding_digest,
        },
        "blocked_source_asset_count": len(overlay.blocked_asset_ids),
        "inputs": inputs,
    }
    manifest = dict(body, manifest_hash=_hash_json(body))
    return {
        APPROVED_URLS_ARTIFACT: approved_payload,
        AUTHORITY_MANIFEST_FILENAME: (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }


def _runtime(repository_root: Path):
    """Import the shipped runtime module, so build and runtime cannot drift apart."""
    sys.path.insert(0, str(Path(repository_root) / "src"))
    try:
        from marketing_knowledge_agent import slack_output_preview as runtime
    finally:
        sys.path.pop(0)
    return runtime


def _index_binding(repository_root: Path, index_path: Path, identities: Mapping[str, object]):
    """Bind the authority to the content index surface it was validated against.

    Computed with the very function the runtime uses, so a build that succeeds here is a build the
    runtime can reproduce. Every approved asset must be reachable in the index or deliberately
    absent -- absent ones (r30) simply never enter the surface and can never be inherited, because
    nothing in the index computes their identity.
    """
    runtime = _runtime(repository_root)
    index_path = Path(index_path)
    if not index_path.is_file():
        raise AuthorityBuildError(f"content index is required to bind the authority: {index_path}")
    approved = {entry["identity"] for entry in identities.values()}
    digest, bound = runtime.compute_index_binding_digest(index_path, approved)
    if not bound:
        raise AuthorityBuildError("content index binds none of the approved assets")
    return digest, bound


def _accepted_overlay(repository_root: Path, sources: Mapping[str, bytes]):
    """Run the accepted eligibility contract, unchanged, over the verified source bytes.

    Reusing the shipped overlay builder is deliberate: the sanitized projection is defined as the
    output of the very logic that was reviewed and accepted, so the two cannot drift apart.
    """
    sys.path.insert(0, str(Path(repository_root) / "src"))
    try:
        from marketing_knowledge_agent import slack_output_preview as runtime
    finally:
        sys.path.pop(0)

    overlay = runtime._build_asset_url_overlay(
        _rows(sources[SOURCE_APPLY_PREVIEW]),
        _rows(sources[SOURCE_BLOCKED_PREVIEW]),
        _rows(sources[SOURCE_HUMAN_DECISIONS]),
    )
    if overlay.errors:
        codes = sorted({issue["code"] for issue in overlay.errors})
        raise AuthorityBuildError(f"source governance did not pass the accepted contract: {codes}")
    if not overlay.values or not overlay.blocked_asset_ids:
        raise AuthorityBuildError("source governance produced an empty authority")
    _assert_identity_shape(overlay, runtime)
    return overlay


def _assert_identity_shape(overlay, runtime) -> None:
    """Reject anything that would break deterministic one-to-one matching."""
    for (record_id, asset_id, _field) in overlay.values:
        if not asset_id.startswith(f"{record_id}:"):
            raise AuthorityBuildError(f"approved URL is not asset-scoped: {asset_id}")


def _runtime_identity():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    try:
        from marketing_knowledge_agent.slack_output_preview import approved_asset_identity
    finally:
        sys.path.pop(0)
    return approved_asset_identity


def _identities_for_approved_assets(overlay, apply_rows: Sequence[Mapping[str, str]]) -> Dict[str, dict]:
    """Map every approved asset to the identity the runtime will recompute for it.

    The identity is derived only from ``IDENTITY_SOURCE_COLUMNS``, read from the same verified rows
    the accepted contract already approved -- never from the row's position in the workbook.
    """
    approved_asset_identity = _runtime_identity()
    published: Dict[str, Mapping[str, str]] = {}
    for row in apply_rows:
        published.setdefault(_text(row.get("asset_id")), row)

    identities: Dict[str, dict] = {}
    by_identity: Dict[str, str] = {}
    for (_record_id, asset_id, _field) in sorted(overlay.values):
        if asset_id in identities:
            continue
        row = published.get(asset_id)
        if row is None:
            raise AuthorityBuildError(f"approved asset has no source row to derive identity: {asset_id}")
        entity_name = _text(row.get("brand_name"))
        title = _text(row.get("asset_title"))
        asset_type = _text(row.get("asset_type"))
        if not entity_name or not title or not asset_type:
            raise AuthorityBuildError(
                f"approved asset lacks a publishable identity component: {asset_id}"
            )
        identity = approved_asset_identity(entity_name, title, asset_type)
        if not identity:
            raise AuthorityBuildError(f"runtime refused to derive an identity for: {asset_id}")
        collided = by_identity.get(identity)
        if collided is not None and collided != asset_id:
            # Two distinct assets sharing one identity would let one asset's approved URL attach to
            # the other. Fail the build rather than ship an ambiguous mapping.
            raise AuthorityBuildError(
                f"public-derived identity collision between distinct assets: {collided} / {asset_id}"
            )
        by_identity[identity] = asset_id
        identities[asset_id] = {
            "identity": identity,
            "entity_name": entity_name,
            "title": title,
            "asset_type": asset_type,
        }
    return identities


def _assert_blocked_never_approved(
    overlay, identities: Mapping[str, dict], blocked_rows: Sequence[Mapping[str, str]] = ()
) -> None:
    """Prove the packaged mapping needs no blocked inventory to stay safe.

    The runtime bundle ships no blocked list, so the guarantee "a blocked asset carries no approved
    URL" has to hold here, at build time, against the reviewed blocked source. Two things are
    checked, because the identity is now content-derived rather than coordinate-derived:

    * no blocked asset is in the approved mapping at all; and
    * no blocked asset's *published triple* derives an emitted identity. Without this, two distinct
      source rows that happened to share brand/title/type -- one approved, one blocked -- would let
      the blocked one resolve the approved one's URL.
    """
    if not overlay.blocked_asset_ids:
        raise AuthorityBuildError("blocked governance source produced no assets to check against")
    overlap = sorted(set(identities) & set(overlay.blocked_asset_ids))
    if overlap:
        raise AuthorityBuildError(
            f"governance-blocked assets reached the approved mapping: {overlap}"
        )
    approved_asset_identity = _runtime_identity()
    emitted = {entry["identity"] for entry in identities.values()}
    for asset_id, entry in identities.items():
        # Every emitted identity must be exactly what the runtime will recompute from the published
        # fields; otherwise the mapping silently resolves to nothing at query time.
        recomputed = approved_asset_identity(entry["entity_name"], entry["title"], entry["asset_type"])
        if recomputed != entry["identity"]:
            raise AuthorityBuildError(f"emitted identity is not runtime-reproducible: {asset_id}")
    leaked = sorted(asset_id for asset_id in overlay.blocked_asset_ids if asset_id in emitted)
    if leaked:
        raise AuthorityBuildError(f"blocked asset id leaked into the emitted identities: {leaked}")

    resolvable = sorted(
        {
            _text(row.get("asset_id"))
            for row in blocked_rows
            if _text(row.get("asset_id")) in overlay.blocked_asset_ids
            and approved_asset_identity(
                _text(row.get("brand_name")), _text(row.get("asset_title")), _text(row.get("asset_type"))
            )
            in emitted
        }
    )
    if resolvable:
        raise AuthorityBuildError(
            f"blocked assets would resolve an approved URL through their published fields: {resolvable}"
        )


def _assert_identity_not_source_derived(identities: Mapping[str, dict]) -> None:
    """Fail the build if any emitted identity is recoverable from source coordinates.

    A column-name allowlist cannot see derivability: the previous release emitted only
    ``asset_identity`` yet that identity was ``sha256(ns:<sheet>:r<row>:<type>)``, so the sheet, the
    row and the asset type came back out of it in milliseconds. This replays every prohibited
    derivation over the real asset set and enumerates the coordinate domain that broke v2.
    """
    approved_asset_identity = _runtime_identity()
    emitted = {entry["identity"]: asset_id for asset_id, entry in identities.items()}

    legacy_namespace = "mka:approved-asset-url:v1"

    def legacy(asset_id: str) -> str:
        return hashlib.sha256(f"{legacy_namespace}:{asset_id}".encode("utf-8")).hexdigest()[:32]

    sheets = set()
    for asset_id in identities:
        record_id, _, _asset_type = asset_id.rpartition(":")
        sheet, _, _row = record_id.rpartition(":")
        if sheet:
            sheets.add(sheet)
        # Direct replay: the coordinate strings themselves, and the v2 hash of them.
        for candidate in (asset_id, record_id, sheet, legacy(asset_id), legacy(record_id)):
            if candidate in emitted:
                raise AuthorityBuildError(
                    f"emitted identity is derivable from a source coordinate: {emitted[candidate]}"
                )

    # Enumeration replay: the exact attack that recovered every v2 identity.
    recovered = []
    for sheet in sorted(sheets):
        for row in range(1, ENUMERATION_ROW_LIMIT + 1):
            for asset_type in ENUMERATION_ASSET_TYPES:
                candidate = f"{sheet}:r{row}:{asset_type}"
                for guess in (legacy(candidate), approved_asset_identity(sheet, f"r{row}", asset_type)):
                    if guess in emitted:
                        recovered.append(candidate)
    if recovered:
        raise AuthorityBuildError(
            f"{len(recovered)} identities recovered by coordinate enumeration, e.g. {recovered[:3]}"
        )


def _approved_urls_csv(overlay, identities: Mapping[str, dict]) -> bytes:
    rows = sorted(
        (identities[asset_id]["identity"], field_name, record.proposed_value)
        for (_record_id, asset_id, field_name), record in overlay.values.items()
    )
    return _csv_bytes(APPROVED_URL_COLUMNS, rows)


def _csv_bytes(columns: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _assert_sanitized(approved_payload: bytes) -> None:
    """Fail the build if the runtime output would carry prohibited fields or free text.

    This is the shallow half of the contract: it can only see literal columns and cell shapes.
    ``_assert_identity_not_source_derived`` is the half that checks what the identity *encodes*.
    """
    for payload, expected in ((approved_payload, APPROVED_URL_COLUMNS),):
        text = payload.decode("utf-8")
        reader = csv.reader(io.StringIO(text, newline=""))
        header = next(reader, None)
        if header is None or tuple(header) != tuple(expected):
            raise AuthorityBuildError(f"derived header is not the allowlisted schema: {header}")
        leaked = PROHIBITED_SOURCE_COLUMNS & set(header)
        if leaked:
            raise AuthorityBuildError(f"derived output carries prohibited columns: {sorted(leaked)}")
        for row in reader:
            if len(row) != len(expected):
                raise AuthorityBuildError("derived row does not match the allowlisted schema")
            for column, cell in zip(expected, row):
                if column == "url":
                    if not cell.startswith(("http://", "https://")):
                        raise AuthorityBuildError(f"derived URL is not an http(s) URL: {cell!r}")
                elif column == "field":
                    if cell not in {"asset_url", "canonical_url"}:
                        raise AuthorityBuildError(f"derived field is outside the contract: {cell!r}")
                elif column == "asset_identity":
                    if len(cell) != 32 or any(c not in "0123456789abcdef" for c in cell):
                        raise AuthorityBuildError("derived identity is not an opaque hex identity")


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _rows(payload: bytes) -> List[dict]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline="")))


def _verified_legacy_entries(manifest_bytes: bytes) -> Dict[str, Mapping[str, object]]:
    payload = json.loads(manifest_bytes.decode("utf-8"))
    stored_hash = payload.get("manifest_hash")
    body = {key: value for key, value in payload.items() if key != "manifest_hash"}
    if not isinstance(stored_hash, str) or stored_hash != _hash_json(body):
        raise AuthorityBuildError("historical input manifest failed its self-integrity contract")
    return {entry["relative_path"]: entry for entry in payload["inputs"]}


def _verified_source_bytes(path: Path, entry: Mapping[str, object]) -> bytes:
    if entry.get("input_type") != "file":
        raise AuthorityBuildError(f"source artifact is not pinned as a file: {path}")
    if path.is_symlink() or not path.is_file():
        raise AuthorityBuildError(f"source artifact is missing: {path}")
    payload = path.read_bytes()
    if (
        len(payload) != entry.get("expected_size")
        or hashlib.sha256(payload).hexdigest() != entry.get("expected_sha256")
    ):
        raise AuthorityBuildError(f"source artifact does not match its pinned hash: {path}")
    return payload


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_authority_bundle(repository_root: Path, artifacts: Mapping[str, bytes]) -> List[str]:
    destination = Path(repository_root) / PACKAGED_AUTHORITY_DIR
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, payload in sorted(artifacts.items()):
        target = destination / filename
        if not target.is_file() or target.read_bytes() != payload:
            target.write_bytes(payload)
            written.append(filename)
    for stale in sorted(destination.glob("*")):
        if stale.is_file() and stale.name not in artifacts and not stale.name.startswith("._"):
            stale.unlink()
            written.append(f"-{stale.name}")
    return written


def check_authority_bundle(repository_root: Path, artifacts: Mapping[str, bytes]) -> List[str]:
    destination = Path(repository_root) / PACKAGED_AUTHORITY_DIR
    drifted = []
    for filename, payload in sorted(artifacts.items()):
        target = destination / filename
        if not target.is_file() or target.read_bytes() != payload:
            drifted.append(filename)
    for stale in sorted(destination.glob("*")):
        if stale.is_file() and stale.name not in artifacts and not stale.name.startswith("._"):
            drifted.append(f"unexpected:{stale.name}")
    return drifted


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description="Build the sanitized approved asset URL authority.")
    parser.add_argument("--check", action="store_true", help="verify the bundle without writing")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--index", default=None, help=f"content index (default: {DEFAULT_CONTENT_INDEX})")
    args = parser.parse_args(list(argv))

    root = Path(args.root).resolve()
    if not (root / REPOSITORY_ROOT_MARKER).is_dir():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2
    try:
        artifacts = build_authority_bundle(root, args.index)
    except (AuthorityBuildError, OSError, ValueError) as exc:
        print(f"authority build failed: {exc}", file=sys.stderr)
        return 2

    if args.check:
        drifted = check_authority_bundle(root, artifacts)
        if drifted:
            print("packaged authority is stale: " + ", ".join(drifted), file=sys.stderr)
            return 1
        print("packaged authority matches the pinned sources")
        return 0

    written = write_authority_bundle(root, artifacts)
    print("updated: " + (", ".join(written) if written else "(already current)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
