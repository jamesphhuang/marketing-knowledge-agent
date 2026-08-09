from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import marketing_knowledge_agent.captured_content as captured_content
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    CanonicalSourceLineage,
    ContentAssetKey,
    MetricId,
    SourceRecordId,
)
from marketing_knowledge_agent.captured_content import (
    AuthorityRole,
    CapturedContent,
    CapturedContentId,
    CaptureStatus,
    EvidenceRelationshipId,
    SafeHttpMetadata,
    Section,
)
from marketing_knowledge_agent.cell_normalization import (
    InheritanceReason,
    SourceFieldLineage,
    SourceLineage,
)
from marketing_knowledge_agent.link_resolution import (
    AssetSourceSlot,
    LinkCandidate,
    LinkSource,
)
from marketing_knowledge_agent.url_safety import validate_and_canonicalize_url


SYNTHETIC_CAPTURE_BODY = "SYNTHETIC_CAPTURE_BODY_9F41"
SYNTHETIC_SECTION_BODY = "SYNTHETIC_SECTION_BODY_7C21"
UTC = timezone.utc
CAPTURED_AT = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
LAST_SUCCESS = datetime(2026, 8, 9, 2, 0, tzinfo=UTC)
LAST_ATTEMPT = datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def _canonical_url(raw_url: str):
    result = validate_and_canonicalize_url(
        LinkCandidate(
            raw_url=raw_url,
            source=LinkSource.CELL_HYPERLINK,
            asset_source_slot=AssetSourceSlot.ARTICLE,
            lineage=SourceLineage(
                spreadsheet_id="synthetic-spreadsheet",
                sheet_id=109,
                sheet_title="Synthetic Captured Content",
                sheet_hidden=False,
                source_row_index=6,
                source_column_index=7,
                source_fingerprint="sha256:synthetic",
                sync_batch_id="SYNTHETIC-WP9-BATCH",
            ),
            field_lineage=SourceFieldLineage(
                field_name="article",
                target_row_index=6,
                target_column_index=7,
                value_row_index=6,
                value_column_index=7,
                merge_anchor_row_index=None,
                merge_anchor_column_index=None,
                merge_range=None,
                inherited_from_merge=False,
                inheritance_reason=InheritanceReason.LOCAL,
            ),
        )
    )
    assert result.canonical_url is not None
    return result.canonical_url


def _source_lineage() -> CanonicalSourceLineage:
    return CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=109,
        sheet_title="Synthetic Captured Content",
        source_row=7,
        source_columns={"source_record_id": "M", "article": "H"},
        source_ranges={"article": "H7"},
        source_fingerprint="sha256:synthetic-source-fingerprint",
        sync_batch_id="SYNTHETIC-WP9-BATCH",
    )


def _primary_success_payload(**overrides):
    payload = {
        "captured_content_id": CapturedContentId("capture-001"),
        "asset_key": ContentAssetKey(
            SourceRecordId("MREC-0001"),
            AssetType.ARTICLE,
        ),
        "metric_id": None,
        "evidence_relationship_id": None,
        "authority_role": AuthorityRole.PRIMARY_CONTENT,
        "source_url": _canonical_url("https://example.test/source"),
        "canonical_url": _canonical_url("https://example.test/canonical"),
        "source_domain": "example.test",
        "content_type": "text/html; charset=utf-8",
        "title": "Synthetic Article",
        "clean_body": SYNTHETIC_CAPTURE_BODY,
        "section_structure": (
            Section(heading="Synthetic Heading", text="Synthetic section text."),
        ),
        "capture_status": CaptureStatus.SUCCESS,
        "captured_at": CAPTURED_AT,
        "last_successful_capture_at": LAST_SUCCESS,
        "last_capture_attempt_at": LAST_ATTEMPT,
        "content_hash": "synthetic-content-hash-current",
        "parser_version": "synthetic-parser-v1",
        "source_http_metadata": SafeHttpMetadata(
            status_code=200,
            content_type="text/html",
            etag='"synthetic-etag"',
            last_modified="Sat, 09 Aug 2026 01:00:00 GMT",
            verified_final_url=_canonical_url("https://example.test/canonical"),
        ),
        "previous_content_hash": None,
        "searchable": True,
        "source_lineage": _source_lineage(),
        "sync_batch_id": "SYNTHETIC-WP9-BATCH",
    }
    payload.update(overrides)
    return payload


def _evidence_success_payload(**overrides):
    payload = _primary_success_payload(
        asset_key=None,
        metric_id=MetricId("MET-0001"),
        evidence_relationship_id=EvidenceRelationshipId("evidence-link-001"),
        authority_role=AuthorityRole.EVIDENCE,
    )
    payload.update(overrides)
    return payload


def _bodyless_payload(status: CaptureStatus, **overrides):
    payload = _primary_success_payload(
        capture_status=status,
        clean_body=None,
        section_structure=(),
        content_hash=None,
        parser_version=None,
        captured_at=None,
        searchable=False,
    )
    if status is CaptureStatus.UNAVAILABLE:
        payload["last_capture_attempt_at"] = LAST_ATTEMPT
    else:
        payload["last_capture_attempt_at"] = None
    payload.update(overrides)
    return payload


def _model_dump(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()  # pragma: no cover - Pydantic 1.x


def _model_field_names(model_type):
    fields = getattr(model_type, "model_fields", None)
    if fields is None:  # pragma: no cover - Pydantic 1.x
        fields = model_type.__fields__
    return set(fields)


@pytest.mark.parametrize(
    "identifier",
    ["capture-001", "CAP-EXAMPLE-001", "opaque:capture:alpha", "文字識別碼"],
)
def test_captured_content_id_accepts_opaque_exact_text(identifier):
    value = CapturedContentId(identifier)

    assert type(value) is CapturedContentId
    assert str(value) == identifier


@pytest.mark.parametrize(
    "identifier",
    ["", "   ", " capture-001", "capture-001 ", "capture\x00id", "capture\nid"],
)
def test_captured_content_id_rejects_blank_edge_whitespace_and_ascii_controls(
    identifier,
):
    with pytest.raises((TypeError, ValueError), match="CAPTURED_CONTENT_ID_INVALID"):
        CapturedContentId(identifier)


@pytest.mark.parametrize("value", [None, 1, True, b"capture-001"])
def test_captured_content_id_requires_exact_string(value):
    with pytest.raises(TypeError, match="CAPTURED_CONTENT_ID_TEXT_REQUIRED"):
        CapturedContentId(value)


@pytest.mark.parametrize(
    "identifier",
    ["evidence-link-001", "EVID-EXAMPLE-001", "opaque:evidence:alpha"],
)
def test_evidence_relationship_id_accepts_opaque_exact_text(identifier):
    value = EvidenceRelationshipId(identifier)

    assert type(value) is EvidenceRelationshipId
    assert str(value) == identifier


@pytest.mark.parametrize(
    "identifier",
    ["", "   ", " evidence-link-001", "evidence-link-001 ", "evidence\rid"],
)
def test_evidence_relationship_id_rejects_blank_edge_whitespace_and_controls(
    identifier,
):
    with pytest.raises(
        (TypeError, ValueError),
        match="EVIDENCE_RELATIONSHIP_ID_INVALID",
    ):
        EvidenceRelationshipId(identifier)


def test_primary_parent_requires_only_content_asset_key():
    record = CapturedContent(**_primary_success_payload())

    assert record.authority_role is AuthorityRole.PRIMARY_CONTENT
    assert type(record.asset_key) is ContentAssetKey
    assert record.metric_id is None
    assert record.evidence_relationship_id is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"asset_key": None},
        {"metric_id": MetricId("MET-0001")},
        {"evidence_relationship_id": EvidenceRelationshipId("evidence-001")},
        {
            "metric_id": MetricId("MET-0001"),
            "evidence_relationship_id": EvidenceRelationshipId("evidence-001"),
        },
    ],
)
def test_primary_parent_rejects_missing_or_evidence_parent_fields(overrides):
    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_PRIMARY_PARENT_INVALID"):
        CapturedContent(**_primary_success_payload(**overrides))


def test_evidence_parent_requires_metric_and_stable_relationship():
    record = CapturedContent(**_evidence_success_payload())

    assert record.authority_role is AuthorityRole.EVIDENCE
    assert record.asset_key is None
    assert type(record.metric_id) is MetricId
    assert type(record.evidence_relationship_id) is EvidenceRelationshipId


@pytest.mark.parametrize(
    "overrides",
    [
        {"metric_id": None},
        {"evidence_relationship_id": None},
        {
            "asset_key": ContentAssetKey(
                SourceRecordId("MREC-0001"),
                AssetType.ARTICLE,
            )
        },
    ],
)
def test_evidence_parent_rejects_missing_components_or_asset_parent(overrides):
    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_EVIDENCE_PARENT_INVALID"):
        CapturedContent(**_evidence_success_payload(**overrides))


def test_evidence_authority_has_no_metric_escalation_surface():
    record = CapturedContent(**_evidence_success_payload(searchable=True))
    forbidden = {
        "approved_metric",
        "approved_statement",
        "can_quote_externally",
        "allowed_exposure_channels",
    }

    assert forbidden.isdisjoint(_model_field_names(CapturedContent))
    assert all(not hasattr(record, field) for field in forbidden)
    assert record.searchable is True
    assert record.authority_role is AuthorityRole.EVIDENCE


def test_authority_and_status_serialized_values_are_frozen():
    assert [role.value for role in AuthorityRole] == ["primary_content", "evidence"]
    assert [status.value for status in CaptureStatus] == [
        "success",
        "stale",
        "unavailable",
        "blocked",
        "metadata_only",
        "needs_review",
    ]


def test_success_and_stale_accept_searchable_true_or_false_with_full_body():
    for status in (CaptureStatus.SUCCESS, CaptureStatus.STALE):
        for searchable in (True, False):
            record = CapturedContent(
                **_primary_success_payload(
                    capture_status=status,
                    searchable=searchable,
                )
            )
            assert record.clean_body == SYNTHETIC_CAPTURE_BODY
            assert record.searchable is searchable


@pytest.mark.parametrize("status", list(CaptureStatus))
def test_all_six_capture_statuses_have_a_valid_canonical_state(status):
    payload = (
        _primary_success_payload(capture_status=status)
        if status in (CaptureStatus.SUCCESS, CaptureStatus.STALE)
        else _bodyless_payload(status)
    )

    assert CapturedContent(**payload).capture_status is status


@pytest.mark.parametrize("status", [CaptureStatus.SUCCESS, CaptureStatus.STALE])
@pytest.mark.parametrize(
    "missing_field",
    [
        "clean_body",
        "content_hash",
        "parser_version",
        "captured_at",
        "last_successful_capture_at",
        "last_capture_attempt_at",
    ],
)
def test_full_text_statuses_require_body_hash_parser_and_all_timestamps(
    status,
    missing_field,
):
    payload = _primary_success_payload(capture_status=status)
    payload[missing_field] = None

    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_STATUS_BODY_INVALID"):
        CapturedContent(**payload)


@pytest.mark.parametrize(
    "status",
    [
        CaptureStatus.UNAVAILABLE,
        CaptureStatus.BLOCKED,
        CaptureStatus.METADATA_ONLY,
        CaptureStatus.NEEDS_REVIEW,
    ],
)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clean_body", SYNTHETIC_CAPTURE_BODY),
        ("section_structure", (Section(heading=None, text="Synthetic text"),)),
        ("content_hash", "synthetic-hash"),
        ("parser_version", "synthetic-parser-v1"),
        ("captured_at", CAPTURED_AT),
        ("searchable", True),
    ],
)
def test_bodyless_statuses_reject_current_body_revision_or_searchable_state(
    status,
    field,
    value,
):
    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_STATUS_BODY_INVALID"):
        CapturedContent(**_bodyless_payload(status, **{field: value}))


def test_unavailable_requires_last_capture_attempt():
    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_TIMESTAMP_INVALID"):
        CapturedContent(
            **_bodyless_payload(
                CaptureStatus.UNAVAILABLE,
                last_capture_attempt_at=None,
            )
        )


@pytest.mark.parametrize(
    "status",
    [CaptureStatus.BLOCKED, CaptureStatus.METADATA_ONLY, CaptureStatus.NEEDS_REVIEW],
)
def test_other_bodyless_statuses_allow_attempt_and_success_timestamps_to_be_absent(
    status,
):
    record = CapturedContent(
        **_bodyless_payload(
            status,
            last_capture_attempt_at=None,
            last_successful_capture_at=None,
        )
    )

    assert record.last_capture_attempt_at is None
    assert record.last_successful_capture_at is None


@pytest.mark.parametrize("status", [CaptureStatus.SUCCESS, CaptureStatus.STALE])
@pytest.mark.parametrize(
    "timestamps",
    [
        {
            "captured_at": LAST_SUCCESS + timedelta(seconds=1),
            "last_successful_capture_at": LAST_SUCCESS,
            "last_capture_attempt_at": LAST_ATTEMPT,
        },
        {
            "captured_at": CAPTURED_AT,
            "last_successful_capture_at": LAST_ATTEMPT + timedelta(seconds=1),
            "last_capture_attempt_at": LAST_ATTEMPT,
        },
    ],
)
def test_success_and_stale_reject_invalid_timestamp_order(status, timestamps):
    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_TIMESTAMP_INVALID"):
        CapturedContent(
            **_primary_success_payload(capture_status=status, **timestamps)
        )


def test_unavailable_rejects_last_success_after_last_attempt():
    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_TIMESTAMP_INVALID"):
        CapturedContent(
            **_bodyless_payload(
                CaptureStatus.UNAVAILABLE,
                last_successful_capture_at=LAST_ATTEMPT + timedelta(seconds=1),
            )
        )


@pytest.mark.parametrize(
    "field",
    ["captured_at", "last_successful_capture_at", "last_capture_attempt_at"],
)
def test_all_supplied_timestamps_must_be_timezone_aware(field):
    payload = _primary_success_payload()
    payload[field] = datetime(2026, 8, 9, 1, 0)

    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_TIMESTAMP_AWARE_REQUIRED"):
        CapturedContent(**payload)


def test_caller_supplied_timestamps_are_preserved_without_current_time_defaults():
    record = CapturedContent(**_primary_success_payload())

    assert record.captured_at is CAPTURED_AT
    assert record.last_successful_capture_at is LAST_SUCCESS
    assert record.last_capture_attempt_at is LAST_ATTEMPT


def test_previous_content_hash_is_optional_for_every_status():
    for status in CaptureStatus:
        payload = (
            _primary_success_payload(
                capture_status=status,
                previous_content_hash="synthetic-previous-hash",
            )
            if status in (CaptureStatus.SUCCESS, CaptureStatus.STALE)
            else _bodyless_payload(
                status,
                previous_content_hash="synthetic-previous-hash",
            )
        )
        assert CapturedContent(**payload).previous_content_hash == (
            "synthetic-previous-hash"
        )


def test_section_accepts_optional_heading_and_nonblank_normalized_text():
    without_heading = Section(heading=None, text="First line\nSecond line")
    with_heading = Section(heading="Synthetic Heading", text="Synthetic text")

    assert without_heading.heading is None
    assert without_heading.text == "First line\nSecond line"
    assert with_heading.heading == "Synthetic Heading"


@pytest.mark.parametrize("text", ["", "   ", "synthetic\x00text", "synthetic\rtext"])
def test_section_rejects_blank_or_unsafe_text(text):
    with pytest.raises(ValidationError, match="SECTION_TEXT_INVALID"):
        Section(heading=None, text=text)


@pytest.mark.parametrize("heading", ["", "   ", "synthetic\nheading", "synthetic\x7f"])
def test_section_rejects_blank_or_controlled_heading(heading):
    with pytest.raises(ValidationError, match="SECTION_HEADING_INVALID"):
        Section(heading=heading, text="Synthetic text")


def test_section_structure_is_an_immutable_tuple_without_order_or_dom_fields():
    record = CapturedContent(**_primary_success_payload())
    section_fields = _model_field_names(Section)

    assert type(record.section_structure) is tuple
    assert section_fields == {"heading", "text"}
    assert {"order", "level", "path", "html", "dom", "section_id"}.isdisjoint(
        section_fields
    )
    with pytest.raises((FrozenInstanceError, TypeError, ValidationError)):
        record.section_structure = ()


def test_safe_http_metadata_accepts_only_five_typed_optional_fields():
    final_url = _canonical_url("https://example.test/final")
    metadata = SafeHttpMetadata(
        status_code=299,
        content_type="text/html",
        etag='W/"synthetic"',
        last_modified="synthetic-http-date",
        verified_final_url=final_url,
    )

    assert _model_field_names(SafeHttpMetadata) == {
        "status_code",
        "content_type",
        "etag",
        "last_modified",
        "verified_final_url",
    }
    assert metadata.verified_final_url is final_url


@pytest.mark.parametrize("status_code", [True, False, 99, 600, "200"])
def test_safe_http_metadata_rejects_bool_out_of_range_or_coerced_status_code(
    status_code,
):
    with pytest.raises(ValidationError):
        SafeHttpMetadata(status_code=status_code)


@pytest.mark.parametrize("field", ["content_type", "etag", "last_modified"])
@pytest.mark.parametrize("value", ["", "   ", "synthetic\rvalue", "synthetic\nvalue"])
def test_safe_http_metadata_rejects_blank_or_controlled_text(field, value):
    with pytest.raises(ValidationError, match="SAFE_HTTP_METADATA_TEXT_INVALID"):
        SafeHttpMetadata(**{field: value})


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "Authorization",
        "authorization",
        "Cookie",
        "Set-Cookie",
        "Location",
        "Server",
        "Via",
        "Cache-Control",
        "Content-Length",
        "X-Synthetic",
        "token",
        "credential",
        "headers",
    ],
)
def test_safe_http_metadata_has_no_arbitrary_header_surface(forbidden_field):
    with pytest.raises(ValidationError):
        SafeHttpMetadata(**{forbidden_field: "SYNTHETIC_SECRET"})


def test_verified_final_url_requires_wp7_canonical_url_type():
    with pytest.raises(ValidationError):
        SafeHttpMetadata(verified_final_url="https://example.test/final")

    trusted = _canonical_url("https://example.test/final")
    assert SafeHttpMetadata(verified_final_url=trusted).verified_final_url is trusted


@pytest.mark.parametrize("field", ["source_url", "canonical_url"])
def test_captured_content_url_fields_require_wp7_trusted_type(field):
    payload = _primary_success_payload()
    payload[field] = "https://example.test/plain-string"

    with pytest.raises(ValidationError):
        CapturedContent(**payload)


def test_source_and_canonical_urls_may_differ_without_wp9_redirect_processing():
    record = CapturedContent(**_primary_success_payload())

    assert record.source_url != record.canonical_url


def test_logical_capture_identity_does_not_change_with_url_hash_parser_or_time():
    before = CapturedContent(**_primary_success_payload())
    changed_canonical_url = _canonical_url(
        "https://example.test/changed-canonical"
    )
    after = CapturedContent(
        **_primary_success_payload(
            source_url=_canonical_url("https://example.test/changed-source"),
            canonical_url=changed_canonical_url,
            content_hash="synthetic-content-hash-changed",
            parser_version="synthetic-parser-v2",
            last_successful_capture_at=LAST_ATTEMPT,
            last_capture_attempt_at=LAST_ATTEMPT + timedelta(hours=1),
            source_http_metadata=SafeHttpMetadata(
                verified_final_url=changed_canonical_url,
            ),
        )
    )

    assert before.captured_content_id == after.captured_content_id
    assert CapturedContent.identity_field_names() == ("captured_content_id",)


def test_captured_content_has_no_identity_allocator_or_revision_allocator():
    forbidden = {
        "allocate_captured_content_id",
        "generate_captured_content_id",
        "CaptureRevisionId",
        "RevisionId",
    }

    assert all(not hasattr(captured_content, name) for name in forbidden)


def test_clean_body_is_redacted_from_repr_str_validation_error_and_logs(caplog):
    record = CapturedContent(**_primary_success_payload())
    invalid = _primary_success_payload(
        capture_status=CaptureStatus.BLOCKED,
        searchable=False,
        captured_at=None,
        content_hash=None,
        parser_version=None,
        section_structure=(),
    )

    with pytest.raises(ValidationError) as error:
        CapturedContent(**invalid)

    rendered = (repr(record), str(record), str(error.value), caplog.text)
    assert all(SYNTHETIC_CAPTURE_BODY not in value for value in rendered)
    assert caplog.text == ""


def test_legal_canonical_serialization_keeps_clean_body():
    record = CapturedContent(**_primary_success_payload())

    assert _model_dump(record)["clean_body"] == SYNTHETIC_CAPTURE_BODY


def test_captured_content_schema_has_no_raw_http_html_governance_or_runtime_surface():
    fields = _model_field_names(CapturedContent)
    forbidden = {
        "raw_html",
        "raw_response",
        "headers",
        "request_headers",
        "approved_metric",
        "can_quote_externally",
        "allowed_exposure_channels",
        "active",
        "publishable",
        "release_id",
        "chunk_id",
    }

    assert forbidden.isdisjoint(fields)
    record = CapturedContent(**_primary_success_payload())
    assert record.sync_batch_id == record.source_lineage.sync_batch_id


@pytest.mark.parametrize(
    ("status", "update"),
    [
        (
            CaptureStatus.BLOCKED,
            {"clean_body": "Synthetic bypass body", "searchable": True},
        ),
        (
            CaptureStatus.METADATA_ONLY,
            {"content_hash": "synthetic-bypass-hash"},
        ),
    ],
)
def test_captured_content_model_copy_rejects_unvalidated_nonempty_updates(
    status,
    update,
):
    record = CapturedContent(**_bodyless_payload(status))

    with pytest.raises(ValueError, match="WP9_UNVALIDATED_COPY_NOT_ALLOWED"):
        record.model_copy(update=update)


def test_captured_content_model_copy_rejects_sync_batch_update():
    record = CapturedContent(**_primary_success_payload())

    with pytest.raises(ValueError, match="WP9_UNVALIDATED_COPY_NOT_ALLOWED"):
        record.model_copy(update={"sync_batch_id": "SYNTHETIC-OTHER-BATCH"})


def test_pydantic_one_style_copy_rejects_update_include_and_exclude():
    record = CapturedContent(**_primary_success_payload())

    with pytest.raises(ValueError, match="WP9_UNVALIDATED_COPY_NOT_ALLOWED"):
        record.copy(update={"capture_status": CaptureStatus.BLOCKED})
    with pytest.raises(ValueError, match="WP9_UNVALIDATED_COPY_NOT_ALLOWED"):
        record.copy(include={"captured_content_id"})
    with pytest.raises(ValueError, match="WP9_UNVALIDATED_COPY_NOT_ALLOWED"):
        record.copy(exclude={"clean_body"})


def test_safe_http_metadata_and_section_reject_unvalidated_update_copy():
    metadata = SafeHttpMetadata(status_code=200)
    section = Section(heading=None, text="Synthetic section text")

    with pytest.raises(ValueError, match="WP9_UNVALIDATED_COPY_NOT_ALLOWED"):
        metadata.model_copy(update={"status_code": True})
    with pytest.raises(ValueError, match="WP9_UNVALIDATED_COPY_NOT_ALLOWED"):
        section.model_copy(update={"text": ""})


def test_pure_model_copy_and_deep_copy_preserve_validated_state():
    record = CapturedContent(**_primary_success_payload())

    shallow = record.model_copy()
    deep = record.model_copy(deep=True)

    assert shallow == record
    assert deep == record
    assert shallow is not record
    assert deep is not record
    assert type(shallow.captured_content_id) is CapturedContentId
    assert type(deep.source_url) is type(record.source_url)


def test_explicit_model_construct_remains_documented_unsafe_low_level_surface():
    payload = _primary_success_payload(
        capture_status=CaptureStatus.BLOCKED,
        searchable=True,
    )

    unsafe = CapturedContent.model_construct(**payload)

    assert unsafe.capture_status is CaptureStatus.BLOCKED
    assert unsafe.clean_body == SYNTHETIC_CAPTURE_BODY
    assert unsafe.searchable is True


@pytest.mark.parametrize(
    "http_content_type",
    ["text/html", "text/html; charset=UTF-8", "application/pdf"],
)
def test_canonical_and_opaque_http_content_types_may_intentionally_differ(
    http_content_type,
):
    record = CapturedContent(
        **_primary_success_payload(
            content_type="text/html",
            source_http_metadata=SafeHttpMetadata(
                content_type=http_content_type,
                verified_final_url=_canonical_url(
                    "https://example.test/canonical"
                ),
            ),
        )
    )

    assert record.content_type == "text/html"
    assert record.source_http_metadata.content_type == http_content_type


def test_verified_final_url_must_equal_canonical_url_when_present():
    canonical_url = _canonical_url("https://final.example.test/a")
    different_final_url = _canonical_url("https://final.example.test/b")

    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_FINAL_URL_MISMATCH"):
        CapturedContent(
            **_primary_success_payload(
                canonical_url=canonical_url,
                source_http_metadata=SafeHttpMetadata(
                    verified_final_url=different_final_url,
                ),
            )
        )


def test_matching_or_absent_verified_final_url_is_valid():
    canonical_url = _canonical_url("https://final.example.test/story")

    matching = CapturedContent(
        **_primary_success_payload(
            canonical_url=canonical_url,
            source_http_metadata=SafeHttpMetadata(
                verified_final_url=canonical_url,
            ),
        )
    )
    absent = CapturedContent(
        **_primary_success_payload(
            canonical_url=canonical_url,
            source_http_metadata=SafeHttpMetadata(verified_final_url=None),
        )
    )

    assert matching.source_http_metadata.verified_final_url == matching.canonical_url
    assert absent.source_http_metadata.verified_final_url is None


def test_source_domain_must_exactly_match_trusted_source_url_hostname():
    source_url = _canonical_url("https://source.example.test/story")

    valid = CapturedContent(
        **_primary_success_payload(
            source_url=source_url,
            source_domain="source.example.test",
        )
    )
    assert valid.source_domain == "source.example.test"

    with pytest.raises(
        ValidationError,
        match="CAPTURED_CONTENT_SOURCE_DOMAIN_MISMATCH",
    ):
        CapturedContent(
            **_primary_success_payload(
                source_url=source_url,
                source_domain="wrong.example.test",
            )
        )


def test_source_host_may_differ_from_matching_canonical_and_final_host():
    source_url = _canonical_url("https://source.example.test/story")
    final_url = _canonical_url("https://final.example.test/story")

    record = CapturedContent(
        **_primary_success_payload(
            source_url=source_url,
            source_domain="source.example.test",
            canonical_url=final_url,
            source_http_metadata=SafeHttpMetadata(verified_final_url=final_url),
        )
    )

    assert record.source_url != record.canonical_url
    assert record.source_domain == "source.example.test"
    assert record.source_http_metadata.verified_final_url == record.canonical_url


def test_source_domain_validation_does_not_change_logical_capture_identity():
    before = CapturedContent(**_primary_success_payload())
    source_url = _canonical_url("https://source.example.test/story")
    after = CapturedContent(
        **_primary_success_payload(
            source_url=source_url,
            source_domain="source.example.test",
        )
    )

    assert before.captured_content_id == after.captured_content_id


def test_section_body_is_redacted_but_retained_in_legal_serialization():
    section = Section(heading="Synthetic heading", text=SYNTHETIC_SECTION_BODY)
    record = CapturedContent(
        **_primary_success_payload(section_structure=(section,))
    )

    assert SYNTHETIC_SECTION_BODY not in repr(section)
    assert SYNTHETIC_SECTION_BODY not in repr(record)
    assert SYNTHETIC_SECTION_BODY not in str(record)
    assert _model_dump(record)["section_structure"][0]["text"] == (
        SYNTHETIC_SECTION_BODY
    )


def test_section_http_and_cross_field_errors_do_not_echo_payloads():
    header_sentinel = "SYNTHETIC_HEADER_VALUE_7C21"
    url_sentinel = "synthetic-url-value-7c21.example.test"

    with pytest.raises(ValidationError) as section_error:
        Section(heading=None, text=f"{SYNTHETIC_SECTION_BODY}\r")
    with pytest.raises(ValidationError) as header_error:
        SafeHttpMetadata(etag=f"{header_sentinel}\r")
    with pytest.raises(ValidationError) as url_error:
        CapturedContent(
            **_primary_success_payload(
                canonical_url=_canonical_url("https://example.test/canonical"),
                source_http_metadata=SafeHttpMetadata(
                    verified_final_url=_canonical_url(f"https://{url_sentinel}/final"),
                ),
            )
        )

    assert SYNTHETIC_SECTION_BODY not in str(section_error.value)
    assert header_sentinel not in str(header_error.value)
    assert url_sentinel not in str(url_error.value)


def test_sync_batch_mismatch_fails_closed():
    with pytest.raises(ValidationError, match="CAPTURED_CONTENT_SYNC_BATCH_MISMATCH"):
        CapturedContent(
            **_primary_success_payload(sync_batch_id="SYNTHETIC-OTHER-BATCH")
        )


@pytest.mark.parametrize("status", [CaptureStatus.SUCCESS, CaptureStatus.STALE])
def test_full_text_timestamp_equality_is_valid(status):
    record = CapturedContent(
        **_primary_success_payload(
            capture_status=status,
            captured_at=CAPTURED_AT,
            last_successful_capture_at=CAPTURED_AT,
            last_capture_attempt_at=CAPTURED_AT,
        )
    )

    assert record.captured_at == record.last_successful_capture_at
    assert record.last_successful_capture_at == record.last_capture_attempt_at


def test_all_wp9_dtos_reject_normal_attribute_mutation():
    record = CapturedContent(**_primary_success_payload())
    metadata = SafeHttpMetadata(status_code=200)
    section = Section(heading=None, text="Synthetic section text")

    with pytest.raises((FrozenInstanceError, TypeError, ValidationError)):
        record.searchable = False
    with pytest.raises((FrozenInstanceError, TypeError, ValidationError)):
        metadata.status_code = 201
    with pytest.raises((FrozenInstanceError, TypeError, ValidationError)):
        section.text = "Changed"
