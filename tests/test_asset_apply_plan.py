import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from marketing_knowledge_agent.asset_apply_plan import (
    PLAN_OUTPUT_FILENAMES,
    create_asset_metadata_apply_plan,
    deterministic_asset_filename,
)
from marketing_knowledge_agent.cli import main


def test_plan_builds_complete_read_only_contract(tmp_path):
    paths = _fixture(tmp_path)
    protected = _protected_hashes(paths)

    summary = _create_plan(paths)

    assert summary["conclusion"] == "A. Ready for human confirmation"
    assert summary["planned_asset_count"] == 2
    assert summary["planned_url_field_count"] == 4
    assert summary["governance_blocked_asset_count"] == 1
    assert summary["preview_parent_join_count"] == 2
    assert summary["formal_vault_parent_join_count"] == 2
    assert summary["formal_sqlite_parent_join_count"] == 2
    assert summary["execution_blocked"] is False
    assert _protected_hashes(paths) == protected
    assert {path.name for path in paths["output"].iterdir()} == set(
        PLAN_OUTPUT_FILENAMES
    )


def test_asset_filename_depends_only_on_stable_asset_id():
    asset_id = "merchant_cases:r8:article"

    first = deterministic_asset_filename(asset_id)
    second = deterministic_asset_filename(asset_id)

    assert first == second
    assert first == hashlib.sha256(asset_id.encode("utf-8")).hexdigest() + ".md"
    assert ":" not in first


def test_vault_plan_contains_required_fields_but_not_content_tags(tmp_path):
    paths = _fixture(tmp_path)

    _create_plan(paths)

    rows = _read_csv(paths["output"] / "asset_vault_write_plan.csv")
    payload = json.loads(rows[0]["proposed_record_json"])
    assert {
        "asset_id",
        "record_id",
        "brand_name",
        "merchant_handle",
        "asset_type",
        "asset_title",
        "asset_url",
        "canonical_url",
        "source",
        "source_location",
        "provenance",
        "reviewed_by",
        "reviewed_at",
        "review_decision",
        "governance_eligibility",
    }.issubset(payload)
    assert "content_tags" not in payload
    assert rows[0]["target_vault_path"].startswith("MKA/managed/assets/")


def test_content_tags_are_resolved_only_from_formal_parent(tmp_path):
    paths = _fixture(tmp_path)

    _create_plan(paths)

    report = (paths["output"] / "asset_tag_resolution_plan.md").read_text(
        encoding="utf-8"
    )
    assert "merchant_cases:r8:article" in report
    assert "resolved_from_parent" in report
    assert '["formal-parent-tag"]' in report
    assert "title-derived-tag" not in report


def test_governance_blocked_parent_does_not_provide_tags(tmp_path):
    paths = _fixture(tmp_path, second_parent_quoteable=False)

    summary = _create_plan(paths)

    report = (paths["output"] / "asset_tag_resolution_plan.md").read_text(
        encoding="utf-8"
    )
    assert summary["tag_governance_blocked_count"] == 1
    assert "merchant_cases:r9:video | governance_blocked | []" in report


def test_blank_parent_tags_are_omitted_without_inference(tmp_path):
    paths = _fixture(tmp_path, second_parent_tags=[])

    summary = _create_plan(paths)

    report = (paths["output"] / "asset_tag_resolution_plan.md").read_text(
        encoding="utf-8"
    )
    assert summary["tag_blank_count"] == 1
    assert "merchant_cases:r9:video | empty_omit | []" in report


def test_missing_formal_parent_fails_closed(tmp_path):
    paths = _fixture(tmp_path, omit_second_formal_parent=True)

    summary = _create_plan(paths)

    assert summary["conclusion"] == "C. Not ready for Apply"
    assert summary["execution_blocked"] is True
    assert summary["formal_vault_parent_missing_count"] == 1
    assert summary["formal_sqlite_parent_missing_count"] == 1
    assert summary["planned_asset_count"] == 2
    checklist = (paths["output"] / "asset_apply_confirmation_checklist.md").read_text(
        encoding="utf-8"
    )
    assert "DO NOT CONFIRM" in checklist


def test_duplicate_preview_parent_fails_closed(tmp_path):
    paths = _fixture(tmp_path)
    parents = json.loads(paths["parents"].read_text(encoding="utf-8"))
    parents.append(dict(parents[0]))
    paths["parents"].write_text(json.dumps(parents), encoding="utf-8")

    summary = _create_plan(paths)

    assert summary["execution_blocked"] is True
    assert summary["duplicate_preview_parent_count"] == 1


def test_governance_blocked_assets_never_enter_apply_manifest(tmp_path):
    paths = _fixture(tmp_path)

    _create_plan(paths)

    manifest = _read_csv(paths["output"] / "asset_metadata_apply_manifest.csv")
    blocked = _read_csv(paths["output"] / "asset_governance_blocked.csv")
    assert {row["asset_id"] for row in manifest} == {
        "merchant_cases:r8:article",
        "merchant_cases:r9:video",
    }
    assert {row["asset_id"] for row in blocked} == {
        "merchant_cases:r10:podcast"
    }


def test_plan_rerun_is_deterministic(tmp_path):
    paths = _fixture(tmp_path)

    first = _create_plan(paths)
    first_hashes = _output_hashes(paths["output"])
    second = _create_plan(paths)
    second_hashes = _output_hashes(paths["output"])

    assert first == second
    assert first_hashes == second_hashes


def test_input_change_changes_plan_id(tmp_path):
    paths = _fixture(tmp_path)
    first = _create_plan(paths)
    rows = _read_csv(paths["apply"])
    rows[0]["proposed_value"] = "https://example.com/reviewed-change"
    _write_csv(paths["apply"], rows)

    second = _create_plan(paths)

    assert first["plan_id"] != second["plan_id"]
    assert first["plan_state_hash"] != second["plan_state_hash"]


def test_stale_decision_manifest_fails_closed(tmp_path):
    paths = _fixture(tmp_path)
    decisions = _read_csv(paths["decisions"])
    decisions[0]["reviewer"] = "Different Reviewer"
    _write_csv(paths["decisions"], decisions)

    summary = _create_plan(paths)

    assert summary["execution_blocked"] is True
    assert summary["error_codes"]["decision_manifest_mismatch"] == 1


def test_plan_documents_sqlite_atomic_swap_backup_and_rollback(tmp_path):
    paths = _fixture(tmp_path)

    _create_plan(paths)

    migration = (paths["output"] / "asset_sqlite_migration_plan.md").read_text(
        encoding="utf-8"
    )
    rollback = (paths["output"] / "asset_rollback_execution_plan.md").read_text(
        encoding="utf-8"
    )
    checksums = json.loads(
        (paths["output"] / "asset_pre_apply_checksums.json").read_text(
            encoding="utf-8"
        )
    )
    assert "CREATE TABLE content_assets" in migration
    assert "source_record_id" in migration
    assert "temporary" in migration.lower()
    assert "atomic" in rollback.lower()
    assert "backup" in rollback.lower()
    assert len(checksums["planned_record_checksums"]) == 2


def test_cli_plan_only_and_confirm_execute_are_disabled(tmp_path, capsys):
    paths = _fixture(tmp_path)
    common = _cli_paths(paths)

    plan_exit = main(["apply-asset-metadata", "--plan", *common])
    plan_payload = json.loads(capsys.readouterr().out)
    confirm_exit = main(
        ["apply-asset-metadata", "--confirm", plan_payload["plan_id"], *common]
    )
    confirm_error = capsys.readouterr().err
    execute_exit = main(
        ["apply-asset-metadata", "--execute", plan_payload["plan_id"], *common]
    )
    execute_error = capsys.readouterr().err

    assert plan_exit == 0
    assert confirm_exit == 2
    assert execute_exit == 2
    assert "not enabled" in confirm_error
    assert "not enabled" in execute_error


def _fixture(
    tmp_path,
    *,
    omit_second_formal_parent=False,
    second_parent_quoteable=True,
    second_parent_tags=None,
):
    second_parent_tags = (
        ["second-parent-tag"] if second_parent_tags is None else second_parent_tags
    )
    paths = {
        "apply": tmp_path / "asset_apply_preview.csv",
        "blocked": tmp_path / "asset_apply_preview_blocked.csv",
        "inventory": tmp_path / "asset_metadata_inventory.csv",
        "parents": tmp_path / "merchant_cases.json",
        "decisions": tmp_path / "human_review_template.csv",
        "validation": tmp_path / "validation",
        "restricted": tmp_path / "restricted_customers.json",
        "vault": tmp_path / "obsidian_vault",
        "db": tmp_path / "content_index.sqlite",
        "output": tmp_path / "asset_metadata_apply_plan",
    }
    assets = [
        ("merchant_cases:r8:article", "merchant_cases:r8", "article", "Article A"),
        ("merchant_cases:r9:video", "merchant_cases:r9", "video", "Video B"),
        ("merchant_cases:r10:podcast", "merchant_cases:r10", "podcast", "審核中"),
    ]
    inventory = []
    apply_rows = []
    blocked_rows = []
    decisions = []
    for asset_id, record_id, asset_type, title in assets:
        blocked = asset_type == "podcast"
        inventory.append(
            {
                "asset_id": asset_id,
                "record_id": record_id,
                "brand_name": "Brand " + record_id[-1],
                "asset_type": asset_type,
                "asset_title": "" if blocked else title,
                "invalid_asset_value": title if blocked else "",
            }
        )
        for field in ("asset_url", "canonical_url"):
            row = {
                "record_id": record_id,
                "asset_id": asset_id,
                "brand_name": "Brand " + record_id[-1],
                "asset_type": asset_type,
                "asset_title": "" if blocked else title,
                "field": field,
                "current_value": "",
                "proposed_value": f"https://example.com/{asset_type}/{field}",
                "review_decision": "exclude_asset" if blocked else "approve",
                "reviewer": "Reviewer A",
                "reviewed_at": "2026-07-17",
                "provenance": "exact reviewed fixture",
                "source_location": record_id,
                "eligibility": (
                    "governance_blocked" if blocked else "ready_for_apply_preview"
                ),
                "governance_status": "blocked" if blocked else "eligible",
                "action": "blocked" if blocked else "add",
                "reason": "fixture",
            }
            (blocked_rows if blocked else apply_rows).append(row)
            decisions.append(dict(row))
    parents = [
        _parent(8, ["preview-tag"], True),
        _parent(9, ["preview-second-tag"], second_parent_quoteable),
        _parent(10, ["blocked-preview-tag"], False),
    ]
    _write_csv(paths["inventory"], inventory)
    _write_csv(paths["apply"], apply_rows)
    _write_csv(paths["blocked"], blocked_rows)
    _write_csv(paths["decisions"], decisions)
    paths["parents"].write_text(json.dumps(parents), encoding="utf-8")
    paths["validation"].mkdir()
    (paths["validation"] / "review_validation_summary.md").write_text(
        "errors: 0\n", encoding="utf-8"
    )
    paths["restricted"].write_text("[]", encoding="utf-8")
    (paths["vault"] / ".obsidian").mkdir(parents=True)
    (paths["vault"] / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    _write_parent_md(paths["vault"], 8, ["formal-parent-tag"], True)
    if not omit_second_formal_parent:
        _write_parent_md(
            paths["vault"], 9, second_parent_tags, second_parent_quoteable
        )
    _create_db(paths["db"], [8] + ([] if omit_second_formal_parent else [9]))
    return paths


def _parent(row, tags, quoteable):
    return {
        "record_type": "merchant_case",
        "source_sheet": "merchant_cases",
        "source_row": row,
        "brand_name": f"Brand {row}",
        "merchant_handle": f"brand{row}",
        "content_tags": tags,
        "can_enter_content_index": True,
        "can_quote_externally": quoteable,
    }


def _write_parent_md(vault, row, tags, quoteable):
    target = vault / "MKA" / "merchant_cases" / f"record-r{row}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "record_type: 'merchant_case'",
        "source_sheet: 'merchant_cases'",
        f"source_row: '{row}'",
        f"brand_name: 'Brand {row}'",
        f"merchant_handle: 'brand{row}'",
        "content_tags:",
        *[f"  - '{tag}'" for tag in tags],
        "can_enter_content_index: true",
        f"can_quote_externally: {'true' if quoteable else 'false'}",
        "managed_by: 'marketing-knowledge-agent'",
        "---",
        "",
        "Body with title-derived-tag.",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")


def _create_db(path, rows):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE documents (id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)"
        )
        for row in rows:
            metadata = _parent(row, ["db-tag"], True)
            connection.execute(
                "INSERT INTO documents VALUES (?, ?)",
                (f"doc-{row}", json.dumps(metadata)),
            )


def _create_plan(paths):
    return create_asset_metadata_apply_plan(
        apply_preview_path=paths["apply"],
        blocked_preview_path=paths["blocked"],
        inventory_path=paths["inventory"],
        parent_records_path=paths["parents"],
        decisions_path=paths["decisions"],
        validation_dir=paths["validation"],
        restricted_customers_path=paths["restricted"],
        vault_path=paths["vault"],
        db_path=paths["db"],
        output_dir=paths["output"],
    )


def _cli_paths(paths):
    return [
        "--apply-preview",
        str(paths["apply"]),
        "--blocked-preview",
        str(paths["blocked"]),
        "--inventory",
        str(paths["inventory"]),
        "--parent-records",
        str(paths["parents"]),
        "--decisions",
        str(paths["decisions"]),
        "--validation-dir",
        str(paths["validation"]),
        "--restricted-customers",
        str(paths["restricted"]),
        "--vault",
        str(paths["vault"]),
        "--db",
        str(paths["db"]),
        "--output",
        str(paths["output"]),
    ]


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path, rows):
    rows = list(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _protected_hashes(paths):
    protected = (
        "apply",
        "blocked",
        "inventory",
        "parents",
        "decisions",
        "restricted",
        "db",
    )
    result = {key: _hash_path(paths[key]) for key in protected}
    result["vault"] = _hash_path(paths["vault"])
    return result


def _output_hashes(output):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
    }


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()
