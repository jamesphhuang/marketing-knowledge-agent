from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import build_parser, main
from marketing_knowledge_agent.production_search_alias_confirmation import (
    EXPECTED_MANIFEST_HASH as OLD_MANIFEST_HASH,
    EXPECTED_PLAN_ID as OLD_PLAN_ID,
    validate_production_search_alias_plan,
)
from marketing_knowledge_agent.production_search_alias_plan_v2 import (
    REPORT_FILENAMES,
    _validate_runtime_manifest,
    canonical_json_bytes,
    generate_production_search_alias_plan_v2,
    load_temporary_projection,
    render_projection,
    validate_projection,
)


CREATED_AT = "2026-07-22T19:30:00+08:00"


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _hash_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


@pytest.fixture(scope="module")
def plan(tmp_path_factory):
    root = tmp_path_factory.mktemp("production-search-alias-plan-v2")
    return generate_production_search_alias_plan_v2(
        repo_root=_root(), output_dir=root / "reports",
        temporary_root=root / "temporary", created_at=CREATED_AT,
    )


def test_old_plan_remains_blocked_and_superseded(plan, tmp_path):
    old = validate_production_search_alias_plan(
        repo_root=_root(), plan_id=OLD_PLAN_ID, manifest_hash=OLD_MANIFEST_HASH,
        report_dir=tmp_path / "old-reports", temporary_root=tmp_path / "old-work",
        now="2026-07-22T18:00:00+08:00",
    )
    assert old["valid"] is False
    assert old["validation_errors"] == [
        "runtime_code_delta_incomplete",
        "alias_projection_payload_contract_incomplete",
    ]
    assert plan["old_plan_status"]["status"] == [
        "CONFIRMATION BLOCKED", "DO NOT CONFIRM", "DO NOT EXECUTE",
        "SUPERSEDED BY CONTRACT-COMPLETE V2 PLAN",
    ]
    assert plan["plan_id"] != OLD_PLAN_ID


def test_authority_chain_aliases_and_counts(plan):
    assert plan["store_health"]["database_sha256_after"] == "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
    assert plan["store_health"]["event_count"] == 162
    assert plan["store_health"]["current_parent_state_count"] == 120
    assert plan["store_health"]["authority_gap"] == 0
    assert plan["store_sync_execution"]["root_execution_hash"] == "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30"
    assert plan["authority"]["active_alias_count"] == 2
    assert plan["authority"]["alias_owner_count"] == 1
    assert plan["authority"]["alias_conflict_count"] == 0
    assert plan["authority"]["missing_reviewer"] == 0
    assert plan["authority"]["missing_reviewed_at"] == 0
    assert plan["authority"]["missing_provenance"] == 0
    assert {(row["raw_alias"], row["normalized_alias"], row["parent_record_id"]) for row in plan["aliases"]} == {
        ("SLP", "slp", "商家夥伴案例資料庫:r32"),
        ("SHOPLINE Payments", "shopline payments", "商家夥伴案例資料庫:r32"),
    }


def test_projection_schema_canonicalization_and_cycle_free_hashes(plan):
    schema = plan["projection_schema"]
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert plan["canonicalization_contract"]["complete"] is True
    assert plan["canonicalization_contract"]["projection_hash_exclusion"] == "top-level projection_hash key omitted from hash input"
    assert plan["projection_template"]["generated_from_plan_id"] == "${PLAN_ID}"
    assert plan["projection_template"]["generated_from_manifest_hash"] == "${PLAN_MANIFEST_HASH}"
    assert "generated_from_plan_id" not in plan["authority_content"]
    assert "generated_at" not in plan["authority_content"]
    assert canonical_json_bytes(plan["authority_content"]) == canonical_json_bytes(
        json.loads(canonical_json_bytes(plan["authority_content"]).decode("utf-8"))
    )


def test_projection_self_hash_tamper_schema_and_stale_authority(plan):
    payload = render_projection(
        plan["projection_template"], plan_id=plan["plan_id"],
        manifest_hash=plan["manifest_hash"], generated_at=CREATED_AT,
    )
    valid = validate_projection(
        payload, plan["projection_schema"],
        decision_store_sha256=plan["manifest"]["decision_store_sha256"],
        store_sync_execution_root_hash=plan["manifest"]["store_sync_execution_root_hash"],
    )
    assert valid["valid"] is True
    assert valid["computed_projection_hash"] == payload["projection_hash"]

    cases = []
    tampered = copy.deepcopy(payload); tampered["aliases"][0]["raw_alias"] += "X"; cases.append((tampered, "projection_hash_mismatch"))
    schema = copy.deepcopy(payload); schema["schema_version"] = 2; cases.append((schema, "unsupported_schema_version"))
    stale_ds = copy.deepcopy(payload); stale_ds["authority"]["decision_store_sha256"] = "0" * 64; cases.append((stale_ds, "stale_decision_store_authority"))
    stale_sync = copy.deepcopy(payload); stale_sync["authority"]["store_sync_execution_root_hash"] = "0" * 64; cases.append((stale_sync, "stale_store_sync_authority"))
    duplicate = copy.deepcopy(payload); duplicate["aliases"].append(dict(duplicate["aliases"][0])); cases.append((duplicate, "duplicate_normalized_alias"))
    for fixture, expected in cases:
        result = validate_projection(
            fixture, plan["projection_schema"],
            decision_store_sha256=plan["manifest"]["decision_store_sha256"],
            store_sync_execution_root_hash=plan["manifest"]["store_sync_execution_root_hash"],
        )
        assert result["valid"] is False
        assert expected in result["errors"]


def test_missing_and_malformed_projection_preserve_organic_search(plan, tmp_path):
    common = {
        "schema": plan["projection_schema"],
        "decision_store_sha256": plan["manifest"]["decision_store_sha256"],
        "store_sync_execution_root_hash": plan["manifest"]["store_sync_execution_root_hash"],
    }
    missing = load_temporary_projection(tmp_path / "missing.json", **common)
    assert missing == {
        "projection": None,
        "diagnostic": "alias_projection_missing",
        "organic_search_available": True,
    }
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text('{"aliases":[', encoding="utf-8")
    malformed = load_temporary_projection(malformed_path, **common)
    assert malformed["projection"] is None
    assert malformed["diagnostic"] == "alias_projection_malformed"
    assert malformed["organic_search_available"] is True


def test_runtime_delta_is_15_of_15_with_real_or_explicit_symbols(plan):
    validation = plan["runtime_validation"]
    assert validation["valid"] is True
    assert validation["component_count"] == 15
    assert validation["complete_count"] == 15
    assert all(row["action_valid"] and row["symbols_valid"] and row["scope_hash_valid"] for row in validation["file_validation"])
    assert validation["patch_manifest_match"] is True
    assert validation["unauthorized_files"] == []
    assert validation["slack_renderer_changed"] is False
    assert validation["sqlite_schema_changed"] is False
    assert validation["vault_or_asset_changed"] is False
    assert {row["component"] for row in plan["runtime_manifest"]["components"]} == {
        "alias_projection_loader", "json_schema_validation", "projection_hash_validation",
        "decision_store_authority_binding", "store_sync_execution_binding",
        "stale_projection_detection", "normalization", "exact_alias_resolver",
        "typed_query_integration", "organic_candidate_merge", "parent_deduplication",
        "asset_deduplication", "ranking_integration", "governance_filter_placement",
        "result_caps_renderer_handoff",
    }


def test_patch_preview_rejects_unauthorized_file(plan):
    unauthorized = plan["runtime_patch_preview"] + (
        "diff --git a/src/marketing_knowledge_agent/slack_interface.py "
        "b/src/marketing_knowledge_agent/slack_interface.py\n"
    )
    observed = _validate_runtime_manifest(_root(), plan["runtime_manifest"], unauthorized)
    assert observed["valid"] is False
    assert observed["patch_manifest_match"] is False
    assert observed["slack_renderer_changed"] is True


def test_candidate_search_ranking_governance_and_asset_boundaries(plan):
    assert plan["candidate"]["valid"] is True
    assert plan["candidate"]["parent_count"] == 109
    assert plan["candidate"]["asset_count"] == 222
    assert plan["candidate"]["searchable_assets"] == 205
    assert plan["candidate"]["hold_assets"] == 1
    assert plan["candidate"]["excluded_or_blocked_assets"] == 16
    assert plan["candidate"]["restricted_leakage"] == 0
    assert plan["candidate"]["pending_leakage"] == 0
    assert plan["candidate"]["hold_leakage"] == 0
    assert plan["ranking_contract"]["parent_cap"] == 5
    assert plan["ranking_contract"]["asset_cap"] == 10
    assert plan["governance_contract"]["semantics_modified"] is False
    assert plan["asset_boundary"]["approved_url_fields"] == 410
    assert plan["asset_boundary"]["url_values_copied"] == 0


def test_slp_shopline_and_special_search_behavior(plan):
    by_query = {row["query"]: row for row in plan["offline"]}
    for query in ("SLP", "slp", "SlP", "  SLP  "):
        assert by_query[query]["alias_matched"] is True
        assert by_query[query]["asset_count"] == 3
        assert by_query[query]["parent_duplicates"] == 0
        assert by_query[query]["asset_duplicates"] == 0
    for query in ("SL", "SLPP", "SLP123", "SHOPLINE Payment", "SHOPLINE", "Payments", "請提供 SLP 的資料"):
        assert by_query[query]["alias_matched"] is False
    shopline = by_query["SHOPLINE Payments"]
    assert shopline["organic_other_parent_count"] == 15
    assert shopline["parent_count"] == 16
    assert shopline["r32_visible_within_cap"] is True
    assert by_query["莉朵花藝"]["asset_count"] == 0
    assert by_query["littlegirl"]["citation_count"] == 0
    assert by_query["廣生堂"]["asset_count"] == 1
    assert by_query["廣生堂"]["citation_count"] == 0
    assert by_query["Package+"]["asset_count"] == 3
    assert by_query["關貿網路"]["asset_count"] == 1


def test_new_identity_reports_cli_and_formal_systems_unchanged(tmp_path):
    protected = [
        _root() / "data/governance/governance_decisions.sqlite",
        _root() / "obsidian_vault/MKA",
        _root() / ".mka/content_index.sqlite",
        _root() / "src/marketing_knowledge_agent/slack_interface.py",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    reports = tmp_path / "reports"
    first = generate_production_search_alias_plan_v2(
        repo_root=_root(), output_dir=reports,
        temporary_root=tmp_path / "work-a", created_at=CREATED_AT,
    )
    first_hash = _hash_path(reports)
    second = generate_production_search_alias_plan_v2(
        repo_root=_root(), output_dir=reports,
        temporary_root=tmp_path / "work-b", created_at=CREATED_AT,
    )
    assert first["plan_id"] == second["plan_id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["plan_id"] != OLD_PLAN_ID
    assert first["execution_blocked"] is False
    assert first["validation_errors"] == []
    assert first["validation_warnings"] == []
    assert _hash_path(reports) == first_hash
    assert len([path for path in reports.iterdir() if path.is_file() and not path.name.startswith("._")]) == len(REPORT_FILENAMES) == 37
    assert {str(path): _hash_path(path) for path in protected} == before
    assert not (_root() / ".mka/search_alias_projection.json").exists()

    parser = build_parser()
    args = parser.parse_args(["plan-production-search-aliases-v2"])
    assert not hasattr(args, "force")
    assert not hasattr(args, "execute")
    assert main([
        "plan-production-search-aliases-v2",
        "--output", str(tmp_path / "cli-reports"),
        "--temporary-root", str(tmp_path / "cli-work"),
        "--created-at", CREATED_AT,
    ]) == 0
