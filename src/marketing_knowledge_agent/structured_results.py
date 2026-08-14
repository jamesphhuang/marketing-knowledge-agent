from __future__ import annotations

from collections import OrderedDict
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from .generation import citation_for_result
from .governance import (
    GovernanceIndex,
    metadata_allows_written_external_use,
    metadata_governance_warnings,
)
from .models import (
    Citation,
    GeneratedAnswer,
    NON_PUBLIC_STATUSES,
    SearchResult,
    StructuredAsset,
    StructuredEntity,
    StructuredRetrievalResult,
)
from .query_planning import FIELD_REGISTRY, TypedQueryPlan


ASSET_FIELDS = (
    ("article", "article_title", "文章"),
    ("video", "video_title", "影片"),
    ("podcast", "podcast_title", "Podcast"),
    ("news", "news_title", "新聞"),
)
DEFAULT_PARENT_CAP = 5
DEFAULT_ASSET_CAP = 10


def generate_structured_answer(
    question: str,
    results: Iterable[SearchResult],
    query_plan: TypedQueryPlan,
    *,
    today: date = None,
    governance_index: Optional[GovernanceIndex] = None,
    parent_cap: int = DEFAULT_PARENT_CAP,
    asset_cap: int = DEFAULT_ASSET_CAP,
) -> GeneratedAnswer:
    unique_results = [] if query_plan.execution_blocked else _unique_document_results(results)
    entities, citations, warnings, removed_asset_count = _aggregate_entities(
        unique_results,
        query_plan,
        today=today,
        governance_index=governance_index,
        parent_cap=parent_cap,
        asset_cap=asset_cap,
    )
    if removed_asset_count:
        warnings.append(f"已依 restricted denylist 移除 {removed_asset_count} 筆結構化資產")
    total_assets = sum(len(entity.assets) for entity in entities)
    abstained = not total_assets
    abstain_reason = query_plan.effective_abstain_reason or ("no_constraint_intersection" if abstained else None)
    plan_dict = query_plan.to_dict()
    structured = StructuredRetrievalResult(
        query_plan=plan_dict,
        matched_entities=entities,
        total_entities=len(entities),
        total_assets=total_assets,
        warnings=warnings,
        supported_constraints=plan_dict["supported_constraints"],
        unsupported_constraints=plan_dict["unsupported_constraints"],
        ambiguous_constraints=plan_dict["ambiguous_constraints"],
        invalid_constraints=plan_dict["invalid_constraints"],
        execution_blocked=query_plan.execution_blocked,
        abstained=abstained,
        abstain_reason=abstain_reason,
    )
    answer_text = render_structured_result(structured)
    return GeneratedAnswer(
        question=question,
        answer=answer_text,
        citations=citations,
        warnings=warnings,
        query_plan=plan_dict,
        structured_result=structured,
    )


def render_structured_result(result: StructuredRetrievalResult) -> str:
    plan = result.query_plan
    constraints = plan.get("hard_filters", [])
    if result.abstained:
        if result.unsupported_constraints:
            labels = _constraint_labels(result.unsupported_constraints)
            return (
                "目前資料尚不支援以下搜尋條件：\n"
                + "\n".join(f"- {label}" for label in labels)
                + "\n\n目前可使用品牌名稱、Handle、Sales Category、採訪年份、內容標籤與內容類型進行搜尋。"
            )
        if result.invalid_constraints:
            labels = _constraint_labels(result.invalid_constraints)
            return "以下搜尋條件格式無效，因此未執行搜尋：\n" + "\n".join(
                f"- {label}" for label in labels
            )
        if plan.get("abstain_reason") in {"unresolved_structured_lookup", "ambiguous_entity_type"}:
            return (
                "找不到可精確辨識的名稱、Handle、年份、狀態、分類、標籤或內容類型。"
                "系統未以相似內容替代正式結果。"
            )
        condition_text = "、".join(_constraint_summary(item) for item in constraints)
        return (
            f"找不到同時符合「{condition_text}」且可供目前管道使用的資料。"
            "請確認名稱、Handle、年份、狀態或分類是否正確。"
        )

    lines = ["已套用搜尋條件：", ""]
    lines.extend(f"- {_constraint_summary(item)}" for item in constraints)
    lines.extend(
        [
            "",
            f"共找到 {result.total_entities} 個品牌／夥伴、{result.total_assets} 筆內容。",
            "",
        ]
    )
    for entity in result.matched_entities:
        lines.append(entity.entity_name)
        if entity.merchant_handle:
            lines.append(f"Handle：{entity.merchant_handle}")
        if entity.sales_category_lv1:
            lines.append(f"Sales Category LV1：{entity.sales_category_lv1}")
        if entity.sales_category_lv2:
            lines.append(f"Sales Category LV2：{entity.sales_category_lv2}")
        for asset in entity.assets:
            lines.extend(
                [
                    "",
                    f"• {_asset_label(asset.asset_type)} {asset.citation_label}",
                    f"  標題：{asset.title}",
                    f"  連結：{asset.url or '資料未提供'}",
                    f"  上線日期：{asset.published_at or '資料未提供'}",
                    f"  採訪年份：{entity.interview_year if entity.interview_year is not None else '資料未提供'}",
                    f"  狀態：{_status_label(asset.publication_status)}",
                    f"  對外引用：{asset.external_usage_status}",
                    f"  資料來源：{asset.source_sheet or '資料未提供'} r{asset.source_row if asset.source_row is not None else '?'}",
                ]
            )
        lines.append("")
    return "\n".join(lines).strip()


def _aggregate_entities(
    results: List[SearchResult],
    query_plan: TypedQueryPlan,
    *,
    today: date = None,
    governance_index: Optional[GovernanceIndex] = None,
    parent_cap: int = DEFAULT_PARENT_CAP,
    asset_cap: int = DEFAULT_ASSET_CAP,
) -> Tuple[List[StructuredEntity], List[Citation], List[str], int]:
    grouped: "OrderedDict[Tuple[str, str], dict]" = OrderedDict()
    citations: List[Citation] = []
    warnings: List[str] = []
    citation_index = 1
    removed_asset_count = 0
    asset_identities = set()

    for result in results:
        if len(citations) >= asset_cap:
            break
        metadata = result.chunk.metadata
        entity_type = "merchant" if metadata.record_type == "merchant_case" else "unknown"
        entity_name = metadata.brand_name or metadata.metric_name or metadata.title
        source_record_id = _source_record_id(
            metadata.source_sheet,
            metadata.source_row,
            result.chunk.document_id,
        )
        key = (entity_type, source_record_id)

        for asset_type, title in _assets_for_result(result, query_plan):
            if len(citations) >= asset_cap:
                break
            if governance_index is not None and governance_index.check_text(title).blocked:
                removed_asset_count += 1
                continue
            asset_identity = (source_record_id, asset_type)
            if asset_identity in asset_identities:
                continue
            if key not in grouped and len(grouped) >= parent_cap:
                break
            state = grouped.setdefault(
                key,
                {
                    "entity_type": entity_type,
                    "entity_name": entity_name,
                    "merchant_handle": metadata.merchant_handle,
                    "sales_category_lv1": metadata.sales_category_lv1,
                    "sales_category_lv2": metadata.sales_category_lv2,
                    "interview_years": [],
                    "assets": [],
                },
            )
            if (
                metadata.interview_year is not None
                and metadata.interview_year not in state["interview_years"]
            ):
                state["interview_years"].append(metadata.interview_year)
            label = f"[{citation_index}]"
            citation_index += 1
            asset = StructuredAsset(
                asset_type=asset_type,
                title=title,
                url=(
                    None
                    if metadata.record_type == "merchant_case"
                    else metadata.canonical_url
                ),
                published_at=(
                    metadata.publish_date.isoformat()
                    if metadata.record_type == "content_asset" and metadata.publish_date
                    else None
                ),
                publication_status=None,
                external_usage_status=_external_usage_label(metadata),
                source_record_id=source_record_id,
                source_sheet=metadata.source_sheet,
                source_row=metadata.source_row,
                citation_label=label,
            )
            state["assets"].append(asset)
            asset_identities.add(asset_identity)
            citation = citation_for_result(
                result,
                label,
                title=title,
                chunk_id=f"{result.chunk.id}:{asset_type}",
                retrieval_reason=_retrieval_reason(query_plan),
                today=today,
            )
            if metadata.record_type == "merchant_case":
                citation.canonical_url = None
            citation_warnings = []
            if metadata.status in NON_PUBLIC_STATUSES:
                citation_warnings.append(f"{label} {title} 的 status={metadata.status}，不可直接對外引用。")
            citation_warnings.extend(metadata_governance_warnings(metadata, label))
            citation.warnings = _dedupe(citation_warnings)
            citations.append(citation)
            warnings.extend(citation.warnings)

    entities = []
    for state in grouped.values():
        if not state["assets"]:
            continue
        years = state.pop("interview_years")
        entities.append(
            StructuredEntity(
                **state,
                interview_year=years[0] if len(years) == 1 else None,
            )
        )
    return entities, citations, _dedupe(warnings), removed_asset_count


def _assets_for_result(result: SearchResult, query_plan: TypedQueryPlan) -> List[Tuple[str, str]]:
    metadata = result.chunk.metadata
    requested = set(query_plan.requested_asset_types)
    assets = []
    if metadata.record_type == "merchant_case":
        for asset_type, field_name, _ in ASSET_FIELDS:
            title = getattr(metadata, field_name)
            if title and (not requested or asset_type in requested):
                assets.append((asset_type, title))
        return assets

    asset_type = metadata.asset_type or ("other" if metadata.record_type == "public_metric" else "article")
    if requested and asset_type not in requested:
        return []
    return [(asset_type, metadata.metric_name or metadata.title)]


def _constraint_summary(constraint: Dict[str, object]) -> str:
    field_name = str(constraint.get("field") or "")
    label = FIELD_REGISTRY.get(field_name).output_label if field_name in FIELD_REGISTRY else field_name
    value = constraint.get("value")
    if constraint.get("operator") == "range" and isinstance(value, list) and len(value) == 2:
        display_value = f"{value[0]}～{value[1]}"
    elif field_name == "asset_type":
        display_value = _asset_label(str(value))
    elif field_name == "publication_status":
        display_value = _status_label(str(value))
    elif field_name == "external_usage_status":
        display_value = "可對外引用" if value else "不可對外引用"
    else:
        display_value = str(value)
    return f"{label}：{display_value}"


def _constraint_labels(constraints: Iterable[Dict[str, object]]) -> List[str]:
    labels: List[str] = []
    for constraint in constraints:
        field_name = str(constraint.get("field") or "")
        definition = FIELD_REGISTRY.get(field_name)
        label = definition.output_label if definition else field_name
        if label and label not in labels:
            labels.append(label)
    return labels


def _status_label(value: Optional[str]) -> str:
    return {
        "published": "已上線",
        "draft": "草稿",
        "archived": "已封存",
        "deprecated": "已棄用",
    }.get(value or "", value or "資料未提供")


def _asset_label(value: str) -> str:
    return dict((asset_type, label) for asset_type, _, label in ASSET_FIELDS).get(value, "其他")


def _external_usage_label(metadata) -> str:
    if metadata_allows_written_external_use(metadata):
        return "可對外引用"
    return "不可直接對外引用"


def _source_record_id(source_sheet: Optional[str], source_row: Optional[int], document_id: str) -> str:
    if source_sheet and source_row is not None:
        return f"{source_sheet}:r{source_row}"
    return document_id


def _retrieval_reason(query_plan: TypedQueryPlan) -> str:
    fields = [constraint.field for constraint in query_plan.hard_constraints]
    return "hard_constraints:" + ",".join(fields) if fields else "semantic_ranking"


def _unique_document_results(results: Iterable[SearchResult]) -> List[SearchResult]:
    unique: Dict[str, SearchResult] = OrderedDict()
    for result in results:
        unique.setdefault(result.chunk.document_id, result)
    return list(unique.values())


def _dedupe(values: List[str]) -> List[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
