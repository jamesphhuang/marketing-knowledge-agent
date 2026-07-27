from __future__ import annotations

import ast
import hashlib
import inspect
import json
import shutil
from pathlib import Path

import pytest

import marketing_knowledge_agent.production_search_alias_plan_v2_confirmation as confirmation
from marketing_knowledge_agent.cli import build_parser, main
from marketing_knowledge_agent.production_search_alias_plan_v2_confirmation import (
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    ProductionSearchAliasPlanV2ConfirmationError,
    confirm_production_search_alias_plan_v2,
    validate_production_search_alias_plan_v2,
    validate_production_search_alias_plan_v2_confirmation,
)


VALIDATED_AT = "2026-07-27T12:00:00+08:00"
TEST_RESULT = {
    "passed": True,
    "passed_count": 599,
    "failed": 0,
    "errors": 0,
    "skipped": 0,
    "warnings": 6,
    "duration_seconds": 1.0,
    "command": "pytest -q",
    "output_tail": "599 passed, 6 warnings",
}


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
def validation(tmp_path_factory, production_search_alias_pre_activation_repo):
    root = tmp_path_factory.mktemp("production-search-alias-v2-confirmation")
    return validate_production_search_alias_plan_v2(
        repo_root=production_search_alias_pre_activation_repo, plan_id=EXPECTED_PLAN_ID,
        manifest_hash=EXPECTED_MANIFEST_HASH,
        report_dir=root / "reports", temporary_root=root / "temporary",
        now=VALIDATED_AT,
    )


def test_validator_does_not_import_or_call_v2_generator_and_reproduces_identity(validation):
    tree = ast.parse(inspect.getsource(confirmation))
    imported_modules = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(module.endswith("production_search_alias_plan_v2") for module in imported_modules)
    assert validation["generator_imported"] is False
    assert validation["generator_called"] is False
    assert validation["reproduced_plan_id"] == EXPECTED_PLAN_ID
    assert validation["reproduced_manifest_hash"] == EXPECTED_MANIFEST_HASH
    assert validation["plan_identity_valid"] is True
    assert validation["valid"] is True
    assert validation["validation_errors"] == []
    assert validation["validation_warnings"] == []


def test_authority_projection_and_non_circular_hash_contract(validation):
    assert validation["authority"] == {
        "valid": True, "active_alias_count": 2, "normalized_alias_count": 2,
        "alias_owner_count": 1, "alias_conflict_count": 0,
        "missing_reviewer": 0, "missing_reviewed_at": 0, "missing_provenance": 0,
        "revoked_or_superseded_active_alias_count": 0,
        "historical_inactive_event_count": 0, "alias_authority_gap": 0,
        "conflicts": {},
    }
    assert validation["hashes"]["projection_schema_hash"] == "062d45607dd8bdd436d19f7aad776ce28eb88f3b5a28ba32f381281ce97e152f"
    authority = validation["authority_content"]
    assert not {"generated_from_plan_id", "generated_from_manifest_hash", "generated_at", "projection_hash"}.intersection(authority)
    assert validation["projection_template"]["generated_from_plan_id"] == "${PLAN_ID}"
    assert validation["projection_template"]["projection_hash"] == "${SELF_EXCLUDING_SHA256}"
    assert validation["canonicalization_contract"]["projection_hash_exclusion"] == "top-level projection_hash key omitted from hash input"


def test_runtime_delta_is_exactly_15_of_15_and_scoped(validation):
    runtime = validation["runtime_validation"]
    assert runtime["valid"] is True
    assert runtime["component_count"] == runtime["complete_count"] == 15
    assert runtime["patch_manifest_match"] is True
    assert runtime["manifest_files"] == [
        "src/marketing_knowledge_agent/pipeline.py",
        "src/marketing_knowledge_agent/search_aliases.py",
        "tests/test_production_search_alias_runtime.py",
    ]
    assert runtime["unauthorized_files"] == []
    assert runtime["slack_renderer_changed"] is False
    assert runtime["sqlite_schema_changed"] is False
    assert runtime["vault_or_asset_changed"] is False
    assert all(all(row[key] for key in ("action_ok", "state_ok", "checksum_ok", "scope_hash_ok", "symbols_ok")) for row in runtime["file_validation"])


def test_loader_failures_candidate_governance_and_search_matrix(validation):
    assert all(row["status"] == "pass" for row in validation["projection_failure_tests"])
    candidate = validation["candidate"]
    assert candidate["parent_count"] == 109
    assert candidate["asset_count"] == 222
    assert candidate["searchable_assets"] == 205
    assert candidate["hold_assets"] == 1
    assert candidate["excluded_or_blocked_assets"] == 16
    assert candidate["restricted_leakage"] == 0
    assert candidate["pending_leakage"] == 0
    assert candidate["hold_leakage"] == 0
    by_query = {row["query"]: row for row in validation["offline"]}
    for query in ("SLP", "slp", "SlP", "  SLP  "):
        assert by_query[query]["alias_matched"] is True
        assert by_query[query]["asset_count"] == 3
    for query in ("SL", "SLPP", "SLP123"):
        assert by_query[query]["alias_matched"] is False
    shopline = by_query["SHOPLINE Payments"]
    assert shopline["parent_count"] == 16
    assert shopline["organic_other_parent_count"] == 15
    assert shopline["r32_visible_within_cap"] is True
    assert shopline["parent_duplicates"] == shopline["asset_duplicates"] == 0
    assert validation["ranking_contract"]["parent_cap"] == 5
    assert validation["ranking_contract"]["asset_cap"] == 10
    assert validation["asset_boundary"]["approved_url_fields"] == 410


def test_exact_identity_expiration_reviewer_and_existing_target_fail_closed(tmp_path):
    common = dict(
        repo_root=_root(), report_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary", now=VALIDATED_AT,
    )
    with pytest.raises(ProductionSearchAliasPlanV2ConfirmationError, match="PLAN_ID"):
        validate_production_search_alias_plan_v2(plan_id="wrong", manifest_hash=EXPECTED_MANIFEST_HASH, **common)
    with pytest.raises(ProductionSearchAliasPlanV2ConfirmationError, match="Manifest Hash"):
        validate_production_search_alias_plan_v2(plan_id=EXPECTED_PLAN_ID, manifest_hash="0" * 64, **common)
    with pytest.raises(ProductionSearchAliasPlanV2ConfirmationError, match="expired"):
        validate_production_search_alias_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
            report_dir=tmp_path / "expired-reports", temporary_root=tmp_path / "expired-temp",
            now="2026-07-29T17:57:01+08:00",
        )
    with pytest.raises(ProductionSearchAliasPlanV2ConfirmationError, match="Admin"):
        confirm_production_search_alias_plan_v2(
            repo_root=_root(), plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
            reviewer="Reviewer", confirmation_path=tmp_path / "bad-reviewer",
        )


def test_tampered_runtime_manifest_blocks_validation(
    tmp_path, production_search_alias_pre_activation_repo
):
    copied = tmp_path / "plan"
    shutil.copytree(_root() / "reports/production_search_alias_plan_v2", copied)
    path = copied / "runtime_code_delta_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["files"][0]["file_path"] = "src/marketing_knowledge_agent/slack_interface.py"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = validate_production_search_alias_plan_v2(
        repo_root=production_search_alias_pre_activation_repo,
        plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
        plan_dir=copied, report_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary", now=VALIDATED_AT,
    )
    assert result["valid"] is False
    assert "runtime_delta_15_of_15" in result["validation_errors"]
    assert "plan_static_identity" in result["validation_errors"]


def test_confirmation_atomic_idempotent_and_conflicting_content_rejected(
    tmp_path, production_search_alias_pre_activation_repo
):
    target = tmp_path / "confirmation"
    kwargs = dict(
        repo_root=production_search_alias_pre_activation_repo,
        plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
        reviewer="Admin", confirmed_at=VALIDATED_AT, confirmation_path=target,
        report_dir=tmp_path / "reports", temporary_root=tmp_path / "temporary",
        require_git_ignored=False, test_runner=lambda root: dict(TEST_RESULT),
    )
    first = confirm_production_search_alias_plan_v2(**kwargs)
    assert first["confirmation_created"] is True
    assert first["idempotent_noop"] is False
    bundle = validate_production_search_alias_plan_v2_confirmation(target)
    assert bundle["valid"] is True
    assert bundle["protected_file_count"] == 18
    second = confirm_production_search_alias_plan_v2(**kwargs)
    assert second["confirmation_created"] is False
    assert second["idempotent_noop"] is True
    assert second["root_confirmation_hash"] == first["root_confirmation_hash"]

    contract = target / "ranking_contract.json"
    contract.write_text(contract.read_text(encoding="utf-8").replace("5", "6", 1), encoding="utf-8")
    with pytest.raises(ProductionSearchAliasPlanV2ConfirmationError, match="checksum"):
        confirm_production_search_alias_plan_v2(**kwargs)


def test_validation_reports_are_deterministic_and_formal_systems_unchanged(
    tmp_path, production_search_alias_pre_activation_repo
):
    protected = [
        _root() / "data/governance/governance_decisions.sqlite",
        _root() / "obsidian_vault/MKA",
        _root() / ".mka/content_index.sqlite",
        _root() / "src/marketing_knowledge_agent/pipeline.py",
        _root() / "src/marketing_knowledge_agent/slack_interface.py",
        _root() / "reports/production_search_alias_plan_v2",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    reports = tmp_path / "reports"
    first = validate_production_search_alias_plan_v2(
        repo_root=production_search_alias_pre_activation_repo,
        plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
        report_dir=reports, temporary_root=tmp_path / "temp-a", now=VALIDATED_AT,
    )
    report_hash = _hash_path(reports)
    second = validate_production_search_alias_plan_v2(
        repo_root=production_search_alias_pre_activation_repo,
        plan_id=EXPECTED_PLAN_ID, manifest_hash=EXPECTED_MANIFEST_HASH,
        report_dir=reports, temporary_root=tmp_path / "temp-b", now=VALIDATED_AT,
    )
    assert _hash_path(reports) == report_hash
    assert first["independent_validation_hash"] == second["independent_validation_hash"]
    assert first["formal_systems_unchanged"] is True
    assert {str(path): _hash_path(path) for path in protected} == before
    assert not (
        production_search_alias_pre_activation_repo
        / ".mka/search_alias_projection.json"
    ).exists()


def test_v2_cli_is_validation_confirmation_only(
    tmp_path, capsys, monkeypatch, production_search_alias_pre_activation_repo
):
    parser = build_parser()
    validate_args = parser.parse_args([
        "validate-production-search-alias-plan-v2", "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
    ])
    confirm_args = parser.parse_args([
        "confirm-production-search-alias-plan-v2", "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH, "--reviewer", "Admin",
    ])
    for args in (validate_args, confirm_args):
        assert not hasattr(args, "force")
        assert not hasattr(args, "execute")
        assert not hasattr(args, "skip_validation")
    monkeypatch.chdir(production_search_alias_pre_activation_repo)
    assert main([
        "validate-production-search-alias-plan-v2", "--plan-id", EXPECTED_PLAN_ID,
        "--manifest-hash", EXPECTED_MANIFEST_HASH,
        "--output", str(tmp_path / "reports"),
        "--temporary-root", str(tmp_path / "temporary"), "--now", VALIDATED_AT,
    ]) == 0
    assert '"valid": true' in capsys.readouterr().out
