import csv
import json
import sqlite3
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance_decision_store_plan import DECISION_STORE_SCHEMA
from marketing_knowledge_agent.governance_decision_store_schema_v2_plan import (
    CANONICAL_SCHEMA_V2_SQL,
    CONFIRMATION_BINDING_PLACEHOLDER,
    SCHEMA_VERSION,
    GovernanceDecisionStoreSchemaV2PlanError,
    bind_event_templates,
    generate_governance_decision_store_schema_v2_plan,
    schema_v2_hashes,
)


CREATED_AT = "2026-07-20T10:00:00+08:00"


def test_v1_is_execute_incompatible_and_v2_adds_complete_contract():
    assert "source_confirmation_id" not in DECISION_STORE_SCHEMA
    assert "schema_metadata" not in DECISION_STORE_SCHEMA
    assert "source_confirmation_id TEXT NOT NULL" in CANONICAL_SCHEMA_V2_SQL
    assert "source_confirmation_root_hash TEXT NOT NULL" in CANONICAL_SCHEMA_V2_SQL
    assert "CREATE TABLE schema_metadata" in CANONICAL_SCHEMA_V2_SQL
    assert "CREATE TABLE execution_metadata" in CANONICAL_SCHEMA_V2_SQL
    assert "schema_metadata_no_update" in CANONICAL_SCHEMA_V2_SQL
    assert "execution_metadata_no_delete" in CANONICAL_SCHEMA_V2_SQL
    assert SCHEMA_VERSION == 2


def test_schema_hash_and_canonical_sql_are_deterministic():
    first = schema_v2_hashes()
    second = schema_v2_hashes()
    assert first == second
    assert len(first["schema_hash"]) == 64
    assert len(first["canonical_schema_sql_hash"]) == 64


def test_confirmation_binding_changes_event_identity_without_changing_schema_or_templates(tmp_path):
    result = generate_governance_decision_store_schema_v2_plan(**_args(tmp_path / "plan"))
    templates = result["event_templates"]
    root_a = "a" * 64
    root_b = "b" * 64

    bound_a = bind_event_templates(templates, "confirmation-a", root_a)
    bound_b = bind_event_templates(templates, "confirmation-b", root_b)

    assert bound_a[0]["event_id"] != bound_b[0]["event_id"]
    assert bound_a[0]["idempotency_key"] != bound_b[0]["idempotency_key"]
    assert result["confirmation_binding_placeholder"] == CONFIRMATION_BINDING_PLACEHOLDER
    assert result["schema_hash"] == schema_v2_hashes()["schema_hash"]


@pytest.mark.parametrize(("confirmation_id", "root_hash"), [("", "a" * 64), ("x", "bad")])
def test_invalid_confirmation_binding_fails_closed(confirmation_id, root_hash):
    with pytest.raises(GovernanceDecisionStoreSchemaV2PlanError):
        bind_event_templates([], confirmation_id, root_hash)


def test_real_schema_v2_plan_is_ready_and_conserves_state(tmp_path):
    result = generate_governance_decision_store_schema_v2_plan(**_args(tmp_path))

    assert result["conclusion"] == "A. Ready for Schema V2 Plan confirmation"
    assert result["schema_version"] == 2
    assert result["execution_blocked"] is False
    assert result["event_count"] == 162
    assert result["current_parent_state_count"] == 120
    assert result["authority_gap"] == 0
    assert (result["eligible_asset_count"], result["hold_asset_count"], result["excluded_asset_count"]) == (205, 1, 16)
    assert result["approved_url_field_count"] == 410
    assert result["formal_data_modified"] is False
    assert result["plan_id"] != "decision-store-plan-a02502d8361549b1"
    assert len(_files(_args(tmp_path)["output_dir"])) == 21


def test_temporary_schema_v2_database_passes_complete_validation(tmp_path):
    result = generate_governance_decision_store_schema_v2_plan(**_args(tmp_path))
    validation = result["temporary_validation"]

    assert validation["decision_event_confirmation_columns"] is True
    assert validation["schema_metadata_exists"] is True
    assert validation["execution_metadata_exists"] is True
    assert validation["integrity_check"] == "ok"
    assert validation["foreign_key_errors"] == 0
    assert validation["decision_events_update_blocked"] is True
    assert validation["decision_events_delete_blocked"] is True
    assert validation["schema_metadata_update_blocked"] is True
    assert validation["schema_metadata_delete_blocked"] is True
    assert validation["execution_metadata_update_blocked"] is True
    assert validation["execution_metadata_delete_blocked"] is True
    assert validation["hash_chain_valid"] is True
    assert validation["tamper_detected"] is True
    assert validation["read_only_reopen"] is True
    assert validation["transaction_rollback"] is True
    assert validation["backup_restore"] is True
    assert validation["database_sha_self_reference_avoided"] is True
    assert validation["temporary_database_checksum_deterministic"] is True


def test_plan_id_excludes_future_confirmation_and_changes_with_schema_hash(tmp_path):
    first_args = _args(tmp_path / "first")
    second_args = _args(tmp_path / "second")
    second_args["target_path"] = first_args["target_path"]
    first = generate_governance_decision_store_schema_v2_plan(**first_args)
    second = generate_governance_decision_store_schema_v2_plan(**second_args)
    manifest = json.loads((first_args["output_dir"] / "schema_v2_plan_manifest.json").read_text())

    assert first["plan_id"] == second["plan_id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert "confirmation_id" not in manifest["plan_identity_inputs"]
    assert "confirmation_root_hash" not in manifest["plan_identity_inputs"]
    assert manifest["confirmation_binding_contract"]["binding_stage"] == "execute"
    assert manifest["schema_hash"] in manifest["plan_identity_inputs"].values()


def test_execution_metadata_avoids_physical_database_hash_self_reference(tmp_path):
    result = generate_governance_decision_store_schema_v2_plan(**_args(tmp_path))
    database = Path(result["temporary_validation"]["retained_database_path"])
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT database_sha256, execution_manifest_hash, status FROM execution_metadata"
        ).fetchone()
    finally:
        connection.close()
    assert row[0] is None
    assert row[1] == "temporary-validation-external-manifest"
    assert row[2] == "validated"


def test_old_plan_and_confirmation_are_superseded_not_modified(tmp_path):
    args = _args(tmp_path)
    old_plan = args["old_plan_manifest_path"].read_bytes()
    old_confirmation = _hash_tree(args["old_confirmation_path"])
    generate_governance_decision_store_schema_v2_plan(**args)
    rows = _read_csv(args["output_dir"] / "obsolete_plan_confirmation_registry.csv")

    old = next(row for row in rows if row["plan_id"] == "decision-store-plan-a02502d8361549b1")
    assert old["execution_compatible"] == "false"
    assert old["do_not_execute"] == "true"
    assert old["confirmation_id"] == "decision-store-confirmation-98fef43f8dd6773a"
    assert args["old_plan_manifest_path"].read_bytes() == old_plan
    assert _hash_tree(args["old_confirmation_path"]) == old_confirmation


def test_cli_is_preview_only_and_requires_no_slack_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    output = tmp_path / "output"
    target = tmp_path / "formal.sqlite"

    assert main([
        "plan-governance-decision-store-schema-v2",
        "--output", str(output),
        "--target", str(target),
        "--created-at", CREATED_AT,
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_blocked"] is False
    assert not target.exists()


def _args(tmp_path):
    root = Path(__file__).resolve().parents[1]
    return {
        "repo_root": root,
        "bundle_path": root / "data/governance/imports/parent-authority-approval-20260719",
        "old_plan_manifest_path": root / "reports/governance_decision_store_regenerated_plan/regenerated_decision_store_manifest.json",
        "old_confirmation_path": root / "data/governance/confirmations/decision-store-plan-a02502d8361549b1",
        "execute_reports_path": root / "reports/governance_decision_store_execution",
        "legacy_decisions_path": root / "reports/excel_preview/review_decisions_template.csv",
        "merchant_cases_path": root / "reports/excel_preview/merchant_cases.json",
        "asset_url_decisions_path": root / "reports/asset_metadata_preview/human_review_template.csv",
        "asset_url_validation_path": root / "reports/asset_metadata_review_validation/review_decision_status.csv",
        "asset_apply_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview.csv",
        "asset_blocked_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv",
        "formal_vault_path": root / "obsidian_vault",
        "formal_db_path": root / ".mka/content_index.sqlite",
        "production_renderer_path": root / "src/marketing_knowledge_agent/slack_interface.py",
        "target_path": tmp_path / "formal-governance.sqlite",
        "output_dir": tmp_path / "reports",
        "temporary_dir": tmp_path / "temporary",
        "created_at": CREATED_AT,
        "source_branch": "test-branch",
        "source_commit": "test-commit",
    }


def _files(path):
    return [item for item in Path(path).iterdir() if item.is_file() and not item.name.startswith("._")]


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _hash_tree(path):
    import hashlib

    digest = hashlib.sha256()
    for child in sorted(item for item in Path(path).rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()
