from __future__ import annotations

import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlsplit

from .asset_apply_preview_reports import (
    OUTPUT_FILENAMES,
    write_asset_apply_preview_reports,
)
from .asset_metadata import NONCANONICAL_HOST_PATHS, SHORTENER_HOSTS, TRACKING_QUERY_KEYS
from .asset_review_validation import validate_asset_review_decisions


ALLOWED_FIELDS = {"asset_url", "canonical_url"}


class AssetApplyPreviewError(ValueError):
    """Raised when asset decisions cannot be previewed without touching formal data."""


def generate_asset_apply_preview(
    decisions_path: Path,
    inventory_path: Path,
    enrichment_path: Path,
    validation_dir: Path,
    output_dir: Path,
    *,
    restricted_customers_path: Optional[Path] = None,
    vault_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    workbook_path: Optional[Path] = None,
) -> dict:
    decisions_path = Path(decisions_path)
    inventory_path = Path(inventory_path)
    enrichment_path = Path(enrichment_path)
    validation_dir = Path(validation_dir)
    output_dir = Path(output_dir)
    source_paths = [decisions_path, inventory_path, enrichment_path, validation_dir]
    if restricted_customers_path is not None:
        source_paths.append(Path(restricted_customers_path))
    for optional in (vault_path, db_path, workbook_path):
        if optional is not None:
            source_paths.append(Path(optional))
    _assert_safe_output(output_dir, source_paths)
    before_hashes = {str(path): _hash_path(path) for path in source_paths}

    inventory_rows = _read_csv(inventory_path)
    enrichment_rows = _read_csv(enrichment_path)
    decision_rows = _read_csv(decisions_path)
    errors: List[dict] = []
    warnings: List[dict] = []

    with TemporaryDirectory(prefix="mka-asset-apply-validation-") as temp_dir:
        fresh_dir = Path(temp_dir)
        validation = validate_asset_review_decisions(
            decisions_path=decisions_path,
            inventory_path=inventory_path,
            enrichment_path=enrichment_path,
            output_dir=fresh_dir,
            restricted_customers_path=restricted_customers_path,
        )
        fresh_status = _read_csv(fresh_dir / "review_decision_status.csv")
        fresh_eligibility = _read_csv(fresh_dir / "apply_preview_eligibility.csv")
        validation_errors = _read_csv(fresh_dir / "review_validation_errors.csv")
        validation_warnings = _read_csv(fresh_dir / "review_validation_warnings.csv")

        errors.extend(_validation_issues(validation_errors, "error"))
        warnings.extend(_validation_issues(validation_warnings, "warning"))
        _validate_persisted_reports(
            validation_dir,
            fresh_status,
            fresh_eligibility,
            errors,
        )

    inventory_by_asset = _unique_index(inventory_rows, "asset_id", "inventory", errors)
    enrichment_by_key = _unique_pair_index(enrichment_rows, errors)
    status_by_asset = _unique_index(fresh_status, "asset_id", "validation status", errors)
    eligibility_by_asset = _unique_index(
        fresh_eligibility, "asset_id", "validation eligibility", errors
    )
    scoped_decisions = [row for row in decision_rows if _text(row.get("field")) in ALLOWED_FIELDS]
    eligible_ids = {
        asset_id
        for asset_id, row in status_by_asset.items()
        if _text(row.get("eligibility")) == "ready_for_apply_preview"
    }
    governance_blocked_ids = {
        asset_id
        for asset_id, row in status_by_asset.items()
        if _text(row.get("eligibility")) == "governance_blocked"
    }
    approved_decisions = [
        row for row in scoped_decisions if _text(row.get("review_decision")) == "approve"
    ]

    if validation["error_count"]:
        _add_issue(errors, "validation_failed", "Fresh decision validation contains errors.")
    if not validation["ready_for_apply_preview"]:
        _add_issue(errors, "validation_not_ready", "Fresh decision validation is not ready for Apply Preview.")
    for issue in validation_warnings:
        if issue.get("code") in {"duplicate_canonical_url", "canonical_tracking_parameters"}:
            _add_issue(
                errors,
                _text(issue.get("code")),
                "URL warning is a blocker at the Apply Preview gate.",
                asset_id=_text(issue.get("asset_id")),
                field=_text(issue.get("field")),
            )

    preview_rows: List[dict] = []
    blocked_rows: List[dict] = []
    if not errors:
        preview_rows, blocked_rows = _build_preview_rows(
            scoped_decisions,
            inventory_by_asset,
            enrichment_by_key,
            status_by_asset,
            eligibility_by_asset,
            errors,
        )
        _validate_duplicate_canonical(preview_rows, errors)
        _validate_conservation(
            inventory_by_asset,
            status_by_asset,
            eligible_ids,
            governance_blocked_ids,
            approved_decisions,
            preview_rows,
            blocked_rows,
            errors,
        )
    if errors:
        preview_rows = []

    after_hashes = {str(path): _hash_path(path) for path in source_paths}
    source_files_modified = before_hashes != after_hashes
    if source_files_modified:
        _add_issue(errors, "source_modified", "A protected source changed during preview generation.")
        preview_rows = []

    action_counts = Counter(row["action"] for row in [*preview_rows, *blocked_rows])
    summary = {
        "conclusion": (
            "C. Requires fixes before Apply"
            if errors
            else "B. Ready with documented limitations"
        ),
        "dry_run": True,
        "total_review_rows": len(decision_rows),
        "in_scope_review_rows": len(scoped_decisions),
        "inventory_asset_count": len(inventory_by_asset),
        "eligible_asset_count": len(eligible_ids),
        "governance_blocked_asset_count": len(governance_blocked_ids),
        "approved_field_count": len(approved_decisions),
        "preview_row_count": len(preview_rows),
        "blocked_row_count": len(blocked_rows),
        "validation_error_count": validation["error_count"],
        "validation_warning_count": validation["warning_count"],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "error_codes": dict(sorted(Counter(item["code"] for item in errors).items())),
        "warning_codes": dict(sorted(Counter(item["code"] for item in warnings).items())),
        "action_counts": dict(sorted(action_counts.items())),
        "asset_identity_stable": set(status_by_asset) == set(inventory_by_asset),
        "record_identity_stable": _record_identity_stable(
            scoped_decisions, inventory_by_asset
        ),
        "eligible_asset_conservation": {
            row["asset_id"] for row in preview_rows
        }
        == eligible_ids,
        "approved_field_conservation": len(preview_rows) == len(approved_decisions),
        "governance_exclusion_conservation": not (
            {row["asset_id"] for row in preview_rows} & governance_blocked_ids
        ),
        "source_files_modified": source_files_modified,
        "vault_modified": False,
        "formal_index_modified": False,
        "workbook_modified": False,
        "decisions_applied": False,
        "query_constraints_enabled": [],
    }
    write_asset_apply_preview_reports(
        output_dir,
        summary,
        preview_rows,
        blocked_rows,
        errors,
        warnings,
    )
    return summary


def _build_preview_rows(
    decisions: Sequence[dict],
    inventory_by_asset: Mapping[str, dict],
    enrichment_by_key: Mapping[Tuple[str, str], dict],
    status_by_asset: Mapping[str, dict],
    eligibility_by_asset: Mapping[str, dict],
    errors: List[dict],
) -> Tuple[List[dict], List[dict]]:
    preview_rows = []
    blocked_rows = []
    for decision in sorted(
        decisions,
        key=lambda row: (
            _text(row.get("record_id")),
            _text(row.get("asset_id")),
            _text(row.get("field")),
        ),
    ):
        asset_id = _text(decision.get("asset_id"))
        record_id = _text(decision.get("record_id"))
        field = _text(decision.get("field"))
        inventory = inventory_by_asset.get(asset_id)
        enrichment = enrichment_by_key.get((asset_id, field))
        status = status_by_asset.get(asset_id)
        eligibility = eligibility_by_asset.get(asset_id)
        if not all((inventory, enrichment, status, eligibility)):
            _add_issue(errors, "missing_join", "Apply Preview join is incomplete.", asset_id, field)
            continue
        if record_id != _text(inventory.get("record_id")):
            _add_issue(errors, "record_identity_mismatch", "record_id changed across sources.", asset_id, field)
            continue
        if _text(decision.get("brand_name")) != _text(inventory.get("brand_name")):
            _add_issue(errors, "brand_identity_mismatch", "brand_name changed across sources.", asset_id, field)
            continue
        if _text(decision.get("asset_type")) != _text(inventory.get("asset_type")):
            _add_issue(errors, "asset_type_mismatch", "asset_type changed across sources.", asset_id, field)
            continue
        asset_eligibility = _text(status.get("eligibility"))
        decision_value = _text(decision.get("review_decision"))
        proposed = _text(decision.get("proposed_value"))
        current = _text(enrichment.get("existing_value"))
        base = {
            "record_id": record_id,
            "asset_id": asset_id,
            "brand_name": _text(inventory.get("brand_name")),
            "asset_type": _text(inventory.get("asset_type")),
            "asset_title": _text(inventory.get("asset_title")),
            "field": field,
            "current_value": current,
            "proposed_value": proposed,
            "review_decision": decision_value,
            "reviewer": _text(decision.get("reviewer")),
            "reviewed_at": _text(decision.get("reviewed_at")),
            "provenance": _text(decision.get("provenance")),
            "source_location": _text(decision.get("source_location")),
            "eligibility": asset_eligibility,
        }
        if asset_eligibility == "governance_blocked":
            blocked_rows.append(
                {
                    **base,
                    "governance_status": "blocked",
                    "action": "blocked",
                    "reason": "Governance-blocked asset is not eligible for proposed Apply.",
                }
            )
            continue
        if asset_eligibility != "ready_for_apply_preview":
            _add_issue(errors, "unsupported_eligibility", "Asset is not eligible for Apply Preview.", asset_id, field)
            continue
        if decision_value != "approve":
            blocked_rows.append(
                {
                    **base,
                    "governance_status": "eligible",
                    "action": "excluded",
                    "reason": "Only approved URL field decisions enter proposed Apply.",
                }
            )
            continue
        url_error = _url_error(field, proposed)
        if url_error:
            _add_issue(errors, url_error, "Approved URL cannot enter Apply Preview.", asset_id, field)
            continue
        preview_rows.append(
            {
                **base,
                "governance_status": "eligible",
                "action": "no_change" if current == proposed else "update" if current else "add",
                "reason": "Exact human-approved proposal; no metadata inference.",
            }
        )
    return preview_rows, blocked_rows


def _validate_persisted_reports(
    validation_dir: Path,
    fresh_status: Sequence[dict],
    fresh_eligibility: Sequence[dict],
    errors: List[dict],
) -> None:
    status_path = validation_dir / "review_decision_status.csv"
    eligibility_path = validation_dir / "apply_preview_eligibility.csv"
    if not status_path.is_file() or not eligibility_path.is_file():
        _add_issue(errors, "missing_validation_report", "Persisted validation reports are missing.")
        return
    if _read_csv(status_path) != list(fresh_status):
        _add_issue(errors, "stale_validation_status", "Persisted decision status differs from fresh validation.")
    if _read_csv(eligibility_path) != list(fresh_eligibility):
        _add_issue(errors, "stale_validation_eligibility", "Persisted eligibility differs from fresh validation.")


def _validate_duplicate_canonical(rows: Sequence[dict], errors: List[dict]) -> None:
    by_url: Dict[str, set] = defaultdict(set)
    for row in rows:
        if row["field"] == "canonical_url":
            by_url[row["proposed_value"]].add(row["asset_id"])
    for asset_ids in by_url.values():
        if len(asset_ids) > 1:
            _add_issue(errors, "duplicate_canonical_url", "Canonical URL maps to multiple assets.")


def _validate_conservation(
    inventory_by_asset: Mapping[str, dict],
    status_by_asset: Mapping[str, dict],
    eligible_ids: set,
    blocked_ids: set,
    approved_decisions: Sequence[dict],
    preview_rows: Sequence[dict],
    blocked_rows: Sequence[dict],
    errors: List[dict],
) -> None:
    if set(inventory_by_asset) != set(status_by_asset):
        _add_issue(errors, "asset_identity_conservation", "Asset identities differ between inventory and validation.")
    if {row["asset_id"] for row in preview_rows} != eligible_ids:
        _add_issue(errors, "eligible_asset_conservation", "Eligible assets were added or lost.")
    if len(preview_rows) != len(approved_decisions):
        _add_issue(errors, "approved_field_conservation", "Approved URL field decisions were added or lost.")
    if {row["asset_id"] for row in preview_rows} & blocked_ids:
        _add_issue(errors, "governance_exclusion", "Governance-blocked asset entered proposed Apply.")
    if {row["asset_id"] for row in blocked_rows if row["action"] == "blocked"} != blocked_ids:
        _add_issue(errors, "blocked_asset_conservation", "Governance-blocked assets are not fully represented.")


def _url_error(field: str, value: str) -> str:
    if not value:
        return "approved_empty_value"
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "malformed_url"
    if parsed.username or parsed.password:
        return "credential_in_url"
    host = parsed.netloc.casefold()
    path = parsed.path.casefold()
    if host in SHORTENER_HOSTS or (host, path) in NONCANONICAL_HOST_PATHS:
        return "non_direct_url"
    if field == "canonical_url":
        query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if any(key.startswith("utm_") or key in TRACKING_QUERY_KEYS for key in query_keys):
            return "canonical_tracking_parameters"
    return ""


def _record_identity_stable(
    decisions: Sequence[dict], inventory_by_asset: Mapping[str, dict]
) -> bool:
    return all(
        _text(row.get("record_id"))
        == _text(inventory_by_asset.get(_text(row.get("asset_id")), {}).get("record_id"))
        for row in decisions
    )


def _unique_index(
    rows: Sequence[dict], key_name: str, label: str, errors: List[dict]
) -> Dict[str, dict]:
    result = {}
    for row in rows:
        key = _text(row.get(key_name))
        if not key:
            _add_issue(errors, f"missing_{label.replace(' ', '_')}_key", f"{label} row has no {key_name}.")
        elif key in result:
            _add_issue(errors, f"duplicate_{label.replace(' ', '_')}_key", f"{label} has duplicate {key_name}.", key)
        else:
            result[key] = row
    return result


def _unique_pair_index(rows: Sequence[dict], errors: List[dict]) -> Dict[Tuple[str, str], dict]:
    result = {}
    for row in rows:
        key = (_text(row.get("asset_id")), _text(row.get("field")))
        if not all(key):
            _add_issue(errors, "missing_enrichment_key", "Enrichment row lacks asset_id or field.")
        elif key in result:
            _add_issue(errors, "duplicate_enrichment_key", "Enrichment has duplicate asset_id/field.", *key)
        else:
            result[key] = row
    return result


def _validation_issues(rows: Sequence[dict], severity: str) -> List[dict]:
    return [
        {
            "severity": severity,
            "code": _text(row.get("code")) or "validation_issue",
            "asset_id": _text(row.get("asset_id")),
            "field": _text(row.get("field")),
            "message": "Fresh decision validation issue; sensitive values are not shown.",
        }
        for row in rows
    ]


def _add_issue(
    issues: List[dict],
    code: str,
    message: str,
    asset_id: str = "",
    field: str = "",
) -> None:
    issues.append(
        {
            "severity": "error",
            "code": code,
            "asset_id": asset_id,
            "field": field,
            "message": message,
        }
    )


def _read_csv(path: Path) -> List[dict]:
    if not path.is_file():
        raise AssetApplyPreviewError(f"required preview input does not exist: {path}")
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise AssetApplyPreviewError(f"preview input has no CSV header: {path}")
            return list(reader)
    except UnicodeDecodeError as exc:
        raise AssetApplyPreviewError(f"preview input is not valid UTF-8: {path}") from exc


def _assert_safe_output(output_dir: Path, source_paths: Sequence[Path]) -> None:
    output = output_dir.resolve()
    if any(
        output == source.resolve()
        or output in source.resolve().parents
        or source.resolve() in output.parents
        for source in source_paths
    ):
        raise AssetApplyPreviewError("output directory must not overwrite or contain a protected source")
    parts = {part.casefold() for part in output.parts}
    if ".mka" in parts or "obsidian_vault" in parts:
        raise AssetApplyPreviewError("Apply Preview cannot write to the formal index or Obsidian Vault")


def _hash_path(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return "missing"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
