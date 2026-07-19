import csv
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance_decision_store_plan import (
    DECISION_STORE_SCHEMA,
    GovernanceDecisionEvent,
    GovernanceDecisionStorePlanError,
    build_temporary_decision_store,
    generate_governance_decision_store_plan,
    legacy_event_from_review_row,
    normalize_event_payload,
    verify_decision_hash_chain,
)


CREATED_AT = "2026-07-19T15:00:00+08:00"
REVIEWED_AT = "2026-07-18T00:33:08+08:00"


def test_event_normalization_and_id_are_deterministic():
    event = _event(new_value={"b": 2, "a": 1})

    first = normalize_event_payload(event)
    second = normalize_event_payload(_event(new_value={"a": 1, "b": 2}))

    assert first == second
    assert event.idempotency_key == _event(new_value={"a": 1, "b": 2}).idempotency_key


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_append_only_update_and_delete_are_blocked(tmp_path, operation):
    path = tmp_path / "decisions.sqlite"
    build_temporary_decision_store(path, [_event()])
    connection = sqlite3.connect(path)
    try:
        sql = (
            "UPDATE decision_events SET decision_reason='changed'"
            if operation == "update"
            else "DELETE FROM decision_events"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(sql)
    finally:
        connection.close()


def test_duplicate_event_is_idempotent(tmp_path):
    event = _event()
    result = build_temporary_decision_store(tmp_path / "decisions.sqlite", [event, event])

    assert result["event_count"] == 1
    assert result["duplicate_event_count"] == 1


def test_legacy_metadata_is_preserved_and_missing_values_are_explicit():
    preserved = legacy_event_from_review_row(
        {
            "source_sheet": "Sheet",
            "source_row": "7",
            "record_type": "merchant_case",
            "review_decision": "approve_internal_only",
            "reviewer": "Legacy Reviewer",
            "reviewed_at": "2026-07-10T10:00:00+08:00",
            "current_issue": "Reviewed reason",
        },
        source_manifest_hash="abc",
        input_checksums={"legacy.csv": "abc"},
    )
    missing = legacy_event_from_review_row(
        {
            "source_sheet": "Sheet",
            "source_row": "8",
            "record_type": "merchant_case",
            "review_decision": "exclude",
            "reviewer": "",
            "reviewed_at": "",
            "current_issue": "",
        },
        source_manifest_hash="abc",
        input_checksums={"legacy.csv": "abc"},
    )

    assert preserved.reviewer == "Legacy Reviewer"
    assert preserved.reviewed_at == "2026-07-10T10:00:00+08:00"
    assert missing.reviewer == "legacy_reviewer_unavailable"
    assert missing.reviewed_at is None
    assert missing.decision_reason == "legacy_reason_unavailable"
    assert missing.provenance == "legacy_import"


def test_admin_is_required_only_for_new_resolution_events():
    with pytest.raises(GovernanceDecisionStorePlanError, match="Admin"):
        _event(provenance="admin_resolution", reviewer="Legacy Reviewer")

    legacy = _event(
        provenance="legacy_import",
        reviewer="Legacy Reviewer",
        reviewed_at="2026-07-10T10:00:00+08:00",
    )
    assert legacy.reviewer == "Legacy Reviewer"


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision_reason": ""},
        {"reviewed_at": "2026-07-18"},
        {"reviewed_at": "not-a-date"},
        {"reviewer": ""},
    ],
)
def test_new_events_fail_closed_on_invalid_review_metadata(overrides):
    with pytest.raises(GovernanceDecisionStorePlanError):
        _event(**overrides)


def test_existing_subject_requires_explicit_supersede(tmp_path):
    first = _event()
    replacement_without_link = _event(
        new_value={"review_decision": "exclude"},
        source_manifest_hash="replacement-manifest",
    )

    with pytest.raises(GovernanceDecisionStorePlanError, match="supersede/revoke"):
        build_temporary_decision_store(
            tmp_path / "decisions.sqlite",
            [first, replacement_without_link],
        )


def test_current_state_projections_supersede_and_revoke(tmp_path):
    original = _event(new_value={"review_decision": "exclude"})
    superseding = _event(
        action="supersede",
        new_value={"review_decision": "approve_internal_only"},
        supersedes_event_id=original.event_id,
        source_manifest_hash="resolution-manifest",
    )
    path = tmp_path / "decisions.sqlite"
    build_temporary_decision_store(path, [original, superseding])

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT new_value_json, event_id FROM current_parent_decisions"
        ).fetchone()
        assert json.loads(row[0])["review_decision"] == "approve_internal_only"
        revoke = _event(
            action="revoke",
            new_value=None,
            supersedes_event_id=row[1],
            source_manifest_hash="revoke-manifest",
        )
    finally:
        connection.close()

    build_temporary_decision_store(path, [original, superseding, revoke])
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM current_parent_decisions").fetchone()[0] == 0
    finally:
        connection.close()


def test_asset_hold_is_not_overridden_by_parent_approval(tmp_path):
    parent = _event(new_value={"review_decision": "approve"})
    asset = _event(
        event_type="asset_eligibility",
        subject_type="asset",
        subject_id="Sheet:r12:video",
        record_id="Sheet:r12",
        asset_id="Sheet:r12:video",
        field_name="asset_eligibility",
        action="hold",
        new_value={
            "asset_index_eligibility": "hold",
            "asset_search_eligibility": "not_searchable",
        },
        source_manifest_hash="asset-manifest",
    )
    path = tmp_path / "decisions.sqlite"
    build_temporary_decision_store(path, [parent, asset])

    connection = sqlite3.connect(path)
    try:
        value = connection.execute(
            "SELECT new_value_json FROM current_asset_eligibility"
        ).fetchone()[0]
        assert json.loads(value)["asset_search_eligibility"] == "not_searchable"
        assert connection.execute("SELECT COUNT(*) FROM current_parent_decisions").fetchone()[0] == 1
    finally:
        connection.close()


def test_alias_projection_supports_multiple_parents_and_is_not_a_tag(tmp_path):
    aliases = [
        _event(
            event_type="search_alias",
            subject_type="search_alias",
            subject_id=f"Sheet:r{row}|shopline payments",
            record_id=f"Sheet:r{row}",
            field_name="search_aliases",
            action="add",
            new_value={"alias": "SHOPLINE Payments", "normalized_alias": "shopline payments"},
            source_manifest_hash=f"alias-{row}",
        )
        for row in (32, 99)
    ]
    path = tmp_path / "decisions.sqlite"
    build_temporary_decision_store(path, aliases)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM current_search_aliases "
            "WHERE json_extract(new_value_json, '$.normalized_alias')='shopline payments'"
        ).fetchone()[0] == 2
        assert "content_tags" not in DECISION_STORE_SCHEMA
    finally:
        connection.close()


def test_hash_chain_and_tamper_detection(tmp_path):
    path = tmp_path / "decisions.sqlite"
    build_temporary_decision_store(path, [_event(), _event(subject_id="Sheet:r8")])
    assert verify_decision_hash_chain(path)["valid"] is True

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TRIGGER decision_events_no_update")
        connection.execute(
            "UPDATE decision_events SET new_value_json='{}' WHERE event_sequence=1"
        )
        connection.commit()
    finally:
        connection.close()

    assert verify_decision_hash_chain(path)["valid"] is False


def test_temporary_store_runs_integrity_fk_readonly_rollback_and_backup(tmp_path):
    result = build_temporary_decision_store(
        tmp_path / "decisions.sqlite",
        [_event(), _event(subject_id="Sheet:r8")],
    )

    assert result["integrity_check"] == "ok"
    assert result["foreign_key_errors"] == 0
    assert result["read_only_reopen"] is True
    assert result["transaction_rollback"] is True
    assert result["backup_restore"] is True
    assert result["hash_chain_valid"] is True


def test_real_plan_inventory_counts_and_blocks_untraceable_formal_records(tmp_path):
    paths = _real_paths(tmp_path)
    summary = generate_governance_decision_store_plan(**paths, created_at=CREATED_AT)

    assert summary["conclusion"] == "C. Not ready to create Decision Store"
    assert summary["baseline_event_count"] == 46
    assert summary["resolution_event_count"] == 19
    assert summary["asset_url_reference_event_count"] == 1
    assert summary["formal_document_count"] == 108
    assert summary["formal_documents_with_explicit_decision"] == 12
    assert summary["formal_documents_without_explicit_decision"] == 96
    assert summary["formal_vault_document_count"] == 109
    assert summary["formal_vault_with_explicit_decision"] == 13
    assert summary["formal_vault_without_explicit_decision"] == 96
    assert summary["eligible_asset_count"] == 205
    assert summary["hold_asset_count"] == 1
    assert summary["excluded_asset_count"] == 16
    assert summary["approved_url_field_count"] == 410
    assert summary["parent_sync_candidate_count"] == 4
    assert summary["excluded_parent_sync_count"] == 1
    assert summary["execution_blocked"] is True
    assert summary["formal_data_modified"] is False
    assert summary["plan_id"].startswith("decision-store-plan-")
    assert summary["plan_id"] not in {
        "resolution-plan-a878e6d1036bac96",
        "asset-plan-07cd12338615c961",
    }

    manifest = json.loads((paths["output_dir"] / "decision_store_manifest.json").read_text())
    assert manifest["target_path"] == "data/governance/governance_decisions.sqlite"
    assert manifest["reviewer"] == "Admin"
    assert manifest["execution_blocked"] is True
    assert "formal_index_decision_coverage_incomplete" in manifest["blocker_reasons"]
    assert len([p for p in paths["output_dir"].iterdir() if p.is_file() and not p.name.startswith("._")]) == 17
    baseline = _read_csv(paths["output_dir"] / "baseline_import_preview.csv")
    assert len(baseline) == 142
    assert sum(row["baseline_status"] == "blocked_missing_authority" for row in baseline) == 96


def test_resolution_events_and_sync_readiness_are_exact(tmp_path):
    paths = _real_paths(tmp_path)
    generate_governance_decision_store_plan(**paths, created_at=CREATED_AT)

    events = _read_csv(paths["output_dir"] / "resolution_event_preview.csv")
    readiness = _read_csv(paths["output_dir"] / "parent_sync_readiness_preview.csv")
    assert len(events) == 19
    assert {row["reviewer"] for row in events} == {"Admin"}
    assert {row["record_id"] for row in readiness if row["sync_readiness"] == "syncable"} == {
        "商家夥伴案例資料庫:r7",
        "商家夥伴案例資料庫:r12",
        "商家夥伴案例資料庫:r32",
        "商家夥伴案例資料庫:r122",
    }
    excluded = next(row for row in readiness if row["record_id"].endswith(":r30"))
    assert excluded["sync_readiness"] == "not_syncable"
    assert excluded["index_eligibility"] == "excluded"
    assert excluded["search_eligibility"] == "not_searchable"


def test_plan_rerun_is_deterministic_and_protected_inputs_unchanged(tmp_path):
    paths = _real_paths(tmp_path)
    protected_before = _protected_hashes(paths)
    first = generate_governance_decision_store_plan(**paths, created_at=CREATED_AT)
    first_outputs = _directory_hashes(paths["output_dir"])
    second = generate_governance_decision_store_plan(**paths, created_at=CREATED_AT)

    assert first == second
    assert first_outputs == _directory_hashes(paths["output_dir"])
    assert protected_before == _protected_hashes(paths)


def test_plan_cli_is_preview_only(tmp_path, capsys):
    paths = _real_paths(tmp_path)
    args = [
        "governance-decision-store",
        "--plan",
        "--output",
        str(paths["output_dir"]),
        "--created-at",
        CREATED_AT,
    ]
    exit_code = main(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["execution_blocked"] is True
    assert payload["formal_data_modified"] is False
    assert not Path("data/governance/governance_decisions.sqlite").exists()


def _event(**overrides):
    values = {
        "event_type": "parent_review_decision",
        "subject_type": "parent",
        "subject_id": "Sheet:r7",
        "record_id": "Sheet:r7",
        "asset_id": None,
        "field_name": "review_decision",
        "action": "approve",
        "previous_value": None,
        "new_value": {"review_decision": "approve"},
        "reviewer": "Admin",
        "reviewed_at": REVIEWED_AT,
        "decision_reason": "Human-approved resolution",
        "provenance": "admin_resolution",
        "source_plan_id": "decision-store-plan-test",
        "source_manifest_hash": "manifest-test",
        "input_checksums": {"input.csv": "abc"},
        "supersedes_event_id": None,
        "created_at": CREATED_AT,
        "code_version": "test-v1",
    }
    values.update(overrides)
    return GovernanceDecisionEvent(**values)


def _real_paths(tmp_path):
    root = Path(__file__).resolve().parents[1]
    return {
        "review_decisions_path": root / "reports/excel_preview/review_decisions_template.csv",
        "merchant_cases_path": root / "reports/excel_preview/merchant_cases.json",
        "public_metrics_path": root / "reports/excel_preview/public_metrics.json",
        "pending_metrics_path": root / "reports/excel_preview/pending_metrics.json",
        "restricted_customers_path": root / "reports/excel_preview/restricted_customers.json",
        "asset_decisions_path": root / "reports/asset_metadata_preview/human_review_template.csv",
        "asset_validation_path": root
        / "reports/asset_metadata_review_validation/review_decision_status.csv",
        "asset_apply_preview_path": root
        / "reports/asset_metadata_apply_preview/asset_apply_preview.csv",
        "asset_blocked_preview_path": root
        / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv",
        "resolution_dir": root / "reports/missing_parent_resolution_preview",
        "formal_vault_path": root / "obsidian_vault",
        "formal_db_path": root / ".mka/content_index.sqlite",
        "production_renderer_path": root / "src/marketing_knowledge_agent/slack_interface.py",
        "output_dir": tmp_path / "governance_decision_store_plan",
    }


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(str(child.relative_to(path)).encode())
            digest.update(child.read_bytes())
    return digest.hexdigest()


def _protected_hashes(paths):
    keys = [
        "review_decisions_path",
        "merchant_cases_path",
        "asset_decisions_path",
        "formal_vault_path",
        "formal_db_path",
        "production_renderer_path",
    ]
    return {key: _hash_path(paths[key]) for key in keys}


def _directory_hashes(path):
    return {
        child.name: hashlib.sha256(child.read_bytes()).hexdigest()
        for child in path.iterdir()
        if child.is_file() and not child.name.startswith("._")
    }
