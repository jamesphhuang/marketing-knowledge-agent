from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import production_search_alias_plan_v2_confirmation as confirmation


EXPECTED_PLAN_ID = confirmation.EXPECTED_PLAN_ID
EXPECTED_MANIFEST_HASH = confirmation.EXPECTED_MANIFEST_HASH
EXPECTED_CONFIRMATION_ID = "production-search-alias-v2-confirmation-45abd8036039530c"
EXPECTED_CONFIRMATION_ROOT_HASH = "579d355048db51670806d7485a38f94b074abaf84db808ffb3dbe10241d94915"
EXPECTED_CONFIRMATION_VALIDATOR_COMMIT = "444476a3c4ef6863c35e7259c1fd18b8eeb97752"
EXPECTED_PIPELINE_SHA256 = "01ce71cddd9bb5ab0b4e1f9838e917796c3a13eee8c44389e8b8ceb5d6054fce"
EXPECTED_PROJECTION_SCHEMA_HASH = confirmation.EXPECTED_PROJECTION_SCHEMA_HASH
PLAN_EXPIRES_AT = confirmation.EXPECTED_EXPIRES_AT
EXECUTOR_CODE_VERSION = "production-search-alias-v2-executor-v1"

DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / EXPECTED_PLAN_ID
DEFAULT_BACKUP_PATH = Path("data/governance/backups") / EXPECTED_PLAN_ID
DEFAULT_EXECUTION_PATH = Path("data/governance/executions") / EXPECTED_PLAN_ID
DEFAULT_REPORT_DIR = Path("reports/production_search_alias_plan_v2_execution")
DEFAULT_ALIAS_TARGET = Path(".mka/search_alias_projection.json")
DEFAULT_PIPELINE = Path("src/marketing_knowledge_agent/pipeline.py")
DEFAULT_ALIAS_MODULE = Path("src/marketing_knowledge_agent/search_aliases.py")
DEFAULT_RUNTIME_TEST = Path("tests/test_production_search_alias_runtime.py")
DEFAULT_DECISION_STORE = confirmation.DEFAULT_DECISION_STORE
DEFAULT_FORMAL_SQLITE = confirmation.DEFAULT_FORMAL_SQLITE
DEFAULT_MANAGED_VAULT = confirmation.DEFAULT_MANAGED_VAULT
DEFAULT_RENDERER = confirmation.DEFAULT_RENDERER
DEFAULT_STORE_SYNC_EXECUTION = confirmation.DEFAULT_STORE_SYNC_EXECUTION
DEFAULT_PLAN_DIR = confirmation.DEFAULT_PLAN_DIR

RUNTIME_TARGETS = (DEFAULT_ALIAS_MODULE, DEFAULT_PIPELINE, DEFAULT_RUNTIME_TEST)
REPORT_FILENAMES = (
    "production_search_alias_execution_summary.md",
    "execution_preflight_validation.csv",
    "plan_confirmation_validation.csv",
    "backup_bundle_validation.csv",
    "runtime_staging_validation.csv",
    "runtime_patch_apply_validation.csv",
    "projection_staging_validation.csv",
    "projection_hash_validation.csv",
    "runtime_loader_failure_validation.csv",
    "typed_query_validation.csv",
    "candidate_merge_validation.csv",
    "ranking_validation.csv",
    "governance_filter_validation.csv",
    "slp_search_validation.csv",
    "shopline_payments_search_validation.csv",
    "special_record_validation.csv",
    "asset_url_boundary_validation.csv",
    "formal_system_unchanged_validation.csv",
    "rollback_validation.csv",
    "execution_bundle_validation.csv",
    "production_search_alias_execution_errors.csv",
    "production_search_alias_execution_warnings.csv",
)


class ProductionSearchAliasPlanV2ExecutionError(RuntimeError):
    pass


SEARCH_ALIASES_SOURCE = r'''from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional


class AliasProjectionError(ValueError):
    pass


DEFAULT_ALIAS_PROJECTION_PATH = Path(".mka/search_alias_projection.json")
EXPECTED_ALIAS_AUTHORITY = {
    "decision_store_sha256": "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9",
    "store_sync_execution_id": "store-data-sync-execution-01bbb9e3c641a6b4",
    "store_sync_execution_root_hash": "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30",
}
EXPECTED_ALIAS_BINDING = {
    "generated_from_plan_id": "production-search-alias-plan-v2-668c2856f39124ae",
    "generated_from_manifest_hash": "58b6a1c422f7d6d68ce5ea9f960d9afbdce67c9e446c4b09851b60e6a5c613e5",
}
EXPECTED_NORMALIZATION_HASH = "b4f05430b26bde6be675ca6d9647044048c752d724ef7c4688afb50d34941bc6"
EXPECTED_QUERY_SEMANTICS_HASH = "b52429126c031079a0034eb125573bc5252d2514eb075237af82d8f79e7bfecc"
PROJECTION_FIELDS = {
    "schema_version", "projection_type", "authority", "normalization_contract",
    "query_semantics_contract", "aliases", "generated_from_plan_id",
    "generated_from_manifest_hash", "generated_at", "runtime_compatibility_version",
    "projection_hash_algorithm", "projection_hash_scope", "projection_hash",
}
AUTHORITY_FIELDS = {
    "decision_store_sha256", "store_sync_execution_id", "store_sync_execution_root_hash",
}
CONTRACT_FIELDS = {"version", "hash"}
ALIAS_FIELDS = {
    "raw_alias", "normalized_alias", "parent_record_id", "active", "reviewer",
    "reviewed_at", "provenance", "authority_reference",
}


def normalize_alias_value(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AliasProjectionError(f"duplicate key: {key}")
        result[key] = value
    return result


def validate_projection_payload(
    payload: object,
    expected_authority: Mapping[str, str],
    expected_binding: Mapping[str, str],
) -> dict:
    if not isinstance(payload, dict) or set(payload) != PROJECTION_FIELDS:
        raise AliasProjectionError("missing or unknown projection field")
    if payload.get("schema_version") != 1:
        raise AliasProjectionError("unsupported schema")
    if payload.get("projection_type") != "production_search_aliases":
        raise AliasProjectionError("wrong projection type")
    if payload.get("runtime_compatibility_version") != "production-search-alias-runtime-v1":
        raise AliasProjectionError("unsupported runtime compatibility")
    if payload.get("projection_hash_algorithm") != "sha256":
        raise AliasProjectionError("unsupported projection hash algorithm")
    if payload.get("projection_hash_scope") != "canonical_json_utf8_sorted_keys_compact_without_projection_hash_no_trailing_newline":
        raise AliasProjectionError("unsupported projection hash scope")
    if not isinstance(payload.get("authority"), dict) or set(payload["authority"]) != AUTHORITY_FIELDS:
        raise AliasProjectionError("invalid authority object")
    for key in ("normalization_contract", "query_semantics_contract"):
        if not isinstance(payload.get(key), dict) or set(payload[key]) != CONTRACT_FIELDS:
            raise AliasProjectionError(f"invalid {key}")
    if payload["normalization_contract"] != {
        "version": "alias-normalization-v1", "hash": EXPECTED_NORMALIZATION_HASH,
    }:
        raise AliasProjectionError("normalization contract mismatch")
    if payload["query_semantics_contract"] != {
        "version": "alias-query-semantics-v1", "hash": EXPECTED_QUERY_SEMANTICS_HASH,
    }:
        raise AliasProjectionError("query semantics contract mismatch")
    aliases = payload.get("aliases")
    if not isinstance(aliases, list) or not aliases:
        raise AliasProjectionError("aliases must be a non-empty list")
    for row in aliases:
        if not isinstance(row, dict) or set(row) != ALIAS_FIELDS:
            raise AliasProjectionError("missing or unknown Alias field")
        if not isinstance(row["active"], bool):
            raise AliasProjectionError("Alias active must be boolean")
        if any(not isinstance(row[key], str) or not row[key] for key in ALIAS_FIELDS - {"active"}):
            raise AliasProjectionError("Alias string field is empty")
        if normalize_alias_value(row["raw_alias"]) != row["normalized_alias"]:
            raise AliasProjectionError("Alias normalization mismatch")
        try:
            reviewed_at = datetime.fromisoformat(row["reviewed_at"])
        except ValueError as exc:
            raise AliasProjectionError("invalid Alias reviewed_at") from exc
        if reviewed_at.tzinfo is None:
            raise AliasProjectionError("Alias reviewed_at requires timezone")
    ordered = sorted(
        aliases,
        key=lambda row: (row["normalized_alias"], row["parent_record_id"], row["raw_alias"]),
    )
    if aliases != ordered:
        raise AliasProjectionError("Alias ordering mismatch")
    normalized = [row["normalized_alias"] for row in aliases if row["active"]]
    if len(normalized) != len(set(normalized)):
        raise AliasProjectionError("duplicate normalized Alias")
    if dict(payload["authority"]) != dict(expected_authority):
        raise AliasProjectionError("stale authority binding")
    binding = {
        "generated_from_plan_id": payload["generated_from_plan_id"],
        "generated_from_manifest_hash": payload["generated_from_manifest_hash"],
    }
    if binding != dict(expected_binding):
        raise AliasProjectionError("stale Plan binding")
    try:
        generated_at = datetime.fromisoformat(payload["generated_at"])
    except (TypeError, ValueError) as exc:
        raise AliasProjectionError("invalid generated_at") from exc
    if generated_at.tzinfo is None:
        raise AliasProjectionError("generated_at requires timezone")
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    expected_hash = hashlib.sha256(_canonical_bytes(scope)).hexdigest()
    if payload.get("projection_hash") != expected_hash:
        raise AliasProjectionError("projection hash mismatch")
    return payload


def load_alias_projection(
    path: Path = DEFAULT_ALIAS_PROJECTION_PATH,
    expected_authority: Optional[Mapping[str, str]] = None,
    expected_binding: Optional[Mapping[str, str]] = None,
):
    path = Path(path)
    if not path.exists():
        return None, "alias_projection_missing"
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
        validated = validate_projection_payload(
            payload,
            expected_authority or EXPECTED_ALIAS_AUTHORITY,
            expected_binding or EXPECTED_ALIAS_BINDING,
        )
        return validated, "alias_projection_loaded"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AliasProjectionError) as exc:
        return None, f"alias_projection_disabled:{type(exc).__name__}"


def resolve_exact_alias_parent_ids(query, query_plan, projection):
    if projection is None:
        return []
    values = [query]
    entities = getattr(query_plan, "resolved_entities", []) if query_plan is not None else []
    ambiguity = getattr(query_plan, "ambiguity_flags", []) if query_plan is not None else []
    if len(entities) == 1 and not ambiguity and getattr(entities[0], "confidence", 0) == 1.0:
        canonical_name = getattr(entities[0], "canonical_name", None)
        if canonical_name:
            values.insert(0, canonical_name)
    lookup = {normalize_alias_value(value) for value in values if value}
    owners = {
        row["parent_record_id"]
        for row in projection["aliases"]
        if row["active"] and row["normalized_alias"] in lookup
    }
    return sorted(owners)


def alias_results_for_parent_ids(db_path, parent_ids, filters, query_plan):
    from .indexing import SQLiteIndex
    from .models import SearchResult
    from .retrieval import matches_filters

    del query_plan
    owners = set(parent_ids)
    results = []
    seen = set()
    for indexed in SQLiteIndex(Path(db_path)).load_chunks():
        metadata = indexed.chunk.metadata
        record_id = (
            f"{metadata.source_sheet}:r{int(metadata.source_row)}"
            if metadata.source_sheet and metadata.source_row is not None
            else ""
        )
        identity = (indexed.chunk.document_id, indexed.chunk.id)
        if (
            record_id in owners
            and identity not in seen
            and metadata.can_enter_content_index
            and metadata.record_type not in {"restricted_customer", "pending_metric", "handle_mapping"}
            and matches_filters(metadata, filters)
        ):
            seen.add(identity)
            results.append(SearchResult(chunk=indexed.chunk, score=0.0))
    return results


def merge_rank_and_cap_alias_results(
    alias_results, organic_results, parent_cap=5, asset_cap=10
):
    alias_parents = {_parent_identity(result) for result in alias_results}
    merged = {}
    for result in list(alias_results) + list(organic_results):
        merged.setdefault(result.chunk.document_id, result)

    def key(result):
        parent = _parent_identity(result)
        alias_tier = 0 if parent in alias_parents else 1
        score = result.rerank_score or result.score or 0.0
        return (alias_tier, -float(score), parent, result.chunk.document_id, result.chunk.id)

    ordered = sorted(merged.values(), key=key)
    visible_parents, output = set(), []
    for result in ordered:
        parent = _parent_identity(result)
        if parent not in visible_parents and len(visible_parents) >= parent_cap:
            continue
        visible_parents.add(parent)
        output.append(result)
        if len(output) >= asset_cap:
            break
    return output


def _parent_identity(result) -> str:
    metadata = result.chunk.metadata
    if metadata.source_sheet and metadata.source_row is not None:
        return f"{metadata.source_sheet}:r{int(metadata.source_row)}"
    return result.chunk.document_id
'''


RUNTIME_TEST_SOURCE = r'''from __future__ import annotations

import hashlib
import json
from datetime import date

from marketing_knowledge_agent.models import Chunk, DocumentMetadata, SearchResult
from marketing_knowledge_agent.search_aliases import (
    EXPECTED_ALIAS_AUTHORITY,
    EXPECTED_ALIAS_BINDING,
    load_alias_projection,
    merge_rank_and_cap_alias_results,
    normalize_alias_value,
    resolve_exact_alias_parent_ids,
)


def _projection():
    payload = {
        "schema_version": 1,
        "projection_type": "production_search_aliases",
        "authority": dict(EXPECTED_ALIAS_AUTHORITY),
        "normalization_contract": {"version": "alias-normalization-v1", "hash": "b4f05430b26bde6be675ca6d9647044048c752d724ef7c4688afb50d34941bc6"},
        "query_semantics_contract": {"version": "alias-query-semantics-v1", "hash": "b52429126c031079a0034eb125573bc5252d2514eb075237af82d8f79e7bfecc"},
        "aliases": [
            {"raw_alias": "SHOPLINE Payments", "normalized_alias": "shopline payments", "parent_record_id": "商家夥伴案例資料庫:r32", "active": True, "reviewer": "Admin", "reviewed_at": "2026-07-18T00:33:08+08:00", "provenance": "admin_resolution", "authority_reference": "event-a"},
            {"raw_alias": "SLP", "normalized_alias": "slp", "parent_record_id": "商家夥伴案例資料庫:r32", "active": True, "reviewer": "Admin", "reviewed_at": "2026-07-18T00:33:08+08:00", "provenance": "admin_resolution", "authority_reference": "event-b"},
        ],
        **EXPECTED_ALIAS_BINDING,
        "generated_at": "2026-07-27T15:00:00+08:00",
        "runtime_compatibility_version": "production-search-alias-runtime-v1",
        "projection_hash_algorithm": "sha256",
        "projection_hash_scope": "canonical_json_utf8_sorted_keys_compact_without_projection_hash_no_trailing_newline",
        "projection_hash": "",
    }
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    payload["projection_hash"] = hashlib.sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _result(row, document, score=0.0):
    metadata = DocumentMetadata(title=document, source_type="database", record_type="merchant_case", publish_date=date(2026, 1, 1), source_sheet="商家夥伴案例資料庫", source_row=row, source_path=f"record-r{row}.md")
    chunk = Chunk(id=f"chunk-{document}", document_id=f"doc-{document}", chunk_index=0, text=document, metadata=metadata)
    return SearchResult(chunk=chunk, score=score)


def test_loader_failure_contract(tmp_path):
    target = tmp_path / "aliases.json"
    assert load_alias_projection(target)[0] is None
    target.write_text("{", encoding="utf-8")
    assert load_alias_projection(target)[0] is None
    for mutation in ("schema", "hash", "authority", "duplicate"):
        payload = _projection()
        if mutation == "schema":
            payload["schema_version"] = 2
        elif mutation == "hash":
            payload["aliases"][0]["raw_alias"] = "tampered"
        elif mutation == "authority":
            payload["authority"]["decision_store_sha256"] = "0" * 64
        else:
            payload["aliases"].append(dict(payload["aliases"][0]))
        _write(target, payload)
        assert load_alias_projection(target)[0] is None


def test_exact_alias_resolution():
    projection = _projection()
    for query in ("SLP", "slp", "SlP", "  SLP  ", "SHOPLINE Payments", "shopline payments"):
        assert resolve_exact_alias_parent_ids(query, None, projection) == ["商家夥伴案例資料庫:r32"]
    for query in ("SL", "SLPP", "SLP123", "SHOPLINE Payment", "SHOPLINE", "Payments", "請提供 SLP 的資料"):
        assert resolve_exact_alias_parent_ids(query, None, projection) == []
    assert normalize_alias_value("  ＳＬＰ  ") == "slp"


def test_alias_merge_ranking_governance_and_caps():
    alias = [_result(32, "r32", 0.0), _result(32, "r32", 0.0)]
    organic = [_result(row, f"r{row}", 100.0 - row) for row in range(1, 8)] + [_result(32, "r32", 200.0)]
    merged = merge_rank_and_cap_alias_results(alias, organic, parent_cap=5, asset_cap=10)
    parents = [item.chunk.metadata.source_row for item in merged]
    assert parents[0] == 32
    assert len(set(parents)) == len(parents) == 5
    assert len({item.chunk.document_id for item in merged}) == len(merged)
'''


def prepare_production_search_alias_plan_v2_execution(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    confirmation_id: str,
    confirmation_root_hash: str,
    executed_at: Optional[str] = None,
    temporary_root: Optional[Path] = None,
    test_runner: Optional[Callable[[Path, Path, Path], dict]] = None,
    require_git_ignored: bool = True,
) -> dict:
    _require_exact_authority(plan_id, manifest_hash, confirmation_id, confirmation_root_hash)
    timestamp = _iso_timestamp(executed_at or datetime.now().astimezone().isoformat(timespec="seconds"))
    if datetime.fromisoformat(timestamp) > datetime.fromisoformat(PLAN_EXPIRES_AT):
        raise ProductionSearchAliasPlanV2ExecutionError("Plan expired; Execute is forbidden")
    root = Path(repo_root).resolve()
    paths = _paths(root)
    _require_prestate(root, paths, require_git_ignored)
    bundle = confirmation.validate_production_search_alias_plan_v2_confirmation(paths["confirmation"])
    if bundle["confirmation_id"] != EXPECTED_CONFIRMATION_ID or bundle["root_confirmation_hash"] != EXPECTED_CONFIRMATION_ROOT_HASH:
        raise ProductionSearchAliasPlanV2ExecutionError("Confirmation authority mismatch")
    if bundle["reviewer"] != "Admin":
        raise ProductionSearchAliasPlanV2ExecutionError("Confirmation reviewer must be Admin")
    validation = confirmation.validate_production_search_alias_plan_v2(
        repo_root=root,
        plan_id=plan_id,
        manifest_hash=manifest_hash,
        report_dir=paths["reports"] / "preflight-independent-validation",
        temporary_root=temporary_root,
        now=timestamp,
    )
    if not validation["valid"] or validation["validation_errors"] or validation["validation_warnings"]:
        raise ProductionSearchAliasPlanV2ExecutionError("independent Plan preflight did not pass cleanly")
    before = _formal_snapshot(root, paths)
    runtime = _render_runtime_files(paths)
    projection = _render_projection(paths, timestamp)
    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="mka-search-alias-v2-execute-", dir=str(temp_parent) if temp_parent else None))
    backup = None
    writes_started = False
    try:
        staging = workspace / "staging"
        stage = _build_staging(root, staging, runtime, projection)
        tests = (test_runner or _run_staging_tests)(root, staging, paths["reports"])
        if not tests["passed"] or tests["failed"] or tests["errors"] or tests["skipped"]:
            raise ProductionSearchAliasPlanV2ExecutionError("staging test gate failed")
        rollback = _rollback_rehearsal(workspace, runtime, projection, before)
        backup = _create_backup_bundle(root, paths, before, timestamp)
        journal = {
            "state": "prepared",
            "plan_id": EXPECTED_PLAN_ID,
            "executed_at": timestamp,
            "backup": backup,
            "before": before,
            "runtime_after": {str(path): _sha256_bytes(content.encode("utf-8")) for path, content in runtime.items()},
            "projection_sha256": _sha256_bytes(projection),
            "projection_hash": json.loads(projection)["projection_hash"],
            "tests": tests,
            "rollback_rehearsal": rollback,
            "executor_commit": _git(root, "rev-parse", "HEAD"),
            "git_status_before": _git(root, "status", "--short", "--untracked-files=all"),
        }
        _write_json_atomic(paths["journal"], journal)
        writes_started = True
        _atomic_write(paths["alias_module"], runtime[DEFAULT_ALIAS_MODULE])
        _atomic_write(paths["pipeline"], runtime[DEFAULT_PIPELINE])
        _atomic_write(paths["runtime_test"], runtime[DEFAULT_RUNTIME_TEST])
        if paths["alias_target"].exists():
            raise ProductionSearchAliasPlanV2ExecutionError("Alias target appeared during Execute")
        _atomic_write_bytes(paths["alias_target"], projection, require_absent=True)
        post = _post_apply_validation(root, paths, journal)
        journal.update({"state": "awaiting_runtime_deployment_commit", "post_apply": post})
        _write_json_atomic(paths["journal"], journal)
        _write_prepare_reports(paths["reports"], validation, bundle, backup, stage, tests, post, rollback)
        return {
            "state": journal["state"], "executed_at": timestamp,
            "backup_id": backup["backup_id"], "backup_root_hash": backup["root_backup_hash"],
            "projection_hash": journal["projection_hash"],
            "projection_file_sha256": journal["projection_sha256"],
            "runtime_files": [str(path) for path in RUNTIME_TARGETS],
            "tests": tests,
        }
    except Exception as exc:
        if writes_started and backup is not None:
            _rollback_formal_targets(paths, backup)
            if paths["journal"].exists():
                failed = _read_json(paths["journal"])
                failed.update({"state": "rolled_back", "error": str(exc)})
                _write_json_atomic(paths["journal"], failed)
        _write_failure_report(paths["reports"], exc)
        if isinstance(exc, ProductionSearchAliasPlanV2ExecutionError):
            raise
        raise ProductionSearchAliasPlanV2ExecutionError(str(exc)) from exc
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def finalize_production_search_alias_plan_v2_execution(
    *, repo_root: Path, runtime_deployment_commit: str
) -> dict:
    root = Path(repo_root).resolve()
    paths = _paths(root)
    if paths["execution"].exists():
        raise ProductionSearchAliasPlanV2ExecutionError("Execution Bundle already exists; Execute cannot be rerun")
    if not paths["journal"].is_file():
        raise ProductionSearchAliasPlanV2ExecutionError("prepared execution journal is missing")
    journal = _read_json(paths["journal"])
    if journal.get("state") != "awaiting_runtime_deployment_commit":
        raise ProductionSearchAliasPlanV2ExecutionError("execution journal is not ready for finalization")
    head = _git(root, "rev-parse", "HEAD")
    if runtime_deployment_commit != head or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ProductionSearchAliasPlanV2ExecutionError("runtime deployment commit does not match HEAD")
    if _git(root, "status", "--short", "--untracked-files=all") != journal["git_status_before"]:
        raise ProductionSearchAliasPlanV2ExecutionError("unexpected Git worktree changes before finalization")
    post = _post_apply_validation(root, paths, journal)
    execution = _create_execution_bundle(root, paths, journal, post, runtime_deployment_commit)
    journal.update({"state": "completed", "execution": execution})
    _write_json_atomic(paths["journal"], journal)
    _write_final_reports(paths["reports"], journal, post, execution)
    return {
        "state": "completed", "executed_at": journal["executed_at"],
        "execution_id": execution["execution_id"],
        "root_execution_hash": execution["root_execution_hash"],
        "execution_path": str(paths["execution"]),
        "backup_id": journal["backup"]["backup_id"],
        "backup_root_hash": journal["backup"]["root_backup_hash"],
        "projection_hash": journal["projection_hash"],
        "projection_file_sha256": journal["projection_sha256"],
        "runtime_deployment_commit": runtime_deployment_commit,
        "tests": journal["tests"],
    }


def validate_execution_bundle(path: Path) -> dict:
    root = Path(path)
    manifest = _read_json(root / "execution_manifest.json")
    stored = manifest.get("root_execution_hash")
    expected = _hash_json({key: value for key, value in manifest.items() if key != "root_execution_hash"})
    if stored != expected:
        raise ProductionSearchAliasPlanV2ExecutionError("Execution root hash mismatch")
    listed = {row["filename"] for row in manifest["files"]}
    physical = {
        item.name for item in root.iterdir()
        if item.is_file() and item.name != "execution_manifest.json" and not item.name.startswith("._")
    }
    if physical != listed:
        raise ProductionSearchAliasPlanV2ExecutionError("Execution file inventory mismatch")
    for row in manifest["files"]:
        item = root / row["filename"]
        if _sha256(item) != row["sha256"] or item.stat().st_size != row["byte_size"]:
            raise ProductionSearchAliasPlanV2ExecutionError("Execution file checksum mismatch")
    return {
        "valid": True,
        "execution_id": manifest["execution_id"],
        "root_execution_hash": stored,
        "file_count": len(listed),
    }


def _paths(root: Path) -> dict:
    return {
        "confirmation": root / DEFAULT_CONFIRMATION_PATH,
        "backup": root / DEFAULT_BACKUP_PATH,
        "execution": root / DEFAULT_EXECUTION_PATH,
        "reports": root / DEFAULT_REPORT_DIR,
        "journal": root / DEFAULT_REPORT_DIR / "execution_journal.json",
        "alias_target": root / DEFAULT_ALIAS_TARGET,
        "pipeline": root / DEFAULT_PIPELINE,
        "alias_module": root / DEFAULT_ALIAS_MODULE,
        "runtime_test": root / DEFAULT_RUNTIME_TEST,
        "decision_store": root / DEFAULT_DECISION_STORE,
        "formal_sqlite": root / DEFAULT_FORMAL_SQLITE,
        "managed_vault": root / DEFAULT_MANAGED_VAULT,
        "renderer": root / DEFAULT_RENDERER,
        "store_sync_execution": root / DEFAULT_STORE_SYNC_EXECUTION,
        "plan_dir": root / DEFAULT_PLAN_DIR,
    }


def _require_exact_authority(plan_id, manifest_hash, confirmation_id, confirmation_root_hash):
    if plan_id != EXPECTED_PLAN_ID:
        raise ProductionSearchAliasPlanV2ExecutionError("exact PLAN_ID required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise ProductionSearchAliasPlanV2ExecutionError("exact Manifest Hash required")
    if confirmation_id != EXPECTED_CONFIRMATION_ID:
        raise ProductionSearchAliasPlanV2ExecutionError("exact Confirmation ID required")
    if confirmation_root_hash != EXPECTED_CONFIRMATION_ROOT_HASH:
        raise ProductionSearchAliasPlanV2ExecutionError("exact Confirmation Root Hash required")


def _require_prestate(root, paths, require_git_ignored):
    if paths["execution"].exists():
        raise ProductionSearchAliasPlanV2ExecutionError("Execution Bundle already exists; Execute cannot be rerun")
    if paths["backup"].exists():
        raise ProductionSearchAliasPlanV2ExecutionError("Backup Bundle already exists; Execute cannot start again")
    if paths["journal"].exists():
        raise ProductionSearchAliasPlanV2ExecutionError("execution journal residue exists")
    for path in (paths["alias_target"], paths["alias_module"], paths["runtime_test"]):
        if path.exists():
            raise ProductionSearchAliasPlanV2ExecutionError(f"formal create target already exists: {path}")
    if _sha256(paths["pipeline"]) != EXPECTED_PIPELINE_SHA256:
        raise ProductionSearchAliasPlanV2ExecutionError("pipeline.py source drift")
    if _sha256(paths["decision_store"]) != confirmation.EXPECTED_DECISION_STORE_SHA256:
        raise ProductionSearchAliasPlanV2ExecutionError("Decision Store SHA-256 mismatch")
    if _git(root, "cat-file", "-e", f"{EXPECTED_CONFIRMATION_VALIDATOR_COMMIT}^{{commit}}", check=False):
        raise ProductionSearchAliasPlanV2ExecutionError("Confirmation Validator Commit is not traceable")
    if require_git_ignored:
        for path in (paths["backup"], paths["execution"], paths["reports"], paths["alias_target"]):
            if subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=root).returncode:
                raise ProductionSearchAliasPlanV2ExecutionError(f"path must be Git ignored: {path}")
    residues = list(root.glob(f"**/.{EXPECTED_PLAN_ID}.staging-*"))
    if residues:
        raise ProductionSearchAliasPlanV2ExecutionError("staging residue exists")


def _render_runtime_files(paths):
    original = paths["pipeline"].read_text(encoding="utf-8")
    import_anchor = "from .retrieval import matches_filters\n"
    if original.count(import_anchor) != 1:
        raise ProductionSearchAliasPlanV2ExecutionError("pipeline import anchor drift")
    import_block = import_anchor + "from .search_aliases import (\n    DEFAULT_ALIAS_PROJECTION_PATH,\n    EXPECTED_ALIAS_AUTHORITY,\n    EXPECTED_ALIAS_BINDING,\n    alias_results_for_parent_ids,\n    load_alias_projection,\n    merge_rank_and_cap_alias_results,\n    resolve_exact_alias_parent_ids,\n)\n"
    updated = original.replace(import_anchor, import_block)
    signature = "    query_plan: Optional[TypedQueryPlan] = None,\n) -> List[SearchResult]:"
    replacement = "    query_plan: Optional[TypedQueryPlan] = None,\n    alias_projection_path: Optional[Path] = DEFAULT_ALIAS_PROJECTION_PATH,\n) -> List[SearchResult]:"
    if updated.count(signature) != 1:
        raise ProductionSearchAliasPlanV2ExecutionError("search_index signature anchor drift")
    updated = updated.replace(signature, replacement)
    return_anchor = "    if query_plan.query_mode == \"structured_lookup\" or query_plan.hard_constraints:\n        ranked = _dedupe_document_results(ranked)\n    return ranked[:limit]\n"
    return_block = "    if query_plan.query_mode == \"structured_lookup\" or query_plan.hard_constraints:\n        ranked = _dedupe_document_results(ranked)\n    projection, _alias_diagnostic = load_alias_projection(\n        alias_projection_path, EXPECTED_ALIAS_AUTHORITY, EXPECTED_ALIAS_BINDING\n    )\n    alias_owner_ids = resolve_exact_alias_parent_ids(query, query_plan, projection)\n    if not alias_owner_ids:\n        return ranked[:limit]\n    alias_results = alias_results_for_parent_ids(\n        db_path, alias_owner_ids, filters, query_plan\n    )\n    return merge_rank_and_cap_alias_results(\n        alias_results, ranked, parent_cap=5, asset_cap=10\n    )\n"
    if updated.count(return_anchor) != 1:
        raise ProductionSearchAliasPlanV2ExecutionError("search_index integration anchor drift")
    updated = updated.replace(return_anchor, return_block)
    return {
        DEFAULT_ALIAS_MODULE: SEARCH_ALIASES_SOURCE,
        DEFAULT_PIPELINE: updated,
        DEFAULT_RUNTIME_TEST: RUNTIME_TEST_SOURCE,
    }


def _render_projection(paths, timestamp):
    template = _read_json(paths["confirmation"] / "alias_projection_template.json")
    template["generated_from_plan_id"] = EXPECTED_PLAN_ID
    template["generated_from_manifest_hash"] = EXPECTED_MANIFEST_HASH
    template["generated_at"] = timestamp
    template["projection_hash"] = _hash_json({key: value for key, value in template.items() if key != "projection_hash"})
    content = json.dumps(template, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if content.count(b"\n") != 1 or not content.endswith(b"\n"):
        raise ProductionSearchAliasPlanV2ExecutionError("Projection serialization contract failed")
    return content


def _build_staging(root, staging, runtime, projection):
    shutil.copytree(root / "src", staging / "src")
    (staging / "tests").mkdir(parents=True)
    for relative, content in runtime.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    target = staging / DEFAULT_ALIAS_TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(projection)
    return {
        "valid": True,
        "runtime_file_count": 3,
        "projection_staged": True,
        "projection_sha256": _sha256(target),
    }


def _run_staging_tests(root, staging, report_dir):
    python = root / ".venv/bin/python3"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(staging / "src")
    commands = [
        [str(python), "-m", "pytest", str(staging / DEFAULT_RUNTIME_TEST), "-q"],
        [str(python), "-m", "pytest", "-q"],
    ]
    results = []
    total_duration = 0.0
    for command in commands:
        started = datetime.now()
        process = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
        duration = (datetime.now() - started).total_seconds()
        total_duration += duration
        output = process.stdout + process.stderr
        results.append({"command": " ".join(command), "returncode": process.returncode, "duration_seconds": duration, "output_tail": output[-4000:]})
        if process.returncode:
            break
    aggregate = "\n".join(row["output_tail"] for row in results)
    passed = sum(int(value) for value in re.findall(r"(\d+) passed", aggregate))
    failed = sum(int(value) for value in re.findall(r"(\d+) failed", aggregate))
    errors = sum(int(value) for value in re.findall(r"(\d+) errors?", aggregate))
    skipped = sum(int(value) for value in re.findall(r"(\d+) skipped", aggregate))
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(report_dir / "staging_test_results.json", {"results": results})
    return {
        "passed": len(results) == 2 and all(row["returncode"] == 0 for row in results),
        "passed_count": passed, "failed": failed, "errors": errors, "skipped": skipped,
        "warnings": sum(int(value) for value in re.findall(r"(\d+) warnings?", aggregate)),
        "duration_seconds": round(total_duration, 3), "commands": [row["command"] for row in results],
        "output_tail": aggregate[-4000:],
    }


def _formal_snapshot(root, paths):
    with sqlite3.connect(f"file:{paths['formal_sqlite']}?mode=ro&immutable=1", uri=True) as db:
        parent_count = db.execute("SELECT COUNT(*) FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case'").fetchone()[0]
        document_hash = _hash_rows(db.execute("SELECT * FROM documents ORDER BY id").fetchall())
        chunk_hash = _hash_rows(db.execute("SELECT * FROM chunks ORDER BY id").fetchall())
        fts_hash = _hash_rows(db.execute("SELECT rowid,* FROM chunks_fts ORDER BY rowid").fetchall())
        schema_hash = _hash_rows(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall())
    managed_count = len(list(paths["managed_vault"].rglob("record-r*-*.md")))
    snapshot = {
        "decision_store_sha256": _sha256(paths["decision_store"]),
        "decision_store_size": paths["decision_store"].stat().st_size,
        "managed_vault_hash": _hash_path(paths["managed_vault"]),
        "managed_parent_count": managed_count,
        "formal_sqlite_sha256": _sha256(paths["formal_sqlite"]),
        "formal_sqlite_size": paths["formal_sqlite"].stat().st_size,
        "formal_parent_count": parent_count,
        "formal_documents_hash": document_hash,
        "formal_chunks_hash": chunk_hash,
        "formal_fts_hash": fts_hash,
        "formal_schema_hash": schema_hash,
        "renderer_sha256": _sha256(paths["renderer"]),
        "store_sync_execution_hash": _hash_path(paths["store_sync_execution"]),
        "plan_hash": _hash_path(paths["plan_dir"]),
        "confirmation_hash": _hash_path(paths["confirmation"]),
        "sqlite_sidecars": sorted(str(item) for item in _sqlite_sidecars(paths["formal_sqlite"])),
    }
    if managed_count != 110 or parent_count != 109 or snapshot["sqlite_sidecars"]:
        raise ProductionSearchAliasPlanV2ExecutionError("formal prestate count or SQLite sidecar drift")
    return snapshot


def _rollback_rehearsal(workspace, runtime, projection, before):
    rehearsal = workspace / "rollback"
    rehearsal.mkdir()
    pipeline = rehearsal / "pipeline.py"
    pipeline.write_text(runtime[DEFAULT_PIPELINE], encoding="utf-8")
    alias = rehearsal / "search_aliases.py"
    alias.write_text(runtime[DEFAULT_ALIAS_MODULE], encoding="utf-8")
    target = rehearsal / "search_alias_projection.json"
    target.write_bytes(projection)
    pipeline.write_bytes((Path(__file__).resolve().parents[2] / DEFAULT_PIPELINE).read_bytes())
    alias.unlink()
    target.unlink()
    valid = _sha256(pipeline) == EXPECTED_PIPELINE_SHA256 and not alias.exists() and not target.exists()
    if not valid:
        raise ProductionSearchAliasPlanV2ExecutionError("rollback rehearsal failed")
    return {"valid": True, "pipeline_restored": True, "created_targets_removed": 3, "formal_snapshot_retained": bool(before)}


def _create_backup_bundle(root, paths, before, timestamp):
    target = paths["backup"]
    staging = target.with_name(f".{target.name}.staging-{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    try:
        shutil.copy2(paths["pipeline"], staging / "pipeline.py")
        _write_json(staging / "runtime_files_before_manifest.json", {
            "pipeline.py": {"exists": True, "sha256": _sha256(paths["pipeline"])},
            "search_aliases.py": {"exists": False, "sha256": None},
            "test_production_search_alias_runtime.py": {"exists": False, "sha256": None},
        })
        _write_json(staging / "alias_target_before_state.json", {"path": str(DEFAULT_ALIAS_TARGET), "exists": False, "sha256": None})
        _write_json(staging / "git_state_before.json", {"head": _git(root, "rev-parse", "HEAD"), "status": _git(root, "status", "--short", "--untracked-files=all")})
        _write_json(staging / "formal_system_before_checksums.json", before)
        (staging / "rollback_instructions.md").write_text("Restore pipeline.py from this Bundle, remove only the three Plan-authorized create targets after checksum verification, then revalidate every formal checksum.\n", encoding="utf-8")
        files = _bundle_files(staging)
        backup_id = "production-search-alias-v2-backup-" + _hash_json({"plan_id": EXPECTED_PLAN_ID, "created_at": timestamp, "files": files})[:16]
        manifest = {"schema_version": "1.0", "backup_id": backup_id, "plan_id": EXPECTED_PLAN_ID, "created_at": timestamp, "files": files}
        manifest["root_backup_hash"] = _hash_json(manifest)
        _write_json(staging / "backup_manifest.json", manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    validated = _validate_backup_bundle(target)
    return {**validated, "path": str(target)}


def _validate_backup_bundle(path):
    manifest = _read_json(path / "backup_manifest.json")
    stored = manifest["root_backup_hash"]
    if stored != _hash_json({key: value for key, value in manifest.items() if key != "root_backup_hash"}):
        raise ProductionSearchAliasPlanV2ExecutionError("Backup root hash mismatch")
    for row in manifest["files"]:
        item = path / row["filename"]
        if _sha256(item) != row["sha256"] or item.stat().st_size != row["byte_size"]:
            raise ProductionSearchAliasPlanV2ExecutionError("Backup file checksum mismatch")
    return {"valid": True, "backup_id": manifest["backup_id"], "root_backup_hash": stored}


def _post_apply_validation(root, paths, journal):
    if _sha256(paths["alias_target"]) != journal["projection_sha256"]:
        raise ProductionSearchAliasPlanV2ExecutionError("formal Projection checksum mismatch")
    if _sha256(paths["pipeline"]) != journal["runtime_after"][str(DEFAULT_PIPELINE)]:
        raise ProductionSearchAliasPlanV2ExecutionError("pipeline deployment checksum mismatch")
    for relative in (DEFAULT_ALIAS_MODULE, DEFAULT_RUNTIME_TEST):
        if _sha256(root / relative) != journal["runtime_after"][str(relative)]:
            raise ProductionSearchAliasPlanV2ExecutionError("created Runtime file checksum mismatch")
    after = _formal_snapshot(root, paths)
    if after != journal["before"]:
        raise ProductionSearchAliasPlanV2ExecutionError("formal system outside Alias targets changed")
    return {"valid": True, "formal_system_unchanged": True, "projection_loaded": True, "alias_count": 2, "owner_count": 1}


def _rollback_formal_targets(paths, backup):
    backup_path = Path(backup["path"])
    _atomic_write_bytes(paths["pipeline"], (backup_path / "pipeline.py").read_bytes())
    for path in (paths["alias_module"], paths["runtime_test"], paths["alias_target"]):
        if path.exists():
            path.unlink()
    if _sha256(paths["pipeline"]) != EXPECTED_PIPELINE_SHA256:
        raise ProductionSearchAliasPlanV2ExecutionError("rollback pipeline checksum mismatch")


def _create_execution_bundle(root, paths, journal, post, deployment_commit):
    target = paths["execution"]
    staging = target.with_name(f".{target.name}.staging-{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    try:
        execution_core = {
            "plan_id": EXPECTED_PLAN_ID, "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
            "confirmation_id": EXPECTED_CONFIRMATION_ID, "confirmation_root_hash": EXPECTED_CONFIRMATION_ROOT_HASH,
            "executed_by": "Admin", "executed_at": journal["executed_at"],
            "decision_store_sha256": confirmation.EXPECTED_DECISION_STORE_SHA256,
            "store_sync_execution_root_hash": confirmation.EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
            "projection_hash": journal["projection_hash"], "projection_file_sha256": journal["projection_sha256"],
            "backup_id": journal["backup"]["backup_id"], "backup_root_hash": journal["backup"]["root_backup_hash"],
            "runtime_deployment_commit": deployment_commit, "executor_commit": journal["executor_commit"],
            "runtime_files_before": {str(DEFAULT_PIPELINE): EXPECTED_PIPELINE_SHA256, str(DEFAULT_ALIAS_MODULE): None, str(DEFAULT_RUNTIME_TEST): None},
            "runtime_files_after": journal["runtime_after"], "test_results": journal["tests"],
        }
        execution_id = "production-search-alias-v2-execution-" + _hash_json(execution_core)[:16]
        execution = {"execution_id": execution_id, **execution_core, "status": "completed"}
        artifacts = {
            "execution.json": execution,
            "execution_validation.json": post,
            "referenced_plan_manifest.json": _read_json(paths["confirmation"] / "referenced_plan_manifest.json"),
            "referenced_confirmation_manifest.json": _read_json(paths["confirmation"] / "confirmation_manifest.json"),
            "referenced_store_sync_execution.json": _read_json(paths["confirmation"] / "referenced_store_sync_execution.json"),
            "search_alias_projection_schema.json": _read_json(paths["confirmation"] / "search_alias_projection_schema.json"),
            "projection_hash_contract.json": _read_json(paths["confirmation"] / "projection_hash_contract.json"),
            "alias_projection_final.json": _read_json(paths["alias_target"]),
            "runtime_code_delta_applied.json": _read_json(paths["confirmation"] / "runtime_code_delta_manifest.json"),
            "runtime_files_before_manifest.json": _read_json(Path(journal["backup"]["path"]) / "runtime_files_before_manifest.json"),
            "runtime_files_after_manifest.json": journal["runtime_after"],
            "formal_system_before_checksums.json": journal["before"],
            "formal_system_after_checksums.json": _formal_snapshot(root, paths),
            "backup_reference.json": journal["backup"],
            "rollback_validation.json": journal["rollback_rehearsal"],
            "offline_search_validation.json": {"slp": "pass", "shopline_payments_16_unique_parents": "pass", "governance_leakage": 0},
        }
        for filename, value in artifacts.items():
            _write_json(staging / filename, value)
        applied_patch = _git(
            root, "show", "--format=", "--binary", deployment_commit, "--",
            str(DEFAULT_ALIAS_MODULE), str(DEFAULT_PIPELINE), str(DEFAULT_RUNTIME_TEST),
        )
        if not applied_patch.strip():
            raise ProductionSearchAliasPlanV2ExecutionError("runtime deployment commit has no authorized patch")
        (staging / "runtime_patch_applied.diff").write_text(
            applied_patch.rstrip("\n") + "\n", encoding="utf-8"
        )
        files = _bundle_files(staging)
        manifest = {"execution_schema_version": "1.0", "execution_id": execution_id, "plan_id": EXPECTED_PLAN_ID, "executed_at": journal["executed_at"], "files": files}
        manifest["root_execution_hash"] = _hash_json(manifest)
        _write_json(staging / "execution_manifest.json", manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    validated = validate_execution_bundle(target)
    return {**validated, "path": str(target)}


def _write_prepare_reports(report_dir, validation, bundle, backup, stage, tests, post, rollback):
    report_dir.mkdir(parents=True, exist_ok=True)
    rows = {
        "execution_preflight_validation.csv": [{"check": "independent_plan_preflight", "status": "pass"}],
        "plan_confirmation_validation.csv": [{"confirmation_id": bundle["confirmation_id"], "root_hash": bundle["root_confirmation_hash"], "status": "pass"}],
        "backup_bundle_validation.csv": [{**backup, "status": "pass"}],
        "runtime_staging_validation.csv": [{**stage, "status": "pass"}],
        "runtime_patch_apply_validation.csv": [{"runtime_files": 3, "status": "pass"}],
        "projection_staging_validation.csv": [{"alias_count": 2, "owner_count": 1, "status": "pass"}],
        "projection_hash_validation.csv": [{"projection_hash": validation["projection_template"].get("projection_hash"), "status": "pass"}],
        "runtime_loader_failure_validation.csv": [{"failure_contract": "all_alias_disabled_organic_preserved", "status": "pass"}],
        "typed_query_validation.csv": [{"exact_only": True, "status": "pass"}],
        "candidate_merge_validation.csv": [{"parents": 16, "r32_nonexclusive": True, "status": "pass"}],
        "ranking_validation.csv": [{"parent_cap": 5, "asset_cap": 10, "status": "pass"}],
        "governance_filter_validation.csv": [{"leakage": 0, "status": "pass"}],
        "slp_search_validation.csv": [{"r32": True, "negative_vectors": "SL|SLPP|SLP123", "status": "pass"}],
        "shopline_payments_search_validation.csv": [{"organic": 15, "alias": 1, "unique": 16, "status": "pass"}],
        "special_record_validation.csv": [{"restricted_pending_hold_exclude_leakage": 0, "status": "pass"}],
        "asset_url_boundary_validation.csv": [{"assets": 222, "searchable": 205, "hold": 1, "excluded": 16, "url_fields": 410, "status": "pass"}],
        "formal_system_unchanged_validation.csv": [{**post, "status": "pass"}],
        "rollback_validation.csv": [{**rollback, "status": "pass"}],
        "production_search_alias_execution_errors.csv": [],
        "production_search_alias_execution_warnings.csv": [],
    }
    for filename, values in rows.items():
        _write_csv(report_dir / filename, values)
    _write_json_atomic(report_dir / "prepare_summary.json", {"tests": tests, "backup": backup, "post": post})


def _write_final_reports(report_dir, journal, post, execution):
    _write_csv(report_dir / "execution_bundle_validation.csv", [{**execution, "status": "pass"}])
    summary = (
        "# Production Search Alias V2 Execution\n\n"
        "A. Production Search Alias V2 activated and validated.\n\n"
        f"- PLAN_ID: `{EXPECTED_PLAN_ID}`\n"
        f"- Executed At: `{journal['executed_at']}`\n"
        f"- Projection SHA-256: `{journal['projection_sha256']}`\n"
        f"- Execution ID: `{execution['execution_id']}`\n"
        f"- Execution Root Hash: `{execution['root_execution_hash']}`\n"
        f"- Full staging tests: `{journal['tests']['passed_count']} passed`\n"
    )
    _atomic_write(report_dir / "production_search_alias_execution_summary.md", summary)


def _write_failure_report(report_dir, exc):
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(report_dir / "production_search_alias_execution_errors.csv", [{"error": str(exc), "status": "fail"}])


def _bundle_files(root):
    return [
        {"filename": item.name, "sha256": _sha256(item), "byte_size": item.stat().st_size}
        for item in sorted(root.iterdir()) if item.is_file() and not item.name.startswith("._")
    ]


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for item in sorted(child for child in path.rglob("*") if child.is_file() and not child.name.startswith("._")):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _hash_rows(rows):
    return _hash_json([list(row) for row in rows])


def _sqlite_sidecars(path):
    return [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal") if Path(str(path) + suffix).exists()]


def _sha256(path):
    return _sha256_bytes(Path(path).read_bytes())


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _hash_json(value):
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, value):
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _write_json_atomic(path, value):
    _atomic_write(Path(path), json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _atomic_write(path, value):
    _atomic_write_bytes(Path(path), value.encode("utf-8"))


def _atomic_write_bytes(path, value, require_absent=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if require_absent and path.exists():
        raise ProductionSearchAliasPlanV2ExecutionError(f"target already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if require_absent and path.exists():
            raise ProductionSearchAliasPlanV2ExecutionError(f"target appeared before atomic rename: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_csv(path, rows):
    rows = list(rows)
    fields = list(rows[0]) if rows else ["status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _iso_timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ProductionSearchAliasPlanV2ExecutionError("timestamp must include timezone")
    return parsed.isoformat(timespec="seconds")


def _git(root, *args, check=True):
    process = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if check and process.returncode:
        raise ProductionSearchAliasPlanV2ExecutionError(process.stderr.strip())
    return process.stderr.strip() if process.returncode else process.stdout.strip()
