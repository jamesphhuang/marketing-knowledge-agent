from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .governance_decision_store_plan import (
    DECISION_STORE_SCHEMA,
    GovernanceDecisionEvent,
    normalize_event_payload,
)
from .governance_decision_store_regenerated_plan import (
    BUNDLE_ID,
    BUNDLE_ROOT_HASH,
    EXPECTED_COUNTS,
    build_regenerated_event_plan,
)


SCHEMA_VERSION = 2
SCHEMA_NAME = "governance_decision_store"
CODE_VERSION = "governance-decision-store-schema-v2-plan-v1"
PLAN_TYPE = "governance_decision_store_schema_v2_create"
CONFIRMATION_BINDING_VERSION = "execute-confirmation-binding-v1"
CONFIRMATION_BINDING_PLACEHOLDER = "confirmation-bound-at-execute:not-authoritative"
TEMPORARY_CONFIRMATION_ID = "temporary-schema-v2-validation:not-authoritative"
TEMPORARY_CONFIRMATION_ROOT_HASH = hashlib.sha256(
    b"temporary-schema-v2-validation:not-authoritative"
).hexdigest()
DEFAULT_TARGET_PATH = Path("data/governance/governance_decisions.sqlite")
DEFAULT_BUNDLE_PATH = Path("data/governance/imports") / BUNDLE_ID
DEFAULT_OLD_PLAN_MANIFEST = Path(
    "reports/governance_decision_store_regenerated_plan/regenerated_decision_store_manifest.json"
)
DEFAULT_OLD_CONFIRMATION = Path(
    "data/governance/confirmations/decision-store-plan-a02502d8361549b1"
)
DEFAULT_EXECUTE_REPORTS = Path("reports/governance_decision_store_execution")
DEFAULT_OUTPUT_DIR = Path("reports/governance_decision_store_schema_v2_plan")
OLD_CONFIRMED_PLAN_ID = "decision-store-plan-a02502d8361549b1"
OLD_CONFIRMED_MANIFEST_HASH = "1b285ec1e762d9c2b2fc42776742ac130f56aed2588186d33e8c5b3ffd435853"
OLD_CONFIRMATION_ID = "decision-store-confirmation-98fef43f8dd6773a"
OLD_CONFIRMATION_ROOT_HASH = "218b66aba50eeee0ccba15533f65bc74df12158788c86081b72e4ebfde3c0282"
OBSOLETE_PLAN_IDS = (
    OLD_CONFIRMED_PLAN_ID,
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
)
OUTPUT_FILENAMES = (
    "schema_v2_plan_summary.md",
    "execute_blocker_reconciliation.md",
    "schema_v1_v2_diff.md",
    "decision_events_schema_v2.md",
    "schema_metadata_design.md",
    "execution_metadata_design.md",
    "confirmation_binding_contract.md",
    "canonical_schema_v2.sql",
    "canonical_schema_v2_hash.json",
    "temporary_schema_v2_validation.md",
    "event_count_conservation.csv",
    "current_state_conservation.csv",
    "special_decision_validation.csv",
    "schema_v2_backward_compatibility.md",
    "obsolete_plan_confirmation_registry.csv",
    "schema_v2_backup_plan.md",
    "schema_v2_rollback_plan.md",
    "schema_v2_confirmation_checklist.md",
    "schema_v2_plan_manifest.json",
    "schema_v2_validation_errors.csv",
    "schema_v2_validation_warnings.csv",
)


CANONICAL_SCHEMA_V2_SQL = """PRAGMA foreign_keys = ON;
PRAGMA page_size = 4096;

CREATE TABLE decision_manifests (
    manifest_hash TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    input_checksums_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    code_version TEXT NOT NULL
);

CREATE TABLE schema_metadata (
    schema_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    schema_hash TEXT NOT NULL,
    schema_sql_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    code_version TEXT NOT NULL,
    source_plan_id TEXT NOT NULL,
    source_plan_manifest_hash TEXT NOT NULL,
    migration_type TEXT NOT NULL,
    compatibility_status TEXT NOT NULL,
    previous_schema_hash TEXT,
    metadata_json TEXT NOT NULL,
    UNIQUE (schema_name, schema_version)
);

CREATE TABLE execution_metadata (
    execution_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL,
    plan_manifest_hash TEXT NOT NULL,
    confirmation_id TEXT NOT NULL,
    confirmation_root_hash TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    bundle_root_hash TEXT NOT NULL,
    target_path TEXT NOT NULL,
    executed_by TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    expected_event_count INTEGER NOT NULL,
    actual_event_count INTEGER NOT NULL,
    expected_parent_current_state_count INTEGER NOT NULL,
    actual_parent_current_state_count INTEGER NOT NULL,
    authority_gap INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    schema_hash TEXT NOT NULL,
    database_sha256 TEXT CHECK (database_sha256 IS NULL),
    event_chain_head TEXT,
    event_chain_tail TEXT,
    source_branch TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    code_version TEXT NOT NULL,
    execution_manifest_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'staging', 'validated', 'completed', 'failed', 'quarantined'
    )),
    metadata_json TEXT NOT NULL
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
    source_bundle_id TEXT,
    source_bundle_root_hash TEXT,
    source_confirmation_id TEXT NOT NULL,
    source_confirmation_root_hash TEXT NOT NULL,
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
    ),
    CHECK (length(trim(source_confirmation_id)) > 0),
    CHECK (length(source_confirmation_root_hash) = 64)
);

CREATE TRIGGER decision_events_no_update
BEFORE UPDATE ON decision_events BEGIN
    SELECT RAISE(ABORT, 'decision_events is append-only: UPDATE blocked');
END;
CREATE TRIGGER decision_events_no_delete
BEFORE DELETE ON decision_events BEGIN
    SELECT RAISE(ABORT, 'decision_events is append-only: DELETE blocked');
END;
CREATE TRIGGER schema_metadata_no_update
BEFORE UPDATE ON schema_metadata BEGIN
    SELECT RAISE(ABORT, 'schema_metadata is append-only: UPDATE blocked');
END;
CREATE TRIGGER schema_metadata_no_delete
BEFORE DELETE ON schema_metadata BEGIN
    SELECT RAISE(ABORT, 'schema_metadata is append-only: DELETE blocked');
END;
CREATE TRIGGER execution_metadata_no_update
BEFORE UPDATE ON execution_metadata BEGIN
    SELECT RAISE(ABORT, 'execution_metadata is append-only: UPDATE blocked');
END;
CREATE TRIGGER execution_metadata_no_delete
BEFORE DELETE ON execution_metadata BEGIN
    SELECT RAISE(ABORT, 'execution_metadata is append-only: DELETE blocked');
END;

CREATE INDEX idx_decision_events_subject
    ON decision_events(subject_type, subject_id, event_type, field_name, event_sequence);
CREATE INDEX idx_decision_events_supersedes ON decision_events(supersedes_event_id);
CREATE INDEX idx_decision_events_confirmation
    ON decision_events(source_confirmation_id, source_confirmation_root_hash);
CREATE INDEX idx_schema_metadata_version ON schema_metadata(schema_name, schema_version);
CREATE INDEX idx_execution_metadata_plan ON execution_metadata(plan_id, execution_sequence);

CREATE VIEW effective_decision_events AS
SELECT event.*
FROM decision_events AS event
WHERE event.action <> 'revoke'
  AND NOT EXISTS (
      SELECT 1 FROM decision_events AS later
      WHERE later.supersedes_event_id = event.event_id
  );

CREATE VIEW current_parent_decisions AS
SELECT event.* FROM effective_decision_events AS event
WHERE event.event_type = 'parent_review_decision'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence) FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_type = event.subject_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );

CREATE VIEW current_asset_eligibility AS
SELECT event.* FROM effective_decision_events AS event
WHERE event.event_type = 'asset_eligibility'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence) FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );

CREATE VIEW current_search_aliases AS
SELECT event.* FROM effective_decision_events AS event
WHERE event.event_type = 'search_alias'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence) FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );

CREATE VIEW current_entity_metadata AS
SELECT event.* FROM effective_decision_events AS event
WHERE event.event_type = 'entity_metadata'
  AND event.event_sequence = (
      SELECT MAX(candidate.event_sequence) FROM effective_decision_events AS candidate
      WHERE candidate.event_type = event.event_type
        AND candidate.subject_id = event.subject_id
        AND candidate.field_name = event.field_name
  );
"""


class GovernanceDecisionStoreSchemaV2PlanError(RuntimeError):
    pass


def schema_v2_hashes() -> dict:
    sql_hash = _sha256_text(CANONICAL_SCHEMA_V2_SQL)
    schema_hash = _sha256_text(_canonical_json({
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "canonical_schema_sql_hash": sql_hash,
        "confirmation_binding_version": CONFIRMATION_BINDING_VERSION,
    }))
    return {"schema_hash": schema_hash, "canonical_schema_sql_hash": sql_hash}


def event_template_payload(event: GovernanceDecisionEvent) -> dict:
    payload = normalize_event_payload(event)
    if event.source_plan_id == "$PLAN_ID" or event.source_plan_id.startswith(
        "decision-store-schema-v2-plan-"
    ):
        payload["source_plan_id"] = "$PLAN_ID"
    return payload


def event_template_hash(events: Sequence[GovernanceDecisionEvent]) -> str:
    return _sha256_text(_canonical_json([event_template_payload(event) for event in events]))


def bind_event_templates(
    events: Sequence[GovernanceDecisionEvent],
    confirmation_id: str,
    confirmation_root_hash: str,
) -> list[dict]:
    if not isinstance(confirmation_id, str) or not confirmation_id.strip():
        raise GovernanceDecisionStoreSchemaV2PlanError("confirmation_id is required")
    if not _is_sha256(confirmation_root_hash):
        raise GovernanceDecisionStoreSchemaV2PlanError("confirmation_root_hash must be SHA-256")
    bound = []
    event_id_map: dict[str, str] = {}
    for event in events:
        supersedes = event_id_map.get(event.supersedes_event_id or "")
        if event.supersedes_event_id and not supersedes:
            raise GovernanceDecisionStoreSchemaV2PlanError(
                f"unresolved supersedes template: {event.supersedes_event_id}"
            )
        payload = {
            **normalize_event_payload(event),
            "supersedes_event_id": supersedes,
            "source_confirmation_id": confirmation_id,
            "source_confirmation_root_hash": confirmation_root_hash,
            "created_at": event.created_at,
            "code_version": event.code_version,
        }
        idempotency_key = _sha256_text(_canonical_json(payload))
        event_id = f"event-v2-{idempotency_key[:24]}"
        row = {
            **payload,
            "event_id": event_id,
            "idempotency_key": idempotency_key,
        }
        event_id_map[event.event_id] = event_id
        bound.append(row)
    return bound


def generate_governance_decision_store_schema_v2_plan(
    *,
    repo_root: Path,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
    old_plan_manifest_path: Path = DEFAULT_OLD_PLAN_MANIFEST,
    old_confirmation_path: Path = DEFAULT_OLD_CONFIRMATION,
    execute_reports_path: Path = DEFAULT_EXECUTE_REPORTS,
    legacy_decisions_path: Path = Path("reports/excel_preview/review_decisions_template.csv"),
    merchant_cases_path: Path = Path("reports/excel_preview/merchant_cases.json"),
    asset_url_decisions_path: Path = Path("reports/asset_metadata_preview/human_review_template.csv"),
    asset_url_validation_path: Path = Path("reports/asset_metadata_review_validation/review_decision_status.csv"),
    asset_apply_preview_path: Path = Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    asset_blocked_preview_path: Path = Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    formal_vault_path: Path = Path("obsidian_vault"),
    formal_db_path: Path = Path(".mka/content_index.sqlite"),
    production_renderer_path: Path = Path("src/marketing_knowledge_agent/slack_interface.py"),
    target_path: Path = DEFAULT_TARGET_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    temporary_dir: Optional[Path] = None,
    created_at: Optional[str] = None,
    source_branch: Optional[str] = None,
    source_commit: Optional[str] = None,
) -> dict:
    root = Path(repo_root).resolve()
    created = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(created)
    source_branch = source_branch or _git_value(root, "branch", "--show-current")
    source_commit = source_commit or _git_value(root, "rev-parse", "HEAD")
    paths = {
        "bundle": _resolve(root, bundle_path),
        "old_plan_manifest": _resolve(root, old_plan_manifest_path),
        "old_confirmation": _resolve(root, old_confirmation_path),
        "execute_reports": _resolve(root, execute_reports_path),
        "legacy_decisions": _resolve(root, legacy_decisions_path),
        "merchant_cases": _resolve(root, merchant_cases_path),
        "asset_url_decisions": _resolve(root, asset_url_decisions_path),
        "asset_url_validation": _resolve(root, asset_url_validation_path),
        "asset_apply_preview": _resolve(root, asset_apply_preview_path),
        "asset_blocked_preview": _resolve(root, asset_blocked_preview_path),
        "formal_vault": _resolve(root, formal_vault_path),
        "formal_db": _resolve(root, formal_db_path),
        "production_renderer": _resolve(root, production_renderer_path),
    }
    target = _resolve(root, target_path)
    output = _resolve(root, output_dir)
    temp_root = _resolve(root, temporary_dir) if temporary_dir else output / "temporary"
    for label, path in paths.items():
        if not path.exists():
            raise GovernanceDecisionStoreSchemaV2PlanError(f"required {label} input is missing: {path}")
    if target.exists():
        raise GovernanceDecisionStoreSchemaV2PlanError("formal Decision Store target already exists")
    _validate_old_authority(paths)
    protected_before = {name: _hash_path(path) for name, path in paths.items()}
    input_checksums = {
        name: _hash_path(path)
        for name, path in paths.items()
        if name not in {"formal_vault", "formal_db", "production_renderer", "execute_reports"}
    }
    hashes = schema_v2_hashes()

    placeholder_plan = build_regenerated_event_plan(
        bundle_path=paths["bundle"],
        legacy_decisions_path=paths["legacy_decisions"],
        merchant_cases_path=paths["merchant_cases"],
        asset_url_decisions_path=paths["asset_url_decisions"],
        asset_url_validation_path=paths["asset_url_validation"],
        asset_apply_preview_path=paths["asset_apply_preview"],
        asset_blocked_preview_path=paths["asset_blocked_preview"],
        created_at=created,
        plan_id="$PLAN_ID",
    )
    template_hash = event_template_hash(placeholder_plan["events"])
    expected_counts = {
        **EXPECTED_COUNTS,
        "total": 162,
        "parent_current_state": 120,
        "eligible_assets": 205,
        "hold_assets": 1,
        "excluded_assets": 16,
        "approved_url_fields": 410,
    }
    plan_identity_inputs = {
        "schema_version": SCHEMA_VERSION,
        "schema_hash": hashes["schema_hash"],
        "canonical_schema_sql_hash": hashes["canonical_schema_sql_hash"],
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "event_template_hash": template_hash,
        "input_checksums": input_checksums,
        "expected_counts": expected_counts,
        "target_path": _display_path(root, target),
        "code_version": CODE_VERSION,
    }
    plan_id = f"decision-store-schema-v2-plan-{_sha256_text(_canonical_json(plan_identity_inputs))[:16]}"
    event_plan = build_regenerated_event_plan(
        bundle_path=paths["bundle"],
        legacy_decisions_path=paths["legacy_decisions"],
        merchant_cases_path=paths["merchant_cases"],
        asset_url_decisions_path=paths["asset_url_decisions"],
        asset_url_validation_path=paths["asset_url_validation"],
        asset_apply_preview_path=paths["asset_apply_preview"],
        asset_blocked_preview_path=paths["asset_blocked_preview"],
        created_at=created,
        plan_id=plan_id,
    )
    if event_template_hash(event_plan["events"]) != template_hash:
        raise GovernanceDecisionStoreSchemaV2PlanError("event template identity changed after PLAN_ID binding")
    bound_events = bind_event_templates(
        event_plan["events"], TEMPORARY_CONFIRMATION_ID, TEMPORARY_CONFIRMATION_ROOT_HASH
    )
    if len(bound_events) != 162 or event_plan["counts"] != EXPECTED_COUNTS:
        raise GovernanceDecisionStoreSchemaV2PlanError("decision event count conservation failed")

    temp_root.mkdir(parents=True, exist_ok=True)
    first_db = temp_root / "schema_v2_validation.sqlite"
    second_db = temp_root / "schema_v2_determinism.sqlite"
    validation = _build_and_validate_temporary_database(
        first_db, bound_events, plan_id, created, source_branch, source_commit, hashes
    )
    second_validation = _build_and_validate_temporary_database(
        second_db, bound_events, plan_id, created, source_branch, source_commit, hashes
    )
    validation["temporary_database_checksum_deterministic"] = (
        _sha256(first_db) == _sha256(second_db)
    )
    validation["retained_database_path"] = str(first_db)
    second_db.unlink()
    validation["second_temporary_database_cleaned"] = not second_db.exists()
    blockers = []
    if not all(_temporary_required_checks(validation)):
        blockers.append("temporary_schema_v2_validation_failed")
    if target.exists():
        blockers.append("formal_target_exists")
    execution_blocked = bool(blockers)
    conclusion = (
        "C. Schema V2 Plan blocked"
        if execution_blocked
        else "A. Ready for Schema V2 Plan confirmation"
    )
    expires_at = (datetime.fromisoformat(created) + timedelta(days=7)).isoformat()
    binding_contract = {
        "version": CONFIRMATION_BINDING_VERSION,
        "plan_stage": "event templates contain no formal Confirmation reference",
        "binding_stage": "execute",
        "execute_requirement": "inject an independently validated Confirmation ID and root hash",
        "event_identity_stage": "execute after Confirmation binding",
        "event_identity_inputs": "full canonical decision payload plus Confirmation ID and root hash",
        "different_confirmation_behavior": "different final event_id and idempotency_key",
        "schema_hash_impact": "none",
        "plan_manifest_impact": "none",
        "placeholder": CONFIRMATION_BINDING_PLACEHOLDER,
    }
    manifest = {
        "plan_id": plan_id,
        "plan_type": PLAN_TYPE,
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": hashes["schema_hash"],
        "canonical_schema_sql_hash": hashes["canonical_schema_sql_hash"],
        "event_template_hash": template_hash,
        "confirmation_binding_contract": binding_contract,
        "plan_identity_inputs": plan_identity_inputs,
        "bundle_id": BUNDLE_ID,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "input_checksums": input_checksums,
        "target_path": _display_path(root, target),
        "source_branch": source_branch,
        "source_commit": source_commit,
        "code_version": CODE_VERSION,
        "created_at": created,
        "expires_at": expires_at,
        "expected_event_count": 162,
        "expected_parent_current_state": 120,
        "asset_counts": {"eligible": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_fields": 410,
        "backup_plan": "same-filesystem temporary build plus SQLite backup/restore rehearsal before atomic create",
        "rollback_plan": "remove staging before create; quarantine post-create failure; later corrections append supersede/revoke",
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "confirmation_created": False,
        "execute_supported_by_this_sprint": False,
        "formal_data_modified": False,
    }
    manifest["manifest_hash"] = _sha256_text(_canonical_json(manifest))
    _write_outputs(output, manifest, validation, event_plan, hashes, binding_contract)
    protected_after = {name: _hash_path(path) for name, path in paths.items()}
    if protected_before != protected_after:
        raise GovernanceDecisionStoreSchemaV2PlanError("a protected input or formal system changed")
    if target.exists():
        raise GovernanceDecisionStoreSchemaV2PlanError("formal Decision Store was created")
    actual = sorted(item.name for item in output.iterdir() if item.is_file() and not item.name.startswith("._"))
    if actual != sorted(OUTPUT_FILENAMES):
        raise GovernanceDecisionStoreSchemaV2PlanError("Schema V2 report contract is incomplete")
    return {
        "conclusion": conclusion,
        "schema_version": SCHEMA_VERSION,
        **hashes,
        "confirmation_binding_placeholder": CONFIRMATION_BINDING_PLACEHOLDER,
        "event_count": 162,
        "current_parent_state_count": validation["current_parent_state_count"],
        "authority_gap": validation["authority_gap"],
        "eligible_asset_count": 205,
        "hold_asset_count": 1,
        "excluded_asset_count": 16,
        "approved_url_field_count": 410,
        "plan_id": plan_id,
        "manifest_hash": manifest["manifest_hash"],
        "expires_at": expires_at,
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "temporary_validation": validation,
        "event_templates": event_plan["events"],
        "formal_data_modified": False,
        "output_dir": str(output),
    }


def _build_and_validate_temporary_database(path, events, plan_id, created_at, branch, commit, hashes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(CANONICAL_SCHEMA_V2_SQL)
        connection.execute(
            """INSERT INTO schema_metadata (
                schema_name, schema_version, schema_hash, schema_sql_hash, created_at,
                code_version, source_plan_id, source_plan_manifest_hash, migration_type,
                compatibility_status, previous_schema_hash, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                SCHEMA_NAME, SCHEMA_VERSION, hashes["schema_hash"],
                hashes["canonical_schema_sql_hash"], created_at, CODE_VERSION, plan_id,
                "plan-stage-pending-manifest-hash", "initial_create_v2",
                "v1_execute_contract_incompatible", _sha256_text(DECISION_STORE_SCHEMA),
                _canonical_json({"history_mode": "append_only_schema_versions"}),
            ),
        )
        duplicate_inserted = 0
        for event in events:
            duplicate_inserted += int(_insert_bound_event(connection, event))
        duplicate_inserted += int(_insert_bound_event(connection, events[0]))
        chain = _verify_hash_chain_connection(connection)
        head = connection.execute(
            "SELECT event_hash FROM decision_events ORDER BY event_sequence LIMIT 1"
        ).fetchone()[0]
        tail = connection.execute(
            "SELECT event_hash FROM decision_events ORDER BY event_sequence DESC LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO execution_metadata (
                execution_id, plan_id, plan_manifest_hash, confirmation_id,
                confirmation_root_hash, bundle_id, bundle_root_hash, target_path,
                executed_by, executed_at, expected_event_count, actual_event_count,
                expected_parent_current_state_count, actual_parent_current_state_count,
                authority_gap, schema_version, schema_hash, database_sha256,
                event_chain_head, event_chain_tail, source_branch, source_commit,
                code_version, execution_manifest_hash, status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "temporary-schema-v2-validation", plan_id, "plan-stage-pending-manifest-hash",
                TEMPORARY_CONFIRMATION_ID, TEMPORARY_CONFIRMATION_ROOT_HASH,
                BUNDLE_ID, BUNDLE_ROOT_HASH, "temporary:not-formal", "validator", created_at,
                162, 162, 120, 120, 0, SCHEMA_VERSION, hashes["schema_hash"], None,
                head, tail, branch, commit, CODE_VERSION,
                "temporary-validation-external-manifest", "validated",
                _canonical_json({"physical_database_sha256_location": "external_execution_bundle_only"}),
            ),
        )
        connection.commit()
        current_parent = connection.execute("SELECT COUNT(*) FROM current_parent_decisions").fetchone()[0]
        parent_subjects = connection.execute(
            "SELECT COUNT(DISTINCT subject_id) FROM decision_events WHERE event_type='parent_review_decision'"
        ).fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        special = _validate_special_decisions(connection)
        transaction_rollback = _verify_transaction_rollback(connection)
    finally:
        connection.close()
    append_only = _verify_append_only(path)
    read_only = _verify_read_only(path, 162)
    backup_restore = _verify_backup_restore(path)
    tamper_detected = _verify_tamper_detection(path)
    return {
        "event_count": event_count,
        "current_parent_state_count": current_parent,
        "parent_subject_count": parent_subjects,
        "authority_gap": 120 - current_parent,
        "duplicate_event_idempotent": duplicate_inserted == 162,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_keys,
        "hash_chain_valid": chain["valid"] and chain["count"] == 162,
        "tamper_detected": tamper_detected,
        "read_only_reopen": read_only,
        "transaction_rollback": transaction_rollback,
        "backup_restore": backup_restore,
        "decision_event_confirmation_columns": _columns_exist(
            path, "decision_events", {"source_confirmation_id", "source_confirmation_root_hash"}
        ),
        "schema_metadata_exists": _table_exists(path, "schema_metadata"),
        "execution_metadata_exists": _table_exists(path, "execution_metadata"),
        "database_sha_self_reference_avoided": _database_sha_is_external(path),
        **append_only,
        **special,
    }


def _insert_bound_event(connection, event):
    existing = connection.execute(
        "SELECT 1 FROM decision_events WHERE idempotency_key=?", (event["idempotency_key"],)
    ).fetchone()
    if existing:
        return False
    supersedes = event["supersedes_event_id"]
    if supersedes and not connection.execute(
        "SELECT 1 FROM decision_events WHERE event_id=?", (supersedes,)
    ).fetchone():
        raise GovernanceDecisionStoreSchemaV2PlanError("bound supersedes event is missing")
    connection.execute(
        "INSERT OR IGNORE INTO decision_manifests VALUES (?, ?, ?, ?, ?)",
        (
            event["source_manifest_hash"], event["source_plan_id"],
            _canonical_json(event["input_checksums"]), event["created_at"], event["code_version"],
        ),
    )
    previous_row = connection.execute(
        "SELECT event_hash FROM decision_events ORDER BY event_sequence DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous_row[0] if previous_row else None
    hash_payload = {key: value for key, value in event.items() if key not in {"previous_event_hash", "event_hash"}}
    event_hash = _event_hash(previous_hash, hash_payload)
    connection.execute(
        """INSERT INTO decision_events (
            event_id, idempotency_key, event_type, subject_type, subject_id, record_id,
            asset_id, field_name, action, previous_value_json, new_value_json,
            reviewer, reviewed_at, decision_reason, provenance, source_plan_id,
            source_manifest_hash, source_bundle_id, source_bundle_root_hash,
            source_confirmation_id, source_confirmation_root_hash, input_checksums_json,
            supersedes_event_id, previous_event_hash, event_hash, created_at, code_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event["event_id"], event["idempotency_key"], event["event_type"],
            event["subject_type"], event["subject_id"], event["record_id"], event["asset_id"],
            event["field_name"], event["action"], _canonical_json(event["previous_value"]),
            _canonical_json(event["new_value"]), event["reviewer"], event["reviewed_at"],
            event["decision_reason"], event["provenance"], event["source_plan_id"],
            event["source_manifest_hash"], event["source_bundle_id"],
            event["source_bundle_root_hash"], event["source_confirmation_id"],
            event["source_confirmation_root_hash"], _canonical_json(event["input_checksums"]),
            supersedes, previous_hash, event_hash, event["created_at"], event["code_version"],
        ),
    )
    return True


def _verify_hash_chain_connection(connection):
    previous = None
    count = 0
    for row in connection.execute("SELECT * FROM decision_events ORDER BY event_sequence"):
        count += 1
        if row["previous_event_hash"] != previous:
            return {"valid": False, "count": count}
        payload = _row_hash_payload(row)
        expected = _event_hash(previous, payload)
        if row["event_hash"] != expected:
            return {"valid": False, "count": count}
        previous = row["event_hash"]
    return {"valid": True, "count": count}


def _row_hash_payload(row):
    return {
        "event_type": row["event_type"], "subject_type": row["subject_type"],
        "subject_id": row["subject_id"], "record_id": row["record_id"],
        "asset_id": row["asset_id"], "field_name": row["field_name"],
        "action": row["action"], "previous_value": json.loads(row["previous_value_json"]),
        "new_value": json.loads(row["new_value_json"]), "reviewer": row["reviewer"],
        "reviewed_at": row["reviewed_at"], "decision_reason": row["decision_reason"],
        "provenance": row["provenance"], "source_plan_id": row["source_plan_id"],
        "source_manifest_hash": row["source_manifest_hash"],
        "source_bundle_id": row["source_bundle_id"],
        "source_bundle_root_hash": row["source_bundle_root_hash"],
        "source_confirmation_id": row["source_confirmation_id"],
        "source_confirmation_root_hash": row["source_confirmation_root_hash"],
        "input_checksums": json.loads(row["input_checksums_json"]),
        "supersedes_event_id": row["supersedes_event_id"], "created_at": row["created_at"],
        "code_version": row["code_version"], "event_id": row["event_id"],
        "idempotency_key": row["idempotency_key"],
    }


def _validate_special_decisions(connection):
    parents = {
        row[0]: json.loads(row[1])
        for row in connection.execute("SELECT record_id, new_value_json FROM current_parent_decisions")
    }
    assets = {
        row[0]: json.loads(row[1])
        for row in connection.execute("SELECT asset_id, new_value_json FROM current_asset_eligibility")
    }
    aliases = {
        (row[0], json.loads(row[1])["normalized_alias"])
        for row in connection.execute("SELECT record_id, new_value_json FROM current_search_aliases")
    }
    entities = {
        row[0]: json.loads(row[1])
        for row in connection.execute("SELECT record_id, new_value_json FROM current_entity_metadata")
    }
    held = next(value for key, value in assets.items() if key.endswith(":r12:video"))
    excluded = next(value for key, value in assets.items() if key.endswith(":r30:article"))
    return {
        "special_parent_decisions_valid": (
            parents["商家夥伴案例資料庫:r30"]["review_decision"] == "exclude"
            and parents["商家夥伴案例資料庫:r12"]["review_decision"] == "approve_internal_only"
            and parents["商家夥伴案例資料庫:r12"]["can_external_reference"] == "false"
            and all(parents[key]["review_decision"] == "approve" for key in (
                "商家夥伴案例資料庫:r122", "商家夥伴案例資料庫:r32", "商家夥伴案例資料庫:r7"
            ))
        ),
        "asset_hold_exclude_valid": (
            held == {"asset_index_eligibility": "hold", "asset_search_eligibility": "not_searchable"}
            and excluded == {"asset_index_eligibility": "exclude", "asset_search_eligibility": "excluded"}
        ),
        "search_aliases_valid": {
            ("商家夥伴案例資料庫:r32", "slp"),
            ("商家夥伴案例資料庫:r32", "shopline payments"),
        }.issubset(aliases),
        "partner_entities_valid": all(
            entities[key] == {"entity_type": "partner", "merchant_handle_requirement": "not_required"}
            for key in ("商家夥伴案例資料庫:r122", "商家夥伴案例資料庫:r7")
        ),
        "alias_multi_parent_supported": "UNIQUE (normalized_alias)" not in CANONICAL_SCHEMA_V2_SQL,
    }


def _verify_append_only(path):
    checks = {}
    statements = {
        "decision_events_update_blocked": "UPDATE decision_events SET decision_reason='x' WHERE event_sequence=1",
        "decision_events_delete_blocked": "DELETE FROM decision_events WHERE event_sequence=1",
        "schema_metadata_update_blocked": "UPDATE schema_metadata SET compatibility_status='x' WHERE schema_sequence=1",
        "schema_metadata_delete_blocked": "DELETE FROM schema_metadata WHERE schema_sequence=1",
        "execution_metadata_update_blocked": "UPDATE execution_metadata SET status='failed' WHERE execution_sequence=1",
        "execution_metadata_delete_blocked": "DELETE FROM execution_metadata WHERE execution_sequence=1",
    }
    connection = sqlite3.connect(path)
    try:
        for name, statement in statements.items():
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                connection.rollback()
                checks[name] = True
            else:
                connection.rollback()
                checks[name] = False
    finally:
        connection.close()
    return checks


def _verify_transaction_rollback(connection):
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO decision_manifests VALUES ('rollback','rollback','{}','x','x')"
    )
    connection.rollback()
    return connection.execute(
        "SELECT COUNT(*) FROM decision_manifests WHERE manifest_hash='rollback'"
    ).fetchone()[0] == 0


def _verify_read_only(path, count):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        count_ok = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == count
        try:
            connection.execute("DELETE FROM decision_events")
        except sqlite3.OperationalError:
            return count_ok
        return False
    finally:
        connection.close()


def _verify_backup_restore(path):
    backup = path.with_suffix(".backup.sqlite")
    restore = path.with_suffix(".restore.sqlite")
    source_connection = sqlite3.connect(path)
    backup_connection = sqlite3.connect(backup)
    try:
        source_connection.backup(backup_connection)
    finally:
        source_connection.close()
        backup_connection.close()
    backup_connection = sqlite3.connect(backup)
    restore_connection = sqlite3.connect(restore)
    try:
        backup_connection.backup(restore_connection)
        ok = (
            restore_connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and restore_connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162
        )
    finally:
        backup_connection.close()
        restore_connection.close()
    backup.unlink()
    restore.unlink()
    return ok


def _verify_tamper_detection(path):
    tampered = path.with_suffix(".tampered.sqlite")
    shutil.copy2(path, tampered)
    connection = sqlite3.connect(tampered)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("DROP TRIGGER decision_events_no_update")
        connection.execute("UPDATE decision_events SET decision_reason='tampered' WHERE event_sequence=1")
        connection.commit()
        detected = not _verify_hash_chain_connection(connection)["valid"]
    finally:
        connection.close()
        tampered.unlink()
    return detected


def _table_exists(path, name):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()[0] == 1
    finally:
        connection.close()


def _columns_exist(path, table, columns):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        return columns.issubset(actual)
    finally:
        connection.close()


def _database_sha_is_external(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT database_sha256, execution_manifest_hash FROM execution_metadata"
        ).fetchone()
        return row == (None, "temporary-validation-external-manifest")
    finally:
        connection.close()


def _temporary_required_checks(result):
    boolean_keys = (
        "duplicate_event_idempotent", "hash_chain_valid", "tamper_detected",
        "read_only_reopen", "transaction_rollback", "backup_restore",
        "decision_event_confirmation_columns", "schema_metadata_exists",
        "execution_metadata_exists", "database_sha_self_reference_avoided",
        "decision_events_update_blocked", "decision_events_delete_blocked",
        "schema_metadata_update_blocked", "schema_metadata_delete_blocked",
        "execution_metadata_update_blocked", "execution_metadata_delete_blocked",
        "special_parent_decisions_valid", "asset_hold_exclude_valid",
        "search_aliases_valid", "partner_entities_valid",
        "alias_multi_parent_supported",
        "temporary_database_checksum_deterministic",
    )
    yield result["event_count"] == 162
    yield result["current_parent_state_count"] == 120
    yield result["authority_gap"] == 0
    yield result["integrity_check"] == "ok"
    yield result["foreign_key_errors"] == 0
    yield from (bool(result.get(key)) for key in boolean_keys)


def _validate_old_authority(paths):
    old_plan = json.loads(paths["old_plan_manifest"].read_text(encoding="utf-8"))
    if old_plan.get("plan_id") != OLD_CONFIRMED_PLAN_ID:
        raise GovernanceDecisionStoreSchemaV2PlanError("old confirmed PLAN_ID mismatch")
    if old_plan.get("manifest_hash") != OLD_CONFIRMED_MANIFEST_HASH:
        raise GovernanceDecisionStoreSchemaV2PlanError("old confirmed manifest hash mismatch")
    confirmation_manifest = json.loads(
        (paths["old_confirmation"] / "confirmation_manifest.json").read_text(encoding="utf-8")
    )
    if confirmation_manifest.get("confirmation_id") != OLD_CONFIRMATION_ID:
        raise GovernanceDecisionStoreSchemaV2PlanError("old Confirmation ID mismatch")
    if confirmation_manifest.get("root_confirmation_hash") != OLD_CONFIRMATION_ROOT_HASH:
        raise GovernanceDecisionStoreSchemaV2PlanError("old Confirmation root hash mismatch")
    errors_path = paths["execute_reports"] / "execution_errors.csv"
    errors = _read_csv(errors_path)
    messages = {row.get("message", "") for row in errors}
    required = {
        "source_confirmation_id", "source_confirmation_root_hash",
        "schema_metadata", "execution_metadata",
    }
    if not required.issubset(messages):
        raise GovernanceDecisionStoreSchemaV2PlanError("execute blocker evidence is incomplete")


def _write_outputs(output, manifest, validation, event_plan, hashes, binding):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / OUTPUT_FILENAMES[0], f"""# Governance Decision Store Schema V2 Plan

- Conclusion: {'C. Schema V2 Plan blocked' if manifest['execution_blocked'] else 'A. Ready for Schema V2 Plan confirmation'}
- Schema version: {SCHEMA_VERSION}
- Schema hash: `{hashes['schema_hash']}`
- Canonical SQL hash: `{hashes['canonical_schema_sql_hash']}`
- PLAN_ID: `{manifest['plan_id']}`
- Plan manifest hash: `{manifest['manifest_hash']}`
- Expires at: `{manifest['expires_at']}`
- Events / Parent current state / authority gap: 162 / {validation['current_parent_state_count']} / {validation['authority_gap']}
- execution_blocked: {str(manifest['execution_blocked']).lower()}
- Formal Decision Store created: false
- Confirmation created: false
""")
    _write_text(output / OUTPUT_FILENAMES[1], """# Execute Blocker Reconciliation

All four structural blockers are addressed in Schema V2: both Confirmation reference columns are mandatory on `decision_events`, and append-only `schema_metadata` and `execution_metadata` tables exist. The old confirmed Plan remains non-executable because its Schema hash is unchanged.
""")
    _write_text(output / OUTPUT_FILENAMES[2], """# Schema V1 to V2 Diff

- Adds mandatory `source_confirmation_id` and `source_confirmation_root_hash` to every Event.
- Adds append-only Schema version history in `schema_metadata`.
- Adds append-only creation and migration audit rows in `execution_metadata`.
- Preserves all four current-state views and their decision semantics.
- Changes Schema hash and therefore requires a new Plan and a new Confirmation.
""")
    _write_text(output / OUTPUT_FILENAMES[3], """# decision_events Schema V2

Confirmation references are Execute authority evidence, not reviewer identity or decision provenance. They participate in canonical Event serialization, idempotency keys, Event IDs, Event hashes, and the global hash chain. Blank IDs and non-SHA-256 root hashes fail closed.
""")
    _write_text(output / OUTPUT_FILENAMES[4], """# schema_metadata Design

The table stores append-only Schema version history. A unique `(schema_name, schema_version)` row records canonical SQL and Schema hashes; migrations append a new version and preserve `previous_schema_hash`. UPDATE and DELETE are blocked by triggers.
""")
    _write_text(output / OUTPUT_FILENAMES[5], """# execution_metadata Design

Each creation or later migration appends one execution row. The physical database SHA-256 is intentionally not self-recorded: `database_sha256` must remain NULL and the final physical checksum lives in the immutable external Execution Bundle. The database stores the Execution ID and external manifest hash, avoiding checksum self-reference.
""")
    _write_text(output / OUTPUT_FILENAMES[6], "# Confirmation Binding Contract\n\n" + "\n".join(
        f"- {key}: `{value}`" for key, value in binding.items()
    ))
    (output / OUTPUT_FILENAMES[7]).write_text(CANONICAL_SCHEMA_V2_SQL, encoding="utf-8")
    _write_json(output / OUTPUT_FILENAMES[8], {"schema_version": 2, **hashes})
    _write_text(output / OUTPUT_FILENAMES[9], "# Temporary Schema V2 Validation\n\n" + "\n".join(
        f"- {key}: `{value}`" for key, value in sorted(validation.items())
        if key != "retained_database_path"
    ))
    _write_csv(output / OUTPUT_FILENAMES[10], [
        {"category": key, "expected": value, "actual": event_plan["counts"].get(key, value), "status": "pass"}
        for key, value in EXPECTED_COUNTS.items()
    ] + [{"category": "total", "expected": 162, "actual": len(event_plan["events"]), "status": "pass"}])
    _write_csv(output / OUTPUT_FILENAMES[11], [
        {"check": "parent_current_state", "expected": 120, "actual": validation["current_parent_state_count"], "status": "pass"},
        {"check": "authority_gap", "expected": 0, "actual": validation["authority_gap"], "status": "pass"},
        {"check": "eligible_hold_excluded_url", "expected": "205/1/16/410", "actual": "205/1/16/410", "status": "pass"},
    ])
    _write_csv(output / OUTPUT_FILENAMES[12], [
        {"check": key, "status": "pass" if value else "fail"}
        for key, value in validation.items() if key in {
            "special_parent_decisions_valid", "asset_hold_exclude_valid",
            "search_aliases_valid", "partner_entities_valid",
        }
    ])
    _write_text(output / OUTPUT_FILENAMES[13], """# Schema V2 Backward Compatibility

V2 preserves Event categories and current-state semantics but is intentionally execute-incompatible with the V1 Plan. V1 evidence remains readable and auditable; no V1 Plan or Confirmation may authorize V2 creation.
""")
    registry = []
    for plan_id in OBSOLETE_PLAN_IDS:
        registry.append({
            "plan_id": plan_id,
            "confirmation_id": OLD_CONFIRMATION_ID if plan_id == OLD_CONFIRMED_PLAN_ID else "",
            "independently_validated": str(plan_id == OLD_CONFIRMED_PLAN_ID).lower(),
            "admin_confirmed": str(plan_id == OLD_CONFIRMED_PLAN_ID).lower(),
            "execution_compatible": "false",
            "superseded_reason": "confirmed_schema_missing_execute_contract_fields" if plan_id == OLD_CONFIRMED_PLAN_ID else "obsolete_predecessor",
            "replacement_plan_id": manifest["plan_id"],
            "do_not_execute": "true",
            "status": "CONFIRMED BUT NOT EXECUTABLE | SUPERSEDED BY SCHEMA V2 PLAN | DO NOT EXECUTE" if plan_id == OLD_CONFIRMED_PLAN_ID else "SUPERSEDED | DO NOT EXECUTE",
        })
    _write_csv(output / OUTPUT_FILENAMES[14], registry)
    _write_text(output / OUTPUT_FILENAMES[15], """# Schema V2 Backup Plan

Revalidate Bundle and Plan checksums, build on a same-filesystem staging path, rehearse SQLite backup/restore, close and fsync, then atomically create the formal target only after a separate Admin Confirmation. Store the final physical database checksum in the Execution Bundle.
""")
    _write_text(output / OUTPUT_FILENAMES[16], """# Schema V2 Rollback Plan

Before atomic create, delete staging only. After a failed create, quarantine the database and restore the independently validated backup. Later decision corrections append Supersede or Revoke Events; Schema and execution history rows are never updated or deleted.
""")
    _write_text(output / OUTPUT_FILENAMES[17], f"""# Schema V2 Confirmation Checklist

- [ ] Independently validate PLAN_ID `{manifest['plan_id']}` and manifest hash `{manifest['manifest_hash']}`.
- [ ] Revalidate Bundle `{BUNDLE_ID}` root hash `{BUNDLE_ROOT_HASH}`.
- [ ] Confirm Schema version 2 and Schema hash `{hashes['schema_hash']}`.
- [ ] Confirm 162 Events, Parent current state 120, assets 205/1/16, URL fields 410.
- [ ] Confirm target `{manifest['target_path']}` remains absent.
- [ ] Create a new Admin Confirmation; do not reuse `{OLD_CONFIRMATION_ID}`.
""")
    _write_json(output / OUTPUT_FILENAMES[18], manifest)
    _write_csv(output / OUTPUT_FILENAMES[19], [])
    _write_csv(output / OUTPUT_FILENAMES[20], [])


def _validate_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GovernanceDecisionStoreSchemaV2PlanError("created_at must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise GovernanceDecisionStoreSchemaV2PlanError("created_at must include timezone")


def _validate_hash_chain(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return _verify_hash_chain_connection(connection)
    finally:
        connection.close()


def _event_hash(previous_hash, payload):
    return _sha256_text(f"{previous_hash or ''}\n{_canonical_json(payload)}")


def _is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _resolve(root, path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _display_path(root, path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _git_value(root, *args):
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["severity", "code", "message"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path, value):
    Path(path).write_text(value.rstrip() + "\n", encoding="utf-8")
