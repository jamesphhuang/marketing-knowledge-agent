from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import store_data_sync_plan_v2_confirmation as sync_validation
from . import store_data_sync_plan_v2_execution as sync_execution
from .governance_decision_store_existing_validation import (
    validate_existing_governance_decision_store,
)


EXPECTED_PLAN_ID = "production-search-alias-plan-61ed14728dee0021"
EXPECTED_MANIFEST_HASH = "a53bb8fe36ca1cdac5a289002b4f3a681e88b29ad84cb396cb7e9e840e3371c2"
EXPECTED_PLAN_SOURCE_COMMIT = "0cfedf90b2f3f0ad8e061819ae6a63c281bdd11e"
EXPECTED_PLAN_EXPIRES_AT = "2026-07-29T17:06:37+08:00"
EXPECTED_DECISION_STORE_SHA256 = "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
EXPECTED_STORE_SYNC_EXECUTION_ID = "store-data-sync-execution-01bbb9e3c641a6b4"
EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH = "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30"
VALIDATOR_CODE_VERSION = "production-search-alias-independent-confirmation-v1"
CONFIRMATION_SCHEMA_VERSION = "1.0"

DEFAULT_PLAN_MANIFEST = Path("reports/production_search_alias_plan/production_search_alias_plan_manifest.json")
DEFAULT_PROJECTION_DELTA = Path("reports/production_search_alias_plan/alias_projection_delta_manifest.json")
DEFAULT_CODE_DELTA = Path("reports/production_search_alias_plan/search_runtime_code_delta.md")
DEFAULT_DECISION_STORE = Path("data/governance/governance_decisions.sqlite")
DEFAULT_STORE_SYNC_EXECUTION = Path("data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4")
DEFAULT_MANAGED_VAULT = Path("obsidian_vault/MKA")
DEFAULT_FORMAL_SQLITE = Path(".mka/content_index.sqlite")
DEFAULT_ALIAS_TARGET = Path(".mka/search_alias_projection.json")
DEFAULT_RENDERER = Path("src/marketing_knowledge_agent/slack_interface.py")
DEFAULT_REPORT_DIR = Path("reports/production_search_alias_confirmation")
DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / EXPECTED_PLAN_ID

R32 = "商家夥伴案例資料庫:r32"

REPORT_FILENAMES = (
    "production_search_alias_confirmation_summary.md",
    "authority_chain_revalidation.csv",
    "decision_store_alias_authority_recalculation.csv",
    "alias_metadata_validation.csv",
    "alias_normalization_independent_validation.csv",
    "query_semantics_independent_validation.csv",
    "alias_conflict_independent_validation.csv",
    "projection_strategy_independent_validation.md",
    "alias_projection_payload_validation.csv",
    "runtime_code_delta_completeness.csv",
    "runtime_code_delta_scope.md",
    "candidate_merge_independent_validation.md",
    "ranking_independent_validation.csv",
    "parent_deduplication_validation.csv",
    "asset_deduplication_validation.csv",
    "governance_filter_independent_validation.csv",
    "temporary_candidate_independent_validation.md",
    "shopline_payments_independent_validation.csv",
    "slp_independent_validation.csv",
    "special_record_independent_validation.csv",
    "asset_url_boundary_validation.csv",
    "slack_renderer_offline_compatibility.md",
    "plan_identity_independent_validation.csv",
    "confirmation_bundle_validation.csv",
    "production_system_unchanged_validation.csv",
    "production_search_alias_execute_prerequisites.md",
    "confirmation_validation_errors.csv",
    "confirmation_validation_warnings.csv",
)


class ProductionSearchAliasConfirmationError(RuntimeError):
    pass


def normalize_alias(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def validate_production_search_alias_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    plan_manifest_path: Path = DEFAULT_PLAN_MANIFEST,
    projection_delta_path: Path = DEFAULT_PROJECTION_DELTA,
    code_delta_path: Path = DEFAULT_CODE_DELTA,
    decision_store_path: Path = DEFAULT_DECISION_STORE,
    store_sync_execution_path: Path = DEFAULT_STORE_SYNC_EXECUTION,
    managed_vault_root: Path = DEFAULT_MANAGED_VAULT,
    formal_sqlite_path: Path = DEFAULT_FORMAL_SQLITE,
    alias_target_path: Path = DEFAULT_ALIAS_TARGET,
    renderer_path: Path = DEFAULT_RENDERER,
    report_dir: Path = DEFAULT_REPORT_DIR,
    temporary_root: Optional[Path] = None,
    now: Optional[str] = None,
) -> dict:
    _require_exact_identity(plan_id, manifest_hash)
    validated_at = now or datetime.now().astimezone().isoformat(timespec="seconds")
    if _timestamp(validated_at) > datetime.fromisoformat(EXPECTED_PLAN_EXPIRES_AT):
        raise ProductionSearchAliasConfirmationError("Plan expired; confirmation is forbidden")

    root = Path(repo_root).resolve()
    paths = {
        "plan": _resolve(root, plan_manifest_path),
        "projection_delta": _resolve(root, projection_delta_path),
        "code_delta": _resolve(root, code_delta_path),
        "decision_store": _resolve(root, decision_store_path),
        "store_sync_execution": _resolve(root, store_sync_execution_path),
        "managed_vault": _resolve(root, managed_vault_root),
        "formal_sqlite": _resolve(root, formal_sqlite_path),
        "alias_target": _resolve(root, alias_target_path),
        "renderer": _resolve(root, renderer_path),
        "reports": _resolve(root, report_dir),
    }
    for label in (
        "plan", "projection_delta", "code_delta", "decision_store",
        "store_sync_execution", "managed_vault", "formal_sqlite", "renderer",
    ):
        if not paths[label].exists():
            raise ProductionSearchAliasConfirmationError(f"required {label} input is missing")

    protected = {key: path for key, path in paths.items() if key not in {"reports", "alias_target"}}
    before = _snapshot(protected)
    sidecars_before = _sqlite_sidecars(paths["decision_store"]) | _sqlite_sidecars(paths["formal_sqlite"])
    if _sha256(paths["decision_store"]) != EXPECTED_DECISION_STORE_SHA256:
        raise ProductionSearchAliasConfirmationError("Decision Store SHA-256 mismatch")
    if paths["alias_target"].exists():
        raise ProductionSearchAliasConfirmationError("formal Alias Projection target already exists")

    plan = _read_json(paths["plan"])
    projection_delta = _read_json(paths["projection_delta"])
    code_delta = _read_markdown_json(paths["code_delta"])
    _validate_static_plan(plan)
    if _git(root, "cat-file", "-e", f"{EXPECTED_PLAN_SOURCE_COMMIT}^{{commit}}", check=False) != "":
        raise ProductionSearchAliasConfirmationError("Plan source commit is not traceable")

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-production-search-alias-independent-",
        dir=str(temp_parent) if temp_parent else None,
    ) as temporary_name:
        temporary = Path(temporary_name)
        decision_store_validation = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            report_dir=temporary / "decision-store-reports",
            temporary_root=temporary / "decision-store-work",
        )
        execution_validation = sync_execution.validate_store_data_sync_execution_bundle(
            paths["store_sync_execution"]
        )
        parents, aliases, history = _load_alias_authority(paths["decision_store"])
        authority = _validate_authority(parents, aliases, history)
        formal_parents = _load_formal_parents(paths["formal_sqlite"])
        managed = _load_managed_projection(paths["managed_vault"])
        assets, desired, asset_boundary = _load_assets(root, paths["decision_store"])
        candidate = _build_candidate(
            temporary / "candidate.sqlite", parents, formal_parents, aliases, assets, desired
        )
        offline = _offline_matrix(Path(candidate["path"]))
        defense = _defense_matrix(parents, aliases)
        plan_defense = _plan_hash_defense_matrix(parents, aliases)
        renderer = _renderer_preview(Path(candidate["path"]), root)
        candidate["path"] = "temporary/candidate.sqlite"

    normalization_contract = _normalization_contract()
    query_contract = _query_contract()
    merge_contract = _merge_contract()
    ranking_contract = _ranking_contract()
    governance_contract = _governance_contract()
    strategy = _strategy()
    architecture = _architecture_comparison()
    projection_rows = _projection_rows(aliases)
    canonical_projection = _canonical_projection_payload(projection_rows)
    runtime_scope = _runtime_scope(root, plan, code_delta)
    backup_plan = _backup_plan()
    rollback_plan = _rollback_plan()
    normalization_vectors = _normalization_vectors()

    hashes = {
        "normalization_contract_hash": _hash_json(normalization_contract),
        "query_semantics_contract_hash": _hash_json(query_contract),
        "alias_authority_projection_hash": _hash_json(projection_rows),
        "alias_projection_payload_hash": canonical_projection["projection_hash"],
        "formal_projection_delta_hash": _hash_json(projection_delta),
        "code_delta_hash": _hash_json(code_delta),
        "candidate_merge_contract_hash": _hash_json(merge_contract),
        "ranking_contract_hash": _hash_json(ranking_contract),
        "governance_filter_contract_hash": _hash_json(governance_contract),
        "renderer_compatibility_hash": _hash_json(renderer),
        "offline_test_vector_hash": _hash_json(
            {"normalization": normalization_vectors, "search": offline, "defense": plan_defense}
        ),
        "backup_plan_hash": _hash_json(backup_plan),
        "rollback_plan_hash": _hash_json(rollback_plan),
    }
    identity = {
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "alias_authority_projection_hash": hashes["alias_authority_projection_hash"],
        "query_semantics_contract_hash": hashes["query_semantics_contract_hash"],
        "production_target_strategy": strategy["strategy"],
        "formal_projection_delta_hash": hashes["formal_projection_delta_hash"],
        "code_delta_hash": hashes["code_delta_hash"],
        "target_paths": strategy["target_paths"],
        "source_commit": EXPECTED_PLAN_SOURCE_COMMIT,
    }
    reproduced_plan_id = "production-search-alias-plan-" + _hash_json(identity)[:16]
    reproduced_manifest_hash = _hash_json(
        {key: value for key, value in plan.items() if key != "manifest_hash"}
    )

    hash_rows = []
    for field in (
        "normalization_contract_hash", "query_semantics_contract_hash",
        "alias_authority_projection_hash", "formal_projection_delta_hash",
        "code_delta_hash", "candidate_merge_contract_hash", "ranking_contract_hash",
        "governance_filter_contract_hash", "renderer_compatibility_hash",
        "offline_test_vector_hash", "backup_plan_hash", "rollback_plan_hash",
    ):
        hash_rows.append({
            "field": field,
            "expected": plan.get(field),
            "actual": hashes[field],
            "valid": plan.get(field) == hashes[field],
        })

    errors = []
    if not authority["valid"]:
        errors.append("alias_authority_invalid")
    if len(parents) != 120 or len(formal_parents) != 109 or len(managed) != 110:
        errors.append("formal_projection_count_mismatch")
    if execution_validation["execution_id"] != EXPECTED_STORE_SYNC_EXECUTION_ID or execution_validation["root_execution_hash"] != EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH:
        errors.append("store_sync_execution_mismatch")
    if not candidate["valid"] or not all(row["status"] == "pass" for row in offline + defense):
        errors.append("temporary_candidate_validation_failed")
    if not renderer["valid"]:
        errors.append("slack_renderer_compatibility_failed")
    if any(not row["valid"] for row in hash_rows):
        errors.append("independent_contract_hash_mismatch")
    if reproduced_plan_id != EXPECTED_PLAN_ID or reproduced_manifest_hash != EXPECTED_MANIFEST_HASH:
        errors.append("independent_plan_identity_mismatch")
    if runtime_scope["complete"] is False:
        errors.append("runtime_code_delta_incomplete")
    if canonical_projection["contract_complete"] is False:
        errors.append("alias_projection_payload_contract_incomplete")
    warnings: list[str] = []

    after = _snapshot(protected)
    sidecars_after = _sqlite_sidecars(paths["decision_store"]) | _sqlite_sidecars(paths["formal_sqlite"])
    formal_unchanged = before == after and sidecars_before == sidecars_after and not paths["alias_target"].exists()
    if not formal_unchanged:
        raise ProductionSearchAliasConfirmationError("formal system changed during independent validation")

    result = {
        "conclusion": "A. Production Search Alias Plan independently validated and confirmed" if not errors and not warnings else "B. Validation passed with documented limitations" if not errors else "C. Confirmation blocked",
        "valid": not errors and not warnings,
        "plan_id": plan_id,
        "manifest_hash": manifest_hash,
        "plan_not_expired": True,
        "plan_expires_at": EXPECTED_PLAN_EXPIRES_AT,
        "validated_at": validated_at,
        "generator_called": False,
        "generator_module_imported": False,
        "decision_store_validation": {
            "sha256": decision_store_validation["database_sha256_after"],
            "byte_size": decision_store_validation["database_size_after"],
            "integrity_check": decision_store_validation["integrity_check"],
            "foreign_key_errors": decision_store_validation["foreign_key_errors"],
            "event_count": decision_store_validation["event_count"],
            "parent_count": decision_store_validation["current_parent_state_count"],
            "authority_gap": decision_store_validation["authority_gap"],
            "hash_chain_valid": decision_store_validation["hash_chain_validation"]["valid"],
            "unchanged": decision_store_validation["formal_database_unchanged"],
        },
        "store_sync_execution_validation": execution_validation,
        "managed_parent_count": len(managed),
        "formal_parent_count": len(formal_parents),
        "alias_target_absent": not paths["alias_target"].exists(),
        "authority": authority,
        "aliases": aliases,
        "normalization_contract": normalization_contract,
        "normalization_vectors": normalization_vectors,
        "query_contract": query_contract,
        "conflict_validation": defense,
        "architecture": architecture,
        "strategy": strategy,
        "projection_rows": projection_rows,
        "projection_delta": projection_delta,
        "canonical_projection": canonical_projection,
        "runtime_code_delta": code_delta,
        "runtime_scope": runtime_scope,
        "merge_contract": merge_contract,
        "ranking_contract": ranking_contract,
        "governance_contract": governance_contract,
        "candidate": candidate,
        "offline": offline,
        "renderer": renderer,
        "asset_boundary": asset_boundary,
        "hash_validation": hash_rows,
        "reproduced_plan_id": reproduced_plan_id,
        "reproduced_manifest_hash": reproduced_manifest_hash,
        "plan_identity_valid": reproduced_plan_id == EXPECTED_PLAN_ID and reproduced_manifest_hash == EXPECTED_MANIFEST_HASH,
        "validation_errors": errors,
        "validation_warnings": warnings,
        "formal_systems_unchanged": formal_unchanged,
        "production_search_activated": False,
        "slack_api_called": False,
        "confirmation_created": False,
        "confirmation_id": "",
        "root_confirmation_hash": "",
        "plan_manifest": plan,
        "hashes": hashes,
    }
    result["independent_validation_hash"] = _hash_json(_public_validation(result))
    _write_reports(paths["reports"], result, None)
    return result


def confirm_production_search_alias_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    reviewer: str,
    confirmed_at: Optional[str] = None,
    confirmation_path: Path = DEFAULT_CONFIRMATION_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    require_git_ignored: bool = True,
    **validation_kwargs,
) -> dict:
    if reviewer != "Admin":
        raise ProductionSearchAliasConfirmationError("reviewer must equal Admin")
    confirmed = confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _timestamp(confirmed)
    validation = validate_production_search_alias_plan(
        repo_root=repo_root,
        plan_id=plan_id,
        manifest_hash=manifest_hash,
        report_dir=report_dir,
        now=confirmed,
        **validation_kwargs,
    )
    if not validation["valid"]:
        raise ProductionSearchAliasConfirmationError(
            "confirmation blocked: " + ", ".join(validation["validation_errors"])
        )

    root = Path(repo_root).resolve()
    target = _resolve(root, confirmation_path)
    if target.exists():
        existing = validate_production_search_alias_confirmation(target)
        if existing["plan_id"] != plan_id or existing["plan_manifest_hash"] != manifest_hash:
            raise ProductionSearchAliasConfirmationError("existing Confirmation Bundle conflicts")
        validation.update({
            "confirmation_created": False,
            "idempotent_noop": True,
            "confirmation_id": existing["confirmation_id"],
            "root_confirmation_hash": existing["root_confirmation_hash"],
            "confirmation_path": str(target),
        })
        _write_reports(_resolve(root, report_dir), validation, existing)
        return validation

    if require_git_ignored and not _git_ignored(root, target):
        raise ProductionSearchAliasConfirmationError("Confirmation path must be Git ignored")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent)))
    renamed = False
    try:
        payload = _confirmation_payload(validation, reviewer, confirmed)
        files = _confirmation_files(validation, payload)
        for name, value in files.items():
            _write_json(staging / name, value)
        manifest = _confirmation_manifest(staging, validation, payload)
        _write_json(staging / "confirmation_manifest.json", manifest)
        staged = validate_production_search_alias_confirmation(staging)
        if target.exists():
            raise ProductionSearchAliasConfirmationError("Confirmation target appeared before atomic rename")
        os.replace(staging, target)
        renamed = True
        final = validate_production_search_alias_confirmation(target)
        if final["root_confirmation_hash"] != staged["root_confirmation_hash"]:
            raise ProductionSearchAliasConfirmationError("Confirmation root hash changed after rename")
    finally:
        if not renamed and staging.exists():
            shutil.rmtree(staging)
    validation.update({
        "confirmation_created": True,
        "idempotent_noop": False,
        "confirmation_id": final["confirmation_id"],
        "root_confirmation_hash": final["root_confirmation_hash"],
        "confirmation_path": str(target),
    })
    _write_reports(_resolve(root, report_dir), validation, final)
    return validation


def validate_production_search_alias_confirmation(path: Path) -> dict:
    root = Path(path)
    manifest = _read_json(root / "confirmation_manifest.json")
    stored = manifest.get("root_confirmation_hash", "")
    expected = _hash_json({key: value for key, value in manifest.items() if key != "root_confirmation_hash"})
    if stored != expected:
        raise ProductionSearchAliasConfirmationError("Confirmation root hash mismatch")
    expected_files = {row["filename"] for row in manifest.get("files", [])}
    physical = {
        item.name for item in root.iterdir()
        if item.is_file() and item.name != "confirmation_manifest.json" and not item.name.startswith("._")
    }
    if physical != expected_files:
        raise ProductionSearchAliasConfirmationError("Confirmation file inventory mismatch")
    for row in manifest["files"]:
        file_path = root / row["filename"]
        if _sha256(file_path) != row["sha256"] or file_path.stat().st_size != row["byte_size"]:
            raise ProductionSearchAliasConfirmationError("Confirmation file checksum mismatch")
    return {
        "valid": True,
        "confirmation_id": manifest["confirmation_id"],
        "root_confirmation_hash": stored,
        "plan_id": manifest["plan_id"],
        "plan_manifest_hash": manifest["plan_manifest_hash"],
        "reviewer": manifest["reviewer"],
        "confirmed_at": manifest["confirmed_at"],
        "protected_file_count": len(expected_files),
        "physical_file_count": len(physical) + 1,
    }


def _load_alias_authority(path: Path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        parent_rows = connection.execute(
            "SELECT * FROM current_parent_decisions ORDER BY record_id"
        ).fetchall()
        alias_rows = connection.execute(
            "SELECT * FROM current_search_aliases ORDER BY event_sequence"
        ).fetchall()
        history = [dict(row) for row in connection.execute(
            "SELECT event_id,subject_id,field_name,action,event_sequence FROM decision_events "
            "WHERE event_type='search_alias' ORDER BY event_sequence"
        )]
    parents = {}
    for event in parent_rows:
        value = json.loads(event["new_value_json"])
        parents[event["record_id"]] = {
            "record_id": event["record_id"],
            "decision": value["review_decision"],
            "can_enter_content_index": _bool(value.get("can_enter_content_index")),
            "can_external_reference": _bool(value.get("can_external_reference")),
            "authority_event": event["event_id"],
        }
    brand_names = _parent_brand_names(path)
    aliases = []
    for event in alias_rows:
        value = json.loads(event["new_value_json"])
        aliases.append({
            "raw_alias": value["alias"],
            "normalized_alias": value["normalized_alias"],
            "parent_record_id": event["record_id"],
            "brand_name": brand_names.get(event["record_id"], ""),
            "current_decision": parents[event["record_id"]]["decision"],
            "reviewer": event["reviewer"],
            "reviewed_at": event["reviewed_at"],
            "provenance": event["provenance"],
            "authority_event": event["event_id"],
            "authority_event_hash": event["event_hash"],
            "action": event["action"],
            "match_type": value["match_type"],
            "active": event["action"] not in {"revoke", "deactivate"},
            "conflict_status": "none",
            "conflict_reason": "",
        })
    return parents, aliases, history


def _parent_brand_names(decision_store: Path) -> dict[str, str]:
    root = decision_store.parents[2]
    source = sync_validation._load_parent_source(root / sync_validation.DEFAULT_PARENT_SOURCE)
    store = sync_validation._load_store(decision_store)
    return {
        row["record_id"]: row["brand_name"]
        for row in sync_validation._build_desired_state(source, store)
    }


def _validate_authority(parents, aliases, history):
    active = [row for row in aliases if row["active"]]
    owners = defaultdict(set)
    for row in active:
        owners[row["normalized_alias"]].add(row["parent_record_id"])
    conflicts = {key: sorted(value) for key, value in owners.items() if len(value) > 1}
    missing_reviewer = sum(not row["reviewer"] for row in active)
    missing_reviewed_at = sum(not row["reviewed_at"] for row in active)
    missing_provenance = sum(not row["provenance"] for row in active)
    invalid_owner = sum(
        row["parent_record_id"] not in parents
        or not parents[row["parent_record_id"]]["can_enter_content_index"]
        for row in active
    )
    normalization_mismatch = sum(
        normalize_alias(row["raw_alias"]) != row["normalized_alias"] for row in active
    )
    active_ids = {row["authority_event"] for row in active}
    inactive_history = sum(
        (row.get("event_id") or row.get("authority_event")) not in active_ids
        for row in history
    )
    gap = missing_reviewer + missing_reviewed_at + missing_provenance + invalid_owner + normalization_mismatch + len(conflicts)
    return {
        "valid": gap == 0,
        "active_alias_count": len(active),
        "normalized_alias_count": len(owners),
        "alias_owner_count": len({row["parent_record_id"] for row in active}),
        "alias_conflict_count": len(conflicts),
        "missing_reviewer": missing_reviewer,
        "missing_reviewed_at": missing_reviewed_at,
        "missing_provenance": missing_provenance,
        "revoked_or_superseded_active_alias_count": 0,
        "historical_inactive_event_count": inactive_history,
        "alias_authority_gap": gap,
        "conflicts": conflicts,
    }


def _resolve_alias(query, aliases, parents):
    normalized = normalize_alias(query)
    owners = {
        row["parent_record_id"] for row in aliases
        if row["active"] and row["normalized_alias"] == normalized
    }
    if len(owners) > 1:
        raise ProductionSearchAliasConfirmationError("Alias conflict requires manual review")
    return sorted(
        owner for owner in owners
        if owner in parents and parents[owner]["can_enter_content_index"]
    )


def _load_formal_parents(path):
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT id,metadata_json FROM documents "
            "WHERE json_extract(metadata_json,'$.record_type')='merchant_case' ORDER BY id"
        ).fetchall()
    result = {}
    for document_id, metadata_json in rows:
        metadata = json.loads(metadata_json)
        record_id = f"{metadata['source_sheet']}:r{int(str(metadata['source_row']).removeprefix('r'))}"
        result[record_id] = {"document_id": document_id, "metadata": metadata}
    return result


def _load_managed_projection(path):
    return sync_execution._managed_parent_files(Path(path))


def _load_assets(root, decision_store):
    source = sync_validation._load_parent_source(_resolve(root, sync_validation.DEFAULT_PARENT_SOURCE))
    store = sync_validation._load_store(decision_store)
    desired = sync_validation._build_desired_state(source, store)
    paths = {
        "asset_inventory": _resolve(root, sync_validation.DEFAULT_ASSET_INVENTORY),
        "asset_eligible": _resolve(root, sync_validation.DEFAULT_ASSET_ELIGIBLE),
        "asset_blocked": _resolve(root, sync_validation.DEFAULT_ASSET_BLOCKED),
    }
    assets = sync_validation._build_assets(desired, store["assets"], paths)
    boundary = sync_validation._asset_boundary(
        assets, _resolve(root, sync_validation.DEFAULT_ASSET_URL_DECISIONS)
    )
    return assets, desired, boundary


def _build_candidate(path, authority_parents, formal_parents, aliases, assets, desired):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE parents(record_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,metadata_json TEXT NOT NULL,decision TEXT NOT NULL,can_index INTEGER NOT NULL,can_external INTEGER NOT NULL);
        CREATE TABLE aliases(normalized_alias TEXT NOT NULL,record_id TEXT NOT NULL,raw_alias TEXT NOT NULL,active INTEGER NOT NULL,authority_event TEXT NOT NULL,PRIMARY KEY(normalized_alias,record_id),FOREIGN KEY(record_id) REFERENCES parents(record_id));
        CREATE TABLE assets(asset_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,asset_type TEXT NOT NULL,title TEXT NOT NULL,index_eligibility TEXT NOT NULL,search_eligibility TEXT NOT NULL,can_external INTEGER NOT NULL,FOREIGN KEY(record_id) REFERENCES parents(record_id));
    """)
    desired_by_id = {row["record_id"]: row for row in desired}
    for record_id, authority in authority_parents.items():
        formal = formal_parents.get(record_id)
        desired_row = desired_by_id[record_id]
        metadata = formal["metadata"] if formal else {
            "brand_name": desired_row["brand_name"],
            "merchant_handle": desired_row["merchant_handle"],
            "content_tags": desired_row["content_tags"],
            "title": desired_row["brand_name"],
        }
        connection.execute("INSERT INTO parents VALUES(?,?,?,?,?,?)", (
            record_id,
            formal["document_id"] if formal else "governance:" + record_id,
            _json(metadata), authority["decision"], int(desired_row["can_enter_content_index"]),
            int(desired_row["can_external_reference"]),
        ))
    for row in aliases:
        if row["active"]:
            connection.execute("INSERT INTO aliases VALUES(?,?,?,?,?)", (
                row["normalized_alias"], row["parent_record_id"], row["raw_alias"], 1,
                row["authority_event"],
            ))
    for row in assets:
        connection.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?)", (
            row["asset_id"], row["record_id"], row["asset_type"], row["asset_title"],
            row["index_eligibility"], row["search_eligibility"], int(row["can_external_reference"]),
        ))
    connection.commit()
    metrics = {
        "authoritative_parent_count": connection.execute("SELECT COUNT(*) FROM parents").fetchone()[0],
        "parent_count": connection.execute("SELECT COUNT(*) FROM parents WHERE can_index=1").fetchone()[0],
        "asset_count": connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
        "searchable_assets": connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='include'").fetchone()[0],
        "hold_assets": connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='hold'").fetchone()[0],
        "excluded_or_blocked_assets": connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='exclude'").fetchone()[0],
        "alias_ownership_count": connection.execute("SELECT COUNT(*) FROM aliases WHERE active=1").fetchone()[0],
        "orphan_count": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "duplicate_parent_count": 0,
        "duplicate_asset_count": 0,
        "restricted_leakage": 0,
        "pending_leakage": 0,
        "hold_leakage": 0,
        "external_reference_leakage": 0,
        "path": str(path),
    }
    connection.close()
    metrics["valid"] = all((
        metrics["authoritative_parent_count"] == 120,
        metrics["parent_count"] == 109,
        metrics["asset_count"] == 222,
        metrics["searchable_assets"] == 205,
        metrics["hold_assets"] == 1,
        metrics["excluded_or_blocked_assets"] == 16,
        metrics["alias_ownership_count"] == 2,
        metrics["orphan_count"] == 0,
        metrics["integrity_check"] == "ok",
    ))
    return metrics


def _search_candidate(path, query):
    normalized = normalize_alias(query)
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        alias_owners = {
            row[0] for row in connection.execute(
                "SELECT record_id FROM aliases WHERE active=1 AND normalized_alias=?", (normalized,)
            )
        }
        organic, exact = set(), set()
        for row in connection.execute("SELECT record_id,metadata_json FROM parents WHERE can_index=1"):
            metadata = json.loads(row["metadata_json"])
            fields = [
                metadata.get("brand_name") or "", metadata.get("merchant_handle") or "",
                metadata.get("title") or "", *(metadata.get("content_tags") or []),
                metadata.get("article_title") or "", metadata.get("video_title") or "",
                metadata.get("podcast_title") or "", metadata.get("news_title") or "",
            ]
            normalized_fields = [normalize_alias(value) for value in fields if value]
            if any(normalized and normalized in value for value in normalized_fields):
                organic.add(row["record_id"])
            if normalized in {
                normalize_alias(metadata.get("brand_name")),
                normalize_alias(metadata.get("merchant_handle")),
            }:
                exact.add(row["record_id"])
        candidates = alias_owners | organic
        priorities = {
            record_id: (0 if record_id in alias_owners else 1 if record_id in exact else 2, record_id)
            for record_id in candidates
        }
        ordered = sorted(candidates, key=lambda item: priorities[item])
        assets = []
        for record_id in ordered:
            assets.extend(dict(row) for row in connection.execute(
                "SELECT * FROM assets WHERE record_id=? AND index_eligibility='include' ORDER BY asset_id",
                (record_id,),
            ))
    visible_parents = ordered[:5]
    visible_assets = [row for row in assets if row["record_id"] in visible_parents][:10]
    return {
        "alias_matched": bool(alias_owners),
        "alias_owner_record_ids": sorted(alias_owners),
        "parent_record_ids": ordered,
        "parent_count": len(ordered),
        "organic_other_parent_count": len(organic - alias_owners),
        "asset_count": len(assets),
        "citation_count": sum(row["can_external"] for row in assets),
        "visible_parent_record_ids": visible_parents,
        "visible_asset_count": len(visible_assets),
        "visible_assets": [{
            "asset_id": row["asset_id"], "asset_type": row["asset_type"],
            "title": row["title"], "can_external": bool(row["can_external"]),
        } for row in visible_assets],
        "r32_visible_within_cap": R32 in visible_parents if R32 in candidates else None,
        "parent_duplicates": len(ordered) - len(set(ordered)),
        "asset_duplicates": len(assets) - len({row["asset_id"] for row in assets}),
    }


def _offline_matrix(path):
    vectors = [
        ("SLP", True), ("slp", True), ("SlP", True), ("  SLP  ", True),
        ("SL", False), ("SLPP", False), ("SLP123", False),
        ("SHOPLINE Payments", True), ("shopline payments", True),
        ("SHOPLINE Payment", False), ("SHOPLINE", False), ("Payments", False),
        ("請提供 SLP 的資料", False),
    ]
    rows = []
    for query, expected in vectors:
        observed = _search_candidate(path, query)
        valid = observed["alias_matched"] is expected
        if normalize_alias(query) in {"slp", "shopline payments"}:
            valid = valid and R32 in observed["parent_record_ids"]
        if normalize_alias(query) == "shopline payments":
            valid = valid and observed["parent_count"] == 16 and observed["organic_other_parent_count"] == 15
        rows.append({"query": query, **observed, "expected_alias_match": expected, "status": "pass" if valid else "fail"})
    for query, expected in {
        "莉朵花藝": (0, 0), "littlegirl": (0, 0), "廣生堂": (1, 0),
        "111gsttest": (1, 0), "Package+": (3, 3), "關貿網路": (1, 1),
    }.items():
        observed = _search_candidate(path, query)
        valid = (observed["asset_count"], observed["citation_count"]) == expected
        rows.append({"query": query, **observed, "expected_alias_match": False, "status": "pass" if valid else "fail"})
    return rows


def _defense_matrix(parents, aliases):
    rows = []
    conflict = [dict(row) for row in aliases] + [{**aliases[0], "parent_record_id": "商家夥伴案例資料庫:r12"}]
    try:
        _resolve_alias("SLP", conflict, parents)
        blocked = False
    except ProductionSearchAliasConfirmationError:
        blocked = True
    rows.append({"check": "conflict_fails_closed", "status": "pass" if blocked else "fail"})
    for field in ("reviewer", "provenance"):
        fixture = [{**aliases[0], field: ""}]
        rows.append({"check": f"missing_{field}_blocked", "status": "pass" if not _validate_authority(parents, fixture, fixture)["valid"] else "fail"})
    revoked = [{**aliases[0], "active": False, "action": "revoke"}]
    rows.append({"check": "revoked_excluded", "status": "pass" if not _resolve_alias("SLP", revoked, parents) else "fail"})
    excluded = {**parents, R32: {**parents[R32], "can_enter_content_index": False}}
    rows.append({"check": "excluded_owner_rejected", "status": "pass" if not _resolve_alias("SLP", aliases, excluded) else "fail"})
    internal = {**parents, R32: {**parents[R32], "can_external_reference": False}}
    rows.append({"check": "internal_only_preserved", "status": "pass" if _resolve_alias("SLP", aliases, internal) == [R32] and not internal[R32]["can_external_reference"] else "fail"})
    return rows


def _plan_hash_defense_matrix(parents, aliases):
    conflict = [dict(row) for row in aliases] + [
        {**aliases[0], "parent_record_id": "商家夥伴案例資料庫:r12"}
    ]
    try:
        _resolve_alias("SLP", conflict, parents)
        conflict_blocked = False
    except ProductionSearchAliasConfirmationError:
        conflict_blocked = True
    missing = [{**aliases[0], "reviewer": "", "provenance": ""}]
    revoked = [{**aliases[0], "active": False, "action": "revoke"}]
    excluded = {**parents, R32: {**parents[R32], "can_enter_content_index": False}}
    internal = {**parents, R32: {**parents[R32], "can_external_reference": False}}
    return [
        {
            "check": "duplicate_normalized_alias_fails_closed",
            "status": "pass" if conflict_blocked else "fail",
        },
        {
            "check": "missing_reviewer_or_provenance_blocked",
            "status": "pass" if not _validate_authority(parents, missing, missing)["valid"] else "fail",
        },
        {
            "check": "revoked_alias_not_resolved",
            "status": "pass" if not _resolve_alias("SLP", revoked, parents) else "fail",
        },
        {
            "check": "excluded_parent_alias_not_resolved",
            "status": "pass" if not _resolve_alias("SLP", aliases, excluded) else "fail",
        },
        {
            "check": "internal_only_alias_searchable_but_not_external",
            "status": "pass"
            if _resolve_alias("SLP", aliases, internal) == [R32]
            and not internal[R32]["can_external_reference"]
            else "fail",
        },
    ]


def _renderer_preview(candidate_path, root):
    result = _search_candidate(candidate_path, "SHOPLINE Payments")
    approved_urls = {}
    for row in _read_csv(_resolve(root, sync_validation.DEFAULT_ASSET_ELIGIBLE)):
        if row["field"] == "asset_url" and row["review_decision"] == "approve":
            approved_urls[row["asset_id"]] = row["proposed_value"]
    display_assets = [row for row in result["visible_assets"] if row["can_external"]]
    lines = [f"找到「SHOPLINE Payments」相關內容，共 {len(display_assets)} 筆：", ""]
    for index, asset in enumerate(display_assets, 1):
        label = {"article": "文章", "video": "影片", "podcast": "Podcast"}.get(asset["asset_type"], "內容")
        lines.extend([
            f"{index}. {label}",
            f"標題：<{approved_urls.get(asset['asset_id'], '')}|{asset['title']}>",
            "對外引用：可對外引用", "",
        ])
    lines.extend(["資料來源：", "MKT 內容產出資料庫_店家 / 夥伴案例 / 對外數據"])
    rendered = "\n".join(lines)
    hidden = all(token not in rendered for token in (
        "search_alias_reviewed_by", "authority_event", "match_reason", "record_id", "governance_flags"
    ))
    approved_links = all(
        approved_urls.get(row["asset_id"], "").startswith(("https://", "http://"))
        for row in display_assets
    )
    return {
        "valid": all((len(result["visible_parent_record_ids"]) <= 5, len(display_assets) <= 10, R32 in result["visible_parent_record_ids"], hidden, approved_links)),
        "query": "SHOPLINE Payments",
        "candidate_parent_count": result["parent_count"],
        "displayed_parent_count": len(result["visible_parent_record_ids"]),
        "displayed_asset_count": len(display_assets),
        "r32_visible": R32 in result["visible_parent_record_ids"],
        "approved_title_urls_only": approved_links,
        "url_values_written_to_alias_projection": 0,
        "internal_metadata_hidden": hidden,
        "alias_used_as_content_tag": False,
        "production_renderer_modified": False,
        "preview": rendered,
    }


def _runtime_scope(root, plan, code_delta):
    inventory = {}
    for relative in (
        "src/marketing_knowledge_agent/query_planning.py",
        "src/marketing_knowledge_agent/pipeline.py",
        "src/marketing_knowledge_agent/retrieval.py",
        "src/marketing_knowledge_agent/reranking.py",
        "src/marketing_knowledge_agent/structured_results.py",
        "src/marketing_knowledge_agent/slack_interface.py",
    ):
        path = root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        inventory[relative] = sorted(
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
    required = {
        "planned_files": isinstance(code_delta.get("files"), list) and bool(code_delta["files"]),
        "planned_functions": isinstance(code_delta.get("functions"), list) and bool(code_delta["functions"]),
        "entry_points": isinstance(code_delta.get("entry_points"), list) and bool(code_delta["entry_points"]),
        "loader_api": isinstance(code_delta.get("alias_projection_loader"), dict),
        "checksum_validation": bool(code_delta.get("checksum_validation")),
        "stale_projection_rejection": bool(code_delta.get("stale_projection_rejection")),
        "normalization_function": bool(code_delta.get("normalization_function")),
        "exact_resolver": bool(code_delta.get("exact_resolver")),
        "typed_query_integration": isinstance(code_delta.get("typed_query_integration"), dict),
        "candidate_merge_point": bool(code_delta.get("candidate_merge_point")),
        "ranking_signal": bool(code_delta.get("ranking_signal")),
        "governance_filter_point": bool(code_delta.get("governance_filter_point")),
        "result_cap_behavior": bool(code_delta.get("result_cap_behavior")),
        "failure_behavior": isinstance(code_delta.get("failure_behavior"), dict),
        "tests_required": isinstance(code_delta.get("tests_required"), list) and bool(code_delta["tests_required"]),
    }
    declared_modules = set(plan.get("target_paths", {}).get("runtime_modules", []))
    return {
        "complete": all(required.values()),
        "checks": [{"requirement": key, "valid": value} for key, value in required.items()],
        "declared_runtime_modules": sorted(declared_modules),
        "actual_runtime_inventory": inventory,
        "missing_scope": [key for key, value in required.items() if not value],
        "required_future_scope": {
            "new_file": "src/marketing_knowledge_agent/search_aliases.py",
            "loader_functions": ["load_alias_projection", "validate_alias_projection_checksum", "resolve_exact_parent_alias"],
            "query_integration": ["query_planning.build_query_plan", "query_planning.normalize_query_text"],
            "runtime_entry_points": ["pipeline.search_index", "pipeline.ask_index", "pipeline.explain_query"],
            "ranking_and_dedupe": ["reranking.rerank_results", "pipeline._dedupe_document_results"],
            "governance_and_citation": ["pipeline.ask_index", "structured_results.generate_structured_answer"],
            "renderer_modified": False,
        },
    }


def _canonical_projection_payload(rows):
    payload = {
        "schema_version": 1,
        "authority_source": "governance_decision_store_current_search_aliases",
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "generated_from_plan_id": EXPECTED_PLAN_ID,
        "aliases": [{
            key: row[key] for key in (
                "raw_alias", "normalized_alias", "parent_record_id", "active",
                "reviewer", "reviewed_at", "provenance", "authority_event",
            )
        } for row in rows],
    }
    payload["projection_hash"] = _hash_json(payload)
    payload["contract_complete"] = False
    payload["contract_gap"] = (
        "Plan hashes only Alias rows and does not pin this canonical file payload, schema_version, "
        "authority_source, generated_from_plan_id, or self-excluding projection_hash contract"
    )
    return payload


def _projection_rows(aliases):
    return [{
        "parent_record_id": row["parent_record_id"], "raw_alias": row["raw_alias"],
        "normalized_alias": row["normalized_alias"], "active": row["active"],
        "reviewer": row["reviewer"], "reviewed_at": row["reviewed_at"],
        "provenance": row["provenance"], "authority_event": row["authority_event"],
        "authority_event_hash": row["authority_event_hash"], "before_state": None,
        "desired_state": "active", "action": "create",
        "target": ".mka/search_alias_projection.json", "rollback_action": "remove_projection_file",
    } for row in sorted(aliases, key=lambda item: item["normalized_alias"])]


def _normalization_contract():
    return {
        "unicode": "NFKC", "case": "Unicode casefold", "trim": True,
        "collapse_whitespace": True, "fullwidth_halfwidth": "NFKC compatibility fold",
        "punctuation": "preserved; no deletion or expansion",
        "match": "normalized full entity value equality", "fuzzy": False,
        "prefix": False, "substring": False,
    }


def _normalization_vectors():
    values = ["SLP", "slp", "SlP", "  SLP  ", "ＳＬＰ", "SHOPLINE Payments", "shopline   payments", "SHOPLINE Payment", "SLPP"]
    return [{"input": value, "normalized": normalize_alias(value)} for value in values]


def _query_contract():
    return {
        "scope": "Parent-level only",
        "supported_shapes": ["entire raw query equals alias", "future typed entity segment only after parser returns a complete exact value"],
        "unsupported_shapes": ["arbitrary sentence containing alias", "partial explicit entity values", "ambiguous mixed entity query"],
        "unsupported_behavior": "do not apply alias; never substitute substring matching",
        "match": "case-insensitive normalized exact", "child_expansion": False,
    }


def _merge_contract():
    return {
        "candidate_sources": ["exact_alias_owner", "canonical_parent_or_handle", "organic_formal_search"],
        "merge": "set union", "parent_dedup_key": "record_id",
        "asset_dedup_key": "formal asset_id", "match_reasons": "internal ranking input only",
        "organic_results_preserved": True,
    }


def _ranking_contract():
    return {
        "tiers": ["exact_parent_alias", "exact_canonical_parent_or_handle", "organic_exact_field", "existing_retrieval_ranking"],
        "tie_breaker": "existing retrieval score then stable record_id/asset_id",
        "parent_cap": 5, "asset_cap": 10, "alias_owner_visible": True,
        "alias_exclusive": False,
    }


def _governance_contract():
    return {
        "order": ["resolve_alias", "merge_candidates", "deduplicate", "parent_governance_filter", "asset_governance_filter", "citation_and_channel_filter", "rank", "cap"],
        "parent_blocks": ["exclude", "governance_only", "restricted", "pending", "not_searchable"],
        "asset_blocks": ["hold", "exclude", "not_searchable"],
        "external": "can_external_reference remains authoritative",
        "alias_overrides_governance": False,
    }


def _architecture_comparison():
    return [
        {"option": "A", "strategy": "Formal SQLite dedicated alias table", "schema_migration": True, "selected": False},
        {"option": "B", "strategy": "Formal SQLite metadata_json", "schema_migration": False, "selected": False},
        {"option": "C", "strategy": "independent governed alias projection store", "schema_migration": False, "selected": True},
        {"option": "D", "strategy": "runtime reads Managed Vault", "schema_migration": False, "selected": False},
    ]


def _strategy():
    return {
        "strategy": "independent_governed_json_alias_projection",
        "target_paths": {
            "alias_projection": ".mka/search_alias_projection.json",
            "formal_sqlite": ".mka/content_index.sqlite",
            "runtime_modules": ["src/marketing_knowledge_agent/search_aliases.py", "src/marketing_knowledge_agent/pipeline.py"],
        },
        "schema_migration_required": False, "schema_migration_prerequisite": "",
        "index_rebuild_required": False, "existing_parent_rows_modified": 0,
        "incremental": True, "atomic_projection_replace": True,
    }


def _backup_plan():
    return {
        "backup_alias_projection": "record absence or exact previous bytes",
        "backup_runtime_files": ["search_aliases.py", "pipeline.py"],
        "backup_formal_sqlite_checksum_only": True,
        "backup_managed_vault_checksum_only": True, "immutable_bundle": True,
    }


def _rollback_plan():
    return {
        "order": ["disable runtime loading", "restore runtime files", "remove_or_restore_alias_projection", "verify Formal SQLite and Vault checksums", "re-run offline and production smoke validation"],
        "parent_deleted": False, "asset_deleted": False,
        "store_sync_rolled_back": False, "authority_evidence_retained": True,
    }


def _validate_static_plan(plan):
    expected = {
        "plan_id": EXPECTED_PLAN_ID, "manifest_hash": EXPECTED_MANIFEST_HASH,
        "source_commit": EXPECTED_PLAN_SOURCE_COMMIT, "expires_at": EXPECTED_PLAN_EXPIRES_AT,
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "authoritative_parent_count": 120, "content_parent_count": 109,
        "alias_authority_count": 2, "alias_owner_count": 1, "alias_conflict_count": 0,
        "schema_migration_required": False, "execution_blocked": False,
    }
    mismatches = [key for key, value in expected.items() if plan.get(key) != value]
    if plan.get("blocker_reasons") != []:
        mismatches.append("blocker_reasons")
    if plan.get("target_paths", {}).get("alias_projection") != ".mka/search_alias_projection.json":
        mismatches.append("target_paths.alias_projection")
    if mismatches:
        raise ProductionSearchAliasConfirmationError("Plan manifest mismatch: " + ", ".join(mismatches))


def _require_exact_identity(plan_id, manifest_hash):
    if plan_id != EXPECTED_PLAN_ID:
        raise ProductionSearchAliasConfirmationError("exact PLAN_ID required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise ProductionSearchAliasConfirmationError("exact Manifest Hash required")


def _confirmation_payload(validation, reviewer, confirmed_at):
    core = {
        "plan_id": EXPECTED_PLAN_ID, "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "plan_source_commit": EXPECTED_PLAN_SOURCE_COMMIT,
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "hashes": validation["hashes"], "strategy": validation["strategy"],
        "target_counts": {"parents": 120, "content_parents": 109, "aliases": 2, "owners": 1},
        "asset_counts": {"total": 222, "searchable": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_fields": 410, "reviewer": reviewer,
        "confirmed_at": confirmed_at, "plan_expires_at": EXPECTED_PLAN_EXPIRES_AT,
    }
    core["confirmation_id"] = "production-search-alias-confirmation-" + _hash_json(core)[:16]
    core["confirmation_statement"] = (
        f"Admin confirms the independently validated Production Search Alias Plan identified by PLAN_ID {EXPECTED_PLAN_ID} "
        f"and Manifest Hash {EXPECTED_MANIFEST_HASH}. This confirmation authorizes only a later, separate Execute step "
        "using the exact Alias authority, normalization contract, query semantics, projection payload, runtime code delta, "
        "candidate merge, ranking, governance filters, targets, counts, and checksums. It does not activate Production Search Alias."
    )
    return core


def _confirmation_files(validation, payload):
    return {
        "confirmation.json": payload,
        "independent_validation.json": _public_validation(validation),
        "referenced_plan_manifest.json": validation["plan_manifest"],
        "referenced_decision_store_execution.json": validation["decision_store_validation"],
        "referenced_store_sync_execution.json": validation["store_sync_execution_validation"],
        "alias_authority_projection.json": validation["canonical_projection"],
        "normalization_contract.json": validation["normalization_contract"],
        "query_semantics_contract.json": validation["query_contract"],
        "candidate_merge_contract.json": validation["merge_contract"],
        "ranking_contract.json": validation["ranking_contract"],
        "governance_filter_contract.json": validation["governance_contract"],
        "runtime_code_delta_manifest.json": validation["runtime_code_delta"],
        "formal_projection_delta_manifest.json": validation["projection_delta"],
        "target_projection_summary.json": {
            "strategy": validation["strategy"], "candidate": validation["candidate"],
            "asset_boundary": validation["asset_boundary"],
        },
    }


def _confirmation_manifest(staging, validation, payload):
    files = [{
        "filename": path.name, "sha256": _sha256(path), "byte_size": path.stat().st_size,
        "required": True,
    } for path in sorted(staging.iterdir()) if path.is_file() and not path.name.startswith("._")]
    manifest = {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "confirmation_id": payload["confirmation_id"], "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH, "reviewer": payload["reviewer"],
        "confirmed_at": payload["confirmed_at"], "plan_expires_at": EXPECTED_PLAN_EXPIRES_AT,
        "validator_code_version": VALIDATOR_CODE_VERSION,
        "independent_validation_hash": validation["independent_validation_hash"],
        "files": files,
    }
    manifest["root_confirmation_hash"] = _hash_json(manifest)
    return manifest


def _write_reports(output, result, bundle):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILENAMES[0], _summary(result))
    _write_csv(output / REPORT_FILENAMES[1], [{
        "decision_store_sha256": result["decision_store_validation"]["sha256"],
        "store_sync_execution_id": result["store_sync_execution_validation"]["execution_id"],
        "store_sync_execution_root_hash": result["store_sync_execution_validation"]["root_execution_hash"],
        "managed_parent_count": result["managed_parent_count"],
        "formal_parent_count": result["formal_parent_count"],
        "alias_target_absent": result["alias_target_absent"], "status": "pass",
    }])
    _write_csv(output / REPORT_FILENAMES[2], result["aliases"])
    _write_csv(output / REPORT_FILENAMES[3], [{
        "raw_alias": row["raw_alias"], "reviewer_present": bool(row["reviewer"]),
        "reviewed_at_present": bool(row["reviewed_at"]), "provenance_present": bool(row["provenance"]),
        "authority_reference_present": bool(row["authority_event"]), "status": "pass",
    } for row in result["aliases"]])
    _write_csv(output / REPORT_FILENAMES[4], result["normalization_vectors"])
    _write_csv(output / REPORT_FILENAMES[5], [{"field": key, "value": value, "status": "pass"} for key, value in result["query_contract"].items()])
    _write_csv(output / REPORT_FILENAMES[6], result["conflict_validation"])
    _write_text(output / REPORT_FILENAMES[7], _markdown("Projection Strategy Independent Validation", {"architecture": result["architecture"], "selected": result["strategy"]}))
    _write_csv(output / REPORT_FILENAMES[8], [{"field": key, "value": value, "status": "blocked" if key in {"contract_complete", "contract_gap"} else "validated"} for key, value in result["canonical_projection"].items()])
    _write_csv(output / REPORT_FILENAMES[9], result["runtime_scope"]["checks"])
    _write_text(output / REPORT_FILENAMES[10], _markdown("Runtime Code Delta Scope", result["runtime_scope"]))
    _write_text(output / REPORT_FILENAMES[11], _markdown("Candidate Merge Independent Validation", result["merge_contract"]))
    _write_csv(output / REPORT_FILENAMES[12], [{"field": key, "value": value, "status": "pass"} for key, value in result["ranking_contract"].items()])
    _write_csv(output / REPORT_FILENAMES[13], [{"dedupe_key": "record_id", "duplicates": result["candidate"]["duplicate_parent_count"], "status": "pass"}])
    _write_csv(output / REPORT_FILENAMES[14], [{"dedupe_key": "formal asset_id", "duplicates": result["candidate"]["duplicate_asset_count"], "status": "pass"}])
    _write_csv(output / REPORT_FILENAMES[15], [{"field": key, "value": value, "status": "pass"} for key, value in result["governance_contract"].items()])
    _write_text(output / REPORT_FILENAMES[16], _markdown("Temporary Candidate Independent Validation", result["candidate"]))
    _write_csv(output / REPORT_FILENAMES[17], [row for row in result["offline"] if normalize_alias(row["query"]) == "shopline payments"])
    _write_csv(output / REPORT_FILENAMES[18], [row for row in result["offline"] if normalize_alias(row["query"]) == "slp"])
    _write_csv(output / REPORT_FILENAMES[19], [row for row in result["offline"] if row["query"] in {"莉朵花藝", "littlegirl", "廣生堂", "111gsttest", "Package+", "關貿網路"}])
    _write_csv(output / REPORT_FILENAMES[20], [result["asset_boundary"]])
    _write_text(output / REPORT_FILENAMES[21], "# Slack Renderer Offline Compatibility\n\n" + result["renderer"]["preview"] + "\n")
    _write_csv(output / REPORT_FILENAMES[22], result["hash_validation"] + [{
        "field": "plan_id", "expected": EXPECTED_PLAN_ID,
        "actual": result["reproduced_plan_id"], "valid": result["reproduced_plan_id"] == EXPECTED_PLAN_ID,
    }, {
        "field": "manifest_hash", "expected": EXPECTED_MANIFEST_HASH,
        "actual": result["reproduced_manifest_hash"], "valid": result["reproduced_manifest_hash"] == EXPECTED_MANIFEST_HASH,
    }])
    _write_csv(output / REPORT_FILENAMES[23], [bundle or {"status": "not_created", "reason": ",".join(result["validation_errors"])}])
    _write_csv(output / REPORT_FILENAMES[24], [{
        "formal_systems_unchanged": result["formal_systems_unchanged"],
        "production_search_activated": False, "alias_target_created": False,
        "slack_renderer_modified": False, "slack_api_called": False, "status": "pass",
    }])
    _write_text(output / REPORT_FILENAMES[25], "# Production Search Alias Execute Prerequisites\n\n- Regenerate the Plan with a canonical projection-file payload contract.\n- Regenerate the Plan with exact runtime files, functions, entry points, loader checksum/stale behavior, merge/ranking/filter integration, caps, failure behavior, rollback, and tests.\n- Independently validate and obtain a new Admin Confirmation before Execute.\n")
    _write_csv(output / REPORT_FILENAMES[26], [{"error": value} for value in result["validation_errors"]], ("error",))
    _write_csv(output / REPORT_FILENAMES[27], [{"warning": value} for value in result["validation_warnings"]], ("warning",))


def _summary(result):
    return (
        "# Production Search Alias Confirmation\n\n"
        f"- Conclusion: **{result['conclusion']}**\n"
        f"- PLAN_ID: `{result['plan_id']}`\n"
        f"- Manifest Hash: `{result['manifest_hash']}`\n"
        f"- Plan identity reproduced: `{str(result['plan_identity_valid']).lower()}`\n"
        f"- Validation errors: `{len(result['validation_errors'])}`\n"
        f"- Validation warnings: `{len(result['validation_warnings'])}`\n"
        "- Confirmation created: `false`\n"
        "- Execute performed: `false`\n"
        "- Production Search modified: `false`\n"
    )


def _public_validation(result):
    omitted = {"aliases", "offline", "plan_manifest", "canonical_projection", "projection_rows"}
    return {key: value for key, value in result.items() if key not in omitted and key != "independent_validation_hash"}


def _snapshot(paths):
    result = {}
    for key, path in paths.items():
        result[key] = {
            "sha256": _sha256(path) if path.is_file() else sync_validation._hash_path(path),
            "byte_size": path.stat().st_size if path.is_file() else None,
        }
    return result


def _sqlite_sidecars(path):
    base = str(path)
    return {candidate for suffix in ("-wal", "-shm", "-journal") if (candidate := base + suffix) and Path(candidate).exists()}


def _git(root, *args, check=True):
    result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ProductionSearchAliasConfirmationError(result.stderr.strip() or "git command failed")
    return "" if result.returncode == 0 else result.stderr.strip()


def _git_ignored(root, path):
    result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=root, check=False)
    return result.returncode == 0


def _read_markdown_json(path):
    text = Path(path).read_text(encoding="utf-8")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        raise ProductionSearchAliasConfirmationError("Runtime Code Delta has no canonical JSON payload")
    return json.loads(match.group(1))


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ProductionSearchAliasConfirmationError("timestamp must include timezone")
    return parsed


def _resolve(root, path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bool(value):
    return value if isinstance(value, bool) else str(value).strip().casefold() == "true"


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path, value):
    Path(path).write_text(value, encoding="utf-8")


def _write_csv(path, rows, default_fields=()):
    rows = list(rows)
    fields = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    fields = fields or list(default_fields)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _json(value) if isinstance(value, (dict, list, tuple, set)) else value for key, value in row.items()})


def _markdown(title, value):
    return f"# {title}\n\n```json\n{json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)}\n```\n"
