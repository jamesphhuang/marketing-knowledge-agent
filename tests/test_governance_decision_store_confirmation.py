import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

import marketing_knowledge_agent.governance_decision_store_confirmation as confirmation_module
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance_decision_store_confirmation import (
    BUNDLE_ROOT_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    GovernanceDecisionStoreConfirmationError,
    confirm_governance_decision_store_plan,
    validate_governance_decision_store_confirmation,
    validate_governance_decision_store_plan,
)


CONFIRMED_AT = "2026-07-20T10:00:00+08:00"


@pytest.fixture(autouse=True)
def _isolate_confirmation_report_formal_checks(monkeypatch):
    checks = (
        "governance_decisions.sqlite_absent", "formal_vault_present_unchanged",
        "managed_vault_present_unchanged", "content_index_present_unchanged",
        "production_renderer_present_unchanged", "parent_sync_not_executed",
        "asset_url_not_applied", "asset_eligibility_not_applied",
        "search_alias_not_applied", "slack_api_not_called",
    )
    monkeypatch.setattr(
        confirmation_module, "_formal_checks",
        lambda root: [{"check": check, "status": "pass"} for check in checks],
    )


def test_real_plan_is_independently_validated_without_generator(tmp_path):
    result = validate_governance_decision_store_plan(**_validation_args(tmp_path))

    assert result["valid"] is True
    assert result["plan_id"] == EXPECTED_PLAN_ID
    assert result["manifest_hash"] == EXPECTED_MANIFEST_HASH
    assert result["bundle_root_manifest_hash"] == BUNDLE_ROOT_HASH
    assert result["bundle_verified_file_count"] == 22
    assert result["event_count"] == 162
    assert result["current_parent_state_count"] == 120
    assert result["parent_authority_coverage"] == "120/120"
    assert result["authority_gap"] == 0
    assert result["formal_target_absent"] is True
    assert result["plan_not_expired"] is True


def test_exact_plan_id_manifest_hash_and_expiration_are_required(tmp_path):
    args = _validation_args(tmp_path)
    args["plan_id"] = "latest"
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="PLAN_ID"):
        validate_governance_decision_store_plan(**args)

    args = _validation_args(tmp_path)
    args["manifest_hash"] = "0" * 64
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="Manifest Hash"):
        validate_governance_decision_store_plan(**args)

    args = _validation_args(tmp_path)
    args["now"] = "2026-07-26T19:54:54+08:00"
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="expired"):
        validate_governance_decision_store_plan(**args)


@pytest.mark.parametrize("obsolete", [
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
])
def test_obsolete_plan_ids_are_rejected(tmp_path, obsolete):
    args = _validation_args(tmp_path)
    args["plan_id"] = obsolete
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="obsolete"):
        validate_governance_decision_store_plan(**args)


def test_tampered_bundle_and_plan_fail_closed(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(_root() / "data/governance/imports/parent-authority-approval-20260719", bundle)
    evidence = bundle / "evidence/approved_parent_authority.csv"
    os.chmod(evidence, 0o644)
    evidence.write_bytes(evidence.read_bytes() + b"\n")
    args = _validation_args(tmp_path)
    args["bundle_path"] = bundle
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="Bundle"):
        validate_governance_decision_store_plan(**args)

    manifest = tmp_path / "plan.json"
    payload = json.loads((_root() / "reports/governance_decision_store_regenerated_plan/regenerated_decision_store_manifest.json").read_text())
    payload["expected_event_count"] = 163
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    args = _validation_args(tmp_path)
    args["plan_manifest_path"] = manifest
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="manifest"):
        validate_governance_decision_store_plan(**args)


def test_independent_event_and_special_decision_reconciliation(tmp_path):
    result = validate_governance_decision_store_plan(**_validation_args(tmp_path))

    assert result["event_counts"] == {
        "legacy_import": 46,
        "batch_parent_approval": 96,
        "resolution_parent_supersede": 5,
        "asset_eligibility": 10,
        "search_alias": 2,
        "entity_metadata": 2,
        "asset_url_manifest_reference": 1,
    }
    assert result["parent_event_count"] == 125
    assert result["non_parent_event_count"] == 37
    assert result["unique_parent_subject_count"] == 120
    assert result["duplicate_event_id_count"] == 0
    assert result["duplicate_idempotency_key_count"] == 0
    assert result["resolution_supersede_count"] == 5
    assert result["special_decision_errors"] == 0
    assert result["approved_url_field_count"] == 410
    assert result["eligible_asset_count"] == 205
    assert result["hold_asset_count"] == 1
    assert result["excluded_asset_count"] == 16


def test_independent_temporary_store_checks_all_pass(tmp_path):
    result = validate_governance_decision_store_plan(**_validation_args(tmp_path))
    temporary = result["temporary_store"]

    assert temporary["event_count"] == 162
    assert temporary["integrity_check"] == "ok"
    assert temporary["foreign_key_errors"] == 0
    assert temporary["update_blocked"] is True
    assert temporary["delete_blocked"] is True
    assert temporary["idempotency"] is True
    assert temporary["hash_chain_valid"] is True
    assert temporary["tamper_detection"] is True
    assert temporary["read_only_reopen"] is True
    assert temporary["transaction_rollback"] is True
    assert temporary["backup_restore"] is True
    assert temporary["supersede_projection"] is True
    assert temporary["revoke_projection"] is True


def test_formal_target_or_residue_blocks_confirmation(tmp_path):
    target = tmp_path / "governance_decisions.sqlite"
    target.write_text("partial", encoding="utf-8")
    args = _validation_args(tmp_path)
    args["formal_target_path"] = target
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="formal target"):
        validate_governance_decision_store_plan(**args)

    target.unlink()
    target.with_suffix(".sqlite-wal").write_text("residue", encoding="utf-8")
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="residue"):
        validate_governance_decision_store_plan(**args)


def test_confirmation_bundle_is_atomic_valid_and_report_complete(tmp_path):
    args = _confirmation_args(tmp_path)
    result = confirm_governance_decision_store_plan(**args)
    validation = validate_governance_decision_store_confirmation(args["confirmation_path"])

    assert result["conclusion"] == "A. Plan independently validated and confirmed"
    assert result["idempotent_noop"] is False
    assert result["reviewer"] == "Admin"
    assert result["confirmed_at"] == CONFIRMED_AT
    assert validation["valid"] is True
    assert validation["physical_file_count"] == 5
    assert validation["protected_file_count"] == 4
    assert len([path for path in args["report_dir"].iterdir() if path.is_file()]) == 13
    assert not args["formal_target_path"].exists()
    assert not list(args["confirmation_path"].parent.glob(f".{args['confirmation_path'].name}.staging-*"))


def test_identical_confirmation_rerun_is_noop_and_conflict_is_rejected(tmp_path):
    args = _confirmation_args(tmp_path)
    first = confirm_governance_decision_store_plan(**args)
    before = _hash_path(args["confirmation_path"])
    second = confirm_governance_decision_store_plan(**args)

    assert first["root_confirmation_hash"] == second["root_confirmation_hash"]
    assert second["idempotent_noop"] is True
    assert _hash_path(args["confirmation_path"]) == before

    manifest = args["confirmation_path"] / "confirmation_manifest.json"
    os.chmod(manifest, 0o644)
    payload = json.loads(manifest.read_text())
    payload["reviewer"] = "Mallory"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GovernanceDecisionStoreConfirmationError):
        confirm_governance_decision_store_plan(**args)


def test_reviewer_is_admin_and_confirmation_timestamp_is_valid(tmp_path):
    args = _confirmation_args(tmp_path)
    args["reviewer"] = "James Huang"
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="Admin"):
        confirm_governance_decision_store_plan(**args)

    args = _confirmation_args(tmp_path)
    args["confirmed_at"] = "not-a-time"
    with pytest.raises(GovernanceDecisionStoreConfirmationError, match="timestamp"):
        confirm_governance_decision_store_plan(**args)


def test_cli_confirms_without_slack_token_or_formal_store(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    confirmation = tmp_path / "confirmation"
    reports = tmp_path / "reports"
    target = tmp_path / "formal-target.sqlite"

    assert main([
        "confirm-governance-decision-store-plan",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--reviewer", "Admin",
        "--confirmed-at", CONFIRMED_AT,
        "--confirmation-path", str(confirmation),
        "--target", str(target),
        "--output", str(reports),
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmed"] is True
    assert not target.exists()


def test_confirmation_does_not_modify_protected_systems(tmp_path):
    args = _confirmation_args(tmp_path)
    protected = [
        _root() / "data/governance/imports/parent-authority-approval-20260719",
        _root() / "reports/governance_decision_store_regenerated_plan/regenerated_decision_store_manifest.json",
        _root() / "obsidian_vault",
        _root() / ".mka/content_index.sqlite",
        _root() / "src/marketing_knowledge_agent/slack_interface.py",
    ]
    before = {str(path): _hash_path(path) for path in protected}

    confirm_governance_decision_store_plan(**args)

    assert before == {str(path): _hash_path(path) for path in protected}
    assert not args["formal_target_path"].exists()


def _validation_args(tmp_path):
    root = _root()
    return {
        "repo_root": root,
        "plan_id": EXPECTED_PLAN_ID,
        "manifest_hash": EXPECTED_MANIFEST_HASH,
        "plan_manifest_path": root / "reports/governance_decision_store_regenerated_plan/regenerated_decision_store_manifest.json",
        "bundle_path": root / "data/governance/imports/parent-authority-approval-20260719",
        "legacy_decisions_path": root / "reports/excel_preview/review_decisions_template.csv",
        "merchant_cases_path": root / "reports/excel_preview/merchant_cases.json",
        "asset_url_decisions_path": root / "reports/asset_metadata_preview/human_review_template.csv",
        "asset_url_validation_path": root / "reports/asset_metadata_review_validation/review_decision_status.csv",
        "asset_apply_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview.csv",
        "asset_blocked_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv",
        "formal_target_path": tmp_path / "formal-target.sqlite",
        "temporary_root": tmp_path / "temporary",
        "now": CONFIRMED_AT,
    }


def _confirmation_args(tmp_path):
    args = _validation_args(tmp_path)
    args.update({
        "reviewer": "Admin",
        "confirmed_at": CONFIRMED_AT,
        "confirmation_path": tmp_path / "confirmations" / EXPECTED_PLAN_ID,
        "report_dir": tmp_path / "reports",
    })
    return args


def _root():
    return Path(__file__).resolve().parents[1]


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()
