"""Fail-closed row_v1 lineage evidence for formal content-index rebuilds."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .obsidian_sync import MANAGED_BY, _content_checksum
from .record_identity_lineage import (
    RECORD_IDENTITY_SCHEME_VERSION,
    assert_row_v1_lineage,
    resolve_apply_lineage,
)
from .stable_record_authority import qualify_legacy_record_id


LINEAGE_GATE_PASSED = "PASSED"
LINEAGE_GATE_NOT_PROVIDED = "NOT_PROVIDED"
LINEAGE_GATE_FAILED = "FAILED"


class ContentIndexLineageError(ValueError):
    """Raised when explicit content-index lineage evidence cannot be verified."""


@dataclass(frozen=True)
class ContentIndexLineageEvidence:
    """Explicit receipt chain; none of these paths is discovered implicitly."""

    apply_dir: Path
    sync_plan_path: Path
    sync_manifest_path: Path


def evaluate_content_index_lineage(
    *,
    vault_path: Path,
    namespace: str,
    evidence: Optional[ContentIndexLineageEvidence],
) -> Dict[str, object]:
    """Return a reportable gate result without blocking read-only plan mode."""
    base: Dict[str, object] = {
        "record_identity_scheme": RECORD_IDENTITY_SCHEME_VERSION,
        "lineage_gate": LINEAGE_GATE_NOT_PROVIDED,
        "lineage_evidence": "none",
        "production_reindex_ready": False,
        "lineage_detail": (
            "explicit apply directory, sync plan, and completed sync manifest were not provided"
        ),
    }
    if evidence is None:
        return base

    base["lineage_evidence"] = "explicit_apply_sync_receipt_and_vault_surface"
    try:
        verified = _verify_evidence(
            vault_path=Path(vault_path).resolve(),
            namespace=namespace,
            evidence=evidence,
        )
    except (ContentIndexLineageError, OSError, UnicodeDecodeError, ValueError) as exc:
        base["lineage_gate"] = LINEAGE_GATE_FAILED
        base["lineage_detail"] = str(exc)
        return base

    base.update(verified)
    base["lineage_gate"] = LINEAGE_GATE_PASSED
    base["production_reindex_ready"] = True
    base["lineage_detail"] = "row_v1 apply lineage and current managed merchant Vault surface verified"
    return base


def _verify_evidence(
    *,
    vault_path: Path,
    namespace: str,
    evidence: ContentIndexLineageEvidence,
) -> Dict[str, object]:
    if not isinstance(evidence, ContentIndexLineageEvidence):
        raise ContentIndexLineageError("lineage evidence must use the explicit evidence bundle")

    apply_dir = _existing_directory(evidence.apply_dir, "apply directory")
    plan_path = _existing_json_file(evidence.sync_plan_path, "sync plan")
    manifest_path = _existing_json_file(evidence.sync_manifest_path, "sync manifest")

    lineage = resolve_apply_lineage(apply_dir)
    assert_row_v1_lineage(lineage, operation="build-content-index --confirm")
    expected_workbook = lineage.get("expected_workbook")
    workbook_sha = expected_workbook.get("sha256") if isinstance(expected_workbook, Mapping) else None
    if not _is_sha256(workbook_sha):
        raise ContentIndexLineageError("canonical row_v1 contract carries no usable workbook sha256")

    plan = _read_json_object(plan_path, "sync plan")
    manifest = _read_json_object(manifest_path, "sync manifest")
    _assert_receipt_binding(
        plan=plan,
        plan_path=plan_path,
        manifest=manifest,
        apply_dir=apply_dir,
        vault_path=vault_path,
        namespace=namespace,
        workbook_sha=workbook_sha,
    )

    expected = _expected_merchant_surface(
        plan, apply_dir, workbook_sha, str(manifest["batch_id"])
    )
    actual = _actual_merchant_surface(vault_path, namespace, workbook_sha)
    _assert_surface_match(expected, actual)
    return {
        "lineage_evidence": "explicit_apply_sync_receipt_and_vault_surface",
        "lineage_apply_dir": str(apply_dir),
        "lineage_sync_plan": str(plan_path),
        "lineage_sync_manifest": str(manifest_path),
        "lineage_workbook_sha256": workbook_sha,
        "lineage_merchant_record_count": len(actual),
    }


def _assert_receipt_binding(
    *,
    plan: Mapping[str, object],
    plan_path: Path,
    manifest: Mapping[str, object],
    apply_dir: Path,
    vault_path: Path,
    namespace: str,
    workbook_sha: str,
) -> None:
    if plan.get("schema_version") != "0.1":
        raise ContentIndexLineageError("sync plan uses an unsupported schema")
    if manifest.get("schema_version") != "0.1":
        raise ContentIndexLineageError("sync manifest uses an unsupported schema")
    if manifest.get("status") != "completed":
        raise ContentIndexLineageError("sync manifest is not a completed execution receipt")

    required_paths = {
        "plan apply directory": (plan.get("apply_dir"), apply_dir),
        "plan Vault": (plan.get("vault"), vault_path),
        "manifest apply directory": (manifest.get("apply_dir"), apply_dir),
        "manifest Vault": (manifest.get("vault"), vault_path),
        "manifest plan": (manifest.get("plan_path"), plan_path),
    }
    for label, (declared, expected) in required_paths.items():
        if not isinstance(declared, str) or Path(declared).resolve() != expected:
            raise ContentIndexLineageError(f"{label} does not match the explicit evidence target")

    if plan.get("namespace") != namespace or manifest.get("namespace") != namespace:
        raise ContentIndexLineageError("sync receipt namespace does not match the index namespace")
    state_hash = plan.get("plan_state_hash")
    if not _is_sha256(state_hash) or manifest.get("plan_state_hash") != state_hash:
        raise ContentIndexLineageError("sync receipt does not bind the plan state hash")
    if manifest.get("plan_sha256") != _sha256_file(plan_path):
        raise ContentIndexLineageError("sync receipt does not bind the exact sync plan bytes")
    if manifest.get("record_identity_scheme_version") != RECORD_IDENTITY_SCHEME_VERSION:
        raise ContentIndexLineageError("sync receipt uses an unsupported record identity scheme")
    if manifest.get("row_v1_workbook_sha256") != workbook_sha:
        raise ContentIndexLineageError("sync receipt workbook lineage does not match the canonical contract")
    if not isinstance(plan.get("entries"), list) or not isinstance(manifest.get("actions"), list):
        raise ContentIndexLineageError("sync plan or manifest entries are malformed")
    batch_id = manifest.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise ContentIndexLineageError("sync manifest carries no execution batch id")
    if _planned_action_receipts(plan, namespace) != _manifest_action_receipts(manifest):
        raise ContentIndexLineageError("sync manifest actions do not bind the exact sync plan")


def _expected_merchant_surface(
    plan: Mapping[str, object], apply_dir: Path, workbook_sha: str, batch_id: str
) -> Dict[str, Dict[str, str]]:
    surface: Dict[str, Dict[str, str]] = {}
    for raw in plan["entries"]:
        if not isinstance(raw, Mapping):
            raise ContentIndexLineageError("sync plan entry is malformed")
        if raw.get("record_type") != "merchant_case":
            continue
        action = raw.get("action")
        if action in {"will_archive", "conflict_user_edited", "conflict_unmanaged"}:
            continue
        if action not in {"will_add", "will_update", "unchanged"}:
            raise ContentIndexLineageError(f"sync plan entry has unsupported action: {action!r}")
        preview_path = raw.get("preview_path")
        vault_relative = raw.get("current_vault_path") or raw.get("vault_path")
        if not isinstance(preview_path, str) or not isinstance(vault_relative, str):
            raise ContentIndexLineageError("merchant sync plan entry lacks bound paths")
        source_path = _contained_file(apply_dir, preview_path, "merchant apply preview")
        content = source_path.read_text(encoding="utf-8")
        metadata, _ = parse_markdown_with_frontmatter(content)
        sheet = metadata.get("source_sheet")
        row = metadata.get("source_row")
        if sheet != raw.get("source_sheet") or row != raw.get("source_row"):
            raise ContentIndexLineageError("merchant sync plan identity differs from its apply preview")
        checksum = _content_checksum(content)
        if checksum != raw.get("checksum"):
            raise ContentIndexLineageError("merchant sync plan checksum differs from its apply preview")
        identity = qualify_legacy_record_id(workbook_sha, sheet, row)
        _insert_surface(
            surface,
            vault_relative,
            identity,
            checksum,
            "apply/plan",
            sync_batch_id=batch_id if action in {"will_add", "will_update"} else None,
        )
    return surface


def _actual_merchant_surface(
    vault_path: Path, namespace: str, workbook_sha: str
) -> Dict[str, Dict[str, str]]:
    namespace_path = vault_path / namespace
    if not namespace_path.is_dir():
        raise ContentIndexLineageError(f"Vault namespace does not exist: {namespace_path}")
    surface: Dict[str, Dict[str, str]] = {}
    for path in sorted(namespace_path.rglob("*.md")):
        relative = path.relative_to(namespace_path).as_posix()
        if path.name.startswith("._") or "_archived" in Path(relative).parts:
            continue
        if path.is_symlink():
            raise ContentIndexLineageError(f"managed Vault surface contains a symlink: {relative}")
        try:
            content = path.read_text(encoding="utf-8")
            metadata, _ = parse_markdown_with_frontmatter(content)
        except (OSError, UnicodeDecodeError, FrontmatterError) as exc:
            raise ContentIndexLineageError(f"managed Vault surface is unreadable: {relative}") from exc
        if metadata.get("managed_by") != MANAGED_BY or metadata.get("record_type") != "merchant_case":
            continue
        stored_checksum = metadata.get("content_checksum")
        actual_checksum = _content_checksum(content)
        if not isinstance(stored_checksum, str) or stored_checksum != actual_checksum:
            raise ContentIndexLineageError(f"managed merchant checksum is unverified: {relative}")
        identity = qualify_legacy_record_id(
            workbook_sha, metadata.get("source_sheet"), metadata.get("source_row")
        )
        _insert_surface(
            surface,
            relative,
            identity,
            actual_checksum,
            "Vault",
            sync_batch_id=str(metadata.get("sync_batch_id") or ""),
        )
    return surface


def _assert_surface_match(
    expected: Mapping[str, Mapping[str, str]], actual: Mapping[str, Mapping[str, str]]
) -> None:
    expected_paths = set(expected)
    actual_paths = set(actual)
    if expected_paths != actual_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise ContentIndexLineageError(
            f"managed merchant Vault surface differs from apply/plan; missing={missing}, extra={extra}"
        )
    for path in sorted(expected_paths):
        if (
            expected[path]["qualified_row_v1_identity"]
            != actual[path]["qualified_row_v1_identity"]
            or expected[path]["content_checksum"] != actual[path]["content_checksum"]
            or (
                expected[path].get("sync_batch_id") is not None
                and expected[path]["sync_batch_id"] != actual[path].get("sync_batch_id")
            )
        ):
            raise ContentIndexLineageError(
                f"managed merchant Vault binding differs from apply/plan: {path}"
            )


def _insert_surface(
    surface: Dict[str, Dict[str, str]],
    path: str,
    identity: str,
    checksum: str,
    label: str,
    *,
    sync_batch_id: Optional[str] = None,
) -> None:
    if path in surface:
        raise ContentIndexLineageError(f"duplicate merchant path in {label} surface: {path}")
    if any(item["qualified_row_v1_identity"] == identity for item in surface.values()):
        raise ContentIndexLineageError(f"duplicate qualified merchant identity in {label} surface")
    surface[path] = {
        "qualified_row_v1_identity": identity,
        "content_checksum": checksum,
        **({"sync_batch_id": sync_batch_id} if sync_batch_id is not None else {}),
    }


def _planned_action_receipts(plan: Mapping[str, object], namespace: str) -> list:
    receipts = []
    for raw in plan["entries"]:
        if not isinstance(raw, Mapping):
            raise ContentIndexLineageError("sync plan entry is malformed")
        action = raw.get("action")
        if action not in {"will_add", "will_update", "will_archive"}:
            continue
        relative = (
            raw.get("current_vault_path")
            if action in {"will_update", "will_archive"}
            else raw.get("vault_path")
        )
        if not isinstance(relative, str):
            raise ContentIndexLineageError("mutating sync plan entry lacks a Vault path")
        receipts.append(
            (
                action,
                (Path(namespace) / relative).as_posix(),
                raw.get("source_sheet"),
                raw.get("source_row"),
                raw.get("current_checksum"),
                raw.get("checksum") if action != "will_archive" else None,
            )
        )
    return sorted(receipts, key=repr)


def _manifest_action_receipts(manifest: Mapping[str, object]) -> list:
    receipts = []
    for raw in manifest["actions"]:
        if not isinstance(raw, Mapping):
            raise ContentIndexLineageError("sync manifest action is malformed")
        receipts.append(
            (
                raw.get("action"),
                raw.get("vault_path"),
                raw.get("source_sheet"),
                raw.get("source_row"),
                raw.get("before_checksum"),
                raw.get("after_checksum"),
            )
        )
    return sorted(receipts, key=repr)


def _existing_directory(path: Path, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise ContentIndexLineageError(f"{label} is missing or untrusted: {candidate}")
    return candidate.resolve()


def _existing_json_file(path: Path, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ContentIndexLineageError(f"{label} is missing or untrusted: {candidate}")
    return candidate.resolve()


def _contained_file(root: Path, relative: str, label: str) -> Path:
    unresolved = root / relative
    candidate = unresolved.resolve()
    if unresolved.is_symlink() or root not in candidate.parents or not candidate.is_file():
        raise ContentIndexLineageError(f"{label} path escapes or is missing: {relative}")
    return candidate


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContentIndexLineageError(f"{label} is malformed JSON") from exc
    if not isinstance(payload, dict):
        raise ContentIndexLineageError(f"{label} must be a JSON object")
    return payload


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
