"""Row-V1 workbook lineage guard.

Merchant-case review decisions are still keyed by Excel row coordinate
(``{source_sheet}:{source_row}`` and ``{source_sheet}:r{source_row}``). A coordinate states
*where* a record sat, never *which workbook version* it sat there in, so inserting one row into
the source workbook silently re-points every existing decision at a different merchant.

Until ``stable_record_id`` exists, this module converts that silent mis-binding into a loud
refusal. It does not migrate identity, rebind rows, or rewrite decision history; it only answers
one question, from a tracked contract:

    "Is this preview / apply payload the workbook lineage the current row_v1 decisions were
     made against?"

Read-only analysis (excel preview, taxonomy audit, row-shift analysis, review validation) is
deliberately never blocked: a new workbook must stay analysable. Only the paths that bind or
mutate on row identity fail closed, and they fail before any write.

``record_identity_scheme_version`` is the forward seam. When identity moves to
``stable_record_v2`` the contract gains that scheme and this guard stops applying; an unknown
scheme fails closed rather than being treated as row_v1.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .frontmatter import parse_markdown_with_frontmatter


RECORD_IDENTITY_SCHEME_VERSION = "row_v1"

AUTHORITY_PACKAGE_RELATIVE_DIR = "authority/row_v1_workbook_lineage"
AUTHORITY_MANIFEST_FILENAME = "manifest.json"

# Written by ``excel-preview`` into the preview directory it produces, and by
# ``apply-review-decisions`` into the apply preview directory it produces. Both are declarations
# of provenance, never of authority: the authority is the packaged contract.
PREVIEW_LINEAGE_FILENAME = "workbook_lineage.json"
APPLY_LINEAGE_FILENAME = "record_identity_binding.json"

MERCHANT_HEADER_NAMESPACE = "mka:row-v1-workbook-lineage:v1:merchant-header"
APPLY_SURFACE_NAMESPACE = "mka:row-v1-apply-row-identity-surface:v1"

APPLY_SURFACE_ROOT = "approved_vault_preview"

LINEAGE_MATCH = "LINEAGE_MATCH"
LINEAGE_MISMATCH = "LINEAGE_MISMATCH"
LINEAGE_UNBOUND = "LINEAGE_UNBOUND"
LINEAGE_UNSUPPORTED_SCHEME = "LINEAGE_UNSUPPORTED_SCHEME"

# How the lineage was established, most direct first.
EVIDENCE_DECLARED = "declared_workbook_lineage"
EVIDENCE_PINNED_PREVIEW_PAYLOAD = "pinned_preview_payload"
EVIDENCE_DECLARED_APPLY_BINDING = "declared_apply_record_identity_binding"
EVIDENCE_PINNED_LEGACY_APPLY_SURFACE = "pinned_legacy_apply_row_identity_surface"
EVIDENCE_NONE = "none"

REQUIRED_NEXT_ACTION = (
    "record identity migration (stable_record_id) or an explicitly reviewed row rebinding must "
    "land before this workbook may bind existing row_v1 decisions; read-only preview and analysis "
    "remain available."
)


class RowV1LineageError(ValueError):
    """Raised when a row_v1 binding or mutation path cannot prove its workbook lineage."""


class RowV1LineageContractError(ValueError):
    """Raised when the packaged lineage contract itself is missing or untrustworthy."""


# --- packaged contract ----------------------------------------------------------------------


def _contract_root() -> Path:
    """Resolve the packaged contract from the module location, never from the CWD.

    Mirrors the approved-asset-URL authority: a deployment shipped without the package data
    fails closed here instead of falling back to any writable location.
    """
    root = Path(__file__).resolve().parent / AUTHORITY_PACKAGE_RELATIVE_DIR
    manifest = root / AUTHORITY_MANIFEST_FILENAME
    if manifest.is_symlink() or not manifest.is_file():
        raise RowV1LineageContractError(
            "packaged row_v1 workbook lineage contract is not installed with the module"
        )
    return root


def load_lineage_contract() -> Dict[str, object]:
    """Read the tracked contract and enforce its self-integrity hash before anything reads it."""
    root = _contract_root()
    try:
        payload = json.loads((root / AUTHORITY_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RowV1LineageContractError("row_v1 workbook lineage contract is unreadable") from exc
    if not isinstance(payload, dict):
        raise RowV1LineageContractError("row_v1 workbook lineage contract is malformed")
    stored_hash = payload.get("manifest_hash")
    body = {key: value for key, value in payload.items() if key != "manifest_hash"}
    if not isinstance(stored_hash, str) or stored_hash != _hash_json(body):
        raise RowV1LineageContractError(
            "row_v1 workbook lineage contract failed its self-integrity contract"
        )
    if payload.get("record_identity_scheme_version") != RECORD_IDENTITY_SCHEME_VERSION:
        raise RowV1LineageContractError(
            "row_v1 workbook lineage contract declares an unsupported record identity scheme"
        )
    lineage = payload.get("lineage_workbook")
    if not isinstance(lineage, Mapping) or not isinstance(lineage.get("sha256"), str):
        raise RowV1LineageContractError("row_v1 workbook lineage contract is malformed")
    return payload


# --- identity derivation --------------------------------------------------------------------


def merchant_header_fingerprint(headers: Sequence[object]) -> str:
    """Fingerprint the merchant sheet header row.

    Diagnostic only. The workbook sha256 already separates any two distinct files; this exists so
    a refusal can say *how* the workbooks differ instead of only *that* they differ.
    """
    normalized = [str(value).strip() if value is not None else "" for value in headers]
    return _namespaced_digest(MERCHANT_HEADER_NAMESPACE, normalized)


def workbook_lineage_identity(
    workbook_path: Path,
    *,
    merchant_sheet_name: str,
    merchant_header_row: int,
    merchant_headers: Sequence[object],
    merchant_record_count: int,
    merchant_source_row_min: Optional[int],
    merchant_source_row_max: Optional[int],
) -> Dict[str, object]:
    """Describe the workbook a preview directory was produced from.

    Takes the sheet-derived facts as arguments so this module never parses xlsx, which keeps it
    importable from the preview layer without a cycle.
    """
    workbook_path = Path(workbook_path)
    payload = workbook_path.read_bytes()
    return {
        "filename": workbook_path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "merchant_sheet_name": merchant_sheet_name,
        "merchant_header_row": merchant_header_row,
        "merchant_header_fingerprint": merchant_header_fingerprint(merchant_headers),
        "merchant_record_count": merchant_record_count,
        "merchant_source_row_min": merchant_source_row_min,
        "merchant_source_row_max": merchant_source_row_max,
    }


def apply_row_identity_surface_entries(apply_dir: Path) -> List[List[str]]:
    """Read the row-identity surface an apply preview directory exposes to the sync path.

    One entry per vault-bound markdown record: the row coordinate it claims plus the path it
    occupies. Deliberately excludes file content — ``applied_at`` changes on every apply run, so
    a content hash could not be pinned, while the coordinate-to-file mapping is exactly what a
    row shift changes.
    """
    root = Path(apply_dir) / APPLY_SURFACE_ROOT
    entries: List[List[str]] = []
    if not root.is_dir():
        return entries
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("._") or not path.is_file():
            continue
        metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        source_row = metadata.get("source_row")
        entries.append(
            [
                str(metadata.get("source_sheet") or ""),
                "" if source_row in (None, "") else str(source_row),
                str(metadata.get("record_type") or ""),
                path.relative_to(Path(apply_dir)).as_posix(),
            ]
        )
    entries.sort()
    return entries


def apply_row_identity_surface_digest(entries: Iterable[Sequence[str]]) -> str:
    rows = sorted([list(entry) for entry in entries])
    return _namespaced_digest(APPLY_SURFACE_NAMESPACE, rows)


# --- lineage resolution ---------------------------------------------------------------------


def resolve_preview_lineage(preview_dir: Path) -> Dict[str, object]:
    """Classify a preview directory against the pinned row_v1 lineage.

    Two independent proofs, in order:

    1. ``workbook_lineage.json`` written by ``excel-preview``. This is the direct statement and
       survives a legitimate re-run of the preview against the same workbook, which changes
       ``normalized_at`` and therefore every payload hash.
    2. The pinned preview payload hashes, for a preview directory produced before this guard
       existed. Absence of both is not treated as a match.
    """
    preview_dir = Path(preview_dir)
    contract = load_lineage_contract()
    lineage = contract["lineage_workbook"]
    expected_sha256 = lineage["sha256"]

    status: Dict[str, object] = {
        "subject": str(preview_dir),
        "subject_kind": "preview_dir",
        "record_identity_scheme_version": RECORD_IDENTITY_SCHEME_VERSION,
        "expected_workbook": dict(lineage),
        "actual_workbook_sha256": None,
        "declared_scheme_version": None,
        "evidence": EVIDENCE_NONE,
        "detail": None,
    }

    declared, declared_error = _read_declaration(preview_dir / PREVIEW_LINEAGE_FILENAME)
    if declared_error is not None:
        status["state"] = LINEAGE_UNBOUND
        status["detail"] = declared_error
        return status

    if declared is not None:
        scheme = declared.get("record_identity_scheme_version")
        status["declared_scheme_version"] = scheme
        workbook = declared.get("workbook")
        actual_sha256 = workbook.get("sha256") if isinstance(workbook, Mapping) else None
        status["actual_workbook_sha256"] = actual_sha256
        status["evidence"] = EVIDENCE_DECLARED
        if scheme != RECORD_IDENTITY_SCHEME_VERSION:
            status["state"] = LINEAGE_UNSUPPORTED_SCHEME
            status["detail"] = (
                "preview directory declares record_identity_scheme_version="
                f"{scheme!r}; this guard only governs {RECORD_IDENTITY_SCHEME_VERSION!r}"
            )
            return status
        if not isinstance(actual_sha256, str) or len(actual_sha256) != 64:
            status["state"] = LINEAGE_UNBOUND
            status["detail"] = "declared workbook lineage carries no usable workbook sha256"
            return status
        if actual_sha256 != expected_sha256:
            status["state"] = LINEAGE_MISMATCH
            status["detail"] = _workbook_difference(lineage, workbook)
            return status
        status["state"] = LINEAGE_MATCH
        return status

    payload_mismatch = _pinned_payload_mismatch(preview_dir, contract.get("preview_payload"))
    if payload_mismatch is None:
        status["state"] = LINEAGE_MATCH
        status["actual_workbook_sha256"] = expected_sha256
        status["evidence"] = EVIDENCE_PINNED_PREVIEW_PAYLOAD
        return status

    status["state"] = LINEAGE_UNBOUND
    status["detail"] = (
        f"preview directory declares no `{PREVIEW_LINEAGE_FILENAME}` and its payload does not "
        f"match the pinned row_v1 lineage ({payload_mismatch}); the workbook it was produced "
        "from cannot be established"
    )
    return status


def resolve_apply_lineage(apply_dir: Path) -> Dict[str, object]:
    """Classify an apply preview directory against the pinned row_v1 lineage.

    The declared binding must also match the directory it describes: a binding copied from
    another apply run carries a row-identity surface digest that no longer recomputes.
    """
    apply_dir = Path(apply_dir)
    contract = load_lineage_contract()
    lineage = contract["lineage_workbook"]
    expected_sha256 = lineage["sha256"]
    observed_digest = apply_row_identity_surface_digest(apply_row_identity_surface_entries(apply_dir))

    status: Dict[str, object] = {
        "subject": str(apply_dir),
        "subject_kind": "apply_dir",
        "record_identity_scheme_version": RECORD_IDENTITY_SCHEME_VERSION,
        "expected_workbook": dict(lineage),
        "actual_workbook_sha256": None,
        "declared_scheme_version": None,
        "evidence": EVIDENCE_NONE,
        "detail": None,
        "row_identity_surface_digest": observed_digest,
    }

    declared, declared_error = _read_declaration(apply_dir / APPLY_LINEAGE_FILENAME)
    if declared_error is not None:
        status["state"] = LINEAGE_UNBOUND
        status["detail"] = declared_error
        return status

    if declared is not None:
        scheme = declared.get("record_identity_scheme_version")
        status["declared_scheme_version"] = scheme
        workbook = declared.get("workbook")
        actual_sha256 = workbook.get("sha256") if isinstance(workbook, Mapping) else None
        status["actual_workbook_sha256"] = actual_sha256
        status["evidence"] = EVIDENCE_DECLARED_APPLY_BINDING
        if scheme != RECORD_IDENTITY_SCHEME_VERSION:
            status["state"] = LINEAGE_UNSUPPORTED_SCHEME
            status["detail"] = (
                "apply preview declares record_identity_scheme_version="
                f"{scheme!r}; this guard only governs {RECORD_IDENTITY_SCHEME_VERSION!r}"
            )
            return status
        if not isinstance(actual_sha256, str) or len(actual_sha256) != 64:
            status["state"] = LINEAGE_UNBOUND
            status["detail"] = "declared record identity binding carries no usable workbook sha256"
            return status
        if actual_sha256 != expected_sha256:
            status["state"] = LINEAGE_MISMATCH
            status["detail"] = _workbook_difference(lineage, workbook)
            return status
        if declared.get("row_identity_surface_digest") != observed_digest:
            status["state"] = LINEAGE_MISMATCH
            status["detail"] = (
                "record identity binding does not describe this apply preview: surface digest "
                f"{declared.get('row_identity_surface_digest')} declared, {observed_digest} "
                "recomputed"
            )
            return status
        status["state"] = LINEAGE_MATCH
        return status

    if observed_digest == contract.get("legacy_apply_row_identity_surface_digest"):
        status["state"] = LINEAGE_MATCH
        status["actual_workbook_sha256"] = expected_sha256
        status["evidence"] = EVIDENCE_PINNED_LEGACY_APPLY_SURFACE
        return status

    status["state"] = LINEAGE_UNBOUND
    status["detail"] = (
        f"apply preview declares no `{APPLY_LINEAGE_FILENAME}` and its row identity surface is "
        "not the pinned pre-guard production surface; re-run apply-review-decisions to produce a "
        "lineage-bound apply preview"
    )
    return status


def assert_row_v1_lineage(status: Mapping[str, object], *, operation: str) -> Mapping[str, object]:
    """Fail closed unless the subject is provably the pinned row_v1 lineage.

    Call this before the first write of any binding or mutation path. Read-only analysis must not
    call it: those paths report ``status`` instead.
    """
    if status.get("state") == LINEAGE_MATCH:
        return status
    raise RowV1LineageError(render_lineage_refusal(status, operation=operation))


def render_lineage_refusal(status: Mapping[str, object], *, operation: str) -> str:
    expected = status.get("expected_workbook") or {}
    actual = status.get("actual_workbook_sha256") or "unknown (not declared)"
    lines = [
        "row_v1 record identity lineage check failed; refusing to bind existing row-based review "
        "decisions to a workbook they were not reviewed against.",
        f"  lineage state                  : {status.get('state')}",
        f"  operation                      : {operation}",
        f"  subject                        : {status.get('subject')}",
        f"  record identity scheme version : {RECORD_IDENTITY_SCHEME_VERSION}",
        f"  expected workbook lineage      : {expected.get('filename')} "
        f"sha256={expected.get('sha256')}",
        f"  expected merchant sheet        : {expected.get('merchant_sheet_name')} "
        f"({expected.get('merchant_record_count')} records, rows "
        f"{expected.get('merchant_source_row_min')}..{expected.get('merchant_source_row_max')})",
        f"  actual workbook sha256         : {actual}",
        f"  evidence                       : {status.get('evidence')}",
    ]
    if status.get("declared_scheme_version") is not None:
        lines.append(
            f"  declared scheme version        : {status.get('declared_scheme_version')}"
        )
    if status.get("detail"):
        lines.append(f"  detail                         : {status.get('detail')}")
    lines.append(f"  required next action           : {REQUIRED_NEXT_ACTION}")
    return "\n".join(lines)


def describe_lineage_status(status: Mapping[str, object]) -> List[str]:
    """Render a lineage status for a read-only report, where mismatch is labelled, not fatal."""
    expected = status.get("expected_workbook") or {}
    lines = [
        f"- Record identity scheme version: `{RECORD_IDENTITY_SCHEME_VERSION}`",
        f"- Row-V1 workbook lineage: `{status.get('state')}`",
        f"- Expected workbook sha256: `{expected.get('sha256')}`",
        f"- Actual workbook sha256: `{status.get('actual_workbook_sha256') or 'unknown (not declared)'}`",
        f"- Lineage evidence: `{status.get('evidence')}`",
    ]
    if status.get("detail"):
        lines.append(f"- Lineage detail: {status.get('detail')}")
    if status.get("state") != LINEAGE_MATCH:
        lines.append(
            "- Read-only analysis of this workbook is allowed; applying existing row_v1 review "
            "decisions, syncing them into the vault, or rebuilding a formal binding from them is "
            "blocked until identity migration or a reviewed rebinding lands."
        )
    return lines


def build_apply_lineage_binding(
    *,
    preview_status: Mapping[str, object],
    surface_entries: Iterable[Sequence[str]],
    applied_at: str,
    decisions_path: object,
) -> Dict[str, object]:
    """Stamp the lineage an apply preview was produced under, for the sync path to verify."""
    expected = preview_status.get("expected_workbook") or {}
    return {
        "record_identity_scheme_version": RECORD_IDENTITY_SCHEME_VERSION,
        "workbook": dict(expected),
        "preview_lineage_state": preview_status.get("state"),
        "preview_lineage_evidence": preview_status.get("evidence"),
        "preview_dir": preview_status.get("subject"),
        "decisions_path": str(decisions_path),
        "applied_at": applied_at,
        "row_identity_surface_digest": apply_row_identity_surface_digest(surface_entries),
        "note": (
            "Row identity is still the primary review identity. This binding records which "
            "workbook lineage these decisions were made against; it assigns no stable_record_id "
            "and rewrites no decision history."
        ),
    }


# --- internals ------------------------------------------------------------------------------


def _read_declaration(path: Path) -> Tuple[Optional[Mapping[str, object]], Optional[str]]:
    """Return (declaration, error). A missing file is not an error; an unusable one is."""
    if path.is_symlink():
        return None, f"lineage declaration is a symlink and is not trusted: {path}"
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, f"lineage declaration is unreadable: {path}"
    if not isinstance(payload, dict):
        return None, f"lineage declaration is malformed: {path}"
    return payload, None


def _pinned_payload_mismatch(preview_dir: Path, pinned: object) -> Optional[str]:
    if not isinstance(pinned, list) or not pinned:
        return "contract pins no preview payload"
    for entry in pinned:
        if not isinstance(entry, Mapping):
            return "contract preview payload is malformed"
        relative = entry.get("relative_path")
        expected_sha256 = entry.get("expected_sha256")
        expected_size = entry.get("expected_size")
        if not isinstance(relative, str) or not isinstance(expected_sha256, str):
            return "contract preview payload is malformed"
        path = preview_dir / relative
        if path.is_symlink() or not path.is_file():
            return f"{relative} is missing"
        payload = path.read_bytes()
        if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
            return f"{relative} differs from the pinned row_v1 payload"
    return None


def _workbook_difference(expected: Mapping[str, object], actual: object) -> str:
    if not isinstance(actual, Mapping):
        return "declared workbook lineage is malformed"
    differences = []
    for field in ("merchant_sheet_name", "merchant_record_count", "merchant_header_fingerprint",
                  "merchant_source_row_min", "merchant_source_row_max"):
        if field in actual and actual.get(field) != expected.get(field):
            differences.append(f"{field}: expected {expected.get(field)!r}, actual {actual.get(field)!r}")
    if not differences:
        return "workbook sha256 differs; merchant sheet shape is unchanged"
    return "; ".join(differences)


def _namespaced_digest(namespace: str, value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{namespace}:{payload}".encode("utf-8")).hexdigest()


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
