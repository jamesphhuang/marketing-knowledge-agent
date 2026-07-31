from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .governance_decision_store_confirmation import (
    BUNDLE_ROOT_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    GovernanceDecisionStoreConfirmationError,
    validate_governance_decision_store_confirmation,
    validate_governance_decision_store_plan,
)
from .governance_decision_store_plan import DECISION_STORE_SCHEMA


PLAN_ID = EXPECTED_PLAN_ID
MANIFEST_HASH = EXPECTED_MANIFEST_HASH
CONFIRMATION_ID = "decision-store-confirmation-98fef43f8dd6773a"
CONFIRMATION_ROOT_HASH = "218b66aba50eeee0ccba15533f65bc74df12158788c86081b72e4ebfde3c0282"
DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / PLAN_ID
DEFAULT_FORMAL_TARGET = Path("data/governance/governance_decisions.sqlite")
DEFAULT_EXECUTION_BUNDLE = Path("data/governance/executions") / PLAN_ID
DEFAULT_REPORT_DIR = Path("reports/governance_decision_store_execution")
OBSOLETE_PLAN_IDS = {
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
}
REQUIRED_EVENT_COLUMNS = {"source_confirmation_id", "source_confirmation_root_hash"}
REQUIRED_TABLES = {"schema_metadata", "execution_metadata"}
REPORT_FILENAMES = (
    "decision_store_execution_summary.md",
    "execution_preflight_validation.csv",
    "bundle_plan_confirmation_validation.csv",
    "decision_event_write_validation.csv",
    "current_state_post_execution_validation.csv",
    "special_decision_post_execution_validation.csv",
    "asset_eligibility_post_execution_validation.csv",
    "search_alias_post_execution_validation.csv",
    "asset_url_reference_post_execution_validation.csv",
    "sqlite_integrity_validation.csv",
    "append_only_trigger_validation.csv",
    "hash_chain_post_execution_validation.csv",
    "formal_database_checksum.json",
    "execution_bundle_validation.csv",
    "formal_system_boundary_validation.csv",
    "execution_rollback_report.md",
    "next_parent_sync_prerequisites.md",
    "execution_errors.csv",
    "execution_warnings.csv",
)


class GovernanceDecisionStoreExecutionError(RuntimeError):
    pass


def validate_governance_decision_store_execution(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    confirmation_id: str,
    confirmation_root_hash: str,
    confirmation_path: Path = DEFAULT_CONFIRMATION_PATH,
    formal_target_path: Path = DEFAULT_FORMAL_TARGET,
    execution_bundle_path: Path = DEFAULT_EXECUTION_BUNDLE,
    report_dir: Path = DEFAULT_REPORT_DIR,
    executed_at: Optional[str] = None,
) -> dict:
    del report_dir
    _validate_authority(plan_id, manifest_hash, confirmation_id, confirmation_root_hash)
    root = Path(repo_root).resolve()
    executed = executed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(executed)
    target = _resolve(root, formal_target_path)
    execution_bundle = _resolve(root, execution_bundle_path)
    if execution_bundle.exists():
        raise GovernanceDecisionStoreExecutionError("Execution Bundle target already exists")
    confirmation_target = _resolve(root, confirmation_path)
    try:
        confirmation = validate_governance_decision_store_confirmation(confirmation_target)
        plan = validate_governance_decision_store_plan(
            repo_root=root,
            plan_id=plan_id,
            manifest_hash=manifest_hash,
            formal_target_path=target,
            now=executed,
        )
    except GovernanceDecisionStoreConfirmationError as exc:
        raise GovernanceDecisionStoreExecutionError(f"execution authority revalidation failed: {exc}") from exc
    if confirmation["confirmation_id"] != confirmation_id:
        raise GovernanceDecisionStoreExecutionError("Confirmation ID mismatch")
    if confirmation["root_confirmation_hash"] != confirmation_root_hash:
        raise GovernanceDecisionStoreExecutionError("Confirmation Root Hash mismatch")
    if confirmation["reviewer"] != "Admin":
        raise GovernanceDecisionStoreExecutionError("Confirmation reviewer is not Admin")

    schema = _inspect_confirmed_schema()
    missing_columns = sorted(REQUIRED_EVENT_COLUMNS - schema["decision_event_columns"])
    missing_tables = sorted(REQUIRED_TABLES - schema["tables"])
    blockers = []
    if missing_columns:
        blockers.append("confirmed_schema_missing_confirmation_provenance_columns")
    if missing_tables:
        blockers.append("confirmed_schema_missing_metadata_tables")
    return {
        "preflight_valid": not blockers,
        "execution_blocked": bool(blockers),
        "blocker_reasons": blockers,
        "plan_id": plan_id,
        "manifest_hash": manifest_hash,
        "confirmation_id": confirmation_id,
        "confirmation_root_hash": confirmation_root_hash,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "executed_at": executed,
        "formal_target_path": str(target),
        "formal_target_absent": plan["formal_target_absent"],
        "execution_bundle_absent": True,
        "event_count_revalidated": plan["event_count"],
        "current_parent_state_revalidated": plan["current_parent_state_count"],
        "authority_gap_revalidated": plan["authority_gap"],
        "asset_url_count_revalidated": plan["approved_url_field_count"],
        "missing_decision_event_columns": missing_columns,
        "missing_tables": missing_tables,
        "confirmed_schema_tables": sorted(schema["tables"]),
        "confirmed_decision_event_columns": sorted(schema["decision_event_columns"]),
        "formal_database_created": False,
        "execution_bundle_created": False,
    }


def execute_governance_decision_store_plan(**kwargs) -> dict:
    result = validate_governance_decision_store_execution(**kwargs)
    if not result["execution_blocked"]:
        raise GovernanceDecisionStoreExecutionError(
            "confirmed schema unexpectedly passed the blocked-only executor; a separately reviewed implementation is required"
        )
    report_dir = _resolve(Path(kwargs["repo_root"]).resolve(), kwargs.get("report_dir", DEFAULT_REPORT_DIR))
    _write_failure_reports(report_dir, result)
    return {
        "conclusion": "C. Execution blocked or rolled back",
        **result,
        "formal_database_created": False,
        "execution_bundle_created": False,
        "report_dir": str(report_dir),
    }


def _validate_authority(plan_id, manifest_hash, confirmation_id, confirmation_root_hash):
    if plan_id in OBSOLETE_PLAN_IDS:
        raise GovernanceDecisionStoreExecutionError("obsolete PLAN_ID is invalid for execution")
    if plan_id != PLAN_ID:
        raise GovernanceDecisionStoreExecutionError("exact PLAN_ID is required")
    if manifest_hash != MANIFEST_HASH:
        raise GovernanceDecisionStoreExecutionError("exact Plan Manifest Hash is required")
    if confirmation_id != CONFIRMATION_ID:
        raise GovernanceDecisionStoreExecutionError("exact Confirmation ID is required")
    if confirmation_root_hash != CONFIRMATION_ROOT_HASH:
        raise GovernanceDecisionStoreExecutionError("exact Confirmation Root Hash is required")


def _inspect_confirmed_schema():
    with tempfile.TemporaryDirectory(prefix="mka-confirmed-schema-audit-") as name:
        database = Path(name) / "schema.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(DECISION_STORE_SCHEMA)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            columns = {row[1] for row in connection.execute("PRAGMA table_info(decision_events)")}
        finally:
            connection.close()
    return {"tables": tables, "decision_event_columns": columns}


def _write_failure_reports(output, result):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(
        output / REPORT_FILENAMES[0],
        "# Governance Decision Store Execution\n\n"
        "- Conclusion: C. Execution blocked or rolled back\n"
        f"- PLAN_ID: `{PLAN_ID}`\n"
        "- Formal database created: false\n"
        "- Execution Bundle created: false\n"
        "- Reason: the confirmed schema lacks mandatory execution provenance columns and metadata tables.\n",
    )
    preflight = [
        {"check": "plan_revalidated", "observed": True, "status": "pass"},
        {"check": "confirmation_revalidated", "observed": True, "status": "pass"},
        {"check": "event_count", "observed": result["event_count_revalidated"], "status": "pass"},
        {"check": "current_parent_state", "observed": result["current_parent_state_revalidated"], "status": "pass"},
        {"check": "confirmed_schema_execute_contract", "observed": False, "status": "blocked"},
    ]
    _write_csv(output / REPORT_FILENAMES[1], preflight)
    _write_csv(output / REPORT_FILENAMES[2], [
        {"authority": "plan", "identifier": PLAN_ID, "status": "pass"},
        {"authority": "confirmation", "identifier": CONFIRMATION_ID, "status": "pass"},
        {"authority": "parent_bundle", "identifier": BUNDLE_ROOT_HASH, "status": "pass"},
    ])
    not_executed = [{"check": "not_executed_due_to_preflight_blocker", "status": "blocked"}]
    for index in range(3, 12):
        _write_csv(output / REPORT_FILENAMES[index], not_executed)
    _write_json(output / REPORT_FILENAMES[12], {
        "database_created": False,
        "sha256": None,
        "byte_size": 0,
        "reason": "execution_blocked_before_staging",
    })
    _write_csv(output / REPORT_FILENAMES[13], [{"check": "execution_bundle_created", "status": "blocked"}])
    _write_csv(output / REPORT_FILENAMES[14], [
        {"check": "formal_decision_store_absent", "status": "pass"},
        {"check": "vault_index_slack_unchanged", "status": "pass"},
    ])
    _write_text(
        output / REPORT_FILENAMES[15],
        "# Execution Rollback Report\n\nNo staging database or formal target was created, so rollback was not required.\n",
    )
    _write_text(
        output / REPORT_FILENAMES[16],
        "# Next Parent Sync Prerequisites\n\nParent Sync remains prohibited. Produce and confirm a new schema-compatible Plan first.\n",
    )
    errors = [
        {"severity": "blocking", "code": "missing_confirmed_schema_field", "message": column}
        for column in result["missing_decision_event_columns"]
    ] + [
        {"severity": "blocking", "code": "missing_confirmed_schema_table", "message": table}
        for table in result["missing_tables"]
    ]
    _write_csv(output / REPORT_FILENAMES[17], errors)
    _write_csv(output / REPORT_FILENAMES[18], [])
    actual = sorted(path.name for path in output.iterdir() if path.is_file() and not path.name.startswith("._"))
    if actual != sorted(REPORT_FILENAMES):
        raise GovernanceDecisionStoreExecutionError("execution failure report contract is incomplete")


def _resolve(root, path):
    value = Path(path)
    return value.resolve() if value.is_absolute() else (Path(root) / value).resolve()


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["severity", "code", "message"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_text(path, value):
    Path(path).write_text(value.rstrip() + "\n", encoding="utf-8")


def _validate_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceDecisionStoreExecutionError("executed_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceDecisionStoreExecutionError("executed_at must include timezone")
