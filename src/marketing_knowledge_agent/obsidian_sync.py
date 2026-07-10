from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .frontmatter import parse_markdown_with_frontmatter
from .governance import GovernanceIndex, RestrictedCustomerRecord, split_restricted_aliases


DEFAULT_NAMESPACE = "MKA"
DEFAULT_OBSIDIAN_VAULT = Path(__file__).resolve().parents[2] / "obsidian_vault"
DEFAULT_SYNC_OUTPUT = Path("reports/obsidian_sync")
MANAGED_BY = "marketing-knowledge-agent"
SYNC_METADATA_KEYS = {"managed_by", "sync_batch_id", "synced_at", "content_checksum"}
SYNCABLE_PREVIEW_DIRS = (
    Path("approved_vault_preview/merchant_cases"),
    Path("approved_vault_preview/public_metrics"),
    Path("approved_vault_preview/_vault_only"),
)
ACTION_NAMES = (
    "will_add",
    "will_update",
    "will_archive",
    "unchanged",
    "conflict_user_edited",
    "conflict_unmanaged",
)


class ObsidianSyncError(ValueError):
    """Raised when a sync plan or execution cannot pass its safety gates."""


def create_sync_plan(
    apply_dir: Path,
    vault_path: Path = DEFAULT_OBSIDIAN_VAULT,
    namespace: str = DEFAULT_NAMESPACE,
    output_dir: Path = DEFAULT_SYNC_OUTPUT,
) -> dict:
    apply_dir = Path(apply_dir).resolve()
    vault_path = Path(vault_path).resolve()
    output_dir = Path(output_dir).resolve()
    if not apply_dir.is_dir():
        raise ObsidianSyncError(f"apply preview directory not found: {apply_dir}")

    namespace_path = _namespace_path(vault_path, namespace)
    _assert_output_outside_vault(output_dir, vault_path)
    preview_items = _load_preview_items(apply_dir)
    current_files = _load_namespace_files(namespace_path)
    current_by_source = {
        key: item for key, item in ((_source_key(item["metadata"]), item) for item in current_files.values()) if key
    }
    current_by_path = {item["relative_path"]: item for item in current_files.values()}
    matched_paths = set()
    entries: List[dict] = []

    for preview in preview_items:
        source_key = _source_key(preview["metadata"])
        target_path = preview["vault_path"]
        current = current_by_source.get(source_key) if source_key else None
        if current is None:
            current = current_by_path.get(target_path)
        if current is not None:
            matched_paths.add(current["relative_path"])
            entries.append(_classify_existing(preview, current))
        else:
            entries.append(
                {
                    **_entry_identity(preview),
                    "action": "will_add",
                    "current_checksum": None,
                    "stored_checksum": None,
                    "current_vault_path": None,
                }
            )

    preview_keys = {_source_key(item["metadata"]) for item in preview_items}
    for current in sorted(current_files.values(), key=lambda item: item["relative_path"]):
        if current["relative_path"] in matched_paths:
            continue
        if not current["managed"]:
            continue
        if _source_key(current["metadata"]) in preview_keys:
            continue
        entries.append(
            {
                "action": "will_archive",
                "source_sheet": current["metadata"].get("source_sheet"),
                "source_row": current["metadata"].get("source_row"),
                "record_type": current["metadata"].get("record_type"),
                "preview_path": None,
                "vault_path": current["relative_path"],
                "checksum": None,
                "current_checksum": current["checksum"],
                "stored_checksum": current["metadata"].get("content_checksum"),
                "current_vault_path": current["relative_path"],
            }
        )

    counts = {name: 0 for name in ACTION_NAMES}
    for entry in entries:
        counts[entry["action"]] += 1
    generated_at = _utc_now()
    plan_id = f"{generated_at.replace(':', '').replace('-', '')}_{uuid.uuid4().hex[:8]}"
    plan_state_hash = _plan_state_hash(apply_dir, namespace_path)
    payload = {
        "schema_version": "0.1",
        "generated_at": generated_at,
        "plan_id": plan_id,
        "batch_id": f"plan_{plan_id}",
        "apply_dir": str(apply_dir),
        "vault": str(vault_path),
        "namespace": namespace,
        "namespace_path": str(namespace_path),
        "output_dir": str(output_dir),
        "plan_state_hash": plan_state_hash,
        "counts": counts,
        "entries": entries,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"sync_plan_{plan_id}.json"
    markdown_path = output_dir / f"sync_plan_{plan_id}.md"
    _write_json(json_path, payload)
    _atomic_write_text(markdown_path, _render_plan(payload))
    return {**payload, "json_path": str(json_path), "markdown_path": str(markdown_path)}


def execute_sync_plan(
    plan_path: Path,
    vault_path: Path = DEFAULT_OBSIDIAN_VAULT,
    confirm: bool = False,
    allow_conflicts_skip: bool = False,
) -> dict:
    plan_path = Path(plan_path).resolve()
    plan = _read_json(plan_path)
    if not confirm:
        return {
            "requires_confirmation": True,
            "message": "execute requires --confirm; no files were written.",
            "plan_path": str(plan_path),
            "counts": plan.get("counts", {}),
        }

    vault_path = Path(vault_path).resolve()
    namespace_path = _namespace_path(vault_path, plan.get("namespace", DEFAULT_NAMESPACE))
    _assert_output_outside_vault(plan_path.parent, vault_path)
    if Path(plan.get("vault", "")).resolve() != vault_path:
        raise ObsidianSyncError("plan vault does not match the execute vault")
    apply_dir = Path(plan["apply_dir"]).resolve()
    if _plan_state_hash(apply_dir, namespace_path) != plan.get("plan_state_hash"):
        raise ObsidianSyncError("狀態已改變，請重新產生 plan 並重新確認")

    conflict_count = plan.get("counts", {}).get("conflict_user_edited", 0) + plan.get("counts", {}).get(
        "conflict_unmanaged", 0
    )
    if conflict_count and not allow_conflicts_skip:
        raise ObsidianSyncError("plan contains conflict entries; resolve them or use --allow-conflicts-skip")

    _assert_apply_summary_safe(apply_dir)
    restricted_index = _load_restricted_index(apply_dir)
    _assert_denylist_final_gate(plan, apply_dir, restricted_index)
    if not (vault_path / ".obsidian").is_dir():
        raise ObsidianSyncError(f"Obsidian vault marker not found: {vault_path / '.obsidian'}")
    _assert_output_outside_vault(plan_path.parent, vault_path)

    output_dir = plan_path.parent
    batch_id = _execution_batch_id()
    backup_dir = output_dir / f"backup_{batch_id}"
    _backup_namespace(namespace_path, backup_dir)
    _write_json(
        backup_dir / "sync_metadata.json",
        {"namespace": plan.get("namespace", DEFAULT_NAMESPACE)},
    )
    outside_snapshot = _snapshot_outside_namespace(vault_path, namespace_path)
    manifest_path = output_dir / f"manifest_{batch_id}.json"
    manifest = {
        "schema_version": "0.1",
        "status": "running",
        "batch_id": batch_id,
        "plan_path": str(plan_path),
        "vault": str(vault_path),
        "namespace": plan.get("namespace", DEFAULT_NAMESPACE),
        "actions": [],
    }
    _write_json(manifest_path, manifest)

    try:
        for entry in plan.get("entries", []):
            action = entry["action"]
            if action in {"will_add", "will_update"}:
                source_path = _safe_apply_path(apply_dir, entry["preview_path"])
                source_content = source_path.read_text(encoding="utf-8")
                _assert_text_not_restricted(source_content, restricted_index, "denylist final gate")
                synced_content, checksum = _synced_content(source_content, batch_id)
                target_relative = entry["vault_path"]
                target_path = _safe_namespace_path(namespace_path, target_relative)
                if action == "will_update" and entry.get("current_vault_path"):
                    target_path = _safe_namespace_path(namespace_path, entry["current_vault_path"])
                _atomic_write_text(target_path, synced_content)
                _assert_written_checksum(target_path, checksum)
                manifest["actions"].append(
                    {
                        "action": action,
                        "vault_path": str(target_path.relative_to(vault_path)),
                        "source_sheet": entry.get("source_sheet"),
                        "source_row": entry.get("source_row"),
                        "before_checksum": entry.get("current_checksum"),
                        "after_checksum": checksum,
                    }
                )
            elif action == "will_archive":
                current_path = _safe_namespace_path(namespace_path, entry["current_vault_path"])
                archive_relative = Path("_archived") / batch_id / entry["current_vault_path"]
                archive_path = _safe_namespace_path(namespace_path, archive_relative.as_posix())
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current_path, archive_path)
                manifest["actions"].append(
                    {
                        "action": action,
                        "vault_path": str(current_path.relative_to(vault_path)),
                        "archive_path": str(archive_path.relative_to(vault_path)),
                        "source_sheet": entry.get("source_sheet"),
                        "source_row": entry.get("source_row"),
                        "before_checksum": entry.get("current_checksum"),
                        "after_checksum": None,
                    }
                )

        _assert_namespace_outside_unchanged(vault_path, namespace_path, outside_snapshot)
        _assert_namespace_denylist_clean(namespace_path, restricted_index)
        _assert_manifest_conservation(plan, manifest["actions"])
        manifest["status"] = "completed"
        manifest["completed_at"] = _utc_now()
        _write_json(manifest_path, manifest)
        _append_audit_log(output_dir.parent / "audit_log.csv", manifest, plan_path)
        return {
            "status": "completed",
            "batch_id": batch_id,
            "manifest_path": str(manifest_path),
            "counts": plan.get("counts", {}),
            "executed_actions": len(manifest["actions"]),
        }
    except Exception as exc:
        _restore_namespace(namespace_path, backup_dir)
        manifest["status"] = "aborted_and_restored"
        manifest["error"] = str(exc)
        manifest["restored_at"] = _utc_now()
        _write_json(manifest_path, manifest)
        raise ObsidianSyncError(f"執行失敗，已從備份還原 namespace: {exc}") from exc


def rollback_sync(
    batch_id: str,
    vault_path: Path = DEFAULT_OBSIDIAN_VAULT,
    output_dir: Path = DEFAULT_SYNC_OUTPUT,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(batch_id)):
        raise ObsidianSyncError("invalid batch id")
    vault_path = Path(vault_path).resolve()
    output_dir = Path(output_dir).resolve()
    _assert_output_outside_vault(output_dir, vault_path)
    backup_dir = output_dir / f"backup_{batch_id}"
    if not backup_dir.exists():
        raise ObsidianSyncError(f"backup not found for batch: {batch_id}")
    backup_metadata = backup_dir / "sync_metadata.json"
    namespace = DEFAULT_NAMESPACE
    if backup_metadata.is_file():
        namespace = _read_json(backup_metadata).get("namespace", DEFAULT_NAMESPACE)
    namespace_path = _namespace_path(vault_path, namespace)
    if not (vault_path / ".obsidian").is_dir():
        raise ObsidianSyncError(f"Obsidian vault marker not found: {vault_path / '.obsidian'}")

    _restore_namespace(namespace_path, backup_dir)
    manifest_path = output_dir / f"rollback_manifest_{batch_id}_{uuid.uuid4().hex[:8]}.json"
    manifest = {
        "schema_version": "0.1",
        "status": "rolled_back",
        "batch_id": batch_id,
        "vault": str(vault_path),
        "namespace": namespace,
        "rolled_back_at": _utc_now(),
    }
    _write_json(manifest_path, manifest)
    _append_audit_log(output_dir.parent / "audit_log.csv", manifest, None, action="rollback")
    return {**manifest, "manifest_path": str(manifest_path)}


def _load_preview_items(apply_dir: Path) -> List[dict]:
    items = []
    for directory in SYNCABLE_PREVIEW_DIRS:
        root = apply_dir / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if path.name.startswith("._") or not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            metadata, _ = parse_markdown_with_frontmatter(content)
            if not metadata.get("source_sheet") or metadata.get("source_row") in (None, ""):
                raise ObsidianSyncError(f"sync preview record lacks source identity: {path}")
            relative_path = path.relative_to(apply_dir).as_posix()
            if directory.name == "_vault_only":
                vault_path = (Path("_vault_only") / path.relative_to(root)).as_posix()
            else:
                vault_path = (Path(directory.name) / path.relative_to(root)).as_posix()
            items.append(
                {
                    "preview_path": relative_path,
                    "vault_path": vault_path,
                    "metadata": metadata,
                    "checksum": _content_checksum(content),
                }
            )
    return items


def _load_namespace_files(namespace_path: Path) -> Dict[str, dict]:
    if not namespace_path.exists():
        return {}
    records = {}
    for path in sorted(namespace_path.rglob("*.md")):
        if path.name.startswith("._") or not path.is_file():
            continue
        relative_path = path.relative_to(namespace_path).as_posix()
        if relative_path.startswith("_archived/"):
            continue
        content = path.read_text(encoding="utf-8")
        metadata, _ = parse_markdown_with_frontmatter(content)
        records[relative_path] = {
            "relative_path": relative_path,
            "metadata": metadata,
            "content": content,
            "checksum": _content_checksum(content),
            "managed": metadata.get("managed_by") == MANAGED_BY,
        }
    return records


def _classify_existing(preview: dict, current: dict) -> dict:
    action = "unchanged"
    if not current["managed"]:
        action = "conflict_unmanaged"
    elif not current["metadata"].get("content_checksum"):
        action = "conflict_user_edited"
    elif current["checksum"] != current["metadata"].get("content_checksum"):
        action = "conflict_user_edited"
    elif current["checksum"] != preview["checksum"]:
        action = "will_update"
    return {
        **_entry_identity(preview),
        "action": action,
        "current_checksum": current["checksum"],
        "stored_checksum": current["metadata"].get("content_checksum"),
        "current_vault_path": current["relative_path"],
    }


def _entry_identity(preview: dict) -> dict:
    metadata = preview["metadata"]
    return {
        "source_sheet": metadata.get("source_sheet"),
        "source_row": metadata.get("source_row"),
        "record_type": metadata.get("record_type"),
        "preview_path": preview["preview_path"],
        "vault_path": preview["vault_path"],
        "checksum": preview["checksum"],
    }


def _source_key(metadata: dict) -> Optional[Tuple[str, str]]:
    sheet = metadata.get("source_sheet")
    row = metadata.get("source_row")
    if sheet in (None, "") or row in (None, ""):
        return None
    return str(sheet), str(row)


def _plan_state_hash(apply_dir: Path, namespace_path: Path) -> str:
    state = {
        "apply": _tree_checksums(apply_dir),
        "namespace": _tree_checksums(namespace_path),
    }
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _tree_checksums(root: Path) -> List[dict]:
    if not root.exists():
        return []
    entries = []
    for path in sorted(root.rglob("*")):
        if path.name.startswith("._") or not path.is_file():
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return entries


def _content_checksum(content: str) -> str:
    try:
        metadata, body = parse_markdown_with_frontmatter(content)
        canonical = {
            "metadata": {key: value for key, value in metadata.items() if key not in SYNC_METADATA_KEYS},
            "body": body,
        }
        raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        raw = content
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _synced_content(content: str, batch_id: str) -> Tuple[str, str]:
    metadata, body = parse_markdown_with_frontmatter(content)
    checksum = _content_checksum(content)
    metadata.update(
        {
            "managed_by": MANAGED_BY,
            "sync_batch_id": batch_id,
            "synced_at": _utc_now(),
            "content_checksum": checksum,
        }
    )
    return _render_markdown(metadata, body), checksum


def _render_markdown(metadata: dict, body: str) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        lines.extend(_yaml_lines(key, value))
    lines.extend(["---", "", body.rstrip(), ""])
    return "\n".join(lines)


def _yaml_lines(key: str, value) -> List[str]:
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        return [f"{key}:"] + [f"  - {_yaml_scalar(item)}" for item in value]
    if isinstance(value, dict):
        return [f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"]
    return [f"{key}: {_yaml_scalar(value)}"]


def _yaml_scalar(value) -> str:
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise ObsidianSyncError("metadata values with literal newlines cannot be rendered safely")
        return f"'{value}'"
    return json.dumps(value, ensure_ascii=False)


def _assert_apply_summary_safe(apply_dir: Path) -> None:
    path = apply_dir / "apply_decisions_summary.md"
    if not path.is_file():
        raise ObsidianSyncError("apply_decisions_summary.md is missing")
    text = path.read_text(encoding="utf-8")
    required = (
        "Conservation ok: yes",
        "Restricted whitelist assertion: passed",
        "Pending metric vault assertion: passed",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise ObsidianSyncError("apply preview safety assertions did not pass: " + ", ".join(missing))


def _load_restricted_index(apply_dir: Path) -> GovernanceIndex:
    path = apply_dir / "governance_table_preview" / "restricted_customers.json"
    if not path.is_file():
        raise ObsidianSyncError(f"restricted customer preview is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ObsidianSyncError("restricted customer preview must be a JSON list")
    records = []
    for item in payload:
        records.append(
            RestrictedCustomerRecord(
                brand_name=item.get("brand_name") or "",
                website_url=item.get("website_url"),
                merchant_handle=item.get("merchant_handle"),
                restricted_aliases=item.get("restricted_aliases") or split_restricted_aliases(item.get("brand_name")),
                source_sheet=item.get("source_sheet"),
                source_row=item.get("source_row"),
            )
        )
    return GovernanceIndex(records)


def _assert_denylist_final_gate(plan: dict, apply_dir: Path, governance_index: GovernanceIndex) -> None:
    for entry in plan.get("entries", []):
        if entry.get("action") not in {"will_add", "will_update"}:
            continue
        source = _safe_apply_path(apply_dir, entry["preview_path"])
        _assert_text_not_restricted(source.read_text(encoding="utf-8"), governance_index, "denylist final gate")


def _assert_text_not_restricted(text: str, governance_index: GovernanceIndex, context: str) -> None:
    if governance_index.check_text(text).blocked:
        raise ObsidianSyncError(f"{context} rejected a restricted source")


def _assert_written_checksum(path: Path, expected_checksum: str) -> None:
    content = path.read_text(encoding="utf-8")
    metadata, _ = parse_markdown_with_frontmatter(content)
    stored_checksum = metadata.get("content_checksum")
    actual_checksum = _content_checksum(content)
    if stored_checksum != expected_checksum:
        raise ObsidianSyncError(f"checksum self-check failed for {path.name}: stored checksum mismatch")
    if actual_checksum != expected_checksum:
        raise ObsidianSyncError(f"checksum self-check failed for {path.name}: content checksum mismatch")


def _backup_namespace(namespace_path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=False)
    if namespace_path.exists():
        shutil.copytree(namespace_path, backup_dir / "namespace")
    else:
        (backup_dir / "namespace_missing").write_text("true\n", encoding="utf-8")


def _restore_namespace(namespace_path: Path, backup_dir: Path) -> None:
    backup_namespace = backup_dir / "namespace"
    if namespace_path.exists():
        shutil.rmtree(namespace_path)
    namespace_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_namespace.exists():
        shutil.copytree(backup_namespace, namespace_path)


def _snapshot_outside_namespace(vault_path: Path, namespace_path: Path) -> Dict[str, Tuple[str, int]]:
    snapshot = {}
    if not vault_path.exists():
        return snapshot
    for path in sorted(vault_path.rglob("*")):
        if path.name.startswith("._") or not path.is_file():
            continue
        try:
            path.relative_to(namespace_path)
            continue
        except ValueError:
            pass
        snapshot[path.relative_to(vault_path).as_posix()] = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    return snapshot


def _assert_namespace_outside_unchanged(
    vault_path: Path, namespace_path: Path, before: Dict[str, Tuple[str, int]]
) -> None:
    after = _snapshot_outside_namespace(vault_path, namespace_path)
    if after != before:
        raise ObsidianSyncError("namespace 外的 vault 檔案發生變更")


def _assert_namespace_denylist_clean(namespace_path: Path, governance_index: GovernanceIndex) -> None:
    for path in _preview_text_files(namespace_path):
        metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        if metadata.get("managed_by") == MANAGED_BY:
            _assert_text_not_restricted(path.read_text(encoding="utf-8"), governance_index, "post-execution denylist scan")


def _assert_manifest_conservation(plan: dict, actions: List[dict]) -> None:
    actual = Counter(action["action"] for action in actions)
    for action in ("will_add", "will_update", "will_archive"):
        if actual[action] != plan.get("counts", {}).get(action, 0):
            raise ObsidianSyncError(f"manifest conservation failed for {action}")


def _append_audit_log(path: Path, manifest: dict, plan_path: Optional[Path], action: str = "sync") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(["timestamp", "batch_id", "action", "add", "update", "archive", "operator", "plan_path"])
        counts = Counter(item.get("action") for item in manifest.get("actions", []))
        writer.writerow(
            [
                _utc_now(),
                manifest.get("batch_id"),
                action,
                counts["will_add"],
                counts["will_update"],
                counts["will_archive"],
                os.environ.get("USER", "unknown"),
                str(plan_path or ""),
            ]
        )


def _render_plan(plan: dict) -> str:
    lines = [
        "# Obsidian Sync Plan",
        "",
        "本檔為唯讀 plan。execute 必須明確提供 `--confirm`，且會重新驗證 plan_state_hash。",
        "任何 conflict 預設都會阻止執行；人工確認後才可使用 `--allow-conflicts-skip` 跳過 conflict。",
        "",
        "## Scope",
        "",
        f"- Vault: `{plan['vault']}`",
        f"- Namespace: `{plan['namespace']}`",
        f"- Plan state hash: `{plan['plan_state_hash']}`",
        "- Archive 行為只會搬移到 namespace/_archived，不會刪除檔案。",
        "",
        "## Counts",
        "",
    ]
    for action, count in plan["counts"].items():
        lines.append(f"- `{action}`: {count}")
    lines.extend(["", "## Files", "", "| action | vault path | source sheet | source row |", "| --- | --- | --- | ---: |"])
    for entry in plan.get("entries", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    entry.get("action", ""),
                    entry.get("vault_path", ""),
                    _md_cell(entry.get("source_sheet")),
                    _md_cell(entry.get("source_row")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Execute", "", "- 先人工確認本 plan，再執行 `mka sync-obsidian execute --plan <plan.json> --confirm`。", "- execute 會先備份 namespace；任何失敗都會自動還原。", ""])
    return "\n".join(lines)


def _namespace_path(vault_path: Path, namespace: str) -> Path:
    vault_path = Path(vault_path).resolve()
    namespace_value = str(namespace).strip()
    if not namespace_value:
        raise ObsidianSyncError("namespace must not be empty")
    candidate = (vault_path / namespace_value).resolve()
    _assert_inside(candidate, vault_path, "namespace")
    return candidate


def _safe_apply_path(apply_dir: Path, relative_path: str) -> Path:
    candidate = (apply_dir / relative_path).resolve()
    _assert_inside(candidate, apply_dir, "apply preview path")
    if not candidate.is_file():
        raise ObsidianSyncError(f"apply preview file not found: {candidate}")
    return candidate


def _safe_namespace_path(namespace_path: Path, relative_path: str) -> Path:
    candidate = (namespace_path / relative_path).resolve()
    _assert_inside(candidate, namespace_path, "namespace write path")
    return candidate


def _assert_output_outside_vault(output_dir: Path, vault_path: Path) -> None:
    output_dir = Path(output_dir).resolve()
    vault_path = Path(vault_path).resolve()
    try:
        output_dir.relative_to(vault_path)
    except ValueError:
        return
    raise ObsidianSyncError("sync output must be outside the Obsidian vault")


def _assert_inside(path: Path, parent: Path, label: str) -> None:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise ObsidianSyncError(f"{label} escapes its allowed root") from exc


def _preview_text_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return [path for path in sorted(root.rglob("*")) if path.is_file() and not path.name.startswith("._") and path.suffix in {".md", ".json"}]


def _atomic_write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return path


def _write_json(path: Path, payload: dict) -> Path:
    return _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ObsidianSyncError(f"invalid sync plan: {path}") from exc
    if not isinstance(payload, dict):
        raise ObsidianSyncError(f"invalid sync plan object: {path}")
    return payload


def _execution_batch_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _md_cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")
