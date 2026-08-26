import csv
import json
import sqlite3
from pathlib import Path

import pytest

from fixtures import build_row_v1_sync_evidence
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.content_index import (
    ContentIndexError,
    _assert_conservation,
    _assert_forbidden_record_types,
    _assert_retrieval_trace,
    build_content_index,
    normalize_vault_frontmatter,
)
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.obsidian_sync import _synced_content


def test_normalize_vault_frontmatter_restores_dict_and_removes_sync_keys():
    normalized = normalize_vault_frontmatter(
        {
            "title": "Example",
            "invalid_asset_values": '{"article": "pending"}',
            "managed_by": "marketing-knowledge-agent",
            "sync_batch_id": "batch-1",
            "synced_at": "2026-07-10T00:00:00Z",
            "content_checksum": "abc",
        }
    )

    assert normalized["invalid_asset_values"] == {"article": "pending"}
    assert "managed_by" not in normalized
    assert "sync_batch_id" not in normalized
    assert "synced_at" not in normalized
    assert "content_checksum" not in normalized


def test_normalize_vault_frontmatter_rejects_invalid_dict_json():
    with pytest.raises(ContentIndexError, match="invalid_asset_values"):
        normalize_vault_frontmatter({"invalid_asset_values": "{broken"})


@pytest.mark.parametrize(
    ("relative_path", "overrides", "reason"),
    [
        ("merchant_cases/not-managed.md", {"managed_by": None}, "not_managed"),
        ("_archived/batch/archived.md", {}, "archived"),
        ("_vault_only/vault-only.md", {"can_enter_content_index": False}, "vault_only"),
        ("merchant_cases/index-false.md", {"can_enter_content_index": False}, "index_flag_false"),
        ("merchant_cases/restricted.md", {"record_type": "restricted_customer"}, "forbidden_record_type"),
        (
            "public_metrics/no-channels.md",
            {"record_type": "public_metric", "allowed_exposure_channels": []},
            "metric_missing_channels",
        ),
        ("merchant_cases/invalid-metadata.md", {"publish_date": "not-a-date"}, "metadata_parse_error"),
    ],
)
def test_each_eligibility_rule_has_a_stable_exclusion_reason(tmp_path, relative_path, overrides, reason):
    vault = _vault(tmp_path)
    _write_synced(vault / "MKA" / relative_path, **overrides)

    result = build_content_index(
        vault_path=vault,
        db_path=tmp_path / "content.sqlite",
        report_dir=tmp_path / "reports" / "content_index",
        confirm=False,
    )

    assert result["scanned_count"] == 1
    assert result["indexable_count"] == 0
    assert result["exclusion_counts"] == {reason: 1}


def test_plan_mode_writes_report_and_audit_but_not_database(tmp_path):
    vault = _vault(tmp_path)
    _write_synced(vault / "MKA" / "merchant_cases" / "eligible.md")
    _write_synced(vault / "MKA" / "merchant_cases" / "._appledouble.md", publish_date="invalid")
    db_path = tmp_path / "content.sqlite"
    report_dir = tmp_path / "reports" / "content_index"

    exit_code = main(
        [
            "build-content-index",
            "--vault",
            str(vault),
            "--db",
            str(db_path),
            "--report-dir",
            str(report_dir),
        ]
    )

    assert exit_code == 0
    assert not db_path.exists()
    assert (report_dir / "build_report.md").is_file()
    assert (report_dir.parent / "audit_log.csv").is_file()
    assert "Scanned files: 1" in (report_dir / "build_report.md").read_text(encoding="utf-8")


def test_content_index_audit_append_preserves_existing_sync_schema(tmp_path):
    vault = _vault(tmp_path)
    _write_synced(vault / "MKA" / "merchant_cases" / "eligible.md")
    report_dir = tmp_path / "reports" / "content_index"
    audit_path = report_dir.parent / "audit_log.csv"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(
        "timestamp,batch_id,action,add,update,archive,operator,plan_path\n"
        "2026-07-10T00:00:00+00:00,batch-1,sync,1,0,0,tester,plan.json\n",
        encoding="utf-8",
    )

    build_content_index(
        vault_path=vault,
        db_path=tmp_path / "content.sqlite",
        report_dir=report_dir,
        confirm=False,
    )

    rows = list(csv.reader(audit_path.open("r", encoding="utf-8", newline="")))
    assert all(len(row) == 8 for row in rows)
    assert rows[-1][2] == "build-content-index plan"
    assert rows[-1][3] == "1"
    assert rows[-1][7] == str(tmp_path / "content.sqlite")


def test_plan_mode_returns_one_for_anomalous_exclusion(tmp_path):
    vault = _vault(tmp_path)
    _write_synced(
        vault / "MKA" / "merchant_cases" / "restricted.md",
        record_type="restricted_customer",
    )

    exit_code = main(
        [
            "build-content-index",
            "--vault",
            str(vault),
            "--db",
            str(tmp_path / "content.sqlite"),
            "--report-dir",
            str(tmp_path / "reports" / "content_index"),
        ]
    )

    assert exit_code == 1
    assert not (tmp_path / "content.sqlite").exists()


def test_confirm_cli_returns_two_and_deletes_database_on_assertion_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _lineage_fixture(
        tmp_path, body="Restricted Brand confidential fact"
    )
    vault = fixture["vault"]
    default_denylist = tmp_path / "reports" / "excel_preview" / "restricted_customers.json"
    default_denylist.parent.mkdir(parents=True)
    default_denylist.write_text(
        json.dumps([{"brand_name": "Restricted Brand"}]),
        encoding="utf-8",
    )
    db_path = tmp_path / "content.sqlite"

    exit_code = main(
        [
            "build-content-index",
            "--vault",
            str(vault),
            "--db",
            str(db_path),
            "--report-dir",
            str(tmp_path / "reports" / "content_index"),
            "--confirm",
            "--lineage-apply-dir",
            str(fixture["evidence"].apply_dir),
            "--lineage-sync-plan",
            str(fixture["evidence"].sync_plan_path),
            "--lineage-sync-manifest",
            str(fixture["evidence"].sync_manifest_path),
        ]
    )

    assert exit_code == 2
    assert not db_path.exists()


def test_synced_vault_copy_plans_thirteen_with_twelve_indexable(tmp_path):
    vault = _vault(tmp_path)
    for row in range(1, 10):
        _write_synced(vault / "MKA" / "merchant_cases" / f"merchant-{row}.md", source_row=row)
    for row in range(10, 13):
        _write_synced(
            vault / "MKA" / "public_metrics" / f"metric-{row}.md",
            source_row=row,
            record_type="public_metric",
            allowed_exposure_channels=["saleskits"],
        )
    _write_synced(
        vault / "MKA" / "_vault_only" / "vault-only.md",
        source_row=13,
        can_enter_content_index=False,
        invalid_asset_values='{"article": "temporarily unavailable"}',
    )

    result = build_content_index(
        vault_path=vault,
        db_path=tmp_path / "content.sqlite",
        report_dir=tmp_path / "reports" / "content_index",
        confirm=False,
    )

    assert result["scanned_count"] == 13
    assert result["indexable_count"] == 12
    assert result["exclusion_counts"] == {"vault_only": 1}


def test_confirm_builds_index_and_passes_all_safety_assertions(tmp_path):
    fixture = _lineage_fixture(tmp_path)
    vault = fixture["vault"]
    denylist = _denylist(tmp_path, [])

    result = build_content_index(
        vault_path=vault,
        db_path=tmp_path / "content.sqlite",
        report_dir=tmp_path / "reports" / "content_index",
        restricted_customers_path=denylist,
        confirm=True,
        lineage_evidence=fixture["evidence"],
    )

    assert result["assertions"] == {
        "forbidden_record_types": "passed",
        "restricted_denylist": "passed",
        "conservation": "passed",
        "retrieval_trace": "passed",
    }
    assert SQLiteIndex(tmp_path / "content.sqlite").counts()["documents"] == 1


def test_forbidden_record_type_refuses_confirm_and_deletes_database(tmp_path):
    fixture = _lineage_fixture(tmp_path, record_type="restricted_customer")
    vault = fixture["vault"]
    db_path = tmp_path / "content.sqlite"
    db_path.write_text("stale", encoding="utf-8")

    with pytest.raises(ContentIndexError, match="forbidden_record_type"):
        build_content_index(
            vault_path=vault,
            db_path=db_path,
            report_dir=tmp_path / "reports" / "content_index",
            restricted_customers_path=_denylist(tmp_path, []),
            confirm=True,
            lineage_evidence=fixture["evidence"],
        )

    assert not db_path.exists()


def test_denylist_assertion_refuses_content_and_deletes_database(tmp_path):
    fixture = _lineage_fixture(
        tmp_path, body="Restricted Brand confidential fact"
    )
    vault = fixture["vault"]
    db_path = tmp_path / "content.sqlite"

    with pytest.raises(ContentIndexError, match="denylist"):
        build_content_index(
            vault_path=vault,
            db_path=db_path,
            report_dir=tmp_path / "reports" / "content_index",
            restricted_customers_path=_denylist(tmp_path, [{"brand_name": "Restricted Brand"}]),
            confirm=True,
            lineage_evidence=fixture["evidence"],
        )

    assert not db_path.exists()


def test_forbidden_type_database_assertion_fails(tmp_path):
    db_path = tmp_path / "content.sqlite"
    _create_minimal_database(
        db_path,
        metadata={"record_type": "pending_metric"},
        source_sheet="Pending",
        source_row=1,
    )

    with pytest.raises(ContentIndexError, match="forbidden record types"):
        _assert_forbidden_record_types(db_path)


def test_conservation_assertion_fails_for_document_count_mismatch(tmp_path):
    db_path = tmp_path / "content.sqlite"
    _create_minimal_database(
        db_path,
        metadata={"record_type": "merchant_case"},
        source_sheet="Cases",
        source_row=1,
    )

    with pytest.raises(ContentIndexError, match="conservation"):
        _assert_conservation(db_path, expected_documents=2)


def test_retrieval_trace_assertion_fails_without_source_coordinates(tmp_path):
    fixture = build_row_v1_sync_evidence(
        tmp_path,
        {"public_metrics/missing-trace.md": _source_markdown(
            record_type="public_metric",
            allowed_exposure_channels=["saleskits"],
        )},
    )
    vault = fixture["vault"]
    target = vault / "MKA" / "public_metrics" / "missing-trace.md"
    changed, _ = _synced_content(
        _source_markdown(
            record_type="public_metric",
            allowed_exposure_channels=["saleskits"],
            source_sheet=None,
            source_row=None,
        ),
        "missing-trace-test",
    )
    target.write_text(changed, encoding="utf-8")
    denylist = _denylist(tmp_path, [])

    with pytest.raises(ContentIndexError, match="source_sheet/source_row"):
        build_content_index(
            vault_path=vault,
            db_path=tmp_path / "content.sqlite",
            report_dir=tmp_path / "reports" / "content_index",
            restricted_customers_path=denylist,
            confirm=True,
            lineage_evidence=fixture["evidence"],
        )

    assert not (tmp_path / "content.sqlite").exists()


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "MKA").mkdir(parents=True)
    return vault


def _denylist(tmp_path: Path, records) -> Path:
    path = tmp_path / "restricted_customers.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return path


def _write_synced(path: Path, **overrides) -> None:
    values = {
        "title": "Example approved record",
        "source_type": "database",
        "record_type": "merchant_case",
        "status": "published",
        "publish_date": "2026-07-01",
        "source_path": "Cases:1",
        "source_sheet": "Cases",
        "source_row": 1,
        "data_classification": "internal",
        "can_quote_externally": False,
        "can_enter_content_index": True,
        "allowed_exposure_channels": [],
        "invalid_asset_values": "{}",
        "managed_by": "marketing-knowledge-agent",
        "sync_batch_id": "batch-1",
        "synced_at": "2026-07-10T00:00:00Z",
        "content_checksum": "test-checksum",
        "body": "known trace content for formal index",
    }
    values.update(overrides)
    body = values.pop("body")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in values.items():
        if value is None:
            if key == "managed_by":
                continue
            lines.append(f"{key}: null")
        elif isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                lines.extend(f"  - '{item}'" for item in value)
            else:
                lines.append(f"{key}: []")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: '{value}'")
    lines.extend(["---", "", body, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _lineage_fixture(tmp_path: Path, **overrides):
    return build_row_v1_sync_evidence(
        tmp_path,
        {"merchant_cases/eligible.md": _source_markdown(**overrides)},
    )


def _source_markdown(**overrides) -> str:
    values = {
        "title": "Example approved record",
        "source_type": "database",
        "record_type": "merchant_case",
        "status": "published",
        "publish_date": "2026-07-01",
        "source_path": "Cases:1",
        "source_sheet": "Cases",
        "source_row": 1,
        "data_classification": "internal",
        "can_quote_externally": False,
        "can_enter_content_index": True,
        "allowed_exposure_channels": [],
        "invalid_asset_values": "{}",
        "body": "known trace content for formal index",
    }
    values.update(overrides)
    body = values.pop("body")
    lines = ["---"]
    for key, value in values.items():
        if value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                lines.extend(f"  - '{item}'" for item in value)
            else:
                lines.append(f"{key}: []")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: '{value}'")
    lines.extend(["---", "", body, ""])
    return "\n".join(lines)


def _create_minimal_database(db_path: Path, metadata: dict, source_sheet, source_row) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute("CREATE TABLE documents (id TEXT, metadata_json TEXT)")
        connection.execute("CREATE TABLE chunks (id TEXT, document_id TEXT, text TEXT)")
        payload = dict(metadata, title="Example", source_sheet=source_sheet, source_row=source_row)
        connection.execute("INSERT INTO documents VALUES (?, ?)", ("doc-1", json.dumps(payload)))
        connection.execute("INSERT INTO chunks VALUES (?, ?, ?)", ("chunk-1", "doc-1", "known trace content"))
