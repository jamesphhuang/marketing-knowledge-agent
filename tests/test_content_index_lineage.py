import json
import sqlite3
from pathlib import Path

import pytest

from fixtures import build_row_v1_sync_evidence
from marketing_knowledge_agent import record_identity_lineage
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.content_index import (
    ContentIndexError,
    build_content_index,
    create_content_index_plan,
)
from marketing_knowledge_agent.content_index_lineage import ContentIndexLineageEvidence
from marketing_knowledge_agent.ingestion import stable_id
from marketing_knowledge_agent.obsidian_sync import (
    _synced_content,
    create_sync_plan,
    execute_sync_plan,
)


def test_plan_without_evidence_reports_not_ready_and_writes_no_database(tmp_path):
    fixture = _fixture(tmp_path)
    db_path = tmp_path / "content.sqlite"

    result = build_content_index(
        fixture["vault"], db_path=db_path, report_dir=tmp_path / "reports", confirm=False
    )

    assert result["record_identity_scheme"] == "row_v1"
    assert result["lineage_gate"] == "NOT_PROVIDED"
    assert result["lineage_evidence"] == "none"
    assert result["production_reindex_ready"] is False
    assert not db_path.exists()


@pytest.mark.parametrize(
    "break_evidence",
    ["missing", "wrong_workbook", "malformed", "unsupported_scheme", "wrong_target"],
)
def test_confirm_rejects_invalid_evidence_before_write_and_preserves_database(
    tmp_path, break_evidence
):
    fixture = _fixture(tmp_path / "primary")
    evidence = fixture["evidence"]
    if break_evidence == "missing":
        evidence = None
    elif break_evidence == "wrong_target":
        evidence = _fixture(tmp_path / "other")["evidence"]
    else:
        manifest_path = evidence.sync_manifest_path
        if break_evidence == "malformed":
            manifest_path.write_text("{", encoding="utf-8")
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if break_evidence == "wrong_workbook":
                manifest["row_v1_workbook_sha256"] = "0" * 64
            else:
                manifest["record_identity_scheme_version"] = "stable_record_v2"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    db_path = tmp_path / "existing.sqlite"
    db_path.write_bytes(b"existing database must survive")
    sidecars = [Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
    sidecars[0].write_bytes(b"existing wal")
    sidecars[1].write_bytes(b"existing shm")
    before = {path: path.read_bytes() for path in [db_path, *sidecars]}

    with pytest.raises(ContentIndexError, match="lineage gate failed before database write"):
        build_content_index(
            fixture["vault"],
            db_path=db_path,
            report_dir=tmp_path / "reports",
            restricted_customers_path=_denylist(tmp_path),
            confirm=True,
            lineage_evidence=evidence,
        )

    assert {path: path.read_bytes() for path in [db_path, *sidecars]} == before


def test_confirm_rejects_missing_canonical_contract_before_write(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    db_path = tmp_path / "existing.sqlite"
    db_path.write_bytes(b"preserve")
    missing = tmp_path / "missing-contract"
    monkeypatch.setattr(record_identity_lineage, "_contract_root", lambda: missing)

    with pytest.raises(ContentIndexError, match="lineage gate failed before database write"):
        build_content_index(
            fixture["vault"],
            db_path=db_path,
            report_dir=tmp_path / "reports",
            restricted_customers_path=_denylist(tmp_path),
            confirm=True,
            lineage_evidence=fixture["evidence"],
        )

    assert db_path.read_bytes() == b"preserve"


@pytest.mark.parametrize("stable_record_id", [None, "MKA-MC-00001"])
def test_matching_binding_builds_temp_index_without_changing_document_ids(
    tmp_path, stable_record_id
):
    fixture = _fixture(tmp_path, stable_record_id=stable_record_id)
    db_path = tmp_path / "content.sqlite"
    plan = create_content_index_plan(
        fixture["vault"], lineage_evidence=fixture["evidence"]
    )
    expected_document_id = stable_id("doc", "MKA/merchant_cases/merchant-a.md")

    result = build_content_index(
        fixture["vault"],
        db_path=db_path,
        report_dir=tmp_path / "reports",
        restricted_customers_path=_denylist(tmp_path),
        confirm=True,
        lineage_evidence=fixture["evidence"],
    )

    assert result["lineage_gate"] == "PASSED"
    assert result["production_reindex_ready"] is True
    assert plan.included[0].document.id == expected_document_id
    expected_chunk_id = stable_id("chunk", f"{expected_document_id}:0")
    with sqlite3.connect(str(db_path)) as connection:
        assert connection.execute("SELECT id FROM documents").fetchone()[0] == expected_document_id
        assert connection.execute("SELECT id FROM chunks").fetchone()[0] == expected_chunk_id


def test_stable_record_id_does_not_replace_missing_lineage_evidence(tmp_path):
    fixture = _fixture(tmp_path, stable_record_id="MKA-MC-00001")
    db_path = tmp_path / "content.sqlite"
    with pytest.raises(ContentIndexError, match="lineage gate failed"):
        build_content_index(
            fixture["vault"],
            db_path=db_path,
            report_dir=tmp_path / "reports",
            restricted_customers_path=_denylist(tmp_path),
            confirm=True,
        )
    assert not db_path.exists()


def test_matching_unchanged_and_update_sync_receipts_bind_current_vault(tmp_path):
    fixture = _fixture(tmp_path)
    unchanged_plan = create_sync_plan(
        fixture["apply_dir"],
        fixture["vault"],
        output_dir=tmp_path / "sync-unchanged",
    )
    unchanged_execution = execute_sync_plan(
        unchanged_plan["json_path"], fixture["vault"], confirm=True
    )
    unchanged_evidence = ContentIndexLineageEvidence(
        apply_dir=fixture["apply_dir"],
        sync_plan_path=Path(unchanged_plan["json_path"]),
        sync_manifest_path=Path(unchanged_execution["manifest_path"]),
    )
    assert create_content_index_plan(
        fixture["vault"], lineage_evidence=unchanged_evidence
    ).lineage_summary["lineage_gate"] == "PASSED"

    source = (
        fixture["apply_dir"]
        / "approved_vault_preview"
        / "merchant_cases"
        / "merchant-a.md"
    )
    source.write_text(_merchant_markdown(body="updated through sync"), encoding="utf-8")
    update_plan = create_sync_plan(
        fixture["apply_dir"], fixture["vault"], output_dir=tmp_path / "sync-update"
    )
    update_execution = execute_sync_plan(
        update_plan["json_path"], fixture["vault"], confirm=True
    )
    update_evidence = ContentIndexLineageEvidence(
        apply_dir=fixture["apply_dir"],
        sync_plan_path=Path(update_plan["json_path"]),
        sync_manifest_path=Path(update_execution["manifest_path"]),
    )
    assert create_content_index_plan(
        fixture["vault"], lineage_evidence=update_evidence
    ).lineage_summary["lineage_gate"] == "PASSED"


@pytest.mark.parametrize(
    "mutation",
    [
        "different_merchant",
        "missing",
        "extra",
        "row_moved",
        "modified",
        "path_moved",
        "self_declared_vault_sha",
    ],
)
def test_vault_merchant_surface_mismatch_fails_closed_and_preserves_database(
    tmp_path, mutation
):
    fixture = _fixture(tmp_path)
    target = fixture["vault"] / "MKA" / "merchant_cases" / "merchant-a.md"
    if mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        extra_source = _merchant_markdown(title="Merchant B", row=2)
        extra_content, _ = _synced_content(extra_source, "manual-test")
        (target.parent / "merchant-b.md").write_text(extra_content, encoding="utf-8")
    elif mutation == "path_moved":
        target.rename(target.parent / "merchant-moved.md")
    elif mutation == "self_declared_vault_sha":
        source = fixture["apply_dir"] / "approved_vault_preview" / "merchant_cases" / "merchant-a.md"
        changed, _ = _synced_content(source.read_text(encoding="utf-8"), "self-declared-batch")
        target.write_text(changed, encoding="utf-8")
    else:
        overrides = {
            "different_merchant": {"title": "Merchant Z", "body": "different merchant"},
            "row_moved": {"row": 2},
            "modified": {"body": "content modified after receipt"},
        }[mutation]
        changed, _ = _synced_content(_merchant_markdown(**overrides), "manual-test")
        target.write_text(changed, encoding="utf-8")

    db_path = tmp_path / "existing.sqlite"
    db_path.write_bytes(b"preserve exact bytes")
    with pytest.raises(ContentIndexError, match="lineage gate failed before database write"):
        build_content_index(
            fixture["vault"],
            db_path=db_path,
            report_dir=tmp_path / "reports",
            restricted_customers_path=_denylist(tmp_path),
            confirm=True,
            lineage_evidence=fixture["evidence"],
        )
    assert db_path.read_bytes() == b"preserve exact bytes"


def test_nonmerchant_input_still_requires_overall_lineage_gate(tmp_path):
    fixture = build_row_v1_sync_evidence(
        tmp_path,
        {"public_metrics/metric.md": _public_metric_markdown()},
    )
    with pytest.raises(ContentIndexError, match="lineage gate failed"):
        build_content_index(
            fixture["vault"],
            db_path=tmp_path / "missing.sqlite",
            report_dir=tmp_path / "missing-reports",
            restricted_customers_path=_denylist(tmp_path),
            confirm=True,
        )

    result = build_content_index(
        fixture["vault"],
        db_path=tmp_path / "valid.sqlite",
        report_dir=tmp_path / "valid-reports",
        restricted_customers_path=_denylist(tmp_path),
        confirm=True,
        lineage_evidence=fixture["evidence"],
    )
    assert result["lineage_gate"] == "PASSED"
    assert result["lineage_merchant_record_count"] == 0


def test_bare_row_style_mapping_is_not_accepted_as_evidence(tmp_path):
    fixture = _fixture(tmp_path)
    result = create_content_index_plan(
        fixture["vault"],
        lineage_evidence={"source_sheet": "商家夥伴案例資料庫", "source_row": 1},
    )
    assert result.lineage_summary["lineage_gate"] == "FAILED"
    assert result.lineage_summary["production_reindex_ready"] is False


def test_cli_rejects_partial_explicit_evidence_before_database_write(tmp_path):
    fixture = _fixture(tmp_path)
    db_path = tmp_path / "content.sqlite"
    assert main(
        [
            "build-content-index",
            "--vault",
            str(fixture["vault"]),
            "--db",
            str(db_path),
            "--confirm",
            "--lineage-apply-dir",
            str(fixture["evidence"].apply_dir),
        ]
    ) == 2
    assert not db_path.exists()


def _fixture(tmp_path: Path, stable_record_id=None):
    return build_row_v1_sync_evidence(
        tmp_path,
        {
            "merchant_cases/merchant-a.md": _merchant_markdown(
                stable_record_id=stable_record_id
            )
        },
    )


def _merchant_markdown(
    title="Merchant A", row=1, body="known trace content", stable_record_id=None
):
    stable_line = f"stable_record_id: {stable_record_id}\n" if stable_record_id else ""
    return f"""---
title: "{title}"
source_type: database
record_type: merchant_case
status: published
publish_date: 2026-07-01
source_sheet: "商家夥伴案例資料庫"
source_row: {row}
source_path: "商家夥伴案例資料庫:{row}"
{stable_line}brand_name: "{title}"
merchant_handle: "handle-{row}"
data_classification: internal
can_quote_externally: false
can_enter_content_index: true
allowed_exposure_channels: []
invalid_asset_values: {{}}
---

{body}
"""


def _public_metric_markdown():
    return """---
title: "Metric"
source_type: database
record_type: public_metric
status: published
publish_date: 2026-07-01
source_sheet: "公開數據"
source_row: 1
source_path: "公開數據:1"
data_classification: public
can_quote_externally: true
can_enter_content_index: true
allowed_exposure_channels:
  - saleskits
invalid_asset_values: {}
---

known public metric trace
"""


def _denylist(tmp_path: Path) -> Path:
    path = tmp_path / "restricted.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    return path
