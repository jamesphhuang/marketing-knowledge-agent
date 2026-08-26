from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence

from .models import DocumentMetadata

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, not runtime behaviour
    from .search_taxonomy import SearchTaxonomy, TaxonomyResolution


@dataclass(frozen=True)
class FieldDefinition:
    canonical_name: str
    source_field: Optional[str]
    metadata_source: Optional[str]
    data_type: str
    accepted_aliases: List[str]
    normalization_rule: str
    allowed_operators: List[str]
    exact_behavior: str
    hard_filter: bool
    output_label: str
    governance_sensitivity: str = "normal"
    available: bool = True
    searchable: bool = True
    executable: bool = True
    value_scope: str = "record_level"
    unsupported_reason: Optional[str] = None


def _field(
    canonical_name: str,
    source_field: Optional[str],
    data_type: str,
    aliases: Sequence[str],
    operators: Sequence[str],
    output_label: str,
    *,
    exact_behavior: str = "exact",
    hard_filter: bool = True,
    governance_sensitivity: str = "normal",
    available: bool = True,
    searchable: Optional[bool] = None,
    executable: Optional[bool] = None,
    value_scope: str = "record_level",
    unsupported_reason: Optional[str] = None,
) -> FieldDefinition:
    searchable = available if searchable is None else searchable
    executable = available if executable is None else executable
    return FieldDefinition(
        canonical_name=canonical_name,
        source_field=source_field,
        metadata_source=source_field,
        data_type=data_type,
        accepted_aliases=list(aliases),
        normalization_rule="unicode_nfkc_casefold_trim",
        allowed_operators=list(operators),
        exact_behavior=exact_behavior,
        hard_filter=hard_filter,
        output_label=output_label,
        governance_sensitivity=governance_sensitivity,
        available=available,
        searchable=searchable,
        executable=executable,
        value_scope=value_scope,
        unsupported_reason=unsupported_reason,
    )


# Canonical registry for parser, executor, explain output, and documentation tests.
FIELD_REGISTRY: Dict[str, FieldDefinition] = {
    "interview_year": _field(
        "interview_year", "interview_year", "integer", ["採訪年份"],
        ["eq", "in", "range", "gte", "lte"], "採訪年份"
    ),
    "interview_date": _field(
        "interview_date", None, "date", ["採訪日期"],
        ["eq", "range", "before", "after"], "採訪日期", available=False,
        value_scope="record_level", unsupported_reason="formal index does not contain interview_date"
    ),
    "entity_name": _field(
        "entity_name", "brand_name", "string", ["商家名稱", "夥伴名稱", "品牌"],
        ["exact"], "商家／夥伴名稱", exact_behavior="canonical_exact"
    ),
    "merchant_name": _field(
        "merchant_name", "brand_name", "string", ["商家名稱", "品牌名稱"],
        ["exact"], "商家名稱", exact_behavior="canonical_exact"
    ),
    "partner_name": _field(
        "partner_name", None, "string", ["夥伴名稱"], ["exact"], "夥伴名稱",
        exact_behavior="canonical_exact", available=False,
        unsupported_reason="formal index does not contain partner_name"
    ),
    "merchant_handle": _field(
        "merchant_handle", "merchant_handle", "normalized_string", ["handle", "merchant handle"],
        ["exact"], "Merchant Handle", exact_behavior="canonical_exact"
    ),
    "sales_category_lv1": _field(
        "sales_category_lv1", "sales_category_lv1", "canonical_enum", ["category lv1", "產業大類"],
        ["canonical_exact", "in"], "Sales Category LV1", exact_behavior="canonical_exact"
    ),
    "sales_category_lv2": _field(
        "sales_category_lv2", "sales_category_lv2", "canonical_enum", ["category lv2", "產業次分類"],
        ["canonical_exact", "in"], "Sales Category LV2", exact_behavior="canonical_exact"
    ),
    "content_tags": _field(
        "content_tags", "content_tags", "controlled_string_collection", ["內容標籤", "標籤"],
        ["contains_exact_tag", "contains_all", "contains_any"], "內容相關標籤",
        exact_behavior="exact_tag"
    ),
    "asset_type": _field(
        "asset_type", "asset_type", "enum", ["素材類型", "內容類型"],
        ["exact", "in"], "內容類型", exact_behavior="canonical_exact"
    ),
    "interview_status": _field(
        "interview_status", None, "enum", ["採訪狀態", "已採訪"],
        ["exact", "in"], "採訪狀態", available=False,
        unsupported_reason="formal index does not contain interview_status"
    ),
    "content_status": _field(
        "content_status", None, "enum", ["內容狀態"], ["exact", "in"], "內容狀態",
        governance_sensitivity="publication", available=False,
        unsupported_reason="formal index does not contain content_status"
    ),
    "publication_status": _field(
        "publication_status", None, "enum", ["上線狀態", "發布狀態", "已上線", "已發布", "已公開"],
        ["exact", "in"], "內容上線狀態", governance_sensitivity="external_usage",
        available=False, value_scope="asset_level",
        unsupported_reason="asset-level publication status is not available"
    ),
    "merchant_status": _field(
        "merchant_status", "merchant_status", "string", ["商家狀態", "夥伴狀態"],
        ["exact", "in"], "商家／夥伴狀態", governance_sensitivity="merchant_risk"
    ),
    "review_status": _field(
        "review_status", None, "enum", ["審核狀態", "待審核", "已審核"],
        ["exact", "in"], "審核狀態", governance_sensitivity="review", available=False,
        unsupported_reason="formal index does not contain review_status"
    ),
    "review_decision": _field(
        "review_decision", None, "enum", ["審核決策"], ["exact", "in"], "審核決策",
        governance_sensitivity="review", available=False,
        unsupported_reason="review_decision is not searchable in the formal index"
    ),
    "governance_status": _field(
        "governance_status", None, "enum", ["治理狀態"], ["exact", "in"], "治理狀態",
        governance_sensitivity="governance", available=False,
        unsupported_reason="formal index does not contain governance_status"
    ),
    "claim_status": _field(
        "claim_status", "claim_status", "enum", ["數據審核狀態"],
        ["exact", "in"], "數據聲明狀態", governance_sensitivity="claim"
    ),
    "external_usage_status": _field(
        "external_usage_status", "can_quote_externally", "boolean", ["可對外引用", "外部使用資格"],
        ["eq"], "對外引用", governance_sensitivity="external_usage"
    ),
    "citation_status": _field(
        "citation_status", "can_quote_externally", "boolean", ["引用狀態", "引用資格"],
        ["eq"], "引用資格", governance_sensitivity="external_usage"
    ),
    "asset_title": _field(
        "asset_title", "article/video/podcast/news_title", "string", ["素材標題", "內容標題"],
        ["exact", "lexical"], "素材標題", exact_behavior="canonical_exact"
    ),
    "asset_url": _field(
        "asset_url", None, "url", ["素材連結", "內容連結"], ["exact"], "素材連結",
        available=False, value_scope="asset_level",
        unsupported_reason="asset-level URL is not available"
    ),
    "published_at": _field(
        "published_at", None, "date", ["上線日期", "發布日期"],
        ["eq", "range", "before", "after"], "上線日期", available=False,
        value_scope="asset_level", unsupported_reason="asset-level published_at is not available"
    ),
    "title": _field(
        "title", "title", "string", ["標題", "內容標題"], ["exact", "lexical", "semantic"], "標題",
        exact_behavior="lexical_then_semantic", hard_filter=False
    ),
    "metric_name": _field(
        "metric_name", "metric_name", "string", ["指標名稱", "數據名稱"],
        ["exact", "lexical"], "指標名稱", exact_behavior="canonical_exact"
    ),
    "notes": _field(
        "notes", "notes", "string", ["備註"], ["lexical", "semantic"], "備註",
        exact_behavior="lexical_then_semantic", hard_filter=False, governance_sensitivity="may_contain_risk"
    ),
    "source_record_id": _field(
        "source_record_id", "source_sheet+source_row", "string", ["來源紀錄"], ["exact"], "來源紀錄"
    ),
    "allowed_exposure_channels": _field(
        "allowed_exposure_channels", "allowed_exposure_channels", "enum_collection", ["曝光渠道", "使用渠道"],
        ["contains_exact", "contains_any"], "允許曝光渠道", governance_sensitivity="external_usage"
    ),
    "can_enter_content_index": _field(
        "can_enter_content_index", "can_enter_content_index", "boolean", ["可進索引"],
        ["eq"], "可進內容索引", governance_sensitivity="index_eligibility"
    ),
}


RUNTIME_SUPPORT_MATRIX: Dict[str, Dict[str, object]] = {
    name: {
        "parser_recognizable": bool(definition.accepted_aliases),
        "query_plan_expressible": True,
        "executor_supported": definition.executable,
        "formal_data_available": bool(definition.metadata_source) and definition.executable,
        "slack_ready": definition.searchable and definition.executable,
        "value_scope": definition.value_scope,
        "unsupported_reason": definition.unsupported_reason,
    }
    for name, definition in FIELD_REGISTRY.items()
}


ASSET_TYPE_ALIASES = {
    "article": ("article", "文章"),
    "video": ("video", "影片", "影音"),
    "podcast": ("podcast", "播客"),
    "news": ("news", "新聞"),
    "other": ("other", "其他素材"),
}

# Aliases are curated and deliberately small. Substring inference is forbidden.
# Superseded, not deleted, when a Search Taxonomy Authority is supplied: two alias sources answering
# the same question at once is the competing-truth failure this parser must not have.
CATEGORY_ALIASES = {
    "家居生活": "居家生活",
}

# The three controlled-vocabulary fields a pinned Search Taxonomy Authority may speak for. Defined
# here, beside the registry that names them, so the Authority module can import one definition
# rather than restate it.
TAXONOMY_FIELDS = ("sales_category_lv1", "sales_category_lv2", "content_tags")
TAXONOMY_OPERATORS = {
    "sales_category_lv1": "canonical_exact",
    "sales_category_lv2": "canonical_exact",
    "content_tags": "contains_exact_tag",
}
TAXONOMY_SOURCE = "search_taxonomy_authority"
# A query states a bounded number of vocabulary terms. The cap stops a pathological query from
# driving the longest-alias scan indefinitely; it never changes which term wins.
TAXONOMY_SCAN_LIMIT = 8
ABSTAIN_TAXONOMY_AMBIGUOUS = "ambiguous_taxonomy_term"
ABSTAIN_TAXONOMY_NOT_INDEXED = "taxonomy_known_but_not_indexed"

PUBLICATION_STATUS_ALIASES = {
    "已上線": "published",
    "已發布": "published",
    "已公開": "published",
    "published": "published",
    "草稿": "draft",
    "draft": "draft",
    "已封存": "archived",
    "archived": "archived",
    "已棄用": "deprecated",
    "deprecated": "deprecated",
}

EXPOSURE_CHANNEL_ALIASES = {
    "press_release": ("press_release", "新聞稿"),
    "owned_media": ("owned_media", "自媒體"),
    "saleskits": ("saleskits", "saleskit", "sales kit"),
    "verbal_briefing": ("verbal_briefing", "口頭說明"),
    "speaking_deck": ("speaking_deck", "演講簡報"),
    "website_recruiting": ("website_recruiting", "官網", "招募網站"),
    "ads": ("ads", "廣告"),
}

FULL_DATE_PATTERN = r"(?<!\d)(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])(?!\d)"
EXPLICIT_CONSTRAINT_PATTERN = r"(?<![a-z0-9_])([a-z_][a-z0-9_]*)\s*=\s*([^+\s，,]+)"
REVIEW_STATUS_ALIASES = {
    "待審核": "pending",
    "已審核": "approved",
}


@dataclass(frozen=True)
class QueryCatalog:
    merchant_names: List[str] = field(default_factory=list)
    partner_names: List[str] = field(default_factory=list)
    merchant_handles: List[str] = field(default_factory=list)
    sales_category_lv1: List[str] = field(default_factory=list)
    sales_category_lv2: List[str] = field(default_factory=list)
    content_tags: List[str] = field(default_factory=list)
    metric_names: List[str] = field(default_factory=list)
    titles: List[str] = field(default_factory=list)
    article_titles: List[str] = field(default_factory=list)
    video_titles: List[str] = field(default_factory=list)
    podcast_titles: List[str] = field(default_factory=list)
    news_titles: List[str] = field(default_factory=list)

    @classmethod
    def from_metadata(cls, values: Iterable[DocumentMetadata]) -> "QueryCatalog":
        unique_metadata: Dict[str, DocumentMetadata] = {}
        for metadata in values:
            key = f"{metadata.source_path}|{metadata.source_sheet}|{metadata.source_row}|{metadata.title}"
            unique_metadata[key] = metadata
        records = list(unique_metadata.values())
        return cls(
            merchant_names=_unique(metadata.brand_name for metadata in records),
            partner_names=[],
            merchant_handles=_unique(metadata.merchant_handle for metadata in records),
            sales_category_lv1=_unique(metadata.sales_category_lv1 for metadata in records),
            sales_category_lv2=_unique(metadata.sales_category_lv2 for metadata in records),
            content_tags=_unique(tag for metadata in records for tag in metadata.content_tags),
            metric_names=_unique(metadata.metric_name for metadata in records),
            titles=_unique(metadata.title for metadata in records),
            article_titles=_unique(metadata.article_title for metadata in records),
            video_titles=_unique(metadata.video_title for metadata in records),
            podcast_titles=_unique(metadata.podcast_title for metadata in records),
            news_titles=_unique(metadata.news_title for metadata in records),
        )


@dataclass(frozen=True)
class ResolvedEntity:
    entity_type: str
    canonical_name: str
    matched_text: str
    source: str
    confidence: float


@dataclass(frozen=True)
class QueryConstraint:
    field: str
    value: Any
    normalized_value: Any
    operator: str
    match_type: str
    hard_filter: bool
    source: str
    confidence: float = 1.0
    support_status: str = "supported"
    reason: Optional[str] = None
    raw_value: Any = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TypedQueryPlan:
    raw_query: str
    normalized_query: str
    query_mode: str
    parsed_terms: List[str]
    resolved_entities: List[ResolvedEntity]
    constraints: List[QueryConstraint]
    operator: str = "AND"
    free_text_terms: List[str] = field(default_factory=list)
    requested_asset_types: List[str] = field(default_factory=list)
    sort: List[str] = field(default_factory=list)
    group_by: List[str] = field(default_factory=list)
    fallback_policy: str = "abstain"
    ambiguity_flags: List[str] = field(default_factory=list)
    parser_warnings: List[str] = field(default_factory=list)
    abstain_reason: Optional[str] = None

    @property
    def hard_constraints(self) -> List[QueryConstraint]:
        return [constraint for constraint in self.validated_constraints if constraint.hard_filter]

    @property
    def validated_constraints(self) -> List[QueryConstraint]:
        return [validate_constraint(constraint) for constraint in self.constraints]

    @property
    def supported_constraints(self) -> List[QueryConstraint]:
        return [item for item in self.validated_constraints if item.support_status == "supported"]

    @property
    def unsupported_constraints(self) -> List[QueryConstraint]:
        return [item for item in self.validated_constraints if item.support_status == "unsupported"]

    @property
    def ambiguous_constraints(self) -> List[QueryConstraint]:
        return [item for item in self.validated_constraints if item.support_status == "ambiguous"]

    @property
    def invalid_constraints(self) -> List[QueryConstraint]:
        return [item for item in self.validated_constraints if item.support_status == "invalid"]

    @property
    def effective_abstain_reason(self) -> Optional[str]:
        if self.abstain_reason:
            return self.abstain_reason
        if any(item.hard_filter for item in self.unsupported_constraints):
            return "unsupported_hard_constraint"
        if any(item.hard_filter for item in self.ambiguous_constraints):
            return "ambiguous_hard_constraint"
        if any(item.hard_filter for item in self.invalid_constraints):
            return "invalid_hard_constraint"
        return None

    @property
    def execution_blocked(self) -> bool:
        return self.effective_abstain_reason is not None

    def to_dict(self) -> dict:
        return {
            "raw_query": self.raw_query,
            "normalized_query": self.normalized_query,
            "query_mode": self.query_mode,
            "parsed_terms": list(self.parsed_terms),
            "resolved_entities": [asdict(entity) for entity in self.resolved_entities],
            "constraints": [constraint.to_dict() for constraint in self.validated_constraints],
            "operator": self.operator,
            "hard_filters": [constraint.to_dict() for constraint in self.hard_constraints],
            "supported_constraints": [item.to_dict() for item in self.supported_constraints],
            "unsupported_constraints": [item.to_dict() for item in self.unsupported_constraints],
            "ambiguous_constraints": [item.to_dict() for item in self.ambiguous_constraints],
            "invalid_constraints": [item.to_dict() for item in self.invalid_constraints],
            "execution_blocked": self.execution_blocked,
            "free_text_terms": list(self.free_text_terms),
            "requested_asset_types": list(self.requested_asset_types),
            "sort": list(self.sort),
            "group_by": list(self.group_by),
            "fallback_policy": self.fallback_policy,
            "ambiguity_flags": list(self.ambiguity_flags),
            "parser_warnings": list(self.parser_warnings),
            "abstain_reason": self.effective_abstain_reason,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TypedQueryPlan":
        return cls(
            raw_query=str(payload.get("raw_query") or ""),
            normalized_query=str(payload.get("normalized_query") or ""),
            query_mode=str(payload.get("query_mode") or "semantic_question"),
            parsed_terms=list(payload.get("parsed_terms") or []),
            resolved_entities=[ResolvedEntity(**item) for item in payload.get("resolved_entities") or []],
            constraints=[validate_constraint(QueryConstraint(**item)) for item in payload.get("constraints") or []],
            operator=str(payload.get("operator") or "AND"),
            free_text_terms=list(payload.get("free_text_terms") or []),
            requested_asset_types=list(payload.get("requested_asset_types") or []),
            sort=list(payload.get("sort") or []),
            group_by=list(payload.get("group_by") or []),
            fallback_policy=str(payload.get("fallback_policy") or "abstain"),
            ambiguity_flags=list(payload.get("ambiguity_flags") or []),
            parser_warnings=list(payload.get("parser_warnings") or []),
            abstain_reason=payload.get("abstain_reason"),
        )


def normalize_query_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()


def normalize_exact_value(value: object, *, handle: bool = False) -> str:
    normalized = normalize_query_text(value)
    if handle:
        normalized = normalized.lstrip("@").strip()
    return normalized


def validate_constraint(constraint: QueryConstraint) -> QueryConstraint:
    definition = FIELD_REGISTRY.get(constraint.field)
    raw_value = constraint.value if constraint.raw_value is None else constraint.raw_value
    if definition is None:
        return replace(
            constraint,
            support_status="unsupported",
            reason="unknown constraint field",
            raw_value=raw_value,
        )
    if constraint.operator not in definition.allowed_operators:
        return replace(
            constraint,
            support_status="invalid",
            reason=f"operator {constraint.operator} is not allowed for {constraint.field}",
            raw_value=raw_value,
        )
    if not definition.searchable or not definition.executable or not definition.metadata_source:
        return replace(
            constraint,
            support_status="unsupported",
            reason=definition.unsupported_reason or "constraint is not executable in the formal index",
            raw_value=raw_value,
        )
    if constraint.support_status not in {"supported", "unsupported", "ambiguous", "invalid"}:
        return replace(
            constraint,
            support_status="invalid",
            reason="unknown constraint support status",
            raw_value=raw_value,
        )
    return replace(constraint, raw_value=raw_value)


def build_query_plan(
    raw_query: str,
    catalog: QueryCatalog,
    taxonomy: Optional["SearchTaxonomy"] = None,
) -> TypedQueryPlan:
    """Build a typed plan, optionally reading vocabulary from a pinned Search Taxonomy Authority.

    ``taxonomy=None`` is the whole existing contract, unchanged: the runtime catalog and the small
    curated ``CATEGORY_ALIASES`` map remain the only alias sources. Supplying an Authority makes it
    the alias source for its three fields, and adds two ways to refuse rather than guess -- a term
    that names more than one canonical value, and a term the Authority knows that the formal index
    does not carry.
    """
    normalized = normalize_query_text(raw_query)
    constraints: List[QueryConstraint] = []
    resolved_entities: List[ResolvedEntity] = []
    parsed_terms: List[str] = []
    requested_asset_types: List[str] = []
    ambiguity_flags: List[str] = []
    parser_warnings: List[str] = []
    matched_fragments: List[str] = []
    identity_fragments: List[str] = []
    taxonomy_decided_fields: set = set()
    taxonomy_fragments: List[str] = []
    taxonomy_abstain_reason: Optional[str] = None
    operator = "OR" if re.search(r"(?:或|任一|其中之一)", normalized) else "AND"

    for match in re.finditer(EXPLICIT_CONSTRAINT_PATTERN, normalized):
        field_name, raw_value = match.group(1), match.group(2)
        value, explicit_operator = _explicit_constraint_value(field_name, raw_value)
        if taxonomy is not None and field_name in TAXONOMY_FIELDS:
            # An explicitly typed field states the taxonomy domain, so the Authority resolves inside
            # that domain only. This is the one way past a cross-level collision.
            outcome = _taxonomy_outcome(
                taxonomy.resolve(raw_value, field=field_name),
                catalog,
                source="explicit_field_parser",
                raw_value=raw_value,
            )
            if outcome.decided:
                taxonomy_decided_fields.add(field_name)
                taxonomy_decided_fields.update(outcome.fields)
                taxonomy_abstain_reason = taxonomy_abstain_reason or outcome.abstain_reason
                _extend_unique(ambiguity_flags, outcome.ambiguity_flags)
                _extend_unique(parser_warnings, outcome.parser_warnings)
                if outcome.constraint is not None:
                    constraints.append(outcome.constraint)
                parsed_terms.append(match.group(0))
                matched_fragments.append(match.group(0))
                # The user named the field, so this text is spent. Without removing it the catalog
                # pass below re-reads the value against the *other* level's catalog and widens an
                # explicitly scoped query into a second taxonomy field.
                taxonomy_fragments.append(match.group(0))
                continue
        constraints.append(_constraint(field_name, value, explicit_operator, "explicit_field_parser", raw_value=raw_value))
        parsed_terms.append(match.group(0))
        matched_fragments.append(match.group(0))
        if field_name == "asset_type" and str(value) in ASSET_TYPE_ALIASES:
            requested_asset_types.append(str(value))

    date_query = _remove_fragments(normalized, matched_fragments)
    for match in re.finditer(FULL_DATE_PATTERN, date_query):
        raw_date = match.group(0)
        canonical_date = _canonical_date(match)
        date_field = (
            "published_at"
            if any(marker in date_query for marker in ("上線", "發布", "公開", "published"))
            else "interview_date"
        )
        constraints.append(_constraint(date_field, canonical_date, "eq", "typed_date_parser", raw_value=raw_date))
        parsed_terms.append(raw_date)
        matched_fragments.append(raw_date)

    for match in re.finditer(r"https?://[^\s+，,]+", normalized):
        raw_url = match.group(0)
        if any(raw_url in fragment for fragment in matched_fragments):
            continue
        constraints.append(_constraint("asset_url", raw_url, "exact", "typed_url_parser", raw_value=raw_url))
        parsed_terms.append(raw_url)
        matched_fragments.append(raw_url)

    year_query = re.sub(FULL_DATE_PATTERN, " ", date_query)
    range_match = re.search(r"(?<!\d)(20\d{2})\s*(?:~|～|至|到|-)\s*(20\d{2})(?!\d)", year_query)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        constraints.append(_constraint("interview_year", [min(start, end), max(start, end)], "range", "typed_date_parser"))
        parsed_terms.append(range_match.group(0))
        matched_fragments.append(range_match.group(0))
    else:
        year_matches = list(re.finditer(r"(?<!\d)(20\d{2})(?:\s*年)?(?!\d)", year_query))
        for match in year_matches:
            constraints.append(_constraint("interview_year", int(match.group(1)), "eq", "typed_date_parser"))
            parsed_terms.append(match.group(0))
            matched_fragments.append(match.group(0))
        if len(year_matches) > 1 and operator != "OR":
            ambiguity_flags.append("conflicting_interview_years")
            parser_warnings.append("多個採訪年份未指定「或」或區間，未自行放寬條件。")

    handle_match = _first_catalog_match(normalized, catalog.merchant_handles, handle=True)
    if handle_match:
        constraints.append(_constraint("merchant_handle", handle_match, "exact", "entity_resolver"))
        resolved_entities.append(ResolvedEntity("merchant", handle_match, handle_match, "merchant_handle", 1.0))
        parsed_terms.append(handle_match)
        matched_fragments.append(handle_match)
        identity_fragments.append(handle_match)

    merchant_match = _first_catalog_match(normalized, catalog.merchant_names)
    partner_match = _first_catalog_match(normalized, catalog.partner_names)
    if merchant_match and partner_match and normalize_exact_value(merchant_match) == normalize_exact_value(partner_match):
        ambiguity_flags.append("entity_type_ambiguous")
        parser_warnings.append("名稱同時存在於商家與夥伴欄位，需確認 entity_type。")
    elif merchant_match:
        constraints.append(_constraint("entity_name", merchant_match, "exact", "entity_resolver"))
        resolved_entities.append(ResolvedEntity("merchant", merchant_match, merchant_match, "merchant_name", 1.0))
        parsed_terms.append(merchant_match)
        matched_fragments.append(merchant_match)
        identity_fragments.append(merchant_match)
    elif partner_match:
        constraints.append(_constraint("partner_name", partner_match, "exact", "entity_resolver"))
        resolved_entities.append(ResolvedEntity("partner", partner_match, partner_match, "partner_name", 1.0))
        parsed_terms.append(partner_match)
        matched_fragments.append(partner_match)
        identity_fragments.append(partner_match)
    elif _contains_exact_phrase(normalized, "夥伴名稱"):
        constraints.append(_constraint("partner_name", "夥伴名稱", "exact", "field_resolver"))
        parsed_terms.append("夥伴名稱")
        matched_fragments.append("夥伴名稱")

    field_query = _remove_fragments(normalized, identity_fragments)

    catalog_query = field_query
    if taxonomy is not None:
        # Runs on what is left after identity resolution has claimed its fragments, so a brand whose
        # name happens to equal a taxonomy term is never re-read as vocabulary.
        for outcome in _scan_taxonomy_terms(
            _remove_fragments(field_query, matched_fragments), catalog, taxonomy
        ):
            if outcome.fields and all(name in taxonomy_decided_fields for name in outcome.fields):
                continue
            taxonomy_decided_fields.update(outcome.fields)
            taxonomy_abstain_reason = taxonomy_abstain_reason or outcome.abstain_reason
            _extend_unique(ambiguity_flags, outcome.ambiguity_flags)
            _extend_unique(parser_warnings, outcome.parser_warnings)
            if outcome.constraint is not None:
                constraints.append(outcome.constraint)
                parsed_terms.append(str(outcome.constraint.value))
            if outcome.matched_text:
                matched_fragments.append(outcome.matched_text)
                taxonomy_fragments.append(outcome.matched_text)
        # A term the Authority has already claimed is spent. Without this the catalog pass would
        # re-read the shorter value inside it -- "居家生活相關" claimed as LV2 still contains the
        # LV1 value "居家生活" -- and add a second, unasked-for constraint beside the first.
        catalog_query = _remove_fragments(field_query, taxonomy_fragments)

    category_match = None
    category_field = None
    if "sales_category_lv1" not in taxonomy_decided_fields:
        category_match = _first_catalog_match(catalog_query, catalog.sales_category_lv1)
        category_field = "sales_category_lv1" if category_match else None
    if not category_match and "sales_category_lv2" not in taxonomy_decided_fields:
        category_match = _first_catalog_match(catalog_query, catalog.sales_category_lv2)
        category_field = "sales_category_lv2" if category_match else None
    if not category_match and taxonomy is None:
        for alias, canonical in CATEGORY_ALIASES.items():
            if _contains_exact_phrase(field_query, alias) and canonical in catalog.sales_category_lv1:
                category_match = canonical
                category_field = "sales_category_lv1"
                matched_fragments.append(alias)
                break
    if category_match:
        constraints.append(_constraint(category_field, category_match, "canonical_exact", "field_resolver"))
        parsed_terms.append(category_match)
        matched_fragments.append(category_match)

    tag_match = (
        _first_catalog_match(catalog_query, catalog.content_tags)
        if "content_tags" not in taxonomy_decided_fields
        else None
    )
    if tag_match:
        constraints.append(_constraint("content_tags", tag_match, "contains_exact_tag", "field_resolver"))
        parsed_terms.append(tag_match)
        matched_fragments.append(tag_match)

    metric_match = _first_catalog_match(field_query, catalog.metric_names)
    if metric_match:
        constraints.append(_constraint("metric_name", metric_match, "exact", "field_resolver"))
        parsed_terms.append(metric_match)
        matched_fragments.append(metric_match)

    title_match = _exact_catalog_value(normalized, catalog.titles)
    if title_match:
        constraints.append(
            QueryConstraint(
                field="title",
                value=title_match,
                normalized_value=normalize_exact_value(title_match),
                operator="exact",
                match_type="canonical_exact",
                hard_filter=True,
                source="field_resolver",
                confidence=1.0,
            )
        )
        parsed_terms.append(title_match)
        matched_fragments.append(title_match)

    for asset_type, asset_titles in (
        ("article", catalog.article_titles),
        ("video", catalog.video_titles),
        ("podcast", catalog.podcast_titles),
        ("news", catalog.news_titles),
    ):
        asset_title_match = _exact_catalog_value(normalized, asset_titles)
        if not asset_title_match:
            continue
        constraints.append(_constraint("asset_title", asset_title_match, "exact", "field_resolver"))
        requested_asset_types.append(asset_type)
        parsed_terms.append(asset_title_match)
        matched_fragments.append(asset_title_match)

    for canonical, aliases in ASSET_TYPE_ALIASES.items():
        if canonical == "news" and _contains_exact_phrase(field_query, "新聞稿"):
            continue
        alias = next((item for item in aliases if _contains_exact_phrase(field_query, item)), None)
        if alias:
            constraints.append(_constraint("asset_type", canonical, "exact", "field_resolver"))
            requested_asset_types.append(canonical)
            parsed_terms.append(alias)
            matched_fragments.append(alias)

    for canonical, aliases in EXPOSURE_CHANNEL_ALIASES.items():
        alias = next((item for item in aliases if _contains_exact_phrase(field_query, item)), None)
        if not alias:
            continue
        constraints.append(
            _constraint("allowed_exposure_channels", canonical, "contains_exact", "governance_resolver")
        )
        parsed_terms.append(alias)
        matched_fragments.append(alias)

    if _contains_exact_phrase(field_query, "已採訪"):
        constraints.append(_constraint("interview_status", "interviewed", "exact", "status_resolver", raw_value="已採訪"))
        ambiguity_flags.append("interview_status_unavailable")
        parser_warnings.append("目前 schema 沒有 interview_status，不能以 publication_status 代替。")
        parsed_terms.append("已採訪")
        matched_fragments.append("已採訪")

    for alias, canonical in REVIEW_STATUS_ALIASES.items():
        if _contains_exact_phrase(field_query, alias):
            constraints.append(_constraint("review_status", canonical, "exact", "status_resolver", raw_value=alias))
            parsed_terms.append(alias)
            matched_fragments.append(alias)
            break

    for alias, canonical in PUBLICATION_STATUS_ALIASES.items():
        if _contains_exact_phrase(field_query, alias):
            constraints.append(_constraint("publication_status", canonical, "exact", "status_resolver", raw_value=alias))
            parsed_terms.append(alias)
            matched_fragments.append(alias)
            break

    if "不可對外引用" not in field_query and "不可直接對外引用" not in field_query and _contains_exact_phrase(field_query, "可對外引用"):
        constraints.append(_constraint("external_usage_status", True, "eq", "governance_resolver"))
        parsed_terms.append("可對外引用")
        matched_fragments.append("可對外引用")

    constraints = _dedupe_constraints(constraints)
    unsupported_constraints = [item for item in constraints if item.support_status == "unsupported"]
    invalid_constraints = [item for item in constraints if item.support_status == "invalid"]
    for constraint in unsupported_constraints:
        flag = f"unsupported_constraint:{constraint.field}"
        if flag not in ambiguity_flags:
            ambiguity_flags.append(flag)
        warning = _constraint_warning(constraint)
        if warning not in parser_warnings:
            parser_warnings.append(warning)
    for constraint in invalid_constraints:
        flag = f"invalid_constraint:{constraint.field}"
        if flag not in ambiguity_flags:
            ambiguity_flags.append(flag)
        warning = _constraint_warning(constraint)
        if warning not in parser_warnings:
            parser_warnings.append(warning)

    free_text_terms = _remaining_terms(normalized, matched_fragments)
    semantic_markers = ("如何", "為什麼", "原因", "策略", "比較", "分析", "摘要", "共同", "提升", "成效")
    has_semantic_intent = any(marker in normalized for marker in semantic_markers)
    if taxonomy_abstain_reason:
        # The Authority recognised a term in this query. Whatever else the query looks like, it is a
        # structured lookup that could not be executed -- not free text to search broadly.
        query_mode = "structured_lookup"
    elif any(item.hard_filter and item.support_status != "supported" for item in constraints):
        query_mode = "structured_lookup"
    elif constraints and not has_semantic_intent:
        query_mode = "structured_lookup"
    elif constraints:
        query_mode = "semantic_question"
    elif _looks_like_lookup(normalized):
        query_mode = "structured_lookup"
        ambiguity_flags.append("unresolved_lookup_term")
        parser_warnings.append("未在 canonical metadata 中找到精確名稱、Handle、分類、標籤或欄位值。")
    else:
        query_mode = "semantic_question"

    abstain_reason = None
    if taxonomy_abstain_reason:
        # Deliberately outranks ``unresolved_structured_lookup``: that reason is the one
        # ``allow_semantic_fallback`` is allowed to clear, and clearing a recognised-but-unusable
        # taxonomy term would turn a refusal into the broad search this parser must never run.
        abstain_reason = taxonomy_abstain_reason
    elif any(item.hard_filter for item in unsupported_constraints):
        abstain_reason = "unsupported_hard_constraint"
    elif any(item.hard_filter for item in invalid_constraints):
        abstain_reason = "invalid_hard_constraint"
    elif "conflicting_interview_years" in ambiguity_flags:
        abstain_reason = "conflicting_constraints"
    elif query_mode == "structured_lookup" and not constraints:
        abstain_reason = "unresolved_structured_lookup"
    elif "entity_type_ambiguous" in ambiguity_flags:
        abstain_reason = "ambiguous_entity_type"

    return TypedQueryPlan(
        raw_query=raw_query,
        normalized_query=normalized,
        query_mode=query_mode,
        parsed_terms=_unique(parsed_terms),
        resolved_entities=resolved_entities,
        constraints=constraints,
        operator=operator,
        free_text_terms=free_text_terms,
        requested_asset_types=_unique(requested_asset_types),
        sort=["relevance"],
        group_by=["entity"] if any(c.field in {"entity_name", "merchant_handle", "sales_category_lv1", "sales_category_lv2"} for c in constraints) else [],
        fallback_policy="abstain",
        ambiguity_flags=ambiguity_flags,
        parser_warnings=parser_warnings,
        abstain_reason=abstain_reason,
    )


def metadata_matches_query_plan(metadata: DocumentMetadata, plan: Optional[TypedQueryPlan]) -> bool:
    if plan is None:
        return True
    if plan.execution_blocked:
        return False
    hard_constraints = plan.hard_constraints
    if not hard_constraints:
        return True
    decisions = [_metadata_matches_constraint(metadata, constraint) for constraint in hard_constraints]
    return any(decisions) if plan.operator == "OR" else all(decisions)


def _metadata_matches_constraint(metadata: DocumentMetadata, constraint: QueryConstraint) -> bool:
    constraint = validate_constraint(constraint)
    if constraint.support_status != "supported":
        return False
    field_name = constraint.field
    value = constraint.normalized_value
    if field_name in {"entity_name", "merchant_name"}:
        return normalize_exact_value(metadata.brand_name) == normalize_exact_value(value)
    if field_name == "partner_name":
        return False
    if field_name == "merchant_handle":
        return normalize_exact_value(metadata.merchant_handle, handle=True) == normalize_exact_value(value, handle=True)
    if field_name in {"sales_category_lv1", "sales_category_lv2", "merchant_status", "claim_status"}:
        source_field = FIELD_REGISTRY[field_name].source_field
        return normalize_exact_value(getattr(metadata, source_field or "", None)) == normalize_exact_value(value)
    if field_name == "content_tags":
        normalized_tags = {normalize_exact_value(item) for item in metadata.content_tags}
        return normalize_exact_value(value) in normalized_tags
    if field_name == "metric_name":
        return normalize_exact_value(metadata.metric_name) == normalize_exact_value(value)
    if field_name == "title":
        return normalize_exact_value(metadata.title) == normalize_exact_value(value)
    if field_name == "asset_title":
        return normalize_exact_value(value) in {
            normalize_exact_value(item)
            for item in (
                metadata.article_title,
                metadata.video_title,
                metadata.podcast_title,
                metadata.news_title,
            )
            if item
        }
    if field_name == "asset_type":
        return _metadata_has_asset_type(metadata, str(value))
    if field_name == "interview_year":
        if metadata.interview_year is None:
            return False
        if constraint.operator == "range":
            start, end = constraint.value
            return start <= metadata.interview_year <= end
        if constraint.operator == "in":
            return metadata.interview_year in constraint.value
        if constraint.operator == "gte":
            return metadata.interview_year >= int(constraint.value)
        if constraint.operator == "lte":
            return metadata.interview_year <= int(constraint.value)
        return metadata.interview_year == int(constraint.value)
    if field_name in {"external_usage_status", "citation_status"}:
        return metadata.can_quote_externally is bool(value) or metadata.can_quote_externally == value
    if field_name == "allowed_exposure_channels":
        return normalize_exact_value(value) in {normalize_exact_value(item) for item in metadata.allowed_exposure_channels}
    if field_name == "can_enter_content_index":
        return metadata.can_enter_content_index == value
    if field_name == "source_record_id":
        record_id = f"{metadata.source_sheet}:r{metadata.source_row}"
        return normalize_exact_value(record_id) == normalize_exact_value(value)
    return False


def _metadata_has_asset_type(metadata: DocumentMetadata, asset_type: str) -> bool:
    field_name = {
        "article": "article_title",
        "video": "video_title",
        "podcast": "podcast_title",
        "news": "news_title",
    }.get(asset_type)
    if field_name:
        return bool(getattr(metadata, field_name))
    return normalize_exact_value(metadata.asset_type) == normalize_exact_value(asset_type)


def _constraint(
    field_name: str,
    value: Any,
    operator: str,
    source: str,
    *,
    raw_value: Any = None,
) -> QueryConstraint:
    definition = FIELD_REGISTRY.get(field_name)
    normalized_value = value
    if isinstance(value, str):
        normalized_value = normalize_exact_value(value, handle=field_name == "merchant_handle")
    constraint = QueryConstraint(
        field=field_name,
        value=value,
        normalized_value=normalized_value,
        operator=operator,
        match_type=definition.exact_behavior if definition else "exact",
        hard_filter=definition.hard_filter if definition else True,
        source=source,
        confidence=1.0,
        raw_value=value if raw_value is None else raw_value,
    )
    return validate_constraint(constraint)


def _explicit_constraint_value(field_name: str, raw_value: str) -> tuple:
    if field_name == "interview_year" and raw_value.isdigit():
        return int(raw_value), "eq"
    if field_name in {"interview_date", "published_at"}:
        return raw_value, "eq"
    if field_name in {"external_usage_status", "citation_status", "can_enter_content_index"}:
        normalized = normalize_exact_value(raw_value)
        if normalized in {"true", "1", "yes"}:
            return True, "eq"
        if normalized in {"false", "0", "no"}:
            return False, "eq"
    operator = {
        "sales_category_lv1": "canonical_exact",
        "sales_category_lv2": "canonical_exact",
        "content_tags": "contains_exact_tag",
        "allowed_exposure_channels": "contains_exact",
    }.get(field_name, "exact")
    return raw_value, operator


def _canonical_date(match: re.Match) -> str:
    year, month, day = (int(match.group(index)) for index in (1, 2, 3))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return match.group(0)


def _constraint_warning(constraint: QueryConstraint) -> str:
    definition = FIELD_REGISTRY.get(constraint.field)
    label = definition.output_label if definition else constraint.field
    if constraint.support_status == "invalid":
        return f"搜尋條件「{label}」格式或 operator 無效，未執行近似搜尋。"
    return f"目前正式索引不支援搜尋條件「{label}」，未執行近似搜尋。"


@dataclass(frozen=True)
class _TaxonomyOutcome:
    """What the Authority decided about one term, before the plan is assembled."""

    decided: bool
    fields: tuple = ()
    constraint: Optional[QueryConstraint] = None
    matched_text: Optional[str] = None
    ambiguity_flags: tuple = ()
    parser_warnings: tuple = ()
    abstain_reason: Optional[str] = None


def _taxonomy_outcome(
    resolution: "TaxonomyResolution",
    catalog: QueryCatalog,
    *,
    source: str,
    raw_value: object,
) -> _TaxonomyOutcome:
    """Turn one Authority resolution into a constraint, or into a refusal.

    The Authority states what a term means; it does not state what the formal index carries. A term
    it resolves cleanly but the index has never seen is refused here rather than passed through as a
    constraint that would quietly match nothing, or worse, be relaxed into a broad search.
    """
    # Compared as plain strings: ``TaxonomyResolutionStatus`` is a ``str`` enum, so this module does
    # not import it and the one-way import from the Authority module back to here stays acyclic.
    if resolution.status == "not_found":
        return _TaxonomyOutcome(decided=False)

    if resolution.status == "ambiguous":
        fields = tuple(sorted({candidate_field for candidate_field, _ in resolution.candidates}))
        labels = "、".join(
            f"{FIELD_REGISTRY[candidate_field].output_label}：{canonical}"
            if candidate_field in FIELD_REGISTRY
            else f"{candidate_field}：{canonical}"
            for candidate_field, canonical in resolution.candidates
        )
        return _TaxonomyOutcome(
            decided=True,
            fields=fields,
            ambiguity_flags=(f"taxonomy_ambiguous_term:{resolution.normalized_alias}",),
            parser_warnings=(
                f"搜尋詞「{raw_value}」在 Search Taxonomy Authority 對應多個正式值（{labels}），"
                "請指定 sales_category_lv1、sales_category_lv2 或 content_tags，未自行選一個。",
            ),
            abstain_reason=ABSTAIN_TAXONOMY_AMBIGUOUS,
        )

    field_name = resolution.field
    indexed_value = _catalog_display_value(catalog, field_name, resolution.normalized_canonical)
    label = FIELD_REGISTRY[field_name].output_label if field_name in FIELD_REGISTRY else field_name
    if indexed_value is None:
        return _TaxonomyOutcome(
            decided=True,
            fields=(field_name,),
            ambiguity_flags=(f"taxonomy_known_but_not_indexed:{field_name}",),
            parser_warnings=(
                f"搜尋詞「{raw_value}」在 Search Taxonomy Authority 對應「{label}："
                f"{resolution.canonical_value}」，但目前正式索引沒有這個值，未改以廣泛語意搜尋。",
            ),
            abstain_reason=ABSTAIN_TAXONOMY_NOT_INDEXED,
        )

    # The constraint carries the value the index actually holds, not the Authority's display value:
    # the workbook keeps names such as ``"居家生活 "`` verbatim while ingestion strips them, and the
    # executor compares against indexed metadata.
    return _TaxonomyOutcome(
        decided=True,
        fields=(field_name,),
        constraint=_constraint(
            field_name,
            indexed_value,
            TAXONOMY_OPERATORS[field_name],
            source,
            raw_value=raw_value,
        ),
    )


def _scan_taxonomy_terms(
    query: str, catalog: QueryCatalog, taxonomy: "SearchTaxonomy"
) -> List[_TaxonomyOutcome]:
    """Read every Authority term stated in the remaining free text, longest term first.

    Longest-first with removal is what keeps ``美食相關`` from also registering the ``美食`` inside
    it: the winning term is taken out of the query before the next pass looks. Length is a property
    of the terms themselves, so this is not workbook row order deciding anything.
    """
    outcomes: List[_TaxonomyOutcome] = []
    remaining = query
    aliases = taxonomy.aliases_longest_first()
    for _ in range(TAXONOMY_SCAN_LIMIT):
        if not remaining:
            break
        alias = next(
            (
                item
                for item in aliases
                # The cheap containment test first, then the parser's own phrase rule, so an ASCII
                # term such as ``pet`` still needs a boundary and does not match inside ``carpet``.
                if item in remaining and _contains_exact_phrase(remaining, item)
            ),
            None,
        )
        if alias is None:
            break
        outcome = _taxonomy_outcome(
            taxonomy.resolve(alias), catalog, source=TAXONOMY_SOURCE, raw_value=alias
        )
        if outcome.decided:
            outcomes.append(replace(outcome, matched_text=alias))
        remaining = _remove_fragments(remaining, [alias])

    if not outcomes and _looks_like_lookup(query):
        suggestion = _taxonomy_suggestion(query, taxonomy)
        if suggestion is not None:
            outcomes.append(suggestion)
    return outcomes


def _taxonomy_suggestion(query: str, taxonomy: "SearchTaxonomy") -> Optional[_TaxonomyOutcome]:
    """Offer the nearest official terms for an unrecognised lookup, and nothing else.

    A suggestion decides no field, adds no constraint and sets no abstain reason. It cannot pick a
    side of an ambiguity either: a term one edit away from two canonical values is reported as two
    suggestions for a human to choose between.
    """
    for term in _unique([query, *re.split(r"\s+", query)]):
        suggestions = taxonomy.suggest_similar(term)
        if not suggestions:
            continue
        names = "、".join(
            f"{FIELD_REGISTRY[item.field].output_label}：{item.canonical_value}"
            if item.field in FIELD_REGISTRY
            else f"{item.field}：{item.canonical_value}"
            for item in suggestions
        )
        return _TaxonomyOutcome(
            decided=True,
            ambiguity_flags=(f"taxonomy_typo_suggestion:{term}",),
            parser_warnings=(
                f"搜尋詞「{term}」不在 Search Taxonomy Authority；最接近的正式詞彙為 {names}。"
                "未自動更正，也未建立搜尋條件。",
            ),
        )
    return None


def _catalog_display_value(
    catalog: QueryCatalog, field_name: Optional[str], normalized_canonical: Optional[str]
) -> Optional[str]:
    """The runtime catalog's own value for a canonical the Authority resolved, if the index has one."""
    values = {
        "sales_category_lv1": catalog.sales_category_lv1,
        "sales_category_lv2": catalog.sales_category_lv2,
        "content_tags": catalog.content_tags,
    }.get(field_name or "", [])
    for value in values:
        if normalize_exact_value(value) == normalized_canonical:
            return value
    return None


def _extend_unique(target: List[str], values: Iterable[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _first_catalog_match(query: str, values: Sequence[str], handle: bool = False) -> Optional[str]:
    for value in sorted((item for item in values if item), key=len, reverse=True):
        normalized = normalize_exact_value(value, handle=handle)
        if not normalized:
            continue
        if handle:
            if re.search(rf"(?<![a-z0-9])@?{re.escape(normalized)}(?![a-z0-9])", query):
                return value
        elif _contains_exact_phrase(query, normalized):
            return value
    return None


def _exact_catalog_value(query: str, values: Sequence[str]) -> Optional[str]:
    for value in values:
        if normalize_exact_value(value) == normalize_exact_value(query):
            return value
    return None


def _contains_exact_phrase(query: str, phrase: str) -> bool:
    normalized_phrase = normalize_exact_value(phrase)
    if not normalized_phrase:
        return False
    if normalized_phrase.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])", query) is not None
    return normalized_phrase in query


def _remaining_terms(query: str, matched_fragments: Sequence[str]) -> List[str]:
    remaining = query
    for fragment in sorted((item for item in matched_fragments if item), key=len, reverse=True):
        remaining = re.sub(re.escape(normalize_query_text(fragment)), " ", remaining, flags=re.IGNORECASE)
    boilerplate = (
        "提供我", "請提供", "請整理", "整理", "我們有什麼", "有什麼", "有哪些", "的內容",
        "品牌相關內容", "品牌", "相關內容", "內容", "採訪的", "採訪", "年", "目前", "請", "我", "的",
    )
    for phrase in boilerplate:
        remaining = remaining.replace(phrase, " ")
    remaining = re.sub(r"[+＋,，。！？?()（）《》「」『』:：]", " ", remaining)
    return [term for term in re.split(r"\s+", remaining.strip()) if term]


def _remove_fragments(query: str, fragments: Sequence[str]) -> str:
    remaining = query
    for fragment in sorted((item for item in fragments if item), key=len, reverse=True):
        remaining = re.sub(re.escape(normalize_query_text(fragment)), " ", remaining, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", remaining).strip()


def _looks_like_lookup(query: str) -> bool:
    if not query:
        return True
    semantic_markers = ("如何", "為什麼", "原因", "策略", "比較", "分析", "摘要", "共同", "提升", "成效")
    if any(marker in query for marker in semantic_markers):
        return False
    explicit_lookup_markers = ("提供我", "請提供", "找出", "查詢", "品牌相關內容", "品牌的內容")
    if any(marker in query for marker in explicit_lookup_markers):
        return True
    bare_query = re.sub(r"[?？!！。,.，:：()（）《》「」『』]", "", query).strip()
    return bool(re.fullmatch(r"[\u3400-\u9fff]{2,20}", bare_query))


def allow_semantic_fallback(plan: TypedQueryPlan) -> TypedQueryPlan:
    """Keep explicit caller filters useful without weakening resolved hard constraints."""
    if plan.execution_blocked and plan.effective_abstain_reason != "unresolved_structured_lookup":
        return plan
    if plan.effective_abstain_reason != "unresolved_structured_lookup":
        return plan
    return replace(
        plan,
        query_mode="semantic_question",
        abstain_reason=None,
    )


def _dedupe_constraints(values: Sequence[QueryConstraint]) -> List[QueryConstraint]:
    deduped: List[QueryConstraint] = []
    seen = set()
    for value in values:
        key = (value.field, value.operator, repr(value.normalized_value))
        if key not in seen:
            seen.add(key)
            deduped.append(value)
    return deduped


def _unique(values: Iterable[Optional[str]]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        normalized = normalize_exact_value(text)
        if text and normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return result
