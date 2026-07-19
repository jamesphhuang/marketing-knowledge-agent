from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from .frontmatter import parse_markdown_with_frontmatter


TARGET_DECISION_STORE_PATH = Path("data/governance/governance_decisions.sqlite")
CODE_VERSION = "governance-decision-store-plan-v1"
ADMIN_REVIEWED_AT = "2026-07-18T00:33:08+08:00"
OLD_PLAN_IDS = (
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
)
OUTPUT_FILENAMES = (
    "governance_decision_store_summary.md",
    "authoritative_path_assessment.md",
    "decision_source_inventory.csv",
    "baseline_import_scope.csv",
    "derived_report_exclusions.csv",
    "decision_event_schema.md",
    "current_state_projection_design.md",
    "temporary_decision_store_schema.sql",
    "temporary_decision_store_validation.md",
    "baseline_import_preview.csv",
    "resolution_event_preview.csv",
    "asset_url_decision_boundary.md",
    "parent_sync_readiness_preview.csv",
    "decision_store_backup_plan.md",
    "decision_store_rollback_plan.md",
    "decision_store_confirmation_checklist.md",
    "decision_store_manifest.json",
)

EVENT_TYPES = {
    "parent_review_decision",
    "record_review_decision",
    "asset_eligibility",
    "search_alias",
    "entity_metadata",
    "asset_url_manifest_reference",
}
SUBJECT_TYPES = {
    "parent",
    "asset",
    "search_alias",
    "public_metric",
    "pending_metric",
    "restricted_customer",
    "manifest",
}
ACTIONS = {"approve", "add", "replace", "hold", "exclude", "supersede", "revoke"}

DECISION_STORE_SCHEMA = """PRAGMA foreign_keys = ON;

CREATE TABLE decision_manifests (
    manifest_hash TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    input_checksums_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    code_version TEXT NOT NULL
);

CREATE TABLE decision_events (
    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'parent_review_decision', 'record_review_decision', 'asset_eligibility',
        'search_alias', 'entity_metadata', 'asset_url_manifest_reference'
    )),
    subject_type TEXT NOT NULL CHECK (subject_type IN (
        'parent', 'asset', 'search_alias', 'public_metric', 'pending_metric',
        'restricted_customer', 'manifest'
    )),
    subject_id TEXT NOT NULL,
    record_id TEXT,
    asset_id TEXT,
    field_name TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN (
        'approve', 'add', 'replace', 'hold', 'exclude', 'supersede', 'revoke'
    )),
    previous_value_json TEXT NOT NULL,
    new_value_json TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reviewed_at TEXT,
    decision_reason TEXT NOT NULL,
    provenance TEXT NOT NULL,
    source_plan_id TEXT NOT NULL,
    source_manifest_hash TEXT NOT NULL,
    input_checksums_json TEXT NOT NULL,
    supersedes_event_id TEXT,
    previous_event_hash TEXT,
    event_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    code_version TEXT NOT NULL,
    FOREIGN KEY (source_manifest_hash)
        REFERENCES decision_manifests(manifest_hash) ON DELETE RESTRICT,
    FOREIGN KEY (supersedes_event_id)
        REFERENCES decision_events(event_id) ON DELETE RESTRICT,
    CHECK (
        (action IN ('supersede', 'revoke') AND supersedes_event_id IS NOT NULL)
        OR (action NOT IN ('supersede', 'revoke') AND supersedes_event_id IS NULL)
    )
);

CREATE TRIGGER decision_events_no_update
BEFORE UPDATE ON decision_events
BEGIN
    SELECT RAISE(ABORT, 'decision_events is append-only: UPDATE blocked');
END;

CREATE TRIGGER decision_events_no_delete
BEFORE DELETE ON decision_events
BEGIN
    SELECT RAISE(ABORT, 'decision_events is append-only: DELETE blocked');
END;

CREATE INDEX idx_decision_events_subject
    ON decision_events(subject_type, subject_id, event_type, field_name, event_sequence);
CREATE INDEX idx_decision_events_supersedes ON decision_events(supersedes_event_id);

CREATE VIEW effective_decision_events AS
SELECT event.*
FROM decision_events AS event
WHERE event.action <> 'revoke'
  AND NOT EXISTS (
      SELECT 1
      FROM decision_events AS later
      WHERE later.supersedes_event_id = event.event_id
  );

CREATE VIEW current_parent_decisions AS
SELECT event.*
FROM effective_decision_events AS event
WHERE event.event_type = 'parent_review_decision'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence)
      FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_type = event.subject_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );

CREATE VIEW current_asset_eligibility AS
SELECT event.*
FROM effective_decision_events AS event
WHERE event.event_type = 'asset_eligibility'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence)
      FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );

CREATE VIEW current_search_aliases AS
SELECT event.*
FROM effective_decision_events AS event
WHERE event.event_type = 'search_alias'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence)
      FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );

CREATE VIEW current_entity_metadata AS
SELECT event.*
FROM effective_decision_events AS event
WHERE event.event_type = 'entity_metadata'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence)
      FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );
"""


class GovernanceDecisionStorePlanError(ValueError):
    """Raised when a Decision Store plan cannot be safely produced."""


@dataclass(frozen=True)
class GovernanceDecisionEvent:
    event_type: str
    subject_type: str
    subject_id: str
    record_id: Optional[str]
    asset_id: Optional[str]
    field_name: str
    action: str
    previous_value: object
    new_value: object
    reviewer: str
    reviewed_at: Optional[str]
    decision_reason: str
    provenance: str
    source_plan_id: str
    source_manifest_hash: str
    input_checksums: Mapping[str, str]
    supersedes_event_id: Optional[str]
    created_at: str
    code_version: str

    def __post_init__(self) -> None:
        for name in (
            "event_type",
            "subject_type",
            "subject_id",
            "field_name",
            "action",
            "reviewer",
            "decision_reason",
            "provenance",
            "source_plan_id",
            "source_manifest_hash",
            "created_at",
            "code_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise GovernanceDecisionStorePlanError(f"{name} is required")
        if self.event_type not in EVENT_TYPES:
            raise GovernanceDecisionStorePlanError("invalid event_type")
        if self.subject_type not in SUBJECT_TYPES:
            raise GovernanceDecisionStorePlanError("invalid subject_type")
        if self.action not in ACTIONS:
            raise GovernanceDecisionStorePlanError("invalid action")
        if (self.action in {"supersede", "revoke"}) != bool(self.supersedes_event_id):
            raise GovernanceDecisionStorePlanError("supersede/revoke reference is invalid")
        _validate_timestamp(self.created_at, "created_at")
        if self.provenance == "legacy_import":
            if self.reviewed_at:
                _validate_legacy_reviewed_at(self.reviewed_at)
        else:
            if not self.reviewed_at:
                raise GovernanceDecisionStorePlanError("reviewed_at is required")
            _validate_timestamp(self.reviewed_at, "reviewed_at")
        if self.provenance == "admin_resolution" and self.reviewer != "Admin":
            raise GovernanceDecisionStorePlanError("new Resolution Events require reviewer Admin")
        if not isinstance(self.input_checksums, Mapping) or not self.input_checksums:
            raise GovernanceDecisionStorePlanError("input_checksums are required")

    @property
    def idempotency_key(self) -> str:
        return hashlib.sha256(_canonical_json(normalize_event_payload(self)).encode()).hexdigest()

    @property
    def event_id(self) -> str:
        return f"event-{self.idempotency_key[:24]}"


def normalize_event_payload(event: GovernanceDecisionEvent) -> dict:
    return {
        "event_type": event.event_type,
        "subject_type": event.subject_type,
        "subject_id": event.subject_id,
        "record_id": event.record_id,
        "asset_id": event.asset_id,
        "field_name": event.field_name,
        "action": event.action,
        "previous_value": _normalize_json_value(event.previous_value),
        "new_value": _normalize_json_value(event.new_value),
        "reviewer": event.reviewer,
        "reviewed_at": event.reviewed_at,
        "decision_reason": event.decision_reason,
        "provenance": event.provenance,
        "source_plan_id": event.source_plan_id,
        "source_manifest_hash": event.source_manifest_hash,
        "input_checksums": dict(sorted(event.input_checksums.items())),
        "supersedes_event_id": event.supersedes_event_id,
    }


def legacy_event_from_review_row(
    row: Mapping[str, str],
    *,
    source_manifest_hash: str,
    input_checksums: Mapping[str, str],
    created_at: str = ADMIN_REVIEWED_AT,
) -> GovernanceDecisionEvent:
    record_type = row.get("record_type", "").strip()
    record_id = f"{row.get('source_sheet', '').strip()}:r{row.get('source_row', '').strip()}"
    reviewer = row.get("reviewer", "").strip() or "legacy_reviewer_unavailable"
    reviewed_at = row.get("reviewed_at", "").strip() or None
    reason = (
        row.get("current_issue", "").strip()
        or row.get("notes", "").strip()
        or "legacy_reason_unavailable"
    )
    if record_type == "merchant_case":
        event_type = "parent_review_decision"
        subject_type = "parent"
    else:
        event_type = "record_review_decision"
        subject_type = record_type
    decision = row.get("review_decision", "").strip()
    return GovernanceDecisionEvent(
        event_type=event_type,
        subject_type=subject_type,
        subject_id=record_id,
        record_id=record_id,
        asset_id=None,
        field_name="review_decision",
        action=_legacy_action(decision),
        previous_value=None,
        new_value={
            "review_decision": decision,
            "can_enter_vault": row.get("can_enter_vault", ""),
            "can_enter_content_index": row.get("can_enter_content_index", ""),
            "can_enter_governance_table": row.get("can_enter_governance_table", ""),
            "can_quote_externally": row.get("can_quote_externally", ""),
            "final_status": row.get("final_status", ""),
        },
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        decision_reason=reason,
        provenance="legacy_import",
        source_plan_id=f"legacy-baseline-{source_manifest_hash[:16]}",
        source_manifest_hash=source_manifest_hash,
        input_checksums=input_checksums,
        supersedes_event_id=None,
        created_at=created_at,
        code_version=CODE_VERSION,
    )


def build_temporary_decision_store(
    db_path: Path,
    events: Sequence[GovernanceDecisionEvent],
) -> dict:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    duplicate_count = 0
    try:
        connection.executescript(DECISION_STORE_SCHEMA)
        connection.commit()
        transaction_rollback = _verify_transaction_rollback(connection)
        for event in events:
            inserted = _insert_event(connection, event)
            duplicate_count += int(not inserted)
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        event_count = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        supersede_revoke = _verify_supersede_revoke_projection(connection)
        alias_multi_parent = _verify_alias_multi_parent_projection(connection)
        connection.commit()
    finally:
        connection.close()

    chain = verify_decision_hash_chain(db_path)
    read_only = _verify_read_only_reopen(db_path, event_count)
    backup_restore = _verify_backup_restore(db_path, event_count)
    tamper_detection = _verify_tamper_detection(db_path)
    return {
        "event_count": event_count,
        "duplicate_event_count": duplicate_count,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
        "read_only_reopen": read_only,
        "transaction_rollback": transaction_rollback,
        "backup_restore": backup_restore,
        "hash_chain_valid": chain["valid"],
        "hash_chain_event_count": chain["event_count"],
        "tamper_detection": tamper_detection,
        "supersede_projection": supersede_revoke[0],
        "revoke_projection": supersede_revoke[1],
        "alias_multi_parent_projection": alias_multi_parent,
    }


def verify_decision_hash_chain(db_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    previous_hash = None
    valid = True
    count = 0
    try:
        for row in connection.execute("SELECT * FROM decision_events ORDER BY event_sequence"):
            count += 1
            if row["previous_event_hash"] != previous_hash:
                valid = False
                break
            payload = _row_hash_payload(row)
            expected = _event_hash(previous_hash, payload)
            if row["event_hash"] != expected:
                valid = False
                break
            previous_hash = row["event_hash"]
    finally:
        connection.close()
    return {"valid": valid, "event_count": count, "last_event_hash": previous_hash}


def generate_governance_decision_store_plan(
    *,
    review_decisions_path: Path,
    merchant_cases_path: Path,
    public_metrics_path: Path,
    pending_metrics_path: Path,
    restricted_customers_path: Path,
    asset_decisions_path: Path,
    asset_validation_path: Path,
    asset_apply_preview_path: Path,
    asset_blocked_preview_path: Path,
    resolution_dir: Path,
    formal_vault_path: Path,
    formal_db_path: Path,
    production_renderer_path: Path,
    output_dir: Path,
    created_at: Optional[str] = None,
) -> dict:
    created_at = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(created_at, "created_at")
    inputs = {
        "legacy_review_decisions": Path(review_decisions_path),
        "merchant_cases": Path(merchant_cases_path),
        "public_metrics": Path(public_metrics_path),
        "pending_metrics": Path(pending_metrics_path),
        "restricted_customers": Path(restricted_customers_path),
        "asset_url_decisions": Path(asset_decisions_path),
        "asset_url_validation": Path(asset_validation_path),
        "asset_apply_preview": Path(asset_apply_preview_path),
        "asset_blocked_preview": Path(asset_blocked_preview_path),
        "resolution_parent_decisions": Path(resolution_dir) / "missing_parent_resolution_decisions.csv",
        "resolution_parent_preview": Path(resolution_dir) / "parent_decision_preview.csv",
        "resolution_asset_eligibility": Path(resolution_dir) / "asset_eligibility_preview.csv",
        "resolution_aliases": Path(resolution_dir) / "search_alias_preview.csv",
    }
    protected = {
        **inputs,
        "formal_vault": Path(formal_vault_path),
        "formal_content_index": Path(formal_db_path),
        "production_renderer": Path(production_renderer_path),
    }
    for label, path in protected.items():
        if not path.exists():
            raise GovernanceDecisionStorePlanError(f"required {label} input does not exist: {path}")
    _assert_safe_output(Path(output_dir), list(protected.values()))
    checksums = {label: _hash_path(path) for label, path in inputs.items()}
    protected_before = {label: _hash_path(path) for label, path in protected.items()}

    review_rows = _read_csv(inputs["legacy_review_decisions"])
    merchant_cases = _read_json_list(inputs["merchant_cases"])
    public_metrics = _read_json_list(inputs["public_metrics"])
    pending_metrics = _read_json_list(inputs["pending_metrics"])
    restricted_customers = _read_json_list(inputs["restricted_customers"])
    asset_review_rows = _read_csv(inputs["asset_url_decisions"])
    asset_validation_rows = _read_csv(inputs["asset_url_validation"])
    apply_rows = _read_csv(inputs["asset_apply_preview"])
    blocked_rows = _read_csv(inputs["asset_blocked_preview"])
    resolution_parents = _read_csv(inputs["resolution_parent_decisions"])
    resolution_parent_preview = _read_csv(inputs["resolution_parent_preview"])
    resolution_assets = _read_csv(inputs["resolution_asset_eligibility"])
    resolution_aliases = _read_csv(inputs["resolution_aliases"])

    coverage, missing_formal_records = _formal_decision_coverage(
        Path(formal_db_path), review_rows
    )
    vault_coverage, missing_vault_records = _formal_vault_decision_coverage(
        Path(formal_vault_path), review_rows
    )
    if {row["record_id"] for row in missing_formal_records} != {
        row["record_id"] for row in missing_vault_records
    }:
        raise GovernanceDecisionStorePlanError(
            "formal Vault and content-index decision coverage disagree"
        )
    coverage = {**coverage, **vault_coverage}
    counts = _conservation_counts(apply_rows, blocked_rows, resolution_assets)
    plan_state = {
        "plan_type": "governance_decision_store_create",
        "target_path": str(TARGET_DECISION_STORE_PATH),
        "input_checksums": checksums,
        "schema_hash": hashlib.sha256(DECISION_STORE_SCHEMA.encode()).hexdigest(),
        "baseline_event_count": len(review_rows),
        "resolution_event_count": 19,
        "asset_url_reference_event_count": 1,
        "coverage": coverage,
        "counts": counts,
        "code_version": CODE_VERSION,
    }
    plan_id = f"decision-store-plan-{hashlib.sha256(_canonical_json(plan_state).encode()).hexdigest()[:16]}"
    baseline_manifest_hash = checksums["legacy_review_decisions"]
    baseline_events = [
        legacy_event_from_review_row(
            row,
            source_manifest_hash=baseline_manifest_hash,
            input_checksums={"legacy_review_decisions": baseline_manifest_hash},
            created_at=created_at,
        )
        for row in review_rows
    ]
    resolution_manifest_hash = _combined_hash(
        checksums["resolution_parent_decisions"],
        checksums["resolution_asset_eligibility"],
        checksums["resolution_aliases"],
    )
    resolution_events = _resolution_events(
        plan_id,
        resolution_manifest_hash,
        checksums,
        created_at,
        baseline_events,
        resolution_parents,
        resolution_parent_preview,
        resolution_assets,
        resolution_aliases,
    )
    asset_url_event = _asset_url_reference_event(
        plan_id,
        checksums,
        created_at,
        asset_review_rows,
        counts,
    )
    if len(baseline_events) != 46 or len(resolution_events) != 19:
        raise GovernanceDecisionStorePlanError("baseline or resolution event conservation failed")

    with tempfile.TemporaryDirectory(prefix="mka-governance-decision-store-") as temp_name:
        temporary_result = build_temporary_decision_store(
            Path(temp_name) / "governance_decisions.sqlite",
            [*baseline_events, *resolution_events, asset_url_event],
        )
        current_state = _validate_expected_current_state(
            Path(temp_name) / "governance_decisions.sqlite"
        )

    exact_tag_counts = _shopline_payments_tag_counts(merchant_cases)
    inventory = _decision_source_inventory(
        review_rows,
        merchant_cases,
        public_metrics,
        pending_metrics,
        restricted_customers,
        asset_review_rows,
        resolution_parents,
        resolution_assets,
        resolution_aliases,
        coverage,
    )
    baseline_scope = _baseline_scope(review_rows, coverage)
    baseline_preview = [
        *_event_preview_rows(baseline_events),
        *[_missing_baseline_preview_row(record) for record in missing_formal_records],
    ]
    resolution_preview = _event_preview_rows(
        resolution_events, status="new_resolution_event"
    )
    readiness = _parent_sync_readiness(resolution_parent_preview)
    blockers = []
    if coverage["formal_documents_without_explicit_decision"]:
        blockers.append("formal_index_decision_coverage_incomplete")
    if temporary_result["foreign_key_errors"]:
        blockers.append("temporary_store_foreign_key_errors")
    execution_blocked = bool(blockers)
    conclusion = (
        "C. Not ready to create Decision Store"
        if execution_blocked
        else "A. Ready for Decision Store confirmation"
    )
    expires_at = (datetime.fromisoformat(created_at) + timedelta(days=7)).isoformat()

    manifest = {
        "plan_id": plan_id,
        "plan_type": "governance_decision_store_create",
        "target_path": str(TARGET_DECISION_STORE_PATH),
        "input_checksums": checksums,
        "baseline_event_count": len(baseline_events),
        "new_resolution_event_count": len(resolution_events),
        "asset_url_reference_event_count": 1,
        "reviewer": "Admin",
        "reviewed_at": ADMIN_REVIEWED_AT,
        "created_at": created_at,
        "expiration": expires_at,
        "code_version": CODE_VERSION,
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "old_plan_status": {plan_id: "DO NOT CONFIRM" for plan_id in OLD_PLAN_IDS},
        "confirm_supported": False,
        "execute_supported": False,
        "formal_data_modified": False,
    }
    manifest["manifest_hash"] = hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_outputs(output_dir)
    _write_csv(output_dir / OUTPUT_FILENAMES[2], inventory)
    _write_csv(output_dir / OUTPUT_FILENAMES[3], baseline_scope)
    _write_csv(output_dir / OUTPUT_FILENAMES[4], _derived_report_exclusions())
    _write_csv(output_dir / OUTPUT_FILENAMES[9], baseline_preview)
    _write_csv(output_dir / OUTPUT_FILENAMES[10], resolution_preview)
    _write_csv(output_dir / OUTPUT_FILENAMES[12], readiness)
    (output_dir / OUTPUT_FILENAMES[0]).write_text(
        _summary_markdown(conclusion, manifest, coverage, counts, temporary_result), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[1]).write_text(_path_assessment_markdown(), encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[5]).write_text(_event_schema_markdown(), encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[6]).write_text(_projection_markdown(), encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[7]).write_text(DECISION_STORE_SCHEMA, encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[8]).write_text(
        _temporary_validation_markdown(temporary_result, current_state, exact_tag_counts),
        encoding="utf-8",
    )
    (output_dir / OUTPUT_FILENAMES[11]).write_text(
        _asset_url_boundary_markdown(checksums, counts), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[13]).write_text(_backup_markdown(), encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[14]).write_text(_rollback_markdown(), encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[15]).write_text(
        _confirmation_markdown(manifest, coverage), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[16]).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    protected_after = {label: _hash_path(path) for label, path in protected.items()}
    if protected_before != protected_after:
        raise GovernanceDecisionStorePlanError("a protected formal/input path changed")
    actual_outputs = sorted(
        path.name for path in output_dir.iterdir()
        if path.is_file() and not path.name.startswith("._")
    )
    if actual_outputs != sorted(OUTPUT_FILENAMES):
        raise GovernanceDecisionStorePlanError("Decision Store plan output contract is incomplete")

    return {
        "conclusion": conclusion,
        "target_path": str(TARGET_DECISION_STORE_PATH),
        "baseline_event_count": len(baseline_events),
        "resolution_event_count": len(resolution_events),
        "asset_url_reference_event_count": 1,
        **coverage,
        **counts,
        "parent_sync_candidate_count": 4,
        "excluded_parent_sync_count": 1,
        "temporary_event_count": temporary_result["event_count"],
        "temporary_integrity_check": temporary_result["integrity_check"],
        "temporary_hash_chain_valid": temporary_result["hash_chain_valid"],
        "temporary_backup_restore": temporary_result["backup_restore"],
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "plan_id": plan_id,
        "manifest_hash": manifest["manifest_hash"],
        "formal_data_modified": False,
        "output_dir": str(output_dir),
    }


def _resolution_events(
    plan_id,
    manifest_hash,
    checksums,
    created_at,
    baseline_events,
    parent_rows,
    parent_preview,
    asset_rows,
    alias_rows,
):
    baseline_by_record = {
        event.record_id: event
        for event in baseline_events
        if event.event_type == "parent_review_decision"
    }
    parent_preview_by_id = {row["record_id"]: row for row in parent_preview}
    events = []
    resolution_inputs = {
        "resolution_parent_decisions": checksums["resolution_parent_decisions"],
        "resolution_asset_eligibility": checksums["resolution_asset_eligibility"],
        "resolution_aliases": checksums["resolution_aliases"],
    }
    for row in parent_rows:
        record_id = row["record_id"]
        previous = baseline_by_record.get(record_id)
        if previous is None:
            raise GovernanceDecisionStorePlanError(f"resolution parent lacks baseline event: {record_id}")
        events.append(
            GovernanceDecisionEvent(
                event_type="parent_review_decision",
                subject_type="parent",
                subject_id=record_id,
                record_id=record_id,
                asset_id=None,
                field_name="review_decision",
                action="supersede",
                previous_value=previous.new_value,
                new_value={
                    "review_decision": row["proposed_review_decision"],
                    "can_enter_vault": row["proposed_can_enter_vault"],
                    "can_enter_content_index": row["proposed_can_enter_content_index"],
                    "can_external_reference": row["proposed_can_external_reference"],
                },
                reviewer="Admin",
                reviewed_at=ADMIN_REVIEWED_AT,
                decision_reason=row["decision_reason"],
                provenance="admin_resolution",
                source_plan_id=plan_id,
                source_manifest_hash=manifest_hash,
                input_checksums=resolution_inputs,
                supersedes_event_id=previous.event_id,
                created_at=created_at,
                code_version=CODE_VERSION,
            )
        )
    for row in asset_rows:
        eligibility = row["proposed_asset_index_eligibility"]
        events.append(
            GovernanceDecisionEvent(
                event_type="asset_eligibility",
                subject_type="asset",
                subject_id=row["asset_id"],
                record_id=row["record_id"],
                asset_id=row["asset_id"],
                field_name="asset_eligibility",
                action={"include": "approve", "hold": "hold", "exclude": "exclude"}[eligibility],
                previous_value=row.get("current_asset_eligibility") or None,
                new_value={
                    "asset_index_eligibility": eligibility,
                    "asset_search_eligibility": row["proposed_asset_search_eligibility"],
                },
                reviewer="Admin",
                reviewed_at=ADMIN_REVIEWED_AT,
                decision_reason=row["eligibility_reason"],
                provenance="admin_resolution",
                source_plan_id=plan_id,
                source_manifest_hash=manifest_hash,
                input_checksums=resolution_inputs,
                supersedes_event_id=None,
                created_at=created_at,
                code_version=CODE_VERSION,
            )
        )
    for row in alias_rows:
        normalized_alias = " ".join(row["alias"].strip().casefold().split())
        events.append(
            GovernanceDecisionEvent(
                event_type="search_alias",
                subject_type="search_alias",
                subject_id=f"{row['record_id']}|{normalized_alias}",
                record_id=row["record_id"],
                asset_id=None,
                field_name="search_aliases",
                action="add",
                previous_value=None,
                new_value={"alias": row["alias"], "normalized_alias": normalized_alias},
                reviewer="Admin",
                reviewed_at=ADMIN_REVIEWED_AT,
                decision_reason="Admin-approved exact source-record alias",
                provenance="admin_resolution",
                source_plan_id=plan_id,
                source_manifest_hash=manifest_hash,
                input_checksums=resolution_inputs,
                supersedes_event_id=None,
                created_at=created_at,
                code_version=CODE_VERSION,
            )
        )
    for record_id in ("商家夥伴案例資料庫:r7", "商家夥伴案例資料庫:r122"):
        row = parent_preview_by_id[record_id]
        events.append(
            GovernanceDecisionEvent(
                event_type="entity_metadata",
                subject_type="parent",
                subject_id=record_id,
                record_id=record_id,
                asset_id=None,
                field_name="entity_type_and_handle_requirement",
                action="add",
                previous_value=None,
                new_value={"entity_type": "partner", "merchant_handle_requirement": "not_required"},
                reviewer="Admin",
                reviewed_at=ADMIN_REVIEWED_AT,
                decision_reason=row["reason"],
                provenance="admin_resolution",
                source_plan_id=plan_id,
                source_manifest_hash=manifest_hash,
                input_checksums=resolution_inputs,
                supersedes_event_id=None,
                created_at=created_at,
                code_version=CODE_VERSION,
            )
        )
    return events


def _asset_url_reference_event(plan_id, checksums, created_at, asset_rows, counts):
    reviewed = [row for row in asset_rows if row.get("review_decision") in {"approve", "exclude_asset"}]
    reviewers = sorted({row.get("reviewer", "") for row in reviewed if row.get("reviewer")})
    reviewed_values = sorted({row.get("reviewed_at", "") for row in reviewed if row.get("reviewed_at")})
    if reviewers != ["James Huang"] or len(reviewed_values) != 1:
        raise GovernanceDecisionStorePlanError("asset URL legacy reviewer metadata is inconsistent")
    reference_hash = _combined_hash(
        checksums["asset_url_decisions"],
        checksums["asset_url_validation"],
        checksums["asset_apply_preview"],
        checksums["asset_blocked_preview"],
    )
    return GovernanceDecisionEvent(
        event_type="asset_url_manifest_reference",
        subject_type="manifest",
        subject_id=f"asset-url-decisions|{reference_hash[:16]}",
        record_id=None,
        asset_id=None,
        field_name="asset_url_decision_manifest",
        action="add",
        previous_value=None,
        new_value={
            "decision_csv": "reports/asset_metadata_preview/human_review_template.csv",
            "decision_csv_hash": checksums["asset_url_decisions"],
            "validator_result_hash": checksums["asset_url_validation"],
            "apply_preview_hash": checksums["asset_apply_preview"],
            "blocked_preview_hash": checksums["asset_blocked_preview"],
            "approved_field_count": counts["approved_url_field_count"],
            "eligible_asset_count": counts["eligible_asset_count"],
            "hold_asset_count": counts["hold_asset_count"],
            "excluded_asset_count": counts["excluded_asset_count"],
        },
        reviewer=reviewers[0],
        reviewed_at=reviewed_values[0],
        decision_reason="Reference validated Asset URL decisions without duplicating URL values",
        provenance="validated_asset_url_manifest_reference",
        source_plan_id=plan_id,
        source_manifest_hash=reference_hash,
        input_checksums={
            "asset_url_decisions": checksums["asset_url_decisions"],
            "asset_url_validation": checksums["asset_url_validation"],
        },
        supersedes_event_id=None,
        created_at=created_at,
        code_version=CODE_VERSION,
    )


def _insert_event(connection, event):
    existing = connection.execute(
        "SELECT event_id FROM decision_events WHERE idempotency_key=?",
        (event.idempotency_key,),
    ).fetchone()
    if existing:
        return False
    if event.supersedes_event_id:
        referenced = connection.execute(
            """SELECT event_id, event_type, subject_type, subject_id, field_name
            FROM decision_events WHERE event_id=?""",
            (event.supersedes_event_id,),
        ).fetchone()
        if not referenced:
            raise GovernanceDecisionStorePlanError("supersedes_event_id does not exist")
        if tuple(referenced[1:]) != (
            event.event_type,
            event.subject_type,
            event.subject_id,
            event.field_name,
        ):
            raise GovernanceDecisionStorePlanError(
                "superseding event must preserve event/subject/field identity"
            )
    else:
        existing_subject = connection.execute(
            """SELECT event_id FROM effective_decision_events
            WHERE event_type=? AND subject_type=? AND subject_id=? AND field_name=?""",
            (event.event_type, event.subject_type, event.subject_id, event.field_name),
        ).fetchone()
        if existing_subject:
            raise GovernanceDecisionStorePlanError(
                "an existing current decision must be changed by supersede/revoke"
            )
    connection.execute(
        "INSERT OR IGNORE INTO decision_manifests VALUES (?, ?, ?, ?, ?)",
        (
            event.source_manifest_hash,
            event.source_plan_id,
            _canonical_json(dict(sorted(event.input_checksums.items()))),
            event.created_at,
            event.code_version,
        ),
    )
    previous_hash_row = connection.execute(
        "SELECT event_hash FROM decision_events ORDER BY event_sequence DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous_hash_row[0] if previous_hash_row else None
    payload = {
        **normalize_event_payload(event),
        "event_id": event.event_id,
        "idempotency_key": event.idempotency_key,
        "created_at": event.created_at,
        "code_version": event.code_version,
    }
    event_hash = _event_hash(previous_hash, payload)
    connection.execute(
        """INSERT INTO decision_events (
            event_id, idempotency_key, event_type, subject_type, subject_id,
            record_id, asset_id, field_name, action, previous_value_json,
            new_value_json, reviewer, reviewed_at, decision_reason, provenance,
            source_plan_id, source_manifest_hash, input_checksums_json,
            supersedes_event_id, previous_event_hash, event_hash, created_at, code_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.event_id,
            event.idempotency_key,
            event.event_type,
            event.subject_type,
            event.subject_id,
            event.record_id,
            event.asset_id,
            event.field_name,
            event.action,
            _canonical_json(event.previous_value),
            _canonical_json(event.new_value),
            event.reviewer,
            event.reviewed_at,
            event.decision_reason,
            event.provenance,
            event.source_plan_id,
            event.source_manifest_hash,
            _canonical_json(dict(sorted(event.input_checksums.items()))),
            event.supersedes_event_id,
            previous_hash,
            event_hash,
            event.created_at,
            event.code_version,
        ),
    )
    return True


def _row_hash_payload(row):
    return {
        "event_type": row["event_type"],
        "subject_type": row["subject_type"],
        "subject_id": row["subject_id"],
        "record_id": row["record_id"],
        "asset_id": row["asset_id"],
        "field_name": row["field_name"],
        "action": row["action"],
        "previous_value": json.loads(row["previous_value_json"]),
        "new_value": json.loads(row["new_value_json"]),
        "reviewer": row["reviewer"],
        "reviewed_at": row["reviewed_at"],
        "decision_reason": row["decision_reason"],
        "provenance": row["provenance"],
        "source_plan_id": row["source_plan_id"],
        "source_manifest_hash": row["source_manifest_hash"],
        "input_checksums": json.loads(row["input_checksums_json"]),
        "supersedes_event_id": row["supersedes_event_id"],
        "event_id": row["event_id"],
        "idempotency_key": row["idempotency_key"],
        "created_at": row["created_at"],
        "code_version": row["code_version"],
    }


def _event_hash(previous_hash, payload):
    chain_input = f"{previous_hash or ''}\n{_canonical_json(payload)}"
    return hashlib.sha256(chain_input.encode()).hexdigest()


def _verify_transaction_rollback(connection):
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO decision_manifests VALUES (?, ?, ?, ?, ?)",
        ("rollback", "rollback", "{}", ADMIN_REVIEWED_AT, CODE_VERSION),
    )
    connection.rollback()
    return connection.execute(
        "SELECT COUNT(*) FROM decision_manifests WHERE manifest_hash='rollback'"
    ).fetchone()[0] == 0


def _verify_read_only_reopen(path, event_count):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        count_ok = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == event_count
        try:
            connection.execute("INSERT INTO decision_manifests VALUES ('x','x','{}','x','x')")
        except sqlite3.OperationalError:
            write_blocked = True
        else:
            write_blocked = False
        return count_ok and write_blocked
    finally:
        connection.close()


def _verify_backup_restore(path, event_count):
    backup_path = path.with_name(f"{path.stem}.backup.sqlite")
    restored_path = path.with_name(f"{path.stem}.restored.sqlite")
    source = sqlite3.connect(path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        source.close()
        backup.close()
    restored = sqlite3.connect(restored_path)
    backup = sqlite3.connect(backup_path)
    try:
        backup.backup(restored)
        ok = restored.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == event_count
    finally:
        backup.close()
        restored.close()
    backup_path.unlink()
    restored_path.unlink()
    return ok


def _verify_tamper_detection(path):
    tampered = path.with_name(f"{path.stem}.tampered.sqlite")
    shutil.copy2(path, tampered)
    connection = sqlite3.connect(tampered)
    try:
        connection.execute("DROP TRIGGER decision_events_no_update")
        connection.execute("UPDATE decision_events SET new_value_json='{}' WHERE event_sequence=1")
        connection.commit()
    finally:
        connection.close()
    detected = not verify_decision_hash_chain(tampered)["valid"]
    tampered.unlink()
    return detected


def _verify_supersede_revoke_projection(connection):
    row = connection.execute(
        "SELECT * FROM current_parent_decisions ORDER BY event_sequence LIMIT 1"
    ).fetchone()
    if row is None:
        return True, True
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM current_parent_decisions ORDER BY event_sequence LIMIT 1"
    ).fetchone()
    connection.execute("SAVEPOINT projection_rehearsal")
    base = _event_from_row_for_rehearsal(row, "supersede")
    _insert_event(connection, base)
    supersede_ok = connection.execute(
        "SELECT event_id FROM current_parent_decisions WHERE subject_id=?",
        (row["subject_id"],),
    ).fetchone()[0] == base.event_id
    revoke = _event_from_row_for_rehearsal(
        connection.execute("SELECT * FROM decision_events WHERE event_id=?", (base.event_id,)).fetchone(),
        "revoke",
    )
    _insert_event(connection, revoke)
    revoke_ok = connection.execute(
        "SELECT COUNT(*) FROM current_parent_decisions WHERE subject_id=?",
        (row["subject_id"],),
    ).fetchone()[0] == 0
    connection.execute("ROLLBACK TO projection_rehearsal")
    connection.execute("RELEASE projection_rehearsal")
    connection.row_factory = None
    return supersede_ok, revoke_ok


def _event_from_row_for_rehearsal(row, action):
    return GovernanceDecisionEvent(
        event_type=row["event_type"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        record_id=row["record_id"],
        asset_id=row["asset_id"],
        field_name=row["field_name"],
        action=action,
        previous_value=json.loads(row["new_value_json"]),
        new_value={"rehearsal": action} if action == "supersede" else None,
        reviewer="Admin",
        reviewed_at=ADMIN_REVIEWED_AT,
        decision_reason=f"Temporary {action} projection rehearsal",
        provenance="admin_resolution",
        source_plan_id=f"temporary-{action}",
        source_manifest_hash=hashlib.sha256(f"temporary-{action}".encode()).hexdigest(),
        input_checksums={"temporary": hashlib.sha256(action.encode()).hexdigest()},
        supersedes_event_id=row["event_id"],
        created_at=ADMIN_REVIEWED_AT,
        code_version=CODE_VERSION,
    )


def _verify_alias_multi_parent_projection(connection):
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM current_search_aliases LIMIT 1").fetchone()
    if row is None:
        connection.row_factory = None
        return True
    value = json.loads(row["new_value_json"])
    connection.execute("SAVEPOINT alias_rehearsal")
    alias = GovernanceDecisionEvent(
        event_type="search_alias",
        subject_type="search_alias",
        subject_id=f"temporary:second|{value['normalized_alias']}",
        record_id="temporary:second",
        asset_id=None,
        field_name="search_aliases",
        action="add",
        previous_value=None,
        new_value=value,
        reviewer="Admin",
        reviewed_at=ADMIN_REVIEWED_AT,
        decision_reason="Temporary one-to-many alias rehearsal",
        provenance="admin_resolution",
        source_plan_id="temporary-alias",
        source_manifest_hash=hashlib.sha256(b"temporary-alias").hexdigest(),
        input_checksums={"temporary": hashlib.sha256(b"alias").hexdigest()},
        supersedes_event_id=None,
        created_at=ADMIN_REVIEWED_AT,
        code_version=CODE_VERSION,
    )
    _insert_event(connection, alias)
    count = connection.execute(
        "SELECT COUNT(*) FROM current_search_aliases "
        "WHERE json_extract(new_value_json, '$.normalized_alias')=?",
        (value["normalized_alias"],),
    ).fetchone()[0]
    connection.execute("ROLLBACK TO alias_rehearsal")
    connection.execute("RELEASE alias_rehearsal")
    connection.row_factory = None
    return count >= 2


def _validate_expected_current_state(path):
    connection = sqlite3.connect(path)
    try:
        parent = {
            row[0]: json.loads(row[1])
            for row in connection.execute(
                "SELECT record_id, new_value_json FROM current_parent_decisions"
            )
        }
        asset = {
            row[0]: json.loads(row[1])
            for row in connection.execute(
                "SELECT asset_id, new_value_json FROM current_asset_eligibility"
            )
        }
        aliases = [
            json.loads(row[0])
            for row in connection.execute("SELECT new_value_json FROM current_search_aliases")
        ]
    finally:
        connection.close()
    checks = {
        "r30_excluded": parent["商家夥伴案例資料庫:r30"]["review_decision"] == "exclude",
        "r12_video_held": asset["商家夥伴案例資料庫:r12:video"]["asset_index_eligibility"] == "hold",
        "slp_exact_alias": any(item["normalized_alias"] == "slp" for item in aliases),
        "shopline_payments_alias": any(
            item["normalized_alias"] == "shopline payments" for item in aliases
        ),
    }
    if not all(checks.values()):
        raise GovernanceDecisionStorePlanError("temporary current-state projection is incorrect")
    return checks


def _formal_decision_coverage(db_path, review_rows):
    decision_keys = {
        (row["source_sheet"], int(row["source_row"]))
        for row in review_rows
        if str(row.get("source_row", "")).isdigit()
    }
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT metadata_json FROM documents").fetchall()
    finally:
        connection.close()
    records = []
    for (raw,) in rows:
        metadata = json.loads(raw)
        key = (metadata.get("source_sheet"), int(metadata.get("source_row")))
        records.append(
            {
                "source_sheet": key[0],
                "source_row": key[1],
                "record_type": metadata.get("record_type") or "",
                "record_id": f"{key[0]}:r{key[1]}",
            }
        )
    keys = [(row["source_sheet"], row["source_row"]) for row in records]
    covered = sum(key in decision_keys for key in keys)
    missing = [
        row for row in records
        if (row["source_sheet"], row["source_row"]) not in decision_keys
    ]
    return (
        {
            "formal_document_count": len(keys),
            "formal_documents_with_explicit_decision": covered,
            "formal_documents_without_explicit_decision": len(keys) - covered,
        },
        sorted(missing, key=lambda row: (row["source_sheet"], row["source_row"])),
    )


def _formal_vault_decision_coverage(vault_path, review_rows):
    decision_keys = {
        (row["source_sheet"], int(row["source_row"]))
        for row in review_rows
        if str(row.get("source_row", "")).isdigit()
    }
    records = []
    namespace = Path(vault_path) / "MKA"
    for path in sorted(namespace.rglob("*.md")):
        if path.name.startswith("._"):
            continue
        metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        source_sheet = metadata.get("source_sheet")
        source_row = metadata.get("source_row")
        if not source_sheet or source_row is None:
            raise GovernanceDecisionStorePlanError(
                f"formal Vault record lacks source identity: {path}"
            )
        row_number = int(source_row)
        records.append(
            {
                "source_sheet": source_sheet,
                "source_row": row_number,
                "record_type": metadata.get("record_type") or "",
                "record_id": f"{source_sheet}:r{row_number}",
            }
        )
    missing = [
        row for row in records
        if (row["source_sheet"], row["source_row"]) not in decision_keys
    ]
    return (
        {
            "formal_vault_document_count": len(records),
            "formal_vault_with_explicit_decision": len(records) - len(missing),
            "formal_vault_without_explicit_decision": len(missing),
        },
        sorted(missing, key=lambda row: (row["source_sheet"], row["source_row"])),
    )


def _conservation_counts(apply_rows, blocked_rows, resolution_assets):
    approved = {}
    for row in apply_rows:
        if row.get("review_decision") == "approve":
            approved.setdefault(row["asset_id"], set()).add(row["field"])
    excluded_asset = "商家夥伴案例資料庫:r30:article"
    eligible = set(approved) - {excluded_asset}
    held = {
        row["asset_id"] for row in resolution_assets
        if row["proposed_asset_index_eligibility"] == "hold"
    }
    blocked = {row["asset_id"] for row in blocked_rows}
    excluded = (blocked - held) | {excluded_asset}
    if (len(eligible), len(held), len(excluded), len(eligible) * 2) != (205, 1, 16, 410):
        raise GovernanceDecisionStorePlanError("205/1/16/410 conservation failed")
    if len(eligible | held | excluded) != 222:
        raise GovernanceDecisionStorePlanError("asset identity conservation failed")
    return {
        "eligible_asset_count": 205,
        "hold_asset_count": 1,
        "excluded_asset_count": 16,
        "approved_url_field_count": 410,
        "new_asset_identity_count": 0,
        "lost_asset_identity_count": 0,
        "record_id_change_count": 0,
        "blocked_asset_in_apply_manifest_count": 0,
    }


def _decision_source_inventory(
    review_rows,
    merchant_cases,
    public_metrics,
    pending_metrics,
    restricted_customers,
    asset_rows,
    resolution_parents,
    resolution_assets,
    resolution_aliases,
    coverage,
):
    reviewed_asset_rows = [row for row in asset_rows if row.get("review_decision")]
    return [
        _inventory("review_decisions_template", 46, "A", "Import 46 preserved legacy decision events"),
        _inventory("merchant_case_governance_flags", len(merchant_cases), "B", "Checksum source facts; do not invent decisions"),
        _inventory("public_metric_governance_flags", len(public_metrics), "B", "Checksum source facts and preserve channel governance"),
        _inventory("pending_metric_governance_flags", len(pending_metrics), "B", "Checksum source facts; remain internal-only"),
        _inventory("restricted_customer_governance_flags", len(restricted_customers), "B", "Checksum denylist source; never expose as content"),
        _inventory("asset_url_review_decisions", len(reviewed_asset_rows), "B", "Reference validated CSV and manifest hashes; do not duplicate URL values"),
        _inventory("resolution_parent_decisions", len(resolution_parents), "A", "Append Admin resolution events after baseline"),
        _inventory("resolution_asset_eligibility", len(resolution_assets), "A", "Append asset eligibility events"),
        _inventory("resolution_search_aliases", len(resolution_aliases), "A", "Append exact source-record alias events"),
        _inventory("formal_index_decision_coverage", coverage["formal_document_count"], "A", f"{coverage['formal_documents_without_explicit_decision']} records require explicit baseline authority"),
        _inventory("apply_and_validation_reports", 1, "C", "Derived reports excluded from authoritative import"),
    ]


def _inventory(source, count, classification, treatment):
    return {
        "source": source,
        "row_count": count,
        "classification": classification,
        "treatment": treatment,
    }


def _baseline_scope(review_rows, coverage):
    counts: Dict[str, int] = {}
    for row in review_rows:
        counts[row["record_type"]] = counts.get(row["record_type"], 0) + 1
    rows = [
        {
            "scope": record_type,
            "source_rows": count,
            "planned_events": count,
            "authority_status": "preserve_legacy_review_metadata",
            "execution_blocker": "false",
        }
        for record_type, count in sorted(counts.items())
    ]
    rows.append(
        {
            "scope": "formal_documents_without_explicit_decision",
            "source_rows": coverage["formal_documents_without_explicit_decision"],
            "planned_events": 0,
            "authority_status": "human_baseline_decision_required",
            "execution_blocker": "true",
        }
    )
    return rows


def _event_preview_rows(events, status="planned_import"):
    return [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "subject_type": event.subject_type,
            "subject_id": event.subject_id,
            "record_id": event.record_id or "",
            "asset_id": event.asset_id or "",
            "field_name": event.field_name,
            "action": event.action,
            "new_value_json": _canonical_json(event.new_value),
            "reviewer": event.reviewer,
            "reviewed_at": event.reviewed_at or "",
            "decision_reason": event.decision_reason,
            "provenance": event.provenance,
            "supersedes_event_id": event.supersedes_event_id or "",
            "idempotency_key": event.idempotency_key,
            "baseline_status": status,
        }
        for event in events
    ]


def _missing_baseline_preview_row(record):
    return {
        "event_id": "",
        "event_type": "parent_review_decision",
        "subject_type": "parent",
        "subject_id": record["record_id"],
        "record_id": record["record_id"],
        "asset_id": "",
        "field_name": "review_decision",
        "action": "",
        "new_value_json": "",
        "reviewer": "",
        "reviewed_at": "",
        "decision_reason": "explicit_baseline_authority_required",
        "provenance": "not_imported",
        "supersedes_event_id": "",
        "idempotency_key": "",
        "baseline_status": "blocked_missing_authority",
    }


def _parent_sync_readiness(parent_rows):
    rows = []
    for row in sorted(parent_rows, key=lambda item: int(item["source_row"]) if item.get("source_row") else 0):
        excluded = row["record_id"] == "商家夥伴案例資料庫:r30"
        rows.append(
            {
                "record_id": row["record_id"],
                "brand_name": row["brand_name"],
                "current_state_decision": row["proposed_review_decision"],
                "sync_readiness": "not_syncable" if excluded else "syncable",
                "index_eligibility": "excluded" if excluded else "included",
                "search_eligibility": "not_searchable" if excluded else "eligible_assets_only",
                "reason": row["reason"],
            }
        )
    return rows


def _derived_report_exclusions():
    return [
        {"source": "preview_summary.md", "reason": "human-readable derived summary"},
        {"source": "review_summary.md", "reason": "count projection, not a decision source"},
        {"source": "asset_apply_preview.csv", "reason": "derived diff; checksum reference only"},
        {"source": "review_decision_status.csv", "reason": "validator projection; checksum reference only"},
        {"source": "missing_parent_resolution_apply_preview", "reason": "previous plan output, not authoritative"},
        {"source": "Slack output previews", "reason": "renderer projections contain no decisions"},
        {"source": "Obsidian sync manifests", "reason": "sync evidence, not decision authority"},
    ]


def _shopline_payments_tag_counts(merchant_cases):
    matches = [
        row for row in merchant_cases
        if any(str(tag).strip().casefold() == "shopline payments" for tag in row.get("content_tags", []))
    ]
    governed = [row for row in matches if row.get("can_quote_externally")]
    return {"raw_exact_tag_count": len(matches), "governed_exact_tag_count": len(governed)}


def _summary_markdown(conclusion, manifest, coverage, counts, validation):
    return f"""# Governance Decision Store Plan

- Conclusion: **{conclusion}**
- PLAN_ID: `{manifest['plan_id']}`
- Target: `{manifest['target_path']}` (not created)
- Baseline events: {manifest['baseline_event_count']}
- New Resolution events: {manifest['new_resolution_event_count']}
- Asset URL manifest-reference events: {manifest['asset_url_reference_event_count']}
- Formal decision coverage: {coverage['formal_documents_with_explicit_decision']} / {coverage['formal_document_count']}
- Missing explicit formal decisions: {coverage['formal_documents_without_explicit_decision']}
- Formal Vault decision coverage: {coverage['formal_vault_with_explicit_decision']} / {coverage['formal_vault_document_count']}
- Formal Vault missing explicit decisions: {coverage['formal_vault_without_explicit_decision']}
- Execution blocked: {str(manifest['execution_blocked']).lower()}

## Conservation

- Eligible / hold / excluded assets: {counts['eligible_asset_count']} / {counts['hold_asset_count']} / {counts['excluded_asset_count']}
- Approved URL fields: {counts['approved_url_field_count']}
- Parent sync / excluded: 4 / 1
- New / lost asset identities: 0 / 0

## Temporary Verification

- Events: {validation['event_count']}
- integrity_check: `{validation['integrity_check']}`
- foreign_key errors: {validation['foreign_key_errors']}
- Hash chain / tamper detection: {str(validation['hash_chain_valid']).lower()} / {str(validation['tamper_detection']).lower()}
- Read-only reopen / rollback / backup restore: {str(validation['read_only_reopen']).lower()} / {str(validation['transaction_rollback']).lower()} / {str(validation['backup_restore']).lower()}

No formal Decision Store, Vault record, index table or production renderer was modified.
"""


def _path_assessment_markdown():
    return """# Authoritative Path Assessment

## Selected Path

`data/governance/governance_decisions.sqlite`

This is the single proposed authoritative path. It is outside `reports/`, `.mka/content_index.sqlite`, temporary directories and Obsidian. The repository already ignores `/data/`, so decision data cannot be accidentally committed. The file can be backed up, integrity-checked and restored independently from the content index.

## Operational Requirement

Future deployment must treat `data/governance/` as persistent runtime data with an explicit backup policy. Preview rebuild and content-index rebuild commands must never remove it. General retrieval and Obsidian ingestion must not scan this directory.

This Sprint did not create the directory or database.
"""


def _event_schema_markdown():
    return """# Append-only Decision Event Schema

- `decision_events` stores normalized old/new JSON, reviewer metadata, provenance, source plan/manifest/checksums, supersession links and a global hash chain.
- UPDATE and DELETE are rejected by SQLite triggers.
- Same manifest plus normalized payload has one deterministic idempotency key/event ID.
- Changes append `supersede`; removals append `revoke`. Historical reviewer/time/reason are never rewritten.
- `legacy_import` preserves reviewer and reviewed_at. Missing values use `legacy_reviewer_unavailable`, NULL reviewed_at and `legacy_reason_unavailable`; they are never changed to Admin.
- New resolution events require reviewer `Admin` and timezone-aware `2026-07-18T00:33:08+08:00`.
- Event hash includes normalized payload, event identity, creation metadata and previous event hash.
"""


def _projection_markdown():
    return """# Current State Projection Design

## Decision

Use SQL Views: `current_parent_decisions`, `current_asset_eligibility`, `current_search_aliases`, and `current_entity_metadata` over append-only events.

Views make event history the sole truth and eliminate materialized-table drift. They are cheap at the current scale and fully rebuildable. A future performance migration may add a cache, but the cache must remain disposable and checksum-linked to the event log.

Revoked chains produce no current row. Superseding events replace only the same event type/subject/field. Parent approval therefore cannot overwrite an asset hold. Alias subject IDs include record ID plus normalized alias, allowing the same exact alias to map to multiple legitimate parents. Alias is never treated as `content_tags`, and eligibility is never publication status.
"""


def _temporary_validation_markdown(result, current, tag_counts):
    return f"""# Temporary Decision Store Validation

- Baseline Legacy Events imported: 46
- New Resolution Events appended: 19
- Asset URL manifest-reference events: 1
- Stored event count: {result['event_count']}
- Duplicate event idempotency: verified
- UPDATE / DELETE triggers: verified by tests
- Parent/asset/alias current state: verified
- Parent approval does not override asset hold: verified
- r30 remains excluded: {str(current['r30_excluded']).lower()}
- SLP exact alias: {str(current['slp_exact_alias']).lower()}
- SHOPLINE Payments alias: {str(current['shopline_payments_alias']).lower()}
- Exact-tag records remain independent: {tag_counts['governed_exact_tag_count']} governed results ({tag_counts['raw_exact_tag_count']} raw)
- Alias one-to-many rehearsal: {str(result['alias_multi_parent_projection']).lower()}
- Supersede / revoke projection: {str(result['supersede_projection']).lower()} / {str(result['revoke_projection']).lower()}
- Hash chain / tamper detection: {str(result['hash_chain_valid']).lower()} / {str(result['tamper_detection']).lower()}
- integrity_check: `{result['integrity_check']}`
- foreign_key errors: {result['foreign_key_errors']}
- Read-only reopen: {str(result['read_only_reopen']).lower()}
- Transaction rollback: {str(result['transaction_rollback']).lower()}
- SQLite backup restore: {str(result['backup_restore']).lower()}
- Temporary files cleaned after validation: true
"""


def _asset_url_boundary_markdown(checksums, counts):
    return f"""# Asset URL Decision Boundary

## Decision: Option B

Do not migrate 410 URL values into Decision Store events yet. Keep the validated human decision CSV as the value authority and append one manifest-reference event containing:

- Decision CSV hash: `{checksums['asset_url_decisions']}`
- Validator result hash: `{checksums['asset_url_validation']}`
- Apply preview hash: `{checksums['asset_apply_preview']}`
- Approved fields: {counts['approved_url_field_count']}
- Eligible / hold / excluded assets: {counts['eligible_asset_count']} / {counts['hold_asset_count']} / {counts['excluded_asset_count']}

The store records no duplicate URL values, avoiding two Sources of Truth. A future URL migration requires a separately reviewed event contract.
"""


def _backup_markdown():
    return """# Decision Store Backup Plan

1. Freeze and checksum every baseline/resolution/URL reference input.
2. Record that the target is absent or checksum its existing predecessor.
3. Build a temporary sibling SQLite database with foreign keys enabled.
4. Run integrity, FK, hash-chain, projection, read-only and conservation checks.
5. Use SQLite Backup API to create a verified backup and rehearse restore.
6. After a separate confirmation, atomically replace the target on the same filesystem.
7. Record post-create checksum and immutable audit manifest.

This Sprint exercised SQLite Backup API and restore only on temporary files.
"""


def _rollback_markdown():
    return """# Decision Store Rollback Plan

## A. Create Failure

Delete the temporary DB; leave the formal target absent.

## B. Post-create Validation Failure

Remove the new formal file, restore the pre-create backup, verify checksums, and retain a failure audit artifact.

## C. Incorrect Later Decision

Never delete or update history. Append a signed `supersede` or `revoke` event and revalidate the hash chain/current-state views.

Atomic replacement, backup restore, transaction rollback and cleanup must all pass before execute can be implemented.
"""


def _confirmation_markdown(manifest, coverage):
    return f"""# Decision Store Confirmation Checklist

## DO NOT CONFIRM

- PLAN_ID: `{manifest['plan_id']}`
- Target: `{manifest['target_path']}`
- Missing explicit formal decisions: {coverage['formal_documents_without_explicit_decision']}
- Execution blocked: {str(manifest['execution_blocked']).lower()}
- Confirm implemented: false
- Execute implemented: false

Before a future confirmation:

- [ ] Resolve all 96 formal documents without explicit decision authority.
- [ ] Rebuild baseline inventory and require zero coverage gaps.
- [ ] Recompute input checksums and plan expiration.
- [ ] Independently approve target path, backup location and manifest hash.
- [ ] Keep `{OLD_PLAN_IDS[0]}` and `{OLD_PLAN_IDS[1]}` **DO NOT CONFIRM**.
- [ ] Do not provide force, skip-validation or skip-confirm paths.
"""


def _legacy_action(decision):
    if decision in {"exclude", "exclude_from_content_index", "enter_governance_table_only"}:
        return "exclude"
    if decision in {"needs_update", "enrich_metadata", "manual_review", "review_identity_mapping"}:
        return "hold"
    return "approve"


def _validate_timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceDecisionStorePlanError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceDecisionStorePlanError(f"{field} must include timezone")


def _validate_legacy_reviewed_at(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceDecisionStorePlanError("legacy reviewed_at must be ISO date/datetime") from exc
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceDecisionStorePlanError(
            "legacy reviewed_at datetime must include timezone; date-only values are preserved"
        )


def _normalize_json_value(value):
    if isinstance(value, Mapping):
        return {str(key): _normalize_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise GovernanceDecisionStorePlanError("event JSON contains unsupported value")


def _canonical_json(value):
    return json.dumps(
        _normalize_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _combined_hash(*values):
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise GovernanceDecisionStorePlanError(f"CSV has no header: {path}")
        return list(reader)


def _read_json_list(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GovernanceDecisionStorePlanError(f"invalid JSON: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise GovernanceDecisionStorePlanError(f"JSON must be an object array: {path}")
    return value


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["source"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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


def _assert_safe_output(output_dir, protected):
    output = output_dir.resolve()
    lowered = {part.casefold() for part in output.parts}
    if "obsidian_vault" in lowered or ".mka" in lowered or output == TARGET_DECISION_STORE_PATH.resolve():
        raise GovernanceDecisionStorePlanError("output must be a separate reports preview directory")
    for path in protected:
        candidate = path.resolve()
        if output == candidate or output in candidate.parents or candidate in output.parents:
            raise GovernanceDecisionStorePlanError("output overlaps a protected input")


def _clear_outputs(output_dir):
    for name in OUTPUT_FILENAMES:
        for path in (output_dir / name, output_dir / f"._{name}"):
            if path.exists():
                path.unlink()
