from __future__ import annotations

import hashlib
from pathlib import Path

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.store_data_sync_existing_validation import (
    EXPECTED_BACKUP_ID,
    EXPECTED_BACKUP_ROOT_HASH,
    EXPECTED_EXECUTION_ID,
    EXPECTED_EXECUTION_ROOT_HASH,
    REPORT_FILENAMES,
    reconstruct_pre_sync_fixture,
    validate_existing_store_data_sync,
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


def test_authority_chain_and_decision_store_are_exact(tmp_path):
    result = validate_existing_store_data_sync(
        repo_root=_root(), report_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary",
    )

    assert result["execution_id"] == EXPECTED_EXECUTION_ID
    assert result["execution_root_hash"] == EXPECTED_EXECUTION_ROOT_HASH
    assert result["backup_id"] == EXPECTED_BACKUP_ID
    assert result["backup_root_hash"] == EXPECTED_BACKUP_ROOT_HASH
    assert all(result["authority_chain"].values())
    assert result["decision_store"]["sha256_before"] == result["decision_store"]["sha256_after"]
    assert result["decision_store"]["event_count"] == 162
    assert result["decision_store"]["current_parent_state"] == 120
    assert result["decision_store"]["authority_gap"] == 0
    assert result["decision_store"]["hash_chain_valid"] is True


def test_managed_and_formal_post_sync_results_are_independent(tmp_path):
    result = validate_existing_store_data_sync(
        repo_root=_root(), report_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary",
    )
    managed = result["managed_vault"]
    formal = result["formal_sqlite"]

    assert managed["parent_count"] == managed["unique_record_id_count"] == 110
    assert managed["content_parent_count"] == 109
    assert managed["create_count"] == 4
    assert managed["update_count"] == 106
    assert managed["body_content_preserved"] is True
    assert managed["unknown_frontmatter_preserved"] is True
    assert managed["audit_only_occurrences"] == 0
    assert formal["parent_count"] == 109
    assert formal["created_rows"] == [7, 12, 32, 122]
    assert formal["existing_105_document_rows_unchanged"] is True
    assert formal["existing_105_metadata_json_unchanged"] is True
    assert formal["existing_105_chunks_unchanged"] is True
    assert formal["existing_105_fts_unchanged"] is True
    assert formal["schema_unchanged"] is True
    assert formal["new_chunk_rows"] == formal["new_fts_rows"] == 4


def test_governance_special_asset_and_materialization_boundaries(tmp_path):
    result = validate_existing_store_data_sync(
        repo_root=_root(), report_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary",
    )

    assert result["managed_vault"]["governance_only_absent"] is True
    assert result["managed_vault"]["vault_only_rows"] == [20]
    assert result["managed_vault"]["audit_only_occurrences"] == 0
    assert result["formal_sqlite"]["audit_only_occurrences"] == 0
    assert all(row["status"] == "pass" for row in result["special_records"])
    assert result["asset_url_boundary"]["valid"] is True
    assert result["asset_url_boundary"]["approved_url_fields"] == 410
    assert result["production_search_alias_activated"] is False


def test_appledouble_contract_allows_only_exact_target_metadata(tmp_path):
    result = validate_existing_store_data_sync(
        repo_root=_root(), report_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary",
    )
    checks = {row["check"]: row["status"] for row in result["appledouble_boundary"]}

    assert checks == {
        "managed_namespace_appledouble_allowed": "pass",
        "exact_plan_staging_companion_allowed": "pass",
        "manifest_target_sidecar_allowed": "pass",
        "target_sidecar_still_changes_content_checksum": "pass",
        "unrelated_appledouble_rejected": "pass",
        "unrelated_content_drift_rejected": "pass",
    }


def test_backup_reconstructs_exact_pre_sync_plan_identity(tmp_path):
    fixture = reconstruct_pre_sync_fixture(_root(), tmp_path / "prestate")

    assert fixture["valid"] is True
    assert fixture["managed_parent_count"] == 106
    assert fixture["formal_parent_count"] == 105
    assert fixture["create_rows_absent"] is True
    assert fixture["plan_identity_reproduced"] is True
    assert fixture["managed_delta_hash"] == "a329349aa7c37f0ca5f750ebb059377ab1a0d08c63fc12d6ad052cbaec82adf1"
    assert fixture["formal_delta_hash"] == "0151ebd95c44e1f77e95027c1a438fb40764db4943c3aa78681e62a522179abb"


def test_completed_execute_is_rejected_without_formal_changes(tmp_path):
    protected = [
        _root() / "data/governance/governance_decisions.sqlite",
        _root() / "data/governance/backups/store-data-sync-plan-v2-4c8eb2a08b399da4",
        _root() / "data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4",
        _root() / "obsidian_vault/MKA",
        _root() / ".mka/content_index.sqlite",
    ]
    before = {str(path): _hash_path(path) for path in protected}
    result = validate_existing_store_data_sync(
        repo_root=_root(), report_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary",
    )

    assert result["execute_rerun"]["valid"] is True
    assert "Execution Bundle already exists" in result["execute_rerun"]["reason"]
    assert {str(path): _hash_path(path) for path in protected} == before


def test_reports_are_complete_and_deterministic(tmp_path):
    reports = tmp_path / "reports"
    first = validate_existing_store_data_sync(
        repo_root=_root(), report_dir=reports, temporary_root=tmp_path / "temporary-a",
    )
    first_hash = _hash_path(reports)
    second = validate_existing_store_data_sync(
        repo_root=_root(), report_dir=reports, temporary_root=tmp_path / "temporary-b",
    )

    assert first["conclusion"] == second["conclusion"]
    assert _hash_path(reports) == first_hash
    assert {path.name for path in reports.iterdir() if path.is_file()} == set(REPORT_FILENAMES)


def test_read_only_cli_has_no_repair_or_override_flags(tmp_path, capsys):
    assert main([
        "validate-existing-store-data-sync",
        "--output", str(tmp_path / "reports"),
        "--temporary-root", str(tmp_path / "temporary"),
    ]) == 0
    assert "independently validated" in capsys.readouterr().out
