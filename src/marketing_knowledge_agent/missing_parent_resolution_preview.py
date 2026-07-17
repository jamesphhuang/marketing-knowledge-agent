from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .asset_metadata_preview import read_sqlite_metadata
from .governance import GovernanceIndex, RestrictedCustomerRecord, split_restricted_aliases
from .models import SearchFilters
from .pipeline import ask_index


REVIEWER = "James Huang"
OLD_PLAN_ID = "asset-plan-07cd12338615c961"
SOURCE_DISPLAY_NAME = "MKT 內容產出資料庫_店家 / 夥伴案例 / 對外數據"
SEARCH_QUERIES = (
    "莉朵花藝",
    "littlegirl",
    "廣生堂",
    "111gsttest",
    "Package+",
    "SLP",
    "SHOPLINE Payments",
    "聊心茶室",
    "關貿網路",
)
RESOLUTION_OUTPUT_FILENAMES = (
    "missing_parent_resolution_summary.md",
    "parent_decision_preview.csv",
    "asset_eligibility_preview.csv",
    "partner_handle_rule_preview.md",
    "search_alias_preview.csv",
    "search_behavior_preview.md",
    "asset_apply_count_reconciliation.md",
    "excluded_and_held_assets.csv",
    "parent_sync_readiness.csv",
    "resolution_confirmation_checklist.md",
    "missing_parent_resolution_decisions.csv",
)


class MissingParentResolutionPreviewError(ValueError):
    """Raised when the human resolution cannot be previewed without mutation."""


@dataclass(frozen=True)
class ParentPolicy:
    proposed_review_decision: str
    proposed_can_enter_vault: bool
    proposed_can_enter_content_index: bool
    proposed_can_external_reference: bool
    reason: str
    search_aliases: Tuple[str, ...] = ()
    lookup_names: Tuple[str, ...] = ()


PARENT_POLICIES = {
    30: ParentPolicy(
        proposed_review_decision="exclude",
        proposed_can_enter_vault=False,
        proposed_can_enter_content_index=False,
        proposed_can_external_reference=False,
        reason="Human confirmed the content is taken down; parent and child assets remain excluded regardless of URL reachability.",
    ),
    12: ParentPolicy(
        proposed_review_decision="approve_internal_only",
        proposed_can_enter_vault=True,
        proposed_can_enter_content_index=True,
        proposed_can_external_reference=False,
        reason="Human confirmed the article is searchable internally while the reviewing video remains held.",
    ),
    122: ParentPolicy(
        proposed_review_decision="approve",
        proposed_can_enter_vault=True,
        proposed_can_enter_content_index=True,
        proposed_can_external_reference=True,
        reason="Human confirmed this is a partner; a Merchant Handle is not required and all three valid assets are included.",
    ),
    32: ParentPolicy(
        proposed_review_decision="approve",
        proposed_can_enter_vault=True,
        proposed_can_enter_content_index=True,
        proposed_can_external_reference=True,
        reason="Human approved the parent, all three assets, and two exact source-record aliases.",
        search_aliases=("SLP", "SHOPLINE Payments"),
        lookup_names=("聊心茶室",),
    ),
    7: ParentPolicy(
        proposed_review_decision="approve",
        proposed_can_enter_vault=True,
        proposed_can_enter_content_index=True,
        proposed_can_external_reference=True,
        reason="Human confirmed this is a partner; a Merchant Handle is not required and the article is included.",
    ),
}


@dataclass
class ResolutionContext:
    parent_decisions: List[dict]
    asset_decisions: List[dict]
    parents_by_id: Dict[str, dict]
    assets_by_record: Dict[str, List[dict]]
    aliases: Dict[str, str]
    identity_terms: Dict[str, str]
    restricted_index: GovernanceIndex
    counts: dict
    final_eligible_asset_ids: Set[str] = field(default_factory=set)
    hold_asset_ids: Set[str] = field(default_factory=set)
    excluded_asset_ids: Set[str] = field(default_factory=set)


def build_resolution_context(
    *,
    parent_records_path: Path,
    review_decisions_path: Path,
    inventory_path: Path,
    apply_preview_path: Path,
    blocked_preview_path: Path,
    restricted_customers_path: Path,
    reviewed_at: str,
) -> ResolutionContext:
    _validate_reviewed_at(reviewed_at)
    parent_rows = _read_json_list(Path(parent_records_path))
    review_rows = _read_csv(Path(review_decisions_path))
    inventory_rows = _read_csv(Path(inventory_path))
    apply_rows = _read_csv(Path(apply_preview_path))
    blocked_rows = _read_csv(Path(blocked_preview_path))
    restricted_index = _restricted_index(Path(restricted_customers_path))

    parents_by_id = _target_parent_index(parent_rows)
    reviews_by_id = _review_index(review_rows)
    inventory_by_asset = _unique_index(inventory_rows, "asset_id", "asset inventory")
    apply_by_key = _unique_apply_index(apply_rows)
    apply_asset_ids = {key[0] for key in apply_by_key}
    blocked_asset_ids = {
        _text(row.get("asset_id"))
        for row in blocked_rows
        if _text(row.get("action")) == "blocked" and _text(row.get("asset_id"))
    }
    all_asset_ids = set(inventory_by_asset)
    if apply_asset_ids & blocked_asset_ids:
        raise MissingParentResolutionPreviewError(
            "asset cannot be both Apply-eligible and governance-blocked"
        )
    if apply_asset_ids | blocked_asset_ids != all_asset_ids:
        raise MissingParentResolutionPreviewError(
            "asset inventory does not conserve Apply-eligible and blocked identities"
        )

    parent_decisions: List[dict] = []
    aliases: Dict[str, str] = {}
    identity_terms: Dict[str, str] = {}
    for record_id, parent in sorted(parents_by_id.items(), key=_record_sort_key):
        source_row = int(parent["source_row"])
        policy = PARENT_POLICIES[source_row]
        review = reviews_by_id.get(record_id)
        if review is None:
            raise MissingParentResolutionPreviewError(
                f"current parent review row is missing: {record_id}"
            )
        entity_type = _entity_type(parent)
        handle = _text(parent.get("merchant_handle"))
        handle_requirement = "not_required" if entity_type == "partner" else "required_by_existing_rules"
        if entity_type == "partner" and handle:
            handle_requirement = "optional_present"
        row = {
            "record_id": record_id,
            "brand_name": _text(parent.get("brand_name")),
            "merchant_handle": handle,
            "merchant_status": _text(parent.get("merchant_status")),
            "entity_type": entity_type,
            "entity_type_source": "merchant_status",
            "merchant_handle_requirement": handle_requirement,
            "current_review_decision": _text(review.get("review_decision")),
            "proposed_review_decision": policy.proposed_review_decision,
            "current_can_enter_vault": _bool_text(review.get("can_enter_vault")),
            "proposed_can_enter_vault": _display_bool(policy.proposed_can_enter_vault),
            "current_can_enter_content_index": _bool_text(review.get("can_enter_content_index")),
            "proposed_can_enter_content_index": _display_bool(policy.proposed_can_enter_content_index),
            "current_can_external_reference": _bool_text(review.get("can_quote_externally")),
            "proposed_can_external_reference": _display_bool(policy.proposed_can_external_reference),
            "proposed_search_aliases": _stable_list(policy.search_aliases),
            "reason": policy.reason,
            "reviewer": REVIEWER,
            "reviewed_at": reviewed_at,
        }
        parent_decisions.append(row)
        brand_term = _normalize_exact(row["brand_name"])
        if brand_term:
            identity_terms[brand_term] = record_id
        if handle:
            identity_terms[_normalize_exact(handle)] = record_id
        for lookup_name in policy.lookup_names:
            identity_terms[_normalize_exact(lookup_name)] = record_id
        for alias in policy.search_aliases:
            normalized_alias = _normalize_exact(alias)
            if normalized_alias in aliases and aliases[normalized_alias] != record_id:
                raise MissingParentResolutionPreviewError("approved alias maps to multiple parents")
            aliases[normalized_alias] = record_id

    target_record_ids = set(parents_by_id)
    target_inventory = [
        row for row in inventory_rows if _text(row.get("record_id")) in target_record_ids
    ]
    asset_decisions: List[dict] = []
    target_include: Set[str] = set()
    target_hold: Set[str] = set()
    target_exclude: Set[str] = set()
    assets_by_record: Dict[str, List[dict]] = {record_id: [] for record_id in target_record_ids}
    for inventory in sorted(target_inventory, key=_asset_sort_key):
        record_id = _text(inventory.get("record_id"))
        asset_id = _text(inventory.get("asset_id"))
        parent = parents_by_id[record_id]
        policy = PARENT_POLICIES[int(parent["source_row"])]
        asset_type = _text(inventory.get("asset_type"))
        eligibility, search_eligibility, reason = _asset_policy(record_id, asset_type)
        if eligibility == "include":
            target_include.add(asset_id)
        elif eligibility == "hold":
            target_hold.add(asset_id)
        else:
            target_exclude.add(asset_id)
        url = _apply_value(apply_by_key, asset_id, "asset_url")
        canonical_url = _apply_value(apply_by_key, asset_id, "canonical_url")
        current_eligibility = (
            "ready_for_apply_preview"
            if asset_id in apply_asset_ids
            else "governance_blocked"
            if asset_id in blocked_asset_ids
            else "not_in_apply_preview"
        )
        if eligibility == "include" and asset_id not in apply_asset_ids:
            raise MissingParentResolutionPreviewError(
                f"human-included asset has no approved URL Apply identity: {asset_id}"
            )
        tags = _string_list(parent.get("content_tags")) if eligibility == "include" else []
        asset_row = {
            "record_id": record_id,
            "asset_id": asset_id,
            "brand_name": _text(parent.get("brand_name")),
            "asset_type": asset_type,
            "asset_title": _text(inventory.get("asset_title")),
            "current_asset_eligibility": current_eligibility,
            "proposed_asset_index_eligibility": eligibility,
            "proposed_asset_search_eligibility": search_eligibility,
            "eligibility_reason": reason,
            "asset_url": url,
            "canonical_url": canonical_url,
            "will_enter_asset_apply_manifest": _display_bool(eligibility == "include"),
            "resolved_content_tags": json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
            "content_tags_source": "parent_source_record" if tags else "not_resolved",
            "reviewer": REVIEWER,
            "reviewed_at": reviewed_at,
        }
        asset_decisions.append(asset_row)
        assets_by_record[record_id].append(asset_row)

    expected_target_assets = {
        _text(row.get("asset_id")) for row in target_inventory if _text(row.get("asset_id"))
    }
    if target_include | target_hold | target_exclude != expected_target_assets:
        raise MissingParentResolutionPreviewError("target asset eligibility is not conserved")
    if target_include & target_hold or target_include & target_exclude or target_hold & target_exclude:
        raise MissingParentResolutionPreviewError("target asset eligibility buckets overlap")

    final_eligible = (apply_asset_ids - target_exclude - target_hold) | target_include
    final_hold = set(target_hold)
    final_excluded = all_asset_ids - final_eligible - final_hold
    if final_eligible | final_hold | final_excluded != all_asset_ids:
        raise MissingParentResolutionPreviewError("final asset eligibility is not conserved")
    final_apply_rows = [row for row in apply_rows if _text(row.get("asset_id")) in final_eligible]
    if len(final_apply_rows) != len(final_eligible) * 2:
        raise MissingParentResolutionPreviewError(
            "every final eligible asset must retain exactly two approved URL fields"
        )
    counts = {
        "parent_count": len(parent_decisions),
        "inventory_asset_count": len(all_asset_ids),
        "original_eligible_asset_count": len(apply_asset_ids),
        "original_approved_url_field_count": len(apply_rows),
        "original_governance_blocked_asset_count": len(blocked_asset_ids),
        "resolution_include_asset_count": len(target_include),
        "resolution_hold_asset_count": len(target_hold),
        "resolution_excluded_asset_count": len(target_exclude),
        "final_eligible_asset_count": len(final_eligible),
        "final_hold_asset_count": len(final_hold),
        "final_excluded_asset_count": len(final_excluded),
        "final_approved_url_field_count": len(final_apply_rows),
        "identity_added_count": len(final_eligible - all_asset_ids),
        "identity_lost_count": len(all_asset_ids - (final_eligible | final_hold | final_excluded)),
    }
    return ResolutionContext(
        parent_decisions=parent_decisions,
        asset_decisions=asset_decisions,
        parents_by_id=parents_by_id,
        assets_by_record=assets_by_record,
        aliases=aliases,
        identity_terms=identity_terms,
        restricted_index=restricted_index,
        counts=counts,
        final_eligible_asset_ids=final_eligible,
        hold_asset_ids=final_hold,
        excluded_asset_ids=final_excluded,
    )


def preview_resolution_search(
    query: str,
    context: ResolutionContext,
    formal_entities: Sequence[dict] = (),
) -> dict:
    normalized = _normalize_exact(query)
    alias_record_id = context.aliases.get(normalized)
    identity_record_id = context.identity_terms.get(normalized)
    selected_record_id = alias_record_id or identity_record_id
    include_formal = normalized == _normalize_exact("SHOPLINE Payments")
    entities: List[dict] = [dict(row) for row in formal_entities] if include_formal else []
    abstain_reason = "no_exact_match"
    resolution_kind = "formal_only" if entities else "none"

    if selected_record_id:
        parent_decision = _row_by_id(context.parent_decisions, selected_record_id)
        if parent_decision["proposed_review_decision"] == "exclude":
            entities = []
            abstain_reason = "excluded_parent"
            resolution_kind = "excluded_identity"
        elif _parent_restricted(selected_record_id, context):
            entities = []
            abstain_reason = "governance_blocked"
            resolution_kind = "governance_blocked"
        else:
            overlay_entity = _preview_entity(selected_record_id, context)
            if overlay_entity["assets"]:
                entities = _merge_entities([overlay_entity], entities)
                abstain_reason = ""
                resolution_kind = "exact_alias" if alias_record_id else "exact_identity"
    entities = _filter_resolution_eligibility(entities, context)
    citations = []
    for entity in entities:
        for asset in entity.get("assets") or []:
            citations.append(
                {
                    "title": asset["title"],
                    "source": SOURCE_DISPLAY_NAME,
                    "can_quote_externally": asset["can_quote_externally"],
                }
            )
    total_assets = sum(len(entity.get("assets") or []) for entity in entities)
    if not total_assets and not abstain_reason:
        abstain_reason = "no_eligible_asset"
    return {
        "query": query,
        "normalized_query": normalized,
        "resolution_kind": resolution_kind,
        "entities": entities,
        "total_entities": len(entities),
        "total_assets": total_assets,
        "citations": citations,
        "abstained": total_assets == 0,
        "abstain_reason": abstain_reason if total_assets == 0 else "",
    }


def render_resolution_standard(result: Mapping[str, object]) -> str:
    query = _text(result.get("query"))
    if result.get("abstained"):
        if result.get("abstain_reason") == "excluded_parent":
            return f"目前找不到與「{_safe_slack_text(query)}」精確匹配且可供搜尋的內容。"
        if result.get("abstain_reason") == "governance_blocked":
            return "目前找不到符合條件且可供使用的內容。"
        return f"目前找不到與「{_safe_slack_text(query)}」精確匹配的內容。"
    all_entities = result.get("entities") or []
    entities, displayed_assets = _display_resolution_subset(all_entities)
    total_assets = int(result.get("total_assets") or 0)
    if len(all_entities) == 1:
        entity = entities[0]
        if _normalize_exact(query) == _normalize_exact(entity.get("merchant_handle")):
            header = (
                f"找到 Handle「{_safe_slack_text(query)}」對應品牌："
                f"{_safe_slack_text(entity.get('entity_name'))}\n共 {total_assets} 筆內容："
            )
        else:
            header = f"找到「{_safe_slack_text(query)}」相關內容，共 {total_assets} 筆："
    else:
        header = f"共找到 {len(all_entities)} 個品牌／夥伴、{total_assets} 筆內容。"
        if len(all_entities) > len(entities) or total_assets > displayed_assets:
            header += f"目前顯示 {len(entities)} 個品牌、{displayed_assets} 筆內容。"
    lines = [header]
    number = 1
    multiple = len(entities) > 1
    for entity in entities:
        if multiple:
            lines.extend(["", _safe_slack_text(entity.get("entity_name"))])
        for asset in entity.get("assets") or []:
            title = _safe_slack_text(asset.get("title")) or "資料未提供"
            url = _text(asset.get("asset_url"))
            display_title = f"<{url}|{title}>" if _safe_url(url) else title
            lines.extend(["", f"{number}. {_asset_label(asset.get('asset_type'))}", f"標題：{display_title}"])
            tags = [str(tag) for tag in asset.get("content_tags") or [] if str(tag).strip()]
            if tags:
                lines.append("內容相關標籤：" + "、".join(_safe_slack_text(tag) for tag in tags))
            lines.append(f"對外引用：{_safe_slack_text(asset.get('external_usage'))}")
            number += 1
    lines.extend(["", f"資料來源：{SOURCE_DISPLAY_NAME}"])
    if len(all_entities) > len(entities) or total_assets > displayed_assets:
        lines.append("")
        if len(all_entities) > len(entities):
            lines.append(f"目前只顯示前 {len(entities)} 個品牌。")
        if total_assets > displayed_assets:
            lines.append(f"目前只顯示前 {displayed_assets} 筆內容。")
        lines.append("請加入品牌名稱、Handle、Category、Tag 或內容類型縮小搜尋；完整結果集合未改變。")
    return "\n".join(lines)


def generate_missing_parent_resolution_preview(
    *,
    parent_records_path: Path,
    review_decisions_path: Path,
    inventory_path: Path,
    apply_preview_path: Path,
    blocked_preview_path: Path,
    restricted_customers_path: Path,
    vault_path: Path,
    db_path: Path,
    production_slack_renderer_path: Path,
    output_dir: Path,
    reviewed_at: Optional[str] = None,
    formal_search_fn: Optional[Callable[[str], Sequence[dict]]] = None,
) -> dict:
    reviewed_at = reviewed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    paths = {
        "parent_records": Path(parent_records_path),
        "review_decisions": Path(review_decisions_path),
        "inventory": Path(inventory_path),
        "apply_preview": Path(apply_preview_path),
        "blocked_preview": Path(blocked_preview_path),
        "restricted_customers": Path(restricted_customers_path),
        "formal_vault": Path(vault_path),
        "formal_sqlite": Path(db_path),
        "production_slack_renderer": Path(production_slack_renderer_path),
    }
    output_dir = Path(output_dir)
    _assert_safe_output(output_dir, list(paths.values()))
    for name, path in paths.items():
        if not path.exists():
            raise MissingParentResolutionPreviewError(f"required preview input is missing: {name}")
    before_hashes = {name: _hash_path(path) for name, path in paths.items()}
    context = build_resolution_context(
        parent_records_path=paths["parent_records"],
        review_decisions_path=paths["review_decisions"],
        inventory_path=paths["inventory"],
        apply_preview_path=paths["apply_preview"],
        blocked_preview_path=paths["blocked_preview"],
        restricted_customers_path=paths["restricted_customers"],
        reviewed_at=reviewed_at,
    )
    if formal_search_fn is None:
        formal_search_fn = _formal_search_factory(
            context,
            paths["formal_sqlite"],
            paths["restricted_customers"],
            paths["apply_preview"],
        )
    search_results = []
    for query in SEARCH_QUERIES:
        formal_entities = list(formal_search_fn(query))
        search_results.append(
            preview_resolution_search(query, context, formal_entities=formal_entities)
        )
    summary = {
        "conclusion": "A. Ready to apply parent decisions",
        "preview_only": True,
        **context.counts,
        "search_query_count": len(search_results),
        "search_abstained_count": sum(row["abstained"] for row in search_results),
        "old_plan_id": OLD_PLAN_ID,
        "old_plan_status": "DO NOT CONFIRM",
        "decisions_applied": False,
        "parent_synced": False,
        "sync_executed": False,
        "confirm_executed": False,
        "execute_executed": False,
        "formal_apply_plan_generated": False,
        "formal_vault_modified": False,
        "formal_sqlite_modified": False,
        "production_slack_renderer_modified": False,
        "review_decisions_modified": False,
        "publication_or_review_query_enabled": False,
    }
    _write_reports(output_dir, summary, context, search_results)
    after_hashes = {name: _hash_path(path) for name, path in paths.items()}
    if after_hashes != before_hashes:
        raise MissingParentResolutionPreviewError(
            "a protected source changed during resolution preview generation"
        )
    return summary


def build_resolution_formal_search(
    context: ResolutionContext,
    db_path: Path,
    restricted_customers_path: Path,
    apply_preview_path: Path,
) -> Callable[[str], Sequence[dict]]:
    """Build the existing read-only formal-search adapter for resolution previews."""
    return _formal_search_factory(
        context,
        Path(db_path),
        Path(restricted_customers_path),
        Path(apply_preview_path),
    )


def _formal_search_factory(
    context: ResolutionContext,
    db_path: Path,
    restricted_customers_path: Path,
    apply_preview_path: Path,
) -> Callable[[str], Sequence[dict]]:
    apply_rows = _read_csv(apply_preview_path)
    apply_values = {
        (_text(row.get("asset_id")), _text(row.get("field"))): _text(row.get("proposed_value"))
        for row in apply_rows
    }
    sqlite_tags: Dict[str, List[str]] = {}
    for (source_sheet, source_row), records in read_sqlite_metadata(db_path).items():
        if len(records) == 1:
            sqlite_tags[f"{source_sheet}:r{source_row}"] = _string_list(
                records[0].get("content_tags")
            )

    def search(query: str) -> Sequence[dict]:
        with TemporaryDirectory(prefix="mka-parent-resolution-audit-") as temp_dir:
            answer = ask_index(
                query,
                db_path,
                filters=SearchFilters(intent="external"),
                limit=250,
                restricted_customers_path=restricted_customers_path,
                provider_name="mock",
                audit_log_path=Path(temp_dir) / "query_audit.csv",
            )
        structured = answer.structured_result
        if structured is None or structured.abstained:
            return []
        citation_by_label = {citation.label: citation for citation in answer.citations}
        entities = []
        for entity in structured.matched_entities:
            assets = []
            for asset in entity.assets:
                asset_id = f"{asset.source_record_id}:{asset.asset_type}"
                citation = citation_by_label.get(asset.citation_label)
                if citation is None or not citation.can_quote_externally:
                    continue
                parent = context.parents_by_id.get(asset.source_record_id, {})
                tags = _string_list(parent.get("content_tags")) or sqlite_tags.get(
                    asset.source_record_id, []
                )
                assets.append(
                    {
                        "record_id": asset.source_record_id,
                        "asset_id": asset_id,
                        "asset_type": asset.asset_type,
                        "title": asset.title,
                        "asset_url": apply_values.get((asset_id, "asset_url"), ""),
                        "canonical_url": apply_values.get((asset_id, "canonical_url"), ""),
                        "content_tags": tags,
                        "external_usage": asset.external_usage_status,
                        "can_quote_externally": citation.can_quote_externally,
                    }
                )
            if assets:
                entities.append(
                    {
                        "entity_type": entity.entity_type,
                        "entity_name": entity.entity_name,
                        "merchant_handle": entity.merchant_handle or "",
                        "sales_category_lv1": entity.sales_category_lv1 or "",
                        "sales_category_lv2": entity.sales_category_lv2 or "",
                        "content_tags": [],
                        "assets": assets,
                    }
                )
        return entities

    return search


def _preview_entity(record_id: str, context: ResolutionContext) -> dict:
    parent = context.parents_by_id[record_id]
    parent_decision = _row_by_id(context.parent_decisions, record_id)
    tags = _string_list(parent.get("content_tags"))
    assets = []
    for asset in context.assets_by_record.get(record_id, []):
        if asset["proposed_asset_index_eligibility"] != "include":
            continue
        can_quote = parent_decision["proposed_can_external_reference"] == "true"
        assets.append(
            {
                "record_id": record_id,
                "asset_id": asset["asset_id"],
                "asset_type": asset["asset_type"],
                "title": asset["asset_title"],
                "asset_url": asset["asset_url"],
                "canonical_url": asset["canonical_url"],
                "content_tags": tags,
                "external_usage": "可對外引用" if can_quote else "不可對外引用",
                "can_quote_externally": can_quote,
            }
        )
    assets.sort(key=lambda row: (_asset_order(row["asset_type"]), row["title"]))
    return {
        "entity_type": parent_decision["entity_type"],
        "entity_name": parent_decision["brand_name"],
        "merchant_handle": parent_decision["merchant_handle"],
        "sales_category_lv1": _text(parent.get("sales_category_lv1")),
        "sales_category_lv2": _text(parent.get("sales_category_lv2")),
        "content_tags": tags,
        "assets": assets,
    }


def _filter_resolution_eligibility(
    entities: Sequence[dict], context: ResolutionContext
) -> List[dict]:
    result = []
    for entity in entities:
        assets = []
        for asset in entity.get("assets") or []:
            asset_id = _text(asset.get("asset_id"))
            if asset_id in context.hold_asset_ids or asset_id in context.excluded_asset_ids:
                continue
            assets.append(dict(asset))
        if assets:
            result.append({**entity, "assets": assets})
    return result


def _merge_entities(existing: Sequence[dict], additions: Sequence[dict]) -> List[dict]:
    result: List[dict] = []
    by_name: Dict[str, dict] = {}
    for raw in [*existing, *additions]:
        entity = dict(raw)
        key = _normalize_exact(entity.get("entity_name"))
        target = by_name.get(key)
        if target is None:
            target = {**entity, "assets": []}
            by_name[key] = target
            result.append(target)
        seen = {
            (_text(asset.get("record_id")), _text(asset.get("asset_id")), _text(asset.get("title")))
            for asset in target["assets"]
        }
        for asset in entity.get("assets") or []:
            identity = (
                _text(asset.get("record_id")),
                _text(asset.get("asset_id")),
                _text(asset.get("title")),
            )
            if identity not in seen:
                target["assets"].append(dict(asset))
                seen.add(identity)
    return result


def _display_resolution_subset(entities: Sequence[dict]) -> Tuple[List[dict], int]:
    selected = []
    asset_count = 0
    for entity in entities[:5]:
        remaining = 10 - asset_count
        if remaining <= 0:
            break
        assets = list(entity.get("assets") or [])[:remaining]
        if not assets:
            continue
        selected.append({**entity, "assets": assets})
        asset_count += len(assets)
    return selected, asset_count


def _parent_restricted(record_id: str, context: ResolutionContext) -> bool:
    parent = context.parents_by_id[record_id]
    identity = "\n".join(
        value
        for value in (
            _text(parent.get("brand_name")),
            _text(parent.get("merchant_handle")),
        )
        if value
    )
    return bool(identity and context.restricted_index.check_text(identity).blocked)


def _asset_policy(record_id: str, asset_type: str) -> Tuple[str, str, str]:
    source_row = int(record_id.rsplit(":r", 1)[1])
    if source_row == 30:
        return "exclude", "excluded", "Human confirmed the taken-down parent and child asset must not be searchable."
    if source_row == 12 and asset_type == "video":
        return "hold", "not_searchable", "Source evidence marks the video as reviewing; parent approval does not release it."
    if source_row == 12:
        return "include", "searchable_internal", "Human confirmed the article is published for internal search only."
    return "include", "searchable", "Human confirmed this valid child asset may enter the future asset Apply manifest."


def _entity_type(parent: Mapping[str, object]) -> str:
    status = _text(parent.get("merchant_status"))
    if status == "合作夥伴":
        return "partner"
    if status == "現有商家":
        return "merchant"
    return "other"


def _target_parent_index(rows: Sequence[dict]) -> Dict[str, dict]:
    result = {}
    found_rows = set()
    for row in rows:
        if _text(row.get("record_type")) != "merchant_case":
            continue
        source_row = _integer(row.get("source_row"))
        if source_row not in PARENT_POLICIES:
            continue
        record_id = _record_id(row)
        if not record_id or record_id in result:
            raise MissingParentResolutionPreviewError("target parent identity is missing or duplicated")
        result[record_id] = row
        found_rows.add(source_row)
    if found_rows != set(PARENT_POLICIES):
        raise MissingParentResolutionPreviewError("not all five human-resolved parents exist")
    return result


def _review_index(rows: Sequence[dict]) -> Dict[str, dict]:
    result = {}
    for row in rows:
        if _text(row.get("record_type")) != "merchant_case":
            continue
        record_id = _record_id(row)
        if record_id in result:
            raise MissingParentResolutionPreviewError("duplicate current parent review row")
        result[record_id] = row
    return result


def _unique_index(rows: Sequence[dict], field_name: str, label: str) -> Dict[str, dict]:
    result = {}
    for row in rows:
        key = _text(row.get(field_name))
        if not key or key in result:
            raise MissingParentResolutionPreviewError(f"{label} identity is missing or duplicated")
        result[key] = row
    return result


def _unique_apply_index(rows: Sequence[dict]) -> Dict[Tuple[str, str], dict]:
    result = {}
    for row in rows:
        asset_id = _text(row.get("asset_id"))
        field_name = _text(row.get("field"))
        key = (asset_id, field_name)
        if not asset_id or field_name not in {"asset_url", "canonical_url"} or key in result:
            raise MissingParentResolutionPreviewError("Apply Preview URL identity is invalid or duplicated")
        if (
            _text(row.get("review_decision")) != "approve"
            or _text(row.get("eligibility")) != "ready_for_apply_preview"
            or _text(row.get("governance_status")) != "eligible"
        ):
            raise MissingParentResolutionPreviewError("Apply Preview contains an ineligible URL row")
        result[key] = row
    return result


def _apply_value(rows: Mapping[Tuple[str, str], dict], asset_id: str, field_name: str) -> str:
    row = rows.get((asset_id, field_name))
    return _text(row.get("proposed_value")) if row else ""


def _restricted_index(path: Path) -> GovernanceIndex:
    return GovernanceIndex(
        RestrictedCustomerRecord(
            brand_name=_text(row.get("brand_name")),
            website_url=_text(row.get("website_url")) or None,
            merchant_handle=_text(row.get("merchant_handle")) or None,
            restricted_aliases=(
                row.get("restricted_aliases")
                if isinstance(row.get("restricted_aliases"), list)
                else split_restricted_aliases(_text(row.get("brand_name")))
            ),
            source_sheet=_text(row.get("source_sheet")) or None,
            source_row=_integer(row.get("source_row")),
        )
        for row in _read_json_list(path)
        if _text(row.get("brand_name"))
    )


def _write_reports(
    output_dir: Path,
    summary: Mapping[str, object],
    context: ResolutionContext,
    search_results: Sequence[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / RESOLUTION_OUTPUT_FILENAMES[1], context.parent_decisions)
    _write_csv(output_dir / RESOLUTION_OUTPUT_FILENAMES[2], context.asset_decisions)
    alias_rows = []
    for parent in context.parent_decisions:
        for alias in json.loads(parent["proposed_search_aliases"]):
            alias_rows.append(
                {
                    "record_id": parent["record_id"],
                    "brand_name": parent["brand_name"],
                    "alias": alias,
                    "normalized_alias": _normalize_exact(alias),
                    "match_type": "case_insensitive_exact",
                    "storage_level": "source_record",
                    "fuzzy_matching": "false",
                    "governance_required": "true",
                    "reviewer": parent["reviewer"],
                    "reviewed_at": parent["reviewed_at"],
                }
            )
    _write_csv(output_dir / RESOLUTION_OUTPUT_FILENAMES[4], alias_rows)
    excluded = [
        row
        for row in context.asset_decisions
        if row["proposed_asset_index_eligibility"] in {"hold", "exclude"}
    ]
    _write_csv(output_dir / RESOLUTION_OUTPUT_FILENAMES[7], excluded)
    readiness = []
    for parent in context.parent_decisions:
        ready = parent["proposed_review_decision"] != "exclude"
        readiness.append(
            {
                "record_id": parent["record_id"],
                "brand_name": parent["brand_name"],
                "proposed_review_decision": parent["proposed_review_decision"],
                "ready_for_parent_decision_apply": _display_bool(ready),
                "ready_for_sync": "false",
                "sync_executed": "false",
                "reason": "Decision proposal is complete; formal sync requires a separately confirmed Apply Sprint."
                if ready
                else "Excluded parent must not be synced into general retrieval.",
            }
        )
    _write_csv(output_dir / RESOLUTION_OUTPUT_FILENAMES[8], readiness)
    proposal_fields = (
        "record_id",
        "brand_name",
        "proposed_review_decision",
        "proposed_search_aliases",
        "proposed_can_enter_vault",
        "proposed_can_enter_content_index",
        "proposed_can_external_reference",
        "reviewer",
        "reviewed_at",
        "reason",
    )
    proposals = [
        {
            **{field: row.get(field, "") for field in proposal_fields},
            "proposed_entity_type": row["entity_type"],
            "decision_reason": row["reason"],
        }
        for row in context.parent_decisions
    ]
    for row in proposals:
        row.pop("reason", None)
    _write_csv(output_dir / RESOLUTION_OUTPUT_FILENAMES[10], proposals)
    (output_dir / RESOLUTION_OUTPUT_FILENAMES[0]).write_text(
        _summary_markdown(summary, context), encoding="utf-8"
    )
    (output_dir / RESOLUTION_OUTPUT_FILENAMES[3]).write_text(
        _partner_rule_markdown(context), encoding="utf-8"
    )
    (output_dir / RESOLUTION_OUTPUT_FILENAMES[5]).write_text(
        _search_markdown(search_results), encoding="utf-8"
    )
    (output_dir / RESOLUTION_OUTPUT_FILENAMES[6]).write_text(
        _reconciliation_markdown(context.counts), encoding="utf-8"
    )
    (output_dir / RESOLUTION_OUTPUT_FILENAMES[9]).write_text(
        _checklist_markdown(summary), encoding="utf-8"
    )


def _summary_markdown(summary: Mapping[str, object], context: ResolutionContext) -> str:
    lines = [
        "# Missing Parent Resolution Preview",
        "",
        "> Human decision preview only. No review decision, Vault, SQLite, index, asset mapping or Slack renderer was changed.",
        "",
        f"- Conclusion: {summary['conclusion']}",
        f"- Parents resolved: {summary['parent_count']}",
        f"- Resolution assets: include={summary['resolution_include_asset_count']}, hold={summary['resolution_hold_asset_count']}, exclude={summary['resolution_excluded_asset_count']}",
        f"- Global assets: eligible={summary['final_eligible_asset_count']}, hold={summary['final_hold_asset_count']}, excluded={summary['final_excluded_asset_count']}",
        f"- Approved URL fields: {summary['original_approved_url_field_count']} -> {summary['final_approved_url_field_count']}",
        f"- Old PLAN_ID: `{OLD_PLAN_ID}` - **DO NOT CONFIRM**",
        "",
        "## Parent Decisions",
        "",
    ]
    for row in context.parent_decisions:
        lines.append(
            f"- `{row['record_id']}` {row['brand_name']}: {row['current_review_decision']} -> "
            f"`{row['proposed_review_decision']}`; entity={row['entity_type']}"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Decisions applied: no",
            "- Parent sync: not run",
            "- Formal Vault / SQLite writes: 0 / 0",
            "- Production Slack renderer changes: 0",
            "- publication_status / review_status queries: remain disabled",
        ]
    )
    return "\n".join(lines) + "\n"


def _partner_rule_markdown(context: ResolutionContext) -> str:
    partners = [row for row in context.parent_decisions if row["entity_type"] == "partner"]
    lines = [
        "# Partner Handle Rule Preview",
        "",
        "- Authoritative identity source: existing `merchant_status`.",
        "- Exact `合作夥伴` maps to preview `entity_type=partner`.",
        "- Partner Merchant Handle is optional; blank is not a metadata error or index blocker.",
        "- No synthetic Handle may be generated from brand name or title.",
        "- This is a preview contract; no schema migration was applied.",
        "",
        "## Resolved Partners",
        "",
    ]
    lines.extend(
        f"- `{row['record_id']}` {row['brand_name']}: handle={row['merchant_handle'] or 'blank (allowed)'}"
        for row in partners
    )
    return "\n".join(lines) + "\n"


def _search_markdown(results: Sequence[dict]) -> str:
    lines = [
        "# Search Behavior Preview",
        "",
        "> Offline read-only overlay using the existing structured retrieval output plus human-approved parent and asset decisions. Production Slack was not changed.",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result['query']}",
                "",
                f"- Entities: {result['total_entities']}",
                f"- Assets: {result['total_assets']}",
                f"- Citations: {len(result['citations'])}",
                f"- Resolution: `{result['resolution_kind']}`",
                "",
                "```text",
                render_resolution_standard(result),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _reconciliation_markdown(counts: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "# Asset Apply Count Reconciliation",
            "",
            f"- Inventory: {counts['inventory_asset_count']}",
            f"- Original: {counts['original_eligible_asset_count']} eligible assets / {counts['original_approved_url_field_count']} URL fields",
            f"- Change: exclude one previously eligible taken-down article; the other eight orphan assets already existed in the approved URL preview.",
            f"- Final: {counts['final_eligible_asset_count']} eligible / {counts['final_hold_asset_count']} hold / {counts['final_excluded_asset_count']} excluded",
            f"- Final URL fields: {counts['final_approved_url_field_count']}",
            f"- Conservation: {counts['final_eligible_asset_count']} + {counts['final_hold_asset_count']} + {counts['final_excluded_asset_count']} = {counts['inventory_asset_count']}",
            f"- Identity added / lost: {counts['identity_added_count']} / {counts['identity_lost_count']}",
            "- The reviewing video remains hold/not_searchable and contributes zero URL Apply fields.",
        ]
    ) + "\n"


def _checklist_markdown(summary: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "# Resolution Confirmation Checklist",
            "",
            "- [x] James Huang decisions represented without rewriting source decisions",
            "- [x] Partner Handle optional rule uses authoritative merchant_status",
            "- [x] Asset include / hold / exclude is independent from parent approval",
            "- [x] SLP and SHOPLINE Payments are exact, case-insensitive source-record aliases",
            "- [x] Excluded parent and held asset produce no result or citation",
            f"- [x] Global candidate reconciliation: {summary['final_eligible_asset_count']} assets / {summary['final_approved_url_field_count']} URL fields",
            "- [x] Formal Vault, SQLite, review decisions and production Slack renderer unchanged",
            f"- [ ] `{OLD_PLAN_ID}` remains invalid - **DO NOT CONFIRM OR EXECUTE**",
            "- [ ] Apply parent decisions in a separately approved Sprint",
            "- [ ] Rebuild parent/asset Apply Plan only after decisions are applied and validated",
        ]
    ) + "\n"


def _write_csv(path: Path, rows: Sequence[dict]) -> None:
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["record_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _safe_csv(row.get(field)) for field in fieldnames})


def _read_csv(path: Path) -> List[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise MissingParentResolutionPreviewError(f"CSV has no header: {path}")
        return list(reader)


def _read_json_list(path: Path) -> List[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MissingParentResolutionPreviewError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise MissingParentResolutionPreviewError(f"JSON must contain an array of objects: {path}")
    return payload


def _assert_safe_output(output_dir: Path, protected_paths: Sequence[Path]) -> None:
    output = output_dir.resolve()
    for path in protected_paths:
        protected = Path(path).resolve()
        if output == protected or output in protected.parents or protected in output.parents:
            raise MissingParentResolutionPreviewError("output must be separate from every protected source")
    lowered = {part.casefold() for part in output.parts}
    if ".mka" in lowered or "obsidian_vault" in lowered:
        raise MissingParentResolutionPreviewError("output cannot be inside formal Vault or .mka")


def _validate_reviewed_at(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MissingParentResolutionPreviewError("reviewed_at must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise MissingParentResolutionPreviewError("reviewed_at must include the local timezone offset")


def _record_id(row: Mapping[str, object]) -> str:
    sheet = _text(row.get("source_sheet"))
    source_row = _integer(row.get("source_row"))
    return f"{sheet}:r{source_row}" if sheet and source_row is not None else ""


def _row_by_id(rows: Sequence[dict], record_id: str) -> dict:
    matches = [row for row in rows if row["record_id"] == record_id]
    if len(matches) != 1:
        raise MissingParentResolutionPreviewError("resolved parent row is missing or duplicated")
    return matches[0]


def _record_sort_key(item: Tuple[str, dict]) -> int:
    return int(item[1]["source_row"])


def _asset_sort_key(row: Mapping[str, object]) -> Tuple[int, int, str]:
    return (
        _integer(row.get("source_row")) or 0,
        _asset_order(_text(row.get("asset_type"))),
        _text(row.get("asset_id")),
    )


def _asset_order(asset_type: str) -> int:
    return {"article": 0, "video": 1, "podcast": 2, "news": 3}.get(asset_type, 9)


def _asset_label(value: object) -> str:
    return {"article": "文章", "video": "影片", "podcast": "Podcast", "news": "新聞"}.get(
        _text(value), "其他"
    )


def _normalize_exact(value: object) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _safe_slack_text(value: object) -> str:
    return _text(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _safe_url(value: str) -> bool:
    return bool(re.match(r"^https?://[^\s<>|]+$", value))


def _merge_unique(values: Iterable[str]) -> List[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _string_list(value: object) -> List[str]:
    if isinstance(value, list):
        return _merge_unique(_text(item) for item in value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [item.strip() for item in value.split("|")]
        return _merge_unique(_text(item) for item in decoded) if isinstance(decoded, list) else []
    return []


def _stable_list(values: Sequence[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _display_bool(value: bool) -> str:
    return str(bool(value)).lower()


def _bool_text(value: object) -> str:
    normalized = _text(value).casefold()
    if normalized in {"true", "1", "yes"}:
        return "true"
    if normalized in {"false", "0", "no"}:
        return "false"
    return "unknown"


def _integer(value: object) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_csv(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "[unsafe input redacted]"
    return text


def _hash_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if not child.is_file() or child.name.startswith("._"):
            continue
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
