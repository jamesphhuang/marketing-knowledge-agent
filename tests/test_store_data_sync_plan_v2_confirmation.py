from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import marketing_knowledge_agent.cli as cli
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.store_data_sync_plan_v2_confirmation import (
    EXPECTED_MANAGED_DELTA_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    EXPECTED_SQLITE_DELTA_HASH,
    REPORT_FILENAMES,
    StoreDataSyncPlanV2ConfirmationError,
    confirm_store_data_sync_plan_v2,
    validate_store_data_sync_plan_v2,
    validate_store_data_sync_plan_v2_confirmation,
)
from marketing_knowledge_agent.store_data_sync_existing_validation import (
    reconstruct_pre_sync_fixture,
)


VALIDATED_AT = "2026-07-22T10:00:00+08:00"


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


@pytest.fixture(scope="module")
def validation(tmp_path_factory):
    root = tmp_path_factory.mktemp("store-sync-v2-confirmation")
    fixture = reconstruct_pre_sync_fixture(_root(), root / "prestate")
    return validate_store_data_sync_plan_v2(
        repo_root=_root(), plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        temporary_root=root / "temporary", now=VALIDATED_AT,
        managed_vault_root=Path(fixture["managed_vault_root"]),
        formal_sqlite_path=Path(fixture["formal_sqlite_path"]),
    )


def test_independent_identity_and_materialization_contract(validation):
    assert validation["valid"] is True
    assert validation["plan_identity_valid"] is True
    assert validation["reproduced_plan_id"] == EXPECTED_PLAN_ID
    assert validation["reproduced_manifest_hash"] == EXPECTED_MANIFEST_HASH
    assert validation["plan_not_expired"] is True
    assert validation["generator_called"] is False
    assert validation["consumer_contract_validation"]["valid"] is True
    assert validation["formal_schema_extended"] is False

    matrix = {row["field_name"]: row for row in validation["materialization_contract"]}
    for field in (
        "decision_event_id", "decision_event_hash", "decision_reviewer",
        "decision_reviewed_at", "decision_provenance",
    ):
        assert matrix[field]["managed_vault"] is False
        assert matrix[field]["formal_sqlite"] is False
        assert matrix[field]["production_search"] is False
        assert matrix[field]["triggers_write"] is False
    for field in (
        "search_alias_reviewed_by", "search_alias_reviewed_at", "search_alias_provenance",
    ):
        assert matrix[field]["managed_vault"] is True


def test_120_record_reconciliation_and_managed_delta(validation):
    assert validation["authoritative_record_count"] == 120
    assert validation["reconciliation_count"] == 120
    assert validation["unique_record_id_count"] == 120
    assert validation["authority_gap"] == 0
    assert validation["action_counts"] == {
        "create": 4,
        "update": 106,
        "no_change": 0,
        "remove_from_content_projection": 0,
        "retain_governance_only": 10,
        "blocked": 0,
        "manual_review": 0,
    }
    assert validation["managed_vault_counts"] == {
        "current": 106, "create": 4, "update": 106,
        "no_change": 0, "target": 110, "path_collisions": 0,
    }
    assert validation["managed_vault_delta_hash"] == EXPECTED_MANAGED_DELTA_HASH
    assert all(
        row["write_required"] and row["changed_fields"]
        for row in validation["managed_vault_delta"]
    )
    assert validation["audit_only_write_occurrences"] == 0


def test_formal_sqlite_delta_and_governance_only(validation):
    assert validation["formal_sqlite_counts"] == {
        "current": 105, "create": 4, "update": 0,
        "no_change": 105, "target": 109, "not_projected": 11,
    }
    assert validation["formal_sqlite_delta_hash"] == EXPECTED_SQLITE_DELTA_HASH
    assert len(validation["formal_sqlite_delta"]) == 4
    assert all(row["action"] == "create" for row in validation["formal_sqlite_delta"])
    assert validation["formal_existing_consumer_diff_count"] == 0
    assert {row["record_id"].rsplit(":", 1)[-1] for row in validation["not_projected"]} == {
        "r20", "r30", "r57", "r83", "r87", "r101",
        "r102", "r103", "r107", "r116", "r121",
    }
    assert validation["r20_vault_only_valid"] is True
    assert len(validation["governance_only_records"]) == 10


def test_four_create_special_asset_candidate_and_search(validation):
    assert {row["record_id"].rsplit(":", 1)[-1] for row in validation["four_create_records"]} == {
        "r7", "r12", "r32", "r122",
    }
    assert all(row["valid"] for row in validation["four_create_records"])
    assert all(row["status"] == "pass" for row in validation["special_record_validation"])
    assert validation["asset_boundary"] == {
        "eligible_assets": 205, "hold_assets": 1,
        "excluded_or_blocked_assets": 16, "approved_url_fields": 410,
        "asset_identity_creates": 0, "asset_identity_deletes": 0,
        "url_values_copied": 0, "parent_tags_copied_to_assets": 0,
        "aliases_copied_to_assets": 0,
    }
    assert validation["candidate_validation"]["managed_vault_parents"] == 110
    assert validation["candidate_validation"]["content_parents"] == 109
    assert validation["candidate_validation"]["candidate_assets"] == 222
    assert validation["candidate_validation"]["searchable_assets"] == 205
    assert validation["candidate_validation"]["orphan_parents"] == 0
    assert validation["offline_search"]["莉朵花藝"]["asset_count"] == 0
    assert validation["offline_search"]["littlegirl"]["asset_count"] == 0
    assert validation["offline_search"]["廣生堂"]["asset_types"] == ["article"]
    assert validation["offline_search"]["SLP"]["asset_types"] == ["article", "podcast", "video"]
    assert validation["offline_search"]["SHOPLINE Payments"]["record_count"] == 16


def test_exact_identity_expiration_and_reviewer_fail_closed(tmp_path):
    with pytest.raises(StoreDataSyncPlanV2ConfirmationError, match="PLAN_ID"):
        validate_store_data_sync_plan_v2(
            repo_root=_root(), plan_id="wrong", manifest_hash=EXPECTED_MANIFEST_HASH,
            temporary_root=tmp_path, now=VALIDATED_AT,
        )
    with pytest.raises(StoreDataSyncPlanV2ConfirmationError, match="Manifest Hash"):
        validate_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash="0" * 64,
            temporary_root=tmp_path, now=VALIDATED_AT,
        )
    with pytest.raises(StoreDataSyncPlanV2ConfirmationError, match="expired"):
        validate_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
            temporary_root=tmp_path, now="2026-07-29T00:00:00+08:00",
        )
    with pytest.raises(StoreDataSyncPlanV2ConfirmationError, match="Admin"):
        confirm_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
            reviewer="Reviewer", confirmed_at=VALIDATED_AT,
            confirmation_path=tmp_path / "confirmation", report_dir=tmp_path / "reports",
            temporary_root=tmp_path / "temporary",
        )


def test_confirmation_atomic_idempotent_and_conflict(tmp_path):
    confirmation = tmp_path / "confirmation"
    reports = tmp_path / "reports"
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")
    target_kwargs = {
        "managed_vault_root": Path(fixture["managed_vault_root"]),
        "formal_sqlite_path": Path(fixture["formal_sqlite_path"]),
    }
    first = confirm_store_data_sync_plan_v2(
        repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
        reviewer="Admin", confirmed_at=VALIDATED_AT,
        confirmation_path=confirmation, report_dir=reports,
        temporary_root=tmp_path / "temporary-a", require_git_ignored=False,
        **target_kwargs,
    )
    validated = validate_store_data_sync_plan_v2_confirmation(confirmation)
    before = _hash_path(confirmation)
    second = confirm_store_data_sync_plan_v2(
        repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
        reviewer="Admin", confirmed_at=VALIDATED_AT,
        confirmation_path=confirmation, report_dir=reports,
        temporary_root=tmp_path / "temporary-b", require_git_ignored=False,
        **target_kwargs,
    )

    assert first["confirmation_created"] is True
    assert first["idempotent_noop"] is False
    assert second["confirmation_created"] is False
    assert second["idempotent_noop"] is True
    assert _hash_path(confirmation) == before
    assert validated["valid"] is True
    assert validated["protected_file_count"] == 8
    assert validated["physical_file_count"] == 9
    assert len([path for path in reports.iterdir() if path.is_file()]) == len(REPORT_FILENAMES)

    with pytest.raises(StoreDataSyncPlanV2ConfirmationError, match="conflicts"):
        confirm_store_data_sync_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
            reviewer="Admin", confirmed_at="2026-07-22T10:01:00+08:00",
            confirmation_path=confirmation, report_dir=reports,
            temporary_root=tmp_path / "temporary-c", require_git_ignored=False,
            **target_kwargs,
        )


def test_formal_systems_unchanged_and_cli_is_confirmation_only(tmp_path, monkeypatch, capsys):
    protected = [
        _root() / "data/governance/governance_decisions.sqlite",
        _root() / "obsidian_vault",
        _root() / ".mka/content_index.sqlite",
        _root() / "src/marketing_knowledge_agent/slack_interface.py",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")
    target_kwargs = {
        "managed_vault_root": Path(fixture["managed_vault_root"]),
        "formal_sqlite_path": Path(fixture["formal_sqlite_path"]),
    }
    original_validate = cli.validate_store_data_sync_plan_v2
    original_confirm = cli.confirm_store_data_sync_plan_v2
    monkeypatch.setattr(
        cli,
        "validate_store_data_sync_plan_v2",
        lambda **kwargs: original_validate(**kwargs, **target_kwargs),
    )
    monkeypatch.setattr(
        cli,
        "confirm_store_data_sync_plan_v2",
        lambda **kwargs: original_confirm(**kwargs, **target_kwargs),
    )
    assert main([
        "validate-store-data-sync-plan-v2",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--temporary-root", str(tmp_path / "validate-temporary"),
        "--now", VALIDATED_AT,
    ]) == 0
    assert "plan_identity_valid" in capsys.readouterr().out

    assert main([
        "confirm-store-data-sync-plan-v2",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--reviewer", "Admin",
        "--confirmed-at", VALIDATED_AT,
        "--confirmation-path", str(tmp_path / "cli-confirmation"),
        "--output", str(tmp_path / "cli-reports"),
        "--temporary-root", str(tmp_path / "cli-temporary"),
        "--allow-non-ignored-test-path",
    ]) == 0
    assert "confirmation_id" in capsys.readouterr().out
    assert {str(path): _hash_path(path) for path in protected} == before
