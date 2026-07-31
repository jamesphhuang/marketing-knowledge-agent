from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import marketing_knowledge_agent.cli as cli
import marketing_knowledge_agent.store_data_sync_plan_v2 as sync_v2
from conftest import managed_fixture_workspace
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.store_data_sync_plan_v2 import (
    AUDIT_ONLY_FIELDS,
    OLD_PLAN_ID,
    REPORT_FILENAMES,
    generate_store_data_sync_plan_v2,
)
from marketing_knowledge_agent.store_data_sync_existing_validation import (
    reconstruct_pre_sync_fixture,
)


CREATED_AT = "2026-07-21T12:00:00+08:00"


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
    root = tmp_path_factory.mktemp("store-sync-v2")
    prestate = _root() / "reports" / ".test-fixtures" / root.name
    with managed_fixture_workspace(prestate):
        fixture = reconstruct_pre_sync_fixture(_root(), prestate)
        yield generate_store_data_sync_plan_v2(
            repo_root=_root(),
            output_dir=root / "reports",
            temporary_root=root / "temporary",
            created_at=CREATED_AT,
            managed_vault_root=Path(fixture["managed_vault_root"]),
            formal_sqlite_path=Path(fixture["formal_sqlite_path"]),
        )


@pytest.fixture
def repo_prestate(tmp_path):
    prestate = _root() / "reports" / ".test-fixtures" / tmp_path.name
    with managed_fixture_workspace(prestate):
        fixture = reconstruct_pre_sync_fixture(_root(), prestate)
        yield fixture


def test_materialization_contract_excludes_decision_event_audit_fields(result):
    matrix = {row["field_name"]: row for row in result["field_materialization_matrix"]}

    assert set(AUDIT_ONLY_FIELDS) == {
        "decision_event_id",
        "decision_event_hash",
        "decision_reviewer",
        "decision_reviewed_at",
        "decision_provenance",
    }
    for field in AUDIT_ONLY_FIELDS:
        assert matrix[field]["materialization_status"] == "audit_only"
        assert matrix[field]["managed_vault"] is False
        assert matrix[field]["formal_sqlite"] is False
        assert matrix[field]["production_search"] is False
        assert matrix[field]["triggers_write"] is False
        assert all(field not in row for row in result["managed_vault_projection"])
        assert all(field not in row for row in result["formal_sqlite_projection"])

    assert matrix["search_alias_reviewed_by"]["managed_vault"] is True
    assert matrix["search_alias_reviewed_by"]["materialization_status"] == "materialize_managed_vault"
    assert result["target_allowlists_distinct"] is True


def test_full_reconciliation_and_target_specific_deltas(result):
    assert result["authoritative_record_count"] == 120
    assert result["reconciliation_count"] == 120
    assert result["action_counts"] == {
        "create": 4,
        "update": 106,
        "no_change": 0,
        "remove_from_content_projection": 0,
        "retain_governance_only": 10,
        "blocked": 0,
        "manual_review": 0,
    }
    assert all(row["necessary_materialized_diff_count"] > 0 for row in result["reconciliation"] if row["proposed_action"] == "update")
    assert result["managed_vault_counts"] == {
        "existing": 106,
        "create": 4,
        "update": 106,
        "no_change": 0,
        "governance_only": 10,
        "target": 110,
        "content_parent": 109,
        "vault_only": 1,
    }
    assert result["formal_sqlite_counts"] == {
        "existing": 105,
        "create": 4,
        "update": 0,
        "no_change": 105,
        "remove": 0,
        "not_projected": 11,
        "target": 109,
    }
    assert result["managed_vault_delta_hash"] != result["formal_sqlite_delta_hash"]
    assert not any(
        field in AUDIT_ONLY_FIELDS
        for row in result["managed_vault_delta"]
        for field in row["changed_fields"]
    )
    assert not any(
        field in AUDIT_ONLY_FIELDS
        for row in result["formal_sqlite_delta"]
        for field in row["changed_fields"]
    )


def test_governance_only_r20_creates_and_special_boundaries(result):
    assert {row["record_id"] for row in result["governance_only_records"]} == {
        f"商家夥伴案例資料庫:{value}"
        for value in ("r30", "r57", "r83", "r87", "r101", "r102", "r103", "r107", "r116", "r121")
    }
    assert result["r20_vault_only_valid"] is True
    assert {row["record_id"] for row in result["four_create_records"]} == {
        "商家夥伴案例資料庫:r7",
        "商家夥伴案例資料庫:r12",
        "商家夥伴案例資料庫:r32",
        "商家夥伴案例資料庫:r122",
    }
    assert all(row["audit_only_fields_absent"] for row in result["four_create_records"])
    assert all(row["status"] == "pass" for row in result["special_record_validation"])
    assert result["asset_boundary"]["eligible_assets"] == 205
    assert result["asset_boundary"]["hold_assets"] == 1
    assert result["asset_boundary"]["excluded_or_blocked_assets"] == 16
    assert result["asset_boundary"]["approved_url_fields"] == 410


def test_candidate_and_new_plan_identity_are_valid(result):
    assert result["candidate_validation"]["managed_vault_parents"] == 110
    assert result["candidate_validation"]["content_parents"] == 109
    assert result["candidate_validation"]["audit_only_managed_columns"] == 0
    assert result["candidate_validation"]["audit_only_formal_columns"] == 0
    assert result["offline_search"]["SHOPLINE Payments"]["record_count"] == 16
    assert result["plan_id"] != OLD_PLAN_ID
    assert result["execution_blocked"] is False
    assert result["blocker_reasons"] == []
    assert result["conclusion"] == "A. Ready for corrected store data sync plan confirmation"
    assert result["old_plan_status"] == [
        "VALIDATION BLOCKED",
        "DO NOT CONFIRM",
        "DO NOT EXECUTE",
        "SUPERSEDED BY MATERIALIZATION-BOUNDARY FIX",
    ]
    assert result["asset_boundary"]["url_values_copied"] == 0
    assert result["asset_boundary"]["parent_tags_copied_to_assets"] == 0
    assert result["asset_boundary"]["aliases_copied_to_assets"] == 0


def test_unsupported_formal_sqlite_field_fails_closed(tmp_path, repo_prestate, monkeypatch):
    original = sync_v2._field_materialization_matrix

    def matrix_with_unsupported_field():
        return original() + [{
            "field_name": "future_required_search_field",
            "authoritative_source": "decision_store_current_parent_decisions",
            "decision_store": True,
            "audit_bundle": False,
            "managed_vault": False,
            "formal_sqlite": False,
            "production_search": True,
            "required_consumer": "future search consumer",
            "safe_to_materialize": False,
            "triggers_write": False,
            "materialization_status": "unsupported_requires_schema_plan",
            "formal_storage_key": "",
            "reason": "schema support is intentionally absent",
        }]

    monkeypatch.setattr(sync_v2, "_field_materialization_matrix", matrix_with_unsupported_field)
    blocked = generate_store_data_sync_plan_v2(
        repo_root=_root(), output_dir=tmp_path / "reports",
        temporary_root=tmp_path / "temporary", created_at=CREATED_AT,
        managed_vault_root=Path(repo_prestate["managed_vault_root"]),
        formal_sqlite_path=Path(repo_prestate["formal_sqlite_path"]),
    )

    assert blocked["execution_blocked"] is True
    assert "formal_sqlite_schema_support_missing" in blocked["blocker_reasons"]


def test_reports_and_rerun_are_deterministic(tmp_path, repo_prestate):
    output = tmp_path / "reports"
    args = {
        "repo_root": _root(),
        "output_dir": output,
        "temporary_root": tmp_path / "temporary-a",
        "created_at": CREATED_AT,
        "managed_vault_root": Path(repo_prestate["managed_vault_root"]),
        "formal_sqlite_path": Path(repo_prestate["formal_sqlite_path"]),
    }
    first = generate_store_data_sync_plan_v2(**args)
    first_hash = _hash_path(output)
    args["temporary_root"] = tmp_path / "temporary-b"
    second = generate_store_data_sync_plan_v2(**args)

    assert first["plan_id"] == second["plan_id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["managed_vault_delta_hash"] == second["managed_vault_delta_hash"]
    assert first["formal_sqlite_delta_hash"] == second["formal_sqlite_delta_hash"]
    assert _hash_path(output) == first_hash
    assert {path.name for path in output.iterdir() if path.is_file()} == set(REPORT_FILENAMES)


def test_cli_is_plan_only(tmp_path, repo_prestate, monkeypatch, capsys):
    original = cli.generate_store_data_sync_plan_v2

    def generate_from_prestate(**kwargs):
        kwargs["managed_vault_root"] = Path(repo_prestate["managed_vault_root"])
        kwargs["formal_sqlite_path"] = Path(repo_prestate["formal_sqlite_path"])
        return original(**kwargs)

    monkeypatch.setattr(cli, "generate_store_data_sync_plan_v2", generate_from_prestate)
    exit_code = main([
        "plan-store-data-sync-v2",
        "--output", str(tmp_path / "reports"),
        "--temporary-root", str(tmp_path / "temporary"),
        "--created-at", CREATED_AT,
    ])

    assert exit_code == 0
    assert "Ready for corrected store data sync plan confirmation" in capsys.readouterr().out


def test_formal_systems_are_unchanged(result):
    assert result["formal_systems_unchanged"] is True
    assert result["decision_store_validation"]["database_unchanged"] is True
