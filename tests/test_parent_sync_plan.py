import hashlib
import json
from pathlib import Path

import pytest

import marketing_knowledge_agent.cli as cli
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.parent_sync_plan import (
    EXPECTED_DATABASE_SHA256,
    ParentSyncPlanError,
    _detect_path_collisions,
    generate_parent_sync_plan,
)
from marketing_knowledge_agent.store_data_sync_existing_validation import (
    reconstruct_pre_sync_fixture,
)


CREATED_AT = "2026-07-20T20:00:00+08:00"


def test_full_authoritative_projection_reconciles_all_120_parents(tmp_path):
    result = generate_parent_sync_plan(**_args(tmp_path))

    assert result["conclusion"] == "A. Ready for Parent Sync Plan confirmation"
    assert result["authoritative_parent_count"] == 120
    assert result["reconciliation_row_count"] == 120
    assert result["unique_record_id_count"] == 120
    assert result["authority_gap"] == 0
    assert result["missing_authoritative_parent_count"] == 0
    assert result["duplicate_authoritative_parent_count"] == 0
    assert result["action_counts"] == {
        "create": 4,
        "update": 106,
        "no_change": 0,
        "remove_from_content_projection": 0,
        "retain_governance_only": 10,
        "blocked": 0,
        "manual_review": 0,
    }


def test_decision_store_and_execution_authority_are_exact(tmp_path):
    result = generate_parent_sync_plan(**_args(tmp_path))
    validation = result["decision_store_validation"]

    assert validation["database_sha256_before"] == EXPECTED_DATABASE_SHA256
    assert validation["database_sha256_after"] == EXPECTED_DATABASE_SHA256
    assert validation["database_size_before"] == 385024
    assert validation["database_size_after"] == 385024
    assert validation["integrity_check"] == "ok"
    assert validation["foreign_key_errors"] == 0
    assert validation["hash_chain_valid"] is True
    assert validation["execution_root_hash_valid"] is True
    assert validation["opened_read_only"] is True


def test_inventory_is_recalculated_from_actual_targets(tmp_path):
    inventory = generate_parent_sync_plan(**_args(tmp_path))["inventory"]

    assert inventory["decision_store_parent_count"] == 120
    assert inventory["managed_vault_parent_count"] == 106
    assert inventory["formal_vault_parent_count"] == 106
    assert inventory["formal_sqlite_parent_count"] == 105
    assert inventory["orphan_parent_count"] == 0
    assert inventory["duplicate_record_id_count"] == 0
    assert inventory["missing_parent_count"] == 14
    assert inventory["stale_parent_count"] == 106
    assert inventory["extra_non_authoritative_parent_count"] == 0


def test_delta_only_write_plan_excludes_no_change_and_governance_only(tmp_path):
    result = generate_parent_sync_plan(**_args(tmp_path))

    assert len(result["write_manifest_records"]) == 110
    assert {row["proposed_action"] for row in result["write_manifest_records"]} == {
        "create", "update"
    }
    assert not any(row["proposed_action"] == "no_change" for row in result["write_manifest_records"])
    assert not any(
        row["proposed_action"] == "retain_governance_only"
        for row in result["write_manifest_records"]
    )


def test_special_parent_actions_and_governance_are_preserved(tmp_path):
    result = generate_parent_sync_plan(**_args(tmp_path))
    rows = {row["record_id"]: row for row in result["reconciliation_rows"]}

    for record_id in ("r7", "r12", "r32", "r122"):
        row = rows[f"商家夥伴案例資料庫:{record_id}"]
        assert row["proposed_action"] == "create"
        assert row["sync_eligible"] is True
    r30 = rows["商家夥伴案例資料庫:r30"]
    assert r30["proposed_action"] == "retain_governance_only"
    assert r30["sync_eligible"] is False
    assert r30["desired_projection_status"] == "excluded"

    special = result["special_parent_validation"]
    assert all(row["status"] == "pass" for row in special)


def test_partner_alias_and_asset_boundaries_are_preserved(tmp_path):
    result = generate_parent_sync_plan(**_args(tmp_path))
    desired = {row["record_id"]: row for row in result["authoritative_projection"]}

    assert desired["商家夥伴案例資料庫:r122"]["normalized_entity_type"] == "partner"
    assert desired["商家夥伴案例資料庫:r122"]["merchant_handle"] == ""
    assert desired["商家夥伴案例資料庫:r7"]["merchant_handle_requirement"] == "not_required"
    assert desired["商家夥伴案例資料庫:r32"]["search_aliases"] == [
        "SLP", "SHOPLINE Payments"
    ]
    assert result["asset_boundary"] == {
        "eligible_assets": 205,
        "hold_assets": 1,
        "excluded_or_blocked_assets": 16,
        "approved_url_fields": 410,
        "asset_identity_count": 222,
        "new_asset_identities": 0,
        "lost_asset_identities": 0,
        "url_values_copied_to_parent": 0,
        "parent_tags_copied_to_assets": 0,
        "parent_approval_overrode_hold": False,
    }


def test_candidate_projection_and_offline_search_behaviors(tmp_path):
    result = generate_parent_sync_plan(**_args(tmp_path))
    candidate = result["candidate_validation"]
    search = result["offline_search_preview"]

    assert candidate["parent_rows"] == 120
    assert candidate["content_parent_rows"] == 109
    assert candidate["asset_rows"] == 222
    assert candidate["searchable_asset_rows"] == 205
    assert candidate["orphan_parent_count"] == 0
    assert candidate["duplicate_parent_count"] == 0
    assert candidate["restricted_leakage"] == 0
    assert candidate["pending_leakage"] == 0
    assert candidate["read_only_reopen"] is True

    assert search["莉朵花藝"]["asset_count"] == 0
    assert search["littlegirl"]["citation_count"] == 0
    assert search["廣生堂"]["asset_types"] == ["article"]
    assert search["廣生堂"]["can_external_reference"] is False
    assert search["111gsttest"]["asset_types"] == ["article"]
    assert search["Package+"]["asset_types"] == ["article", "podcast", "video"]
    assert search["SLP"]["record_ids"] == ["商家夥伴案例資料庫:r32"]
    assert search["SHOPLINE Payments"]["record_count"] == 16
    assert search["SHOPLINE Payments"]["asset_count"] == 32
    assert search["聊心茶室"]["asset_types"] == ["article", "podcast", "video"]
    assert search["關貿網路"]["asset_types"] == ["article"]


def test_path_plan_is_stable_and_detects_collisions(tmp_path):
    result = generate_parent_sync_plan(**_args(tmp_path))

    assert result["path_collision_count"] == 0
    assert _detect_path_collisions([
        {"record_id": "Sheet:r1", "proposed_path": "same.md"},
        {"record_id": "Sheet:r2", "proposed_path": "same.md"},
    ]) == ["same.md"]


def test_hashes_plan_id_and_reports_are_deterministic(tmp_path):
    args = _args(tmp_path)
    first = generate_parent_sync_plan(**args)
    first_report_hash = _directory_hash(args["output_dir"])
    second = generate_parent_sync_plan(**args)

    assert first["desired_projection_hash"] == second["desired_projection_hash"]
    assert first["delta_manifest_hash"] == second["delta_manifest_hash"]
    assert first["plan_id"] == second["plan_id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert _directory_hash(args["output_dir"]) == first_report_hash
    assert len([path for path in args["output_dir"].iterdir() if path.is_file()]) == 26


def test_formal_systems_remain_unchanged(tmp_path):
    root = _root()
    protected = [
        root / "data/governance/governance_decisions.sqlite",
        root / "data/governance/executions/decision-store-schema-v2-plan-2aab43cd463170f2",
        root / "obsidian_vault",
        root / ".mka/content_index.sqlite",
        root / "src/marketing_knowledge_agent/slack_interface.py",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    result = generate_parent_sync_plan(**_args(tmp_path))

    assert {str(path): _hash_path(path) for path in protected} == before
    assert result["formal_systems_unchanged"] is True
    assert result["execution_blocked"] is False


def test_wrong_decision_store_sha_fails_closed(tmp_path):
    database = tmp_path / "wrong.sqlite"
    database.write_bytes(b"not sqlite")
    args = _args(tmp_path)
    args["decision_store_path"] = database

    with pytest.raises(ParentSyncPlanError, match="SHA-256"):
        generate_parent_sync_plan(**args)


def test_cli_is_preview_only_and_needs_no_slack_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")
    original = cli.generate_parent_sync_plan

    def generate_from_prestate(**kwargs):
        kwargs["managed_vault_root"] = Path(fixture["managed_vault_root"])
        kwargs["formal_sqlite_path"] = Path(fixture["formal_sqlite_path"])
        return original(**kwargs)

    monkeypatch.setattr(cli, "generate_parent_sync_plan", generate_from_prestate)

    assert main([
        "plan-parent-sync",
        "--output", str(tmp_path / "reports"),
        "--temporary-root", str(tmp_path / "temporary"),
        "--created-at", CREATED_AT,
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_id"].startswith("parent-sync-plan-")
    assert payload["execution_blocked"] is False
    assert payload["formal_data_modified"] is False


def _args(tmp_path):
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")
    return {
        "repo_root": _root(),
        "output_dir": tmp_path / "reports",
        "temporary_root": tmp_path / "temporary",
        "created_at": CREATED_AT,
        "managed_vault_root": Path(fixture["managed_vault_root"]),
        "formal_sqlite_path": Path(fixture["formal_sqlite_path"]),
    }


def _root():
    return Path(__file__).resolve().parents[1]


def _hash_path(path):
    path = Path(path)
    digest = hashlib.sha256()
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _directory_hash(path):
    return _hash_path(path)
