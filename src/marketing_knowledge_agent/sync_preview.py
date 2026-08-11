"""Deterministic, payload-free validation and diff preview contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .canonical_models import AssetType, ContentAssetKey, MetricId
from .google_normalization import ExcludedSourceRef, ExclusionReason
from .link_resolution import AssetResolution, AssetResolutionStatus
from .url_safety import URLRejectionCode, URLValidationResult


_SCHEMA_VERSION = "sync-preview-v1"
_ERROR_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_VALIDATION_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_HASH_REFERENCE_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_POLICY_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MARKDOWN_UNSAFE = frozenset("|`<>[]()\n\r")


class PreviewContractError(ValueError):
    """Stable, caller-payload-free WP15 contract failure."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _ERROR_CODE_PATTERN.fullmatch(code) is None:
            code = "PREVIEW_ERROR_CODE_INVALID"
        self.code = code
        super().__init__(code)


class _StructuralConstructionMeta(type):
    def __call__(cls, *args, **kwargs):
        try:
            return super().__call__(*args, **kwargs)
        except PreviewContractError:
            raise
        except TypeError:
            raise PreviewContractError(
                "PREVIEW_STRUCTURAL_CONSTRUCTION_INVALID"
            ) from None


class PreviewStatus(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    ARCHIVE = "archive"
    RESTORE = "restore"
    INCOMPLETE = "incomplete"
    EXCLUDED = "excluded"
    NEEDS_REVIEW = "needs_review"
    UNCHANGED = "unchanged"


class ValidationSeverity(str, Enum):
    BLOCKING_ERROR = "blocking_error"
    NEEDS_REVIEW = "needs_review"
    EXCLUDED = "excluded"
    WARNING = "warning"


class PreviewField(str, Enum):
    PUBLIC_METRIC = "public_metric"
    ARTICLE = "article"
    VIDEO = "video"
    PODCAST = "podcast"
    NEWS = "news"


class PreviewReasonDomain(str, Enum):
    EXCLUSION = "exclusion"
    ASSET_RESOLUTION = "asset_resolution"
    URL_REJECTION = "url_rejection"
    VALIDATION = "validation"


class ValidationReasonCode(str, metaclass=_StructuralConstructionMeta):
    """Strict already-redacted validator-code extension point."""

    __slots__ = ()

    def __new__(cls, value: str):
        if (
            type(value) is not str
            or _VALIDATION_CODE_PATTERN.fullmatch(value) is None
        ):
            raise PreviewContractError("VALIDATION_REASON_CODE_INVALID")
        return str.__new__(cls, value)


@dataclass(frozen=True)
class PreviewReason(metaclass=_StructuralConstructionMeta):
    domain: PreviewReasonDomain
    code: str

    def __post_init__(self) -> None:
        if type(self.domain) is not PreviewReasonDomain:
            raise PreviewContractError("PREVIEW_REASON_DOMAIN_INVALID")
        code = self.code
        if self.domain is PreviewReasonDomain.EXCLUSION:
            valid = type(code) is str and code == ExclusionReason.ORAL_ONLY.value
        elif self.domain is PreviewReasonDomain.ASSET_RESOLUTION:
            valid = type(code) is str and code in {
                AssetResolutionStatus.INCOMPLETE.value,
                AssetResolutionStatus.NEEDS_REVIEW.value,
            }
        elif self.domain is PreviewReasonDomain.URL_REJECTION:
            valid = type(code) is str and code in _URL_REJECTION_VALUES
        else:
            valid = type(code) is ValidationReasonCode
        if not valid:
            raise PreviewContractError("PREVIEW_REASON_CODE_INVALID")
        object.__setattr__(self, "code", str(code))


@dataclass(frozen=True)
class PreviewBuildContext(metaclass=_StructuralConstructionMeta):
    source_fingerprint: str
    policy_version: str
    normalized_hash: str

    def __post_init__(self) -> None:
        _validate_hash_reference(
            self.source_fingerprint,
            "SOURCE_FINGERPRINT_INVALID",
        )
        _validate_policy_version(self.policy_version)
        _validate_hash_reference(
            self.normalized_hash,
            "NORMALIZED_HASH_INVALID",
        )


@dataclass(frozen=True)
class PreviewDiffDecision(metaclass=_StructuralConstructionMeta):
    asset_key: ContentAssetKey
    status: PreviewStatus
    sheet_id: int
    source_row: int

    def __post_init__(self) -> None:
        if type(self.asset_key) is not ContentAssetKey:
            raise PreviewContractError("DIFF_ASSET_KEY_INVALID")
        if type(self.status) is not PreviewStatus or self.status not in _DIFF_STATUSES:
            raise PreviewContractError("DIFF_STATUS_INVALID")
        _validate_location(self.sheet_id, self.source_row)


@dataclass(frozen=True)
class RedactedValidationIssueInput(metaclass=_StructuralConstructionMeta):
    severity: ValidationSeverity
    reason_code: ValidationReasonCode
    sheet_id: int
    source_row: int
    field: PreviewField
    asset_key: Optional[ContentAssetKey]
    metric_id: Optional[MetricId]

    def __post_init__(self) -> None:
        if type(self.severity) is not ValidationSeverity:
            raise PreviewContractError("VALIDATION_SEVERITY_INVALID")
        if type(self.reason_code) is not ValidationReasonCode:
            raise PreviewContractError("VALIDATION_REASON_CODE_REQUIRED")
        _validate_location(self.sheet_id, self.source_row)
        if type(self.field) is not PreviewField:
            raise PreviewContractError("PREVIEW_FIELD_INVALID")
        _validate_identity(self.asset_key, self.metric_id)


@dataclass(frozen=True, repr=False, init=False)
class ValidationIssue:
    severity: ValidationSeverity
    reason: PreviewReason
    sheet_id: int
    source_row: int
    field: PreviewField
    asset_key: Optional[ContentAssetKey]
    metric_id: Optional[MetricId]

    def __post_init__(self) -> None:
        if type(self.severity) is not ValidationSeverity:
            raise PreviewContractError("VALIDATION_SEVERITY_INVALID")
        if type(self.reason) is not PreviewReason:
            raise PreviewContractError("PREVIEW_REASON_REQUIRED")
        _validate_location(self.sheet_id, self.source_row)
        if type(self.field) is not PreviewField:
            raise PreviewContractError("PREVIEW_FIELD_INVALID")
        _validate_identity(self.asset_key, self.metric_id)
        if self.asset_key is None and self.metric_id is None and self.reason.domain not in {
            PreviewReasonDomain.EXCLUSION,
            PreviewReasonDomain.VALIDATION,
        }:
            raise PreviewContractError("VALIDATION_ISSUE_IDENTITY_REQUIRED")

    def __repr__(self) -> str:
        return (
            "ValidationIssue("
            f"severity={self.severity.value!r}, "
            f"reason={_reason_wire(self.reason)!r}, "
            f"sheet_id={self.sheet_id!r}, "
            f"source_row={self.source_row!r}, "
            f"identity={_identity_wire(self.asset_key, self.metric_id)!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False, init=False)
class PreviewItem:
    status: PreviewStatus
    sheet_id: int
    source_row: int
    field: PreviewField
    asset_key: Optional[ContentAssetKey]
    metric_id: Optional[MetricId]
    reasons: Tuple[PreviewReason, ...]
    candidate_count: int
    rejected_count: int

    def __post_init__(self) -> None:
        if type(self.status) is not PreviewStatus:
            raise PreviewContractError("PREVIEW_STATUS_INVALID")
        _validate_location(self.sheet_id, self.source_row)
        if type(self.field) is not PreviewField:
            raise PreviewContractError("PREVIEW_FIELD_INVALID")
        _validate_identity(self.asset_key, self.metric_id)
        if type(self.reasons) is not tuple or any(
            type(reason) is not PreviewReason for reason in self.reasons
        ):
            raise PreviewContractError("PREVIEW_REASONS_INVALID")
        if self.reasons != _canonical_reasons(self.reasons):
            raise PreviewContractError("PREVIEW_REASONS_NOT_CANONICAL")
        _validate_count(self.candidate_count, "CANDIDATE_COUNT_INVALID")
        _validate_count(self.rejected_count, "REJECTED_COUNT_INVALID")

        if self.status in _REASON_REQUIRED_STATUSES and not self.reasons:
            raise PreviewContractError("PREVIEW_REASON_REQUIRED")
        if self.status not in _REASON_REQUIRED_STATUSES and self.reasons:
            raise PreviewContractError("LIFECYCLE_REASON_FORBIDDEN")
        if self.status is PreviewStatus.EXCLUDED:
            valid_exclusion = (
                self.field is PreviewField.PUBLIC_METRIC
                and self.asset_key is None
                and self.candidate_count == 0
                and self.rejected_count == 0
                and self.reasons
                == (
                    PreviewReason(
                        PreviewReasonDomain.EXCLUSION,
                        ExclusionReason.ORAL_ONLY.value,
                    ),
                )
            )
            if not valid_exclusion:
                raise PreviewContractError("EXCLUDED_ITEM_INVALID")
        elif self.asset_key is None or self.metric_id is not None:
            raise PreviewContractError("ASSET_ITEM_IDENTITY_INVALID")
        if self.asset_key is not None and self.field is not _field_for_asset_key(
            self.asset_key
        ):
            raise PreviewContractError("ASSET_FIELD_MISMATCH")
        if self.status is PreviewStatus.ARCHIVE and (
            self.candidate_count != 0 or self.rejected_count != 0
        ):
            raise PreviewContractError("ARCHIVE_COUNTS_INVALID")

    def __repr__(self) -> str:
        return (
            "PreviewItem("
            f"status={self.status.value!r}, "
            f"sheet_id={self.sheet_id!r}, "
            f"source_row={self.source_row!r}, "
            f"identity={_identity_wire(self.asset_key, self.metric_id)!r}, "
            f"candidate_count={self.candidate_count!r}, "
            f"rejected_count={self.rejected_count!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False, init=False)
class PreviewSummary:
    schema_version: str
    source_fingerprint: str
    policy_version: str
    normalized_hash: str
    status_counts: Tuple[Tuple[PreviewStatus, int], ...]
    severity_counts: Tuple[Tuple[ValidationSeverity, int], ...]
    items: Tuple[PreviewItem, ...]
    issues: Tuple[ValidationIssue, ...]

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise PreviewContractError("PREVIEW_SCHEMA_VERSION_INVALID")
        _validate_hash_reference(
            self.source_fingerprint,
            "SOURCE_FINGERPRINT_INVALID",
        )
        _validate_policy_version(self.policy_version)
        _validate_hash_reference(
            self.normalized_hash,
            "NORMALIZED_HASH_INVALID",
        )
        _validate_status_counts(self.status_counts, self.items)
        _validate_severity_counts(self.severity_counts, self.issues)
        if type(self.items) is not tuple or any(
            type(item) is not PreviewItem for item in self.items
        ):
            raise PreviewContractError("PREVIEW_ITEMS_INVALID")
        if type(self.issues) is not tuple or any(
            type(issue) is not ValidationIssue for issue in self.issues
        ):
            raise PreviewContractError("VALIDATION_ISSUES_INVALID")
        if self.items != tuple(sorted(self.items, key=_item_sort_key)):
            raise PreviewContractError("PREVIEW_ITEMS_NOT_CANONICAL")
        if self.issues != tuple(sorted(self.issues, key=_issue_sort_key)):
            raise PreviewContractError("VALIDATION_ISSUES_NOT_CANONICAL")

    def __repr__(self) -> str:
        return (
            "PreviewSummary("
            f"schema_version={self.schema_version!r}, "
            f"item_count={len(self.items)!r}, "
            f"issue_count={len(self.issues)!r})"
        )

    __str__ = __repr__


_STATUS_ORDER = tuple(PreviewStatus)
_STATUS_RANK = {status: rank for rank, status in enumerate(_STATUS_ORDER)}
_SEVERITY_ORDER = tuple(ValidationSeverity)
_SEVERITY_RANK = {
    severity: rank for rank, severity in enumerate(_SEVERITY_ORDER)
}
_DIFF_STATUSES = frozenset(
    {
        PreviewStatus.CREATE,
        PreviewStatus.UPDATE,
        PreviewStatus.ARCHIVE,
        PreviewStatus.RESTORE,
        PreviewStatus.UNCHANGED,
    }
)
_RESOLVED_DIFF_STATUSES = frozenset(
    {
        PreviewStatus.CREATE,
        PreviewStatus.UPDATE,
        PreviewStatus.RESTORE,
        PreviewStatus.UNCHANGED,
    }
)
_REASON_REQUIRED_STATUSES = frozenset(
    {
        PreviewStatus.INCOMPLETE,
        PreviewStatus.EXCLUDED,
        PreviewStatus.NEEDS_REVIEW,
    }
)
_URL_REJECTION_VALUES = frozenset(item.value for item in URLRejectionCode)
_ASSET_FIELD_BY_TYPE = {
    AssetType.ARTICLE: PreviewField.ARTICLE,
    AssetType.VIDEO: PreviewField.VIDEO,
    AssetType.PODCAST: PreviewField.PODCAST,
    AssetType.NEWS: PreviewField.NEWS,
}


def _build_canonical_constructors():
    authorization = object()

    def issue_init(self, *args, _wp15_gate=None, **values) -> None:
        if _wp15_gate is not authorization:
            raise PreviewContractError("VALIDATION_ISSUE_REQUIRES_BUILDER")
        _initialize_canonical(
            self,
            args,
            values,
            (
                "severity",
                "reason",
                "sheet_id",
                "source_row",
                "field",
                "asset_key",
                "metric_id",
            ),
        )

    def item_init(self, *args, _wp15_gate=None, **values) -> None:
        if _wp15_gate is not authorization:
            raise PreviewContractError("PREVIEW_ITEM_REQUIRES_BUILDER")
        _initialize_canonical(
            self,
            args,
            values,
            (
                "status",
                "sheet_id",
                "source_row",
                "field",
                "asset_key",
                "metric_id",
                "reasons",
                "candidate_count",
                "rejected_count",
            ),
        )

    def summary_init(self, *args, _wp15_gate=None, **values) -> None:
        if _wp15_gate is not authorization:
            raise PreviewContractError("PREVIEW_SUMMARY_REQUIRES_BUILDER")
        _initialize_canonical(
            self,
            args,
            values,
            (
                "schema_version",
                "source_fingerprint",
                "policy_version",
                "normalized_hash",
                "status_counts",
                "severity_counts",
                "items",
                "issues",
            ),
        )

    def create_issue(**values) -> ValidationIssue:
        return ValidationIssue(_wp15_gate=authorization, **values)

    def create_item(**values) -> PreviewItem:
        return PreviewItem(_wp15_gate=authorization, **values)

    def create_summary(**values) -> PreviewSummary:
        return PreviewSummary(_wp15_gate=authorization, **values)

    issue_init.__name__ = "__init__"
    issue_init.__qualname__ = "ValidationIssue.__init__"
    item_init.__name__ = "__init__"
    item_init.__qualname__ = "PreviewItem.__init__"
    summary_init.__name__ = "__init__"
    summary_init.__qualname__ = "PreviewSummary.__init__"
    return issue_init, item_init, summary_init, create_issue, create_item, create_summary


(
    ValidationIssue.__init__,
    PreviewItem.__init__,
    PreviewSummary.__init__,
    _create_validation_issue,
    _create_preview_item,
    _create_preview_summary,
) = _build_canonical_constructors()
del _build_canonical_constructors


def build_preview(
    context,
    exclusions,
    asset_resolutions,
    diff_decisions,
    validation_issues,
):
    """Compose one immutable safe preview from exact typed upstream inputs."""

    try:
        return _build_preview(
            context,
            exclusions,
            asset_resolutions,
            diff_decisions,
            validation_issues,
        )
    except PreviewContractError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise PreviewContractError("PREVIEW_BUILD_FAILED") from None


def _build_preview(
    context,
    exclusions,
    asset_resolutions,
    diff_decisions,
    validation_issues,
) -> PreviewSummary:
    if type(context) is not PreviewBuildContext:
        raise PreviewContractError("PREVIEW_CONTEXT_REQUIRED")
    _validate_collection(
        exclusions,
        ExcludedSourceRef,
        "EXCLUSIONS_TUPLE_REQUIRED",
        "EXCLUSION_ELEMENT_INVALID",
    )
    _validate_collection(
        asset_resolutions,
        AssetResolution,
        "ASSET_RESOLUTIONS_TUPLE_REQUIRED",
        "ASSET_RESOLUTION_ELEMENT_INVALID",
    )
    _validate_collection(
        diff_decisions,
        PreviewDiffDecision,
        "DIFF_DECISIONS_TUPLE_REQUIRED",
        "DIFF_DECISION_ELEMENT_INVALID",
    )
    _validate_collection(
        validation_issues,
        RedactedValidationIssueInput,
        "VALIDATION_INPUTS_TUPLE_REQUIRED",
        "VALIDATION_INPUT_ELEMENT_INVALID",
    )

    decisions_by_key = {}
    for decision in diff_decisions:
        if decision.asset_key in decisions_by_key:
            raise PreviewContractError("DUPLICATE_DIFF_DECISION")
        decisions_by_key[decision.asset_key] = decision

    resolutions_by_key = {}
    resolution_locations = {}
    for resolution in asset_resolutions:
        _validate_resolution_shape(resolution)
        if resolution.asset_key in resolutions_by_key:
            raise PreviewContractError("DUPLICATE_ASSET_RESOLUTION")
        resolutions_by_key[resolution.asset_key] = resolution
        resolution_locations[resolution.asset_key] = _resolution_location(resolution)

    items = []
    issues = []
    oral_reason = PreviewReason(
        PreviewReasonDomain.EXCLUSION,
        ExclusionReason.ORAL_ONLY.value,
    )
    for exclusion in exclusions:
        _validate_exclusion(exclusion)
        item = _create_preview_item(
            status=PreviewStatus.EXCLUDED,
            sheet_id=exclusion.sheet_id,
            source_row=exclusion.source_row,
            field=PreviewField.PUBLIC_METRIC,
            asset_key=None,
            metric_id=exclusion.metric_id,
            reasons=(oral_reason,),
            candidate_count=0,
            rejected_count=0,
        )
        items.append(item)
        issues.append(
            _create_validation_issue(
                severity=ValidationSeverity.EXCLUDED,
                reason=oral_reason,
                sheet_id=item.sheet_id,
                source_row=item.source_row,
                field=item.field,
                asset_key=None,
                metric_id=item.metric_id,
            )
        )

    for resolution in asset_resolutions:
        sheet_id, source_row = resolution_locations[resolution.asset_key]
        field = _field_for_asset_key(resolution.asset_key)
        decision = decisions_by_key.get(resolution.asset_key)
        candidate_count = len(resolution.candidates)
        rejected_count = len(resolution.rejected_occurrences)

        if resolution.status is AssetResolutionStatus.RESOLVED_CANDIDATE:
            if decision is None:
                raise PreviewContractError("RESOLVED_DIFF_DECISION_REQUIRED")
            if decision.status not in _RESOLVED_DIFF_STATUSES:
                raise PreviewContractError("RESOLVED_DIFF_STATUS_INVALID")
            if (
                decision.sheet_id != sheet_id
                or decision.source_row != source_row
            ):
                raise PreviewContractError("DIFF_LOCATION_MISMATCH")
            items.append(
                _create_preview_item(
                    status=decision.status,
                    sheet_id=sheet_id,
                    source_row=source_row,
                    field=field,
                    asset_key=resolution.asset_key,
                    metric_id=None,
                    reasons=(),
                    candidate_count=candidate_count,
                    rejected_count=rejected_count,
                )
            )
            rejection_severity = ValidationSeverity.WARNING
        else:
            if decision is not None:
                raise PreviewContractError("DIFF_FOR_UNRESOLVED_ASSET")
            reason = PreviewReason(
                PreviewReasonDomain.ASSET_RESOLUTION,
                resolution.status.value,
            )
            status = PreviewStatus(resolution.status.value)
            item = _create_preview_item(
                status=status,
                sheet_id=sheet_id,
                source_row=source_row,
                field=field,
                asset_key=resolution.asset_key,
                metric_id=None,
                reasons=(reason,),
                candidate_count=candidate_count,
                rejected_count=rejected_count,
            )
            items.append(item)
            issues.append(
                _create_validation_issue(
                    severity=ValidationSeverity.NEEDS_REVIEW,
                    reason=reason,
                    sheet_id=sheet_id,
                    source_row=source_row,
                    field=field,
                    asset_key=resolution.asset_key,
                    metric_id=None,
                )
            )
            rejection_severity = ValidationSeverity.NEEDS_REVIEW

        rejection_codes = _unique_rejection_codes(resolution)
        for rejection_code in rejection_codes:
            issues.append(
                _create_validation_issue(
                    severity=rejection_severity,
                    reason=PreviewReason(
                        PreviewReasonDomain.URL_REJECTION,
                        rejection_code.value,
                    ),
                    sheet_id=sheet_id,
                    source_row=source_row,
                    field=field,
                    asset_key=resolution.asset_key,
                    metric_id=None,
                )
            )

    for decision in diff_decisions:
        resolution = resolutions_by_key.get(decision.asset_key)
        if decision.status is PreviewStatus.ARCHIVE:
            if resolution is not None:
                raise PreviewContractError("ARCHIVE_CURRENT_ASSET_CONFLICT")
            items.append(
                _create_preview_item(
                    status=PreviewStatus.ARCHIVE,
                    sheet_id=decision.sheet_id,
                    source_row=decision.source_row,
                    field=_field_for_asset_key(decision.asset_key),
                    asset_key=decision.asset_key,
                    metric_id=None,
                    reasons=(),
                    candidate_count=0,
                    rejected_count=0,
                )
            )
        elif resolution is None:
            raise PreviewContractError("ORPHAN_DIFF_DECISION")
        elif resolution.status is not AssetResolutionStatus.RESOLVED_CANDIDATE:
            raise PreviewContractError("DIFF_FOR_UNRESOLVED_ASSET")

    for validation_input in validation_issues:
        issues.append(
            _create_validation_issue(
                severity=validation_input.severity,
                reason=PreviewReason(
                    PreviewReasonDomain.VALIDATION,
                    validation_input.reason_code,
                ),
                sheet_id=validation_input.sheet_id,
                source_row=validation_input.source_row,
                field=validation_input.field,
                asset_key=validation_input.asset_key,
                metric_id=validation_input.metric_id,
            )
        )

    canonical_items = _dedupe_and_sort_items(tuple(items))
    canonical_issues = _dedupe_and_sort_issues(tuple(issues))
    status_counts = tuple(
        (
            status,
            sum(item.status is status for item in canonical_items),
        )
        for status in _STATUS_ORDER
    )
    severity_counts = tuple(
        (
            severity,
            sum(issue.severity is severity for issue in canonical_issues),
        )
        for severity in _SEVERITY_ORDER
    )
    return _create_preview_summary(
        schema_version=_SCHEMA_VERSION,
        source_fingerprint=context.source_fingerprint,
        policy_version=context.policy_version,
        normalized_hash=context.normalized_hash,
        status_counts=status_counts,
        severity_counts=severity_counts,
        items=canonical_items,
        issues=canonical_issues,
    )


def render_preview_json(summary) -> str:
    """Render deterministic JSON from one canonical safe preview."""

    if type(summary) is not PreviewSummary:
        raise PreviewContractError("PREVIEW_SUMMARY_REQUIRED")
    try:
        rendered = json.dumps(
            _json_primitive(summary),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return rendered + "\n"
    except PreviewContractError:
        raise
    except (
        AttributeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise PreviewContractError("PREVIEW_JSON_RENDER_FAILED") from None


def render_preview_markdown(summary) -> str:
    """Render deterministic Markdown from one canonical safe preview."""

    if type(summary) is not PreviewSummary:
        raise PreviewContractError("PREVIEW_SUMMARY_REQUIRED")
    try:
        lines = [
            "# Sync Preview",
            "",
            "## Metadata",
            "",
            f"- Schema Version: `{_markdown_value(summary.schema_version)}`",
            f"- Source Fingerprint: `{_markdown_value(summary.source_fingerprint)}`",
            f"- Policy Version: `{_markdown_value(summary.policy_version)}`",
            f"- Normalized Hash: `{_markdown_value(summary.normalized_hash)}`",
            "",
            "## Status Counts",
            "",
            "| Status | Count |",
            "| --- | ---: |",
        ]
        lines.extend(
            f"| {_markdown_value(status.value)} | {count} |"
            for status, count in summary.status_counts
        )
        lines.extend(
            [
                "",
                "## Severity Counts",
                "",
                "| Severity | Count |",
                "| --- | ---: |",
            ]
        )
        lines.extend(
            f"| {_markdown_value(severity.value)} | {count} |"
            for severity, count in summary.severity_counts
        )
        lines.extend(
            [
                "",
                "## Items",
                "",
                "| Status | Sheet ID | Source Row | Field | Identity | Reasons | Candidates | Rejected |",
                "| --- | ---: | ---: | --- | --- | --- | ---: | ---: |",
            ]
        )
        for item in summary.items:
            reasons = (
                ",".join(_reason_wire(reason) for reason in item.reasons)
                if item.reasons
                else "none"
            )
            lines.append(
                "| "
                f"{_markdown_value(item.status.value)} | "
                f"{item.sheet_id} | {item.source_row} | "
                f"{_markdown_value(item.field.value)} | "
                f"{_markdown_value(_identity_wire(item.asset_key, item.metric_id))} | "
                f"{_markdown_value(reasons)} | "
                f"{item.candidate_count} | {item.rejected_count} |"
            )
        lines.extend(
            [
                "",
                "## Issues",
                "",
                "| Severity | Sheet ID | Source Row | Field | Identity | Reason |",
                "| --- | ---: | ---: | --- | --- | --- |",
            ]
        )
        for issue in summary.issues:
            lines.append(
                "| "
                f"{_markdown_value(issue.severity.value)} | "
                f"{issue.sheet_id} | {issue.source_row} | "
                f"{_markdown_value(issue.field.value)} | "
                f"{_markdown_value(_identity_wire(issue.asset_key, issue.metric_id))} | "
                f"{_markdown_value(_reason_wire(issue.reason))} |"
            )
        return "\n".join(lines) + "\n"
    except PreviewContractError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise PreviewContractError("PREVIEW_MARKDOWN_RENDER_FAILED") from None


def _initialize_canonical(self, args, values, names) -> None:
    if args or set(values) != set(names):
        raise PreviewContractError("CANONICAL_CONSTRUCTION_INVALID")
    for name in names:
        object.__setattr__(self, name, values[name])
    self.__post_init__()


def _validate_collection(collection, item_type, tuple_code, item_code) -> None:
    if type(collection) is not tuple:
        raise PreviewContractError(tuple_code)
    if any(type(item) is not item_type for item in collection):
        raise PreviewContractError(item_code)


def _validate_hash_reference(value, code) -> None:
    if type(value) is not str or _HASH_REFERENCE_PATTERN.fullmatch(value) is None:
        raise PreviewContractError(code)


def _validate_policy_version(value) -> None:
    if type(value) is not str or _POLICY_VERSION_PATTERN.fullmatch(value) is None:
        raise PreviewContractError("POLICY_VERSION_INVALID")


def _validate_location(sheet_id, source_row) -> None:
    if type(sheet_id) is not int or sheet_id < 0:
        raise PreviewContractError("SHEET_ID_INVALID")
    if type(source_row) is not int or source_row <= 0:
        raise PreviewContractError("SOURCE_ROW_INVALID")


def _validate_count(value, code) -> None:
    if type(value) is not int or value < 0:
        raise PreviewContractError(code)


def _validate_identity(asset_key, metric_id) -> None:
    if asset_key is not None and type(asset_key) is not ContentAssetKey:
        raise PreviewContractError("ASSET_KEY_INVALID")
    if metric_id is not None and type(metric_id) is not MetricId:
        raise PreviewContractError("METRIC_ID_INVALID")
    if asset_key is not None and metric_id is not None:
        raise PreviewContractError("PREVIEW_IDENTITY_CONFLICT")


def _validate_exclusion(exclusion: ExcludedSourceRef) -> None:
    _validate_location(exclusion.sheet_id, exclusion.source_row)
    if exclusion.metric_id is not None and type(exclusion.metric_id) is not MetricId:
        raise PreviewContractError("METRIC_ID_INVALID")
    if exclusion.reason is not ExclusionReason.ORAL_ONLY:
        raise PreviewContractError("EXCLUSION_REASON_INVALID")


def _validate_resolution_shape(resolution: AssetResolution) -> None:
    if type(resolution.asset_key) is not ContentAssetKey:
        raise PreviewContractError("ASSET_KEY_INVALID")
    if type(resolution.status) is not AssetResolutionStatus:
        raise PreviewContractError("ASSET_RESOLUTION_STATUS_INVALID")
    if type(resolution.candidates) is not tuple:
        raise PreviewContractError("ASSET_CANDIDATES_INVALID")
    if type(resolution.rejected_occurrences) is not tuple:
        raise PreviewContractError("ASSET_REJECTIONS_INVALID")
    _resolution_location(resolution)


def _resolution_location(resolution: AssetResolution) -> Tuple[int, int]:
    sheet_id = resolution.lineage.sheet_id
    row_index = resolution.lineage.source_row_index
    if type(sheet_id) is not int or sheet_id < 0:
        raise PreviewContractError("SHEET_ID_INVALID")
    if type(row_index) is not int or row_index < 0:
        raise PreviewContractError("SOURCE_ROW_INDEX_INVALID")
    return sheet_id, row_index + 1


def _field_for_asset_key(asset_key: ContentAssetKey) -> PreviewField:
    if type(asset_key) is not ContentAssetKey:
        raise PreviewContractError("ASSET_KEY_INVALID")
    field = _ASSET_FIELD_BY_TYPE.get(asset_key.asset_type)
    if field is None:
        raise PreviewContractError("ASSET_FIELD_MAPPING_INVALID")
    return field


def _unique_rejection_codes(
    resolution: AssetResolution,
) -> Tuple[URLRejectionCode, ...]:
    codes = {}
    for occurrence in resolution.rejected_occurrences:
        if type(occurrence) is not URLValidationResult:
            raise PreviewContractError("URL_REJECTION_RESULT_INVALID")
        code = occurrence.rejection_code
        if type(code) is not URLRejectionCode:
            raise PreviewContractError("URL_REJECTION_CODE_INVALID")
        codes[code.value] = code
    return tuple(codes[value] for value in sorted(codes))


def _canonical_reasons(
    reasons: Tuple[PreviewReason, ...],
) -> Tuple[PreviewReason, ...]:
    unique = {}
    for reason in reasons:
        if type(reason) is not PreviewReason:
            raise PreviewContractError("PREVIEW_REASON_REQUIRED")
        unique[(reason.domain.value, reason.code)] = reason
    return tuple(unique[key] for key in sorted(unique))


def _identity_parts(asset_key, metric_id) -> Tuple[int, str]:
    _validate_identity(asset_key, metric_id)
    if metric_id is not None:
        return 1, str(metric_id)
    if asset_key is not None:
        return 2, str(asset_key)
    return 0, ""


def _identity_wire(asset_key, metric_id) -> str:
    rank, value = _identity_parts(asset_key, metric_id)
    if rank == 1:
        return f"metric:{value}"
    if rank == 2:
        return f"asset:{value}"
    return "none"


def _reason_wire(reason: PreviewReason) -> str:
    if type(reason) is not PreviewReason:
        raise PreviewContractError("PREVIEW_REASON_REQUIRED")
    return f"{reason.domain.value}:{reason.code}"


def _item_identity_key(item: PreviewItem) -> tuple:
    identity_rank, identity_value = _identity_parts(item.asset_key, item.metric_id)
    return (
        item.field,
        identity_rank,
        identity_value,
        item.sheet_id,
        item.source_row,
    )


def _item_sort_key(item: PreviewItem) -> tuple:
    identity_rank, identity_value = _identity_parts(item.asset_key, item.metric_id)
    return (
        _STATUS_RANK[item.status],
        item.sheet_id,
        item.source_row,
        item.field.value,
        identity_rank,
        identity_value,
        tuple((reason.domain.value, reason.code) for reason in item.reasons),
        item.candidate_count,
        item.rejected_count,
    )


def _issue_sort_key(issue: ValidationIssue) -> tuple:
    identity_rank, identity_value = _identity_parts(
        issue.asset_key,
        issue.metric_id,
    )
    return (
        _SEVERITY_RANK[issue.severity],
        issue.sheet_id,
        issue.source_row,
        issue.field.value,
        identity_rank,
        identity_value,
        issue.reason.domain.value,
        issue.reason.code,
    )


def _dedupe_and_sort_items(items: Tuple[PreviewItem, ...]) -> Tuple[PreviewItem, ...]:
    by_key = {}
    for item in items:
        key = _item_identity_key(item)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = item
        elif existing != item:
            raise PreviewContractError("PREVIEW_ITEM_CONFLICT")
    return tuple(sorted(by_key.values(), key=_item_sort_key))


def _dedupe_and_sort_issues(
    issues: Tuple[ValidationIssue, ...],
) -> Tuple[ValidationIssue, ...]:
    unique = set(issues)
    return tuple(sorted(unique, key=_issue_sort_key))


def _validate_status_counts(status_counts, items) -> None:
    if type(items) is not tuple:
        raise PreviewContractError("PREVIEW_ITEMS_INVALID")
    if type(status_counts) is not tuple or len(status_counts) != len(_STATUS_ORDER):
        raise PreviewContractError("STATUS_COUNTS_INVALID")
    for expected, pair in zip(_STATUS_ORDER, status_counts):
        if type(pair) is not tuple or len(pair) != 2 or pair[0] is not expected:
            raise PreviewContractError("STATUS_COUNTS_NOT_CANONICAL")
        _validate_count(pair[1], "STATUS_COUNT_INVALID")
    if sum(count for _, count in status_counts) != len(items):
        raise PreviewContractError("STATUS_COUNT_CONSERVATION_FAILED")


def _validate_severity_counts(severity_counts, issues) -> None:
    if type(issues) is not tuple:
        raise PreviewContractError("VALIDATION_ISSUES_INVALID")
    if type(severity_counts) is not tuple or len(severity_counts) != len(
        _SEVERITY_ORDER
    ):
        raise PreviewContractError("SEVERITY_COUNTS_INVALID")
    for expected, pair in zip(_SEVERITY_ORDER, severity_counts):
        if type(pair) is not tuple or len(pair) != 2 or pair[0] is not expected:
            raise PreviewContractError("SEVERITY_COUNTS_NOT_CANONICAL")
        _validate_count(pair[1], "SEVERITY_COUNT_INVALID")
    if sum(count for _, count in severity_counts) != len(issues):
        raise PreviewContractError("SEVERITY_COUNT_CONSERVATION_FAILED")


def _json_primitive(summary: PreviewSummary) -> dict:
    return {
        "schema_version": summary.schema_version,
        "source_fingerprint": summary.source_fingerprint,
        "policy_version": summary.policy_version,
        "normalized_hash": summary.normalized_hash,
        "status_counts": {
            status.value: count for status, count in summary.status_counts
        },
        "severity_counts": {
            severity.value: count for severity, count in summary.severity_counts
        },
        "items": [
            {
                "status": item.status.value,
                "sheet_id": item.sheet_id,
                "source_row": item.source_row,
                "field": item.field.value,
                "asset_key": (
                    str(item.asset_key) if item.asset_key is not None else None
                ),
                "metric_id": (
                    str(item.metric_id) if item.metric_id is not None else None
                ),
                "reasons": [
                    {"domain": reason.domain.value, "code": reason.code}
                    for reason in item.reasons
                ],
                "candidate_count": item.candidate_count,
                "rejected_count": item.rejected_count,
            }
            for item in summary.items
        ],
        "issues": [
            {
                "severity": issue.severity.value,
                "reason": {
                    "domain": issue.reason.domain.value,
                    "code": issue.reason.code,
                },
                "sheet_id": issue.sheet_id,
                "source_row": issue.source_row,
                "field": issue.field.value,
                "asset_key": (
                    str(issue.asset_key) if issue.asset_key is not None else None
                ),
                "metric_id": (
                    str(issue.metric_id) if issue.metric_id is not None else None
                ),
            }
            for issue in summary.issues
        ],
    }


def _markdown_value(value: str) -> str:
    if type(value) is not str:
        raise PreviewContractError("MARKDOWN_VALUE_INVALID")
    if any(
        character in _MARKDOWN_UNSAFE
        or ord(character) < 32
        or ord(character) == 127
        for character in value
    ):
        raise PreviewContractError("MARKDOWN_VALUE_UNSAFE")
    return value


__all__ = [
    "PreviewContractError",
    "PreviewStatus",
    "ValidationSeverity",
    "PreviewField",
    "PreviewReasonDomain",
    "ValidationReasonCode",
    "PreviewReason",
    "PreviewBuildContext",
    "PreviewDiffDecision",
    "RedactedValidationIssueInput",
    "ValidationIssue",
    "PreviewItem",
    "PreviewSummary",
    "build_preview",
    "render_preview_json",
    "render_preview_markdown",
]
