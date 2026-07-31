from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import marketing_knowledge_agent.cli as cli
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.parent_sync_confirmation import (
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    ParentSyncConfirmationError,
    confirm_parent_sync_plan,
    validate_parent_sync_plan,
)
from marketing_knowledge_agent.store_data_sync_existing_validation import (
    reconstruct_pre_sync_fixture,
)


VALIDATED_AT = "2026-07-21T10:00:00+08:00"


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _hash_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _validate(tmp_path: Path) -> dict:
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")
    return validate_parent_sync_plan(
        repo_root=_root(),
        plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        temporary_root=tmp_path / "temporary",
        validated_at=VALIDATED_AT,
        managed_vault_root=Path(fixture["managed_vault_root"]),
        formal_sqlite_path=Path(fixture["formal_sqlite_path"]),
    )


def test_independent_recalculation_matches_counts_but_blocks_audit_only_delta(tmp_path):
    result = _validate(tmp_path)

    assert result["plan_identity_matches"] is True
    assert result["authoritative_parent_count"] == 120
    assert result["reconciliation_row_count"] == 120
    assert result["original_action_counts"] == {
        "create": 4,
        "update": 106,
        "no_change": 0,
        "remove_from_content_projection": 0,
        "retain_governance_only": 10,
        "blocked": 0,
        "manual_review": 0,
    }
    assert result["confirmation_allowed"] is False
    assert "audit_only_fields_materialized_by_plan" in result["blocker_reasons"]
    assert result["audit_only_fields"] == [
        "decision_event_hash",
        "decision_event_id",
        "decision_provenance",
        "decision_reviewed_at",
        "decision_reviewer",
    ]
    assert result["corrected_delta_manifest_hash"] != result["plan_delta_manifest_hash"]


def test_all_106_updates_have_projection_required_differences(tmp_path):
    result = _validate(tmp_path)
    updates = [row for row in result["reconciliation"] if row["proposed_action"] == "update"]

    assert len(updates) == 106
    assert all(row["necessary_projection_diff_count"] > 0 for row in updates)
    assert all(row["audit_only_diff_count"] == 5 for row in updates)
    assert result["corrected_action_counts"]["update"] == 106
    assert result["corrected_action_counts"]["no_change"] == 0


def test_governance_only_and_count_relationship_is_explicit(tmp_path):
    result = _validate(tmp_path)
    governance = result["governance_only_rows"]

    assert len(governance) == 10
    assert all(row["should_create_content_file"] is False for row in governance)
    assert all(row["should_enter_content_index"] is False for row in governance)
    assert all(row["desired_governance_storage_location"] == "decision_store_only" for row in governance)
    assert result["managed_vault_count_reconciliation"] == {
        "existing": 106,
        "creates": 4,
        "removals": 0,
        "expected": 110,
        "content_parents": 109,
        "vault_only_parents": 1,
        "decision_store_only_parents": 10,
    }


def test_formal_projection_and_four_creates_are_independently_verified(tmp_path):
    result = _validate(tmp_path)

    assert result["formal_sqlite_count_reconciliation"] == {
        "existing": 105,
        "creates": 4,
        "updates": 105,
        "removals": 0,
        "not_projected": 11,
        "expected": 109,
    }
    assert len(result["not_projected_rows"]) == 11
    assert {row["record_id"] for row in result["create_rows"]} == {
        "商家夥伴案例資料庫:r7",
        "商家夥伴案例資料庫:r12",
        "商家夥伴案例資料庫:r32",
        "商家夥伴案例資料庫:r122",
    }
    assert all(row["path_collision"] is False for row in result["create_rows"])


def test_candidate_asset_and_search_boundaries_are_rebuilt(tmp_path):
    result = _validate(tmp_path)

    assert result["candidate_validation"] == {
        "authoritative_parents": 120,
        "content_parents": 109,
        "candidate_assets": 222,
        "searchable_assets": 205,
        "hold_assets": 1,
        "excluded_or_blocked_assets": 16,
        "orphan_parents": 0,
        "duplicate_parents": 0,
        "restricted_leakage": 0,
        "pending_leakage": 0,
        "integrity_check": "ok",
        "foreign_key_errors": 0,
        "read_only_reopen": True,
    }
    assert result["asset_boundary"]["approved_url_fields"] == 410
    assert result["asset_boundary"]["asset_identity_creates"] == 0
    assert result["asset_boundary"]["asset_identity_deletes"] == 0
    search = result["offline_search"]
    assert search["莉朵花藝"]["asset_count"] == 0
    assert search["littlegirl"]["citation_count"] == 0
    assert search["廣生堂"]["asset_types"] == ["article"]
    assert search["SHOPLINE Payments"]["record_count"] == 16
    assert search["SLP"]["record_ids"] == ["商家夥伴案例資料庫:r32"]


def test_exact_identity_and_expiration_fail_closed(tmp_path):
    with pytest.raises(ParentSyncConfirmationError, match="PLAN_ID"):
        validate_parent_sync_plan(
            repo_root=_root(), plan_id="parent-sync-plan-wrong",
            manifest_hash=EXPECTED_MANIFEST_HASH, temporary_root=tmp_path,
            validated_at=VALIDATED_AT,
        )
    with pytest.raises(ParentSyncConfirmationError, match="Manifest Hash"):
        validate_parent_sync_plan(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
            manifest_hash="0" * 64, temporary_root=tmp_path,
            validated_at=VALIDATED_AT,
        )
    with pytest.raises(ParentSyncConfirmationError, match="expired"):
        validate_parent_sync_plan(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
            manifest_hash=EXPECTED_MANIFEST_HASH, temporary_root=tmp_path,
            validated_at="2026-07-28T00:00:00+08:00",
        )


def test_confirmation_is_blocked_and_reports_are_complete(tmp_path):
    confirmation = tmp_path / "confirmation"
    reports = tmp_path / "reports"
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")
    target_kwargs = {
        "managed_vault_root": Path(fixture["managed_vault_root"]),
        "formal_sqlite_path": Path(fixture["formal_sqlite_path"]),
    }
    result = confirm_parent_sync_plan(
        repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH, reviewer="Admin",
        confirmed_at=VALIDATED_AT, confirmation_path=confirmation,
        report_dir=reports, temporary_root=tmp_path / "temporary",
        **target_kwargs,
    )

    assert result["conclusion"] == "C. Confirmation blocked"
    assert result["confirmation_created"] is False
    assert not confirmation.exists()
    assert len([path for path in reports.iterdir() if path.is_file() and not path.name.startswith("._")]) == 22
    assert (reports / "confirmation_validation_errors.csv").stat().st_size > 0

    first_hash = _hash_path(reports)
    second = confirm_parent_sync_plan(
        repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH, reviewer="Admin",
        confirmed_at=VALIDATED_AT, confirmation_path=confirmation,
        report_dir=reports, temporary_root=tmp_path / "temporary-rerun",
        **target_kwargs,
    )
    assert second["confirmation_created"] is False
    assert _hash_path(reports) == first_hash


def test_formal_systems_remain_unchanged(tmp_path):
    protected = [
        _root() / "data/governance/governance_decisions.sqlite",
        _root() / "data/governance/executions/decision-store-schema-v2-plan-2aab43cd463170f2",
        _root() / "obsidian_vault",
        _root() / ".mka/content_index.sqlite",
        _root() / "src/marketing_knowledge_agent/slack_interface.py",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    result = _validate(tmp_path)

    assert {str(path): _hash_path(path) for path in protected} == before
    assert result["formal_systems_unchanged"] is True
    assert result["decision_store_validation"]["database_unchanged"] is True


def test_cli_validate_and_confirm_are_preview_only(tmp_path, monkeypatch, capsys):
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")
    target_kwargs = {
        "managed_vault_root": Path(fixture["managed_vault_root"]),
        "formal_sqlite_path": Path(fixture["formal_sqlite_path"]),
    }
    original_validate = cli.validate_parent_sync_plan
    original_confirm = cli.confirm_parent_sync_plan

    monkeypatch.setattr(
        cli,
        "validate_parent_sync_plan",
        lambda **kwargs: original_validate(**kwargs, **target_kwargs),
    )
    monkeypatch.setattr(
        cli,
        "confirm_parent_sync_plan",
        lambda **kwargs: original_confirm(**kwargs, **target_kwargs),
    )
    assert main([
        "validate-parent-sync-plan",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--temporary-root", str(tmp_path / "validate-temp"),
        "--validated-at", VALIDATED_AT,
    ]) == 1
    assert "audit_only_fields_materialized_by_plan" in capsys.readouterr().out

    assert main([
        "confirm-parent-sync-plan",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--reviewer", "Admin",
        "--confirmed-at", VALIDATED_AT,
        "--confirmation-path", str(tmp_path / "confirmation"),
        "--output", str(tmp_path / "reports"),
        "--temporary-root", str(tmp_path / "confirm-temp"),
    ]) == 1
    assert not (tmp_path / "confirmation").exists()
