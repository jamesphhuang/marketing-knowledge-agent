from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator


SourceType = Literal[
    "blog",
    "showcase",
    "social",
    "podcast",
    "website",
    "youtube",
    "design",
    "database",
]
FunnelStage = Literal["awareness", "consideration", "decision", "retention", "advocacy"]
Status = Literal["published", "draft", "archived", "deprecated"]
RecordType = Literal[
    "content_asset",
    "merchant_case",
    "restricted_customer",
    "public_metric",
    "pending_metric",
    "handle_mapping",
]
ClaimStatus = Literal["approved", "pending_review", "draft", "deprecated"]
DataClassification = Literal["public", "internal", "restricted"]
AllowedExposureChannel = Literal[
    "press_release",
    "owned_media",
    "saleskits",
    "verbal_briefing",
    "speaking_deck",
    "website_recruiting",
    "ads",
]

NON_PUBLIC_STATUSES = {"archived", "deprecated", "draft"}
NON_RETRIEVABLE_RECORD_TYPES = {"restricted_customer", "handle_mapping"}


def _normalize_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip().lower()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    raise TypeError("must be a string or list of strings")


class DocumentMetadata(BaseModel):
    title: str
    source_type: SourceType
    record_type: RecordType = "content_asset"
    content_category: Optional[str] = None
    parent_source_type: Optional[SourceType] = None
    product: List[str] = Field(default_factory=list)
    industry: List[str] = Field(default_factory=list)
    topic: List[str] = Field(default_factory=list)
    funnel_stage: List[FunnelStage] = Field(default_factory=list)
    status: Status = "published"
    publish_date: date
    updated_date: Optional[date] = None
    captured_date: Optional[date] = None
    last_reviewed: Optional[date] = None
    review_due_date: Optional[date] = None
    source_path: str = ""
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    stable_record_id: Optional[str] = None
    canonical_url: Optional[str] = None
    language: str = "zh-TW"
    author: Optional[str] = None
    owner: Optional[str] = None
    asset_type: Optional[str] = None
    brand_name: Optional[str] = None
    merchant_handle: Optional[str] = None
    merchant_status: Optional[str] = None
    interview_year: Optional[int] = None
    sales_category_lv1: Optional[str] = None
    sales_category_lv2: Optional[str] = None
    industry_subcategory: Optional[str] = None
    content_tags: List[str] = Field(default_factory=list)
    article_title: Optional[str] = None
    video_title: Optional[str] = None
    podcast_title: Optional[str] = None
    news_title: Optional[str] = None
    notes: Optional[str] = None
    metric_type: Optional[str] = None
    metric_name: Optional[str] = None
    claim_statement: Optional[str] = None
    metric_note: Optional[str] = None
    metric_updated_date: Optional[date] = None
    reference_source: Optional[str] = None
    claim_status: Optional[ClaimStatus] = None
    data_classification: DataClassification = "public"
    can_quote_externally: bool = True
    allowed_exposure_channels: List[AllowedExposureChannel] = Field(default_factory=list)
    website_url: Optional[str] = None
    nda_signed: Optional[bool] = None
    nda_uploaded_salesforce: Optional[bool] = None
    restricted_reason: Optional[str] = None
    restricted_aliases: List[str] = Field(default_factory=list)
    submitted_by: Optional[str] = None
    restricted_updated_year: Optional[int] = None
    no_valid_content_asset: bool = False
    can_enter_content_index: bool = True
    missing_allowed_exposure_channels: bool = False
    restricted_note: Optional[str] = None
    governance_issue_types: List[str] = Field(default_factory=list)
    governance_risk_reasons: List[str] = Field(default_factory=list)
    governance_risk_fields: List[str] = Field(default_factory=list)
    invalid_asset_fields: List[str] = Field(default_factory=list)
    invalid_asset_values: Dict[str, str] = Field(default_factory=dict)
    same_brand_multiple_records: bool = False
    same_handle_multiple_records: bool = False
    multi_interview_record: bool = False
    suspected_duplicate_review: bool = False

    @validator(
        "source_type",
        "status",
        "record_type",
        "claim_status",
        "data_classification",
        "parent_source_type",
        pre=True,
    )
    def normalize_enum(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @validator(
        "content_category",
        "brand_name",
        "merchant_handle",
        "merchant_status",
        "sales_category_lv1",
        "sales_category_lv2",
        "industry_subcategory",
        "article_title",
        "video_title",
        "podcast_title",
        "news_title",
        "notes",
        "metric_type",
        "metric_name",
        "claim_statement",
        "metric_note",
        "reference_source",
        "website_url",
        "restricted_reason",
        "submitted_by",
        "restricted_note",
        "owner",
        "asset_type",
        pre=True,
    )
    def normalize_optional_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @validator(
        "product",
        "industry",
        "topic",
        "funnel_stage",
        "content_tags",
        "allowed_exposure_channels",
        "restricted_aliases",
        "governance_issue_types",
        "governance_risk_reasons",
        "governance_risk_fields",
        "invalid_asset_fields",
        pre=True,
    )
    def normalize_lists(cls, value: Any) -> List[str]:
        return _normalize_list(value)

    @validator(
        "can_quote_externally",
        "nda_signed",
        "nda_uploaded_salesforce",
        "no_valid_content_asset",
        "can_enter_content_index",
        "missing_allowed_exposure_channels",
        "same_brand_multiple_records",
        "same_handle_multiple_records",
        "multi_interview_record",
        "suspected_duplicate_review",
        pre=True,
    )
    def normalize_bool(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "y", "1", "是", "有"}:
                return True
            if normalized in {"false", "no", "n", "0", "否", "無"}:
                return False
        return value

    @validator("stable_record_id", pre=True)
    def validate_stable_record_id(cls, value: Any) -> Optional[str]:
        # Imported lazily because the crosswalk's Excel/governance imports eventually reference
        # DocumentMetadata; the regex remains canonical without creating a module-import cycle.
        from .stable_record_crosswalk import STABLE_ID_RE

        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if not STABLE_ID_RE.match(normalized):
            raise ValueError(
                f"stable_record_id must match canonical pattern {STABLE_ID_RE.pattern}"
            )
        return normalized

    @property
    def effective_date(self) -> date:
        return self.last_reviewed or self.updated_date or self.captured_date or self.publish_date

    @property
    def requires_public_warning(self) -> bool:
        return self.status in NON_PUBLIC_STATUSES or not self.can_quote_externally

    def metadata_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "source_type": self.source_type,
            "record_type": self.record_type,
            "content_category": self.content_category,
            "parent_source_type": self.parent_source_type,
            "product": self.product,
            "industry": self.industry,
            "topic": self.topic,
            "funnel_stage": self.funnel_stage,
            "status": self.status,
            "publish_date": self.publish_date.isoformat(),
            "updated_date": self.updated_date.isoformat() if self.updated_date else None,
            "captured_date": self.captured_date.isoformat() if self.captured_date else None,
            "last_reviewed": self.last_reviewed.isoformat() if self.last_reviewed else None,
            "review_due_date": self.review_due_date.isoformat() if self.review_due_date else None,
            "source_path": self.source_path,
            "source_sheet": self.source_sheet,
            "source_row": self.source_row,
            "stable_record_id": self.stable_record_id,
            "canonical_url": self.canonical_url,
            "language": self.language,
            "author": self.author,
            "owner": self.owner,
            "asset_type": self.asset_type,
            "brand_name": self.brand_name,
            "merchant_handle": self.merchant_handle,
            "merchant_status": self.merchant_status,
            "interview_year": self.interview_year,
            "sales_category_lv1": self.sales_category_lv1,
            "sales_category_lv2": self.sales_category_lv2,
            "industry_subcategory": self.industry_subcategory,
            "content_tags": self.content_tags,
            "article_title": self.article_title,
            "video_title": self.video_title,
            "podcast_title": self.podcast_title,
            "news_title": self.news_title,
            "notes": self.notes,
            "metric_type": self.metric_type,
            "metric_name": self.metric_name,
            "claim_statement": self.claim_statement,
            "metric_note": self.metric_note,
            "metric_updated_date": self.metric_updated_date.isoformat() if self.metric_updated_date else None,
            "reference_source": self.reference_source,
            "claim_status": self.claim_status,
            "data_classification": self.data_classification,
            "can_quote_externally": self.can_quote_externally,
            "allowed_exposure_channels": self.allowed_exposure_channels,
            "website_url": self.website_url,
            "nda_signed": self.nda_signed,
            "nda_uploaded_salesforce": self.nda_uploaded_salesforce,
            "restricted_reason": self.restricted_reason,
            "restricted_aliases": self.restricted_aliases,
            "submitted_by": self.submitted_by,
            "restricted_updated_year": self.restricted_updated_year,
            "no_valid_content_asset": self.no_valid_content_asset,
            "can_enter_content_index": self.can_enter_content_index,
            "missing_allowed_exposure_channels": self.missing_allowed_exposure_channels,
            "restricted_note": self.restricted_note,
            "governance_issue_types": self.governance_issue_types,
            "governance_risk_reasons": self.governance_risk_reasons,
            "governance_risk_fields": self.governance_risk_fields,
            "invalid_asset_fields": self.invalid_asset_fields,
            "invalid_asset_values": self.invalid_asset_values,
            "same_brand_multiple_records": self.same_brand_multiple_records,
            "same_handle_multiple_records": self.same_handle_multiple_records,
            "multi_interview_record": self.multi_interview_record,
            "suspected_duplicate_review": self.suspected_duplicate_review,
        }


# Shadow-scheme keys that carry no meaning until Stable Record V2 is activated. They exist in
# memory and in the SQLite ``metadata_json`` round-trip, but an unresolved one must never be
# rendered into a governed Markdown file: ``row_v1`` is still the mutation authority, and a Vault
# file carrying ``stable_record_id: null`` states a successor scheme that nobody has activated.
UNRESOLVED_SHADOW_IDENTITY_KEYS = ("stable_record_id",)


def governed_markdown_frontmatter(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``payload`` with unresolved shadow identity keys omitted.

    This is the single serialization boundary every governed Markdown writer shares. It drops a
    shadow key only when its value is ``None``; a resolved ``MKA-MC-#####`` is preserved verbatim,
    because omitting a real identity would be its own kind of silent loss. Nothing else is
    filtered -- other ``None`` values are part of the existing frontmatter contract and stay.
    """
    frontmatter = dict(payload)
    for key in UNRESOLVED_SHADOW_IDENTITY_KEYS:
        if frontmatter.get(key) is None:
            frontmatter.pop(key, None)
    return frontmatter


class Document(BaseModel):
    id: str
    metadata: DocumentMetadata
    content: str

    @property
    def source_path(self) -> str:
        return self.metadata.source_path


class Chunk(BaseModel):
    id: str
    document_id: str
    chunk_index: int
    text: str
    metadata: DocumentMetadata
    start_char: int = 0
    end_char: int = 0

    @property
    def source_path(self) -> str:
        return self.metadata.source_path


class SearchFilters(BaseModel):
    intent: Literal["internal", "external"] = "internal"
    record_type: List[str] = Field(default_factory=list)
    source_type: List[str] = Field(default_factory=list)
    content_category: List[str] = Field(default_factory=list)
    parent_source_type: List[str] = Field(default_factory=list)
    brand_name: List[str] = Field(default_factory=list)
    merchant_handle: List[str] = Field(default_factory=list)
    merchant_status: List[str] = Field(default_factory=list)
    product: List[str] = Field(default_factory=list)
    industry: List[str] = Field(default_factory=list)
    sales_category_lv1: List[str] = Field(default_factory=list)
    sales_category_lv2: List[str] = Field(default_factory=list)
    content_tags: List[str] = Field(default_factory=list)
    topic: List[str] = Field(default_factory=list)
    metric_type: List[str] = Field(default_factory=list)
    metric_name: List[str] = Field(default_factory=list)
    claim_status: List[str] = Field(default_factory=list)
    data_classification: List[str] = Field(default_factory=list)
    exposure_channel: List[AllowedExposureChannel] = Field(default_factory=list)
    funnel_stage: List[str] = Field(default_factory=list)
    status: List[str] = Field(default_factory=list)
    can_quote_externally: Optional[bool] = None

    @validator(
        "record_type",
        "source_type",
        "content_category",
        "parent_source_type",
        "brand_name",
        "merchant_handle",
        "merchant_status",
        "product",
        "industry",
        "sales_category_lv1",
        "sales_category_lv2",
        "content_tags",
        "topic",
        "metric_type",
        "metric_name",
        "claim_status",
        "data_classification",
        "exposure_channel",
        "funnel_stage",
        "status",
        pre=True,
    )
    def normalize_filter_lists(cls, value: Any) -> List[str]:
        return _normalize_list(value)

    @validator("intent", pre=True)
    def normalize_intent(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    def is_empty(self) -> bool:
        return not any(
            [
                self.source_type,
                self.content_category,
                self.parent_source_type,
                self.record_type,
                self.brand_name,
                self.merchant_handle,
                self.merchant_status,
                self.product,
                self.industry,
                self.sales_category_lv1,
                self.sales_category_lv2,
                self.content_tags,
                self.topic,
                self.metric_type,
                self.metric_name,
                self.claim_status,
                self.data_classification,
                self.exposure_channel,
                self.funnel_stage,
                self.status,
                self.can_quote_externally is not None,
            ]
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "record_type": self.record_type,
            "source_type": self.source_type,
            "content_category": self.content_category,
            "parent_source_type": self.parent_source_type,
            "brand_name": self.brand_name,
            "merchant_handle": self.merchant_handle,
            "merchant_status": self.merchant_status,
            "product": self.product,
            "industry": self.industry,
            "sales_category_lv1": self.sales_category_lv1,
            "sales_category_lv2": self.sales_category_lv2,
            "content_tags": self.content_tags,
            "topic": self.topic,
            "metric_type": self.metric_type,
            "metric_name": self.metric_name,
            "claim_status": self.claim_status,
            "data_classification": self.data_classification,
            "exposure_channel": self.exposure_channel,
            "funnel_stage": self.funnel_stage,
            "status": self.status,
            "can_quote_externally": self.can_quote_externally,
        }


class SearchResult(BaseModel):
    chunk: Chunk
    score: float
    keyword_score: float = 0.0
    vector_score: float = 0.0
    rerank_score: float = 0.0


class Citation(BaseModel):
    label: str
    title: str
    source_path: str
    chunk_id: str
    status: Status
    source_type: SourceType
    record_type: RecordType
    data_classification: DataClassification
    can_quote_externally: bool
    publish_date: str
    updated_date: Optional[str] = None
    captured_date: Optional[str] = None
    last_reviewed: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    canonical_url: Optional[str] = None
    freshness_note: str
    allowed_exposure_channels: List[AllowedExposureChannel] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    retrieval_reason: Optional[str] = None


class StructuredAsset(BaseModel):
    asset_type: str
    title: str
    url: Optional[str] = None
    published_at: Optional[str] = None
    publication_status: Optional[str] = None
    external_usage_status: str
    source_record_id: str
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    citation_label: str


class StructuredEntity(BaseModel):
    entity_type: Literal["merchant", "partner", "unknown"] = "unknown"
    entity_name: str
    merchant_handle: Optional[str] = None
    sales_category_lv1: Optional[str] = None
    sales_category_lv2: Optional[str] = None
    interview_year: Optional[int] = None
    assets: List[StructuredAsset] = Field(default_factory=list)


class StructuredRetrievalResult(BaseModel):
    query_plan: Dict[str, Any]
    matched_entities: List[StructuredEntity] = Field(default_factory=list)
    total_entities: int = 0
    total_assets: int = 0
    warnings: List[str] = Field(default_factory=list)
    supported_constraints: List[Dict[str, Any]] = Field(default_factory=list)
    unsupported_constraints: List[Dict[str, Any]] = Field(default_factory=list)
    ambiguous_constraints: List[Dict[str, Any]] = Field(default_factory=list)
    invalid_constraints: List[Dict[str, Any]] = Field(default_factory=list)
    execution_blocked: bool = False
    abstained: bool = False
    abstain_reason: Optional[str] = None
    # True when a retrieval stage dropped candidates it had already found, so the counts below
    # describe what was admitted rather than what exists. Set before any grouping or presentation
    # filtering runs, so a layer that shrinks the visible result cannot erase the fact.
    retrieval_truncated: bool = False


class GeneratedAnswer(BaseModel):
    question: str
    answer: str
    citations: List[Citation]
    warnings: List[str] = Field(default_factory=list)
    governance_checked: bool = False
    query_plan: Optional[Dict[str, Any]] = None
    structured_result: Optional[StructuredRetrievalResult] = None
