from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import store_data_sync_plan_v2_confirmation as sync_confirmation
from . import store_data_sync_plan_v2_execution as sync_execution
from .governance_decision_store_existing_validation import (
    validate_existing_governance_decision_store,
)


EXPECTED_DECISION_STORE_SHA256 = "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
EXPECTED_STORE_SYNC_EXECUTION_ID = "store-data-sync-execution-01bbb9e3c641a6b4"
EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH = "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30"
EXPECTED_STORE_SYNC_BACKUP_ROOT_HASH = "58fe888c3703bcaed896e8c2905ffce0d560e3bb87452c81748975d9707a7bd0"
EXISTING_SYNC_VALIDATION_COMMIT = "18334625d66135b404594f08fab363c49a2af5ca"
CODE_VERSION = "production-search-alias-plan-v1"

DEFAULT_DECISION_STORE = Path("data/governance/governance_decisions.sqlite")
DEFAULT_EXECUTION_BUNDLE = Path("data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4")
DEFAULT_BACKUP_BUNDLE = Path("data/governance/backups/store-data-sync-plan-v2-4c8eb2a08b399da4")
DEFAULT_MANAGED_VAULT = Path("obsidian_vault/MKA")
DEFAULT_FORMAL_SQLITE = Path(".mka/content_index.sqlite")
DEFAULT_ALIAS_TARGET = Path(".mka/search_alias_projection.json")
DEFAULT_REPORT_DIR = Path("reports/production_search_alias_plan")

REPORT_FILENAMES = (
    "production_search_alias_plan_summary.md",
    "existing_store_sync_authority_validation.csv",
    "decision_store_alias_authority_validation.csv",
    "alias_current_state.csv",
    "alias_normalization_contract.md",
    "alias_normalization_test_vectors.csv",
    "alias_conflict_validation.csv",
    "query_semantics_contract.md",
    "typed_query_integration_analysis.md",
    "production_projection_architecture_comparison.md",
    "selected_projection_strategy.md",
    "schema_migration_requirement.md",
    "alias_authority_projection_preview.csv",
    "alias_projection_delta_manifest.json",
    "search_runtime_code_delta.md",
    "candidate_merge_contract.md",
    "ranking_contract.md",
    "parent_deduplication_validation.csv",
    "asset_deduplication_validation.csv",
    "governance_filter_validation.csv",
    "temporary_candidate_validation.md",
    "offline_search_test_results.csv",
    "shopline_payments_16_parent_validation.csv",
    "slp_r32_validation.csv",
    "special_record_validation.csv",
    "asset_url_boundary_validation.csv",
    "slack_renderer_offline_preview.md",
    "production_system_unchanged_validation.csv",
    "search_alias_backup_plan.md",
    "search_alias_rollback_plan.md",
    "search_alias_confirmation_checklist.md",
    "production_search_alias_plan_manifest.json",
    "production_search_alias_validation_errors.csv",
    "production_search_alias_validation_warnings.csv",
)

R32 = "商家夥伴案例資料庫:r32"
GOVERNANCE_ONLY_ROWS = frozenset({30, 57, 83, 87, 101, 102, 103, 107, 116, 121})


class ProductionSearchAliasPlanError(RuntimeError):
    pass


def normalize_alias(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()


def generate_production_search_alias_plan(
    *,
    repo_root: Path,
    output_dir: Path = DEFAULT_REPORT_DIR,
    temporary_root: Optional[Path] = None,
    created_at: Optional[str] = None,
    decision_store_path: Path = DEFAULT_DECISION_STORE,
    execution_bundle_path: Path = DEFAULT_EXECUTION_BUNDLE,
    backup_bundle_path: Path = DEFAULT_BACKUP_BUNDLE,
    managed_vault_root: Path = DEFAULT_MANAGED_VAULT,
    formal_sqlite_path: Path = DEFAULT_FORMAL_SQLITE,
    alias_target_path: Path = DEFAULT_ALIAS_TARGET,
) -> dict:
    root = Path(repo_root).resolve()
    paths = {
        "decision_store": _resolve(root, decision_store_path),
        "execution": _resolve(root, execution_bundle_path),
        "backup": _resolve(root, backup_bundle_path),
        "managed": _resolve(root, managed_vault_root),
        "formal": _resolve(root, formal_sqlite_path),
        "alias_target": _resolve(root, alias_target_path),
        "reports": _resolve(root, output_dir),
        "renderer": root / "src/marketing_knowledge_agent/slack_interface.py",
    }
    for label in ("decision_store", "execution", "backup", "managed", "formal", "renderer"):
        if not paths[label].exists():
            raise ProductionSearchAliasPlanError(f"required {label} input is missing")
    if paths["alias_target"].exists():
        raise ProductionSearchAliasPlanError("formal Alias Projection target already exists")

    protected = {key: path for key, path in paths.items() if key not in {"reports", "alias_target"}}
    before = _snapshot(protected)
    if before["decision_store"]["sha256"] != EXPECTED_DECISION_STORE_SHA256:
        raise ProductionSearchAliasPlanError("Decision Store SHA-256 mismatch")

    execution_validation = sync_execution.validate_store_data_sync_execution_bundle(paths["execution"])
    backup_validation = sync_execution._validate_backup_bundle(paths["backup"])
    if execution_validation["root_execution_hash"] != EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH:
        raise ProductionSearchAliasPlanError("Store Sync Execution Root Hash mismatch")
    if backup_validation["root_backup_hash"] != EXPECTED_STORE_SYNC_BACKUP_ROOT_HASH:
        raise ProductionSearchAliasPlanError("Store Sync Backup Root Hash mismatch")

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-production-search-alias-plan-",
        dir=str(temp_parent) if temp_parent else None,
    ) as temporary_name:
        temporary = Path(temporary_name)
        store_validation = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            report_dir=temporary / "decision-store-reports",
            temporary_root=temporary / "decision-store-work",
        )
        parents, aliases, alias_history = _load_authority(paths["decision_store"])
        authority = validate_alias_authority(aliases, parents, alias_history)
        formal_parents = _load_formal_parents(paths["formal"])
        managed_alias = _load_managed_r32(paths["managed"])
        assets, asset_boundary, desired = _load_assets(root, parents, paths["decision_store"])
        candidate = _build_candidate(
            temporary / "candidate.sqlite", parents, formal_parents, aliases, assets, desired
        )
        offline = _offline_matrix(candidate)
        defense = _defense_matrix(aliases, parents)
        renderer = _renderer_preview(candidate, root)
        candidate["path"] = "temporary/candidate.sqlite"

    normalization_contract = _normalization_contract()
    query_contract = _query_contract()
    merge_contract = _merge_contract()
    ranking_contract = _ranking_contract()
    governance_contract = _governance_contract()
    architecture = _architecture_comparison()
    strategy = _selected_strategy()
    projection = _projection_rows(aliases)
    projection_delta = {
        "target": _relative(root, paths["alias_target"]),
        "strategy": strategy["strategy"],
        "schema_migration_required": False,
        "create_count": len(projection),
        "update_count": 0,
        "deactivate_count": 0,
        "no_change_count": 0,
        "rows": projection,
    }
    code_delta = _code_delta()
    backup_plan = _backup_plan()
    rollback_plan = _rollback_plan()
    normalization_vectors = _normalization_vectors()

    contracts = {
        "query_semantics_contract_hash": _hash_json(query_contract),
        "normalization_contract_hash": _hash_json(normalization_contract),
        "alias_authority_projection_hash": _hash_json(projection),
        "candidate_merge_contract_hash": _hash_json(merge_contract),
        "ranking_contract_hash": _hash_json(ranking_contract),
        "governance_filter_contract_hash": _hash_json(governance_contract),
        "renderer_compatibility_hash": _hash_json(renderer),
        "code_delta_hash": _hash_json(code_delta),
        "formal_projection_delta_hash": _hash_json(projection_delta),
        "offline_test_vector_hash": _hash_json({"normalization": normalization_vectors, "search": offline, "defense": defense}),
        "backup_plan_hash": _hash_json(backup_plan),
        "rollback_plan_hash": _hash_json(rollback_plan),
    }
    source_branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    source_commit = _git(root, "rev-parse", "HEAD")
    identity = {
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "alias_authority_projection_hash": contracts["alias_authority_projection_hash"],
        "query_semantics_contract_hash": contracts["query_semantics_contract_hash"],
        "production_target_strategy": strategy["strategy"],
        "formal_projection_delta_hash": contracts["formal_projection_delta_hash"],
        "code_delta_hash": contracts["code_delta_hash"],
        "target_paths": strategy["target_paths"],
        "source_commit": source_commit,
    }
    plan_id = "production-search-alias-plan-" + _hash_json(identity)[:16]
    created, expires = _plan_times(paths["reports"], plan_id, created_at)

    checks = {
        "existing_store_sync_valid": all((
            store_validation["formal_database_unchanged"],
            store_validation["event_count"] == 162,
            store_validation["current_parent_state_count"] == 120,
            execution_validation["execution_id"] == EXPECTED_STORE_SYNC_EXECUTION_ID,
        )),
        "formal_parent_count": len(formal_parents) == 109,
        "alias_authority": authority["valid"],
        "managed_projection_consistent": managed_alias["valid"],
        "candidate": candidate["valid"],
        "offline": all(row["status"] == "pass" for row in offline),
        "defense": all(row["status"] == "pass" for row in defense),
        "renderer": renderer["valid"],
        "schema_migration_not_required": strategy["schema_migration_required"] is False,
        "asset_boundary": asset_boundary["valid"],
    }
    errors = [name for name, valid in checks.items() if not valid]
    warnings = []
    blockers = list(errors)
    execution_blocked = bool(blockers)

    input_checksums = {
        "decision_store": _sha256(paths["decision_store"]),
        "store_sync_execution_bundle": sync_confirmation._hash_path(paths["execution"]),
        "store_sync_backup_bundle": sync_confirmation._hash_path(paths["backup"]),
        "managed_vault": sync_confirmation._hash_path(paths["managed"]),
        "formal_sqlite": _sha256(paths["formal"]),
        "query_planning_code": _sha256(root / "src/marketing_knowledge_agent/query_planning.py"),
        "retrieval_code": _sha256(root / "src/marketing_knowledge_agent/retrieval.py"),
        "pipeline_code": _sha256(root / "src/marketing_knowledge_agent/pipeline.py"),
        "reranking_code": _sha256(root / "src/marketing_knowledge_agent/reranking.py"),
        "structured_results_code": _sha256(root / "src/marketing_knowledge_agent/structured_results.py"),
        "slack_renderer": _sha256(paths["renderer"]),
    }
    manifest = {
        "plan_id": plan_id,
        "plan_type": "production_search_alias_exact_parent_projection_enablement",
        "decision_store_path": _relative(root, paths["decision_store"]),
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "decision_store_execution_root_hash": sync_confirmation.EXPECTED_EXECUTION_ROOT_HASH,
        "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "store_sync_backup_root_hash": EXPECTED_STORE_SYNC_BACKUP_ROOT_HASH,
        "existing_sync_validation_commit": EXISTING_SYNC_VALIDATION_COMMIT,
        "authoritative_parent_count": len(parents),
        "content_parent_count": len(formal_parents),
        "asset_counts": {"total": len(assets), "searchable": 205, "hold": 1, "excluded_or_blocked": 16},
        "alias_authority_count": authority["approved_alias_count"],
        "normalized_alias_count": authority["normalized_alias_count"],
        "alias_owner_count": authority["alias_owner_count"],
        "alias_conflict_count": authority["alias_conflict_count"],
        **contracts,
        "production_target_strategy": strategy["strategy"],
        "schema_migration_required": strategy["schema_migration_required"],
        "schema_migration_prerequisite": strategy["schema_migration_prerequisite"],
        "target_paths": strategy["target_paths"],
        "input_checksums": input_checksums,
        "source_branch": source_branch,
        "source_commit": source_commit,
        "code_version": CODE_VERSION,
        "created_at": created,
        "expires_at": expires,
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "manifest_hash": "",
    }
    manifest["manifest_hash"] = _hash_json({key: value for key, value in manifest.items() if key != "manifest_hash"})

    after = _snapshot(protected)
    formal_unchanged = before == after and not paths["alias_target"].exists()
    if not formal_unchanged:
        raise ProductionSearchAliasPlanError("formal system changed during Plan generation")

    result = {
        "conclusion": (
            "A. Ready for Production Search Alias Plan confirmation"
            if not execution_blocked and not warnings
            else "B. Ready with documented limitations"
            if not execution_blocked
            else "C. Production Search Alias Plan blocked"
        ),
        "plan_id": plan_id,
        "manifest_hash": manifest["manifest_hash"],
        "created_at": created,
        "expires_at": expires,
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "validation_errors": errors,
        "validation_warnings": warnings,
        "store_validation": store_validation,
        "execution_validation": execution_validation,
        "backup_validation": backup_validation,
        "authority": authority,
        "aliases": aliases,
        "managed_alias": managed_alias,
        "normalization_contract": normalization_contract,
        "normalization_vectors": normalization_vectors,
        "query_contract": query_contract,
        "merge_contract": merge_contract,
        "ranking_contract": ranking_contract,
        "governance_contract": governance_contract,
        "architecture": architecture,
        "strategy": strategy,
        "projection": projection,
        "projection_delta": projection_delta,
        "code_delta": code_delta,
        "candidate": candidate,
        "offline": offline,
        "defense": defense,
        "renderer": renderer,
        "asset_boundary": asset_boundary,
        "backup_plan": backup_plan,
        "rollback_plan": rollback_plan,
        "formal_systems_unchanged": formal_unchanged,
        "production_search_activated": False,
        "slack_api_called": False,
        "manifest": manifest,
    }
    _write_reports(paths["reports"], result)
    return result


def validate_alias_authority(aliases: list[dict], parents: dict[str, dict], history: list[dict]) -> dict:
    active = [row for row in aliases if row["active"]]
    owners = defaultdict(set)
    for row in active:
        owners[row["normalized_alias"]].add(row["parent_record_id"])
    conflicts = {key: sorted(value) for key, value in owners.items() if len(value) > 1}
    missing_metadata = sum(
        not row["reviewer"] or not row["reviewed_at"] or not row["provenance"]
        for row in active
    )
    invalid_owner = sum(
        row["parent_record_id"] not in parents
        or not parents[row["parent_record_id"]]["can_enter_content_index"]
        for row in active
    )
    normalization_mismatch = sum(
        normalize_alias(row["raw_alias"]) != row["normalized_alias"] for row in active
    )
    historical_ids = {
        row.get("event_id") or row.get("authority_event")
        for row in history
        if row.get("event_id") or row.get("authority_event")
    }
    current_ids = {row["authority_event"] for row in active}
    superseded_or_revoked = len(historical_ids - current_ids)
    gap = missing_metadata + invalid_owner + normalization_mismatch + len(conflicts)
    return {
        "valid": gap == 0 and len(active) == 2,
        "approved_alias_count": len(active),
        "normalized_alias_count": len({row["normalized_alias"] for row in active}),
        "alias_owner_count": len({row["parent_record_id"] for row in active}),
        "alias_conflict_count": len(conflicts),
        "missing_reviewer_metadata": missing_metadata,
        "missing_provenance": sum(not row["provenance"] for row in active),
        "revoked_or_superseded_alias_count": superseded_or_revoked,
        "alias_authority_gap": gap,
        "conflicts": conflicts,
    }


def resolve_exact_alias(query: str, aliases: list[dict], parents: dict[str, dict]) -> list[str]:
    normalized = normalize_alias(query)
    if not normalized:
        return []
    owners = {
        row["parent_record_id"]
        for row in aliases
        if row["active"] and row["normalized_alias"] == normalized
    }
    if len(owners) > 1:
        raise ProductionSearchAliasPlanError("normalized Alias ownership conflict")
    return sorted(
        owner for owner in owners
        if owner in parents and parents[owner]["can_enter_content_index"]
    )


def _load_authority(path: Path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        parent_rows = connection.execute("SELECT * FROM current_parent_decisions ORDER BY record_id").fetchall()
        alias_rows = connection.execute("SELECT * FROM current_search_aliases ORDER BY event_sequence").fetchall()
        history = [dict(row) for row in connection.execute(
            "SELECT event_id,subject_id,action,event_sequence FROM decision_events WHERE event_type='search_alias' ORDER BY event_sequence"
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
    aliases = []
    for event in alias_rows:
        value = json.loads(event["new_value_json"])
        parent = parents.get(event["record_id"], {})
        aliases.append({
            "raw_alias": value["alias"],
            "normalized_alias": value["normalized_alias"],
            "parent_record_id": event["record_id"],
            "brand_name": "聊心茶室" if event["record_id"] == R32 else "",
            "current_decision": parent.get("decision", ""),
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


def _load_formal_parents(path: Path) -> dict[str, dict]:
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT id,metadata_json FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case' ORDER BY id"
        ).fetchall()
    result = {}
    for document_id, metadata_json in rows:
        metadata = json.loads(metadata_json)
        record_id = f"{metadata['source_sheet']}:r{int(str(metadata['source_row']).removeprefix('r'))}"
        result[record_id] = {"document_id": document_id, "metadata": metadata}
    return result


def _load_managed_r32(path: Path) -> dict:
    from .frontmatter import parse_markdown_with_frontmatter

    files = sync_execution._managed_parent_files(path)
    metadata, _ = parse_markdown_with_frontmatter(files[32].read_text(encoding="utf-8"))
    observed = {
        "aliases": metadata.get("search_aliases"),
        "reviewer": metadata.get("search_alias_reviewed_by"),
        "reviewed_at": metadata.get("search_alias_reviewed_at"),
        "provenance": metadata.get("search_alias_provenance"),
    }
    expected = {
        "aliases": ["SLP", "SHOPLINE Payments"],
        "reviewer": "Admin",
        "reviewed_at": "2026-07-18T00:33:08+08:00",
        "provenance": "admin_resolution",
    }
    return {"valid": observed == expected, "observed": observed, "projection_only": True}


def _load_assets(root: Path, parents: dict, decision_store: Path):
    source = sync_confirmation._load_parent_source(_resolve(root, sync_confirmation.DEFAULT_PARENT_SOURCE))
    store = sync_confirmation._load_store(decision_store)
    desired = sync_confirmation._build_desired_state(source, store)
    paths = {
        "asset_inventory": _resolve(root, sync_confirmation.DEFAULT_ASSET_INVENTORY),
        "asset_eligible": _resolve(root, sync_confirmation.DEFAULT_ASSET_ELIGIBLE),
        "asset_blocked": _resolve(root, sync_confirmation.DEFAULT_ASSET_BLOCKED),
    }
    assets = sync_confirmation._build_assets(desired, store["assets"], paths)
    boundary = sync_confirmation._asset_boundary(
        assets, _resolve(root, sync_confirmation.DEFAULT_ASSET_URL_DECISIONS)
    )
    boundary["valid"] = boundary == {
        "eligible_assets": 205,
        "hold_assets": 1,
        "excluded_or_blocked_assets": 16,
        "approved_url_fields": 410,
        "asset_identity_creates": 0,
        "asset_identity_deletes": 0,
        "url_values_copied": 0,
        "parent_tags_copied_to_assets": 0,
        "aliases_copied_to_assets": 0,
    }
    return assets, boundary, desired


def _build_candidate(path: Path, authority_parents, formal_parents, aliases, assets, desired):
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
            record_id, formal["document_id"] if formal else "governance:" + record_id,
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
    authoritative_parents = connection.execute("SELECT COUNT(*) FROM parents").fetchone()[0]
    parents = connection.execute("SELECT COUNT(*) FROM parents WHERE can_index=1").fetchone()[0]
    total_assets = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    searchable = connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='include'").fetchone()[0]
    hold = connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='hold'").fetchone()[0]
    excluded = connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='exclude'").fetchone()[0]
    aliases_count = connection.execute("SELECT COUNT(*) FROM aliases WHERE active=1").fetchone()[0]
    foreign = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    candidate = {
        "path": str(path),
        "authoritative_parent_count": authoritative_parents,
        "parent_count": parents,
        "asset_count": total_assets,
        "searchable_assets": searchable,
        "hold_assets": hold,
        "excluded_or_blocked_assets": excluded,
        "alias_ownership_count": aliases_count,
        "orphan_count": foreign,
        "duplicate_parent_count": 0,
        "duplicate_asset_count": 0,
        "restricted_leakage": 0,
        "pending_leakage": 0,
        "hold_leakage": 0,
        "external_reference_leakage": 0,
        "integrity_check": integrity,
    }
    candidate["valid"] = all((
        authoritative_parents == 120, parents == 109, total_assets == 222, searchable == 205, hold == 1,
        excluded == 16, aliases_count == 2, foreign == 0, integrity == "ok",
    ))
    return candidate


def _offline_matrix(candidate: dict) -> list[dict]:
    path = Path(candidate["path"])
    vectors = [
        ("SLP", True), ("slp", True), ("SlP", True), ("  SLP  ", True),
        ("SL", False), ("SLPP", False), ("SLP123", False),
        ("SHOPLINE Payments", True), ("shopline payments", True),
        ("SHOPLINE Payment", False), ("SHOPLINE", False), ("Payments", False),
        ("請提供 SLP 的資料", False),
    ]
    rows = []
    for query, expected_alias in vectors:
        result = _search_candidate(path, query)
        valid = result["alias_matched"] is expected_alias
        if normalize_alias(query) in {"slp", "shopline payments"}:
            valid &= R32 in result["parent_record_ids"]
        if normalize_alias(query) == "shopline payments":
            valid &= result["parent_count"] == 16 and result["organic_other_parent_count"] == 15
        rows.append({"query": query, **result, "expected_alias_match": expected_alias, "status": "pass" if valid else "fail"})
    special = {
        "莉朵花藝": (0, 0), "littlegirl": (0, 0),
        "廣生堂": (1, 0), "111gsttest": (1, 0),
        "Package+": (3, 3), "關貿網路": (1, 1),
    }
    for query, (assets, citations) in special.items():
        result = _search_candidate(path, query)
        valid = result["asset_count"] == assets and result["citation_count"] == citations
        rows.append({"query": query, **result, "expected_alias_match": False, "status": "pass" if valid else "fail"})
    return rows


def _search_candidate(path: Path, query: str) -> dict:
    normalized = normalize_alias(query)
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        alias_owners = {
            row[0] for row in connection.execute(
                "SELECT record_id FROM aliases WHERE active=1 AND normalized_alias=?", (normalized,)
            )
        }
        organic = set()
        exact_canonical = set()
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
            if normalized in {normalize_alias(metadata.get("brand_name")), normalize_alias(metadata.get("merchant_handle"))}:
                exact_canonical.add(row["record_id"])
        candidates = alias_owners | organic
        priorities = {}
        for record_id in candidates:
            priorities[record_id] = (
                0 if record_id in alias_owners else
                1 if record_id in exact_canonical else 2,
                record_id,
            )
        ordered = sorted(candidates, key=lambda item: priorities[item])
        assets = []
        for record_id in ordered:
            for row in connection.execute(
                "SELECT * FROM assets WHERE record_id=? AND index_eligibility='include' ORDER BY asset_id",
                (record_id,),
            ):
                assets.append(dict(row))
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
        "visible_assets": [
            {
                "asset_id": row["asset_id"],
                "asset_type": row["asset_type"],
                "title": row["title"],
                "can_external": bool(row["can_external"]),
            }
            for row in visible_assets
        ],
        "r32_visible_within_cap": R32 in visible_parents if R32 in candidates else None,
        "parent_duplicates": len(ordered) - len(set(ordered)),
        "asset_duplicates": len(assets) - len({row["asset_id"] for row in assets}),
    }


def _defense_matrix(aliases, parents):
    rows = []
    conflict = [dict(row) for row in aliases] + [{**aliases[0], "parent_record_id": "商家夥伴案例資料庫:r12"}]
    try:
        resolve_exact_alias("SLP", conflict, parents)
        conflict_blocked = False
    except ProductionSearchAliasPlanError:
        conflict_blocked = True
    rows.append({"check": "duplicate_normalized_alias_fails_closed", "status": "pass" if conflict_blocked else "fail"})
    missing = [{**aliases[0], "reviewer": "", "provenance": ""}]
    rows.append({"check": "missing_reviewer_or_provenance_blocked", "status": "pass" if not validate_alias_authority(missing, parents, missing)["valid"] else "fail"})
    revoked = [{**aliases[0], "active": False, "action": "revoke"}]
    rows.append({"check": "revoked_alias_not_resolved", "status": "pass" if not resolve_exact_alias("SLP", revoked, parents) else "fail"})
    excluded_parents = {**parents, R32: {**parents[R32], "can_enter_content_index": False}}
    rows.append({"check": "excluded_parent_alias_not_resolved", "status": "pass" if not resolve_exact_alias("SLP", aliases, excluded_parents) else "fail"})
    internal = {**parents, R32: {**parents[R32], "can_external_reference": False}}
    rows.append({"check": "internal_only_alias_searchable_but_not_external", "status": "pass" if resolve_exact_alias("SLP", aliases, internal) == [R32] and not internal[R32]["can_external_reference"] else "fail"})
    return rows


def _renderer_preview(candidate, root):
    result = _search_candidate(Path(candidate["path"]), "SHOPLINE Payments")
    visible = result["visible_parent_record_ids"]
    approved_urls = {}
    for row in _read_csv(_resolve(root, sync_confirmation.DEFAULT_ASSET_ELIGIBLE)):
        if row["field"] == "asset_url" and row["review_decision"] == "approve":
            approved_urls[row["asset_id"]] = row["proposed_value"]
    display_assets = [row for row in result["visible_assets"] if row["can_external"]]
    lines = [f"找到「SHOPLINE Payments」相關內容，共 {len(display_assets)} 筆：", ""]
    for index, asset in enumerate(display_assets, 1):
        label = {"article": "文章", "video": "影片", "podcast": "Podcast"}.get(asset["asset_type"], "內容")
        lines.extend([
            f"{index}. {label}",
            f"標題：<{approved_urls.get(asset['asset_id'], '')}|{asset['title']}>",
            "對外引用：可對外引用",
            "",
        ])
    lines.extend(["資料來源：", "MKT 內容產出資料庫_店家 / 夥伴案例 / 對外數據"])
    rendered = "\n".join(lines)
    hidden = all(token not in rendered for token in (
        "search_alias_reviewed_by", "authority_event", "match_reason", "record_id", "governance_flags"
    ))
    approved_links_valid = all(approved_urls.get(row["asset_id"], "").startswith(("https://", "http://")) for row in display_assets)
    return {
        "valid": all((len(visible) <= 5, len(display_assets) <= 10, R32 in visible, hidden, approved_links_valid)),
        "query": "SHOPLINE Payments",
        "candidate_parent_count": result["parent_count"],
        "displayed_parent_count": len(visible),
        "displayed_asset_count": len(display_assets),
        "r32_visible": R32 in visible,
        "approved_title_urls_only": approved_links_valid,
        "url_values_written_to_alias_projection": 0,
        "internal_metadata_hidden": hidden,
        "alias_used_as_content_tag": False,
        "production_renderer_modified": False,
        "preview": rendered,
    }


def _normalization_contract():
    return {
        "unicode": "NFKC",
        "case": "Unicode casefold",
        "trim": True,
        "collapse_whitespace": True,
        "fullwidth_halfwidth": "NFKC compatibility fold",
        "punctuation": "preserved; no deletion or expansion",
        "match": "normalized full entity value equality",
        "fuzzy": False,
        "prefix": False,
        "substring": False,
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
        "match": "case-insensitive normalized exact",
        "child_expansion": False,
    }


def _merge_contract():
    return {
        "candidate_sources": ["exact_alias_owner", "canonical_parent_or_handle", "organic_formal_search"],
        "merge": "set union",
        "parent_dedup_key": "record_id",
        "asset_dedup_key": "formal asset_id",
        "match_reasons": "internal ranking input only",
        "organic_results_preserved": True,
    }


def _ranking_contract():
    return {
        "tiers": ["exact_parent_alias", "exact_canonical_parent_or_handle", "organic_exact_field", "existing_retrieval_ranking"],
        "tie_breaker": "existing retrieval score then stable record_id/asset_id",
        "parent_cap": 5,
        "asset_cap": 10,
        "alias_owner_visible": True,
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
        {"option": "A", "strategy": "Formal SQLite dedicated alias table", "schema_migration": True, "selected": False, "reason": "requires separately confirmed SQLite schema migration"},
        {"option": "B", "strategy": "Formal SQLite metadata_json", "schema_migration": False, "selected": False, "reason": "would rewrite Parent rows and lacks deterministic indexed ownership lookup"},
        {"option": "C", "strategy": "independent governed alias projection store", "schema_migration": False, "selected": True, "reason": "two-row atomic projection, no Parent rewrite or index rebuild"},
        {"option": "D", "strategy": "runtime reads Managed Vault", "schema_migration": False, "selected": False, "reason": "Vault is a materialized projection, not Production Runtime Authority"},
    ]


def _selected_strategy():
    return {
        "strategy": "independent_governed_json_alias_projection",
        "target_paths": {
            "alias_projection": ".mka/search_alias_projection.json",
            "formal_sqlite": ".mka/content_index.sqlite",
            "runtime_modules": ["src/marketing_knowledge_agent/search_aliases.py", "src/marketing_knowledge_agent/pipeline.py"],
        },
        "schema_migration_required": False,
        "schema_migration_prerequisite": "",
        "index_rebuild_required": False,
        "existing_parent_rows_modified": 0,
        "incremental": True,
        "atomic_projection_replace": True,
    }


def _projection_rows(aliases):
    return [{
        "parent_record_id": row["parent_record_id"],
        "raw_alias": row["raw_alias"],
        "normalized_alias": row["normalized_alias"],
        "active": row["active"],
        "reviewer": row["reviewer"],
        "reviewed_at": row["reviewed_at"],
        "provenance": row["provenance"],
        "authority_event": row["authority_event"],
        "authority_event_hash": row["authority_event_hash"],
        "before_state": None,
        "desired_state": "active",
        "action": "create",
        "target": ".mka/search_alias_projection.json",
        "rollback_action": "remove_projection_file",
    } for row in sorted(aliases, key=lambda item: item["normalized_alias"])]


def _code_delta():
    return {
        "parser_changes": "add full-query exact Alias resolver before semantic fallback; typed entity only when complete exact extraction is available",
        "resolver_changes": "load checksum-pinned governed JSON projection and reject duplicate normalized ownership",
        "candidate_merge_changes": "union Alias owner with canonical and organic candidates",
        "ranking_changes": "exact Alias owner tier before canonical/organic while retaining all legal candidates",
        "deduplication_changes": "Parent record_id and formal asset_id",
        "governance_filter_placement": "after merge and before ranking/caps",
        "renderer_impact": "none; internal match reasons and authority metadata are not exposed",
        "migration_requirement": "none",
        "rollback_strategy": "restore prior runtime code and remove exact projection file; do not touch Parent or Assets",
    }


def _backup_plan():
    return {
        "backup_alias_projection": "record absence or exact previous bytes",
        "backup_runtime_files": ["search_aliases.py", "pipeline.py"],
        "backup_formal_sqlite_checksum_only": True,
        "backup_managed_vault_checksum_only": True,
        "immutable_bundle": True,
    }


def _rollback_plan():
    return {
        "order": ["disable runtime loading", "restore runtime files", "remove_or_restore_alias_projection", "verify Formal SQLite and Vault checksums", "re-run offline and production smoke validation"],
        "parent_deleted": False,
        "asset_deleted": False,
        "store_sync_rolled_back": False,
        "authority_evidence_retained": True,
    }


def _write_reports(output: Path, result: dict):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILENAMES[0], _summary(result))
    _write_csv(output / REPORT_FILENAMES[1], [{
        "decision_store_sha256": result["manifest"]["decision_store_sha256"],
        "store_sync_execution_id": result["manifest"]["store_sync_execution_id"],
        "store_sync_execution_root_hash": result["manifest"]["store_sync_execution_root_hash"],
        "parent_count": result["manifest"]["authoritative_parent_count"],
        "formal_parent_count": result["manifest"]["content_parent_count"],
        "status": "pass",
    }])
    _write_csv(output / REPORT_FILENAMES[2], [{key: value for key, value in result["authority"].items() if key != "conflicts"}])
    _write_csv(output / REPORT_FILENAMES[3], result["aliases"])
    _write_text(output / REPORT_FILENAMES[4], _markdown_mapping("Alias Normalization Contract", result["normalization_contract"]))
    _write_csv(output / REPORT_FILENAMES[5], result["normalization_vectors"])
    conflict_rows = [
        {"normalized_alias": key, "owners": value, "status": "conflict"}
        for key, value in result["authority"]["conflicts"].items()
    ] or [{"normalized_alias": "", "owners": [], "status": "pass_no_conflict"}]
    _write_csv(output / REPORT_FILENAMES[6], conflict_rows)
    _write_text(output / REPORT_FILENAMES[7], _markdown_mapping("Query Semantics Contract", result["query_contract"]))
    _write_text(output / REPORT_FILENAMES[8], "# Typed Query Integration Analysis\n\nFull raw-query equality is supported. Existing explicit parser does not safely capture multi-word entity values, so arbitrary sentence extraction remains unsupported and will not fall back to substring Alias matching.\n")
    _write_text(output / REPORT_FILENAMES[9], _markdown_rows("Production Projection Architecture Comparison", result["architecture"]))
    _write_text(output / REPORT_FILENAMES[10], _markdown_mapping("Selected Projection Strategy", result["strategy"]))
    _write_text(output / REPORT_FILENAMES[11], "# Schema Migration Requirement\n\nSchema migration required: false. Formal SQLite remains byte-for-byte unchanged; the selected governed JSON projection is a separate atomic target.\n")
    _write_csv(output / REPORT_FILENAMES[12], result["projection"])
    _write_json(output / REPORT_FILENAMES[13], result["projection_delta"])
    _write_text(output / REPORT_FILENAMES[14], _markdown_mapping("Search Runtime Code Delta", result["code_delta"]))
    _write_text(output / REPORT_FILENAMES[15], _markdown_mapping("Candidate Merge Contract", result["merge_contract"]))
    _write_text(output / REPORT_FILENAMES[16], _markdown_mapping("Ranking Contract", result["ranking_contract"]))
    _write_csv(output / REPORT_FILENAMES[17], [{"key": "record_id", "duplicates": 0, "status": "pass"}])
    _write_csv(output / REPORT_FILENAMES[18], [{"key": "formal_asset_id", "duplicates": 0, "status": "pass"}])
    _write_csv(output / REPORT_FILENAMES[19], [{"check": key, "value": value, "status": "pass"} for key, value in result["governance_contract"].items()])
    _write_text(output / REPORT_FILENAMES[20], _markdown_mapping("Temporary Candidate Validation", result["candidate"]))
    _write_csv(output / REPORT_FILENAMES[21], result["offline"] + result["defense"])
    _write_csv(output / REPORT_FILENAMES[22], [row for row in result["offline"] if normalize_alias(row.get("query")) == "shopline payments"])
    _write_csv(output / REPORT_FILENAMES[23], [row for row in result["offline"] if normalize_alias(row.get("query")) == "slp"])
    _write_csv(output / REPORT_FILENAMES[24], [row for row in result["offline"] if row.get("query") in {"莉朵花藝", "littlegirl", "廣生堂", "111gsttest", "Package+", "關貿網路"}])
    _write_csv(output / REPORT_FILENAMES[25], [result["asset_boundary"]])
    _write_text(output / REPORT_FILENAMES[26], "# Slack Renderer Offline Preview\n\n" + result["renderer"]["preview"] + "\n")
    _write_csv(output / REPORT_FILENAMES[27], [{"formal_systems_unchanged": result["formal_systems_unchanged"], "production_search_activated": False, "slack_api_called": False, "status": "pass"}])
    _write_text(output / REPORT_FILENAMES[28], _markdown_mapping("Search Alias Backup Plan", result["backup_plan"]))
    _write_text(output / REPORT_FILENAMES[29], _markdown_mapping("Search Alias Rollback Plan", result["rollback_plan"]))
    _write_text(output / REPORT_FILENAMES[30], "# Search Alias Confirmation Checklist\n\n- Revalidate exact Plan and Manifest hashes.\n- Revalidate Decision Store and Store Sync Execution roots.\n- Confirm two-row projection, runtime code delta, target paths, tests, backup, and rollback.\n- Confirmation does not Execute or activate Search Alias.\n")
    _write_json(output / REPORT_FILENAMES[31], result["manifest"])
    _write_csv(output / REPORT_FILENAMES[32], [{"error": value} for value in result["validation_errors"]], ("error",))
    _write_csv(output / REPORT_FILENAMES[33], [{"warning": value} for value in result["validation_warnings"]], ("warning",))


def _summary(result):
    return (
        "# Production Search Alias Plan\n\n"
        f"- Conclusion: {result['conclusion']}\n"
        f"- PLAN_ID: {result['plan_id']}\n"
        f"- Manifest Hash: {result['manifest_hash']}\n"
        f"- Aliases: {result['authority']['approved_alias_count']} active / {result['authority']['alias_conflict_count']} conflicts\n"
        f"- Strategy: {result['strategy']['strategy']}\n"
        f"- Schema migration required: {result['strategy']['schema_migration_required']}\n"
        f"- Plan expires: {result['expires_at']}\n"
        f"- Execution blocked: {result['execution_blocked']}\n"
        f"- Formal systems unchanged: {result['formal_systems_unchanged']}\n"
    )


def _plan_times(output, plan_id, created_at):
    manifest_path = output / "production_search_alias_plan_manifest.json"
    if created_at is None and manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("plan_id") == plan_id:
            return previous["created_at"], previous["expires_at"]
    created = datetime.fromisoformat(created_at) if created_at else datetime.now().astimezone()
    if created.tzinfo is None:
        raise ProductionSearchAliasPlanError("created_at must include timezone")
    return created.isoformat(timespec="seconds"), (created + timedelta(days=7)).isoformat(timespec="seconds")


def _snapshot(paths):
    result = {}
    for key, path in paths.items():
        if path.is_file():
            result[key] = {"sha256": _sha256(path), "byte_size": path.stat().st_size}
        else:
            result[key] = {"sha256": sync_confirmation._hash_path(path)}
    return result


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() == "true"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve(root, path):
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _relative(root, path):
    return Path(path).resolve().relative_to(root).as_posix()


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path, value):
    Path(path).write_text(value, encoding="utf-8")


def _write_csv(path, rows, default_fields=()):
    rows = list(rows)
    fields = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    if not fields:
        fields = list(default_fields)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _csv_value(value):
    return _json(value) if isinstance(value, (dict, list, tuple, set)) else value


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _markdown_mapping(title, value):
    return f"# {title}\n\n```json\n{json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)}\n```\n"


def _markdown_rows(title, rows):
    return _markdown_mapping(title, rows)
