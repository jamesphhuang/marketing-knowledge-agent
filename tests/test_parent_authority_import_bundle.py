import csv
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.parent_authority_import_bundle import (
    ParentAuthorityImportBundleError,
    create_parent_authority_import_bundle,
    default_bundle_source_specs,
    validate_parent_authority_import_bundle,
)


CREATED_AT = "2026-07-19T19:00:00+08:00"


def test_real_bundle_contains_all_required_evidence_and_validates(tmp_path):
    summary = create_parent_authority_import_bundle(**_kwargs(tmp_path))
    validation = validate_parent_authority_import_bundle(tmp_path / "bundle")

    assert summary["conclusion"] == "A. Import Bundle created and validated"
    assert summary["source_file_count"] == 21
    assert summary["manifest_file_count"] == 22
    assert summary["physical_file_count"] == 23
    assert summary["approved_parent_count"] == 96
    assert summary["parent_authority_total"] == 120
    assert summary["remaining_authority_gap"] == 0
    assert summary["expected_decision_store_event_count"] == 162
    assert validation["valid"] is True
    assert validation["read_only_reopen"] is True
    assert validation["root_manifest_hash_valid"] is True
    assert validation["file_checksum_errors"] == 0


def test_missing_required_source_fails_closed_and_leaves_no_target(tmp_path):
    specs = default_bundle_source_specs(_root())
    specs[0] = replace(specs[0], source_path=tmp_path / "missing.csv")
    kwargs = _kwargs(tmp_path)
    kwargs["source_specs"] = specs

    with pytest.raises(ParentAuthorityImportBundleError, match="missing required source"):
        create_parent_authority_import_bundle(**kwargs)

    assert not (tmp_path / "bundle").exists()


def test_source_content_mismatch_fails_closed(tmp_path):
    specs = default_bundle_source_specs(_root())
    changed = tmp_path / "approved.csv"
    changed.write_bytes(specs[0].source_path.read_bytes())
    rows = _read_csv(changed)
    rows[0]["reviewer"] = "not-admin"
    _write_csv(changed, rows)
    specs[0] = replace(specs[0], source_path=changed)
    kwargs = _kwargs(tmp_path)
    kwargs["source_specs"] = specs

    with pytest.raises(ParentAuthorityImportBundleError, match="reviewer"):
        create_parent_authority_import_bundle(**kwargs)

    assert not (tmp_path / "bundle").exists()


def test_staging_is_cleaned_when_failure_is_injected(tmp_path):
    kwargs = _kwargs(tmp_path)
    kwargs["failure_hook"] = lambda stage: (_ for _ in ()).throw(RuntimeError("boom")) if stage == "after_copy" else None

    with pytest.raises(RuntimeError, match="boom"):
        create_parent_authority_import_bundle(**kwargs)

    assert not (tmp_path / "bundle").exists()
    assert list(tmp_path.glob(".*staging-*")) == []


def test_existing_identical_bundle_is_idempotent_but_conflict_fails_closed(tmp_path):
    kwargs = _kwargs(tmp_path)
    create_parent_authority_import_bundle(**kwargs)

    second = create_parent_authority_import_bundle(**kwargs)
    assert second["idempotent_noop"] is True

    evidence = tmp_path / "bundle/evidence/approved_parent_authority.csv"
    os.chmod(evidence, 0o644)
    evidence.write_bytes(evidence.read_bytes() + b"\n")

    with pytest.raises(ParentAuthorityImportBundleError, match="existing bundle conflicts"):
        create_parent_authority_import_bundle(**kwargs)


def test_tampered_bundle_file_is_detected(tmp_path):
    create_parent_authority_import_bundle(**_kwargs(tmp_path))
    evidence = tmp_path / "bundle/evidence/approved_parent_authority.csv"
    os.chmod(evidence, 0o644)
    evidence.write_bytes(evidence.read_bytes() + b"\n")

    with pytest.raises(ParentAuthorityImportBundleError, match="checksum"):
        validate_parent_authority_import_bundle(tmp_path / "bundle")


def test_tampered_manifest_is_detected(tmp_path):
    create_parent_authority_import_bundle(**_kwargs(tmp_path))
    manifest_path = tmp_path / "bundle/bundle_manifest.json"
    os.chmod(manifest_path, 0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["approved_parent_count"] = 95
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ParentAuthorityImportBundleError, match="root manifest hash"):
        validate_parent_authority_import_bundle(tmp_path / "bundle")


def test_bundle_content_is_deterministic_for_fixed_inputs(tmp_path):
    first_kwargs = _kwargs(tmp_path / "first")
    second_kwargs = _kwargs(tmp_path / "second")
    create_parent_authority_import_bundle(**first_kwargs)
    create_parent_authority_import_bundle(**second_kwargs)

    assert _tree_hashes(first_kwargs["target_path"]) == _tree_hashes(second_kwargs["target_path"])


def test_bundle_is_read_only_and_validation_does_not_modify_it(tmp_path):
    create_parent_authority_import_bundle(**_kwargs(tmp_path))
    bundle = tmp_path / "bundle"
    before = _tree_hashes(bundle)

    result = validate_parent_authority_import_bundle(bundle)

    assert result["read_only_reopen"] is True
    assert _tree_hashes(bundle) == before
    assert all(path.stat().st_mode & 0o222 == 0 for path in bundle.rglob("*") if path.is_file())


def test_manifest_preserves_authority_special_decision_and_event_counts(tmp_path):
    create_parent_authority_import_bundle(**_kwargs(tmp_path))
    manifest = json.loads((tmp_path / "bundle/bundle_manifest.json").read_text(encoding="utf-8"))

    assert manifest["legacy_authority_count"] == 19
    assert manifest["existing_admin_resolution_count"] == 5
    assert manifest["restricted_authority_count"] == 11
    assert manifest["pending_authority_count"] == 7
    assert manifest["public_metric_authority_count"] == 4
    assert manifest["excluded_parent_authority_count"] == 10
    assert manifest["asset_url_decision_count"] == 410
    assert manifest["expected_decision_store_event_count"] == 162
    assert manifest["expected_parent_current_state_count"] == 120
    assert manifest["special_decision_validation"]["valid"] is True


def test_root_manifest_hash_excludes_only_itself(tmp_path):
    create_parent_authority_import_bundle(**_kwargs(tmp_path))
    manifest = json.loads((tmp_path / "bundle/bundle_manifest.json").read_text(encoding="utf-8"))
    stored = manifest.pop("root_manifest_hash")
    expected = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert stored == expected


def test_formal_systems_and_source_reports_are_unchanged(tmp_path):
    kwargs = _kwargs(tmp_path)
    protected = {
        "vault": _root() / "obsidian_vault",
        "db": _root() / ".mka/content_index.sqlite",
        "renderer": _root() / "src/marketing_knowledge_agent/slack_interface.py",
        "approved": _root() / "reports/parent_baseline_authority_review/baseline_parent_authority_review_template.csv",
    }
    before = {name: _path_hash(path) for name, path in protected.items()}

    summary = create_parent_authority_import_bundle(**kwargs)

    assert before == {name: _path_hash(path) for name, path in protected.items()}
    assert summary["formal_data_modified"] is False
    assert not (_root() / "data/governance/governance_decisions.sqlite").exists()


def test_create_and_validate_cli_require_no_slack_tokens(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    target = tmp_path / "bundle"
    reports = tmp_path / "reports"

    assert main([
        "create-parent-authority-import-bundle",
        "--target", str(target),
        "--output", str(reports),
        "--created-at", CREATED_AT,
    ]) == 0
    create_result = json.loads(capsys.readouterr().out)
    assert create_result["expected_decision_store_event_count"] == 162

    assert main([
        "validate-parent-authority-import-bundle",
        "--bundle", str(target),
    ]) == 0
    validate_result = json.loads(capsys.readouterr().out)
    assert validate_result["valid"] is True


def _kwargs(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return {
        "repo_root": _root(),
        "target_path": tmp_path / "bundle",
        "report_dir": tmp_path / "reports",
        "created_at": CREATED_AT,
        "source_commit": "test-commit",
        "source_branch": "test-branch",
    }


def _root():
    return Path(__file__).resolve().parents[1]


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _path_hash(path):
    path = Path(path)
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _tree_hashes(path):
    return {
        str(child.relative_to(path)): hashlib.sha256(child.read_bytes()).hexdigest()
        for child in sorted(item for item in Path(path).rglob("*") if item.is_file())
    }
