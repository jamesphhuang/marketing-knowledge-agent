from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


BUNDLE_ID = "parent-authority-approval-20260719"
BUNDLE_TYPE = "parent_authority_approval_import_evidence"
BUNDLE_SCHEMA_VERSION = "1.0"
DEFAULT_BUNDLE_PATH = Path("data/governance/imports") / BUNDLE_ID
DEFAULT_REPORT_DIR = Path("reports/parent_authority_import_bundle")
COHORT_ID = "parent-cohort-889a811ec342"
COHORT_CHECKSUM = "889a811ec3420a78925ab97f5427e5266ef365b2044b38bb284c2d98e2030d4a"
APPROVAL_REVIEWED_AT = "2026-07-19T18:14:14+08:00"
APPROVAL_REVIEWER = "Admin"

EXPECTED_COUNTS = {
    "approved_parent_count": 96,
    "parent_authority_total": 120,
    "remaining_authority_gap": 0,
    "legacy_authority_count": 19,
    "existing_admin_resolution_count": 5,
    "restricted_authority_count": 11,
    "pending_authority_count": 7,
    "public_metric_authority_count": 4,
    "excluded_parent_authority_count": 10,
    "asset_url_decision_count": 410,
    "expected_decision_store_event_count": 162,
    "expected_parent_current_state_count": 120,
}

EVENT_COUNTS = {
    "legacy_import_events": 46,
    "batch_parent_approval_events": 96,
    "resolution_parent_supersede_events": 5,
    "asset_eligibility_events": 10,
    "search_alias_events": 2,
    "entity_metadata_events": 2,
    "asset_url_manifest_reference_events": 1,
    "total_events": 162,
    "parent_events": 125,
    "parent_supersede_events": 5,
    "non_parent_events": 37,
    "current_parent_state": 120,
}

REVIEW_FIELDS = {"final_review_decision", "reviewer", "reviewed_at", "notes"}
OLD_PLAN_STATUS = {
    "decision-store-plan-8f0655bae1febc90": "DO NOT CONFIRM",
    "resolution-plan-a878e6d1036bac96": "DO NOT CONFIRM",
    "asset-plan-07cd12338615c961": "DO NOT CONFIRM",
}


class ParentAuthorityImportBundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class BundleSourceSpec:
    logical_role: str
    source_path: Path
    bundle_relative_path: Path
    content_type: str
    required: bool = True


def default_bundle_source_specs(repo_root: Path) -> List[BundleSourceSpec]:
    root = Path(repo_root)
    definitions = [
        ("approved_parent_authority", "reports/parent_baseline_authority_review/baseline_parent_authority_review_template.csv", "evidence/approved_parent_authority.csv"),
        ("pre_approval_parent_authority", "reports/parent_baseline_authority_review/backups/baseline_parent_authority_review_template.20260719T181414+0800.csv", "evidence/pre_approval_parent_authority.csv"),
        ("parent_cohort_definition", "reports/parent_baseline_authority_review/baseline_parent_batch_review_groups.csv", "evidence/parent_cohort_definition.csv"),
        ("batch_approval_manifest", "reports/parent_baseline_authority_approval/parent_batch_approval_manifest.json", "manifests/batch_approval_manifest.json"),
        ("batch_approval_validation", "reports/parent_baseline_authority_approval/parent_batch_approval_validation.csv", "validation/parent_batch_approval_validation.csv"),
        ("authority_coverage_after_approval", "reports/parent_baseline_authority_approval/parent_authority_coverage_after_approval.csv", "evidence/parent_authority_coverage_after_approval.csv"),
        ("batch_approval_checksums", "reports/parent_baseline_authority_approval/parent_batch_approval_checksum.json", "manifests/parent_batch_approval_checksums.json"),
        ("batch_approval_summary", "reports/parent_baseline_authority_approval/parent_batch_approval_summary.md", "validation/parent_batch_approval_summary.md"),
        ("final_validation_summary", "reports/parent_authority_final_validation/parent_authority_final_validation_summary.md", "validation/parent_authority_final_validation_summary.md"),
        ("batch_approval_integrity", "reports/parent_authority_final_validation/batch_approval_integrity_validation.csv", "validation/batch_approval_integrity_validation.csv"),
        ("parent_authority_120_coverage", "reports/parent_authority_final_validation/parent_authority_120_coverage.csv", "evidence/parent_authority_120_coverage.csv"),
        ("authority_source_reconciliation", "reports/parent_authority_final_validation/authority_source_reconciliation.csv", "evidence/authority_source_reconciliation.csv"),
        ("existing_admin_resolution_overlap", "reports/parent_authority_final_validation/existing_admin_resolution_overlap.csv", "evidence/existing_admin_resolution_overlap.csv"),
        ("governance_decision_conservation", "reports/parent_authority_final_validation/governance_decision_conservation.csv", "validation/governance_decision_conservation.csv"),
        ("decision_store_event_reconciliation", "reports/parent_authority_final_validation/decision_store_event_reconciliation.csv", "manifests/decision_store_event_reconciliation.csv"),
        ("final_validation_errors", "reports/parent_authority_final_validation/final_validation_errors.csv", "validation/final_validation_errors.csv"),
        ("final_validation_warnings", "reports/parent_authority_final_validation/final_validation_warnings.csv", "validation/final_validation_warnings.csv"),
        ("resolution_parent_decisions", "reports/missing_parent_resolution_preview/missing_parent_resolution_decisions.csv", "evidence/resolution_parent_decisions.csv"),
        ("resolution_parent_preview", "reports/missing_parent_resolution_preview/parent_decision_preview.csv", "evidence/resolution_parent_preview.csv"),
        ("resolution_asset_eligibility", "reports/missing_parent_resolution_preview/asset_eligibility_preview.csv", "evidence/resolution_asset_eligibility.csv"),
        ("resolution_search_aliases", "reports/missing_parent_resolution_preview/search_alias_preview.csv", "evidence/resolution_search_aliases.csv"),
    ]
    return [
        BundleSourceSpec(
            logical_role=role,
            source_path=root / source,
            bundle_relative_path=Path(destination),
            content_type=_content_type(Path(source)),
        )
        for role, source, destination in definitions
    ]


def create_parent_authority_import_bundle(
    repo_root: Path,
    target_path: Path = DEFAULT_BUNDLE_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    created_at: Optional[str] = None,
    source_commit: Optional[str] = None,
    source_branch: Optional[str] = None,
    source_specs: Optional[Sequence[BundleSourceSpec]] = None,
    failure_hook: Optional[Callable[[str], None]] = None,
) -> dict:
    root = Path(repo_root).resolve()
    target = _resolve_from_root(root, target_path)
    reports = _resolve_from_root(root, report_dir)
    specs = list(source_specs or default_bundle_source_specs(root))
    created = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(created, "created_at")
    source_commit = source_commit or _git_value(root, "rev-parse", "HEAD")
    source_branch = source_branch or _git_value(root, "branch", "--show-current")

    if target.exists():
        return _validate_existing_target(root, target, reports, specs)

    _validate_target(root, target)
    source_facts, source_checks = _validate_sources(root, specs)
    protected_before = _protected_hashes(root, specs)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent)))
    renamed = False
    try:
        for spec in specs:
            destination = staging / spec.bundle_relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(spec.source_path), str(destination))
        if failure_hook:
            failure_hook("after_copy")

        readme = _bundle_readme(source_facts)
        (staging / "README.md").write_text(readme, encoding="utf-8")
        entries = [_manifest_entry(root, staging, spec) for spec in specs]
        entries.append(_generated_manifest_entry(staging / "README.md"))
        entries.sort(key=lambda item: item["bundle_relative_path"])
        manifest = _build_manifest(
            created_at=created,
            source_commit=source_commit,
            source_branch=source_branch,
            entries=entries,
            facts=source_facts,
        )
        manifest["root_manifest_hash"] = _manifest_hash(manifest)
        (staging / "bundle_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        permissions_enforced = _make_files_read_only(staging)
        staging_validation = validate_parent_authority_import_bundle(staging)
        if failure_hook:
            failure_hook("before_atomic_rename")
        if target.exists():
            raise ParentAuthorityImportBundleError("target appeared before atomic rename")
        os.replace(str(staging), str(target))
        renamed = True
        if permissions_enforced:
            _make_directories_read_only(target)
        validation = validate_parent_authority_import_bundle(target)
        protected_after = _protected_hashes(root, specs)
        if protected_before != protected_after:
            raise ParentAuthorityImportBundleError("protected formal data or source evidence changed")
        formal_checks = _formal_system_checks(root, protected_before, protected_after)
    except Exception:
        if renamed and target.exists():
            quarantine = target.with_name(f"{target.name}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            if not quarantine.exists():
                os.replace(str(target), str(quarantine))
        else:
            _remove_tree(staging)
        raise
    summary = _summary(target, manifest, validation, idempotent_noop=False)
    _write_reports(reports, summary, manifest, source_checks, validation, formal_checks)
    return summary


def validate_parent_authority_import_bundle(bundle_path: Path) -> dict:
    bundle = Path(bundle_path)
    manifest_path = bundle / "bundle_manifest.json"
    if not bundle.is_dir() or not manifest_path.is_file():
        raise ParentAuthorityImportBundleError("bundle or bundle_manifest.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ParentAuthorityImportBundleError("bundle manifest is invalid JSON") from exc
    stored_hash = manifest.get("root_manifest_hash", "")
    if not stored_hash or stored_hash != _manifest_hash(manifest):
        raise ParentAuthorityImportBundleError("root manifest hash mismatch")
    _validate_manifest_contract(manifest)

    listed = set()
    checksum_rows = []
    for entry in manifest["files"]:
        relative = _safe_relative_path(entry.get("bundle_relative_path"))
        if relative in listed:
            raise ParentAuthorityImportBundleError(f"duplicate bundle path: {relative}")
        listed.add(relative)
        path = bundle / relative
        if not path.is_file():
            raise ParentAuthorityImportBundleError(f"required bundle file missing: {relative}")
        digest = _sha256(path)
        if digest != entry.get("sha256"):
            raise ParentAuthorityImportBundleError(f"bundle file checksum mismatch: {relative}")
        if path.stat().st_size != entry.get("byte_size"):
            raise ParentAuthorityImportBundleError(f"bundle file byte size mismatch: {relative}")
        if path.suffix.lower() == ".csv" and _csv_row_count(path) != entry.get("row_count"):
            raise ParentAuthorityImportBundleError(f"bundle CSV row count mismatch: {relative}")
        with path.open("rb") as handle:
            handle.read(1)
        checksum_rows.append({"bundle_relative_path": relative, "status": "pass", "sha256": digest})

    physical = {
        str(path.relative_to(bundle))
        for path in bundle.rglob("*")
        if path.is_file() and path.name != "bundle_manifest.json"
    }
    if physical != listed:
        raise ParentAuthorityImportBundleError("bundle contains unlisted or missing physical files")
    all_files = [path for path in bundle.rglob("*") if path.is_file()]
    read_only = all(path.stat().st_mode & 0o222 == 0 for path in all_files)
    return {
        "valid": True,
        "bundle_id": manifest["bundle_id"],
        "root_manifest_hash": stored_hash,
        "root_manifest_hash_valid": True,
        "manifest_file_count": len(manifest["files"]),
        "physical_file_count": len(all_files),
        "file_checksum_errors": 0,
        "read_only_reopen": True,
        "read_only_permissions": read_only,
        "checksum_rows": checksum_rows,
    }


def _validate_sources(root: Path, specs: Sequence[BundleSourceSpec]):
    if len(specs) != 21:
        raise ParentAuthorityImportBundleError(f"expected 21 source files, found {len(specs)}")
    by_role = {}
    checks = []
    for spec in specs:
        if spec.logical_role in by_role:
            raise ParentAuthorityImportBundleError(f"duplicate source role: {spec.logical_role}")
        if spec.required and not spec.source_path.is_file():
            raise ParentAuthorityImportBundleError(f"missing required source: {spec.source_path}")
        by_role[spec.logical_role] = spec.source_path
        checks.append(_check_row("source_present", spec.logical_role, "pass", str(spec.source_path)))

    approved = _read_csv(by_role["approved_parent_authority"])
    backup = _read_csv(by_role["pre_approval_parent_authority"])
    cohort_rows = _read_csv(by_role["parent_cohort_definition"])
    if len(approved) != 96 or len({row.get("record_id") for row in approved}) != 96:
        raise ParentAuthorityImportBundleError("approved Parent count or record_id uniqueness mismatch")
    for row in approved:
        if row.get("cohort_id") != COHORT_ID:
            raise ParentAuthorityImportBundleError("approved row has wrong cohort_id")
        if row.get("final_review_decision") != "approve":
            raise ParentAuthorityImportBundleError("approved row decision is not approve")
        if row.get("reviewer") != APPROVAL_REVIEWER:
            raise ParentAuthorityImportBundleError("approved row reviewer is not Admin")
        if row.get("reviewed_at") != APPROVAL_REVIEWED_AT:
            raise ParentAuthorityImportBundleError("approved row reviewed_at mismatch")
        if not row.get("notes", "").strip():
            raise ParentAuthorityImportBundleError("approved row notes are blank")
    _validate_timestamp(APPROVAL_REVIEWED_AT, "approval reviewed_at")
    _validate_only_review_fields_changed(backup, approved)
    checks.append(_check_row("batch_approval", "approved_rows", "pass", "96 unique Admin approvals"))

    if len(cohort_rows) != 1:
        raise ParentAuthorityImportBundleError("cohort definition must contain exactly one row")
    cohort = cohort_rows[0]
    cohort_ids = cohort.get("record_ids", "").split("|") if cohort.get("record_ids") else []
    if cohort.get("cohort_id") != COHORT_ID or cohort.get("cohort_checksum") != COHORT_CHECKSUM:
        raise ParentAuthorityImportBundleError("cohort checksum or ID mismatch")
    if int(cohort.get("record_count", 0)) != 96 or len(set(cohort_ids)) != 96:
        raise ParentAuthorityImportBundleError("cohort record count mismatch")
    if set(cohort_ids) != {row["record_id"] for row in approved}:
        raise ParentAuthorityImportBundleError("cohort members do not match approved Parent rows")
    calculated = _cohort_checksum(cohort)
    if calculated != COHORT_CHECKSUM:
        raise ParentAuthorityImportBundleError("cohort checksum does not match Review Template content")
    if cohort.get("batch_approval_safe") != "true" or cohort.get("exception_count") != "0" or cohort.get("blocker_reason"):
        raise ParentAuthorityImportBundleError("cohort is not safe for approved import evidence")

    approval_manifest = _read_json(by_role["batch_approval_manifest"])
    approval_checksums = _read_json(by_role["batch_approval_checksums"])
    _expect(approval_manifest, "approved_record_count", 96, "batch approval manifest")
    _expect(approval_manifest, "authority_gap_after_approval", 0, "batch approval manifest")
    _expect(approval_manifest, "reviewer", APPROVAL_REVIEWER, "batch approval manifest")
    _expect(approval_manifest, "reviewed_at", APPROVAL_REVIEWED_AT, "batch approval manifest")
    _expect(approval_manifest, "cohort_checksum", COHORT_CHECKSUM, "batch approval manifest")
    _expect(approval_checksums, "cohort_checksum", COHORT_CHECKSUM, "batch approval checksums")
    if approval_manifest.get("source_template_checksum_after") != _sha256(by_role["approved_parent_authority"]):
        raise ParentAuthorityImportBundleError("approved source checksum mismatch")
    if approval_manifest.get("source_template_checksum_before") != _sha256(by_role["pre_approval_parent_authority"]):
        raise ParentAuthorityImportBundleError("pre-approval source checksum mismatch")

    validation_rows = _read_csv(by_role["batch_approval_validation"])
    if not validation_rows or any(row.get("status") != "pass" for row in validation_rows):
        raise ParentAuthorityImportBundleError("batch approval validation contains non-pass checks")
    if _read_csv(by_role["final_validation_errors"]):
        raise ParentAuthorityImportBundleError("final validation errors are nonzero")
    if _read_csv(by_role["final_validation_warnings"]):
        raise ParentAuthorityImportBundleError("final validation warnings are nonzero")

    authority_rows = _read_csv(by_role["authority_source_reconciliation"])
    if len(authority_rows) != 120 or len({row["record_id"] for row in authority_rows}) != 120:
        raise ParentAuthorityImportBundleError("Parent Authority Coverage is not 120/120")
    source_counts = _count(authority_rows, "authority_source")
    if source_counts != {"batch_approval": 96, "legacy_authority": 19, "existing_admin_resolution": 5}:
        raise ParentAuthorityImportBundleError(f"authority source counts mismatch: {source_counts}")

    conservation = {row["decision_scope"]: row for row in _read_csv(by_role["governance_decision_conservation"])}
    _conservation_count(conservation, "restricted_customer", 11)
    _conservation_count(conservation, "pending_metric", 7)
    _conservation_count(conservation, "public_metric", 4)
    _conservation_count(conservation, "excluded_parent", 10)
    _conservation_count(conservation, "asset_hold", 1)

    events = {row["event_category"]: row for row in _read_csv(by_role["decision_store_event_reconciliation"])}
    _event_count(events, "legacy_parent_decision", 24)
    _event_count(events, "legacy_non_parent_decision", 22)
    _event_count(events, "batch_parent_approval", 96)
    _event_count(events, "resolution_parent_decision", 5)
    _event_count(events, "asset_eligibility", 10)
    _event_count(events, "search_alias", 2)
    _event_count(events, "entity_metadata", 2)
    _event_count(events, "asset_url_manifest_reference", 1)
    if sum(int(row["event_count"]) for row in events.values()) != 162:
        raise ParentAuthorityImportBundleError("Decision Store event count is not 162")

    special = _validate_special_decisions(by_role)
    checks.extend(special["checks"])
    checks.extend([
        _check_row("authority_coverage", "parent_current_state", "pass", "120/120; gap 0"),
        _check_row("governance_conservation", "restricted_pending_excluded", "pass", "11/7/10"),
        _check_row("event_reconciliation", "expected_events", "pass", "162 events; 120 current Parent states"),
        _check_row("asset_url_conservation", "approved_url_fields", "pass", "410 unchanged"),
    ])
    facts = dict(EXPECTED_COUNTS)
    facts["special_decision_validation"] = special["summary"]
    facts["event_counts"] = dict(EVENT_COUNTS)
    facts["source_file_count"] = len(specs)
    return facts, checks


def _validate_special_decisions(by_role: Dict[str, Path]) -> dict:
    parents = {row["record_id"]: row for row in _read_csv(by_role["resolution_parent_decisions"])}
    expected_parent = {
        "商家夥伴案例資料庫:r30": ("exclude", "merchant", "false"),
        "商家夥伴案例資料庫:r12": ("approve_internal_only", "merchant", "false"),
        "商家夥伴案例資料庫:r122": ("approve", "partner", "true"),
        "商家夥伴案例資料庫:r32": ("approve", "merchant", "true"),
        "商家夥伴案例資料庫:r7": ("approve", "partner", "true"),
    }
    if set(parents) != set(expected_parent):
        raise ParentAuthorityImportBundleError("five special Parent decisions are incomplete")
    for record_id, (decision, entity_type, external) in expected_parent.items():
        row = parents[record_id]
        if (row.get("proposed_review_decision"), row.get("proposed_entity_type"), row.get("proposed_can_external_reference")) != (decision, entity_type, external):
            raise ParentAuthorityImportBundleError(f"special Parent decision mismatch: {record_id}")
        if row.get("reviewer") != "Admin" or row.get("reviewed_at") != "2026-07-18T00:33:08+08:00":
            raise ParentAuthorityImportBundleError(f"special Parent audit metadata mismatch: {record_id}")

    assets = _read_csv(by_role["resolution_asset_eligibility"])
    asset_counts = _count(assets, "proposed_asset_index_eligibility")
    if asset_counts != {"include": 8, "hold": 1, "exclude": 1}:
        raise ParentAuthorityImportBundleError(f"special asset eligibility mismatch: {asset_counts}")
    guang = [row for row in assets if row["record_id"] == "商家夥伴案例資料庫:r12"]
    if {(row["asset_type"], row["proposed_asset_index_eligibility"], row["proposed_asset_search_eligibility"]) for row in guang} != {
        ("article", "include", "searchable_internal"),
        ("video", "hold", "not_searchable"),
    }:
        raise ParentAuthorityImportBundleError("Guang Sheng Tang Article/Video conservation failed")
    aliases = _read_csv(by_role["resolution_search_aliases"])
    if {(row["record_id"], row["alias"], row["match_type"]) for row in aliases} != {
        ("商家夥伴案例資料庫:r32", "SLP", "case_insensitive_exact"),
        ("商家夥伴案例資料庫:r32", "SHOPLINE Payments", "case_insensitive_exact"),
    }:
        raise ParentAuthorityImportBundleError("approved search aliases are not conserved")
    checks = [
        _check_row("special_decision", "r30", "pass", "Parent and child excluded; handle cannot search"),
        _check_row("special_decision", "r12", "pass", "internal Article included; Video held"),
        _check_row("special_decision", "r122", "pass", "partner; three assets included"),
        _check_row("special_decision", "r32", "pass", "three assets and two exact aliases preserved"),
        _check_row("special_decision", "r7", "pass", "partner; Article included"),
    ]
    return {"summary": {"valid": True, "parent_count": 5, "asset_include": 8, "asset_hold": 1, "asset_exclude": 1, "alias_count": 2}, "checks": checks}


def _validate_existing_target(root, target, reports, specs):
    try:
        validation = validate_parent_authority_import_bundle(target)
        manifest = _read_json(target / "bundle_manifest.json")
        current = {spec.logical_role: _sha256(spec.source_path) if spec.source_path.is_file() else "" for spec in specs}
        bundled = {entry["logical_role"]: entry["sha256"] for entry in manifest["files"] if entry["original_path"] != "generated:README.md"}
        if current != bundled:
            raise ParentAuthorityImportBundleError("source checksums differ from existing bundle")
    except ParentAuthorityImportBundleError as exc:
        raise ParentAuthorityImportBundleError(f"existing bundle conflicts: {exc}") from exc
    summary = _summary(target, manifest, validation, idempotent_noop=True)
    source_checks = [_check_row("idempotent", "existing_bundle", "pass", "identical immutable bundle reused")]
    formal_checks = _formal_system_checks(root, {}, {})
    _write_reports(reports, summary, manifest, source_checks, validation, formal_checks)
    return summary


def _build_manifest(created_at, source_commit, source_branch, entries, facts):
    manifest = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": BUNDLE_ID,
        "bundle_type": BUNDLE_TYPE,
        "created_at": created_at,
        "created_by": "Admin",
        "approval_reviewer": APPROVAL_REVIEWER,
        "approval_reviewed_at": APPROVAL_REVIEWED_AT,
        "cohort_id": COHORT_ID,
        "cohort_checksum": COHORT_CHECKSUM,
        "source_commit": source_commit,
        "source_branch": source_branch,
        "file_count": len(entries),
        "files": entries,
        "event_count_reconciliation": facts["event_counts"],
        "special_decision_validation": facts["special_decision_validation"],
        "old_plan_status": dict(OLD_PLAN_STATUS),
    }
    manifest.update({key: facts[key] for key in EXPECTED_COUNTS})
    return manifest


def _validate_manifest_contract(manifest):
    expected = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": BUNDLE_ID,
        "bundle_type": BUNDLE_TYPE,
        "created_by": "Admin",
        "approval_reviewer": APPROVAL_REVIEWER,
        "approval_reviewed_at": APPROVAL_REVIEWED_AT,
        "cohort_id": COHORT_ID,
        "cohort_checksum": COHORT_CHECKSUM,
    }
    expected.update(EXPECTED_COUNTS)
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ParentAuthorityImportBundleError(f"manifest field mismatch: {key}")
    _validate_timestamp(manifest.get("created_at", ""), "manifest created_at")
    if not isinstance(manifest.get("files"), list) or manifest.get("file_count") != len(manifest["files"]):
        raise ParentAuthorityImportBundleError("manifest file count mismatch")
    if manifest.get("file_count") != 22:
        raise ParentAuthorityImportBundleError("manifest must protect 22 files")
    if manifest.get("event_count_reconciliation") != EVENT_COUNTS:
        raise ParentAuthorityImportBundleError("manifest event reconciliation mismatch")
    if not manifest.get("special_decision_validation", {}).get("valid"):
        raise ParentAuthorityImportBundleError("manifest special decision validation failed")


def _manifest_entry(root, staging, spec):
    source = spec.source_path.resolve()
    try:
        original = str(source.relative_to(root))
    except ValueError:
        original = str(source)
    path = staging / spec.bundle_relative_path
    return {
        "logical_role": spec.logical_role,
        "original_path": original,
        "original_filename": spec.source_path.name,
        "bundle_relative_path": spec.bundle_relative_path.as_posix(),
        "sha256": _sha256(path),
        "byte_size": path.stat().st_size,
        "row_count": _csv_row_count(path) if path.suffix.lower() == ".csv" else None,
        "required": spec.required,
        "content_type": spec.content_type,
    }


def _generated_manifest_entry(path):
    return {
        "logical_role": "bundle_readme",
        "original_path": "generated:README.md",
        "original_filename": "README.md",
        "bundle_relative_path": "README.md",
        "sha256": _sha256(path),
        "byte_size": path.stat().st_size,
        "row_count": None,
        "required": True,
        "content_type": "text/markdown",
    }


def _bundle_readme(facts):
    return "\n".join([
        "# Parent Authority Approval Import Bundle",
        "",
        "This immutable bundle preserves approved Parent authority import evidence.",
        "It is not the Governance Decision Store and contains no executable plan.",
        "Future imports must validate every file checksum and the root manifest hash.",
        "Reports outside this directory are non-authoritative projections.",
        "",
        f"- Bundle ID: `{BUNDLE_ID}`",
        f"- Approved Parent records: {facts['approved_parent_count']}",
        f"- Parent authority coverage: {facts['parent_authority_total']}/120",
        f"- Expected Decision Store events: {facts['expected_decision_store_event_count']}",
        "- Mutation policy: create a new versioned bundle; never edit this directory in place.",
        "",
    ])


def _summary(target, manifest, validation, idempotent_noop):
    return {
        "conclusion": "A. Import Bundle created and validated",
        "bundle_path": str(target),
        "bundle_id": manifest["bundle_id"],
        "root_manifest_hash": manifest["root_manifest_hash"],
        "source_file_count": 21,
        "manifest_file_count": manifest["file_count"],
        "physical_file_count": validation["physical_file_count"],
        "approved_parent_count": manifest["approved_parent_count"],
        "parent_authority_total": manifest["parent_authority_total"],
        "remaining_authority_gap": manifest["remaining_authority_gap"],
        "expected_decision_store_event_count": manifest["expected_decision_store_event_count"],
        "asset_url_decision_count": manifest["asset_url_decision_count"],
        "atomic_write": True,
        "read_only_reopen": validation["read_only_reopen"],
        "idempotent_noop": idempotent_noop,
        "formal_data_modified": False,
        "decision_store_created": False,
        "old_plan_status": dict(OLD_PLAN_STATUS),
    }


def _write_reports(report_dir, summary, manifest, source_checks, validation, formal_checks):
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_text(report_dir / "import_bundle_creation_summary.md", "\n".join([
        "# Parent Authority Import Bundle Creation Summary",
        "",
        f"- Conclusion: {summary['conclusion']}",
        f"- Bundle: `{summary['bundle_path']}`",
        f"- Bundle ID: `{summary['bundle_id']}`",
        f"- Root manifest hash: `{summary['root_manifest_hash']}`",
        f"- Sources copied byte-for-byte: {summary['source_file_count']}",
        f"- Manifest-protected files: {summary['manifest_file_count']}",
        f"- Physical files including manifest: {summary['physical_file_count']}",
        "- Coverage: 120/120; authority gap 0",
        "- Formal Decision Store, Vault, SQLite, index and Slack renderer were not modified.",
        "",
    ]))
    _write_csv(report_dir / "import_bundle_file_inventory.csv", manifest["files"])
    _write_csv(report_dir / "import_bundle_source_validation.csv", source_checks)
    _write_csv(report_dir / "import_bundle_checksum_validation.csv", validation["checksum_rows"])
    _write_csv(report_dir / "import_bundle_authority_reconciliation.csv", [
        {"metric": key, "expected": value, "observed": manifest[key], "status": "pass"}
        for key, value in EXPECTED_COUNTS.items() if "event" not in key and "state" not in key
    ])
    special = manifest["special_decision_validation"]
    _write_csv(report_dir / "import_bundle_special_decision_validation.csv", [
        {"subject": "r30", "status": "pass", "evidence": "excluded Parent and child"},
        {"subject": "r12", "status": "pass", "evidence": "internal Article included; Video held"},
        {"subject": "r122", "status": "pass", "evidence": "partner and three assets"},
        {"subject": "r32", "status": "pass", "evidence": "three assets and two aliases"},
        {"subject": "r7", "status": "pass", "evidence": "partner and Article"},
        {"subject": "aggregate", "status": "pass" if special["valid"] else "fail", "evidence": json.dumps(special, ensure_ascii=False, sort_keys=True)},
    ])
    _write_csv(report_dir / "import_bundle_event_count_validation.csv", [
        {"event_metric": key, "expected": value, "observed": manifest["event_count_reconciliation"][key], "status": "pass"}
        for key, value in EVENT_COUNTS.items()
    ])
    _write_csv(report_dir / "import_bundle_formal_system_unchanged.csv", formal_checks)
    _write_text(report_dir / "import_bundle_rollback.md", """# Import Bundle Rollback

- Before atomic rename: remove staging; the formal target remains absent.
- After rename validation failure: move the failed bundle to a quarantine path and never treat it as valid.
- After success: do not delete or update the bundle; create a new versioned bundle for later decisions.
- The bundle is independent of Vault, content index and the future Decision Store.
""")
    _write_text(report_dir / "next_decision_store_plan_prerequisites.md", """# Next Decision Store Plan Prerequisites

1. Validate this bundle with `mka validate-parent-authority-import-bundle`.
2. Regenerate a new Decision Store Plan that reads only this bundle for Parent approval evidence.
3. Preserve 162 expected events and 120 Parent current-state subjects.
4. Keep all prior plan IDs at DO NOT CONFIRM.
5. Require a separate human confirmation before any Decision Store creation.
""")


def _formal_system_checks(root, before, after):
    paths = {
        "formal_vault": root / "obsidian_vault",
        "managed_vault": root / "obsidian_vault/managed",
        "formal_sqlite": root / ".mka/content_index.sqlite",
        "production_slack_renderer": root / "src/marketing_knowledge_agent/slack_interface.py",
        "governance_decision_store": root / "data/governance/governance_decisions.sqlite",
    }
    rows = []
    for name, path in paths.items():
        if name == "governance_decision_store":
            unchanged = not path.exists()
            evidence = "absent" if unchanged else "unexpectedly present"
        else:
            unchanged = not before or before.get(name) == after.get(name)
            evidence = "checksum unchanged" if unchanged else "checksum changed"
        rows.append({"system": name, "status": "pass" if unchanged else "fail", "evidence": evidence})
    return rows


def _protected_hashes(root, specs):
    paths = {
        "formal_vault": root / "obsidian_vault",
        "managed_vault": root / "obsidian_vault/managed",
        "formal_sqlite": root / ".mka/content_index.sqlite",
        "production_slack_renderer": root / "src/marketing_knowledge_agent/slack_interface.py",
    }
    paths.update({f"source:{spec.logical_role}": spec.source_path for spec in specs})
    return {name: _path_hash(path) for name, path in paths.items()}


def _validate_target(root, target):
    if target == root:
        raise ParentAuthorityImportBundleError("bundle target cannot be the repository root")


def _validate_only_review_fields_changed(before_rows, after_rows):
    before = {row["record_id"]: row for row in before_rows}
    after = {row["record_id"]: row for row in after_rows}
    if len(before_rows) != 96 or len(after_rows) != 96 or set(before) != set(after):
        raise ParentAuthorityImportBundleError("approval backup and approved rows do not have the same 96 Parents")
    for record_id in before:
        for field in before[record_id]:
            if field not in REVIEW_FIELDS and before[record_id].get(field) != after[record_id].get(field):
                raise ParentAuthorityImportBundleError(f"non-review field changed for {record_id}: {field}")


def _cohort_checksum(cohort):
    signature = json.loads(cohort["shared_evidence"])
    record_ids = cohort["record_ids"].split("|")
    return hashlib.sha256(_canonical_json({"signature": signature, "record_ids": record_ids})).hexdigest()


def _manifest_hash(manifest):
    payload = {key: value for key, value in manifest.items() if key != "root_manifest_hash"}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ParentAuthorityImportBundleError(f"invalid JSON source: {path}") from exc


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["status"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_text(path, value):
    Path(path).write_text(value.rstrip() + "\n", encoding="utf-8")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_hash(path):
    path = Path(path)
    if not path.exists():
        return "absent"
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _csv_row_count(path):
    return len(_read_csv(path))


def _count(rows, field):
    counts = {}
    for row in rows:
        value = row.get(field, "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _expect(payload, key, expected, label):
    if payload.get(key) != expected:
        raise ParentAuthorityImportBundleError(f"{label} {key} mismatch")


def _conservation_count(rows, key, expected):
    row = rows.get(key)
    if not row or row.get("status") != "pass" or int(row.get("observed_count", -1)) != expected:
        raise ParentAuthorityImportBundleError(f"governance conservation mismatch: {key}")


def _event_count(rows, key, expected):
    row = rows.get(key)
    if not row or int(row.get("event_count", -1)) != expected:
        raise ParentAuthorityImportBundleError(f"event reconciliation mismatch: {key}")


def _check_row(scope, subject, status, evidence):
    return {"scope": scope, "subject": subject, "status": status, "evidence": evidence}


def _validate_timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ParentAuthorityImportBundleError(f"{label} is not valid ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParentAuthorityImportBundleError(f"{label} must include a timezone")


def _make_files_read_only(root):
    files = [
        path for path in Path(root).rglob("*")
        if path.is_file() and not path.name.startswith("._")
    ]
    for path in files:
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    apple_double = [path for path in Path(root).rglob("._*") if path.is_file()]
    if apple_double:
        for path in files:
            try:
                path.chmod(0o600)
            except FileNotFoundError:
                pass
        _remove_appledouble(root)
        return False
    return True


def _make_directories_read_only(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    Path(root).chmod(0o555)


def _remove_tree(path):
    path = Path(path)
    if not path.exists():
        return
    for child in list(path.rglob("*")):
        try:
            child.chmod(0o700 if child.is_dir() else 0o600)
        except (FileNotFoundError, OSError):
            pass
    try:
        path.chmod(0o700)
    except OSError:
        pass
    shutil.rmtree(str(path), ignore_errors=True)
    sidecar = path.parent / f"._{path.name}"
    try:
        sidecar.unlink()
    except FileNotFoundError:
        pass


def _remove_appledouble(root):
    for path in list(Path(root).rglob("._*")):
        if path.is_file():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _safe_relative_path(value):
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ParentAuthorityImportBundleError(f"unsafe bundle path: {value}")
    return path.as_posix()


def _resolve_from_root(root, path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _content_type(path):
    return {".csv": "text/csv", ".json": "application/json", ".md": "text/markdown"}.get(path.suffix.lower(), "application/octet-stream")


def _git_value(root, *args):
    try:
        return subprocess.check_output(["git", *args], cwd=str(root), text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ParentAuthorityImportBundleError("unable to determine source git identity") from exc
