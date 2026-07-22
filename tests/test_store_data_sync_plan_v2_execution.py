from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from marketing_knowledge_agent.frontmatter import parse_markdown_with_frontmatter
from marketing_knowledge_agent.store_data_sync_plan_v2_execution import (
    EXPECTED_CONFIRMATION_ID,
    EXPECTED_CONFIRMATION_ROOT_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    REPORT_FILENAMES,
    StoreDataSyncPlanV2ExecutionError,
    execute_store_data_sync_plan_v2,
    validate_store_data_sync_execution_bundle,
)


EXECUTED_AT = "2026-07-22T15:00:00+08:00"
AUDIT_ONLY = {
    "decision_event_id",
    "decision_event_hash",
    "decision_reviewer",
    "decision_reviewed_at",
    "decision_provenance",
}
CREATE_ROWS = {7, 12, 32, 122}


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _parent_files(root: Path) -> dict[int, Path]:
    result = {}
    for path in root.rglob("*.md"):
        if path.name.startswith("._") or "_archived" in path.parts:
            continue
        metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        if metadata.get("record_type") == "merchant_case":
            result[int(str(metadata["source_row"]).removeprefix("r"))] = path
    return result


def _parent_rows(path: Path) -> dict[int, tuple]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case'"
        ).fetchall()
    return {
        int(json.loads(row["metadata_json"])["source_row"]): tuple(row)
        for row in rows
    }


def _fixture(tmp_path: Path) -> dict:
    managed = tmp_path / "obsidian_vault" / "MKA"
    managed.parent.mkdir(parents=True)
    shutil.copytree(_root() / "obsidian_vault" / "MKA", managed)
    formal = tmp_path / ".mka" / "content_index.sqlite"
    formal.parent.mkdir(parents=True)
    shutil.copy2(_root() / ".mka" / "content_index.sqlite", formal)
    return {
        "managed_vault_root": managed,
        "formal_sqlite_path": formal,
        "backup_path": tmp_path / "data" / "backups" / EXPECTED_PLAN_ID,
        "execution_path": tmp_path / "data" / "executions" / EXPECTED_PLAN_ID,
        "report_dir": tmp_path / "reports",
        "temporary_root": tmp_path / "temporary",
        "require_git_ignored": False,
        "allow_noncanonical_test_targets": True,
    }


def _execute(tmp_path: Path, **overrides):
    arguments = _fixture(tmp_path)
    arguments.update(overrides)
    return execute_store_data_sync_plan_v2(
        repo_root=_root(),
        plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        confirmation_id=EXPECTED_CONFIRMATION_ID,
        confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
        executed_at=EXECUTED_AT,
        **arguments,
    ), arguments


def test_exact_authority_and_expiration_fail_closed(tmp_path):
    arguments = _fixture(tmp_path)
    common = dict(
        repo_root=_root(), manifest_hash=EXPECTED_MANIFEST_HASH,
        confirmation_id=EXPECTED_CONFIRMATION_ID,
        confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
        executed_at=EXECUTED_AT, **arguments,
    )
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="PLAN_ID"):
        execute_store_data_sync_plan_v2(plan_id="parent-sync-plan-23f9805386fb6a5d", **common)
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="Confirmation ID"):
        execute_store_data_sync_plan_v2(plan_id=EXPECTED_PLAN_ID, **{**common, "confirmation_id": "wrong"})
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="Manifest Hash"):
        execute_store_data_sync_plan_v2(plan_id=EXPECTED_PLAN_ID, **{**common, "manifest_hash": "0" * 64})
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="Confirmation Root Hash"):
        execute_store_data_sync_plan_v2(
            plan_id=EXPECTED_PLAN_ID, **{**common, "confirmation_root_hash": "0" * 64}
        )
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="expired"):
        execute_store_data_sync_plan_v2(
            plan_id=EXPECTED_PLAN_ID, **{**common, "executed_at": "2026-07-29T00:00:00+08:00"}
        )


def test_execute_applies_exact_vault_and_sqlite_delta(tmp_path):
    arguments = _fixture(tmp_path)
    before_files = _parent_files(arguments["managed_vault_root"])
    before_bodies = {
        row: parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))[1]
        for row, path in before_files.items()
    }
    before_unknown = {
        row: parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))[0].get("reviewer")
        for row, path in before_files.items()
    }
    before_rows = _parent_rows(arguments["formal_sqlite_path"])
    before_schema = _schema_hash(arguments["formal_sqlite_path"])

    result = execute_store_data_sync_plan_v2(
        repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        confirmation_id=EXPECTED_CONFIRMATION_ID,
        confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
        executed_at=EXECUTED_AT, **arguments,
    )

    after_files = _parent_files(arguments["managed_vault_root"])
    assert len(before_files) == 106
    assert len(after_files) == 110
    assert set(after_files) - set(before_files) == CREATE_ROWS
    for row in before_files:
        metadata, body = parse_markdown_with_frontmatter(after_files[row].read_text(encoding="utf-8"))
        assert body == before_bodies[row]
        assert metadata.get("reviewer") == before_unknown[row]
        assert not AUDIT_ONLY.intersection(metadata)
    for row in CREATE_ROWS:
        metadata, _ = parse_markdown_with_frontmatter(after_files[row].read_text(encoding="utf-8"))
        assert not AUDIT_ONLY.intersection(metadata)
        if row in {7, 122}:
            assert metadata["normalized_entity_type"] == "partner"
            assert metadata["merchant_handle_requirement"] == "not_required"
            assert metadata.get("merchant_handle") is None
    assert parse_markdown_with_frontmatter(after_files[32].read_text(encoding="utf-8"))[0]["search_aliases"] == [
        "SLP", "SHOPLINE Payments",
    ]

    after_rows = _parent_rows(arguments["formal_sqlite_path"])
    assert len(after_rows) == 109
    assert set(after_rows) - set(before_rows) == CREATE_ROWS
    assert all(after_rows[row] == before_rows[row] for row in before_rows)
    assert _schema_hash(arguments["formal_sqlite_path"]) == before_schema
    with sqlite3.connect(arguments["formal_sqlite_path"]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        created_metadata = [
            json.loads(row[0]) for row in connection.execute(
                "SELECT metadata_json FROM documents WHERE json_extract(metadata_json,'$.source_row') IN (7,12,32,122)"
            )
        ]
        assert all(not AUDIT_ONLY.intersection(item) for item in created_metadata)
        assert all("search_aliases" not in item for item in created_metadata)

    assert result["conclusion"].startswith("A.")
    assert result["managed_vault_counts"] == {"before": 106, "after": 110, "create": 4, "update": 106}
    assert result["formal_sqlite_counts"] == {
        "before": 105, "after": 109, "create": 4, "update": 0, "no_change": 105,
    }
    assert result["audit_only_write_occurrences"] == 0
    assert result["decision_store_unchanged"] is True
    assert result["production_search_alias_activated"] is False
    assert validate_store_data_sync_execution_bundle(arguments["execution_path"])["valid"] is True
    assert result["rollback_validation"] == {
        "valid": True, "managed_vault_restored": True, "formal_sqlite_restored": True,
    }
    assert len([path for path in arguments["report_dir"].iterdir() if path.name in REPORT_FILENAMES]) == len(REPORT_FILENAMES)
    backup = json.loads((arguments["backup_path"] / "backup_manifest.json").read_text(encoding="utf-8"))
    assert backup["root_backup_hash"]
    assert backup["managed_vault_update_count"] == 106
    assert backup["managed_vault_create_count"] == 4


def test_target_drift_and_existing_execution_rejected(tmp_path):
    arguments = _fixture(tmp_path)
    parent = next(iter(_parent_files(arguments["managed_vault_root"]).values()))
    parent.write_text(parent.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="preflight|changed|mismatch"):
        execute_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
            manifest_hash=EXPECTED_MANIFEST_HASH,
            confirmation_id=EXPECTED_CONFIRMATION_ID,
            confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
            executed_at=EXECUTED_AT, **arguments,
        )


def test_tampered_confirmation_and_decision_store_rejected(tmp_path):
    arguments = _fixture(tmp_path)
    copied_confirmation = tmp_path / "confirmation"
    shutil.copytree(
        _root() / "data" / "governance" / "confirmations" / EXPECTED_PLAN_ID,
        copied_confirmation,
    )
    copied_confirmation.chmod(0o755)
    payload = copied_confirmation / "confirmation.json"
    payload.chmod(0o644)
    payload.write_text(payload.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="Confirmation preflight"):
        execute_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
            manifest_hash=EXPECTED_MANIFEST_HASH,
            confirmation_id=EXPECTED_CONFIRMATION_ID,
            confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
            confirmation_path=copied_confirmation, executed_at=EXECUTED_AT, **arguments,
        )

    arguments = _fixture(tmp_path / "decision-store")
    decision_store = tmp_path / "tampered-governance.sqlite"
    shutil.copy2(_root() / "data" / "governance" / "governance_decisions.sqlite", decision_store)
    with decision_store.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="preflight"):
        execute_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
            manifest_hash=EXPECTED_MANIFEST_HASH,
            confirmation_id=EXPECTED_CONFIRMATION_ID,
            confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
            decision_store_path=decision_store, executed_at=EXECUTED_AT, **arguments,
        )

    arguments = _fixture(tmp_path / "second")
    arguments["execution_path"].mkdir(parents=True)
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="Execution Bundle"):
        execute_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
            manifest_hash=EXPECTED_MANIFEST_HASH,
            confirmation_id=EXPECTED_CONFIRMATION_ID,
            confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
            executed_at=EXECUTED_AT, **arguments,
        )


def test_execution_bundle_failure_rolls_back_both_targets(tmp_path, monkeypatch):
    import marketing_knowledge_agent.store_data_sync_plan_v2_execution as module

    arguments = _fixture(tmp_path)
    vault_before = _hash_tree(arguments["managed_vault_root"])
    sqlite_before = _sha256(arguments["formal_sqlite_path"])

    def fail_bundle(*args, **kwargs):
        raise StoreDataSyncPlanV2ExecutionError("injected execution bundle failure")

    monkeypatch.setattr(module, "_create_execution_bundle", fail_bundle)
    with pytest.raises(StoreDataSyncPlanV2ExecutionError, match="rolled back"):
        execute_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
            manifest_hash=EXPECTED_MANIFEST_HASH,
            confirmation_id=EXPECTED_CONFIRMATION_ID,
            confirmation_root_hash=EXPECTED_CONFIRMATION_ROOT_HASH,
            executed_at=EXECUTED_AT, **arguments,
        )
    assert _hash_tree(arguments["managed_vault_root"]) == vault_before
    assert _sha256(arguments["formal_sqlite_path"]) == sqlite_before
    assert not arguments["execution_path"].exists()


def _schema_hash(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
