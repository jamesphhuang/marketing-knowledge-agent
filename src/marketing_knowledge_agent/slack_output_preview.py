from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from .governance import metadata_allows_written_external_use
from .models import GeneratedAnswer, SearchFilters
from .pipeline import ask_index
from .query_planning import FIELD_REGISTRY
from .query_gating import RESTRICTED_QUERY_REFUSAL
from .slack_output_preview_reports import write_slack_output_preview_reports
from .slack_presentation import escape_mrkdwn_url, url_is_mrkdwn_safe


SAMPLE_QUERIES = (
    "三風製麵",
    "提供我三風製麵的內容",
    "dachun",
    "居家生活",
    "居家生活 影片",
    "2025 年採訪的品牌",
    "dachun Podcast",
    "數位轉型",
    "莉朵花藝",
    "已上線的影片",
    "2025 居家生活 Podcast",
)
VARIANTS = ("concise", "standard", "detailed")
ALLOWED_OVERLAY_FIELDS = {"asset_url", "canonical_url"}
MAX_DISPLAY_ENTITIES = 5
MAX_DISPLAY_ASSETS = 10
MAX_TITLE_CHARS = 160
# The tracked manifest is the only authority for "approved" asset URL artifacts. Writable CSV
# columns such as reviewer/review_decision/eligibility cannot self-attest without it.
AUTHORITY_MANIFEST_RELATIVE_PATH = "tests/fixtures/historical_inputs_manifest.json"
APPROVED_ASSET_URL_APPLY_PREVIEW = "reports/asset_metadata_apply_preview/asset_apply_preview.csv"
APPROVED_ASSET_URL_BLOCKED_PREVIEW = "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"
APPROVED_ASSET_URL_DECISIONS = "reports/asset_metadata_preview/human_review_template.csv"
APPROVED_ASSET_URL_INPUTS = (
    APPROVED_ASSET_URL_APPLY_PREVIEW,
    APPROVED_ASSET_URL_BLOCKED_PREVIEW,
    APPROVED_ASSET_URL_DECISIONS,
)


class SlackOutputPreviewError(ValueError):
    """Raised when a Slack output preview cannot be built without unsafe assumptions."""


class ApprovedAssetUrlAuthorityError(SlackOutputPreviewError):
    """Raised when approved asset URL artifacts are not bound to the tracked authority manifest."""


@dataclass(frozen=True)
class AssetUrlRecord:
    record_id: str
    asset_id: str
    field: str
    proposed_value: str
    reviewer: str
    reviewed_at: str


@dataclass
class AssetUrlOverlay:
    values: Dict[Tuple[str, str, str], AssetUrlRecord] = field(default_factory=dict)
    blocked_asset_ids: Set[str] = field(default_factory=set)
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)

    def value(self, record_id: str, asset_id: str, field_name: str) -> Optional[str]:
        record = self.values.get((record_id, asset_id, field_name))
        return record.proposed_value if record else None


@dataclass
class SlackPreviewBundle:
    payload: dict
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    result_identity: Tuple[Tuple[str, str, str], ...] = ()
    backend_citations: Tuple[dict, ...] = ()


def load_pinned_approved_asset_url_overlay() -> AssetUrlOverlay:
    """Load approved asset URLs from repository-pinned, hash-verified artifacts.

    This is the only entry point the Slack runtime may use. It deliberately takes no arguments:
    the repository root, the manifest, the artifact paths and the expected hashes are all fixed,
    so a caller cannot substitute writable CSV files that self-attest as approved. Any failure
    raises ApprovedAssetUrlAuthorityError, which callers must treat as "no URL enrichment".
    """
    apply_bytes, blocked_bytes, decision_bytes = _authorized_asset_url_inputs()
    # Parsing the verified bytes, rather than re-reading the paths, keeps the artifact that was
    # hashed and the artifact that is parsed identical even under a concurrent rewrite.
    return _build_asset_url_overlay(
        _parse_pinned_csv(apply_bytes),
        _parse_pinned_csv(blocked_bytes),
        _parse_pinned_csv(decision_bytes),
    )


def _authorized_asset_url_inputs() -> Tuple[bytes, bytes, bytes]:
    root = _repository_root()
    manifest = _verified_authority_manifest(root)
    payloads = []
    for relative in APPROVED_ASSET_URL_INPUTS:
        entry = manifest.get(relative)
        if entry is None:
            raise ApprovedAssetUrlAuthorityError(
                "approved asset URL input is not covered by the tracked authority manifest"
            )
        payloads.append(_verified_pinned_bytes(root / relative, entry))
    return (payloads[0], payloads[1], payloads[2])


def _repository_root() -> Path:
    """Resolve the repository root from the module location, never from the working directory."""
    root = Path(__file__).resolve().parents[2]
    if not (root / AUTHORITY_MANIFEST_RELATIVE_PATH).is_file():
        raise ApprovedAssetUrlAuthorityError(
            "tracked authority manifest is not reachable from the installed module location"
        )
    return root


def _verified_authority_manifest(root: Path) -> Dict[str, Mapping[str, object]]:
    """Parse the tracked manifest using its existing schema and enforce its self-integrity hash."""
    try:
        payload = json.loads((root / AUTHORITY_MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovedAssetUrlAuthorityError("tracked authority manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
    stored_hash = payload.get("manifest_hash")
    body = {key: value for key, value in payload.items() if key != "manifest_hash"}
    if not isinstance(stored_hash, str) or stored_hash != _hash_json(body):
        raise ApprovedAssetUrlAuthorityError(
            "tracked authority manifest failed its self-integrity contract"
        )
    inputs = payload.get("inputs")
    if not isinstance(inputs, list):
        raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
    entries: Dict[str, Mapping[str, object]] = {}
    for entry in inputs:
        if not isinstance(entry, Mapping):
            raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
        relative = entry.get("relative_path")
        if not isinstance(relative, str) or not relative:
            raise ApprovedAssetUrlAuthorityError("tracked authority manifest is malformed")
        if relative in entries:
            raise ApprovedAssetUrlAuthorityError(
                "tracked authority manifest contains a duplicate input"
            )
        entries[relative] = entry
    return entries


def _verified_pinned_bytes(path: Path, entry: Mapping[str, object]) -> bytes:
    expected_sha256 = entry.get("expected_sha256")
    expected_size = entry.get("expected_size")
    if (
        entry.get("input_type") != "file"
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
    ):
        raise ApprovedAssetUrlAuthorityError(
            "approved asset URL input has no usable pinned file hash"
        )
    if path.is_symlink() or not path.is_file():
        raise ApprovedAssetUrlAuthorityError("approved asset URL input is missing")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ApprovedAssetUrlAuthorityError("approved asset URL input is unreadable") from exc
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ApprovedAssetUrlAuthorityError(
            "approved asset URL input does not match its pinned hash"
        )
    return payload


def _parse_pinned_csv(payload: bytes) -> List[dict]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApprovedAssetUrlAuthorityError("approved asset URL input is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise ApprovedAssetUrlAuthorityError("approved asset URL input has no CSV header")
    return list(reader)


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_asset_url_overlay(
    apply_preview_path: Path,
    blocked_preview_path: Path,
    decisions_path: Path,
) -> AssetUrlOverlay:
    return _build_asset_url_overlay(
        _read_csv(Path(apply_preview_path)),
        _read_csv(Path(blocked_preview_path)),
        _read_csv(Path(decisions_path)),
    )


def _build_asset_url_overlay(
    apply_rows: Sequence[Mapping[str, object]],
    blocked_rows: Sequence[Mapping[str, object]],
    decision_rows: Sequence[Mapping[str, object]],
) -> AssetUrlOverlay:
    errors: List[dict] = []
    warnings: List[dict] = []
    decisions: Dict[Tuple[str, str, str], dict] = {}

    for row in decision_rows:
        field_name = _text(row.get("field"))
        if field_name not in ALLOWED_OVERLAY_FIELDS:
            continue
        key = _row_key(row)
        if not all(key):
            _issue(errors, "invalid_decision_join_key", "Decision row lacks record/asset/field identity.")
        elif key in decisions:
            _issue(errors, "duplicate_decision_join_key", "Decision rows contain a duplicate identity.")
        else:
            decisions[key] = row

    blocked_asset_ids = {
        _text(row.get("asset_id"))
        for row in blocked_rows
        if _text(row.get("action")) == "blocked" and _text(row.get("asset_id"))
    }
    values: Dict[Tuple[str, str, str], AssetUrlRecord] = {}
    for row in apply_rows:
        key = _row_key(row)
        record_id, asset_id, field_name = key
        if field_name not in ALLOWED_OVERLAY_FIELDS:
            _issue(errors, "unsupported_overlay_field", "Apply Preview contains a field outside the URL contract.")
            continue
        if not all(key):
            _issue(errors, "invalid_apply_join_key", "Apply Preview row lacks record/asset/field identity.")
            continue
        if key in values:
            _issue(errors, "duplicate_apply_join_key", "Apply Preview contains a duplicate identity.")
            continue
        if asset_id in blocked_asset_ids:
            _issue(errors, "blocked_asset_in_apply_preview", "A governance-blocked asset entered proposed Apply.")
            continue
        if (
            _text(row.get("eligibility")) != "ready_for_apply_preview"
            or _text(row.get("review_decision")) != "approve"
            or _text(row.get("governance_status")) != "eligible"
            or _text(row.get("action")) not in {"add", "update", "no_change"}
        ):
            _issue(errors, "ineligible_apply_row", "Apply Preview row does not satisfy the approved eligibility contract.")
            continue
        decision = decisions.get(key)
        if decision is None:
            _issue(errors, "missing_human_decision", "Apply Preview row has no matching human decision.")
            continue
        proposed_value = _text(row.get("proposed_value"))
        if not _decision_matches(row, decision):
            _issue(errors, "decision_apply_mismatch", "Apply Preview differs from the approved human decision.")
            continue
        if not _text(row.get("reviewer")) or not _valid_iso_date(_text(row.get("reviewed_at"))):
            _issue(errors, "invalid_review_attestation", "Approved URL lacks a valid reviewer or reviewed_at date.")
            continue
        if not _safe_http_url(proposed_value):
            _issue(errors, "invalid_overlay_url", "Approved URL is not safe for an offline Slack preview.")
            continue
        values[key] = AssetUrlRecord(
            record_id=record_id,
            asset_id=asset_id,
            field=field_name,
            proposed_value=proposed_value,
            reviewer=_text(row.get("reviewer")),
            reviewed_at=_text(row.get("reviewed_at")),
        )

    return AssetUrlOverlay(
        values=values,
        blocked_asset_ids=blocked_asset_ids,
        errors=errors,
        warnings=warnings,
    )


def apply_approved_asset_url_overlay(answer, overlay: AssetUrlOverlay) -> int:
    """Attach approved asset-level URLs to an externally safe structured answer."""
    generated = getattr(answer, "generated", answer)
    structured = getattr(generated, "structured_result", None)
    if structured is None or overlay.errors:
        return 0

    citations = {
        citation.label: citation
        for citation in getattr(generated, "citations", [])
        if citation.can_quote_externally
        and citation.data_classification == "public"
        and metadata_allows_written_external_use(citation)
    }
    applied = 0
    for entity in structured.matched_entities:
        for asset in entity.assets:
            record_id = asset.source_record_id
            asset_id = f"{record_id}:{asset.asset_type}"
            if asset_id in overlay.blocked_asset_ids:
                continue
            citation = citations.get(asset.citation_label)
            if (
                citation is None
                or citation.title != asset.title
                or citation.source_sheet != asset.source_sheet
                or citation.source_row != asset.source_row
                or citation.chunk_id.rsplit(":", 1)[-1] != asset.asset_type
            ):
                continue
            value = (
                overlay.value(record_id, asset_id, "canonical_url")
                or overlay.value(record_id, asset_id, "asset_url")
            )
            if not value or not _safe_http_url(value):
                continue
            asset.url = value
            citation.canonical_url = value
            applied += 1
    return applied


def build_slack_preview_payload(
    query: str,
    answer: GeneratedAnswer,
    overlay: AssetUrlOverlay,
) -> SlackPreviewBundle:
    errors = list(overlay.errors)
    warnings = list(overlay.warnings)
    structured = answer.structured_result
    if structured is None:
        if answer.answer == RESTRICTED_QUERY_REFUSAL:
            return SlackPreviewBundle(
                payload=_empty_payload("[restricted query]", "restricted_refusal"),
                warnings=list(overlay.warnings),
            )
        _issue(errors, "missing_structured_result", "Preview requires StructuredRetrievalResult.")
        return SlackPreviewBundle(
            payload=_empty_payload(query, "missing_structured_result"),
            errors=errors,
            warnings=warnings,
        )

    citation_by_label = {
        citation.label: citation
        for citation in answer.citations
        if citation.can_quote_externally and citation.data_classification == "public"
    }
    entities = []
    citations = []
    backend_citations = []
    result_identity: List[Tuple[str, str, str]] = []
    for entity in structured.matched_entities:
        assets = []
        for asset in entity.assets:
            record_id = asset.source_record_id
            asset_id = f"{record_id}:{asset.asset_type}"
            identity = (record_id, asset_id, asset.asset_type)
            if asset_id in overlay.blocked_asset_ids:
                _issue(
                    warnings,
                    "governance_blocked_asset",
                    "A governance-blocked asset was omitted from the preview.",
                    query=query,
                )
                continue
            citation = citation_by_label.get(asset.citation_label)
            if citation is None:
                _issue(
                    warnings,
                    "citation_not_external",
                    "An asset without an external-safe citation was omitted.",
                    query=query,
                )
                continue
            if (
                citation.title != asset.title
                or citation.source_sheet != asset.source_sheet
                or citation.source_row != asset.source_row
            ):
                _issue(
                    errors,
                    "citation_asset_mismatch",
                    "Citation does not map one-to-one to the structured asset.",
                    query=query,
                )
                continue
            display_url = overlay.value(record_id, asset_id, "asset_url")
            canonical_url = overlay.value(record_id, asset_id, "canonical_url")
            if display_url is None:
                _issue(
                    warnings,
                    "url_overlay_missing",
                    "Asset identity has no approved display URL; the title remains visible without a link.",
                    query=query,
                    field="asset_url",
                )
            elif not _safe_slack_url(display_url):
                _issue(
                    warnings,
                    "url_not_slack_safe",
                    "Approved URL cannot be embedded safely and was omitted from display.",
                    query=query,
                    field="asset_url",
                )
                display_url = None
            source = _safe_source(asset.source_sheet, asset.source_row)
            asset_payload = {
                "asset_type": asset.asset_type,
                "asset_type_label": _asset_label(asset.asset_type),
                "title": asset.title,
                "url": display_url,
                "external_usage": asset.external_usage_status,
                "source": source,
                "citation_label": asset.citation_label,
            }
            assets.append(asset_payload)
            citations.append(
                {
                    "label": citation.label,
                    "title": citation.title,
                    "source": source,
                    "can_quote_externally": True,
                }
            )
            backend_citations.append(
                {
                    "label": citation.label,
                    "record_id": record_id,
                    "asset_id": asset_id,
                    "canonical_url": canonical_url,
                }
            )
            result_identity.append(identity)
            if canonical_url is None:
                _issue(
                    warnings,
                    "canonical_overlay_missing",
                    "Asset identity has no approved canonical URL for backend citation metadata.",
                    query=query,
                    field="canonical_url",
                )
        if assets:
            entities.append(
                {
                    "entity_type": entity.entity_type,
                    "entity_name": entity.entity_name,
                    "merchant_handle": entity.merchant_handle,
                    "sales_category_lv1": entity.sales_category_lv1,
                    "sales_category_lv2": entity.sales_category_lv2,
                    "interview_year": entity.interview_year,
                    "assets": assets,
                }
            )

    constraints = _safe_constraints(structured.query_plan)
    total_assets = sum(len(entity["assets"]) for entity in entities)
    abstain_kind = _abstain_kind(structured, total_assets)
    payload = {
        "query": query,
        "query_type": _query_type(structured.query_plan),
        "resolved_handle": _resolved_handle(structured.query_plan),
        "applied_constraints": constraints,
        "operator": structured.query_plan.get("operator") or "AND",
        "entities": entities,
        "total_entities": len(entities),
        "total_assets": total_assets,
        "citations": _dedupe_dicts(citations, "label"),
        "abstained": bool(abstain_kind),
        "abstain_kind": abstain_kind,
        "unsupported_labels": _constraint_labels(structured.unsupported_constraints),
    }
    return SlackPreviewBundle(
        payload=payload,
        errors=errors,
        warnings=warnings,
        result_identity=tuple(result_identity),
        backend_citations=tuple(backend_citations),
    )


def render_slack_preview(payload: Mapping[str, object], variant: str) -> str:
    if variant not in VARIANTS:
        raise SlackOutputPreviewError(f"unsupported preview variant: {variant}")
    if payload.get("abstained"):
        return _render_abstention(payload)

    entities, displayed_assets = _display_subset(payload.get("entities") or [])
    lines = [_result_header(payload, len(entities), displayed_assets)]
    condition_lines = _render_conditions(payload.get("applied_constraints") or [])
    if condition_lines:
        lines.extend(["", *condition_lines])
    if variant == "concise":
        lines.extend(_render_concise(entities))
    elif variant == "standard":
        lines.extend(_render_standard(entities))
    else:
        lines.extend(_render_detailed(entities))
    lines.extend(_limit_notice(payload, entities, displayed_assets))
    return "\n".join(line for line in lines if line is not None).strip()


def preview_slack_query(
    query: str,
    variant: str,
    db_path: Path,
    apply_preview_path: Path,
    blocked_preview_path: Path,
    decisions_path: Path,
    restricted_customers_path: Path,
    *,
    ask_fn: Callable = ask_index,
) -> str:
    overlay = load_asset_url_overlay(
        Path(apply_preview_path),
        Path(blocked_preview_path),
        Path(decisions_path),
    )
    if overlay.errors:
        raise SlackOutputPreviewError("asset URL overlay did not pass the preview contract")
    with TemporaryDirectory(prefix="mka-slack-preview-audit-") as temp_dir:
        answer = ask_fn(
            query,
            Path(db_path),
            filters=SearchFilters(intent="external"),
            limit=250,
            restricted_customers_path=Path(restricted_customers_path),
            provider_name="mock",
            audit_log_path=Path(temp_dir) / "query_audit.csv",
        )
    bundle = build_slack_preview_payload(query, answer, overlay)
    if bundle.errors:
        raise SlackOutputPreviewError("structured result did not pass the preview contract")
    return render_slack_preview(bundle.payload, variant)


def generate_slack_output_preview(
    queries: Sequence[str],
    db_path: Path,
    apply_preview_path: Path,
    blocked_preview_path: Path,
    decisions_path: Path,
    restricted_customers_path: Path,
    output_dir: Path,
    *,
    vault_path: Optional[Path] = None,
    workbook_path: Optional[Path] = None,
    ask_fn: Callable = ask_index,
) -> dict:
    queries = [str(query).strip() for query in queries if str(query).strip()]
    if not queries:
        raise SlackOutputPreviewError("at least one preview query is required")
    paths = [
        Path(db_path),
        Path(apply_preview_path),
        Path(blocked_preview_path),
        Path(decisions_path),
        Path(restricted_customers_path),
    ]
    if vault_path is not None:
        paths.append(Path(vault_path))
    if workbook_path is not None:
        paths.append(Path(workbook_path))
    _assert_safe_output(Path(output_dir), paths)
    before_hashes = {str(path): _hash_path(path) for path in paths}
    overlay = load_asset_url_overlay(
        Path(apply_preview_path),
        Path(blocked_preview_path),
        Path(decisions_path),
    )
    bundles = []
    errors = list(overlay.errors)
    warnings = list(overlay.warnings)
    if not errors:
        with TemporaryDirectory(prefix="mka-slack-preview-audit-") as temp_dir:
            audit_path = Path(temp_dir) / "query_audit.csv"
            for query in queries:
                answer = ask_fn(
                    query,
                    Path(db_path),
                    filters=SearchFilters(intent="external"),
                    limit=250,
                    restricted_customers_path=Path(restricted_customers_path),
                    provider_name="mock",
                    audit_log_path=audit_path,
                )
                bundle = build_slack_preview_payload(query, answer, overlay)
                bundles.append(bundle)
                errors.extend(bundle.errors)
                warnings.extend(bundle.warnings)

    after_hashes = {str(path): _hash_path(path) for path in paths}
    source_files_modified = before_hashes != after_hashes
    if source_files_modified:
        _issue(errors, "protected_source_modified", "A protected source changed during preview generation.")

    payloads = [bundle.payload for bundle in bundles]
    summary = {
        "conclusion": (
            "C. Requires fixes before format decision"
            if errors
            else "A. Ready for output format decision"
        ),
        "preview_only": True,
        "query_count": len(queries),
        "payload_count": len(payloads),
        "approved_overlay_field_count": len(overlay.values),
        "approved_overlay_asset_count": len({key[1] for key in overlay.values}),
        "governance_blocked_asset_count": len(overlay.blocked_asset_ids),
        "result_asset_count": sum(payload["total_assets"] for payload in payloads),
        "backend_citation_count": sum(len(bundle.backend_citations) for bundle in bundles),
        "abstained_query_count": sum(bool(payload["abstained"]) for payload in payloads),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "source_files_modified": source_files_modified,
        "slack_api_called": False,
        "slack_token_read": False,
        "asset_urls_applied": False,
        "query_constraints_enabled": [],
        "variants": list(VARIANTS),
        "variant_result_sets_identical": True,
    }
    write_slack_output_preview_reports(
        Path(output_dir),
        summary,
        payloads,
        errors,
        warnings,
    )
    return summary


def _render_abstention(payload: Mapping[str, object]) -> str:
    kind = payload.get("abstain_kind")
    query = _slack_text(payload.get("query"))
    if kind == "unsupported_constraint":
        labels = "、".join(_slack_text(value) for value in payload.get("unsupported_labels") or [])
        target = f"「{labels}」" if labels else "其中一個條件"
        return (
            f"目前資料尚不支援以{target}進行可靠篩選，因此未執行此次搜尋。\n\n"
            "目前可使用品牌名稱、Handle、Sales Category、採訪年份、內容標籤及內容類型進行搜尋。"
        )
    if kind == "restricted_refusal":
        return RESTRICTED_QUERY_REFUSAL
    if kind == "zero_intersection":
        conditions = "、".join(
            f"{_slack_text(item.get('label'))}：{_slack_text(item.get('value'))}"
            for item in payload.get("applied_constraints") or []
        )
        return f"找不到同時符合所有條件（{conditions}）且可供 Slack 使用的內容。"
    if kind == "governance_filtered":
        return "目前找不到符合條件且可供 Slack 對外使用的內容。"
    return (
        f"目前找不到與「{query}」精確匹配且可供 Slack 使用的內容。\n\n"
        "請確認品牌名稱、Merchant Handle，或該資料是否已進入正式內容索引。"
    )


def _result_header(payload: Mapping[str, object], displayed_entities: int, displayed_assets: int) -> str:
    total_entities = int(payload.get("total_entities") or 0)
    total_assets = int(payload.get("total_assets") or 0)
    entities = payload.get("entities") or []
    resolved_handle = payload.get("resolved_handle")
    if resolved_handle and len(entities) == 1:
        return (
            f"找到 Handle「{_slack_text(resolved_handle)}」對應品牌："
            f"{_slack_text(entities[0].get('entity_name'))}\n共 {total_assets} 筆內容："
        )
    if payload.get("query_type") == "brand" and len(entities) == 1:
        return f"找到「{_slack_text(entities[0].get('entity_name'))}」相關內容，共 {total_assets} 筆："
    if total_entities > displayed_entities or total_assets > displayed_assets:
        return (
            f"共找到 {total_entities} 個品牌、{total_assets} 筆內容；"
            f"目前顯示 {displayed_entities} 個品牌、{displayed_assets} 筆內容。"
        )
    return f"共找到 {total_entities} 個品牌、{total_assets} 筆內容。"


def _render_conditions(constraints: Sequence[Mapping[str, object]]) -> List[str]:
    visible = [item for item in constraints if item.get("field") not in {"entity_name", "merchant_handle"}]
    if not visible:
        return []
    return ["已套用搜尋條件："] + [
        f"- {_slack_text(item.get('label'))}：{_slack_text(item.get('value'))}" for item in visible
    ]


def _render_concise(entities: Sequence[Mapping[str, object]]) -> List[str]:
    lines: List[str] = []
    multiple = len(entities) > 1
    for entity in entities:
        if multiple:
            lines.extend(["", _slack_text(entity.get("entity_name"))])
        for asset in entity.get("assets") or []:
            title = _display_title(asset.get("title"))
            link = _slack_link(asset.get("url"), title)
            lines.extend(
                [
                    "",
                    f"• {_slack_text(asset.get('asset_type_label'))}｜{link}",
                    f"  對外引用：{'可' if asset.get('external_usage') == '可對外引用' else '不可'}",
                ]
            )
    sources = _unique_sources(entities)
    if sources:
        lines.extend(["", "資料來源：" + "、".join(sources)])
    return lines


def _render_standard(entities: Sequence[Mapping[str, object]]) -> List[str]:
    lines: List[str] = []
    number = 1
    multiple = len(entities) > 1
    for entity in entities:
        if multiple:
            lines.extend(["", _slack_text(entity.get("entity_name"))])
        for asset in entity.get("assets") or []:
            lines.extend(
                [
                    "",
                    f"{number}. {_slack_text(asset.get('asset_type_label'))}",
                    f"標題：{_display_title(asset.get('title'))}",
                    f"查看內容：{_slack_link(asset.get('url'), '開啟' + _slack_text(asset.get('asset_type_label')))}",
                    f"對外引用：{_slack_text(asset.get('external_usage'))}",
                ]
            )
            number += 1
    sources = _unique_sources(entities)
    if sources:
        lines.extend(["", "資料來源：" + "、".join(sources)])
    return lines


def _render_detailed(entities: Sequence[Mapping[str, object]]) -> List[str]:
    lines: List[str] = []
    for entity in entities:
        lines.extend(
            [
                "",
                f"品牌：{_slack_text(entity.get('entity_name'))}",
                f"Sales Category LV1：{_slack_text(entity.get('sales_category_lv1')) or '資料未提供'}",
                f"Sales Category LV2：{_slack_text(entity.get('sales_category_lv2')) or '資料未提供'}",
            ]
        )
        if entity.get("merchant_handle"):
            lines.append(f"Handle：{_slack_text(entity.get('merchant_handle'))}")
        for asset in entity.get("assets") or []:
            lines.extend(
                [
                    "",
                    f"• 內容類型：{_slack_text(asset.get('asset_type_label'))}",
                    f"  標題：{_display_title(asset.get('title'))}",
                    f"  查看內容：{_slack_link(asset.get('url'), '開啟' + _slack_text(asset.get('asset_type_label')))}",
                    f"  對外引用：{_slack_text(asset.get('external_usage'))}",
                ]
            )
        sources = _unique_sources([entity])
        if sources:
            lines.append("資料來源：" + "、".join(sources))
    return lines


def _limit_notice(
    payload: Mapping[str, object],
    displayed_entities: Sequence[Mapping[str, object]],
    displayed_assets: int,
) -> List[str]:
    total_entities = int(payload.get("total_entities") or 0)
    total_assets = int(payload.get("total_assets") or 0)
    if total_entities <= len(displayed_entities) and total_assets <= displayed_assets:
        return []
    lines = [""]
    if total_entities > len(displayed_entities):
        lines.append(f"目前只顯示前 {len(displayed_entities)} 個品牌。")
    if total_assets > displayed_assets:
        lines.append(f"目前只顯示前 {displayed_assets} 筆內容。")
    lines.append("請使用品牌名稱或內容類型縮小範圍；完整檢索集合未被改變。")
    return lines


def _display_subset(entities: Sequence[Mapping[str, object]]) -> Tuple[List[dict], int]:
    selected = []
    asset_count = 0
    for entity in entities[:MAX_DISPLAY_ENTITIES]:
        remaining = MAX_DISPLAY_ASSETS - asset_count
        if remaining <= 0:
            break
        assets = list(entity.get("assets") or [])[:remaining]
        if not assets:
            continue
        selected.append({**entity, "assets": assets})
        asset_count += len(assets)
    return selected, asset_count


def _safe_constraints(plan: Mapping[str, object]) -> List[dict]:
    result = []
    for constraint in plan.get("supported_constraints") or plan.get("hard_filters") or []:
        if constraint.get("support_status", "supported") != "supported":
            continue
        field_name = _text(constraint.get("field"))
        definition = FIELD_REGISTRY.get(field_name)
        label = constraint.get("output_label") or (definition.output_label if definition else field_name)
        result.append(
            {
                "field": field_name,
                "label": str(label),
                "value": _constraint_value(field_name, constraint.get("value"), constraint.get("operator")),
            }
        )
    return result


def _constraint_labels(constraints: Iterable[Mapping[str, object]]) -> List[str]:
    labels = []
    for constraint in constraints:
        field_name = _text(constraint.get("field"))
        definition = FIELD_REGISTRY.get(field_name)
        label = constraint.get("output_label") or (definition.output_label if definition else field_name)
        if label and label not in labels:
            labels.append(str(label))
    return labels


def _constraint_value(field_name: str, value: object, operator: object) -> str:
    if operator == "range" and isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}～{value[1]}"
    if field_name == "asset_type":
        return _asset_label(_text(value))
    if field_name == "external_usage_status":
        return "可對外引用" if value else "不可對外引用"
    return _text(value)


def _query_type(plan: Mapping[str, object]) -> str:
    fields = {
        _text(item.get("field"))
        for item in plan.get("supported_constraints") or plan.get("hard_filters") or []
    }
    if "merchant_handle" in fields:
        return "handle"
    if fields & {"entity_name", "merchant_name"}:
        return "brand"
    if fields & {"sales_category_lv1", "sales_category_lv2"}:
        return "category"
    if "interview_year" in fields:
        return "year"
    if "content_tags" in fields:
        return "tag"
    if "asset_type" in fields:
        return "asset_type"
    return "lookup"


def _resolved_handle(plan: Mapping[str, object]) -> Optional[str]:
    for constraint in plan.get("supported_constraints") or plan.get("hard_filters") or []:
        if constraint.get("field") == "merchant_handle":
            return _text(constraint.get("value")) or None
    return None


def _abstain_kind(structured, total_assets: int) -> Optional[str]:
    if (
        structured.unsupported_constraints
        or structured.abstain_reason in {
            "unsupported_hard_constraint",
            "ambiguous_hard_constraint",
            "invalid_hard_constraint",
        }
    ):
        return "unsupported_constraint"
    if total_assets:
        return None
    if structured.total_assets and not total_assets:
        return "governance_filtered"
    if structured.abstain_reason == "no_constraint_intersection":
        return "zero_intersection"
    return "no_exact_match"


def _safe_source(source_sheet: object, source_row: object) -> str:
    sheet = _slack_text(source_sheet) or "未知來源"
    row = str(source_row) if source_row is not None else "?"
    return f"{sheet} r{row}"


def _display_title(value: object) -> str:
    title = _slack_text(value) or "資料未提供"
    if len(title) <= MAX_TITLE_CHARS:
        return title
    head = MAX_TITLE_CHARS - 25
    return f"{title[:head]}…{title[-24:]}"


def _slack_link(url: object, label: str) -> str:
    value = _text(url)
    if not value or not _safe_slack_url(value):
        return f"{_slack_text(label)}（連結未提供）"
    return f"<{escape_mrkdwn_url(value)}|{_slack_text(label)}>"


def _slack_text(value: object) -> str:
    return _text(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unique_sources(entities: Sequence[Mapping[str, object]]) -> List[str]:
    sources = []
    for entity in entities:
        for asset in entity.get("assets") or []:
            source = _slack_text(asset.get("source"))
            if source and source not in sources:
                sources.append(source)
    return sources


def _safe_http_url(value: str) -> bool:
    if not value or not url_is_mrkdwn_safe(value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not (parsed.username or parsed.password)
    )


def _valid_iso_date(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _safe_slack_url(value: str) -> bool:
    return _safe_http_url(value)


def _decision_matches(apply_row: Mapping[str, object], decision: Mapping[str, object]) -> bool:
    return all(
        _text(apply_row.get(column)) == _text(decision.get(column))
        for column in (
            "record_id",
            "asset_id",
            "brand_name",
            "asset_type",
            "field",
            "proposed_value",
            "review_decision",
            "reviewer",
            "reviewed_at",
            "provenance",
            "source_location",
        )
    )


def _row_key(row: Mapping[str, object]) -> Tuple[str, str, str]:
    return (
        _text(row.get("record_id")),
        _text(row.get("asset_id")),
        _text(row.get("field")),
    )


def _asset_label(value: str) -> str:
    return {
        "article": "文章",
        "video": "影片",
        "podcast": "Podcast",
        "news": "新聞",
        "other": "其他",
    }.get(value, "其他")


def _dedupe_dicts(values: Sequence[dict], key: str) -> List[dict]:
    result = []
    seen = set()
    for value in values:
        marker = value.get(key)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _empty_payload(query: str, reason: str) -> dict:
    return {
        "query": query,
        "query_type": "lookup",
        "resolved_handle": None,
        "applied_constraints": [],
        "operator": "AND",
        "entities": [],
        "total_entities": 0,
        "total_assets": 0,
        "citations": [],
        "abstained": True,
        "abstain_kind": reason,
        "unsupported_labels": [],
    }


def _issue(
    issues: List[dict],
    code: str,
    message: str,
    *,
    query: str = "",
    field: str = "",
) -> None:
    issues.append({"code": code, "query": query, "field": field, "message": message})


def _read_csv(path: Path) -> List[dict]:
    if not path.is_file():
        raise SlackOutputPreviewError(f"required preview input does not exist: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SlackOutputPreviewError(f"preview input has no CSV header: {path}")
        return list(reader)


def _assert_safe_output(output_dir: Path, source_paths: Sequence[Path]) -> None:
    output = output_dir.resolve()
    for source in source_paths:
        resolved = source.resolve()
        if output == resolved or output in resolved.parents or resolved in output.parents:
            raise SlackOutputPreviewError("output directory must not overwrite or contain a protected source")
    parts = {part.casefold() for part in output.parts}
    if ".mka" in parts or "obsidian_vault" in parts:
        raise SlackOutputPreviewError("Slack preview cannot write to the formal index or Obsidian Vault")


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
