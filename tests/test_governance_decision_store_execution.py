import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance_decision_store_execution import (
    CONFIRMATION_ID,
    CONFIRMATION_ROOT_HASH,
    MANIFEST_HASH,
    PLAN_ID,
    GovernanceDecisionStoreExecutionError,
    execute_governance_decision_store_plan,
    validate_governance_decision_store_execution,
)


NOW = "2026-07-20T12:30:00+08:00"


def test_confirmed_plan_is_blocked_by_missing_execution_schema_contract(tmp_path):
    result = validate_governance_decision_store_execution(**_args(tmp_path))

    assert result["preflight_valid"] is False
    assert result["execution_blocked"] is True
    assert result["plan_id"] == PLAN_ID
    assert result["confirmation_id"] == CONFIRMATION_ID
    assert result["missing_decision_event_columns"] == [
        "source_confirmation_id",
        "source_confirmation_root_hash",
    ]
    assert result["missing_tables"] == ["execution_metadata", "schema_metadata"]
    assert result["formal_target_absent"] is True
    assert result["event_count_revalidated"] == 162
    assert result["current_parent_state_revalidated"] == 120


@pytest.mark.parametrize(("field", "value", "message"), [
    ("plan_id", "latest", "PLAN_ID"),
    ("manifest_hash", "0" * 64, "Manifest Hash"),
    ("confirmation_id", "latest", "Confirmation ID"),
    ("confirmation_root_hash", "0" * 64, "Confirmation Root Hash"),
])
def test_exact_execution_authority_is_required(tmp_path, field, value, message):
    args = _args(tmp_path)
    args[field] = value
    with pytest.raises(GovernanceDecisionStoreExecutionError, match=message):
        validate_governance_decision_store_execution(**args)


@pytest.mark.parametrize("obsolete", [
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
])
def test_obsolete_plans_are_rejected(tmp_path, obsolete):
    args = _args(tmp_path)
    args["plan_id"] = obsolete
    with pytest.raises(GovernanceDecisionStoreExecutionError, match="obsolete"):
        validate_governance_decision_store_execution(**args)


def test_blocked_execution_writes_failure_reports_only(tmp_path):
    args = _args(tmp_path)
    result = execute_governance_decision_store_plan(**args)

    assert result["conclusion"] == "C. Execution blocked or rolled back"
    assert result["formal_database_created"] is False
    assert result["execution_bundle_created"] is False
    assert len([path for path in args["report_dir"].iterdir() if path.is_file()]) == 19
    assert not args["formal_target_path"].exists()
    assert not args["execution_bundle_path"].exists()


def test_cli_fails_closed_without_creating_formal_database(tmp_path, capsys):
    target = tmp_path / "governance_decisions.sqlite"
    execution = tmp_path / "execution"
    reports = tmp_path / "reports"

    assert main([
        "execute-governance-decision-store-plan",
        "--plan-id", PLAN_ID,
        "--manifest-hash", MANIFEST_HASH,
        "--confirmation-id", CONFIRMATION_ID,
        "--confirmation-root-hash", CONFIRMATION_ROOT_HASH,
        "--target", str(target),
        "--execution-bundle", str(execution),
        "--output", str(reports),
        "--executed-at", NOW,
    ]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_blocked"] is True
    assert not target.exists()
    assert not execution.exists()


def _args(tmp_path):
    root = Path(__file__).resolve().parents[1]
    return {
        "repo_root": root,
        "plan_id": PLAN_ID,
        "manifest_hash": MANIFEST_HASH,
        "confirmation_id": CONFIRMATION_ID,
        "confirmation_root_hash": CONFIRMATION_ROOT_HASH,
        "formal_target_path": tmp_path / "governance_decisions.sqlite",
        "execution_bundle_path": tmp_path / "execution",
        "report_dir": tmp_path / "reports",
        "executed_at": NOW,
    }
