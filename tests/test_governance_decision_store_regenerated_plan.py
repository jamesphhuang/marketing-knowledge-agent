import csv
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance_decision_store_plan import DECISION_STORE_SCHEMA
from marketing_knowledge_agent.governance_decision_store_regenerated_plan import (
    BUNDLE_ID,
    BUNDLE_ROOT_HASH,
    OBSOLETE_PLAN_IDS,
    RegeneratedDecisionStorePlanError,
    build_regenerated_event_plan,
    generate_regenerated_governance_decision_store_plan,
)


CREATED_AT = "2026-07-19T20:00:00+08:00"


def test_real_regenerated_plan_is_ready_and_uses_validated_bundle(tmp_path):
    summary = generate_regenerated_governance_decision_store_plan(**_real_args(tmp_path))

    assert summary["conclusion"] == "A. Ready for Admin Decision Store confirmation"
    assert summary["bundle_id"] == BUNDLE_ID
    assert summary["bundle_root_manifest_hash"] == BUNDLE_ROOT_HASH
    assert summary["bundle_file_checksum_errors"] == 0
    assert summary["expected_event_count"] == 162
    assert summary["current_parent_state_count"] == 120
    assert summary["parent_authority_coverage"] == "120/120"
    assert summary["remaining_authority_gap"] == 0
    assert summary["execution_blocked"] is False
    assert summary["formal_data_modified"] is False
    assert len(_output_files(_real_args(tmp_path)["output_dir"])) == 19


def test_missing_and_tampered_bundle_fail_closed(tmp_path):
    missing = _real_args(tmp_path / "missing")
    missing["bundle_path"] = tmp_path / "does-not-exist"
    with pytest.raises(RegeneratedDecisionStorePlanError, match="Bundle"):
        generate_regenerated_governance_decision_store_plan(**missing)

    tampered_bundle = tmp_path / "tampered-bundle"
    shutil.copytree(_root() / "data/governance/imports/parent-authority-approval-20260719", tampered_bundle)
    evidence = tampered_bundle / "evidence/approved_parent_authority.csv"
    os.chmod(evidence, 0o644)
    evidence.write_bytes(evidence.read_bytes() + b"\n")
    tampered = _real_args(tmp_path / "tampered")
    tampered["bundle_path"] = tampered_bundle
    with pytest.raises(RegeneratedDecisionStorePlanError, match="Bundle"):
        generate_regenerated_governance_decision_store_plan(**tampered)


def test_event_plan_conserves_categories_and_bundle_provenance(tmp_path):
    plan = build_regenerated_event_plan(**_event_args(tmp_path))
    events = plan["events"]

    assert len(events) == 162
    assert plan["counts"] == {
        "legacy_import": 46,
        "batch_parent_approval": 96,
        "resolution_parent_supersede": 5,
        "asset_eligibility": 10,
        "search_alias": 2,
        "entity_metadata": 2,
        "asset_url_manifest_reference": 1,
    }
    bundle_events = [event for event in events if event.provenance in {"batch_approval", "admin_resolution"}]
    assert len(bundle_events) == 115
    assert {event.source_bundle_id for event in bundle_events} == {BUNDLE_ID}
    assert {event.source_bundle_root_hash for event in bundle_events} == {BUNDLE_ROOT_HASH}
    resolution = [event for event in events if event.event_type == "parent_review_decision" and event.action == "supersede"]
    assert len(resolution) == 5
    assert all(event.supersedes_event_id for event in resolution)


def test_legacy_batch_and_resolution_reviewer_metadata_are_preserved(tmp_path):
    events = build_regenerated_event_plan(**_event_args(tmp_path))["events"]
    legacy = [event for event in events if event.provenance == "legacy_import"]
    batch = [event for event in events if event.provenance == "batch_approval"]
    resolution = [event for event in events if event.event_type == "parent_review_decision" and event.action == "supersede"]

    assert len(legacy) == 46
    assert all(event.reviewed_at == "2026-07-10" for event in legacy)
    assert len(batch) == 96
    assert {event.reviewer for event in batch} == {"Admin"}
    assert {event.reviewed_at for event in batch} == {"2026-07-19T18:14:14+08:00"}
    assert {event.reviewer for event in resolution} == {"Admin"}
    assert {event.reviewed_at for event in resolution} == {"2026-07-18T00:33:08+08:00"}


def test_special_parent_asset_alias_and_entity_events_remain_exact(tmp_path):
    events = build_regenerated_event_plan(**_event_args(tmp_path))["events"]
    assets = [event for event in events if event.event_type == "asset_eligibility"]
    aliases = [event for event in events if event.event_type == "search_alias"]
    entities = [event for event in events if event.event_type == "entity_metadata"]
    parents = {event.record_id: event for event in events if event.event_type == "parent_review_decision" and event.action == "supersede"}

    assert sum(event.action == "approve" for event in assets) == 8
    assert sum(event.action == "hold" for event in assets) == 1
    assert sum(event.action == "exclude" for event in assets) == 1
    held = next(event for event in assets if event.asset_id.endswith(":r12:video"))
    assert held.new_value["asset_search_eligibility"] == "not_searchable"
    assert parents["商家夥伴案例資料庫:r12"].new_value["can_external_reference"] == "false"
    assert parents["商家夥伴案例資料庫:r30"].new_value["review_decision"] == "exclude"
    assert {event.new_value["normalized_alias"] for event in aliases} == {"slp", "shopline payments"}
    assert {event.record_id for event in entities} == {"商家夥伴案例資料庫:r7", "商家夥伴案例資料庫:r122"}
    assert all(event.new_value["merchant_handle_requirement"] == "not_required" for event in entities)


def test_temporary_store_and_current_state_projections_are_fully_validated(tmp_path):
    summary = generate_regenerated_governance_decision_store_plan(**_real_args(tmp_path))

    assert summary["temporary_event_count"] == 162
    assert summary["current_parent_state_count"] == 120
    assert summary["temporary_integrity_check"] == "ok"
    assert summary["temporary_foreign_key_errors"] == 0
    assert summary["temporary_update_blocked"] is True
    assert summary["temporary_delete_blocked"] is True
    assert summary["temporary_idempotency"] is True
    assert summary["temporary_hash_chain_valid"] is True
    assert summary["temporary_tamper_detection"] is True
    assert summary["temporary_read_only_reopen"] is True
    assert summary["temporary_transaction_rollback"] is True
    assert summary["temporary_backup_restore"] is True
    assert summary["temporary_supersede_projection"] is True
    assert summary["temporary_revoke_projection"] is True
    assert summary["temporary_alias_multi_parent"] is True


def test_asset_url_reference_preserves_410_without_copying_values(tmp_path):
    plan = build_regenerated_event_plan(**_event_args(tmp_path))
    reference = next(event for event in plan["events"] if event.event_type == "asset_url_manifest_reference")

    assert reference.new_value["approved_url_field_count"] == 410
    assert reference.new_value["eligible_asset_count"] == 205
    assert reference.new_value["hold_asset_count"] == 1
    assert reference.new_value["excluded_or_blocked_asset_count"] == 16
    assert "url_values" not in reference.new_value
    assert reference.new_value["decision_csv_checksum"] == plan["input_checksums"]["asset_url_decisions"]


def test_manifest_plan_id_is_new_deterministic_and_not_executable(tmp_path):
    first_args = _real_args(tmp_path / "first")
    second_args = _real_args(tmp_path / "second")
    second_args["target_path"] = first_args["target_path"]
    first = generate_regenerated_governance_decision_store_plan(**first_args)
    second = generate_regenerated_governance_decision_store_plan(**second_args)
    manifest = json.loads((first_args["output_dir"] / "regenerated_decision_store_manifest.json").read_text())

    assert first["plan_id"] == second["plan_id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["plan_id"] not in OBSOLETE_PLAN_IDS
    assert manifest["confirm_supported"] is False
    assert manifest["execute_supported"] is False
    assert manifest["execution_blocked"] is False
    assert manifest["bundle_root_manifest_hash"] == BUNDLE_ROOT_HASH


def test_obsolete_plan_registry_rejects_all_previous_plans(tmp_path):
    args = _real_args(tmp_path)
    generate_regenerated_governance_decision_store_plan(**args)
    rows = _read_csv(args["output_dir"] / "obsolete_plan_registry.csv")

    assert {row["plan_id"] for row in rows} == set(OBSOLETE_PLAN_IDS)
    assert {row["status"] for row in rows} == {"DO NOT CONFIRM | SUPERSEDED | INVALID FOR EXECUTION"}


def test_sync_readiness_and_zero_authority_gap(tmp_path):
    args = _real_args(tmp_path)
    generate_regenerated_governance_decision_store_plan(**args)
    readiness = _read_csv(args["output_dir"] / "parent_sync_readiness.csv")
    coverage = _read_csv(args["output_dir"] / "parent_authority_coverage_validation.csv")

    assert {row["record_id"] for row in readiness if row["sync_readiness"] == "syncable"} == {
        "商家夥伴案例資料庫:r7",
        "商家夥伴案例資料庫:r12",
        "商家夥伴案例資料庫:r32",
        "商家夥伴案例資料庫:r122",
    }
    excluded = next(row for row in readiness if row["record_id"].endswith(":r30"))
    assert excluded["sync_readiness"] == "not_syncable"
    assert sum(row["authority_gap"] == "true" for row in coverage) == 0
    assert len(coverage) == 120


def test_schema_records_bundle_provenance_and_remains_append_only():
    assert "source_bundle_id TEXT" in DECISION_STORE_SCHEMA
    assert "source_bundle_root_hash TEXT" in DECISION_STORE_SCHEMA
    assert "decision_events_no_update" in DECISION_STORE_SCHEMA
    assert "decision_events_no_delete" in DECISION_STORE_SCHEMA


def test_formal_systems_bundle_and_inputs_are_unchanged(tmp_path):
    args = _real_args(tmp_path)
    protected = [
        args["bundle_path"],
        args["legacy_decisions_path"],
        args["asset_url_decisions_path"],
        args["formal_vault_path"],
        args["formal_db_path"],
        args["production_renderer_path"],
    ]
    before = {_safe_name(path): _hash_path(path) for path in protected}

    summary = generate_regenerated_governance_decision_store_plan(**args)

    assert before == {_safe_name(path): _hash_path(path) for path in protected}
    assert summary["formal_data_modified"] is False
    assert not args["target_path"].exists()


def test_cli_requires_no_slack_token_and_only_generates_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    output = tmp_path / "reports"
    target = tmp_path / "planned-governance-decisions.sqlite"

    assert main([
        "regenerate-governance-decision-store-plan",
        "--output", str(output),
        "--target", str(target),
        "--created-at", CREATED_AT,
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_blocked"] is False
    assert payload["formal_data_modified"] is False
    assert not target.exists()


def _real_args(tmp_path):
    root = _root()
    return {
        "repo_root": root,
        "bundle_path": root / "data/governance/imports/parent-authority-approval-20260719",
        "legacy_decisions_path": root / "reports/excel_preview/review_decisions_template.csv",
        "merchant_cases_path": root / "reports/excel_preview/merchant_cases.json",
        "asset_url_decisions_path": root / "reports/asset_metadata_preview/human_review_template.csv",
        "asset_url_validation_path": root / "reports/asset_metadata_review_validation/review_decision_status.csv",
        "asset_apply_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview.csv",
        "asset_blocked_preview_path": root / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv",
        "formal_vault_path": root / "obsidian_vault",
        "formal_db_path": root / ".mka/content_index.sqlite",
        "production_renderer_path": root / "src/marketing_knowledge_agent/slack_interface.py",
        "target_path": tmp_path / "planned-governance-decisions.sqlite",
        "output_dir": tmp_path / "regenerated-plan",
        "created_at": CREATED_AT,
        "source_branch": "test-branch",
        "source_commit": "test-commit",
    }


def _event_args(tmp_path):
    args = _real_args(tmp_path)
    for key in (
        "repo_root", "formal_vault_path", "formal_db_path", "production_renderer_path",
        "target_path", "output_dir", "source_branch", "source_commit",
    ):
        args.pop(key)
    args["plan_id"] = "decision-store-plan-test-regenerated"
    return args


def _root():
    return Path(__file__).resolve().parents[1]


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _safe_name(path):
    return str(Path(path))


def _output_files(path):
    return [item for item in Path(path).iterdir() if item.is_file() and not item.name.startswith("._")]
