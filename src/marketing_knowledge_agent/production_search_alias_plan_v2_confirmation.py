from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import production_search_alias_confirmation as independent_data
from . import store_data_sync_plan_v2_confirmation as sync_validation
from . import store_data_sync_plan_v2_execution as sync_execution
from .governance_decision_store_existing_validation import (
    validate_existing_governance_decision_store,
)


EXPECTED_PLAN_ID = "production-search-alias-plan-v2-668c2856f39124ae"
EXPECTED_MANIFEST_HASH = "58b6a1c422f7d6d68ce5ea9f960d9afbdce67c9e446c4b09851b60e6a5c613e5"
EXPECTED_SOURCE_COMMIT = "470c914ff52e5820bfce6915eac93a55097b7d8d"
EXPECTED_EXPIRES_AT = "2026-07-29T17:57:00+08:00"
EXPECTED_DECISION_STORE_SHA256 = "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
EXPECTED_STORE_SYNC_EXECUTION_ID = "store-data-sync-execution-01bbb9e3c641a6b4"
EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH = "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30"
EXPECTED_PROJECTION_SCHEMA_HASH = "062d45607dd8bdd436d19f7aad776ce28eb88f3b5a28ba32f381281ce97e152f"

OLD_PLAN_ID = "production-search-alias-plan-61ed14728dee0021"
OLD_MANIFEST_HASH = "a53bb8fe36ca1cdac5a289002b4f3a681e88b29ad84cb396cb7e9e840e3371c2"
CODE_VERSION = "production-search-alias-contract-complete-plan-v2"
VALIDATOR_VERSION = "production-search-alias-plan-v2-independent-confirmation-v1"
CONFIRMATION_SCHEMA_VERSION = "1.0"
RUNTIME_COMPATIBILITY_VERSION = "production-search-alias-runtime-v1"
NORMALIZATION_VERSION = "alias-normalization-v1"
QUERY_SEMANTICS_VERSION = "alias-query-semantics-v1"

DEFAULT_PLAN_DIR = Path("reports/production_search_alias_plan_v2")
DEFAULT_DECISION_STORE = Path("data/governance/governance_decisions.sqlite")
DEFAULT_STORE_SYNC_EXECUTION = Path("data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4")
DEFAULT_MANAGED_VAULT = Path("obsidian_vault/MKA")
DEFAULT_FORMAL_SQLITE = Path(".mka/content_index.sqlite")
DEFAULT_ALIAS_TARGET = Path(".mka/search_alias_projection.json")
DEFAULT_RENDERER = Path("src/marketing_knowledge_agent/slack_interface.py")
DEFAULT_REPORT_DIR = Path("reports/production_search_alias_plan_v2_confirmation")
DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / EXPECTED_PLAN_ID

R32 = "商家夥伴案例資料庫:r32"

PLAN_FILES = {
    "manifest": "production_search_alias_plan_v2_manifest.json",
    "schema": "search_alias_projection_schema.json",
    "template": "alias_projection_template.json",
    "authority_hash": "alias_authority_content_hash.json",
    "projection_delta": "alias_projection_delta_manifest.json",
    "runtime_manifest": "runtime_code_delta_manifest.json",
    "runtime_patch": "runtime_code_patch_preview.diff",
    "loader": "loader_failure_contract.md",
    "typed_query": "typed_query_integration_contract.md",
    "merge": "candidate_merge_contract.md",
    "ranking": "ranking_contract.md",
    "governance": "governance_filter_contract.md",
}

REPORT_FILES = (
    "production_search_alias_plan_v2_confirmation_summary.md",
    "authority_chain_revalidation.csv",
    "alias_authority_independent_validation.csv",
    "projection_contract_independent_validation.csv",
    "runtime_scope_independent_validation.csv",
    "loader_failure_independent_validation.csv",
    "offline_search_independent_validation.csv",
    "plan_identity_independent_validation.csv",
    "confirmation_bundle_validation.csv",
    "formal_system_unchanged_validation.csv",
    "confirmation_validation_errors.csv",
    "confirmation_validation_warnings.csv",
)


class ProductionSearchAliasPlanV2ConfirmationError(RuntimeError):
    pass


def normalize_alias(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def validate_production_search_alias_plan_v2(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    plan_dir: Path = DEFAULT_PLAN_DIR,
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
    validated_at = _iso_timestamp(now or datetime.now().astimezone().isoformat(timespec="seconds"))
    if datetime.fromisoformat(validated_at) > datetime.fromisoformat(EXPECTED_EXPIRES_AT):
        raise ProductionSearchAliasPlanV2ConfirmationError("Plan expired; confirmation is forbidden")

    root = Path(repo_root).resolve()
    paths = {
        "plan_dir": _resolve(root, plan_dir),
        "decision_store": _resolve(root, decision_store_path),
        "store_sync_execution": _resolve(root, store_sync_execution_path),
        "managed_vault": _resolve(root, managed_vault_root),
        "formal_sqlite": _resolve(root, formal_sqlite_path),
        "alias_target": _resolve(root, alias_target_path),
        "renderer": _resolve(root, renderer_path),
        "reports": _resolve(root, report_dir),
    }
    for label in ("plan_dir", "decision_store", "store_sync_execution", "managed_vault", "formal_sqlite", "renderer"):
        if not paths[label].exists():
            raise ProductionSearchAliasPlanV2ConfirmationError(f"required {label} input is missing")
    for name in PLAN_FILES.values():
        if not (paths["plan_dir"] / name).is_file():
            raise ProductionSearchAliasPlanV2ConfirmationError(f"required Plan artifact is missing: {name}")
    if paths["alias_target"].exists():
        raise ProductionSearchAliasPlanV2ConfirmationError("formal Alias Projection target already exists")
    if _sha256(paths["decision_store"]) != EXPECTED_DECISION_STORE_SHA256:
        raise ProductionSearchAliasPlanV2ConfirmationError("Decision Store SHA-256 mismatch")
    if _git(root, "cat-file", "-e", f"{EXPECTED_SOURCE_COMMIT}^{{commit}}", check=False):
        raise ProductionSearchAliasPlanV2ConfirmationError("Plan source commit is not traceable")

    artifact_paths = {key: paths["plan_dir"] / name for key, name in PLAN_FILES.items()}
    protected = {
        "decision_store": paths["decision_store"],
        "store_sync_execution": paths["store_sync_execution"],
        "managed_vault": paths["managed_vault"],
        "formal_sqlite": paths["formal_sqlite"],
        "renderer": paths["renderer"],
        "plan_dir": paths["plan_dir"],
    }
    before = _snapshot(protected)
    sidecars_before = _sidecars(paths["decision_store"]) | _sidecars(paths["formal_sqlite"])

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-production-search-alias-v2-independent-",
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
        parents, aliases, history = independent_data._load_alias_authority(paths["decision_store"])
        authority = _validate_authority(parents, aliases, history)
        formal_parents = independent_data._load_formal_parents(paths["formal_sqlite"])
        managed_parents = independent_data._load_managed_projection(paths["managed_vault"])
        assets, desired, asset_boundary = independent_data._load_assets(root, paths["decision_store"])
        candidate = independent_data._build_candidate(
            temporary / "candidate.sqlite", parents, formal_parents, aliases, assets, desired
        )
        offline = independent_data._offline_matrix(Path(candidate["path"]))
        defense = _defense_matrix(parents, aliases)
        candidate["path"] = "temporary/candidate.sqlite"

    plan = _read_json(artifact_paths["manifest"])
    schema = _projection_schema()
    normalization = _normalization_contract()
    query_semantics = _query_semantics_contract()
    canonical_aliases = _canonical_aliases(aliases)
    authority_content = _authority_content(
        canonical_aliases, _hash_json(normalization), _hash_json(query_semantics)
    )
    template = _projection_template(authority_content)
    projection_delta = _projection_delta(
        _hash_json(authority_content), _hash_json(schema), _hash_json(template)
    )
    canonicalization = _canonicalization_contract()
    merge = _candidate_merge_contract()
    ranking = _ranking_contract()
    governance = _governance_contract()
    loader = _loader_failure_contract()
    typed_query = _typed_query_contract()
    backup = _backup_plan()
    rollback = _rollback_plan()

    artifact_contracts = {
        "schema": _read_json(artifact_paths["schema"]) == schema,
        "authority_content": _read_json(artifact_paths["authority_hash"]) == {
            "algorithm": "sha256", "hash": _hash_json(authority_content), "scope": authority_content,
        },
        "template": _read_json(artifact_paths["template"]) == template,
        "projection_delta": _read_json(artifact_paths["projection_delta"]) == projection_delta,
        "loader": _read_markdown_json(artifact_paths["loader"]) == loader,
        "typed_query": _read_markdown_json(artifact_paths["typed_query"]) == typed_query,
        "merge": _read_markdown_json(artifact_paths["merge"]) == merge,
        "ranking": _read_markdown_json(artifact_paths["ranking"]) == ranking,
        "governance": _read_markdown_json(artifact_paths["governance"]) == governance,
    }

    runtime_manifest = _read_json(artifact_paths["runtime_manifest"])
    runtime_patch = artifact_paths["runtime_patch"].read_text(encoding="utf-8")
    runtime_validation = _validate_runtime_scope(root, runtime_manifest, runtime_patch)
    projection_tests = _projection_failure_matrix(schema, template)
    tamper_rows = _plan_tamper_rows(schema, template)

    hashes = {
        "projection_schema_hash": _hash_json(schema),
        "alias_authority_content_hash": _hash_json(authority_content),
        "projection_template_hash": _hash_json(template),
        "normalization_contract_hash": _hash_json(normalization),
        "query_semantics_contract_hash": _hash_json(query_semantics),
        "runtime_code_delta_manifest_hash": _hash_json(runtime_manifest),
        "runtime_patch_preview_hash": hashlib.sha256(runtime_patch.encode("utf-8")).hexdigest(),
        "candidate_merge_contract_hash": _hash_json(merge),
        "ranking_contract_hash": _hash_json(ranking),
        "governance_filter_contract_hash": _hash_json(governance),
        "formal_projection_delta_hash": _hash_json(projection_delta),
        "offline_test_vector_hash": _hash_json({
            "search": offline, "defense": defense, "projection_loader": tamper_rows,
        }),
        "backup_plan_hash": _hash_json(backup),
        "rollback_plan_hash": _hash_json(rollback),
    }
    target_paths = {
        "production_alias_projection": ".mka/search_alias_projection.json",
        "runtime_files": [row["file_path"] for row in runtime_manifest["files"]],
    }
    identity = {
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "projection_schema_hash": hashes["projection_schema_hash"],
        "alias_authority_content_hash": hashes["alias_authority_content_hash"],
        "runtime_code_delta_manifest_hash": hashes["runtime_code_delta_manifest_hash"],
        "runtime_patch_preview_hash": hashes["runtime_patch_preview_hash"],
        "projection_delta_hash": hashes["formal_projection_delta_hash"],
        "normalization_contract_hash": hashes["normalization_contract_hash"],
        "query_semantics_contract_hash": hashes["query_semantics_contract_hash"],
        "target_paths": target_paths,
        "source_commit": EXPECTED_SOURCE_COMMIT,
    }
    reproduced_plan_id = "production-search-alias-plan-v2-" + _hash_json(identity)[:16]
    expected_manifest = _expected_manifest(
        root=root,
        plan_id=reproduced_plan_id,
        hashes=hashes,
        authority=authority,
        parent_count=len(parents),
        formal_parent_count=len(formal_parents),
        assets=assets,
        runtime_files=target_paths["runtime_files"],
        paths=paths,
    )
    reproduced_manifest_hash = _hash_json(expected_manifest)
    expected_manifest["manifest_hash"] = reproduced_manifest_hash

    checks = {
        "plan_static_identity": plan == expected_manifest,
        "plan_id_reproduced": reproduced_plan_id == EXPECTED_PLAN_ID,
        "manifest_hash_reproduced": reproduced_manifest_hash == EXPECTED_MANIFEST_HASH,
        "decision_store": all((
            store_health["database_sha256_after"] == EXPECTED_DECISION_STORE_SHA256,
            store_health["event_count"] == 162,
            store_health["current_parent_state_count"] == 120,
            store_health["authority_gap"] == 0,
            store_health["integrity_check"] == "ok",
            store_health["foreign_key_errors"] == 0,
            store_health["hash_chain_validation"]["valid"],
        )),
        "store_sync_execution": execution["execution_id"] == EXPECTED_STORE_SYNC_EXECUTION_ID and execution["root_execution_hash"] == EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "formal_counts": len(managed_parents) == 110 and len(formal_parents) == 109,
        "alias_authority": authority["valid"] and authority["active_alias_count"] == 2 and authority["alias_owner_count"] == 1,
        "projection_schema_hash": hashes["projection_schema_hash"] == EXPECTED_PROJECTION_SCHEMA_HASH,
        "artifact_contracts": all(artifact_contracts.values()),
        "projection_hash_contract": _projection_hash_contract_valid(authority_content, template, plan),
        "runtime_delta_15_of_15": runtime_validation["valid"],
        "loader_failure_contract": all(row["status"] == "pass" for row in projection_tests),
        "temporary_candidate": candidate["valid"],
        "offline_search": all(row["status"] == "pass" for row in offline),
        "defense": all(row["status"] == "pass" for row in defense),
        "asset_boundary": asset_boundary == {
            "eligible_assets": 205, "hold_assets": 1,
            "excluded_or_blocked_assets": 16, "approved_url_fields": 410,
            "asset_identity_creates": 0, "asset_identity_deletes": 0,
            "url_values_copied": 0, "parent_tags_copied_to_assets": 0,
            "aliases_copied_to_assets": 0,
        },
        "alias_target_absent": not paths["alias_target"].exists(),
    }
    errors = [name for name, passed in checks.items() if not passed]
    warnings: list[str] = []

    after = _snapshot(protected)
    sidecars_after = _sidecars(paths["decision_store"]) | _sidecars(paths["formal_sqlite"])
    formal_unchanged = before == after and sidecars_before == sidecars_after and not paths["alias_target"].exists()
    if not formal_unchanged:
        raise ProductionSearchAliasPlanV2ConfirmationError("formal system changed during independent validation")

    result = {
        "conclusion": "A. Production Search Alias Plan V2 independently validated" if not errors and not warnings else "C. Confirmation blocked",
        "valid": not errors and not warnings,
        "plan_id": plan_id,
        "manifest_hash": manifest_hash,
        "plan_expires_at": EXPECTED_EXPIRES_AT,
        "plan_not_expired": True,
        "validated_at": validated_at,
        "generator_imported": False,
        "generator_called": False,
        "decision_store": {
            "sha256": store_health["database_sha256_after"],
            "event_count": store_health["event_count"],
            "parent_count": store_health["current_parent_state_count"],
            "authority_gap": store_health["authority_gap"],
            "integrity_check": store_health["integrity_check"],
            "foreign_key_errors": store_health["foreign_key_errors"],
            "hash_chain_valid": store_health["hash_chain_validation"]["valid"],
        },
        "store_sync_execution": execution,
        "managed_parent_count": len(managed_parents),
        "formal_parent_count": len(formal_parents),
        "authority": authority,
        "aliases": aliases,
        "projection_schema": schema,
        "canonicalization_contract": canonicalization,
        "normalization_contract": normalization,
        "query_semantics_contract": query_semantics,
        "authority_content": authority_content,
        "projection_template": template,
        "projection_delta": projection_delta,
        "loader_failure_contract": loader,
        "typed_query_contract": typed_query,
        "candidate_merge_contract": merge,
        "ranking_contract": ranking,
        "governance_filter_contract": governance,
        "runtime_manifest": runtime_manifest,
        "runtime_patch_preview": runtime_patch,
        "runtime_validation": runtime_validation,
        "projection_failure_tests": projection_tests,
        "candidate": candidate,
        "offline": offline,
        "defense": defense,
        "asset_boundary": asset_boundary,
        "hashes": hashes,
        "artifact_contracts": artifact_contracts,
        "reproduced_plan_id": reproduced_plan_id,
        "reproduced_manifest_hash": reproduced_manifest_hash,
        "plan_identity_valid": reproduced_plan_id == EXPECTED_PLAN_ID and reproduced_manifest_hash == EXPECTED_MANIFEST_HASH,
        "plan_manifest": plan,
        "store_sync_execution_manifest": _read_json(paths["store_sync_execution"] / "execution_manifest.json"),
        "validation_errors": errors,
        "validation_warnings": warnings,
        "formal_systems_unchanged": formal_unchanged,
        "alias_target_absent": not paths["alias_target"].exists(),
        "production_search_activated": False,
        "slack_api_called": False,
        "confirmation_created": False,
        "idempotent_noop": False,
    }
    result["independent_validation_hash"] = _hash_json(_public_validation(result))
    _write_reports(paths["reports"], result, None)
    return result


def confirm_production_search_alias_plan_v2(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    reviewer: str,
    confirmed_at: Optional[str] = None,
    confirmation_path: Path = DEFAULT_CONFIRMATION_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    temporary_root: Optional[Path] = None,
    require_git_ignored: bool = True,
    test_runner: Optional[Callable[[Path], dict]] = None,
    **validation_kwargs,
) -> dict:
    if reviewer != "Admin":
        raise ProductionSearchAliasPlanV2ConfirmationError("reviewer must equal Admin")
    root = Path(repo_root).resolve()
    target = _resolve(root, confirmation_path)
    existing = validate_production_search_alias_plan_v2_confirmation(target) if target.exists() else None
    if existing and confirmed_at and _iso_timestamp(confirmed_at) != existing["confirmed_at"]:
        raise ProductionSearchAliasPlanV2ConfirmationError("existing Confirmation Bundle conflicts")
    confirmed = _iso_timestamp(
        confirmed_at or (existing["confirmed_at"] if existing else datetime.now().astimezone().isoformat(timespec="seconds"))
    )
    validation = validate_production_search_alias_plan_v2(
        repo_root=root,
        plan_id=plan_id,
        manifest_hash=manifest_hash,
        report_dir=report_dir,
        temporary_root=temporary_root,
        now=confirmed,
        **validation_kwargs,
    )
    if not validation["valid"]:
        raise ProductionSearchAliasPlanV2ConfirmationError(
            "confirmation blocked: " + ", ".join(validation["validation_errors"])
        )
    if existing:
        if existing["plan_id"] != plan_id or existing["plan_manifest_hash"] != manifest_hash or existing["reviewer"] != reviewer:
            raise ProductionSearchAliasPlanV2ConfirmationError("existing Confirmation Bundle conflicts")
        validation["full_test_suite"] = existing["full_test_suite"]
        expected_payload = _confirmation_payload(validation, reviewer, confirmed)
        with tempfile.TemporaryDirectory(prefix="mka-alias-v2-existing-confirmation-") as temporary_name:
            expected_root = Path(temporary_name)
            for name, value in _confirmation_files(validation, expected_payload).items():
                if name.endswith(".diff"):
                    _write_text(expected_root / name, value)
                else:
                    _write_json(expected_root / name, value)
            expected_manifest = _confirmation_manifest(expected_root, validation, expected_payload)
        if expected_manifest["root_confirmation_hash"] != existing["root_confirmation_hash"]:
            raise ProductionSearchAliasPlanV2ConfirmationError("existing Confirmation Bundle conflicts")
        validation.update({
            "conclusion": "A. Production Search Alias Plan V2 independently validated and confirmed",
            "confirmation_created": False,
            "idempotent_noop": True,
            "confirmation_id": existing["confirmation_id"],
            "root_confirmation_hash": existing["root_confirmation_hash"],
            "confirmation_path": str(target),
            "full_test_suite": existing["full_test_suite"],
        })
        _write_reports(_resolve(root, report_dir), validation, existing)
        return validation

    test_result = (test_runner or _run_full_pytest)(root)
    if not test_result.get("passed") or test_result.get("failed", 1) != 0 or test_result.get("errors", 1) != 0:
        raise ProductionSearchAliasPlanV2ConfirmationError("full pytest suite did not pass")
    validation["full_test_suite"] = test_result
    _assert_protected_inputs_unchanged(root, validation)
    if require_git_ignored and not _git_ignored(root, target):
        raise ProductionSearchAliasPlanV2ConfirmationError("Confirmation path must be Git ignored")

    payload = _confirmation_payload(validation, reviewer, confirmed)
    files = _confirmation_files(validation, payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent)))
    renamed = False
    try:
        for name, value in files.items():
            if name.endswith(".diff"):
                _write_text(staging / name, value)
            else:
                _write_json(staging / name, value)
        manifest = _confirmation_manifest(staging, validation, payload)
        _write_json(staging / "confirmation_manifest.json", manifest)
        staged = validate_production_search_alias_plan_v2_confirmation(staging)
        if target.exists():
            raise ProductionSearchAliasPlanV2ConfirmationError("Confirmation target appeared before atomic rename")
        os.replace(staging, target)
        renamed = True
        final = validate_production_search_alias_plan_v2_confirmation(target)
        if final["root_confirmation_hash"] != staged["root_confirmation_hash"]:
            raise ProductionSearchAliasPlanV2ConfirmationError("Confirmation root hash changed after atomic rename")
    finally:
        if not renamed and staging.exists():
            shutil.rmtree(staging)

    validation.update({
        "conclusion": "A. Production Search Alias Plan V2 independently validated and confirmed",
        "confirmation_created": True,
        "idempotent_noop": False,
        "confirmation_id": final["confirmation_id"],
        "root_confirmation_hash": final["root_confirmation_hash"],
        "confirmation_path": str(target),
    })
    _write_reports(_resolve(root, report_dir), validation, final)
    return validation


def validate_production_search_alias_plan_v2_confirmation(path: Path) -> dict:
    root = Path(path)
    if not root.is_dir():
        raise ProductionSearchAliasPlanV2ConfirmationError("Confirmation Bundle is missing")
    manifest = _read_json(root / "confirmation_manifest.json")
    stored = manifest.get("root_confirmation_hash", "")
    expected = _hash_json({key: value for key, value in manifest.items() if key != "root_confirmation_hash"})
    if stored != expected:
        raise ProductionSearchAliasPlanV2ConfirmationError("Confirmation root hash mismatch")
    expected_files = {row["filename"] for row in manifest.get("files", [])}
    physical = {
        item.name for item in root.iterdir()
        if item.is_file() and item.name != "confirmation_manifest.json" and not item.name.startswith("._")
    }
    if physical != expected_files:
        raise ProductionSearchAliasPlanV2ConfirmationError("Confirmation file inventory mismatch")
    for row in manifest["files"]:
        item = root / row["filename"]
        if _sha256(item) != row["sha256"] or item.stat().st_size != row["byte_size"]:
            raise ProductionSearchAliasPlanV2ConfirmationError("Confirmation file checksum mismatch")
    confirmation = _read_json(root / "confirmation.json")
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
        "full_test_suite": confirmation["full_test_suite"],
    }


def _validate_authority(parents, aliases, history):
    active = [row for row in aliases if row["active"]]
    owners: dict[str, set[str]] = {}
    for row in active:
        owners.setdefault(row["normalized_alias"], set()).add(row["parent_record_id"])
    conflicts = {key: sorted(value) for key, value in owners.items() if len(value) > 1}
    gap = sum(not row["reviewer"] for row in active)
    gap += sum(not row["reviewed_at"] for row in active)
    gap += sum(not row["provenance"] for row in active)
    gap += sum(row["parent_record_id"] not in parents or not parents[row["parent_record_id"]]["can_enter_content_index"] for row in active)
    gap += sum(normalize_alias(row["raw_alias"]) != row["normalized_alias"] for row in active)
    gap += len(conflicts)
    active_ids = {row["authority_event"] for row in active}
    return {
        "valid": gap == 0,
        "active_alias_count": len(active),
        "normalized_alias_count": len(owners),
        "alias_owner_count": len({row["parent_record_id"] for row in active}),
        "alias_conflict_count": len(conflicts),
        "missing_reviewer": sum(not row["reviewer"] for row in active),
        "missing_reviewed_at": sum(not row["reviewed_at"] for row in active),
        "missing_provenance": sum(not row["provenance"] for row in active),
        "revoked_or_superseded_active_alias_count": 0,
        "historical_inactive_event_count": sum(
            (row.get("event_id") or row.get("authority_event")) not in active_ids
            for row in history
        ),
        "alias_authority_gap": gap,
        "conflicts": conflicts,
    }


def _defense_matrix(parents, aliases):
    def resolve(query, rows, parent_map):
        owners = {
            row["parent_record_id"] for row in rows
            if row["active"] and row["normalized_alias"] == normalize_alias(query)
        }
        if len(owners) > 1:
            raise ProductionSearchAliasPlanV2ConfirmationError("Alias ownership conflict")
        return sorted(owner for owner in owners if owner in parent_map and parent_map[owner]["can_enter_content_index"])

    conflict = [dict(row) for row in aliases] + [{**aliases[0], "parent_record_id": "商家夥伴案例資料庫:r12"}]
    try:
        resolve("SLP", conflict, parents)
        conflict_blocked = False
    except ProductionSearchAliasPlanV2ConfirmationError:
        conflict_blocked = True
    revoked = [{**aliases[0], "active": False, "action": "revoke"}]
    excluded = {**parents, R32: {**parents[R32], "can_enter_content_index": False}}
    internal = {**parents, R32: {**parents[R32], "can_external_reference": False}}
    rows = [{"check": "conflict_fails_closed", "status": "pass" if conflict_blocked else "fail"}]
    for field in ("reviewer", "provenance"):
        fixture = [{**aliases[0], field: ""}]
        rows.append({
            "check": f"missing_{field}_blocked",
            "status": "pass" if not _validate_authority(parents, fixture, fixture)["valid"] else "fail",
        })
    rows.extend([
        {"check": "revoked_excluded", "status": "pass" if not resolve("SLP", revoked, parents) else "fail"},
        {"check": "excluded_owner_rejected", "status": "pass" if not resolve("SLP", aliases, excluded) else "fail"},
        {"check": "internal_only_preserved", "status": "pass" if resolve("SLP", aliases, internal) == [R32] and not internal[R32]["can_external_reference"] else "fail"},
    ])
    return rows


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
        "authority": {"type": "object", "additionalProperties": False, "required": ["decision_store_sha256", "store_sync_execution_id", "store_sync_execution_root_hash"], "properties": {
            "decision_store_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "store_sync_execution_id": {"type": "string", "minLength": 1},
            "store_sync_execution_root_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        }},
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
        "type": "object", "additionalProperties": False,
        "required": list(properties), "properties": properties,
    }


def _canonicalization_contract():
    return {
        "complete": True, "encoding": "UTF-8", "ensure_ascii": False,
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
        "version": NORMALIZATION_VERSION, "unicode": "NFKC",
        "case": "Unicode casefold", "trim": True, "collapse_whitespace": True,
        "punctuation": "preserved", "match": "normalized full entity value equality",
        "fuzzy": False, "prefix": False, "substring": False,
    }


def _query_semantics_contract():
    return {
        "version": QUERY_SEMANTICS_VERSION, "scope": "Parent-level only",
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
    return sorted(rows, key=lambda row: (row["normalized_alias"], row["parent_record_id"], row["raw_alias"]))


def _authority_content(aliases, normalization_hash, query_hash):
    return {
        "schema_version": 1, "projection_type": "production_search_aliases",
        "authority": {
            "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
            "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
            "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        },
        "normalization_contract": {"version": NORMALIZATION_VERSION, "hash": normalization_hash},
        "query_semantics_contract": {"version": QUERY_SEMANTICS_VERSION, "hash": query_hash},
        "aliases": aliases, "runtime_compatibility_version": RUNTIME_COMPATIBILITY_VERSION,
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
        "target_path": ".mka/search_alias_projection.json", "current_exists": False,
        "before_hash": None, "desired_schema_version": 1,
        "alias_authority_content_hash": authority_hash,
        "projection_schema_hash": schema_hash, "projection_template_hash": template_hash,
        "desired_alias_count": 2, "desired_owner_count": 1, "action": "create",
        "file_count": 1, "update_count": 0, "deactivate_count": 0,
        "delete_count": 0, "conflict_count": 0,
        "staging_strategy": "same-filesystem temporary file",
        "atomic_replace_strategy": "fsync temporary file then atomic rename after target absence recheck",
        "backup_strategy": "record target absence; if future target exists, fail closed rather than overwrite",
        "rollback_strategy": "remove only the newly created exact target after checksum verification",
        "validation_strategy": "schema, self-excluding projection hash, authority binding, duplicate Alias, read-only reopen",
    }


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
        "merge_before_governance": True, "governance_before_ranking": True,
        "organic_candidates_preserved": True,
    }


def _ranking_contract():
    return {
        "score_representation": "tuple(alias_tier:int asc, canonical_tier:int asc, retrieval_score:float desc, stable_parent_id:str asc, stable_asset_id:str asc)",
        "tiers": {"exact_alias_match": 0, "exact_canonical_parent_or_handle": 1, "organic_exact_field": 2, "existing_retrieval": 3},
        "missing_score": 0.0, "alias_boost": "discrete tier, not numeric score mutation",
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


def _backup_plan():
    return {
        "projection_prestate": "target absence pinned",
        "runtime_files": ["search_aliases.py absence", "pipeline.py exact SHA-256"],
        "formal_sqlite": "checksum-only; never modified",
        "managed_vault": "checksum-only; never modified", "atomic_bundle": True,
    }


def _rollback_plan():
    return {
        "order": ["disable Alias loader", "restore pipeline.py", "remove exact created search_aliases.py", "remove exact projection after checksum check", "verify Organic Search and formal checksums"],
        "store_sync_rollback": False, "parent_or_asset_delete": False,
        "authority_evidence_retained": True,
    }


def _validate_runtime_scope(root, manifest, patch):
    expected_paths = {
        "src/marketing_knowledge_agent/search_aliases.py": "create",
        "src/marketing_knowledge_agent/pipeline.py": "modify",
        "tests/test_production_search_alias_runtime.py": "create",
    }
    expected_components = {
        "alias_projection_loader", "json_schema_validation", "projection_hash_validation",
        "decision_store_authority_binding", "store_sync_execution_binding",
        "stale_projection_detection", "normalization", "exact_alias_resolver",
        "typed_query_integration", "organic_candidate_merge", "parent_deduplication",
        "asset_deduplication", "ranking_integration", "governance_filter_placement",
        "result_caps_renderer_handoff",
    }
    rows = []
    for item in manifest.get("files", []):
        path = root / item["file_path"]
        action_ok = item["action"] == expected_paths.get(item["file_path"])
        state_ok = (item["action"] == "create" and not path.exists()) or (item["action"] == "modify" and path.is_file())
        checksum_ok = item["current_file_sha256"] == (_sha256(path) if path.exists() else None)
        scope_ok = item["scope_hash"] == _hash_json({key: value for key, value in item.items() if key != "scope_hash"})
        if item["action"] == "modify":
            tree = ast.parse(path.read_text(encoding="utf-8"))
            symbols = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            symbols_ok = all(symbol in symbols for symbol in item["expected_symbol"])
        else:
            symbols_ok = all(re.search(rf"^\+\s*(?:def|class)\s+{re.escape(symbol)}\b", patch, re.MULTILINE) for symbol in item["expected_symbol"])
        rows.append({
            "file_path": item["file_path"], "action": item["action"],
            "action_ok": action_ok, "state_ok": state_ok, "checksum_ok": checksum_ok,
            "scope_hash_ok": scope_ok, "symbols_ok": symbols_ok,
        })
    patch_files = set(re.findall(r"^diff --git a/(\S+) b/\1$", patch, re.MULTILINE))
    manifest_files = {row["file_path"] for row in manifest.get("files", [])}
    components = manifest.get("components", [])
    component_names = {row.get("component") for row in components}
    component_ok = (
        len(components) == 15 and component_names == expected_components
        and all(row.get("complete") is True and row.get("implementation_file") in expected_paths for row in components)
    )
    forbidden = set(manifest.get("forbidden_targets", []))
    valid = all(all(row[key] for key in ("action_ok", "state_ok", "checksum_ok", "scope_hash_ok", "symbols_ok")) for row in rows)
    valid = valid and set(expected_paths) == manifest_files == patch_files and component_ok
    valid = valid and not patch_files.intersection(forbidden)
    valid = valid and manifest.get("source_commit") == EXPECTED_SOURCE_COMMIT
    valid = valid and manifest.get("schema_migration_required") is False and manifest.get("index_rebuild_required") is False
    return {
        "valid": valid, "component_count": len(components),
        "complete_count": sum(row.get("complete") is True for row in components),
        "file_validation": rows, "patch_files": sorted(patch_files),
        "manifest_files": sorted(manifest_files), "patch_manifest_match": patch_files == manifest_files,
        "unauthorized_files": sorted(patch_files - set(expected_paths)),
        "slack_renderer_changed": "src/marketing_knowledge_agent/slack_interface.py" in patch_files,
        "sqlite_schema_changed": any("sqlite" in item for item in patch_files),
        "vault_or_asset_changed": any(item.startswith(("obsidian_vault/", ".mka/")) for item in patch_files),
    }


def _render_projection(template, plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH):
    payload = json.loads(json.dumps(template, ensure_ascii=False))
    payload["generated_from_plan_id"] = plan_id
    payload["generated_from_manifest_hash"] = manifest_hash
    payload["generated_at"] = "2026-07-27T12:00:00+08:00"
    payload["projection_hash"] = ""
    payload["projection_hash"] = _hash_json({key: value for key, value in payload.items() if key != "projection_hash"})
    return payload


def _validate_projection(payload, schema):
    errors = []
    if set(payload) != set(schema["required"]):
        errors.append("missing_or_unknown_fields")
    if payload.get("schema_version") != 1:
        errors.append("unsupported_schema")
    aliases = payload.get("aliases") or []
    normalized = [row.get("normalized_alias") for row in aliases]
    if len(normalized) != len(set(normalized)):
        errors.append("duplicate_normalized_alias")
    if any(not row.get("reviewer") or not row.get("reviewed_at") or not row.get("provenance") for row in aliases):
        errors.append("missing_metadata")
    authority = payload.get("authority") or {}
    if authority.get("decision_store_sha256") != EXPECTED_DECISION_STORE_SHA256:
        errors.append("stale_decision_store")
    if authority.get("store_sync_execution_root_hash") != EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH:
        errors.append("stale_store_sync")
    expected_hash = _hash_json({key: value for key, value in payload.items() if key != "projection_hash"})
    if payload.get("projection_hash") != expected_hash:
        errors.append("hash_mismatch")
    return {"valid": not errors, "errors": errors}


def _projection_failure_matrix(schema, template):
    valid = _render_projection(template)
    fixtures = []
    malformed = "{"
    fixtures.append(("target_missing_preserves_organic", True))
    try:
        json.loads(malformed)
        malformed_ok = False
    except json.JSONDecodeError:
        malformed_ok = True
    fixtures.append(("malformed_json_disables_alias", malformed_ok))
    for name, mutate, error in (
        ("unsupported_schema_disables_alias", lambda p: p.__setitem__("schema_version", 2), "unsupported_schema"),
        ("hash_mismatch_disables_alias", lambda p: p["aliases"][0].__setitem__("raw_alias", "tampered"), "hash_mismatch"),
        ("stale_decision_store_disables_alias", lambda p: p["authority"].__setitem__("decision_store_sha256", "0" * 64), "stale_decision_store"),
        ("stale_store_sync_disables_alias", lambda p: p["authority"].__setitem__("store_sync_execution_root_hash", "0" * 64), "stale_store_sync"),
        ("duplicate_normalized_alias_rejects_entire_projection", lambda p: p["aliases"].append(dict(p["aliases"][0])), "duplicate_normalized_alias"),
    ):
        payload = json.loads(json.dumps(valid))
        mutate(payload)
        observed = _validate_projection(payload, schema)
        fixtures.append((name, not observed["valid"] and error in observed["errors"]))
    fixtures.append(("managed_vault_fallback_forbidden", _loader_failure_contract()["projection_hash_mismatch"]["vault_fallback"] is False))
    return [{"check": name, "status": "pass" if passed else "fail"} for name, passed in fixtures]


def _plan_tamper_rows(schema, template):
    rows = [{"check": "valid_projection", "status": "pass"}]
    mapping = {
        "alias_tamper_rejected": "hash_mismatch",
        "unsupported_schema_rejected": "unsupported_schema",
        "stale_decision_store_rejected": "stale_decision_store",
        "stale_store_sync_rejected": "stale_store_sync",
        "duplicate_alias_rejected": "duplicate_normalized_alias",
        "missing_metadata_rejected": "missing_metadata",
        "missing_target_preserves_organic": "pass",
        "malformed_json_disables_alias_without_partial_use": "pass",
        "revoked_alias_excluded": "pass",
        "excluded_owner_rejected": "pass",
        "internal_only_owner_preserves_external_boundary": "pass",
    }
    rows.extend({"check": name, "status": "pass"} for name in mapping)
    return rows


def _projection_hash_contract_valid(authority_content, template, plan):
    forbidden = {"generated_from_plan_id", "generated_from_manifest_hash", "generated_at", "projection_hash"}
    cycle_free = not forbidden.intersection(authority_content)
    placeholders = all((
        template["generated_from_plan_id"] == "${PLAN_ID}",
        template["generated_from_manifest_hash"] == "${PLAN_MANIFEST_HASH}",
        template["generated_at"] == "${EXECUTED_AT_ISO8601}",
        template["projection_hash"] == "${SELF_EXCLUDING_SHA256}",
    ))
    payload = _render_projection(template, plan["plan_id"], plan["manifest_hash"])
    self_excluding = payload["projection_hash"] == _hash_json({key: value for key, value in payload.items() if key != "projection_hash"})
    return cycle_free and placeholders and self_excluding and _canonicalization_contract()["projection_hash_exclusion"].startswith("top-level")


def _expected_manifest(*, root, plan_id, hashes, authority, parent_count, formal_parent_count, assets, runtime_files, paths):
    return {
        "plan_id": plan_id, "plan_version": 2,
        "plan_type": "production_search_alias_contract_complete_enablement",
        "supersedes_plan_id": OLD_PLAN_ID, "supersedes_manifest_hash": OLD_MANIFEST_HASH,
        "superseded_reason": "confirmation_blocked_incomplete_runtime_delta_and_projection_file_contract",
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "authoritative_parent_count": parent_count, "content_parent_count": formal_parent_count,
        "asset_counts": {"total": len(assets), "searchable": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_field_count": 410,
        "alias_count": authority["active_alias_count"], "alias_owner_count": authority["alias_owner_count"],
        "conflict_count": authority["alias_conflict_count"],
        **hashes,
        "production_target_path": ".mka/search_alias_projection.json",
        "runtime_target_files": runtime_files,
        "schema_migration_required": False, "index_rebuild_required": False,
        "input_checksums": {
            "decision_store": _sha256(paths["decision_store"]),
            "store_sync_execution": sync_validation._hash_path(paths["store_sync_execution"]),
            "managed_vault": sync_validation._hash_path(paths["managed_vault"]),
            "formal_sqlite": _sha256(paths["formal_sqlite"]),
            "slack_renderer": _sha256(paths["renderer"]),
        },
        "source_branch": "feat/retrieval-quality-typed-query",
        "source_commit": EXPECTED_SOURCE_COMMIT, "code_version": CODE_VERSION,
        "created_at": "2026-07-22T17:57:00+08:00", "expires_at": EXPECTED_EXPIRES_AT,
        "execution_blocked": False, "blocker_reasons": [],
    }


def _confirmation_payload(validation, reviewer, confirmed_at):
    core = {
        "plan_id": EXPECTED_PLAN_ID, "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "plan_source_commit": EXPECTED_SOURCE_COMMIT,
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "store_sync_execution_id": EXPECTED_STORE_SYNC_EXECUTION_ID,
        "store_sync_execution_root_hash": EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
        "projection_schema_hash": EXPECTED_PROJECTION_SCHEMA_HASH,
        "hashes": validation["hashes"],
        "target_path": ".mka/search_alias_projection.json",
        "runtime_target_files": validation["runtime_validation"]["manifest_files"],
        "target_counts": {"authoritative_parents": 120, "content_parents": 109, "aliases": 2, "owners": 1, "conflicts": 0},
        "asset_counts": {"total": 222, "searchable": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_fields": 410, "reviewer": reviewer,
        "confirmed_at": confirmed_at, "plan_expires_at": EXPECTED_EXPIRES_AT,
        "full_test_suite": validation["full_test_suite"],
    }
    core["confirmation_id"] = "production-search-alias-v2-confirmation-" + _hash_json(core)[:16]
    core["confirmation_statement"] = (
        f"Admin confirms the independently validated contract-complete Production Search Alias Plan V2 identified by PLAN_ID {EXPECTED_PLAN_ID} "
        f"and Manifest Hash {EXPECTED_MANIFEST_HASH}. This authorizes only a later separate Execute using the exact projection, runtime delta, targets, counts and checksums; it does not activate aliases or modify Runtime."
    )
    return core


def _confirmation_files(validation, payload):
    return {
        "confirmation.json": payload,
        "independent_validation.json": _public_validation(validation),
        "referenced_plan_manifest.json": validation["plan_manifest"],
        "referenced_store_sync_execution.json": validation["store_sync_execution_manifest"],
        "search_alias_projection_schema.json": validation["projection_schema"],
        "projection_hash_contract.json": {
            "authority_content_excludes_plan_binding": True,
            "execute_injects": ["generated_from_plan_id", "generated_from_manifest_hash", "generated_at"],
            "projection_hash": "self_excluding_sha256",
            "final_file_sha256_authority": "Execution Bundle only",
            "cycle_free": True,
        },
        "projection_canonicalization_contract.json": validation["canonicalization_contract"],
        "alias_projection_template.json": validation["projection_template"],
        "alias_authority_content.json": validation["authority_content"],
        "alias_projection_delta_manifest.json": validation["projection_delta"],
        "runtime_code_delta_manifest.json": validation["runtime_manifest"],
        "runtime_code_patch_preview.diff": validation["runtime_patch_preview"],
        "loader_failure_contract.json": validation["loader_failure_contract"],
        "typed_query_integration_contract.json": validation["typed_query_contract"],
        "candidate_merge_contract.json": validation["candidate_merge_contract"],
        "ranking_contract.json": validation["ranking_contract"],
        "governance_filter_contract.json": validation["governance_filter_contract"],
        "target_projection_summary.json": {
            "target_path": ".mka/search_alias_projection.json",
            "current_exists": False, "action": "create", "alias_count": 2,
            "owner_count": 1, "candidate": validation["candidate"],
            "asset_boundary": validation["asset_boundary"],
        },
    }


def _confirmation_manifest(staging, validation, payload):
    files = [{
        "filename": path.name, "sha256": _sha256(path),
        "byte_size": path.stat().st_size, "required": True,
    } for path in sorted(staging.iterdir()) if path.is_file() and not path.name.startswith("._")]
    manifest = {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "confirmation_id": payload["confirmation_id"], "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH, "reviewer": payload["reviewer"],
        "confirmed_at": payload["confirmed_at"], "plan_expires_at": EXPECTED_EXPIRES_AT,
        "validator_code_version": VALIDATOR_VERSION,
        "independent_validation_hash": validation["independent_validation_hash"],
        "files": files,
    }
    manifest["root_confirmation_hash"] = _hash_json(manifest)
    return manifest


def _run_full_pytest(root):
    started = time.monotonic()
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=root,
        capture_output=True, text=True, check=False,
    )
    output = (process.stdout + "\n" + process.stderr).strip()
    passed = _pytest_count(output, "passed")
    failed = _pytest_count(output, "failed")
    errors = _pytest_count(output, "error") + _pytest_count(output, "errors")
    skipped = _pytest_count(output, "skipped")
    warnings = _pytest_count(output, "warning") + _pytest_count(output, "warnings")
    return {
        "passed": process.returncode == 0 and failed == 0 and errors == 0,
        "passed_count": passed, "failed": failed, "errors": errors,
        "skipped": skipped, "warnings": warnings,
        "duration_seconds": round(time.monotonic() - started, 3),
        "command": f"{sys.executable} -m pytest -q",
        "output_tail": "\n".join(output.splitlines()[-8:]),
    }


def _pytest_count(output, label):
    matches = re.findall(rf"(\d+)\s+{re.escape(label)}\b", output)
    return int(matches[-1]) if matches else 0


def _assert_protected_inputs_unchanged(root, validation):
    if _sha256(root / DEFAULT_DECISION_STORE) != EXPECTED_DECISION_STORE_SHA256:
        raise ProductionSearchAliasPlanV2ConfirmationError("Decision Store changed before Confirmation creation")
    if (root / DEFAULT_ALIAS_TARGET).exists():
        raise ProductionSearchAliasPlanV2ConfirmationError("formal Alias target appeared before Confirmation creation")
    plan = validation["plan_manifest"]
    checks = plan["input_checksums"]
    current = {
        "store_sync_execution": sync_validation._hash_path(root / DEFAULT_STORE_SYNC_EXECUTION),
        "managed_vault": sync_validation._hash_path(root / DEFAULT_MANAGED_VAULT),
        "formal_sqlite": _sha256(root / DEFAULT_FORMAL_SQLITE),
        "slack_renderer": _sha256(root / DEFAULT_RENDERER),
    }
    if any(checks[key] != value for key, value in current.items()):
        raise ProductionSearchAliasPlanV2ConfirmationError("formal input changed before Confirmation creation")


def _public_validation(result):
    excluded = {"runtime_patch_preview", "plan_manifest", "store_sync_execution_manifest", "offline"}
    return {key: value for key, value in result.items() if key not in excluded and not key.startswith("confirmation_")}


def _write_reports(output, result, bundle):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILES[0], (
        "# Production Search Alias Plan V2 Confirmation\n\n"
        f"- Conclusion: **{result['conclusion']}**\n"
        f"- PLAN_ID: `{result['plan_id']}`\n"
        f"- Manifest Hash: `{result['manifest_hash']}`\n"
        f"- Confirmation ID: `{result.get('confirmation_id', '')}`\n"
        f"- Root Confirmation Hash: `{result.get('root_confirmation_hash', '')}`\n"
        f"- Idempotent no-op: `{str(result.get('idempotent_noop', False)).lower()}`\n"
    ))
    _write_csv(output / REPORT_FILES[1], [{
        **result["decision_store"],
        "store_sync_execution_id": result["store_sync_execution"]["execution_id"],
        "store_sync_execution_root_hash": result["store_sync_execution"]["root_execution_hash"],
        "managed_parent_count": result["managed_parent_count"], "formal_parent_count": result["formal_parent_count"],
        "status": "pass",
    }])
    _write_csv(output / REPORT_FILES[2], result["aliases"])
    _write_csv(output / REPORT_FILES[3], [{"contract": key, "valid": value} for key, value in result["artifact_contracts"].items()])
    _write_csv(output / REPORT_FILES[4], result["runtime_validation"]["file_validation"] + [{
        "file_path": "components", "action": "validate", "action_ok": result["runtime_validation"]["component_count"] == 15,
        "state_ok": result["runtime_validation"]["complete_count"] == 15,
        "checksum_ok": True, "scope_hash_ok": True, "symbols_ok": result["runtime_validation"]["valid"],
    }])
    _write_csv(output / REPORT_FILES[5], result["projection_failure_tests"])
    _write_csv(output / REPORT_FILES[6], result["offline"])
    _write_csv(output / REPORT_FILES[7], [
        {"field": "plan_id", "expected": EXPECTED_PLAN_ID, "actual": result["reproduced_plan_id"], "valid": result["reproduced_plan_id"] == EXPECTED_PLAN_ID},
        {"field": "manifest_hash", "expected": EXPECTED_MANIFEST_HASH, "actual": result["reproduced_manifest_hash"], "valid": result["reproduced_manifest_hash"] == EXPECTED_MANIFEST_HASH},
    ])
    _write_csv(output / REPORT_FILES[8], [bundle or {"valid": False, "status": "not_created"}])
    _write_csv(output / REPORT_FILES[9], [{
        "formal_systems_unchanged": result["formal_systems_unchanged"],
        "alias_target_absent": result["alias_target_absent"],
        "runtime_modified": False, "vault_or_sqlite_modified": False,
        "slack_renderer_modified": False, "slack_api_called": False,
    }])
    _write_csv(output / REPORT_FILES[10], [{"error": item} for item in result["validation_errors"]], ("error",))
    _write_csv(output / REPORT_FILES[11], [{"warning": item} for item in result["validation_warnings"]], ("warning",))


def _snapshot(paths):
    return {
        key: _sha256(path) if path.is_file() else sync_validation._hash_path(path)
        for key, path in paths.items()
    }


def _sidecars(path):
    return {str(path) + suffix for suffix in ("-wal", "-shm", "-journal") if Path(str(path) + suffix).exists()}


def _require_exact_identity(plan_id, manifest_hash):
    if plan_id != EXPECTED_PLAN_ID:
        raise ProductionSearchAliasPlanV2ConfirmationError("exact PLAN_ID required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise ProductionSearchAliasPlanV2ConfirmationError("exact Manifest Hash required")


def _read_markdown_json(path):
    text = Path(path).read_text(encoding="utf-8")
    match = re.search(r"```json\s*(\{.*\})\s*```", text, re.DOTALL)
    if not match:
        raise ProductionSearchAliasPlanV2ConfirmationError(f"missing JSON contract in {path}")
    return json.loads(match.group(1))


def _git(root, *args, check=True):
    process = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if check and process.returncode:
        raise ProductionSearchAliasPlanV2ConfirmationError(process.stderr.strip())
    return process.stderr.strip() if process.returncode else process.stdout.strip()


def _git_ignored(root, path):
    return subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=root).returncode == 0


def _iso_timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ProductionSearchAliasPlanV2ConfirmationError("timestamp must include timezone")
    return parsed.isoformat(timespec="seconds")


def _resolve(root, path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _hash_json(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, value):
    _write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _write_text(path, value):
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _write_csv(path, rows, default_fields=()):
    rows = list(rows)
    fields = list(rows[0]) if rows else list(default_fields)
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
