from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

import marketing_knowledge_agent.canonical_models as canonical_models
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    Brand,
    BrandEntityType,
    BrandId,
    BrandIdentityDecision,
    CanonicalModelError,
    CanonicalSourceLineage,
    ContentAssetKey,
    LifecycleStatus,
    MetricId,
    PublicMetric,
    PublishEligibility,
    ReviewStatus,
    SourceRecord,
    SourceRecordId,
    create_public_metric,
    validate_unique_brand_ids,
    validate_unique_metric_ids,
    validate_unique_source_record_ids,
)


def _lineage(*, source_row: int = 7) -> CanonicalSourceLineage:
    return CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:synthetic-spreadsheet-id",
        sheet_id=101,
        sheet_title="Synthetic Source",
        source_row=source_row,
        source_columns={"source_record_id": "M"},
        source_ranges={"source_name": f"C{source_row}"},
        source_fingerprint="sha256:synthetic-source-fingerprint",
        sync_batch_id="SYNTHETIC-WP4-BATCH",
    )


def _public_metric_payload() -> dict:
    return {
        "metric_id": MetricId("MET-0001"),
        "metric_type": "Synthetic metric type",
        "indicator": "Synthetic indicator",
        "approved_statement": "Synthetic approved statement",
        "maintenance_updated_at": date(2026, 8, 9),
        "lifecycle_status": LifecycleStatus.ACTIVE,
        "review_status": ReviewStatus.APPROVED,
        "publish_eligibility": PublishEligibility.ELIGIBLE,
        "can_quote_externally": True,
        "source_lineage": _lineage(),
    }


@pytest.mark.parametrize(
    ("id_type", "raw"),
    [
        (SourceRecordId, "MREC-0001"),
        (BrandId, "BRD-0001"),
        (MetricId, "MET-0001"),
        (SourceRecordId, "MREC-12345"),
        (BrandId, "BRD-12345"),
        (MetricId, "MET-12345"),
    ],
)
def test_permanent_ids_accept_exact_namespace_with_at_least_four_digits(id_type, raw):
    permanent_id = id_type(raw)

    assert str(permanent_id) == raw
    assert isinstance(permanent_id, id_type)


@pytest.mark.parametrize(
    ("id_type", "raw"),
    [
        (SourceRecordId, "MET-0001"),
        (SourceRecordId, "BRD-0001"),
        (BrandId, "MREC-0001"),
        (BrandId, "MET-0001"),
        (MetricId, "MREC-0001"),
        (MetricId, "BRD-0001"),
        (SourceRecordId, "MREC-001"),
        (BrandId, "BRD-001"),
        (MetricId, "MET-001"),
        (SourceRecordId, ""),
        (BrandId, "   "),
        (MetricId, "=ROW()"),
        (SourceRecordId, '="MREC-"&TEXT(1,"0000")'),
        (BrandId, "BRD-0001 "),
        (MetricId, "met-0001"),
    ],
)
def test_permanent_ids_reject_wrong_namespace_short_blank_formula_or_non_exact_text(
    id_type,
    raw,
):
    with pytest.raises(ValueError, match="PERMANENT_ID_FORMAT_INVALID"):
        id_type(raw)


def test_id_value_objects_are_immutable_and_namespace_types_are_not_interchangeable():
    source_id = SourceRecordId("MREC-0001")
    metric_id = MetricId("MET-0001")

    assert type(source_id) is SourceRecordId
    assert type(metric_id) is MetricId
    assert source_id != metric_id
    with pytest.raises((AttributeError, TypeError)):
        source_id.value = "MREC-9999"


def test_content_asset_key_is_mrec_plus_frozen_asset_type():
    article = ContentAssetKey(
        source_record_id=SourceRecordId("MREC-0001"),
        asset_type=AssetType.ARTICLE,
    )
    video = ContentAssetKey(
        source_record_id=SourceRecordId("MREC-0001"),
        asset_type=AssetType.VIDEO,
    )

    assert str(article) == "MREC-0001:article"
    assert str(video) == "MREC-0001:video"
    assert article != video
    assert ContentAssetKey.parse("MREC-0001:podcast") == ContentAssetKey(
        source_record_id=SourceRecordId("MREC-0001"),
        asset_type=AssetType.PODCAST,
    )
    with pytest.raises(ValueError, match="CONTENT_ASSET_KEY_FORMAT_INVALID"):
        ContentAssetKey.parse("MET-0001:article")

    before_metadata = {
        "asset_key": article,
        "title": "Synthetic Article Title",
        "url": "https://example.com/synthetic-before",
    }
    after_metadata = {
        "asset_key": article,
        "title": "Synthetic Article Title Renamed",
        "url": "https://example.org/synthetic-after",
    }
    assert before_metadata["asset_key"] == after_metadata["asset_key"]


def test_content_asset_key_is_immutable_and_has_no_ast_identity():
    key = ContentAssetKey(SourceRecordId("MREC-0001"), AssetType.NEWS)

    with pytest.raises(FrozenInstanceError):
        key.asset_type = AssetType.ARTICLE
    assert not hasattr(canonical_models, "AssetId")
    assert "AST" not in str(key)


def test_row_reorder_and_display_metadata_changes_do_not_change_permanent_identity():
    before = SourceRecord(
        source_record_id=SourceRecordId("MREC-0001"),
        brand_identity=BrandIdentityDecision(
            review_status=ReviewStatus.APPROVED,
            brand_id=BrandId("BRD-0001"),
        ),
        source_name="Example Brand Alpha",
        interview_year=2025,
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        source_lineage=_lineage(source_row=7),
    )
    after = SourceRecord(
        source_record_id=SourceRecordId("MREC-0001"),
        brand_identity=before.brand_identity,
        source_name="Example Brand Alpha Renamed",
        interview_year=2025,
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        source_lineage=_lineage(source_row=99),
    )

    assert before.source_record_id == after.source_record_id
    assert before.brand_id == after.brand_id
    assert before.source_lineage.source_row != after.source_lineage.source_row
    assert before.source_name != after.source_name
    assert "source_row" not in SourceRecord.identity_field_names()
    assert "source_name" not in SourceRecord.identity_field_names()
    assert "url" not in SourceRecord.identity_field_names()


def test_brand_display_name_handle_and_website_are_not_identity_fields():
    brand = Brand(
        brand_id=BrandId("BRD-0001"),
        canonical_name="Example Brand Alpha",
        entity_type=BrandEntityType.MERCHANT,
        handle="example-alpha",
        official_website="https://example.com/alpha",
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        source_lineage=_lineage(),
    )

    assert Brand.identity_field_names() == ("brand_id",)
    assert brand.brand_id == BrandId("BRD-0001")


def test_uniqueness_validators_are_namespace_specific_and_in_memory_only():
    source_ids = [SourceRecordId("MREC-0001"), SourceRecordId("MREC-0002")]
    metric_ids = [MetricId("MET-0001"), MetricId("MET-0002")]
    brand_ids = [BrandId("BRD-0001"), BrandId("BRD-0002")]

    assert validate_unique_source_record_ids(source_ids) == tuple(source_ids)
    assert validate_unique_metric_ids(metric_ids) == tuple(metric_ids)
    assert validate_unique_brand_ids(brand_ids) == tuple(brand_ids)
    with pytest.raises(ValueError, match="SOURCE_RECORD_ID_DUPLICATE"):
        validate_unique_source_record_ids([source_ids[0], source_ids[0]])
    with pytest.raises(ValueError, match="METRIC_ID_DUPLICATE"):
        validate_unique_metric_ids([metric_ids[0], metric_ids[0]])
    with pytest.raises(ValueError, match="BRAND_ID_DUPLICATE"):
        validate_unique_brand_ids([brand_ids[0], brand_ids[0]])
    with pytest.raises(TypeError, match="SOURCE_RECORD_ID_NAMESPACE_MISMATCH"):
        validate_unique_source_record_ids([MetricId("MET-0001")])


def test_uncertain_or_blank_brd_is_needs_review_without_assignment():
    uncertain = BrandIdentityDecision(
        review_status=ReviewStatus.NEEDS_REVIEW,
        brand_id=None,
    )

    assert uncertain.review_status is ReviewStatus.NEEDS_REVIEW
    assert uncertain.brand_id is None
    with pytest.raises(ValueError, match="BRAND_ID_REQUIRED_FOR_APPROVED_DECISION"):
        BrandIdentityDecision(
            review_status=ReviewStatus.APPROVED,
            brand_id=None,
        )
    with pytest.raises(ValueError, match="BRAND_ID_FORBIDDEN_FOR_UNCERTAIN_DECISION"):
        BrandIdentityDecision(
            review_status=ReviewStatus.NEEDS_REVIEW,
            brand_id=BrandId("BRD-0001"),
        )
    with pytest.raises(ValueError):
        BrandIdentityDecision(
            review_status=ReviewStatus.APPROVED,
            brand_id=SourceRecordId("MREC-0001"),
        )


def test_structural_enums_contain_only_frozen_wp4_states():
    assert {item.value for item in AssetType} == {"article", "video", "podcast", "news"}
    assert {item.value for item in LifecycleStatus} == {
        "candidate",
        "needs_review",
        "active",
        "incomplete",
        "archived",
    }
    assert {item.value for item in ReviewStatus} == {
        "approved",
        "needs_review",
        "excluded",
    }
    assert {item.value for item in PublishEligibility} == {
        "eligible",
        "ineligible",
        "needs_review",
    }
    assert "resolved_candidate" not in {item.value for item in LifecycleStatus}


def test_canonical_models_are_independent_from_legacy_document_metadata():
    from marketing_knowledge_agent.models import DocumentMetadata

    legacy_fields = (
        DocumentMetadata.model_fields
        if hasattr(DocumentMetadata, "model_fields")
        else DocumentMetadata.__fields__
    )
    assert not issubclass(Brand, DocumentMetadata)
    assert not issubclass(SourceRecord, DocumentMetadata)
    assert not issubclass(PublicMetric, DocumentMetadata)
    assert "brand_id" not in legacy_fields
    assert "source_record_id" not in legacy_fields
    assert "metric_id" not in legacy_fields


def test_public_metric_schema_is_defined_but_direct_construction_fails_closed():
    payload = _public_metric_payload()
    assert PublicMetric.identity_field_names() == ("metric_id",)
    assert "_PUBLIC_METRIC_WP5_CONSTRUCTION_GATE" not in vars(canonical_models)
    public_metric_fields = (
        PublicMetric.model_fields
        if hasattr(PublicMetric, "model_fields")
        else PublicMetric.__fields__
    )
    assert "approved_statement" in public_metric_fields
    with pytest.raises(TypeError, match="PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"):
        PublicMetric(**payload)


def test_public_metric_general_construction_api_matrix_fails_closed_without_payload_echo():
    sentinel = "SYNTHETIC-WP4-CONSTRUCTION-SENTINEL"
    payload = _public_metric_payload()
    payload["approved_statement"] = sentinel
    method_calls = (
        ("model_validate", (payload,), {}),
        ("model_validate_json", ('{"approved_statement":"synthetic"}',), {}),
        ("model_validate_strings", ({"approved_statement": sentinel},), {}),
        ("model_construct", (), payload),
        ("parse_obj", (payload,), {}),
        ("parse_raw", ('{"approved_statement":"synthetic"}',), {}),
        ("parse_file", ("/synthetic/wp4-public-metric.json",), {}),
        ("from_orm", (object(),), {}),
        ("construct", (), payload),
        ("validate", (payload,), {}),
    )

    for method_name, args, kwargs in method_calls:
        if not hasattr(PublicMetric, method_name):
            continue
        with pytest.raises(TypeError) as exc_info:
            getattr(PublicMetric, method_name)(*args, **kwargs)
        assert str(exc_info.value) == "PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"
        assert sentinel not in str(exc_info.value)


def test_public_metric_copy_update_paths_are_explicitly_fail_closed():
    update = {"approved_statement": "SYNTHETIC-WP4-COPY-SENTINEL"}
    method_names = ["copy"]
    if hasattr(BaseModel, "model_copy"):
        method_names.append("model_copy")

    for method_name in method_names:
        assert method_name in PublicMetric.__dict__
        with pytest.raises(TypeError) as exc_info:
            PublicMetric.__dict__[method_name](None, update=update)
        assert str(exc_info.value) == "PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"
        assert update["approved_statement"] not in str(exc_info.value)

    copy_method = PublicMetric.__dict__["copy"]
    for field_filter in ({"include": {"metric_id"}}, {"exclude": {"approved_statement"}}):
        with pytest.raises(TypeError, match="PUBLIC_METRIC_REQUIRES_WP5_ELIGIBLE_INPUT"):
            copy_method(None, **field_filter)


@pytest.mark.parametrize(
    ("review_status", "publish_eligibility", "can_quote_externally", "valid"),
    [
        (ReviewStatus.APPROVED, PublishEligibility.ELIGIBLE, True, True),
        (ReviewStatus.APPROVED, PublishEligibility.INELIGIBLE, True, False),
        (ReviewStatus.NEEDS_REVIEW, PublishEligibility.ELIGIBLE, True, False),
        (ReviewStatus.NEEDS_REVIEW, PublishEligibility.INELIGIBLE, True, False),
        (ReviewStatus.EXCLUDED, PublishEligibility.INELIGIBLE, True, False),
        (ReviewStatus.EXCLUDED, PublishEligibility.ELIGIBLE, False, False),
        (ReviewStatus.EXCLUDED, PublishEligibility.INELIGIBLE, False, True),
        (ReviewStatus.APPROVED, PublishEligibility.INELIGIBLE, False, True),
        (ReviewStatus.NEEDS_REVIEW, PublishEligibility.ELIGIBLE, False, True),
    ],
)
def test_shared_public_metric_governance_invariant_has_exact_frozen_matrix(
    review_status,
    publish_eligibility,
    can_quote_externally,
    valid,
):
    kwargs = {
        "review_status": review_status,
        "publish_eligibility": publish_eligibility,
        "can_quote_externally": can_quote_externally,
    }

    if valid:
        assert canonical_models._validate_public_metric_governance_state(**kwargs) is None
    else:
        with pytest.raises(CanonicalModelError) as caught:
            canonical_models._validate_public_metric_governance_state(**kwargs)
        assert caught.value.code == "PUBLIC_METRIC_GOVERNANCE_STATE_INVALID"


@pytest.mark.parametrize(
    ("model_type", "id_field", "expected_pattern"),
    [
        (Brand, "brand_id", "^BRD-[0-9]{4,}$"),
        (SourceRecord, "source_record_id", "^MREC-[0-9]{4,}$"),
        (PublicMetric, "metric_id", "^MET-[0-9]{4,}$"),
    ],
)
def test_canonical_entity_json_schemas_preserve_id_namespaces(
    model_type,
    id_field,
    expected_pattern,
):
    schema = (
        model_type.model_json_schema()
        if hasattr(model_type, "model_json_schema")
        else model_type.schema()
    )

    assert schema["properties"][id_field]["type"] == "string"
    assert schema["properties"][id_field]["pattern"] == expected_pattern


@pytest.mark.parametrize(
    ("id_type", "raw"),
    [
        (SourceRecordId, 1),
        (SourceRecordId, True),
        (SourceRecordId, "BRD-0001"),
        (SourceRecordId, " MREC-0001"),
        (SourceRecordId, "MREC-0001 "),
        (SourceRecordId, "mrec-0001"),
        (SourceRecordId, "=ROW()"),
        (BrandId, 1),
        (BrandId, True),
        (BrandId, "MREC-0001"),
        (BrandId, " BRD-0001"),
        (BrandId, "BRD-0001 "),
        (BrandId, "brd-0001"),
        (BrandId, '="BRD-"&TEXT(1,"0000")'),
        (MetricId, 1),
        (MetricId, True),
        (MetricId, "BRD-0001"),
        (MetricId, " MET-0001"),
        (MetricId, "MET-0001 "),
        (MetricId, "met-0001"),
        (MetricId, '="MET-"&TEXT(1,"0000")'),
    ],
)
def test_pydantic_two_permanent_id_validation_never_silently_coerces(id_type, raw):
    if not hasattr(BaseModel, "model_validate"):
        pytest.skip("Pydantic 2 coercion contract")
    from pydantic import TypeAdapter

    with pytest.raises(ValidationError):
        TypeAdapter(id_type).validate_python(raw)


def test_wp3_resolved_cell_value_is_not_a_wp4_public_metric_factory_input_surface():
    from marketing_knowledge_agent.cell_normalization import ResolvedCellValue

    public_factories = []
    for name, member in inspect.getmembers(canonical_models):
        if name.startswith("_") or not callable(member):
            continue
        try:
            signature = inspect.signature(member)
        except (TypeError, ValueError):
            continue
        if name == "create_public_metric":
            public_factories.append((name, signature))
        assert all(
            parameter.annotation is not ResolvedCellValue
            for parameter in signature.parameters.values()
        )

    assert public_factories == [("create_public_metric", inspect.signature(create_public_metric))]
    assert not any(
        name in canonical_models.__dict__
        for name in (
            "source_cells_to_public_metric",
            "resolved_cell_to_public_metric",
            "public_metric_from_source",
        )
    )
