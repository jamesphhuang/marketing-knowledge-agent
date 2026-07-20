import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance_decision_store_schema_v2_confirmation import (
    CANONICAL_SQL_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    EXPECTED_SCHEMA_HASH,
    GovernanceDecisionStoreSchemaV2ConfirmationError,
    confirm_governance_decision_store_schema_v2_plan,
    validate_governance_decision_store_schema_v2_confirmation,
    validate_governance_decision_store_schema_v2_plan,
)


CONFIRMED_AT = "2026-07-20T16:00:00+08:00"


def test_real_v2_plan_is_independently_validated(tmp_path):
    result = validate_governance_decision_store_schema_v2_plan(**_validation_args(tmp_path))

    assert result["valid"] is True
    assert result["plan_id"] == EXPECTED_PLAN_ID
    assert result["manifest_hash"] == EXPECTED_MANIFEST_HASH
    assert result["schema_hash"] == EXPECTED_SCHEMA_HASH
    assert result["canonical_sql_hash"] == CANONICAL_SQL_HASH
    assert result["plan_not_expired"] is True
    assert result["bundle_verified_file_count"] == 22
    assert result["bundle_file_checksum_errors"] == 0
    assert result["event_count"] == 162
    assert result["current_parent_state_count"] == 120
    assert result["authority_gap"] == 0


@pytest.mark.parametrize(("field", "value", "message"), [
    ("plan_id", "latest", "PLAN_ID"),
    ("manifest_hash", "0" * 64, "Manifest Hash"),
    ("schema_hash", "0" * 64, "Schema Hash"),
    ("canonical_sql_hash", "0" * 64, "Canonical SQL Hash"),
])
def test_exact_v2_authority_is_required(tmp_path, field, value, message):
    args = _validation_args(tmp_path)
    args[field] = value
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match=message):
        validate_governance_decision_store_schema_v2_plan(**args)


@pytest.mark.parametrize("obsolete", [
    "decision-store-plan-a02502d8361549b1",
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
])
def test_v1_and_obsolete_plans_are_rejected(tmp_path, obsolete):
    args = _validation_args(tmp_path)
    args["plan_id"] = obsolete
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="obsolete"):
        validate_governance_decision_store_schema_v2_plan(**args)


def test_expired_plan_is_rejected(tmp_path):
    args = _validation_args(tmp_path)
    args["now"] = "2026-07-27T15:29:03+08:00"
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="expired"):
        validate_governance_decision_store_schema_v2_plan(**args)


def test_tampered_bundle_schema_and_plan_fail_closed(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(_root() / "data/governance/imports/parent-authority-approval-20260719", bundle)
    evidence = bundle / "evidence/approved_parent_authority.csv"
    os.chmod(evidence, 0o644)
    evidence.write_bytes(evidence.read_bytes() + b"\n")
    args = _validation_args(tmp_path)
    args["bundle_path"] = bundle
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="Bundle"):
        validate_governance_decision_store_schema_v2_plan(**args)

    schema = tmp_path / "schema.sql"
    schema.write_bytes(_validation_args(tmp_path)["canonical_schema_path"].read_bytes() + b"\n")
    args = _validation_args(tmp_path)
    args["canonical_schema_path"] = schema
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="Canonical SQL"):
        validate_governance_decision_store_schema_v2_plan(**args)

    manifest = tmp_path / "manifest.json"
    payload = json.loads(_validation_args(tmp_path)["plan_manifest_path"].read_text())
    payload["expected_event_count"] = 163
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    args = _validation_args(tmp_path)
    args["plan_manifest_path"] = manifest
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="manifest"):
        validate_governance_decision_store_schema_v2_plan(**args)


def test_binding_contract_is_deterministic_and_collision_safe(tmp_path):
    result = validate_governance_decision_store_schema_v2_plan(**_validation_args(tmp_path))
    binding = result["binding_validation"]

    assert binding["plan_excludes_future_confirmation"] is True
    assert binding["schema_hash_unchanged_by_binding"] is True
    assert binding["decision_payload_unchanged"] is True
    assert binding["no_circular_dependency"] is True
    assert binding["same_confirmation_deterministic"] is True
    assert binding["different_confirmation_no_collision"] is True
    assert binding["old_confirmation_rejected"] is True


def test_independent_event_and_special_decision_reconciliation(tmp_path):
    result = validate_governance_decision_store_schema_v2_plan(**_validation_args(tmp_path))

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
    assert result["duplicate_event_template_count"] == 0
    assert result["resolution_supersede_count"] == 5
    assert result["special_decision_errors"] == 0
    assert (result["eligible_asset_count"], result["hold_asset_count"], result["excluded_asset_count"]) == (205, 1, 16)
    assert result["approved_url_field_count"] == 410


def test_independent_temporary_v2_database_passes(tmp_path):
    temporary = validate_governance_decision_store_schema_v2_plan(
        **_validation_args(tmp_path)
    )["temporary_store"]

    assert temporary["fresh_path"] is True
    assert temporary["event_count"] == 162
    assert temporary["current_parent_state_count"] == 120
    assert temporary["integrity_check"] == "ok"
    assert temporary["foreign_key_errors"] == 0
    assert temporary["all_append_only_updates_blocked"] is True
    assert temporary["all_append_only_deletes_blocked"] is True
    assert temporary["idempotency"] is True
    assert temporary["supersede_projection"] is True
    assert temporary["revoke_projection"] is True
    assert temporary["hash_chain_valid"] is True
    assert temporary["tamper_detection"] is True
    assert temporary["read_only_reopen"] is True
    assert temporary["transaction_rollback"] is True
    assert temporary["backup_restore"] is True
    assert temporary["deterministic_rerun"] is True
    assert temporary["database_sha_self_reference_absent"] is True


def test_formal_target_or_residue_blocks_confirmation(tmp_path):
    target = tmp_path / "governance_decisions.sqlite"
    target.write_text("partial", encoding="utf-8")
    args = _validation_args(tmp_path)
    args["formal_target_path"] = target
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="formal target"):
        validate_governance_decision_store_schema_v2_plan(**args)

    target.unlink()
    target.with_name(target.name + "-wal").write_text("residue", encoding="utf-8")
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="residue"):
        validate_governance_decision_store_schema_v2_plan(**args)


def test_confirmation_bundle_is_atomic_valid_and_complete(tmp_path):
    args = _confirmation_args(tmp_path)
    result = confirm_governance_decision_store_schema_v2_plan(**args)
    validation = validate_governance_decision_store_schema_v2_confirmation(
        args["confirmation_path"]
    )

    assert result["conclusion"] == "A. Schema V2 Plan independently validated and confirmed"
    assert result["idempotent_noop"] is False
    assert validation["valid"] is True
    assert validation["protected_file_count"] == 6
    assert validation["physical_file_count"] == 7
    assert len([path for path in args["report_dir"].iterdir() if path.is_file()]) == 16
    assert not args["formal_target_path"].exists()
    assert not list(args["confirmation_path"].parent.glob(f".{args['confirmation_path'].name}.staging-*"))


def test_identical_rerun_is_noop_and_conflicting_bundle_is_rejected(tmp_path):
    args = _confirmation_args(tmp_path)
    first = confirm_governance_decision_store_schema_v2_plan(**args)
    before = _hash_path(args["confirmation_path"])
    second = confirm_governance_decision_store_schema_v2_plan(**args)

    assert second["idempotent_noop"] is True
    assert second["root_confirmation_hash"] == first["root_confirmation_hash"]
    assert _hash_path(args["confirmation_path"]) == before

    confirmation = args["confirmation_path"] / "confirmation.json"
    os.chmod(confirmation, 0o644)
    payload = json.loads(confirmation.read_text())
    payload["reviewer"] = "Mallory"
    confirmation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError):
        confirm_governance_decision_store_schema_v2_plan(**args)


def test_old_confirmation_is_rejected_for_v2():
    old = _root() / "data/governance/confirmations/decision-store-plan-a02502d8361549b1"
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="Schema V2"):
        validate_governance_decision_store_schema_v2_confirmation(old)


def test_reviewer_timestamp_and_cli_contract(tmp_path, monkeypatch, capsys):
    args = _confirmation_args(tmp_path / "bad-reviewer")
    args["reviewer"] = "James Huang"
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="Admin"):
        confirm_governance_decision_store_schema_v2_plan(**args)

    args = _confirmation_args(tmp_path / "bad-time")
    args["confirmed_at"] = "not-a-time"
    with pytest.raises(GovernanceDecisionStoreSchemaV2ConfirmationError, match="timestamp"):
        confirm_governance_decision_store_schema_v2_plan(**args)

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    confirmation = tmp_path / "cli-confirmation"
    reports = tmp_path / "cli-reports"
    assert main([
        "confirm-governance-decision-store-schema-v2-plan",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--schema-hash", EXPECTED_SCHEMA_HASH,
        "--canonical-sql-hash", CANONICAL_SQL_HASH,
        "--reviewer", "Admin",
        "--confirmed-at", CONFIRMED_AT,
        "--confirmation-path", str(confirmation),
        "--output", str(reports),
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmed"] is True
    assert not (_root() / "data/governance/governance_decisions.sqlite").exists()


def test_confirmation_does_not_modify_protected_systems(tmp_path):
    args = _confirmation_args(tmp_path)
    protected = [
        _root() / "data/governance/imports/parent-authority-approval-20260719",
        _root() / "reports/governance_decision_store_schema_v2_plan",
        _root() / "data/governance/confirmations/decision-store-plan-a02502d8361549b1",
        _root() / "obsidian_vault",
        _root() / ".mka/content_index.sqlite",
        _root() / "src/marketing_knowledge_agent/slack_interface.py",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    confirm_governance_decision_store_schema_v2_plan(**args)

    assert before == {str(path): _hash_path(path) for path in protected}
    assert not args["formal_target_path"].exists()


def _validation_args(tmp_path):
    root = _root()
    return {
        "repo_root": root,
        "plan_id": EXPECTED_PLAN_ID,
        "manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_sql_hash": CANONICAL_SQL_HASH,
        "plan_manifest_path": root / "reports/governance_decision_store_schema_v2_plan/schema_v2_plan_manifest.json",
        "canonical_schema_path": root / "reports/governance_decision_store_schema_v2_plan/canonical_schema_v2.sql",
        "schema_hash_path": root / "reports/governance_decision_store_schema_v2_plan/canonical_schema_v2_hash.json",
        "bundle_path": root / "data/governance/imports/parent-authority-approval-20260719",
        "old_confirmation_path": root / "data/governance/confirmations/decision-store-plan-a02502d8361549b1",
        "legacy_decisions_path": root / "reports/excel_preview/review_decisions_template.csv",
        "merchant_cases_path": root / "reports/excel_preview/merchant_cases.json",
        "asset_url_decisions_path": root / "reports/asset_metadata_preview/human_review_template.csv",
        "asset_url_validation_path": root / "reports/asset_metadata_review_validation/review_decision_status.csv",
        "asset_apply_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview.csv",
        "asset_blocked_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv",
        "formal_target_path": root / "data/governance/governance_decisions.sqlite",
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
