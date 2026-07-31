from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

import marketing_knowledge_agent.production_search_alias_confirmation as confirmation
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.production_search_alias_confirmation import (
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    ProductionSearchAliasConfirmationError,
    confirm_production_search_alias_plan,
    normalize_alias,
    validate_production_search_alias_plan,
)


VALIDATED_AT = "2026-07-22T18:00:00+08:00"


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
def validation(tmp_path_factory, production_search_alias_v1_pre_activation_repo):
    root = tmp_path_factory.mktemp("production-search-alias-confirmation")
    return validate_production_search_alias_plan(
        repo_root=production_search_alias_v1_pre_activation_repo, plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        report_dir=root / "reports", temporary_root=root / "temporary",
        now=VALIDATED_AT,
    )


def test_validator_is_independent_and_reproduces_plan_identity(validation):
    source = inspect.getsource(confirmation)
    assert "from .production_search_alias_plan import" not in source
    assert validation["generator_called"] is False
    assert validation["generator_module_imported"] is False
    assert validation["reproduced_plan_id"] == EXPECTED_PLAN_ID
    assert validation["reproduced_manifest_hash"] == EXPECTED_MANIFEST_HASH
    assert validation["plan_identity_valid"] is True
    assert all(row["valid"] for row in validation["hash_validation"])


def test_authority_normalization_and_conflict_defenses(validation):
    assert validation["authority"] == {
        "valid": True,
        "active_alias_count": 2,
        "normalized_alias_count": 2,
        "alias_owner_count": 1,
        "alias_conflict_count": 0,
        "missing_reviewer": 0,
        "missing_reviewed_at": 0,
        "missing_provenance": 0,
        "revoked_or_superseded_active_alias_count": 0,
        "historical_inactive_event_count": 0,
        "alias_authority_gap": 0,
        "conflicts": {},
    }
    assert {(row["raw_alias"], row["parent_record_id"]) for row in validation["aliases"]} == {
        ("SLP", "商家夥伴案例資料庫:r32"),
        ("SHOPLINE Payments", "商家夥伴案例資料庫:r32"),
    }
    assert normalize_alias(" ＳｌＰ ") == "slp"
    assert normalize_alias("SHOPLINE   Payments") == "shopline payments"
    assert all(row["status"] == "pass" for row in validation["conflict_validation"])


def test_candidate_merge_ranking_dedupe_and_governance(validation):
    candidate = validation["candidate"]
    assert candidate["valid"] is True
    assert candidate["authoritative_parent_count"] == 120
    assert candidate["parent_count"] == 109
    assert candidate["asset_count"] == 222
    assert candidate["searchable_assets"] == 205
    assert candidate["hold_assets"] == 1
    assert candidate["excluded_or_blocked_assets"] == 16
    assert candidate["orphan_count"] == 0
    assert candidate["duplicate_parent_count"] == 0
    assert candidate["duplicate_asset_count"] == 0
    assert candidate["restricted_leakage"] == 0
    assert candidate["pending_leakage"] == 0
    assert candidate["hold_leakage"] == 0
    assert validation["merge_contract"]["parent_dedup_key"] == "record_id"
    assert validation["merge_contract"]["asset_dedup_key"] == "formal asset_id"
    assert validation["ranking_contract"]["parent_cap"] == 5
    assert validation["ranking_contract"]["asset_cap"] == 10
    assert validation["governance_contract"]["alias_overrides_governance"] is False


def test_slp_shopline_special_renderer_and_asset_boundaries(validation):
    by_query = {row["query"]: row for row in validation["offline"]}
    for query in ("SLP", "slp", "SlP", "  SLP  "):
        assert by_query[query]["alias_matched"] is True
        assert "商家夥伴案例資料庫:r32" in by_query[query]["parent_record_ids"]
        assert by_query[query]["asset_count"] == 3
    for query in ("SL", "SLPP", "SLP123", "SHOPLINE Payment", "SHOPLINE", "Payments", "請提供 SLP 的資料"):
        assert by_query[query]["alias_matched"] is False
    shopline = by_query["SHOPLINE Payments"]
    assert shopline["parent_count"] == 16
    assert shopline["organic_other_parent_count"] == 15
    assert shopline["r32_visible_within_cap"] is True
    assert shopline["parent_duplicates"] == 0
    assert shopline["asset_duplicates"] == 0
    assert by_query["莉朵花藝"]["asset_count"] == 0
    assert by_query["littlegirl"]["citation_count"] == 0
    assert by_query["廣生堂"]["asset_count"] == 1
    assert by_query["廣生堂"]["citation_count"] == 0
    assert validation["asset_boundary"] == {
        "eligible_assets": 205, "hold_assets": 1,
        "excluded_or_blocked_assets": 16, "approved_url_fields": 410,
        "asset_identity_creates": 0, "asset_identity_deletes": 0,
        "url_values_copied": 0, "parent_tags_copied_to_assets": 0,
        "aliases_copied_to_assets": 0,
    }
    assert validation["renderer"]["valid"] is True
    assert validation["renderer"]["internal_metadata_hidden"] is True
    assert validation["renderer"]["production_renderer_modified"] is False


def test_runtime_and_projection_contract_gaps_block_confirmation(validation):
    assert validation["conclusion"] == "C. Confirmation blocked"
    assert validation["valid"] is False
    assert validation["runtime_scope"]["complete"] is False
    assert "planned_functions" in validation["runtime_scope"]["missing_scope"]
    assert "checksum_validation" in validation["runtime_scope"]["missing_scope"]
    assert "stale_projection_rejection" in validation["runtime_scope"]["missing_scope"]
    assert "tests_required" in validation["runtime_scope"]["missing_scope"]
    assert validation["canonical_projection"]["contract_complete"] is False
    assert validation["validation_errors"] == [
        "runtime_code_delta_incomplete",
        "alias_projection_payload_contract_incomplete",
    ]
    assert validation["validation_warnings"] == []


def test_exact_identity_expiration_and_reviewer_fail_closed(tmp_path):
    with pytest.raises(ProductionSearchAliasConfirmationError, match="PLAN_ID"):
        validate_production_search_alias_plan(
            repo_root=_root(), plan_id="wrong", manifest_hash=EXPECTED_MANIFEST_HASH,
            report_dir=tmp_path / "reports-a", temporary_root=tmp_path / "temp-a",
            now=VALIDATED_AT,
        )
    with pytest.raises(ProductionSearchAliasConfirmationError, match="Manifest Hash"):
        validate_production_search_alias_plan(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash="0" * 64,
            report_dir=tmp_path / "reports-b", temporary_root=tmp_path / "temp-b",
            now=VALIDATED_AT,
        )
    with pytest.raises(ProductionSearchAliasConfirmationError, match="expired"):
        validate_production_search_alias_plan(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
            report_dir=tmp_path / "reports-c", temporary_root=tmp_path / "temp-c",
            now="2026-07-29T17:06:38+08:00",
        )
    with pytest.raises(ProductionSearchAliasConfirmationError, match="Admin"):
        confirm_production_search_alias_plan(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
            reviewer="Reviewer", confirmation_path=tmp_path / "confirmation",
            report_dir=tmp_path / "reports-d", temporary_root=tmp_path / "temp-d",
            confirmed_at=VALIDATED_AT, require_git_ignored=False,
        )


def test_confirmation_cli_is_blocked_and_creates_no_bundle(
    tmp_path, capsys, monkeypatch, production_search_alias_v1_pre_activation_repo
):
    monkeypatch.chdir(production_search_alias_v1_pre_activation_repo)
    confirmation_path = tmp_path / "confirmation"
    result = main([
        "validate-production-search-alias-plan",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--output", str(tmp_path / "validate-reports"),
        "--temporary-root", str(tmp_path / "validate-temp"),
        "--now", VALIDATED_AT,
    ])
    assert result == 1
    assert "runtime_code_delta_incomplete" in capsys.readouterr().out

    result = main([
        "confirm-production-search-alias-plan",
        "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--reviewer", "Admin",
        "--confirmed-at", VALIDATED_AT,
        "--confirmation-path", str(confirmation_path),
        "--output", str(tmp_path / "confirm-reports"),
        "--temporary-root", str(tmp_path / "confirm-temp"),
        "--allow-non-ignored-test-path",
    ])
    assert result == 2
    assert "confirmation blocked" in capsys.readouterr().err
    assert not confirmation_path.exists()


def test_reports_are_complete_deterministic_and_formal_systems_unchanged(
    tmp_path, production_search_alias_v1_pre_activation_repo
):
    protected = [
        _root() / "data/governance/governance_decisions.sqlite",
        _root() / "obsidian_vault/MKA",
        _root() / ".mka/content_index.sqlite",
        _root() / "src/marketing_knowledge_agent/slack_interface.py",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    reports = tmp_path / "reports"
    first = validate_production_search_alias_plan(
        repo_root=production_search_alias_v1_pre_activation_repo, plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        report_dir=reports, temporary_root=tmp_path / "temp-a", now=VALIDATED_AT,
    )
    first_hash = _hash_path(reports)
    second = validate_production_search_alias_plan(
        repo_root=production_search_alias_v1_pre_activation_repo, plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        report_dir=reports, temporary_root=tmp_path / "temp-b", now=VALIDATED_AT,
    )
    assert len([path for path in reports.iterdir() if path.is_file() and not path.name.startswith("._")]) == 28
    assert _hash_path(reports) == first_hash
    assert first["independent_validation_hash"] == second["independent_validation_hash"]
    assert first["formal_systems_unchanged"] is True
    assert second["formal_systems_unchanged"] is True
    assert {str(path): _hash_path(path) for path in protected} == before
    assert not (
        production_search_alias_v1_pre_activation_repo
        / ".mka/search_alias_projection.json"
    ).exists()
