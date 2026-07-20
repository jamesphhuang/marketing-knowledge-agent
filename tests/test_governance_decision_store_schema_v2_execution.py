import json
import sqlite3
from pathlib import Path

import pytest

from marketing_knowledge_agent.governance_decision_store_schema_v2_execution import (
    CONFIRMATION_ID,
    CONFIRMATION_ROOT_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    EXPECTED_SCHEMA_HASH,
    GovernanceDecisionStoreSchemaV2ExecutionError,
    _is_git_ignored,
    execute_governance_decision_store_schema_v2_plan,
    validate_governance_decision_store_schema_v2_execution_bundle,
)
from marketing_knowledge_agent.cli import main


EXECUTED_AT = "2026-07-20T20:00:00+08:00"


def test_nonexistent_formal_governance_paths_are_recognized_as_git_ignored():
    root = Path(__file__).resolve().parents[1]
    assert _is_git_ignored(root, root / "data/governance/not-yet-created.sqlite") is True


@pytest.mark.parametrize(("field", "value", "message"), [
    ("plan_id", "latest", "PLAN_ID"),
    ("manifest_hash", "0" * 64, "Manifest Hash"),
    ("schema_hash", "0" * 64, "Schema Hash"),
    ("confirmation_id", "latest", "Confirmation ID"),
    ("confirmation_root_hash", "0" * 64, "Confirmation Root Hash"),
])
def test_exact_v2_execute_authority_is_required(tmp_path, field, value, message):
    args = _args(tmp_path)
    args[field] = value
    with pytest.raises(GovernanceDecisionStoreSchemaV2ExecutionError, match=message):
        execute_governance_decision_store_schema_v2_plan(**args)
    assert not args["formal_target_path"].exists()


@pytest.mark.parametrize("plan_id", [
    "decision-store-plan-a02502d8361549b1",
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
])
def test_obsolete_plans_are_rejected(tmp_path, plan_id):
    args = _args(tmp_path)
    args["plan_id"] = plan_id
    with pytest.raises(GovernanceDecisionStoreSchemaV2ExecutionError, match="obsolete"):
        execute_governance_decision_store_schema_v2_plan(**args)


def test_expired_plan_and_old_confirmation_are_rejected(tmp_path):
    args = _args(tmp_path)
    args["executed_at"] = "2026-07-27T15:29:03+08:00"
    with pytest.raises(GovernanceDecisionStoreSchemaV2ExecutionError, match="expired"):
        execute_governance_decision_store_schema_v2_plan(**args)

    args = _args(tmp_path / "old-confirmation")
    args["confirmation_id"] = "decision-store-confirmation-98fef43f8dd6773a"
    with pytest.raises(GovernanceDecisionStoreSchemaV2ExecutionError, match="old Confirmation"):
        execute_governance_decision_store_schema_v2_plan(**args)


def test_schema_v2_execute_creates_valid_store_and_execution_bundle(tmp_path):
    args = _args(tmp_path)
    result = execute_governance_decision_store_schema_v2_plan(**args)
    bundle = validate_governance_decision_store_schema_v2_execution_bundle(
        args["execution_bundle_path"], args["formal_target_path"]
    )

    assert result["conclusion"] == "A. Schema V2 Decision Store created and validated"
    assert result["event_count"] == 162
    assert result["current_parent_state_count"] == 120
    assert result["authority_gap"] == 0
    assert result["schema_version"] == 2
    assert result["schema_hash"] == EXPECTED_SCHEMA_HASH
    assert result["database_sha256"] == bundle["database_sha256"]
    assert result["execution_root_hash"] == bundle["root_execution_hash"]
    assert bundle["protected_file_count"] == 7
    assert bundle["physical_file_count"] == 8
    assert len([p for p in args["report_dir"].iterdir() if p.is_file()]) == 23
    assert not list(args["formal_target_path"].parent.glob(".governance_decisions.sqlite.staging-*"))
    assert not list(args["execution_bundle_path"].parent.glob(f".{args['execution_bundle_path'].name}.staging-*"))

    connection = sqlite3.connect(f"file:{args['formal_target_path']}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162
        assert connection.execute("SELECT COUNT(*) FROM current_parent_decisions").fetchone()[0] == 120
        assert connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE source_confirmation_id=? "
            "AND source_confirmation_root_hash=?",
            (CONFIRMATION_ID, CONFIRMATION_ROOT_HASH),
        ).fetchone()[0] == 162
        schema = connection.execute("SELECT * FROM schema_metadata").fetchone()
        assert schema["schema_version"] == 2
        assert schema["schema_hash"] == EXPECTED_SCHEMA_HASH
        execution = connection.execute("SELECT * FROM execution_metadata").fetchone()
        assert execution["confirmation_id"] == CONFIRMATION_ID
        assert execution["status"] == "completed"
        assert execution["database_sha256"] is None
        assert execution["execution_manifest_hash"]
    finally:
        connection.close()


def test_special_decisions_and_non_parent_boundaries_are_preserved(tmp_path):
    args = _args(tmp_path)
    execute_governance_decision_store_schema_v2_plan(**args)
    connection = sqlite3.connect(f"file:{args['formal_target_path']}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        parents = {
            row["record_id"]: json.loads(row["new_value_json"])["review_decision"]
            for row in connection.execute(
                "SELECT record_id,new_value_json FROM current_parent_decisions "
                "WHERE record_id IN (?,?,?,?,?)",
                (
                    "商家夥伴案例資料庫:r30", "商家夥伴案例資料庫:r12",
                    "商家夥伴案例資料庫:r122", "商家夥伴案例資料庫:r32",
                    "商家夥伴案例資料庫:r7",
                ),
            )
        }
        assert parents == {
            "商家夥伴案例資料庫:r30": "exclude",
            "商家夥伴案例資料庫:r12": "approve_internal_only",
            "商家夥伴案例資料庫:r122": "approve",
            "商家夥伴案例資料庫:r32": "approve",
            "商家夥伴案例資料庫:r7": "approve",
        }
        asset_rows = [json.loads(row[0]) for row in connection.execute(
            "SELECT new_value_json FROM current_asset_eligibility"
        )]
        assert sum(row["asset_index_eligibility"] == "include" for row in asset_rows) == 8
        assert sum(row["asset_index_eligibility"] == "hold" for row in asset_rows) == 1
        assert sum(row["asset_index_eligibility"] == "exclude" for row in asset_rows) == 1
        r12_video = json.loads(connection.execute(
            "SELECT new_value_json FROM current_asset_eligibility WHERE asset_id=?",
            ("商家夥伴案例資料庫:r12:video",),
        ).fetchone()[0])
        assert r12_video == {
            "asset_index_eligibility": "hold",
            "asset_search_eligibility": "not_searchable",
        }
        aliases = {
            json.loads(row[0])["alias"] for row in connection.execute(
                "SELECT new_value_json FROM current_search_aliases WHERE record_id=?",
                ("商家夥伴案例資料庫:r32",),
            )
        }
        assert aliases == {"SLP", "SHOPLINE Payments"}
        entities = {
            row["record_id"]: json.loads(row["new_value_json"])["entity_type"]
            for row in connection.execute("SELECT record_id,new_value_json FROM current_entity_metadata")
        }
        assert entities == {
            "商家夥伴案例資料庫:r7": "partner",
            "商家夥伴案例資料庫:r122": "partner",
        }
        reference = json.loads(connection.execute(
            "SELECT new_value_json FROM decision_events WHERE event_type='asset_url_manifest_reference'"
        ).fetchone()[0])
        assert reference["approved_url_field_count"] == 410
        assert "url_values" not in reference
    finally:
        connection.close()


def test_append_only_hash_chain_and_existing_target_rejection(tmp_path):
    args = _args(tmp_path)
    result = execute_governance_decision_store_schema_v2_plan(**args)
    assert result["append_only_updates_blocked"] is True
    assert result["append_only_deletes_blocked"] is True
    assert result["hash_chain_valid"] is True

    with pytest.raises(GovernanceDecisionStoreSchemaV2ExecutionError, match="formal target"):
        execute_governance_decision_store_schema_v2_plan(**args)


def test_cli_requires_all_exact_authority_values(monkeypatch, capsys):
    captured = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"conclusion": "A. Schema V2 Decision Store created and validated"}

    monkeypatch.setattr(
        "marketing_knowledge_agent.cli.execute_governance_decision_store_schema_v2_plan",
        fake_execute,
    )
    assert main([
        "execute-governance-decision-store-schema-v2-plan",
        "--plan-id", EXPECTED_PLAN_ID,
        "--plan-manifest-hash", EXPECTED_MANIFEST_HASH,
        "--schema-hash", EXPECTED_SCHEMA_HASH,
        "--confirmation-id", CONFIRMATION_ID,
        "--confirmation-root-hash", CONFIRMATION_ROOT_HASH,
        "--executed-at", EXECUTED_AT,
    ]) == 0
    assert json.loads(capsys.readouterr().out)["conclusion"].startswith("A.")
    assert captured["plan_id"] == EXPECTED_PLAN_ID
    assert captured["confirmation_id"] == CONFIRMATION_ID


def _args(tmp_path):
    root = Path(__file__).resolve().parents[1]
    return {
        "repo_root": root,
        "plan_id": EXPECTED_PLAN_ID,
        "manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "confirmation_id": CONFIRMATION_ID,
        "confirmation_root_hash": CONFIRMATION_ROOT_HASH,
        "formal_target_path": tmp_path / "governance_decisions.sqlite",
        "execution_bundle_path": tmp_path / "executions" / EXPECTED_PLAN_ID,
        "report_dir": tmp_path / "reports",
        "temporary_root": tmp_path / "temporary-validation",
        "executed_at": EXECUTED_AT,
        "_allow_test_paths": True,
    }
