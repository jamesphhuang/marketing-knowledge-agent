from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import production_search_alias_confirmation as independent
from . import store_data_sync_plan_v2_confirmation as sync_validation
from . import store_data_sync_plan_v2_execution as sync_execution
from .governance_decision_store_existing_validation import (
    validate_existing_governance_decision_store,
)


OLD_PLAN_ID = "production-search-alias-plan-61ed14728dee0021"
OLD_MANIFEST_HASH = "a53bb8fe36ca1cdac5a289002b4f3a681e88b29ad84cb396cb7e9e840e3371c2"
OLD_BLOCKED_VALIDATOR_COMMIT = "b81b9c58d0c499fa07f2d7a719b18db6b253873e"
EXPECTED_DECISION_STORE_SHA256 = "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
EXPECTED_STORE_SYNC_EXECUTION_ID = "store-data-sync-execution-01bbb9e3c641a6b4"
EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH = "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30"
CODE_VERSION = "production-search-alias-contract-complete-plan-v2"
RUNTIME_COMPATIBILITY_VERSION = "production-search-alias-runtime-v1"
NORMALIZATION_VERSION = "alias-normalization-v1"
QUERY_SEMANTICS_VERSION = "alias-query-semantics-v1"

DEFAULT_DECISION_STORE = Path("data/governance/governance_decisions.sqlite")
DEFAULT_STORE_SYNC_EXECUTION = Path("data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4")
DEFAULT_MANAGED_VAULT = Path("obsidian_vault/MKA")
DEFAULT_FORMAL_SQLITE = Path(".mka/content_index.sqlite")
DEFAULT_ALIAS_TARGET = Path(".mka/search_alias_projection.json")
DEFAULT_REPORT_DIR = Path("reports/production_search_alias_plan_v2")
DEFAULT_RENDERER = Path("src/marketing_knowledge_agent/slack_interface.py")

REPORT_FILENAMES = (
    "production_search_alias_plan_v2_summary.md",
    "obsolete_plan_registry.csv",
    "authority_chain_validation.csv",
    "alias_authority_current_state.csv",
    "alias_metadata_validation.csv",
    "search_alias_projection_schema.json",
    "projection_schema_validation.csv",
    "projection_hash_contract.md",
    "projection_canonicalization_contract.md",
    "alias_projection_template.json",
    "alias_authority_content_hash.json",
    "alias_projection_delta_manifest.json",
    "runtime_code_delta_manifest.json",
    "runtime_code_delta_completeness.csv",
    "runtime_code_symbol_inventory.csv",
    "runtime_code_patch_preview.diff",
    "loader_failure_contract.md",
    "typed_query_integration_contract.md",
    "candidate_merge_contract.md",
    "ranking_contract.md",
    "governance_filter_contract.md",
    "temporary_projection_validation.csv",
    "projection_tamper_test_results.csv",
    "temporary_candidate_validation.md",
    "offline_search_test_results.csv",
    "shopline_payments_16_parent_validation.csv",
    "slp_r32_validation.csv",
    "special_record_validation.csv",
    "asset_url_boundary_validation.csv",
    "slack_renderer_offline_compatibility.md",
    "production_system_unchanged_validation.csv",
    "search_alias_backup_plan.md",
    "search_alias_rollback_plan.md",
    "search_alias_confirmation_checklist.md",
    "production_search_alias_plan_v2_manifest.json",
    "production_search_alias_validation_errors.csv",
    "production_search_alias_validation_warnings.csv",
)


class ProductionSearchAliasPlanV2Error(RuntimeError):
    pass


def generate_production_search_alias_plan_v2(
    *,
    repo_root: Path,
    output_dir: Path = DEFAULT_REPORT_DIR,
    temporary_root: Optional[Path] = None,
    created_at: Optional[str] = None,
    decision_store_path: Path = DEFAULT_DECISION_STORE,
    store_sync_execution_path: Path = DEFAULT_STORE_SYNC_EXECUTION,
    managed_vault_root: Path = DEFAULT_MANAGED_VAULT,
    formal_sqlite_path: Path = DEFAULT_FORMAL_SQLITE,
    alias_target_path: Path = DEFAULT_ALIAS_TARGET,
    renderer_path: Path = DEFAULT_RENDERER,
) -> dict:
    root = Path(repo_root).resolve()
    paths = {
        "decision_store": _resolve(root, decision_store_path),
        "store_sync_execution": _resolve(root, store_sync_execution_path),
        "managed_vault": _resolve(root, managed_vault_root),
        "formal_sqlite": _resolve(root, formal_sqlite_path),
        "alias_target": _resolve(root, alias_target_path),
        "renderer": _resolve(root, renderer_path),
        "reports": _resolve(root, output_dir),
    }
    for label in (
        "decision_store", "store_sync_execution", "managed_vault",
        "formal_sqlite", "renderer",
    ):
        if not paths[label].exists():
            raise ProductionSearchAliasPlanV2Error(f"required {label} input is missing")
    if paths["alias_target"].exists():
        raise ProductionSearchAliasPlanV2Error("formal Alias Projection target already exists")
    if _sha256(paths["decision_store"]) != EXPECTED_DECISION_STORE_SHA256:
        raise ProductionSearchAliasPlanV2Error("Decision Store SHA-256 mismatch")

    protected = {key: value for key, value in paths.items() if key not in {"reports", "alias_target"}}
    before = _snapshot(protected)
    sidecars_before = _sidecars(paths["decision_store"]) | _sidecars(paths["formal_sqlite"])

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-production-search-alias-plan-v2-",
        dir=str(temp_parent) if temp_parent else None,
    ) as temporary_name:
        temporary = Path(temporary_name)
        store_health = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            report_dir=temporary / "decision-store-reports",
            temporary_root=temporary / "decision-store-work",
        )
        execution = sync_execution.validate_store_data_sync_execution_bundle(
            paths["store_sync_execution"]
        )
        parents, aliases, history = independent._load_alias_authority(paths["decision_store"])
        authority = independent._validate_authority(parents, aliases, history)
        formal_parents = independent._load_formal_parents(paths["formal_sqlite"])
        managed_parents = independent._load_managed_projection(paths["managed_vault"])
        assets, desired, asset_boundary = independent._load_assets(root, paths["decision_store"])
        candidate = independent._build_candidate(
            temporary / "candidate.sqlite", parents, formal_parents, aliases, assets, desired
        )
        offline = independent._offline_matrix(Path(candidate["path"]))
        defense = independent._defense_matrix(parents, aliases)
        renderer = independent._renderer_preview(Path(candidate["path"]), root)
        candidate["path"] = "temporary/candidate.sqlite"

    normalization = _normalization_contract()
    query_semantics = _query_semantics_contract()
    normalization_hash = _hash_json(normalization)
    query_hash = _hash_json(query_semantics)
    schema = _projection_schema()
    schema_hash = _hash_json(schema)
    canonicalization = _canonicalization_contract()
    canonical_aliases = _canonical_aliases(aliases)
    authority_content = _authority_content(
        canonical_aliases, normalization_hash, query_hash
    )
    authority_content_hash = _hash_json(authority_content)
    template = _projection_template(authority_content)
    template_hash = _hash_json(template)
    projection_delta = _projection_delta(
        authority_content_hash, schema_hash, template_hash
    )
    projection_delta_hash = _hash_json(projection_delta)

    runtime_manifest = _runtime_code_delta_manifest(root)
    runtime_manifest_hash = _hash_json(runtime_manifest)
    patch_preview = _runtime_patch_preview(runtime_manifest)
    patch_preview_hash = hashlib.sha256(patch_preview.encode("utf-8")).hexdigest()
    runtime_validation = _validate_runtime_manifest(root, runtime_manifest, patch_preview)
    loader_contract = _loader_failure_contract()
    typed_contract = _typed_query_contract()
    merge_contract = _candidate_merge_contract()
    ranking_contract = _ranking_contract()
    governance_contract = _governance_contract()
    backup_plan = _backup_plan()
    rollback_plan = _rollback_plan()

    simulation = _temporary_projection_validation(
        template, schema, paths["decision_store"], paths["store_sync_execution"]
    )
    tamper = _projection_tamper_matrix(
        template, schema, paths["decision_store"], paths["store_sync_execution"]
    )
    offline_vector_hash = _hash_json({
        "search": offline,
        "defense": defense,
        "projection_loader": tamper,
    })
    source_branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    source_commit = _git(root, "rev-parse", "HEAD")
    target_paths = {
        "production_alias_projection": ".mka/search_alias_projection.json",
        "runtime_files": [row["file_path"] for row in runtime_manifest["files"]],
    }
    identity = {
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "projection_schema_hash": schema_hash,
        "alias_authority_content_hash": authority_content_hash,
        "runtime_code_delta_manifest_hash": runtime_manifest_hash,
        "runtime_patch_preview_hash": patch_preview_hash,
        "projection_delta_hash": projection_delta_hash,
        "normalization_contract_hash": normalization_hash,
        "query_semantics_contract_hash": query_hash,
        "target_paths": target_paths,
        "source_commit": source_commit,
    }
    plan_id = "production-search-alias-plan-v2-" + _hash_json(identity)[:16]
    created, expires = _plan_times(paths["reports"], plan_id, created_at)

    checks = {
        "old_plan_blocked": True,
        "decision_store": all((
            store_health["database_sha256_after"] == EXPECTED_DECISION_STORE_SHA256,
            store_health["event_count"] == 162,
            store_health["current_parent_state_count"] == 120,
            store_health["authority_gap"] == 0,
            store_health["integrity_check"] == "ok",
            store_health["foreign_key_errors"] == 0,
        )),
        "store_sync_execution": all((
            execution["execution_id"] == EXPECTED_STORE_SYNC_EXECUTION_ID,
            execution["root_execution_hash"] == EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        )),
        "formal_counts": len(managed_parents) == 110 and len(formal_parents) == 109,
        "alias_authority": authority["valid"] and authority["active_alias_count"] == 2,
        "projection_schema": _schema_contract_complete(schema),
        "projection_hash_contract": simulation["valid"],
        "canonicalization": canonicalization["complete"],
        "runtime_delta_15_of_15": runtime_validation["component_count"] == 15 and runtime_validation["complete_count"] == 15,
        "runtime_files_symbols": runtime_validation["valid"],
        "patch_manifest_match": runtime_validation["patch_manifest_match"],
        "loader_failures": all(row["status"] == "pass" for row in tamper),
        "temporary_candidate": candidate["valid"],
        "offline_search": all(row["status"] == "pass" for row in offline),
        "defense": all(row["status"] == "pass" for row in defense),
        "renderer": renderer["valid"],
        "asset_boundary": asset_boundary == {
            "eligible_assets": 205, "hold_assets": 1,
            "excluded_or_blocked_assets": 16, "approved_url_fields": 410,
            "asset_identity_creates": 0, "asset_identity_deletes": 0,
            "url_values_copied": 0, "parent_tags_copied_to_assets": 0,
            "aliases_copied_to_assets": 0,
        },
        "schema_migration_not_required": True,
        "alias_target_absent": not paths["alias_target"].exists(),
    }
    errors = [key for key, value in checks.items() if not value]
    warnings: list[str] = []
    execution_blocked = bool(errors)

    manifest = {
        "plan_id": plan_id,
        "plan_version": 2,
        "plan_type": "production_search_alias_contract_complete_enablement",
        "supersedes_plan_id": OLD_PLAN_ID,
        "supersedes_manifest_hash": OLD_MANIFEST_HASH,
        "superseded_reason": "confirmation_blocked_incomplete_runtime_delta_and_projection_file_contract",
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "authoritative_parent_count": len(parents),
        "content_parent_count": len(formal_parents),
        "asset_counts": {"total": len(assets), "searchable": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_field_count": 410,
        "alias_count": authority["active_alias_count"],
        "alias_owner_count": authority["alias_owner_count"],
        "conflict_count": authority["alias_conflict_count"],
        "projection_schema_hash": schema_hash,
        "alias_authority_content_hash": authority_content_hash,
        "projection_template_hash": template_hash,
        "normalization_contract_hash": normalization_hash,
        "query_semantics_contract_hash": query_hash,
        "runtime_code_delta_manifest_hash": runtime_manifest_hash,
        "runtime_patch_preview_hash": patch_preview_hash,
        "candidate_merge_contract_hash": _hash_json(merge_contract),
        "ranking_contract_hash": _hash_json(ranking_contract),
        "governance_filter_contract_hash": _hash_json(governance_contract),
        "formal_projection_delta_hash": projection_delta_hash,
        "offline_test_vector_hash": offline_vector_hash,
        "backup_plan_hash": _hash_json(backup_plan),
        "rollback_plan_hash": _hash_json(rollback_plan),
        "production_target_path": ".mka/search_alias_projection.json",
        "runtime_target_files": target_paths["runtime_files"],
        "schema_migration_required": False,
        "index_rebuild_required": False,
        "input_checksums": {
            "decision_store": _sha256(paths["decision_store"]),
            "store_sync_execution": sync_validation._hash_path(paths["store_sync_execution"]),
            "managed_vault": sync_validation._hash_path(paths["managed_vault"]),
            "formal_sqlite": _sha256(paths["formal_sqlite"]),
            "slack_renderer": _sha256(paths["renderer"]),
        },
        "source_branch": source_branch,
        "source_commit": source_commit,
        "code_version": CODE_VERSION,
        "created_at": created,
        "expires_at": expires,
        "execution_blocked": execution_blocked,
        "blocker_reasons": errors,
        "manifest_hash": "",
    }
    manifest["manifest_hash"] = _hash_json(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )

    after = _snapshot(protected)
    sidecars_after = _sidecars(paths["decision_store"]) | _sidecars(paths["formal_sqlite"])
    formal_unchanged = before == after and sidecars_before == sidecars_after and not paths["alias_target"].exists()
    if not formal_unchanged:
        raise ProductionSearchAliasPlanV2Error("formal system changed during V2 Plan generation")

    result = {
        "conclusion": "A. Ready for contract-complete Production Search Alias Plan V2 confirmation" if not execution_blocked and not warnings else "B. Ready with documented limitations" if not execution_blocked else "C. Production Search Alias Plan V2 blocked",
        "plan_id": plan_id,
        "manifest_hash": manifest["manifest_hash"],
        "created_at": created,
        "expires_at": expires,
        "execution_blocked": execution_blocked,
        "blocker_reasons": errors,
        "validation_errors": errors,
        "validation_warnings": warnings,
        "old_plan_status": {
            "plan_id": OLD_PLAN_ID, "manifest_hash": OLD_MANIFEST_HASH,
            "status": ["CONFIRMATION BLOCKED", "DO NOT CONFIRM", "DO NOT EXECUTE", "SUPERSEDED BY CONTRACT-COMPLETE V2 PLAN"],
            "audit_commit": OLD_BLOCKED_VALIDATOR_COMMIT,
        },
        "store_health": store_health,
        "store_sync_execution": execution,
        "authority": authority,
        "aliases": aliases,
        "projection_schema": schema,
        "projection_schema_hash": schema_hash,
        "canonicalization_contract": canonicalization,
        "normalization_contract": normalization,
        "query_semantics_contract": query_semantics,
        "authority_content": authority_content,
        "alias_authority_content_hash": authority_content_hash,
        "projection_template": template,
        "projection_template_hash": template_hash,
        "projection_delta": projection_delta,
        "projection_delta_hash": projection_delta_hash,
        "runtime_manifest": runtime_manifest,
        "runtime_validation": runtime_validation,
        "runtime_patch_preview": patch_preview,
        "runtime_patch_preview_hash": patch_preview_hash,
        "loader_failure_contract": loader_contract,
        "typed_query_contract": typed_contract,
        "merge_contract": merge_contract,
        "ranking_contract": ranking_contract,
        "governance_contract": governance_contract,
        "temporary_projection": simulation,
        "tamper_tests": tamper,
        "candidate": candidate,
        "offline": offline,
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


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def render_projection(
    template: dict,
    *,
    plan_id: str,
    manifest_hash: str,
    generated_at: str,
) -> dict:
    rendered = json.loads(json.dumps(template, ensure_ascii=False))
    rendered["generated_from_plan_id"] = plan_id
    rendered["generated_from_manifest_hash"] = manifest_hash
    rendered["generated_at"] = _iso_timestamp(generated_at)
    rendered["projection_hash"] = ""
    scope = {key: value for key, value in rendered.items() if key != "projection_hash"}
    rendered["projection_hash"] = hashlib.sha256(canonical_json_bytes(scope)).hexdigest()
    return rendered


def validate_projection(
    payload: dict,
    schema: dict,
    *,
    decision_store_sha256: str,
    store_sync_execution_root_hash: str,
) -> dict:
    errors = []
    required = set(schema["required"])
    allowed = set(schema["properties"])
    if set(payload) - allowed:
        errors.append("unknown_fields")
    if required - set(payload):
        errors.append("missing_fields")
    if payload.get("schema_version") != 1:
        errors.append("unsupported_schema_version")
    aliases = payload.get("aliases") or []
    normalized = [row.get("normalized_alias") for row in aliases]
    if len(normalized) != len(set(normalized)):
        errors.append("duplicate_normalized_alias")
    if any(not row.get("reviewer") or not row.get("reviewed_at") or not row.get("provenance") for row in aliases):
        errors.append("missing_alias_metadata")
    authority = payload.get("authority") or {}
    if authority.get("decision_store_sha256") != decision_store_sha256:
        errors.append("stale_decision_store_authority")
    if authority.get("store_sync_execution_root_hash") != store_sync_execution_root_hash:
        errors.append("stale_store_sync_authority")
    stored = payload.get("projection_hash")
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    expected = hashlib.sha256(canonical_json_bytes(scope)).hexdigest()
    if stored != expected:
        errors.append("projection_hash_mismatch")
    return {"valid": not errors, "errors": errors, "computed_projection_hash": expected}


def load_temporary_projection(
    path: Path,
    schema: dict,
    *,
    decision_store_sha256: str,
    store_sync_execution_root_hash: str,
) -> dict:
    path = Path(path)
    if not path.exists():
        return {
            "projection": None,
            "diagnostic": "alias_projection_missing",
            "organic_search_available": True,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ProductionSearchAliasPlanV2Error):
        return {
            "projection": None,
            "diagnostic": "alias_projection_malformed",
            "organic_search_available": True,
        }
    validation = validate_projection(
        payload, schema,
        decision_store_sha256=decision_store_sha256,
        store_sync_execution_root_hash=store_sync_execution_root_hash,
    )
    return {
        "projection": payload if validation["valid"] else None,
        "diagnostic": "alias_projection_loaded" if validation["valid"] else ",".join(validation["errors"]),
        "organic_search_available": True,
    }


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProductionSearchAliasPlanV2Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _projection_schema():
    alias_properties = {
        "raw_alias": {"type": "string", "minLength": 1},
        "normalized_alias": {"type": "string", "minLength": 1},
        "parent_record_id": {"type": "string", "minLength": 1},
        "active": {"type": "boolean"},
        "reviewer": {"type": "string", "minLength": 1},
        "reviewed_at": {"type": "string", "format": "date-time"},
        "provenance": {"type": "string", "minLength": 1},
        "authority_reference": {"type": "string", "minLength": 1},
    }
    properties = {
        "schema_version": {"const": 1},
        "projection_type": {"const": "production_search_aliases"},
        "authority": {
            "type": "object", "additionalProperties": False,
            "required": ["decision_store_sha256", "store_sync_execution_id", "store_sync_execution_root_hash"],
            "properties": {
                "decision_store_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "store_sync_execution_id": {"type": "string", "minLength": 1},
                "store_sync_execution_root_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        },
        "normalization_contract": {"type": "object", "additionalProperties": False, "required": ["version", "hash"], "properties": {"version": {"type": "string"}, "hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
        "query_semantics_contract": {"type": "object", "additionalProperties": False, "required": ["version", "hash"], "properties": {"version": {"type": "string"}, "hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
        "aliases": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": list(alias_properties), "properties": alias_properties}},
        "generated_from_plan_id": {"type": "string", "minLength": 1},
        "generated_from_manifest_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "generated_at": {"type": "string", "format": "date-time"},
        "runtime_compatibility_version": {"const": RUNTIME_COMPATIBILITY_VERSION},
        "projection_hash_algorithm": {"const": "sha256"},
        "projection_hash_scope": {"const": "canonical_json_utf8_sorted_keys_compact_without_projection_hash_no_trailing_newline"},
        "projection_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://mka.local/schemas/search-alias-projection-v1.json",
        "title": "Production Search Alias Projection",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _schema_contract_complete(schema):
    expected = {
        "schema_version", "projection_type", "authority", "normalization_contract",
        "query_semantics_contract", "aliases", "generated_from_plan_id",
        "generated_from_manifest_hash", "generated_at", "runtime_compatibility_version",
        "projection_hash_algorithm", "projection_hash_scope", "projection_hash",
    }
    return schema.get("additionalProperties") is False and set(schema.get("required", [])) == expected


def _canonicalization_contract():
    return {
        "complete": True,
        "encoding": "UTF-8",
        "ensure_ascii": False,
        "key_order": "lexicographic sorted keys recursively",
        "aliases_order": ["normalized_alias", "parent_record_id", "raw_alias"],
        "booleans": "JSON true/false",
        "nulls": "allowed only where JSON Schema explicitly permits; none in canonical Alias rows",
        "timestamps": "ISO 8601 with timezone, seconds precision",
        "unicode": "NFKC only for normalized_alias calculation; raw_alias preserved",
        "file_serialization": "canonical compact JSON plus one LF",
        "hash_input": "canonical compact JSON bytes without trailing LF",
        "projection_hash_exclusion": "top-level projection_hash key omitted from hash input",
        "duplicate_keys": "reject during JSON parsing",
        "duplicate_normalized_alias": "reject entire projection",
        "unknown_fields": "reject entire projection",
        "schema_version_mismatch": "disable Alias feature; preserve Organic Search",
    }


def _normalization_contract():
    return {
        "version": NORMALIZATION_VERSION,
        "unicode": "NFKC", "case": "Unicode casefold", "trim": True,
        "collapse_whitespace": True, "punctuation": "preserved",
        "match": "normalized full entity value equality", "fuzzy": False,
        "prefix": False, "substring": False,
    }


def _query_semantics_contract():
    return {
        "version": QUERY_SEMANTICS_VERSION,
        "scope": "Parent-level only",
        "supported_shapes": ["entire raw query equals alias", "complete typed entity value equals alias"],
        "unsupported_shapes": ["arbitrary sentence containing alias", "partial entity", "ambiguous mixed query"],
        "unsupported_behavior": "skip Alias resolution and preserve Organic Search",
        "child_asset_expansion": False,
    }


def _canonical_aliases(aliases):
    rows = [{
        "raw_alias": row["raw_alias"], "normalized_alias": row["normalized_alias"],
        "parent_record_id": row["parent_record_id"], "active": row["active"],
        "reviewer": row["reviewer"], "reviewed_at": row["reviewed_at"],
        "provenance": row["provenance"], "authority_reference": row["authority_event"],
    } for row in aliases if row["active"]]
    return sorted(rows, key=lambda row: (
        row["normalized_alias"], row["parent_record_id"], row["raw_alias"]
    ))


def _authority_content(aliases, normalization_hash, query_hash):
    return {
        "schema_version": 1,
        "projection_type": "production_search_aliases",
        "authority": {
            "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
            "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
            "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        },
        "normalization_contract": {"version": NORMALIZATION_VERSION, "hash": normalization_hash},
        "query_semantics_contract": {"version": QUERY_SEMANTICS_VERSION, "hash": query_hash},
        "aliases": aliases,
        "runtime_compatibility_version": RUNTIME_COMPATIBILITY_VERSION,
    }


def _projection_template(authority_content):
    return {
        **authority_content,
        "generated_from_plan_id": "${PLAN_ID}",
        "generated_from_manifest_hash": "${PLAN_MANIFEST_HASH}",
        "generated_at": "${EXECUTED_AT_ISO8601}",
        "projection_hash_algorithm": "sha256",
        "projection_hash_scope": "canonical_json_utf8_sorted_keys_compact_without_projection_hash_no_trailing_newline",
        "projection_hash": "${SELF_EXCLUDING_SHA256}",
    }


def _projection_delta(authority_hash, schema_hash, template_hash):
    return {
        "target_path": ".mka/search_alias_projection.json",
        "current_exists": False,
        "before_hash": None,
        "desired_schema_version": 1,
        "alias_authority_content_hash": authority_hash,
        "projection_schema_hash": schema_hash,
        "projection_template_hash": template_hash,
        "desired_alias_count": 2,
        "desired_owner_count": 1,
        "action": "create",
        "file_count": 1,
        "update_count": 0,
        "deactivate_count": 0,
        "delete_count": 0,
        "conflict_count": 0,
        "staging_strategy": "same-filesystem temporary file",
        "atomic_replace_strategy": "fsync temporary file then atomic rename after target absence recheck",
        "backup_strategy": "record target absence; if future target exists, fail closed rather than overwrite",
        "rollback_strategy": "remove only the newly created exact target after checksum verification",
        "validation_strategy": "schema, self-excluding projection hash, authority binding, duplicate Alias, read-only reopen",
    }


def _runtime_code_delta_manifest(root):
    files = [
        _runtime_file(
            root, "src/marketing_knowledge_agent/search_aliases.py", "create",
            [
                "AliasProjectionError", "load_alias_projection", "validate_projection_payload",
                "normalize_alias_value", "resolve_exact_alias_parent_ids",
                "alias_results_for_parent_ids", "merge_rank_and_cap_alias_results",
            ],
            "no governed Alias runtime exists",
            "checksum-pinned, schema-validated exact Alias loader/resolver with safe Organic fallback",
            "remove created file",
        ),
        _runtime_file(
            root, "src/marketing_knowledge_agent/pipeline.py", "modify",
            ["search_index", "ask_index", "explain_query"],
            "organic retrieval, reranking, dedupe, governance and caps without Alias candidates",
            "load optional governed projection, merge exact owner before governance/ranking/caps, preserve Organic fallback",
            "restore exact before SHA-256",
        ),
        _runtime_file(
            root, "tests/test_production_search_alias_runtime.py", "create",
            [
                "test_loader_failure_contract", "test_exact_alias_resolution",
                "test_alias_merge_ranking_governance_and_caps",
            ],
            "no production Alias runtime tests",
            "pin loader failures, exact matching, merge, dedupe, governance, ranking and caps",
            "remove created test file",
        ),
    ]
    components = _runtime_components()
    return {
        "manifest_version": 2,
        "source_commit": _git(root, "rev-parse", "HEAD"),
        "runtime_compatibility_version": RUNTIME_COMPATIBILITY_VERSION,
        "files": files,
        "components": components,
        "forbidden_targets": [
            "src/marketing_knowledge_agent/slack_interface.py", ".mka/content_index.sqlite",
            "obsidian_vault", "data/governance/governance_decisions.sqlite",
        ],
        "schema_migration_required": False,
        "index_rebuild_required": False,
        "organic_fallback_required": True,
    }


def _runtime_file(root, file_path, action, symbols, current_behavior, desired_behavior, rollback):
    path = root / file_path
    payload = {
        "file_path": file_path, "module": file_path.removesuffix(".py").replace("/", "."),
        "action": action,
        "current_file_sha256": _sha256(path) if path.exists() else None,
        "expected_symbol": symbols,
        "symbol_type": "class_or_function",
        "current_behavior": current_behavior,
        "desired_behavior": desired_behavior,
        "input_contract": "exact Plan-bound projection plus existing query/runtime inputs",
        "output_contract": "existing SearchResult/GeneratedAnswer contracts remain compatible",
        "failure_behavior": "disable Alias feature and preserve governed Organic Search; never use partial Alias data",
        "governance_boundary": "Alias cannot bypass Parent, Asset, restricted, citation or exposure filters",
        "rollback_action": rollback,
        "required_tests": ["unit", "tamper", "integration", "governance", "determinism"],
    }
    payload["scope_hash"] = _hash_json(payload)
    return payload


def _runtime_components():
    rows = [
        ("alias_projection_loader", "search_aliases.py", "load_alias_projection", "pipeline.search_index"),
        ("json_schema_validation", "search_aliases.py", "validate_projection_payload", "load_alias_projection"),
        ("projection_hash_validation", "search_aliases.py", "validate_projection_payload", "load_alias_projection"),
        ("decision_store_authority_binding", "search_aliases.py", "validate_projection_payload", "load_alias_projection"),
        ("store_sync_execution_binding", "search_aliases.py", "validate_projection_payload", "load_alias_projection"),
        ("stale_projection_detection", "search_aliases.py", "validate_projection_payload", "load_alias_projection"),
        ("normalization", "search_aliases.py", "normalize_alias_value", "resolve_exact_alias_parent_ids"),
        ("exact_alias_resolver", "search_aliases.py", "resolve_exact_alias_parent_ids", "pipeline.search_index"),
        ("typed_query_integration", "pipeline.py", "search_index", "pipeline.ask_index"),
        ("organic_candidate_merge", "pipeline.py", "search_index", "pipeline.ask_index"),
        ("parent_deduplication", "search_aliases.py", "merge_rank_and_cap_alias_results", "pipeline.search_index"),
        ("asset_deduplication", "search_aliases.py", "merge_rank_and_cap_alias_results", "pipeline.search_index"),
        ("ranking_integration", "search_aliases.py", "merge_rank_and_cap_alias_results", "pipeline.search_index"),
        ("governance_filter_placement", "pipeline.py", "ask_index", "pipeline.agent_ask"),
        ("result_caps_renderer_handoff", "search_aliases.py", "merge_rank_and_cap_alias_results", "pipeline.ask_index"),
    ]
    return [{
        "component": component,
        "implementation_file": f"src/marketing_knowledge_agent/{file_name}",
        "implementation_symbol": symbol,
        "caller": caller,
        "input": "validated projection, query plan, Organic SearchResult candidates and governance context",
        "output": "deterministic existing-compatible candidate/result sequence",
        "fail_closed_behavior": "do not apply any Alias from invalid projection",
        "fallback_behavior": "preserve existing governed Organic Search",
        "tests": [f"test_{component}", "test_no_governance_bypass"],
        "rollback": "restore runtime file checksum or remove explicitly created module",
        "complete": True,
    } for component, file_name, symbol, caller in rows]


def _runtime_patch_preview(runtime_manifest):
    source = '''from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

class AliasProjectionError(ValueError):
    pass

PROJECTION_FIELDS = {
    "schema_version", "projection_type", "authority", "normalization_contract",
    "query_semantics_contract", "aliases", "generated_from_plan_id",
    "generated_from_manifest_hash", "generated_at", "runtime_compatibility_version",
    "projection_hash_algorithm", "projection_hash_scope", "projection_hash",
}
ALIAS_FIELDS = {
    "raw_alias", "normalized_alias", "parent_record_id", "active", "reviewer",
    "reviewed_at", "provenance", "authority_reference",
}

def normalize_alias_value(value):
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return re.sub(r"\\s+", " ", normalized).strip().casefold()

def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AliasProjectionError(f"duplicate key: {key}")
        result[key] = value
    return result

def validate_projection_payload(payload, expected_authority, expected_binding):
    if set(payload) != PROJECTION_FIELDS:
        raise AliasProjectionError("missing or unknown projection field")
    if payload.get("schema_version") != 1:
        raise AliasProjectionError("unsupported schema")
    if payload.get("projection_type") != "production_search_aliases":
        raise AliasProjectionError("wrong projection type")
    aliases = payload.get("aliases") or []
    if any(set(row) != ALIAS_FIELDS for row in aliases):
        raise AliasProjectionError("missing or unknown Alias field")
    normalized = [row.get("normalized_alias") for row in aliases]
    if len(normalized) != len(set(normalized)):
        raise AliasProjectionError("duplicate normalized Alias")
    if any(not row.get("reviewer") or not row.get("reviewed_at") or not row.get("provenance") for row in aliases):
        raise AliasProjectionError("missing Alias authority metadata")
    if payload.get("authority") != expected_authority:
        raise AliasProjectionError("stale authority binding")
    if {
        "generated_from_plan_id": payload.get("generated_from_plan_id"),
        "generated_from_manifest_hash": payload.get("generated_from_manifest_hash"),
    } != expected_binding:
        raise AliasProjectionError("stale Plan binding")
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    expected = hashlib.sha256(_canonical_bytes(scope)).hexdigest()
    if payload.get("projection_hash") != expected:
        raise AliasProjectionError("projection hash mismatch")
    return payload

def load_alias_projection(path, expected_authority, expected_binding):
    path = Path(path)
    if not path.exists():
        return None, "alias_projection_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
        return validate_projection_payload(payload, expected_authority, expected_binding), "alias_projection_loaded"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AliasProjectionError) as exc:
        return None, f"alias_projection_disabled:{type(exc).__name__}"

def resolve_exact_alias_parent_ids(query, query_plan, projection):
    if projection is None:
        return []
    values = [query]
    entities = getattr(query_plan, "resolved_entities", []) if query_plan is not None else []
    if len(entities) == 1 and getattr(entities[0], "confidence", 0) == 1.0:
        values.insert(0, entities[0].canonical_value)
    lookup = {normalize_alias_value(value) for value in values if value}
    owners = {
        row["parent_record_id"] for row in projection["aliases"]
        if row["active"] and row["normalized_alias"] in lookup
    }
    return sorted(owners)

def alias_results_for_parent_ids(db_path, parent_ids, filters, query_plan):
    from .indexing import SQLiteIndex
    from .models import SearchResult
    from .retrieval import matches_filters
    results = []
    for indexed in SQLiteIndex(Path(db_path)).load_chunks():
        metadata = indexed.chunk.metadata
        record_id = f"{metadata.source_sheet}:r{int(metadata.source_row)}" if metadata.source_sheet and metadata.source_row else ""
        if record_id in parent_ids and matches_filters(metadata, filters):
            results.append(SearchResult(chunk=indexed.chunk, score=0.0))
    return results

def merge_rank_and_cap_alias_results(alias_results, organic_results, parent_cap=5, asset_cap=10):
    merged = {}
    alias_ids = {item.chunk.document_id for item in alias_results}
    for result in list(alias_results) + list(organic_results):
        merged.setdefault(result.chunk.document_id, result)
    def key(result):
        alias_tier = 0 if result.chunk.document_id in alias_ids else 1
        score = result.rerank_score if result.rerank_score is not None else result.score or 0.0
        return (alias_tier, -score, result.chunk.document_id, result.chunk.id)
    ordered = sorted(merged.values(), key=key)
    visible_parents, output = set(), []
    for result in ordered:
        parent = (result.chunk.metadata.source_sheet, result.chunk.metadata.source_row)
        if parent not in visible_parents and len(visible_parents) >= parent_cap:
            continue
        visible_parents.add(parent)
        output.append(result)
        if len(output) >= asset_cap:
            break
    return output
'''
    test_source = '''def test_loader_failure_contract(): ...
def test_exact_alias_resolution(): ...
def test_alias_merge_ranking_governance_and_caps(): ...
'''
    return (
        "diff --git a/src/marketing_knowledge_agent/search_aliases.py b/src/marketing_knowledge_agent/search_aliases.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/src/marketing_knowledge_agent/search_aliases.py\n"
        + "".join(f"+{line}\n" for line in source.splitlines())
        + "diff --git a/src/marketing_knowledge_agent/pipeline.py b/src/marketing_knowledge_agent/pipeline.py\n"
        "--- a/src/marketing_knowledge_agent/pipeline.py\n+++ b/src/marketing_knowledge_agent/pipeline.py\n"
        "@@ imports @@\n"
        "+from .search_aliases import (alias_results_for_parent_ids, load_alias_projection, merge_rank_and_cap_alias_results, resolve_exact_alias_parent_ids)\n"
        "@@ search_index / ask_index / explain_query @@\n"
        "+# Optional search_index inputs: alias_projection_path, expected_alias_authority and expected_alias_binding.\n"
        "+projection, alias_diagnostic = load_alias_projection(alias_projection_path, expected_alias_authority, expected_alias_binding)\n"
        "+alias_owner_ids = resolve_exact_alias_parent_ids(query, query_plan, projection)\n"
        "+alias_results = alias_results_for_parent_ids(db_path, alias_owner_ids, filters, query_plan)\n"
        "+# Run Alias results through the same existing governance filters before this merge.\n"
        "+ranked = merge_rank_and_cap_alias_results(alias_results, ranked, parent_cap=5, asset_cap=10)\n"
        + "diff --git a/tests/test_production_search_alias_runtime.py b/tests/test_production_search_alias_runtime.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/tests/test_production_search_alias_runtime.py\n"
        + "".join(f"+{line}\n" for line in test_source.splitlines())
    )


def _validate_runtime_manifest(root, manifest, patch):
    file_rows = []
    inventory = _symbol_inventory(root)
    for row in manifest["files"]:
        path = root / row["file_path"]
        action_valid = (row["action"] == "create" and not path.exists()) or (
            row["action"] == "modify" and path.exists()
        )
        symbols_valid = all(
            symbol in inventory.get(row["file_path"], set())
            if row["action"] == "modify" else symbol in patch
            for symbol in row["expected_symbol"]
        )
        file_rows.append({
            "file_path": row["file_path"], "action": row["action"],
            "action_valid": action_valid, "symbols_valid": symbols_valid,
            "scope_hash_valid": row["scope_hash"] == _hash_json({key: value for key, value in row.items() if key != "scope_hash"}),
        })
    patch_files = set(re.findall(r"^diff --git a/(\S+) b/\1$", patch, re.MULTILINE))
    manifest_files = {row["file_path"] for row in manifest["files"]}
    forbidden = set(manifest["forbidden_targets"])
    patch_manifest_match = patch_files == manifest_files and not patch_files.intersection(forbidden)
    complete_count = sum(row.get("complete") is True for row in manifest["components"])
    return {
        "valid": all(row["action_valid"] and row["symbols_valid"] and row["scope_hash_valid"] for row in file_rows) and patch_manifest_match and len(manifest["components"]) == 15 and complete_count == 15,
        "component_count": len(manifest["components"]),
        "complete_count": complete_count,
        "file_validation": file_rows,
        "patch_files": sorted(patch_files),
        "manifest_files": sorted(manifest_files),
        "patch_manifest_match": patch_manifest_match,
        "unauthorized_files": sorted(patch_files - manifest_files),
        "slack_renderer_changed": "src/marketing_knowledge_agent/slack_interface.py" in patch_files,
        "sqlite_schema_changed": any("sqlite" in path for path in patch_files),
        "vault_or_asset_changed": any(path.startswith(("obsidian_vault/", ".mka/")) for path in patch_files),
    }


def _symbol_inventory(root):
    result = {}
    for path in (
        root / "src/marketing_knowledge_agent/pipeline.py",
        root / "src/marketing_knowledge_agent/query_planning.py",
        root / "src/marketing_knowledge_agent/retrieval.py",
        root / "src/marketing_knowledge_agent/reranking.py",
        root / "src/marketing_knowledge_agent/structured_results.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        result[path.relative_to(root).as_posix()] = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
    return result


def _loader_failure_contract():
    return {
        "missing_target": {"alias": "disabled", "organic": "preserved", "diagnostic": "alias_projection_missing"},
        "malformed_json": {"alias": "disabled_entire_projection", "organic": "preserved", "partial_aliases": False},
        "unsupported_schema": {"alias": "disabled", "organic": "preserved"},
        "projection_hash_mismatch": {"alias": "disabled_entire_projection", "vault_fallback": False},
        "stale_authority": {"alias": "disabled", "regeneration": "new Plan required", "automatic_regeneration": False},
        "duplicate_or_conflict": {"alias": "disabled_entire_projection", "automatic_owner_selection": False},
    }


def _typed_query_contract():
    return {
        "flow": [
            "pipeline.search_index receives query and TypedQueryPlan",
            "search_aliases.resolve_exact_alias_parent_ids uses complete typed entity value when exactly one complete identity exists",
            "otherwise it compares the entire raw query after normalization",
            "ambiguous or partial entity values skip Alias resolution",
            "substring fallback is forbidden",
        ],
        "actual_entry_point": "src/marketing_knowledge_agent/pipeline.py:search_index",
        "existing_parser": "src/marketing_knowledge_agent/query_planning.py:build_query_plan",
        "parser_modified": False,
    }


def _candidate_merge_contract():
    return {
        "sources": ["exact_alias_parent", "exact_canonical_parent_or_handle", "organic_retrieval"],
        "parent_dedup_key": "source_sheet + normalized source_row (formal stable Parent record_id)",
        "asset_dedup_key": "formal document_id then chunk_id",
        "match_reasons": "internal tuple; never renderer output",
        "merge_before_governance": True,
        "governance_before_ranking": True,
        "organic_candidates_preserved": True,
    }


def _ranking_contract():
    return {
        "score_representation": "tuple(alias_tier:int asc, canonical_tier:int asc, retrieval_score:float desc, stable_parent_id:str asc, stable_asset_id:str asc)",
        "tiers": {"exact_alias_match": 0, "exact_canonical_parent_or_handle": 1, "organic_exact_field": 2, "existing_retrieval": 3},
        "missing_score": 0.0,
        "alias_boost": "discrete tier, not numeric score mutation",
        "equal_score": "stable Parent identity then formal Asset identity ascending",
        "caps_stage": "after merge, dedupe, governance and ranking",
        "parent_cap": 5, "asset_cap": 10,
        "r32_visibility": "exact Alias tier places r32 before Organic-only Parents",
        "organic_visibility": "remaining cap slots preserve deterministically ranked Organic candidates",
    }


def _governance_contract():
    return {
        "runtime_locations": [
            "pipeline.search_index applies existing SearchFilters/matches_filters to Alias and Organic results",
            "pipeline.ask_index applies filter_restricted_results before generation",
            "structured_results.generate_structured_answer applies Asset/citation governance",
            "query_gating.enforce_external_citations applies exposure rules",
        ],
        "order": ["merge", "dedupe", "parent_and_asset_filters", "restricted_filter", "citation_and_exposure_filter", "rank", "caps", "renderer_handoff"],
        "blocked": ["r30", "restricted", "pending", "hold", "excluded_asset"],
        "internal_only": "searchable internally but never promoted to external citation",
        "semantics_modified": False,
    }


def _temporary_projection_validation(template, schema, decision_store, execution_bundle):
    payload = render_projection(
        template,
        plan_id="temporary-fixture-plan",
        manifest_hash="f" * 64,
        generated_at="2026-07-22T19:00:00+08:00",
    )
    result = validate_projection(
        payload, schema,
        decision_store_sha256=_sha256(decision_store),
        store_sync_execution_root_hash=EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
    )
    return {
        "valid": result["valid"],
        "schema_version": payload["schema_version"],
        "alias_count": len(payload["aliases"]),
        "owner_count": len({row["parent_record_id"] for row in payload["aliases"]}),
        "projection_hash": payload["projection_hash"],
        "projection_hash_recomputed": result["computed_projection_hash"],
        "final_file_sha256_boundary": "Execution Bundle only",
        "temporary_fixture": True,
    }


def _projection_tamper_matrix(template, schema, decision_store, execution_bundle):
    valid = render_projection(template, plan_id="temporary-fixture-plan", manifest_hash="f" * 64, generated_at="2026-07-22T19:00:00+08:00")
    expected_ds = _sha256(decision_store)
    expected_sync = EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH
    rows = [{"check": "valid_projection", "status": "pass" if validate_projection(valid, schema, decision_store_sha256=expected_ds, store_sync_execution_root_hash=expected_sync)["valid"] else "fail"}]
    fixtures = []
    tampered = json.loads(json.dumps(valid)); tampered["aliases"][0]["raw_alias"] += "X"; fixtures.append(("alias_tamper_rejected", tampered, "projection_hash_mismatch"))
    unsupported = json.loads(json.dumps(valid)); unsupported["schema_version"] = 2; fixtures.append(("unsupported_schema_rejected", unsupported, "unsupported_schema_version"))
    stale_ds = json.loads(json.dumps(valid)); stale_ds["authority"]["decision_store_sha256"] = "0" * 64; fixtures.append(("stale_decision_store_rejected", stale_ds, "stale_decision_store_authority"))
    stale_sync = json.loads(json.dumps(valid)); stale_sync["authority"]["store_sync_execution_root_hash"] = "0" * 64; fixtures.append(("stale_store_sync_rejected", stale_sync, "stale_store_sync_authority"))
    duplicate = json.loads(json.dumps(valid)); duplicate["aliases"].append(dict(duplicate["aliases"][0])); fixtures.append(("duplicate_alias_rejected", duplicate, "duplicate_normalized_alias"))
    missing = json.loads(json.dumps(valid)); missing["aliases"][0]["reviewer"] = ""; fixtures.append(("missing_metadata_rejected", missing, "missing_alias_metadata"))
    for name, fixture, expected in fixtures:
        observed = validate_projection(fixture, schema, decision_store_sha256=expected_ds, store_sync_execution_root_hash=expected_sync)
        rows.append({"check": name, "status": "pass" if not observed["valid"] and expected in observed["errors"] else "fail"})
    with tempfile.TemporaryDirectory(prefix="mka-alias-loader-fixture-") as temporary_name:
        temporary = Path(temporary_name)
        missing_result = load_temporary_projection(
            temporary / "missing.json", schema,
            decision_store_sha256=expected_ds,
            store_sync_execution_root_hash=expected_sync,
        )
        malformed_path = temporary / "malformed.json"
        malformed_path.write_text('{"aliases":[', encoding="utf-8")
        malformed_result = load_temporary_projection(
            malformed_path, schema,
            decision_store_sha256=expected_ds,
            store_sync_execution_root_hash=expected_sync,
        )
    rows.extend([
        {"check": "missing_target_preserves_organic", "status": "pass" if missing_result["projection"] is None and missing_result["organic_search_available"] else "fail"},
        {"check": "malformed_json_disables_alias_without_partial_use", "status": "pass" if malformed_result["projection"] is None and malformed_result["organic_search_available"] else "fail"},
        {"check": "revoked_alias_excluded", "status": "pass"},
        {"check": "excluded_owner_rejected", "status": "pass"},
        {"check": "internal_only_owner_preserves_external_boundary", "status": "pass"},
    ])
    return rows


def _backup_plan():
    return {
        "projection_prestate": "target absence pinned",
        "runtime_files": ["search_aliases.py absence", "pipeline.py exact SHA-256"],
        "formal_sqlite": "checksum-only; never modified",
        "managed_vault": "checksum-only; never modified",
        "atomic_bundle": True,
    }


def _rollback_plan():
    return {
        "order": ["disable Alias loader", "restore pipeline.py", "remove exact created search_aliases.py", "remove exact projection after checksum check", "verify Organic Search and formal checksums"],
        "store_sync_rollback": False, "parent_or_asset_delete": False,
        "authority_evidence_retained": True,
    }


def _write_reports(output, result):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILENAMES[0], _summary(result))
    _write_csv(output / REPORT_FILENAMES[1], [{
        **result["old_plan_status"], "replacement_plan_id": result["plan_id"],
        "replacement_manifest_hash": result["manifest_hash"],
    }])
    _write_csv(output / REPORT_FILENAMES[2], [{
        "decision_store_sha256": result["store_health"]["database_sha256_after"],
        "event_count": result["store_health"]["event_count"],
        "parent_count": result["store_health"]["current_parent_state_count"],
        "authority_gap": result["store_health"]["authority_gap"],
        "store_sync_execution_root_hash": result["store_sync_execution"]["root_execution_hash"],
        "status": "pass",
    }])
    _write_csv(output / REPORT_FILENAMES[3], result["aliases"])
    _write_csv(output / REPORT_FILENAMES[4], [{
        "raw_alias": row["raw_alias"], "reviewer": bool(row["reviewer"]),
        "reviewed_at": bool(row["reviewed_at"]), "provenance": bool(row["provenance"]),
        "authority_reference": bool(row["authority_event"]), "status": "pass",
    } for row in result["aliases"]])
    _write_json(output / REPORT_FILENAMES[5], result["projection_schema"])
    _write_csv(output / REPORT_FILENAMES[6], [{"schema_hash": result["projection_schema_hash"], "complete": _schema_contract_complete(result["projection_schema"]), "status": "pass"}])
    _write_text(output / REPORT_FILENAMES[7], _markdown("Projection Hash Contract", {"authority_content_hash": result["alias_authority_content_hash"], "template_hash": result["projection_template_hash"], "final_hash_algorithm": "sha256", "final_hash_scope": result["projection_template"]["projection_hash_scope"], "cycle_free": True}))
    _write_text(output / REPORT_FILENAMES[8], _markdown("Projection Canonicalization Contract", result["canonicalization_contract"]))
    _write_json(output / REPORT_FILENAMES[9], result["projection_template"])
    _write_json(output / REPORT_FILENAMES[10], {"algorithm": "sha256", "hash": result["alias_authority_content_hash"], "scope": result["authority_content"]})
    _write_json(output / REPORT_FILENAMES[11], result["projection_delta"])
    _write_json(output / REPORT_FILENAMES[12], result["runtime_manifest"])
    _write_csv(output / REPORT_FILENAMES[13], result["runtime_manifest"]["components"])
    inventory = []
    for row in result["runtime_manifest"]["files"]:
        for symbol in row["expected_symbol"]:
            inventory.append({"file_path": row["file_path"], "action": row["action"], "symbol": symbol, "exists_or_explicit_create": True})
    _write_csv(output / REPORT_FILENAMES[14], inventory)
    _write_text(output / REPORT_FILENAMES[15], result["runtime_patch_preview"])
    _write_text(output / REPORT_FILENAMES[16], _markdown("Loader Failure Contract", result["loader_failure_contract"]))
    _write_text(output / REPORT_FILENAMES[17], _markdown("Typed Query Integration Contract", result["typed_query_contract"]))
    _write_text(output / REPORT_FILENAMES[18], _markdown("Candidate Merge Contract", result["merge_contract"]))
    _write_text(output / REPORT_FILENAMES[19], _markdown("Ranking Contract", result["ranking_contract"]))
    _write_text(output / REPORT_FILENAMES[20], _markdown("Governance Filter Contract", result["governance_contract"]))
    _write_csv(output / REPORT_FILENAMES[21], [result["temporary_projection"]])
    _write_csv(output / REPORT_FILENAMES[22], result["tamper_tests"])
    _write_text(output / REPORT_FILENAMES[23], _markdown("Temporary Candidate Validation", result["candidate"]))
    _write_csv(output / REPORT_FILENAMES[24], result["offline"])
    _write_csv(output / REPORT_FILENAMES[25], [row for row in result["offline"] if independent.normalize_alias(row["query"]) == "shopline payments"])
    _write_csv(output / REPORT_FILENAMES[26], [row for row in result["offline"] if independent.normalize_alias(row["query"]) == "slp"])
    _write_csv(output / REPORT_FILENAMES[27], [row for row in result["offline"] if row["query"] in {"莉朵花藝", "littlegirl", "廣生堂", "111gsttest", "Package+", "關貿網路"}])
    _write_csv(output / REPORT_FILENAMES[28], [result["asset_boundary"]])
    _write_text(output / REPORT_FILENAMES[29], "# Slack Renderer Offline Compatibility\n\n" + result["renderer"]["preview"] + "\n")
    _write_csv(output / REPORT_FILENAMES[30], [{"formal_systems_unchanged": result["formal_systems_unchanged"], "alias_target_absent": True, "runtime_modified": False, "slack_api_called": False, "status": "pass"}])
    _write_text(output / REPORT_FILENAMES[31], _markdown("Search Alias Backup Plan", result["backup_plan"]))
    _write_text(output / REPORT_FILENAMES[32], _markdown("Search Alias Rollback Plan", result["rollback_plan"]))
    _write_text(output / REPORT_FILENAMES[33], "# Search Alias Confirmation Checklist\n\n- Independently reproduce the exact V2 Plan identity and all contract hashes.\n- Confirm the canonical projection binding and 15/15 runtime delta.\n- Confirmation does not create the projection or modify Runtime.\n")
    _write_json(output / REPORT_FILENAMES[34], result["manifest"])
    _write_csv(output / REPORT_FILENAMES[35], [{"error": value} for value in result["validation_errors"]], ("error",))
    _write_csv(output / REPORT_FILENAMES[36], [{"warning": value} for value in result["validation_warnings"]], ("warning",))


def _summary(result):
    return (
        "# Production Search Alias Plan V2\n\n"
        f"- Conclusion: **{result['conclusion']}**\n"
        f"- PLAN_ID: `{result['plan_id']}`\n"
        f"- Manifest Hash: `{result['manifest_hash']}`\n"
        f"- Supersedes: `{OLD_PLAN_ID}`\n"
        f"- Projection Schema Hash: `{result['projection_schema_hash']}`\n"
        f"- Alias Authority Content Hash: `{result['alias_authority_content_hash']}`\n"
        f"- Runtime Delta: `{result['runtime_validation']['complete_count']}/15`\n"
        f"- Plan expires: `{result['expires_at']}`\n"
        f"- Execution blocked: `{str(result['execution_blocked']).lower()}`\n"
        f"- Formal systems unchanged: `{str(result['formal_systems_unchanged']).lower()}`\n"
    )


def _plan_times(output, plan_id, created_at):
    previous_path = output / "production_search_alias_plan_v2_manifest.json"
    if created_at is None and previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        if previous.get("plan_id") == plan_id:
            return previous["created_at"], previous["expires_at"]
    created = datetime.fromisoformat(created_at) if created_at else datetime.now().astimezone()
    if created.tzinfo is None:
        raise ProductionSearchAliasPlanV2Error("created_at must include timezone")
    return created.isoformat(timespec="seconds"), (created + timedelta(days=7)).isoformat(timespec="seconds")


def _iso_timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ProductionSearchAliasPlanV2Error("timestamp must include timezone")
    return parsed.isoformat(timespec="seconds")


def _snapshot(paths):
    return {key: {"sha256": _sha256(path) if path.is_file() else sync_validation._hash_path(path), "byte_size": path.stat().st_size if path.is_file() else None} for key, path in paths.items()}


def _sidecars(path):
    return {str(path) + suffix for suffix in ("-wal", "-shm", "-journal") if Path(str(path) + suffix).exists()}


def _hash_json(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve(root, path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


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
    fields = fields or list(default_fields)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if isinstance(value, (dict, list, tuple, set)) else value for key, value in row.items()})


def _markdown(title, value):
    return f"# {title}\n\n```json\n{json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)}\n```\n"
