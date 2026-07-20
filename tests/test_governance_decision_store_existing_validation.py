import hashlib
import json
import shutil
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance_decision_store_existing_validation import (
    EXPECTED_DATABASE_SHA256,
    EXPECTED_DATABASE_SIZE,
    ExistingGovernanceDecisionStoreValidationError,
    _compare_schema_objects,
    validate_existing_governance_decision_store,
)


def test_existing_formal_store_is_independently_validated(tmp_path):
    result = validate_existing_governance_decision_store(**_args(tmp_path))

    assert result["conclusion"] == "A. Existing Decision Store independently validated"
    assert result["database_sha256_before"] == EXPECTED_DATABASE_SHA256
    assert result["database_sha256_after"] == EXPECTED_DATABASE_SHA256
    assert result["database_size_before"] == EXPECTED_DATABASE_SIZE
    assert result["database_size_after"] == EXPECTED_DATABASE_SIZE
    assert result["event_count"] == 162
    assert result["current_parent_state_count"] == 120
    assert result["authority_gap"] == 0
    assert result["integrity_check"] == "ok"
    assert result["foreign_key_errors"] == 0
    assert result["formal_database_unchanged"] is True


def test_exact_database_sha_and_size_are_required(tmp_path):
    database = tmp_path / "tampered.sqlite"
    shutil.copy2(_root() / "data/governance/governance_decisions.sqlite", database)
    database.write_bytes(database.read_bytes() + b"x")
    args = _args(tmp_path)
    args["database_path"] = database
    with pytest.raises(ExistingGovernanceDecisionStoreValidationError, match="SHA-256|byte size"):
        validate_existing_governance_decision_store(**args)


def test_execution_authority_chain_and_schema_match(tmp_path):
    result = validate_existing_governance_decision_store(**_args(tmp_path))

    assert all(result["authority_chain"].values())
    assert result["execution_bundle"]["root_execution_hash"] == (
        "2813a7e9989c7f6b878d903c36e632e4ea38a0c8b4254fa2079a4900f57b58b7"
    )
    assert result["schema_validation"]["schema_version"] == 2
    assert result["schema_validation"]["schema_hash_valid"] is True
    assert result["schema_validation"]["canonical_sql_hash_valid"] is True
    assert result["schema_validation"]["objects_match_canonical"] is True
    assert result["schema_validation"]["unexpected_objects"] == []
    assert result["schema_validation"]["missing_objects"] == []


def test_unexpected_schema_object_is_rejected_on_temporary_copy(tmp_path):
    database = tmp_path / "schema.sqlite"
    shutil.copy2(_root() / "data/governance/governance_decisions.sqlite", database)
    import sqlite3
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unexpected_object(value TEXT)")
    connection.commit()
    connection.close()

    result = _compare_schema_objects(database, tmp_path / "canonical.sqlite")
    assert result["objects_match_canonical"] is False
    assert "table:unexpected_object" in result["unexpected_objects"]


def test_event_identity_counts_and_hash_chain_are_recomputed(tmp_path):
    result = validate_existing_governance_decision_store(**_args(tmp_path))
    events = result["event_validation"]
    chain = result["hash_chain_validation"]

    assert events["counts"] == {
        "legacy_import": 46,
        "batch_parent_approval": 96,
        "resolution_parent_supersede": 5,
        "asset_eligibility": 10,
        "search_alias": 2,
        "entity_metadata": 2,
        "asset_url_manifest_reference": 1,
        "total": 162,
        "parent_events": 125,
        "non_parent_events": 37,
    }
    assert events["unique_event_ids"] == 162
    assert events["unique_idempotency_keys"] == 162
    assert events["orphan_supersedes"] == 0
    assert events["confirmation_reference_count"] == 162
    assert chain["valid"] is True
    assert chain["head"] == "bb0b4d007f7d8477333f08af8ead7d611540e35834fe1e11a2a64be51ed20b31"
    assert chain["tail"] == "60daf915d898f074c6f4f6bb9535e36fc45e1ecb1361fdbd38255767b37c6243"


def test_metadata_current_state_and_special_decisions_are_valid(tmp_path):
    result = validate_existing_governance_decision_store(**_args(tmp_path))

    assert result["schema_metadata_validation"]["valid"] is True
    assert result["execution_metadata_validation"]["valid"] is True
    assert result["execution_metadata_validation"]["database_sha256_is_null"] is True
    assert result["parent_state_validation"]["batch_current"] == 96
    assert result["parent_state_validation"]["legacy_current"] == 19
    assert result["parent_state_validation"]["admin_resolution_current"] == 5
    assert all(row["status"] == "pass" for row in result["special_parent_rows"])


def test_asset_alias_and_url_boundaries_are_valid(tmp_path):
    result = validate_existing_governance_decision_store(**_args(tmp_path))

    assert result["asset_validation"]["include"] == 8
    assert result["asset_validation"]["hold"] == 1
    assert result["asset_validation"]["exclude"] == 1
    assert result["asset_validation"]["parent_approval_does_not_override_hold"] is True
    assert result["alias_validation"]["count"] == 2
    assert result["alias_validation"]["exact_aliases"] == ["SHOPLINE Payments", "SLP"]
    assert result["url_reference_validation"]["count"] == 1
    assert result["url_reference_validation"]["approved_url_field_count"] == 410
    assert result["url_reference_validation"]["actual_url_values_stored"] is False


def test_destructive_checks_run_only_on_cleaned_temporary_copy(tmp_path):
    result = validate_existing_governance_decision_store(**_args(tmp_path))
    temporary = result["temporary_copy_validation"]

    assert temporary["all_updates_blocked"] is True
    assert temporary["all_deletes_blocked"] is True
    assert temporary["duplicate_event_id_rejected"] is True
    assert temporary["duplicate_idempotency_key_rejected"] is True
    assert temporary["tampered_payload_detected"] is True
    assert temporary["broken_previous_hash_detected"] is True
    assert temporary["supersede_projection"] is True
    assert temporary["revoke_projection"] is True
    assert temporary["backup_restore"] is True
    assert temporary["read_only_reopen"] is True
    assert temporary["temporary_files_cleaned"] is True


def test_validation_does_not_modify_formal_database_or_create_sidecars(tmp_path):
    database = _root() / "data/governance/governance_decisions.sqlite"
    before = _file_state(database)
    result = validate_existing_governance_decision_store(**_args(tmp_path))

    assert _file_state(database) == before
    assert result["sidecars_before"] == []
    assert result["sidecars_after"] == []
    assert result["mtime_unchanged"] is True
    assert result["schema_version_pragma_unchanged"] is True
    assert result["user_version_pragma_unchanged"] is True


def test_reports_are_complete_and_deterministic(tmp_path):
    args = _args(tmp_path)
    first = validate_existing_governance_decision_store(**args)
    first_hash = _directory_hash(args["report_dir"])
    second = validate_existing_governance_decision_store(**args)

    assert first["validation_fingerprint"] == second["validation_fingerprint"]
    assert _directory_hash(args["report_dir"]) == first_hash
    assert len([path for path in args["report_dir"].iterdir() if path.is_file()]) == 22


def test_cli_is_read_only_and_requires_no_slack_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    output = tmp_path / "reports"
    assert main([
        "validate-existing-governance-decision-store",
        "--output", str(output),
        "--temporary-root", str(tmp_path / "temporary"),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["event_count"] == 162
    assert result["formal_database_unchanged"] is True


def _args(tmp_path):
    return {
        "repo_root": _root(),
        "database_path": _root() / "data/governance/governance_decisions.sqlite",
        "report_dir": tmp_path / "reports",
        "temporary_root": tmp_path / "temporary",
    }


def _root():
    return Path(__file__).resolve().parents[1]


def _file_state(path):
    stat = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


def _directory_hash(path):
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()
