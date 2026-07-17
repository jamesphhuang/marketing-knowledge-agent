import hashlib
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.missing_parent_resolution_apply_preview import (
    OLD_ASSET_PLAN_ID,
    generate_resolution_apply_preview,
    validate_resolution_manifest_input_checksums,
)
from test_missing_parent_resolution_preview import (
    REVIEWED_AT,
    _fixture,
    _formal_entity,
    _generate,
    _read_csv,
    _write_csv,
)


def test_exact_five_parent_decisions_validate(tmp_path):
    paths = _prepared(tmp_path)

    summary = _run(paths)
    rows = _read_csv(paths["apply_output"] / "parent_decision_validation.csv")

    assert summary["validation_error_count"] == 0
    assert {(row["record_id"], row["proposed_review_decision"]) for row in rows} == {
        ("Sheet:r30", "exclude"),
        ("Sheet:r12", "approve_internal_only"),
        ("Sheet:r122", "approve"),
        ("Sheet:r32", "approve"),
        ("Sheet:r7", "approve"),
    }
    assert all(row["validation_status"] == "valid" for row in rows)


def test_stale_parent_decision_is_rejected(tmp_path):
    paths = _prepared(tmp_path)
    rows = _read_csv(paths["reviews"])
    next(row for row in rows if row["source_row"] == "12")["review_decision"] = "approve"
    _write_csv(paths["reviews"], rows)

    summary = _run(paths)

    assert summary["execution_blocked"] is True
    assert summary["validation_error_codes"]["stale_parent_decision"] == 1


def test_duplicate_and_unknown_parent_are_rejected(tmp_path):
    paths = _prepared(tmp_path)
    proposal = paths["output"] / "missing_parent_resolution_decisions.csv"
    rows = _read_csv(proposal)
    rows.append(dict(rows[0]))
    rows.append({**rows[0], "record_id": "Sheet:r999", "brand_name": "Unknown"})
    _write_csv(proposal, rows)

    summary = _run(paths)

    assert summary["execution_blocked"] is True
    assert summary["validation_error_codes"]["duplicate_parent_decision"] == 1
    assert summary["validation_error_codes"]["unknown_parent_decision"] == 1


def test_partner_without_handle_is_valid_but_synthetic_handle_is_rejected(tmp_path):
    paths = _prepared(tmp_path)
    summary = _run(paths)
    rows = _read_csv(paths["apply_output"] / "source_metadata_apply_preview.csv")

    partners = [row for row in rows if row["proposed_entity_type"] == "partner"]
    assert summary["validation_error_count"] == 0
    assert {row["record_id"] for row in partners} == {"Sheet:r7", "Sheet:r122"}
    assert all(row["merchant_handle_requirement"] == "not_required" for row in partners)
    assert all(row["merchant_handle"] == "" for row in partners)

    preview = paths["output"] / "parent_decision_preview.csv"
    tampered = _read_csv(preview)
    next(row for row in tampered if row["record_id"] == "Sheet:r7")["merchant_handle"] = "trade-network"
    _write_csv(preview, tampered)
    rejected = _run(paths)
    assert rejected["validation_error_codes"]["synthetic_partner_handle"] == 1


def test_alias_collision_is_reported_without_forcing_unique_result(tmp_path):
    paths = _prepared(tmp_path)
    parents = json.loads(paths["parents"].read_text(encoding="utf-8"))
    next(row for row in parents if row["source_row"] == 7)["content_tags"] = [
        "SHOPLINE Payments"
    ]
    paths["parents"].write_text(json.dumps(parents, ensure_ascii=False), encoding="utf-8")

    summary = _run(paths, formal_search_fn=lambda query: [_formal_entity(1)] if query == "SHOPLINE Payments" else [])
    collisions = _read_csv(paths["apply_output"] / "alias_collision_validation.csv")

    shopline = next(row for row in collisions if row["alias"] == "SHOPLINE Payments")
    assert shopline["severity"] == "warning"
    assert shopline["collision_type"] == "shared_exact_content_tag"
    assert int(shopline["other_record_match_count"]) == 1
    search = (paths["apply_output"] / "post_apply_search_preview.md").read_text(encoding="utf-8")
    assert "聊心茶室（SLP 用戶）" in search
    assert "Formal 1" in search
    assert summary["alias_warning_count"] == 1


def test_alias_resolution_is_exact_case_insensitive_and_governed(tmp_path):
    paths = _prepared(tmp_path)
    _run(paths)
    alias_rows = _read_csv(paths["apply_output"] / "alias_collision_validation.csv")

    assert {(row["alias"], row["normalized_alias"]) for row in alias_rows} == {
        ("SLP", "slp"),
        ("SHOPLINE Payments", "shopline payments"),
    }
    assert all(row["match_type"] == "case_insensitive_exact" for row in alias_rows)
    assert all(row["fuzzy_matching"] == "false" for row in alias_rows)
    assert all(row["governance_required"] == "true" for row in alias_rows)


def test_held_video_is_excluded_from_manifest_search_slack_and_citation(tmp_path):
    paths = _prepared(tmp_path)
    _run(paths)
    assets = _read_csv(paths["apply_output"] / "asset_eligibility_apply_preview.csv")
    video = next(row for row in assets if row["asset_id"] == "Sheet:r12:video")

    assert video["proposed_asset_index_eligibility"] == "hold"
    assert video["will_enter_apply_manifest"] == "false"
    assert video["will_enter_search_index"] == "false"
    assert video["will_render_in_slack"] == "false"
    assert video["will_generate_citation"] == "false"
    assert video["parent_review_decision"] == "approve_internal_only"
    search = (paths["apply_output"] / "post_apply_search_preview.md").read_text(encoding="utf-8")
    assert "Health Article" in search
    assert "Health Video" not in search


def test_excluded_parent_and_internal_only_contract_are_preserved(tmp_path):
    paths = _prepared(tmp_path)
    _run(paths)
    assets = _read_csv(paths["apply_output"] / "asset_eligibility_apply_preview.csv")
    flower = next(row for row in assets if row["asset_id"] == "Sheet:r30:article")
    article = next(row for row in assets if row["asset_id"] == "Sheet:r12:article")

    assert flower["will_enter_apply_manifest"] == "false"
    assert flower["will_render_in_slack"] == "false"
    assert article["will_enter_apply_manifest"] == "true"
    assert article["can_external_reference"] == "false"
    search = (paths["apply_output"] / "post_apply_search_preview.md").read_text(encoding="utf-8")
    assert "## 莉朵花藝\n\n- Assets: 0\n- Citations: 0" in search
    assert "## littlegirl\n\n- Assets: 0\n- Citations: 0" in search


def test_tags_resolve_from_eligible_parent_only(tmp_path):
    paths = _prepared(tmp_path)
    _run(paths)
    assets = _read_csv(paths["apply_output"] / "asset_eligibility_apply_preview.csv")

    included = next(row for row in assets if row["asset_id"] == "Sheet:r122:article")
    held = next(row for row in assets if row["asset_id"] == "Sheet:r12:video")
    excluded = next(row for row in assets if row["asset_id"] == "Sheet:r30:article")
    assert included["content_tags_source"] == "eligible_parent_source_record"
    assert json.loads(included["resolved_content_tags"]) == ["partner-tag"]
    assert json.loads(held["resolved_content_tags"]) == []
    assert json.loads(excluded["resolved_content_tags"]) == []


def test_counts_and_identifiers_are_conserved(tmp_path):
    paths = _prepared(tmp_path)
    summary = _run(paths)

    assert summary["eligible_asset_count"] == 8
    assert summary["hold_asset_count"] == 1
    assert summary["excluded_asset_count"] == 1
    assert summary["approved_url_field_count"] == 16
    assert summary["new_asset_id_count"] == 0
    assert summary["lost_asset_id_count"] == 0
    assert summary["record_id_change_count"] == 0
    assert summary["blocked_asset_in_apply_manifest_count"] == 0


def test_storage_gaps_block_execution_and_old_plan_stays_invalid(tmp_path):
    paths = _prepared(tmp_path)
    summary = _run(paths)
    manifest = json.loads(
        (paths["apply_output"] / "resolution_apply_manifest.json").read_text(encoding="utf-8")
    )

    assert summary["conclusion"] == "C. Not ready for Apply"
    assert summary["execution_blocked"] is True
    assert "search_alias_storage_not_implemented" in manifest["blocker_reasons"]
    assert "asset_eligibility_storage_not_implemented" in manifest["blocker_reasons"]
    assert manifest["old_asset_plan_id"] == OLD_ASSET_PLAN_ID
    assert manifest["old_asset_plan_status"] == "DO NOT CONFIRM"
    assert manifest["confirm_supported"] is False
    assert manifest["execute_supported"] is False


def test_input_checksum_mismatch_blocks_future_execution(tmp_path):
    paths = _prepared(tmp_path)
    _run(paths)
    manifest = json.loads(
        (paths["apply_output"] / "resolution_apply_manifest.json").read_text(encoding="utf-8")
    )
    proposal = paths["output"] / "missing_parent_resolution_decisions.csv"
    proposal.write_text(proposal.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert _sha256(proposal) != manifest["input_checksums"]["decision_proposal"]
    assert manifest["input_checksum_match_required_for_future_execution"] is True
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_resolution_manifest_input_checksums(
            paths["apply_output"] / "resolution_apply_manifest.json",
            _manifest_input_paths(paths),
        )


def test_generation_is_read_only_and_deterministic(tmp_path):
    paths = _prepared(tmp_path)
    protected = _protected_hashes(paths)

    first = _run(paths)
    first_outputs = _directory_hashes(paths["apply_output"])
    second = _run(paths)

    assert first == second
    assert first_outputs == _directory_hashes(paths["apply_output"])
    assert _protected_hashes(paths) == protected
    assert first["formal_vault_modified"] is False
    assert first["formal_sqlite_modified"] is False
    assert first["production_slack_renderer_modified"] is False
    assert first["original_decisions_modified"] is False


def test_cli_generates_validation_and_plan_only(tmp_path, monkeypatch, capsys):
    paths = _prepared(tmp_path)
    monkeypatch.setattr(
        "marketing_knowledge_agent.cli.generate_resolution_apply_preview",
        lambda **kwargs: {
            "validation_only": True,
            "plan_only": True,
            "execution_blocked": True,
            "decisions_applied": False,
        },
    )

    exit_code = main(
        [
            "validate-missing-parent-resolution",
            "--resolution-dir", str(paths["output"]),
            "--parent-records", str(paths["parents"]),
            "--review-decisions", str(paths["reviews"]),
            "--inventory", str(paths["inventory"]),
            "--asset-apply-preview", str(paths["apply"]),
            "--asset-blocked-preview", str(paths["blocked"]),
            "--restricted-customers", str(paths["restricted"]),
            "--vault", str(paths["vault"]),
            "--db", str(paths["db"]),
            "--production-slack-renderer", str(paths["renderer"]),
            "--output", str(paths["apply_output"]),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["validation_only"] is True
    assert payload["plan_only"] is True
    assert payload["decisions_applied"] is False


def _prepared(tmp_path):
    paths = _fixture(tmp_path)
    _generate(paths)
    paths["apply_output"] = tmp_path / "apply_output"
    return paths


def _run(paths, formal_search_fn=None):
    return generate_resolution_apply_preview(
        resolution_dir=paths["output"],
        parent_records_path=paths["parents"],
        review_decisions_path=paths["reviews"],
        inventory_path=paths["inventory"],
        asset_apply_preview_path=paths["apply"],
        asset_blocked_preview_path=paths["blocked"],
        restricted_customers_path=paths["restricted"],
        vault_path=paths["vault"],
        db_path=paths["db"],
        production_slack_renderer_path=paths["renderer"],
        output_dir=paths["apply_output"],
        formal_search_fn=formal_search_fn or (lambda query: []),
    )


def _protected_hashes(paths):
    return {
        key: _hash_path(paths[key])
        for key in (
            "parents",
            "reviews",
            "inventory",
            "apply",
            "blocked",
            "restricted",
            "vault",
            "db",
            "renderer",
            "output",
        )
    }


def _manifest_input_paths(paths):
    return {
        "decision_proposal": paths["output"] / "missing_parent_resolution_decisions.csv",
        "parent_preview": paths["output"] / "parent_decision_preview.csv",
        "asset_eligibility_preview": paths["output"] / "asset_eligibility_preview.csv",
        "search_alias_preview": paths["output"] / "search_alias_preview.csv",
        "parent_records": paths["parents"],
        "original_parent_decisions": paths["reviews"],
        "asset_inventory": paths["inventory"],
        "asset_apply_preview": paths["apply"],
        "asset_blocked_preview": paths["blocked"],
        "restricted_customers": paths["restricted"],
        "formal_vault": paths["vault"],
        "formal_sqlite": paths["db"],
        "production_slack_renderer": paths["renderer"],
    }


def _directory_hashes(path):
    return {
        child.name: _sha256(child)
        for child in sorted(path.iterdir())
        if child.is_file() and not child.name.startswith("._")
    }


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(child.relative_to(path).as_posix().encode())
            digest.update(bytes.fromhex(_sha256(child)))
    return digest.hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
