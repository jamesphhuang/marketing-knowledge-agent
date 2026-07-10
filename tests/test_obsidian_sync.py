import json
import os
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.obsidian_sync import (
    ObsidianSyncError,
    create_sync_plan,
    execute_sync_plan,
    rollback_sync,
)


def test_sync_plan_detects_add_and_unchanged(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")

    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")
    assert plan["counts"]["will_add"] == 1
    assert plan["counts"]["unchanged"] == 0

    result = execute_sync_plan(plan["json_path"], vault, confirm=True)
    assert result["counts"]["will_add"] == 1
    assert (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()

    second_plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-2")
    assert second_plan["counts"]["will_add"] == 0
    assert second_plan["counts"]["unchanged"] == 1


def test_sync_plan_detects_update(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1, body="v1")])
    vault = _write_vault(tmp_path / "vault")
    first_plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-1")
    execute_sync_plan(first_plan["json_path"], vault, confirm=True)

    (apply_dir / "approved_vault_preview" / "merchant_cases" / "merchant-a.md").write_text(
        _markdown(_record("Merchant A", 1, body="v2")), encoding="utf-8"
    )
    second_plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-2")
    assert second_plan["counts"]["will_update"] == 1
    execute_sync_plan(second_plan["json_path"], vault, confirm=True)
    assert "v2" in (vault / "MKA" / "merchant_cases" / "merchant-a.md").read_text(encoding="utf-8")


def test_sync_plan_detects_archive(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply-1", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    first_plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-1")
    execute_sync_plan(first_plan["json_path"], vault, confirm=True)

    empty_apply = _write_apply_dir(tmp_path / "apply-2", [])
    second_plan = create_sync_plan(empty_apply, vault, output_dir=tmp_path / "sync-2")
    assert second_plan["counts"]["will_archive"] == 1
    result = execute_sync_plan(second_plan["json_path"], vault, confirm=True)
    assert not (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()
    archive = vault / "MKA" / "_archived" / result["batch_id"] / "merchant_cases" / "merchant-a.md"
    assert archive.exists()


def test_user_edited_managed_file_is_conflict_and_not_overwritten(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1, body="original")])
    vault = _write_vault(tmp_path / "vault")
    first_plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-1")
    execute_sync_plan(first_plan["json_path"], vault, confirm=True)
    target = vault / "MKA" / "merchant_cases" / "merchant-a.md"
    target.write_text(target.read_text(encoding="utf-8") + "manual edit\n", encoding="utf-8")

    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-2")
    assert plan["counts"]["conflict_user_edited"] == 1
    with pytest.raises(ObsidianSyncError, match="conflict"):
        execute_sync_plan(plan["json_path"], vault, confirm=True)
    assert "manual edit" in target.read_text(encoding="utf-8")


def test_unmanaged_same_target_is_conflict_and_unchanged(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    target = vault / "MKA" / "merchant_cases" / "merchant-a.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("manual note\n", encoding="utf-8")
    before = (target.read_text(encoding="utf-8"), target.stat().st_mtime_ns)

    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")
    assert plan["counts"]["conflict_unmanaged"] == 1
    result = execute_sync_plan(plan["json_path"], vault, confirm=True, allow_conflicts_skip=True)
    assert result["counts"]["conflict_unmanaged"] == 1
    assert (target.read_text(encoding="utf-8"), target.stat().st_mtime_ns) == before


def test_namespace_outside_file_is_not_changed(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    outside = vault / "User Note.md"
    outside.write_text("keep me\n", encoding="utf-8")
    before = (outside.read_text(encoding="utf-8"), outside.stat().st_mtime_ns)

    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")
    execute_sync_plan(plan["json_path"], vault, confirm=True)
    assert (outside.read_text(encoding="utf-8"), outside.stat().st_mtime_ns) == before


def test_plan_binding_rejects_apply_preview_drift(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")
    source = apply_dir / "approved_vault_preview" / "merchant_cases" / "merchant-a.md"
    source.write_text(source.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")

    with pytest.raises(ObsidianSyncError, match="狀態已改變"):
        execute_sync_plan(plan["json_path"], vault, confirm=True)
    assert not (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()


def test_denylist_final_gate_rejects_whole_batch(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1, body="Restricted Brand detail")])
    (apply_dir / "governance_table_preview" / "restricted_customers.json").write_text(
        json.dumps([{"brand_name": "Restricted Brand", "restricted_aliases": ["Restricted Brand"]}], ensure_ascii=False),
        encoding="utf-8",
    )
    vault = _write_vault(tmp_path / "vault")
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")

    with pytest.raises(ObsidianSyncError, match="denylist"):
        execute_sync_plan(plan["json_path"], vault, confirm=True)
    assert not (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()


def test_mid_execution_failure_restores_namespace_and_marks_manifest(tmp_path, monkeypatch):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1), _record("Merchant B", 2)])
    vault = _write_vault(tmp_path / "vault")
    before = sorted(path.relative_to(vault).as_posix() for path in vault.rglob("*") if path.is_file())
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")

    import marketing_knowledge_agent.obsidian_sync as sync_module

    original = sync_module._atomic_write_text
    calls = {"count": 0}

    def fail_second(path, content):
        if vault.resolve() in Path(path).resolve().parents:
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected write failure")
        return original(path, content)

    monkeypatch.setattr(sync_module, "_atomic_write_text", fail_second)
    with pytest.raises(ObsidianSyncError, match="還原"):
        execute_sync_plan(plan["json_path"], vault, confirm=True)

    after = sorted(path.relative_to(vault).as_posix() for path in vault.rglob("*") if path.is_file())
    assert after == before
    manifests = list((tmp_path / "sync").glob("manifest_*.json"))
    assert manifests
    assert json.loads(manifests[0].read_text(encoding="utf-8"))["status"] == "aborted_and_restored"


def test_rollback_restores_namespace(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")
    result = execute_sync_plan(plan["json_path"], vault, confirm=True)
    assert (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()

    rollback = rollback_sync(result["batch_id"], vault, output_dir=tmp_path / "sync")
    assert rollback["status"] == "rolled_back"
    assert not (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()


def test_execute_without_confirm_writes_nothing(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")
    result = execute_sync_plan(plan["json_path"], vault, confirm=False)

    assert result["requires_confirmation"] is True
    assert not (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()
    assert not list((tmp_path / "sync").glob("manifest_*.json"))


def test_cli_execute_without_confirm_returns_one(tmp_path):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")

    exit_code = main(["sync-obsidian", "execute", "--plan", str(plan["json_path"]), "--vault", str(vault)])
    assert exit_code == 1
    assert not (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()


def test_roundtrip_preserves_backslash_and_quotes(tmp_path):
    literal_note = 'GMV range\\n* only for written context with "quotes"'
    nested_json = '{"文章": "暫時下架", "note": "literal\\ntext"}'
    apply_dir = _write_apply_dir(
        tmp_path / "apply",
        [
            _record(
                "Merchant A",
                1,
                extra_frontmatter={
                    "metric_note": literal_note,
                    "restricted_note": 'Example "restricted" note\\nwith marker',
                    "invalid_asset_values": nested_json,
                },
            )
        ],
    )
    vault = _write_vault(tmp_path / "vault")
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-1")

    execute_sync_plan(plan["json_path"], vault, confirm=True)

    second_plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync-2")
    assert second_plan["counts"]["conflict_user_edited"] == 0
    assert second_plan["counts"]["unchanged"] == 1


def test_execute_self_check_fails_on_checksum_mismatch(tmp_path, monkeypatch):
    apply_dir = _write_apply_dir(tmp_path / "apply", [_record("Merchant A", 1)])
    vault = _write_vault(tmp_path / "vault")
    plan = create_sync_plan(apply_dir, vault, output_dir=tmp_path / "sync")

    import marketing_knowledge_agent.obsidian_sync as sync_module

    original = sync_module._atomic_write_text

    def corrupt_vault_markdown(path, content):
        resolved = Path(path).resolve()
        if vault.resolve() in resolved.parents and resolved.suffix == ".md":
            return original(path, content + "\ncorrupted after checksum\n")
        return original(path, content)

    monkeypatch.setattr(sync_module, "_atomic_write_text", corrupt_vault_markdown)
    with pytest.raises(ObsidianSyncError, match="checksum"):
        execute_sync_plan(plan["json_path"], vault, confirm=True)

    assert not (vault / "MKA" / "merchant_cases" / "merchant-a.md").exists()


def _write_vault(vault: Path) -> Path:
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    (vault / ".obsidian" / ".keep").write_text("", encoding="utf-8")
    (vault / "MKA").mkdir(parents=True, exist_ok=True)
    return vault


def _write_apply_dir(apply_dir: Path, records) -> Path:
    target = apply_dir / "approved_vault_preview" / "merchant_cases"
    target.mkdir(parents=True, exist_ok=True)
    (apply_dir / "governance_table_preview").mkdir(parents=True, exist_ok=True)
    (apply_dir / "governance_table_preview" / "restricted_customers.json").write_text("[]", encoding="utf-8")
    (apply_dir / "apply_decisions_summary.md").write_text(
        """# Apply Review Decisions Preview Summary

## Conservation
- merchant_case: 1 = 1(vault); ok=yes

## Whitelist Assertions
- Conservation ok: yes
- Restricted whitelist assertion: passed
- Pending metric vault assertion: passed
""",
        encoding="utf-8",
    )
    for record in records:
        (target / f"merchant-{record['brand_name'][-1].lower()}.md").write_text(_markdown(record), encoding="utf-8")
    return apply_dir


def _record(brand_name, source_row, body="clean content", extra_frontmatter=None):
    record = {
        "title": brand_name,
        "source_type": "database",
        "record_type": "merchant_case",
        "status": "published",
        "publish_date": "2026-07-01",
        "source_sheet": "商家夥伴案例資料庫",
        "source_row": source_row,
        "brand_name": brand_name,
        "merchant_handle": f"handle-{source_row}",
        "body": body,
    }
    if extra_frontmatter:
        record["extra_frontmatter"] = extra_frontmatter
    return record


def _markdown(record):
    lines = [
        "---",
        f"title: \"{record['title']}\"",
        f"source_type: {record['source_type']}",
        f"record_type: {record['record_type']}",
        f"status: {record['status']}",
        f"publish_date: {record['publish_date']}",
        f"source_sheet: \"{record['source_sheet']}\"",
        f"source_row: {record['source_row']}",
        f"brand_name: \"{record['brand_name']}\"",
        f"merchant_handle: \"{record['merchant_handle']}\"",
    ]
    for key, value in (record.get("extra_frontmatter") or {}).items():
        lines.append(f"{key}: '{value}'")
    lines.extend(["---", "", record["body"], ""])
    return "\n".join(lines)
