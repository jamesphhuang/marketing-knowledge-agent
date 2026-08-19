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
# One Slack message shows at most this many brand groups; the rest continue in the same thread.
BRAND_PAGE_SIZE = 15
# chat.postMessage accepts 40,000 characters of text. A page is filled brand by brand and stops
# early once the next brand group would cross this budget, so a page is never silently truncated
# and a brand group is never split across two messages. The budget sits far below the API limit:
# it only binds on pathologically large brand groups, where one extra page is the safe outcome.
PAGE_CHAR_BUDGET = 12000
# What the Slack event handler matches on, once it has stripped the app mention from the reply.
SHOW_MORE_COMMAND = "顯示更多"
# What the notice actually asks the user to type. The bot subscribes to app_mention and nothing
# else, so a thread reply that does not mention it never reaches the handler at all -- quoting the
# bare command would walk the user into a reply that is silently dropped. One definition of each
# half, so the matcher and the instruction can never drift apart.
SHOW_MORE_MENTION = "@Marketing Knowledge Agent"
SHOW_MORE_REPLY = f"{SHOW_MORE_MENTION} {SHOW_MORE_COMMAND}"
# How many brand groups the Slack surface materialises for one search before it stops admitting
# new ones. It is display capacity, not ranking -- slack_interface asks pipeline.agent_ask for
# exactly this much. The renderer owns the number because a result that reaches the ceiling has to
# be described to the user as a ceiling rather than as a complete total.
SLACK_SEARCH_PARENT_CAP = 60
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


@dataclass(frozen=True)
class SlackSearchPages:
    """One search result, already rendered into the messages that will carry it.

    Paging over rendered text keeps the continuation deterministic: the ordered result is
    formatted once, and every later page is the text this same search produced, never a second
    retrieval that a changed index could answer differently. It is also the least data a
    continuation can hold -- user-facing output that has already passed the governance,
    external-use and mrkdwn contracts, carrying no query plan, citation, provenance or identity.
    """

    pages: Tuple[str, ...]
    total_entities: int
    total_assets: int


def format_structured_slack_reply(answer) -> Optional[str]:
    """The first Slack message for a structured search, or None when this is not one."""
    pages = build_structured_slack_pages(answer)
    return pages.pages[0] if pages is not None else None


def build_structured_slack_pages(answer) -> Optional[SlackSearchPages]:
    generated = getattr(answer, "generated", answer)
    structured = getattr(generated, "structured_result", None)
    if structured is None:
        return None
    if structured.unsupported_constraints:
        return None
    if structured.abstain_reason in {"no_constraint_intersection", "unresolved_structured_lookup"}:
        return SlackSearchPages(pages=(GENERAL_NO_RESULT_MESSAGE,), total_entities=0, total_assets=0)

    entities = _presentation_entities(structured, getattr(generated, "citations", []))
    conditions = _render_conditions(getattr(generated, "question", ""), structured.query_plan)
    if not entities:
        empty = "\n".join(
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
        return SlackSearchPages(pages=(empty,), total_entities=0, total_assets=0)

    total_assets = sum(len(entity["assets"]) for entity in entities)
    # Whether the ceiling bound is a fact about the records the structured layer admitted, not
    # about the brands left after this module grouped them. ``matched_entities`` carries one entry
    # per source record and structured_results stops admitting new ones at exactly this cap, so a
    # full list means the ceiling bound. Brand groups are only ever fewer -- one brand can own
    # several source records, and a group is dropped outright on conflicting handles or when no
    # asset survives governance -- so reading the ceiling off the brand count lets a truncated
    # result introduce itself as a complete one. A full list may also be an exactly-full result
    # rather than a truncated one; the two are indistinguishable without a pre-cap total, and
    # disclosing the ceiling is the side that claims less.
    at_ceiling = len(structured.matched_entities) >= SLACK_SEARCH_PARENT_CAP
    blocks = []
    number = 1
    for entity in entities:
        block, number = _entity_block(entity, number)
        blocks.append(block)

    pages = []
    shown = 0
    grouped = _paginate_blocks(blocks)
    for page_index, page_blocks in enumerate(grouped):
        first_rank = shown + 1
        shown += len(page_blocks)
        pages.append(
            _render_page(
                page_blocks,
                conditions=conditions,
                page_index=page_index,
                first_rank=first_rank,
                last_rank=shown,
                total_entities=len(entities),
                total_assets=total_assets,
                remaining=len(entities) - shown,
                at_ceiling=at_ceiling,
            )
        )
    return SlackSearchPages(
        pages=tuple(pages), total_entities=len(entities), total_assets=total_assets
    )


def _paginate_blocks(blocks: Sequence[List[str]]) -> List[List[List[str]]]:
    """Split rendered brand blocks into pages, in rank order, without ever splitting a block.

    A page closes when it already holds BRAND_PAGE_SIZE brands or when the next brand would push
    it past the character budget. The ``current`` guard means a single oversized brand group still
    gets its own page rather than being dropped or cut: brand atomicity outranks the budget.
    """
    pages: List[List[List[str]]] = []
    current: List[List[str]] = []
    current_chars = 0
    for block in blocks:
        size = _block_chars(block)
        if current and (len(current) >= BRAND_PAGE_SIZE or current_chars + size > PAGE_CHAR_BUDGET):
            pages.append(current)
            current = []
            current_chars = 0
        current.append(block)
        current_chars += size
    if current:
        pages.append(current)
    return pages


def _block_chars(block: Sequence[str]) -> int:
    # Every line costs its own newline, plus the blank line that separates blocks on a page.
    return sum(len(line) + 1 for line in block) + 1


def _render_page(
    page_blocks: Sequence[Sequence[str]],
    *,
    conditions: str,
    page_index: int,
    first_rank: int,
    last_rank: int,
    total_entities: int,
    total_assets: int,
    remaining: int,
    at_ceiling: bool,
) -> str:
    # ``at_ceiling`` is decided by the caller from the retrieved record count, never re-derived
    # from ``total_entities`` here: the two differ whenever grouping merges or drops a brand, and
    # this function only ever sees the smaller number.
    if page_index == 0:
        # Either wording describes the whole retrieved result rather than this page: a user who
        # reads 23 and sees 15 brands is told below exactly how many are still waiting.
        lines = [
            f"已套用搜尋條件：{conditions}",
            "",
            f"目前顯示最多 {total_entities} 個品牌／夥伴，共 {total_assets} 筆內容。"
            if at_ceiling
            else f"共找到 {total_entities} 個品牌／夥伴、{total_assets} 筆內容。",
        ]
    else:
        lines = [f"繼續顯示搜尋結果（第 {first_rank}–{last_rank} 個品牌／夥伴）"]
    for block in page_blocks:
        lines.append("")
        lines.extend(block)
    if remaining > 0:
        lines.extend(
            [
                "",
                f"尚有 {remaining} 個品牌／夥伴未顯示。",
                f"若要繼續查看，請在此討論串回覆「{SHOW_MORE_REPLY}」。",
            ]
        )
    elif at_ceiling:
        # The last page of a ceiling-limited result. It discloses the ceiling without asserting
        # that anything lies beyond it -- that is exactly what cannot be known here -- and points
        # at the one action that could surface more.
        lines.extend(
            [
                "",
                f"已顯示目前最多可提供的 {total_entities} 個品牌／夥伴。",
                "若想查看更多可能結果，請縮小或調整搜尋條件後重新搜尋。",
            ]
        )
    return "\n".join(lines)


def _entity_block(entity: Mapping[str, object], number: int) -> Tuple[List[str], int]:
    lines = [
        f"`{_inline(entity['entity_name'])}`",
        f"_{_label_value('Handle', entity['merchant_handle'])}_",
        f"_{_label_value('Sales Category LV1', entity['sales_category_lv1'])}_",
        f"_{_label_value('Sales Category LV2', entity['sales_category_lv2'])}_",
    ]
    assets = entity["assets"]
    for asset in assets:
        asset["number"] = number
        number += 1
        # Slack only closes a bold run when the trailing "*" sits on a delimiter boundary, so a
        # label whose "*" is immediately followed by the value is shown to the user verbatim
        # whenever that value starts with a word character (CJK and digits both count). The asset
        # header below keeps its bold because its closing "*" ends the line; the title beneath it
        # carries no formatter-owned marker at all, so its rendering cannot depend on the value.
        lines.extend(
            [
                "> • *" + f"{ASSET_LABELS.get(asset['asset_type'], '其他')} [{asset['number']}]" + "*",
                f"> {_asset_title(asset)}",
            ]
        )
        if asset is not assets[-1]:
            lines.append(">")
    return lines, number


def _asset_title(asset: Mapping[str, object]) -> str:
    """The asset title, clickable only when this asset's own approved URL resolves safely.

    No URL is derived, guessed or borrowed here. ``asset["url"]`` is whatever the approved URL
    authority already attached to this asset -- an authority keyed on the asset's own type, so an
    article link can only ever reach an article and a video link only a video. Anything that is
    not a mrkdwn-safe absolute URL (absent, conflicting, malformed, hostile) leaves the title as
    plain text; the title is never allowed to supply a link of its own.
    """
    title = _normal_text(asset["title"])
    if not title:
        return MISSING
    url = canonicalize_link_target(asset["url"])
    if url is None or not url_is_mrkdwn_safe(url.display):
        return title
    # The title reaches here already escaped: "<" and ">" are entity references, so it cannot
    # close this link construct or open another, and control characters have collapsed to spaces
    # so it cannot leave the line. A "|" inside the label is inert -- Slack splits on the first.
    return f"<{escape_mrkdwn_url(url.display)}|{title}>"


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


def _label_value(label: str, value: object) -> str:
    return f"{label}：{_normal_text(value) or MISSING}"


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
