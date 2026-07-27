from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import marketing_knowledge_agent.production_search_alias_plan as module
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.production_search_alias_plan import (
    EXPECTED_DECISION_STORE_SHA256,
    EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH,
    ProductionSearchAliasPlanError,
    REPORT_FILENAMES,
    generate_production_search_alias_plan,
    normalize_alias,
    resolve_exact_alias,
    validate_alias_authority,
)


CREATED_AT = "2026-07-22T18:00:00+08:00"
PRE_ACTIVATION_ALIAS_TARGET = Path(
    ".mka/search_alias_projection.preactivation-fixture.json"
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _hash_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    path = tmp_path_factory.mktemp("production-search-alias-plan")
    return generate_production_search_alias_plan(
        repo_root=_root(), output_dir=path / "reports",
        temporary_root=path / "temporary", created_at=CREATED_AT,
        alias_target_path=PRE_ACTIVATION_ALIAS_TARGET,
    )


def test_exact_authority_chain_and_formal_inputs_are_required(result):
    assert result["manifest"]["decision_store_sha256"] == EXPECTED_DECISION_STORE_SHA256
    assert result["manifest"]["store_sync_execution_root_hash"] == EXPECTED_STORE_SYNC_EXECUTION_ROOT_HASH
    assert result["manifest"]["authoritative_parent_count"] == 120
    assert result["manifest"]["content_parent_count"] == 109
    assert result["store_validation"]["integrity_check"] == "ok"
    assert result["store_validation"]["foreign_key_errors"] == 0
    assert result["store_validation"]["event_count"] == 162
    assert result["formal_systems_unchanged"] is True


def test_alias_authority_is_loaded_from_current_state_with_complete_metadata(result):
    authority = result["authority"]
    assert authority["approved_alias_count"] == 2
    assert authority["normalized_alias_count"] == 2
    assert authority["alias_owner_count"] == 1
    assert authority["alias_conflict_count"] == 0
    assert authority["missing_reviewer_metadata"] == 0
    assert authority["missing_provenance"] == 0
    assert authority["alias_authority_gap"] == 0
    assert {row["raw_alias"] for row in result["aliases"]} == {"SLP", "SHOPLINE Payments"}
    assert all(row["parent_record_id"] == "商家夥伴案例資料庫:r32" for row in result["aliases"])
    assert result["managed_alias"]["valid"] is True


def test_normalization_is_exact_casefolded_and_not_expansive():
    assert normalize_alias("SLP") == normalize_alias(" slp ") == normalize_alias("ＳＬＰ") == "slp"
    assert normalize_alias("SHOPLINE   Payments") == "shopline payments"
    assert normalize_alias("SHOPLINE Payment") != normalize_alias("SHOPLINE Payments")
    assert normalize_alias("SL") != normalize_alias("SLP")
    assert normalize_alias("SLPP") != normalize_alias("SLP")
    assert normalize_alias("SLP123") != normalize_alias("SLP")


def test_conflict_missing_metadata_revoked_excluded_and_internal_only_fail_closed(result):
    aliases = result["aliases"]
    parents, _, history = module._load_authority(_root() / "data/governance/governance_decisions.sqlite")
    conflict = [dict(row) for row in aliases] + [{**aliases[0], "parent_record_id": "商家夥伴案例資料庫:r12"}]
    with pytest.raises(ProductionSearchAliasPlanError, match="conflict"):
        resolve_exact_alias("SLP", conflict, parents)
    missing = [{**aliases[0], "reviewer": "", "provenance": ""}]
    assert validate_alias_authority(missing, parents, history)["valid"] is False
    assert resolve_exact_alias("SLP", [{**aliases[0], "active": False}], parents) == []
    excluded = {**parents, aliases[0]["parent_record_id"]: {**parents[aliases[0]["parent_record_id"]], "can_enter_content_index": False}}
    assert resolve_exact_alias("SLP", aliases, excluded) == []
    internal = {**parents, aliases[0]["parent_record_id"]: {**parents[aliases[0]["parent_record_id"]], "can_external_reference": False}}
    assert resolve_exact_alias("SLP", aliases, internal) == ["商家夥伴案例資料庫:r32"]
    assert internal["商家夥伴案例資料庫:r32"]["can_external_reference"] is False


def test_temporary_candidate_preserves_parent_asset_and_governance_counts(result):
    candidate = result["candidate"]
    assert candidate["authoritative_parent_count"] == 120
    assert candidate["parent_count"] == 109
    assert candidate["asset_count"] == 222
    assert candidate["searchable_assets"] == 205
    assert candidate["hold_assets"] == 1
    assert candidate["excluded_or_blocked_assets"] == 16
    assert candidate["alias_ownership_count"] == 2
    assert candidate["orphan_count"] == candidate["duplicate_parent_count"] == 0
    assert candidate["restricted_leakage"] == candidate["pending_leakage"] == 0
    assert candidate["hold_leakage"] == candidate["external_reference_leakage"] == 0
    assert result["asset_boundary"]["approved_url_fields"] == 410
    assert result["asset_boundary"]["asset_identity_creates"] == 0
    assert result["asset_boundary"]["asset_identity_deletes"] == 0


def test_slp_and_shopline_payments_merge_rank_and_dedupe(result):
    rows = {row["query"]: row for row in result["offline"]}
    for query in ("SLP", "slp", "SlP", "  SLP  "):
        assert rows[query]["alias_matched"] is True
        assert rows[query]["parent_record_ids"] == ["商家夥伴案例資料庫:r32"]
        assert rows[query]["asset_count"] == 3
        assert rows[query]["parent_duplicates"] == rows[query]["asset_duplicates"] == 0
    for query in ("SL", "SLPP", "SLP123", "SHOPLINE Payment", "SHOPLINE", "Payments", "請提供 SLP 的資料"):
        assert rows[query]["alias_matched"] is False
    shopline = rows["SHOPLINE Payments"]
    assert shopline["parent_count"] == 16
    assert shopline["organic_other_parent_count"] == 15
    assert shopline["alias_owner_record_ids"] == ["商家夥伴案例資料庫:r32"]
    assert shopline["r32_visible_within_cap"] is True
    assert shopline["parent_duplicates"] == shopline["asset_duplicates"] == 0
    assert len(shopline["visible_parent_record_ids"]) == 5
    assert shopline["visible_asset_count"] <= 10


def test_special_records_and_renderer_boundary(result):
    rows = {row["query"]: row for row in result["offline"]}
    assert rows["莉朵花藝"]["asset_count"] == rows["littlegirl"]["citation_count"] == 0
    assert rows["廣生堂"]["asset_count"] == 1
    assert rows["廣生堂"]["citation_count"] == 0
    assert rows["111gsttest"]["asset_count"] == 1
    assert rows["Package+"]["asset_count"] == 3
    assert rows["關貿網路"]["asset_count"] == 1
    assert result["renderer"]["valid"] is True
    assert result["renderer"]["displayed_parent_count"] <= 5
    assert result["renderer"]["displayed_asset_count"] <= 10
    assert result["renderer"]["internal_metadata_hidden"] is True
    assert result["renderer"]["approved_title_urls_only"] is True
    assert result["renderer"]["url_values_written_to_alias_projection"] == 0
    assert result["renderer"]["production_renderer_modified"] is False


def test_projection_strategy_is_incremental_without_schema_migration(result):
    selected = [row for row in result["architecture"] if row["selected"]]
    assert [row["option"] for row in selected] == ["C"]
    assert result["strategy"]["strategy"] == "independent_governed_json_alias_projection"
    assert result["strategy"]["schema_migration_required"] is False
    assert result["strategy"]["index_rebuild_required"] is False
    assert result["strategy"]["existing_parent_rows_modified"] == 0
    assert result["projection_delta"]["create_count"] == 2
    assert result["strategy"]["target_paths"]["alias_projection"] == ".mka/search_alias_projection.json"
    assert result["execution_blocked"] is False
    assert result["conclusion"].startswith("A.")


def test_unconfirmed_schema_migration_requirement_blocks_plan(
    tmp_path, monkeypatch
):
    original = module._selected_strategy

    def migration_strategy():
        value = original()
        value["schema_migration_required"] = True
        value["schema_migration_prerequisite"] = "independent_search_alias_schema_migration_plan"
        return value

    monkeypatch.setattr(module, "_selected_strategy", migration_strategy)
    blocked = generate_production_search_alias_plan(
        repo_root=_root(), output_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary", created_at=CREATED_AT,
        alias_target_path=PRE_ACTIVATION_ALIAS_TARGET,
    )
    assert blocked["execution_blocked"] is True
    assert "schema_migration_not_required" in blocked["blocker_reasons"]


def test_exact_database_sha_and_absent_target_fail_closed(tmp_path):
    wrong = tmp_path / "wrong.sqlite"
    wrong.write_bytes(b"not the formal decision store")
    with pytest.raises(ProductionSearchAliasPlanError, match="SHA-256"):
        generate_production_search_alias_plan(
            repo_root=_root(), output_dir=tmp_path / "reports-a",
            temporary_root=tmp_path / "temporary-a", decision_store_path=wrong,
            alias_target_path=PRE_ACTIVATION_ALIAS_TARGET,
            created_at=CREATED_AT,
        )
    target = tmp_path / "aliases.json"
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ProductionSearchAliasPlanError, match="already exists"):
        generate_production_search_alias_plan(
            repo_root=_root(), output_dir=tmp_path / "reports-b",
            temporary_root=tmp_path / "temporary-b", alias_target_path=target,
            created_at=CREATED_AT,
        )


def test_plan_identity_reports_and_rerun_are_deterministic(
    tmp_path
):
    reports = tmp_path / "reports"
    first = generate_production_search_alias_plan(
        repo_root=_root(), output_dir=reports,
        temporary_root=tmp_path / "temporary-a", created_at=CREATED_AT,
        alias_target_path=PRE_ACTIVATION_ALIAS_TARGET,
    )
    first_hash = _hash_path(reports)
    second = generate_production_search_alias_plan(
        repo_root=_root(), output_dir=reports,
        temporary_root=tmp_path / "temporary-b", created_at=CREATED_AT,
        alias_target_path=PRE_ACTIVATION_ALIAS_TARGET,
    )
    assert first["plan_id"] == second["plan_id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert _hash_path(reports) == first_hash
    assert {path.name for path in reports.iterdir() if path.is_file()} == set(REPORT_FILENAMES)
    manifest = json.loads((reports / "production_search_alias_plan_manifest.json").read_text(encoding="utf-8"))
    assert manifest["expires_at"] == "2026-07-29T18:00:00+08:00"


def test_cli_is_plan_only_and_does_not_require_slack_token(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    assert main([
        "plan-production-search-aliases",
        "--output", str(tmp_path / "reports"),
        "--temporary-root", str(tmp_path / "temporary"),
        "--created-at", CREATED_AT,
    ]) == 2
    assert "already exists" in capsys.readouterr().err
