from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .governance import metadata_allows_written_external_use
from .models import Citation, StructuredAsset, StructuredEntity, StructuredRetrievalResult
from .query_planning import FIELD_REGISTRY


ASSET_LABELS = {
    "article": "文章",
    "video": "影片",
    "podcast": "Podcast",
    "news": "新聞",
    "other": "其他",
}
ASSET_ORDER = {"article": 0, "video": 1, "podcast": 2, "news": 3, "other": 4}
MISSING = "資料未提供"
DATA_CONFLICT = "資料不一致"
GENERAL_NO_RESULT_MESSAGE = "找不到相關內容。請換個關鍵字,或聯繫管理者確認資料是否已收錄。"
GOVERNANCE_SILENT_WORDS = (
    "restricted",
    "pending",
    "verbal_briefing",
    "denylist",
    "治理",
    "受限制",
)
# Literal characters that end a Slack mrkdwn link, plus the backslash Slack uses to escape them.
MRKDWN_UNSAFE_CHARS = re.compile(r"[\x00-\x20\x7f<>|\\]")
# Everything that would split a rendered line apart: C0/C1 controls, DEL, and the Unicode line and
# paragraph separators. A run collapses to a single space so field text stays on one line.
MRKDWN_LINE_BREAKS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")
# Any character reference that could decode into a Slack mrkdwn delimiter. Slack's decode order is
# undocumented, so a URL that already carries entity syntax is ambiguous and is never rendered.
MRKDWN_ENTITY_REFERENCE = re.compile(
    r"&(?:#|[A-Za-z][A-Za-z0-9]*;|(?:amp|lt|gt|quot)\b)",
    re.IGNORECASE,
)


def url_is_mrkdwn_safe(value: str) -> bool:
    """Reject URLs that could break out of a Slack mrkdwn link however Slack decodes them."""
    return not MRKDWN_UNSAFE_CHARS.search(value) and not MRKDWN_ENTITY_REFERENCE.search(value)


def escape_mrkdwn_url(value: str) -> str:
    """Escape the only delimiter-relevant character a mrkdwn-safe URL can still contain."""
    return value.replace("&", "&amp;")


@dataclass(frozen=True)
class UrlCanonicalizationPolicy:
    www_equivalent_hosts: frozenset[str] = frozenset()
    https_equivalent_hosts: frozenset[str] = frozenset()
    path_case_insensitive_hosts: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CanonicalUrl:
    display: str
    identity: str


def canonicalize_url(
    value: Optional[str],
    policy: UrlCanonicalizationPolicy = UrlCanonicalizationPolicy(),
) -> Optional[CanonicalUrl]:
    if not value:
        return None
    raw = str(value).strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None

    host = parsed.hostname.casefold()
    if host.startswith("www.") and host[4:] in policy.www_equivalent_hosts:
        host = host[4:]
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = host if port is None else f"{host}:{port}"
    scheme = parsed.scheme.casefold()
    if host in policy.https_equivalent_hosts:
        scheme = "https"

    query_pairs = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold()
        not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
    ]
    query_pairs.sort(key=lambda pair: (pair[0], pair[1]))
    query = urlencode(query_pairs, doseq=True)
    display_path = parsed.path or "/"
    identity_path = display_path.rstrip("/") or "/"
    if host in policy.path_case_insensitive_hosts:
        display_path = display_path.casefold()
        identity_path = identity_path.casefold()
    display = urlunsplit((scheme, netloc, display_path, query, parsed.fragment))
    identity = urlunsplit((scheme, netloc, identity_path, query, ""))
    return CanonicalUrl(display=display, identity=identity)


def canonicalize_link_target(value: Optional[str]) -> Optional[CanonicalUrl]:
    """Canonicalize a URL only after the RAW value has passed the mrkdwn safety policy.

    urlsplit() silently removes TAB, LF and CR before parsing, so a value validated only in its
    canonical form could be rewritten into a different URL and then rendered as a clickable link.
    Deciding on the raw value first keeps normalization from becoming acceptance. Surrounding
    whitespace is stripped exactly as canonicalize_url does, so merely padded values still resolve;
    every other MRKDWN_UNSAFE_CHARS byte, including the C0 controls and DEL, fails closed here.
    """
    raw = str(value or "").strip()
    if not raw or not url_is_mrkdwn_safe(raw):
        return None
    return canonicalize_url(raw)


def format_structured_slack_reply(answer) -> Optional[str]:
    generated = getattr(answer, "generated", answer)
    structured = getattr(generated, "structured_result", None)
    if structured is None:
        return None
    if structured.unsupported_constraints:
        return None
    if structured.abstain_reason in {"no_constraint_intersection", "unresolved_structured_lookup"}:
        return GENERAL_NO_RESULT_MESSAGE

    entities = _presentation_entities(structured, getattr(generated, "citations", []))
    conditions = _render_conditions(getattr(generated, "question", ""), structured.query_plan)
    if not entities:
        return "\n".join(
            [
                f"已套用搜尋條件：{conditions}",
                "",
                "找不到符合條件的品牌或內容。",
                "",
                "你可以嘗試：",
                "• 減少搜尋條件",
                "• 改用品牌名稱或 Handle",
                "• 移除年份或內容類型限制",
            ]
        )

    displayed_entities = entities[:5]
    displayed_assets: List[dict] = []
    for entity in displayed_entities:
        remaining = 10 - len(displayed_assets)
        if remaining <= 0:
            break
        entity["assets"] = entity["assets"][:remaining]
        displayed_assets.extend(entity["assets"])
    displayed_entities = [entity for entity in displayed_entities if entity["assets"]]

    lines = [
        f"已套用搜尋條件：{conditions}",
        "",
        f"共找到 {len(displayed_entities)} 個品牌／夥伴、{len(displayed_assets)} 筆內容。",
    ]
    number = 1
    for entity_index, entity in enumerate(displayed_entities):
        lines.extend(["", f"`{_inline(entity['entity_name'])}`"])
        lines.extend(
            [
                f"_{_label_value('Handle', entity['merchant_handle'])}_",
                f"_{_label_value('Sales Category LV1', entity['sales_category_lv1'])}_",
                f"_{_label_value('Sales Category LV2', entity['sales_category_lv2'])}_",
            ]
        )
        for asset in entity["assets"]:
            asset["number"] = number
            number += 1
            # Slack only closes a bold run when the trailing "*" sits on a delimiter boundary, so a
            # label whose "*" is immediately followed by the value is shown to the user verbatim
            # whenever that value starts with a word character (CJK and digits both count). The
            # asset header below keeps its bold because its closing "*" ends the line; the field
            # labels carry none, so their rendering can never depend on the dynamic value.
            lines.extend(
                [
                    "> • *"
                    + f"{ASSET_LABELS.get(asset['asset_type'], '其他')} [{asset['number']}]"
                    + "*",
                    f"> 標題：{_normal_text(asset['title'])}",
                    f"> 連結：{_slack_link(asset['url']) if asset['url'] else MISSING}",
                    f"> 上線日期：{_normal_text(asset['published_at']) or MISSING}",
                    f"> 採訪年份：{_normal_text(asset.get('interview_year') or entity.get('interview_year')) or MISSING}",
                    f"> 狀態：{_status_label(asset['status'])}",
                    f"> 對外引用：{asset['external_usage']}",
                    f"> 資料來源：{_normal_text(asset['source']) or MISSING}",
                ]
            )
            if asset is not entity["assets"][-1]:
                lines.append(">")
    return "\n".join(lines)


def _presentation_entities(
    structured: StructuredRetrievalResult,
    citations: Sequence[Citation],
) -> List[dict]:
    citation_lookup = _citation_lookup(citations)
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    for entity_index, entity in enumerate(structured.matched_entities):
        entity_key = _entity_key(entity)
        state = grouped.setdefault(
            entity_key,
            {
                "entity_name": entity.entity_name,
                "handles": [],
                "lv1": [],
                "lv2": [],
                "years": [],
                "assets": [],
                "invalid": False,
                "first_index": entity_index,
            },
        )
        _collect_value(state["handles"], entity.merchant_handle)
        _collect_value(state["lv1"], entity.sales_category_lv1)
        _collect_value(state["lv2"], entity.sales_category_lv2)
        if entity.interview_year is not None:
            _collect_value(state["years"], entity.interview_year)
        for asset_index, asset in enumerate(entity.assets):
            citation = _match_citation(asset, citation_lookup)
            if citation is None or not _citation_is_written_safe(citation):
                continue
            candidate = _asset_candidate(asset, citation, entity.interview_year, entity_index, asset_index)
            keys = set(_content_identity_keys(candidate))
            existing = next(
                (group for group in state["assets"] if keys.intersection(group["keys"])),
                None,
            )
            if existing is None:
                state["assets"].append({"keys": keys, "candidates": [candidate]})
            else:
                existing["keys"].update(keys)
                existing["candidates"].append(candidate)

    output = []
    for state in grouped.values():
        if len(state["handles"]) > 1:
            continue
        assets = []
        for group in state["assets"]:
            primary = _merge_asset_candidates(group["candidates"])
            if primary is not None:
                assets.append(primary)
        if not assets:
            continue
        assets.sort(key=lambda item: (ASSET_ORDER.get(item["asset_type"], 4), item["first_index"], item["title"]))
        output.append(
            {
                "entity_name": state["entity_name"],
                "merchant_handle": state["handles"][0] if state["handles"] else None,
                "sales_category_lv1": _merged_value(state["lv1"]),
                "sales_category_lv2": _merged_value(state["lv2"]),
                "interview_year": _merged_value(state["years"]),
                "assets": assets,
                "first_index": state["first_index"],
            }
        )
    output.sort(key=lambda item: item["first_index"])
    return output


def _asset_candidate(
    asset: StructuredAsset,
    citation: Citation,
    interview_year: Optional[int],
    entity_index: int,
    asset_index: int,
) -> dict:
    # Link targets are validated raw-first; the title is only ever compared, never linked.
    citation_url = canonicalize_link_target(citation.canonical_url)
    asset_url = canonicalize_link_target(asset.url)
    title_is_url = canonicalize_url(asset.title)
    url = citation_url or asset_url
    title = asset.title or citation.title
    url_conflict = False
    if title_is_url and not (citation.canonical_url or asset.url):
        title = MISSING
    elif title_is_url and (citation.canonical_url or asset.url):
        linked_title = canonicalize_url(citation.canonical_url or asset.url)
        if linked_title and linked_title.identity != title_is_url.identity:
            title = DATA_CONFLICT
            url_conflict = True
    return {
        "asset_type": asset.asset_type or "other",
        "title": title,
        "url": DATA_CONFLICT if url_conflict else (url.display if url else None),
        "url_conflict": url_conflict,
        "url_identity": url.identity if url else None,
        "published_at": citation.publish_date or asset.published_at,
        "status": citation.status or asset.publication_status,
        "interview_year": interview_year,
        "external_usage": "可對外引用" if _citation_is_written_safe(citation) else "不可對外引用",
        "source": _source_value(citation.source_sheet, citation.source_row),
        "source_key": (citation.source_sheet or "", citation.source_row or 0),
        "source_sheet": citation.source_sheet,
        "source_row": citation.source_row,
        "canonical_content_identity": citation.chunk_id.rsplit(":", 1)[0] if citation.chunk_id else None,
        "citation": citation,
        "first_index": (entity_index, asset_index),
    }


def _content_identity_keys(candidate: Mapping[str, object]) -> List[Tuple[object, ...]]:
    asset_type = candidate["asset_type"]
    keys = []
    if candidate["canonical_content_identity"]:
        keys.append(("content", candidate["canonical_content_identity"], asset_type))
    if candidate["url_identity"]:
        keys.append(("url", candidate["url_identity"], asset_type))
    if candidate["source_sheet"] and candidate["source_row"]:
        keys.append(("source", str(candidate["source_sheet"]).casefold(), candidate["source_row"], asset_type))
    if not keys:
        keys.append(("title", _identity_text(candidate["title"]), asset_type))
    return keys


def _merge_asset_candidates(candidates: Sequence[dict]) -> Optional[dict]:
    titles = {_identity_text(candidate["title"]) for candidate in candidates if candidate["title"]}
    if len(titles) > 1:
        return None
    ranked = sorted(candidates, key=lambda item: (-_completeness(item), item["first_index"], item["title"]))
    primary = dict(ranked[0])
    for field in ("url", "published_at", "status", "interview_year"):
        if not primary.get(field):
            for candidate in ranked[1:]:
                if candidate.get(field):
                    primary[field] = candidate[field]
                    break
    if len({candidate.get("url_identity") for candidate in candidates if candidate.get("url_identity")}) > 1:
        primary["url"] = DATA_CONFLICT
    if len({candidate.get("published_at") for candidate in candidates if candidate.get("published_at")}) > 1:
        primary["published_at"] = DATA_CONFLICT
    if len({candidate.get("status") for candidate in candidates if candidate.get("status")}) > 1:
        primary["status"] = DATA_CONFLICT
    if len({candidate.get("interview_year") for candidate in candidates if candidate.get("interview_year")}) > 1:
        primary["interview_year"] = DATA_CONFLICT
    sources = sorted(
        {candidate["source"] for candidate in candidates if candidate.get("source")},
        key=_source_sort_key,
    )
    primary["source"] = "、".join(sources) if sources else None
    return primary


def _source_sort_key(value: str) -> Tuple[object, ...]:
    match = re.search(r" r(\d+)$", value)
    return (value[: match.start()] if match else value, int(match.group(1)) if match else 0)


def _completeness(candidate: Mapping[str, object]) -> int:
    return sum(bool(candidate.get(field)) for field in ("title", "url", "published_at", "status", "source"))


def _citation_lookup(citations: Sequence[Citation]) -> dict:
    lookup: Dict[object, List[Citation]] = {}
    for citation in citations:
        key = (citation.source_sheet, citation.source_row, citation.title)
        lookup.setdefault(key, []).append(citation)
    return lookup


def _match_citation(asset: StructuredAsset, lookup: Mapping[object, List[Citation]]) -> Optional[Citation]:
    exact = [
        citation
        for citations in lookup.values()
        for citation in citations
        if citation.label == asset.citation_label and asset.citation_label
    ]
    if exact:
        return exact[0]
    candidates = lookup.get((asset.source_sheet, asset.source_row, asset.title), [])
    matching_type = [
        citation for citation in candidates if _citation_asset_type(citation) == asset.asset_type
    ]
    if matching_type:
        return matching_type[0]
    return candidates[0] if len(candidates) == 1 else None


def _citation_asset_type(citation: Citation) -> Optional[str]:
    if not citation.chunk_id:
        return None
    return citation.chunk_id.rsplit(":", 1)[-1]


def _entity_key(entity: StructuredEntity) -> str:
    name = _identity_text(entity.entity_name)
    return f"{entity.entity_type}:{name}"


def _collect_value(values: List[object], value: object) -> None:
    if value is not None and str(value).strip() and value not in values:
        values.append(value)


def _merged_value(values: Sequence[object]) -> Optional[object]:
    if not values:
        return None
    return values[0] if len(values) == 1 else DATA_CONFLICT


def _render_conditions(question: str, plan: Mapping[str, object]) -> str:
    constraints = plan.get("supported_constraints") or plan.get("hard_filters") or []
    groups: "OrderedDict[str, List[str]]" = OrderedDict()
    free_terms = plan.get("free_text_terms") or []
    if constraints:
        if free_terms:
            groups["關鍵字"] = [str(item) for item in free_terms]
        for constraint in constraints:
            field = str(constraint.get("field") or "")
            label = _condition_label(field, constraint)
            value = _constraint_value(field, constraint.get("value"), constraint.get("operator"))
            if value and value not in groups.setdefault(label, []):
                groups[label].append(value)
    else:
        groups[""] = [question.strip() or str(plan.get("normalized_query") or "")]
    return "｜".join(f"`{_inline(label + '：' + '、'.join(values) if label else values[0])}`" for label, values in groups.items())


def _condition_label(field: str, constraint: Mapping[str, object]) -> str:
    if field in {"entity_name", "merchant_name", "partner_name", "merchant_handle"}:
        return "關鍵字" if field != "merchant_handle" else "Handle"
    definition = FIELD_REGISTRY.get(field)
    return str(constraint.get("output_label") or (definition.output_label if definition else field))


def _constraint_value(field: str, value: object, operator: object) -> str:
    if operator == "range" and isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}～{value[1]}"
    if field == "asset_type":
        return ASSET_LABELS.get(str(value), str(value))
    return str(value) if value is not None else ""


def _citation_is_written_safe(citation: Citation) -> bool:
    return citation.can_quote_externally is True and metadata_allows_written_external_use(citation)


def _source_value(sheet: Optional[str], row: Optional[int]) -> Optional[str]:
    if not sheet:
        return None
    return f"{sheet} r{row}" if row is not None else sheet


def _status_label(value: Optional[str]) -> str:
    return {
        "published": "已上線",
        "draft": "草稿",
        "archived": "已封存",
        "deprecated": "已棄用",
    }.get(str(value or ""), _normal_text(value) or MISSING)


def _label_value(label: str, value: object) -> str:
    return f"{label}：{_normal_text(value) or MISSING}"


def _slack_link(value: object) -> str:
    # Raw safety is decided before canonicalization, then re-checked on the canonical form so
    # escaping never changes the URL's identity.
    url = canonicalize_link_target(value)
    if url is None or not url_is_mrkdwn_safe(url.display):
        return MISSING
    return f"<{escape_mrkdwn_url(url.display)}|開啟連結>"


def _normal_text(value: object) -> str:
    if value is None:
        return ""
    return _mrkdwn_escape(str(value))


def _inline(value: object) -> str:
    # This value is placed inside a code span the formatter opens, and a raw backtick would close
    # that span early. Slack has no escape for it, so the character itself is swapped for a grave
    # accent that carries no mrkdwn meaning.
    return _mrkdwn_escape(str(value)).replace("`", "ˋ")


def _mrkdwn_escape(value: str) -> str:
    # Slack decodes exactly these three entity forms and offers no backslash escape, so they are
    # the only delimiters that can be neutralised in place. A backslash written in front of any
    # other marker reaches the user as a literal backslash.
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Dynamic text must not leave the line the formatter put it on: a raw newline would drop out of
    # the blockquote and the next line would read as a field the formatter never wrote.
    return MRKDWN_LINE_BREAKS.sub(" ", escaped)


def _identity_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
