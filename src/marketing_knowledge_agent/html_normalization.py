"""Deterministic normalization for the approved WP11 synthetic HTML subset."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from typing import List, Optional, Sequence, Set, Tuple


HTML_NORMALIZER_VERSION = "html-normalizer-v1"
HTML_NORMALIZER_MAX_INPUT_BYTES = 1_048_576

DIAGNOSTIC_CODE_ORDER = (
    "NO_MEANINGFUL_CONTENT",
    "UNSUPPORTED_TABLE_STRUCTURE",
    "MALFORMED_STRUCTURE",
    "INPUT_TOO_LARGE",
    "NEEDS_REVIEW",
)

_HTML_WHITESPACE = re.compile(r"[ \t\n\f\r]+")
_LINE_BREAK = object()

_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_DROP_SUBTREE_TAGS = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "canvas",
    "svg",
    "nav",
    "footer",
}
_SYNTHETIC_BOILERPLATE_VALUES = {
    "cookie",
    "ad",
    "social",
    "related",
    "tracking",
    "duplicate-header",
    "duplicate-footer",
    "cta",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_LIST_STRUCTURAL_TAGS = {"li"}
_TABLE_STRUCTURAL_TAGS = {"caption", "thead", "tbody", "tfoot", "tr", "th", "td"}
_BLOCK_CONTAINER_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "details",
    "dialog",
    "div",
    "dl",
    "fieldset",
    "figure",
    "form",
    "header",
    "hgroup",
    "main",
    "pre",
    "section",
    "summary",
}
_INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "bdi",
    "bdo",
    "cite",
    "code",
    "data",
    "del",
    "dfn",
    "em",
    "i",
    "ins",
    "kbd",
    "label",
    "mark",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
}
_TABLE_WRAPPER_TAGS = {"thead", "tbody", "tfoot"}


class HtmlNormalizationError(ValueError):
    """Stable validation failure containing only its approved code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NormalizationStatus(str, Enum):
    SUCCESS = "success"
    NO_MEANINGFUL_CONTENT = "no_meaningful_content"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, repr=False)
class NormalizedSection:
    heading: Optional[str]
    text: str

    def __post_init__(self) -> None:
        if self.heading is not None and not _is_canonical_single_line(self.heading):
            raise HtmlNormalizationError("NORMALIZED_SECTION_HEADING_INVALID")
        if not _is_canonical_section_text(self.text):
            raise HtmlNormalizationError("NORMALIZED_SECTION_TEXT_INVALID")

    def __repr__(self) -> str:
        return "NormalizedSection(heading=<redacted>, text=<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class HtmlNormalizationResult:
    status: NormalizationStatus
    title: Optional[str]
    clean_body: Optional[str]
    sections: Tuple[NormalizedSection, ...]
    parser_version: str
    diagnostic_codes: Tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_normalization_result(self)

    def __repr__(self) -> str:
        return (
            "HtmlNormalizationResult("
            f"status={self.status.value!r}, "
            "title=<redacted>, clean_body=<redacted>, "
            f"section_count={len(self.sections)}, "
            f"parser_version={self.parser_version!r}, "
            f"diagnostic_codes={self.diagnostic_codes!r})"
        )

    __str__ = __repr__


@dataclass
class _TextCapture:
    tag: str
    kind: str
    parts: List[object] = field(default_factory=list)


@dataclass
class _ListState:
    tag: str
    items: List[Tuple[int, str]] = field(default_factory=list)
    current_parts: Optional[List[object]] = None
    current_number: Optional[int] = None
    source_item_count: int = 0
    nested_depth: int = 0
    unsupported: bool = False


@dataclass
class _TableState:
    rows: List[List[str]] = field(default_factory=list)
    current_row: Optional[List[str]] = None
    current_cell: Optional[List[object]] = None
    caption_parts: Optional[List[object]] = None
    caption: Optional[str] = None
    nested_depth: int = 0
    unsupported: bool = False


def normalize_html(
    html: str,
    *,
    expected_parser_version: str,
) -> HtmlNormalizationResult:
    if (
        type(expected_parser_version) is not str
        or expected_parser_version != HTML_NORMALIZER_VERSION
    ):
        raise HtmlNormalizationError("PARSER_VERSION_UNSUPPORTED")
    if type(html) is not str:
        raise HtmlNormalizationError("HTML_INPUT_TEXT_REQUIRED")
    try:
        encoded_html = html.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise HtmlNormalizationError("HTML_TEXT_INVALID") from None

    if len(encoded_html) > HTML_NORMALIZER_MAX_INPUT_BYTES:
        return HtmlNormalizationResult(
            status=NormalizationStatus.NEEDS_REVIEW,
            title=None,
            clean_body=None,
            sections=(),
            parser_version=HTML_NORMALIZER_VERSION,
            diagnostic_codes=("INPUT_TOO_LARGE", "NEEDS_REVIEW"),
        )

    parser = _SyntheticHtmlParser()
    parser.feed(html)
    parser.close()
    parser.finish()
    return parser.result()


class _SyntheticHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._events: List[Tuple[str, str]] = []
        self._root_parts: List[object] = []
        self._capture: Optional[_TextCapture] = None
        self._title_capture: Optional[List[object]] = None
        self._document_title: Optional[str] = None
        self._first_h1: Optional[str] = None
        self._head_depth = 0
        self._suppression_stack: List[str] = []
        self._list: Optional[_ListState] = None
        self._table: Optional[_TableState] = None
        self._diagnostics: Set[str] = set()
        self._finished = False

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        if self._suppression_stack:
            if tag not in _VOID_TAGS:
                self._suppression_stack.append(tag)
            return

        if _should_drop_subtree(tag, attrs):
            if tag not in _VOID_TAGS:
                self._suppression_stack = [tag]
            return

        if self._title_capture is not None:
            if tag == "br":
                self._title_capture.append(_LINE_BREAK)
            elif tag == "title":
                self._mark_malformed()
            return

        if tag == "head":
            self._flush_root()
            self._head_depth += 1
            return
        if tag == "title":
            self._title_capture = []
            return
        if self._head_depth:
            return

        if self._table is not None:
            self._handle_table_start(tag, attrs)
            return
        if tag in _TABLE_STRUCTURAL_TAGS:
            self._mark_malformed()
            return
        if self._list is not None:
            self._handle_list_start(tag, attrs)
            return
        if tag in _LIST_STRUCTURAL_TAGS:
            self._mark_malformed()
            return

        if tag == "table":
            self._prepare_for_block_start(tag)
            self._table = _TableState()
            return
        if tag in {"ul", "ol"}:
            self._prepare_for_block_start(tag)
            list_state = _ListState(tag=tag)
            if tag == "ol":
                start = _attribute_value(attrs, "start")
                if _has_attribute(attrs, "start") and start != "1":
                    self._mark_list_unsupported(list_state)
            self._list = list_state
            return
        if tag in _HEADING_TAGS:
            self._prepare_for_block_start(tag)
            self._capture = _TextCapture(tag=tag, kind="heading")
            return
        if tag in {"p", "figcaption"}:
            self._prepare_for_block_start(tag)
            self._capture = _TextCapture(tag=tag, kind="text")
            return
        if tag == "br":
            self._append_line_break()
            return
        if tag == "img":
            return
        if tag in _BLOCK_CONTAINER_TAGS:
            self._prepare_for_container_boundary()

    def handle_startendtag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._suppression_stack:
            if tag != self._suppression_stack[-1]:
                self._mark_malformed()
                return
            self._suppression_stack.pop()
            return

        if self._title_capture is not None:
            if tag == "title":
                self._finish_title()
            elif tag == "head":
                self._mark_malformed()
                self._finish_title()
                self._head_depth = max(0, self._head_depth - 1)
            return

        if self._head_depth:
            if tag == "head":
                self._head_depth -= 1
            return

        if tag == "embed":
            self._mark_malformed()
            return
        if self._table is not None:
            self._handle_table_end(tag)
            return
        if tag in _TABLE_STRUCTURAL_TAGS or tag == "table":
            self._mark_malformed()
            return
        if self._list is not None:
            self._handle_list_end(tag)
            return
        if tag in _LIST_STRUCTURAL_TAGS or tag in {"ul", "ol"}:
            self._mark_malformed()
            return

        if self._capture is not None:
            if tag == self._capture.tag:
                self._finish_capture()
                return
            if tag in _HEADING_TAGS or tag in {
                "p",
                "figcaption",
                "table",
                "ul",
                "ol",
            } or tag in _BLOCK_CONTAINER_TAGS:
                if self._capture.kind == "heading":
                    self._mark_malformed()
                self._finish_capture()

        if tag in _BLOCK_CONTAINER_TAGS:
            self._flush_root()

    def handle_data(self, data: str) -> None:
        if self._suppression_stack:
            return
        if self._title_capture is not None:
            self._title_capture.append(data)
            return
        if self._head_depth:
            return
        if self._table is not None:
            self._handle_table_data(data)
            return
        if self._list is not None:
            self._handle_list_data(data)
            return
        if self._capture is not None:
            self._capture.parts.append(data)
            return
        self._root_parts.append(data)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True

        if self._suppression_stack:
            self._mark_malformed()
            self._suppression_stack.clear()
        if self._title_capture is not None:
            self._mark_malformed()
            self._finish_title()
        if self._head_depth:
            self._mark_malformed()
            self._head_depth = 0
        if self._table is not None:
            self._mark_malformed()
            self._table = None
        if self._list is not None:
            self._mark_malformed()
            self._list = None
        if self._capture is not None:
            if self._capture.kind == "heading":
                self._mark_malformed()
            self._finish_capture()
        self._flush_root()

    def result(self) -> HtmlNormalizationResult:
        title = self._first_h1 or self._document_title
        sections = self._build_sections()

        if "NEEDS_REVIEW" in self._diagnostics:
            return HtmlNormalizationResult(
                status=NormalizationStatus.NEEDS_REVIEW,
                title=title,
                clean_body=None,
                sections=(),
                parser_version=HTML_NORMALIZER_VERSION,
                diagnostic_codes=self._ordered_diagnostics(),
            )
        if not sections:
            self._diagnostics.add("NO_MEANINGFUL_CONTENT")
            return HtmlNormalizationResult(
                status=NormalizationStatus.NO_MEANINGFUL_CONTENT,
                title=title,
                clean_body=None,
                sections=(),
                parser_version=HTML_NORMALIZER_VERSION,
                diagnostic_codes=self._ordered_diagnostics(),
            )

        clean_body = _render_sections(sections)
        return HtmlNormalizationResult(
            status=NormalizationStatus.SUCCESS,
            title=title,
            clean_body=clean_body,
            sections=sections,
            parser_version=HTML_NORMALIZER_VERSION,
            diagnostic_codes=(),
        )

    def _prepare_for_block_start(self, tag: str) -> None:
        if self._capture is not None:
            if self._capture.tag == "p" and tag != "figcaption":
                self._finish_capture()
            else:
                if self._capture.kind == "heading":
                    self._mark_malformed()
                else:
                    self._mark_malformed()
                self._finish_capture()
        self._flush_root()

    def _prepare_for_container_boundary(self) -> None:
        if self._capture is not None:
            if self._capture.kind == "heading":
                self._mark_malformed()
            self._finish_capture()
        self._flush_root()

    def _append_line_break(self) -> None:
        if self._capture is not None:
            self._capture.parts.append(_LINE_BREAK)
        else:
            self._root_parts.append(_LINE_BREAK)

    def _finish_title(self) -> None:
        if self._title_capture is None:
            return
        title = _normalize_parts(self._title_capture)
        if title and not _is_canonical_single_line(title):
            self._mark_malformed()
            title = None
        if title and self._document_title is None:
            self._document_title = title
        self._title_capture = None

    def _finish_capture(self) -> None:
        if self._capture is None:
            return
        capture = self._capture
        self._capture = None
        text = _normalize_parts(capture.parts)
        if not text:
            return
        if capture.kind == "heading":
            if self._append_event("heading", text):
                if capture.tag == "h1" and self._first_h1 is None:
                    self._first_h1 = text
        else:
            self._append_event("text", text)

    def _append_event(self, kind: str, text: str) -> bool:
        canonical = (
            _is_canonical_single_line(text)
            if kind == "heading"
            else _is_canonical_section_text(text)
        )
        if not canonical:
            self._mark_malformed()
            return False
        self._events.append((kind, text))
        return True

    def _flush_root(self) -> None:
        text = _normalize_parts(self._root_parts)
        self._root_parts.clear()
        if text:
            self._append_event("text", text)

    def _handle_list_start(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        state = self._list
        if state is None:
            return
        if state.nested_depth:
            if tag in {"ul", "ol"}:
                state.nested_depth += 1
            return
        if tag in {"ul", "ol"}:
            state.nested_depth = 1
            self._mark_list_unsupported(state)
            return
        if tag == "li":
            if state.current_parts is not None:
                self._finish_list_item(state)
            state.source_item_count += 1
            state.current_number = state.source_item_count
            state.current_parts = []
            value = _attribute_value(attrs, "value")
            if _has_attribute(attrs, "value"):
                if state.tag != "ol" or value != str(state.current_number):
                    self._mark_list_unsupported(state)
            return
        if tag == "br" and state.current_parts is not None:
            state.current_parts.append(_LINE_BREAK)
            return
        if tag == "img":
            return
        if tag in _INLINE_TAGS:
            return
        if tag not in _VOID_TAGS:
            self._mark_list_unsupported(state)

    def _handle_list_end(self, tag: str) -> None:
        state = self._list
        if state is None:
            return
        if state.nested_depth:
            if tag in {"ul", "ol"}:
                state.nested_depth -= 1
            return
        if tag == "li":
            if state.current_parts is None:
                self._mark_malformed()
                return
            self._finish_list_item(state)
            return
        if tag == state.tag:
            self._finish_list_item(state)
            self._list = None
            if state.unsupported:
                return
            lines = [
                (f"• {text}" if state.tag == "ul" else f"{number}. {text}")
                for number, text in state.items
            ]
            if lines:
                self._append_event("text", "\n".join(lines))

    def _handle_list_data(self, data: str) -> None:
        state = self._list
        if state is None or state.nested_depth:
            return
        if state.current_parts is not None:
            state.current_parts.append(data)
        elif _normalize_parts([data]):
            self._mark_list_unsupported(state)

    def _finish_list_item(self, state: _ListState) -> None:
        if state.current_parts is None or state.current_number is None:
            return
        text = _normalize_parts(state.current_parts)
        if text:
            state.items.append((state.current_number, text))
        state.current_parts = None
        state.current_number = None

    def _mark_list_unsupported(self, state: _ListState) -> None:
        state.unsupported = True
        self._diagnostics.add("NEEDS_REVIEW")

    def _handle_table_start(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        state = self._table
        if state is None:
            return
        if state.nested_depth:
            if tag == "table":
                state.nested_depth += 1
            return
        if tag == "table":
            state.nested_depth = 1
            self._mark_table_unsupported(state)
            return
        if tag == "caption":
            if (
                state.caption_parts is not None
                or state.caption is not None
                or state.current_row is not None
            ):
                self._mark_table_unsupported(state)
            state.caption_parts = []
            return
        if tag == "tr":
            if state.caption_parts is not None:
                self._finish_table_caption(state)
            if state.current_cell is not None:
                self._finish_table_cell(state)
            if state.current_row is not None:
                self._finish_table_row(state)
            state.current_row = []
            return
        if tag in {"td", "th"}:
            if state.current_row is None:
                self._mark_malformed()
                state.current_row = []
            if state.current_cell is not None:
                self._finish_table_cell(state)
            state.current_cell = []
            for attribute in ("rowspan", "colspan"):
                value = _attribute_value(attrs, attribute)
                if _has_attribute(attrs, attribute) and value != "1":
                    self._mark_table_unsupported(state)
            return
        if tag == "br":
            if state.current_cell is not None:
                self._mark_table_unsupported(state)
                state.current_cell.append(_LINE_BREAK)
            elif state.caption_parts is not None:
                state.caption_parts.append(_LINE_BREAK)
            else:
                self._mark_table_unsupported(state)
            return
        if tag == "img":
            return
        if tag in _INLINE_TAGS:
            return
        if tag in _TABLE_WRAPPER_TAGS:
            if state.current_cell is not None:
                self._mark_table_unsupported(state)
            return
        if tag not in _VOID_TAGS:
            self._mark_table_unsupported(state)

    def _handle_table_end(self, tag: str) -> None:
        state = self._table
        if state is None:
            return
        if state.nested_depth:
            if tag == "table":
                state.nested_depth -= 1
            return
        if tag in {"td", "th"}:
            if state.current_cell is None:
                self._mark_malformed()
                return
            self._finish_table_cell(state)
            return
        if tag == "tr":
            if state.current_row is None:
                self._mark_malformed()
                return
            self._finish_table_cell(state)
            self._finish_table_row(state)
            return
        if tag == "caption":
            if state.caption_parts is None:
                self._mark_malformed()
                return
            self._finish_table_caption(state)
            return
        if tag == "table":
            self._finish_table_cell(state)
            self._finish_table_row(state)
            self._finish_table_caption(state)
            self._table = None
            self._emit_table(state)

    def _handle_table_data(self, data: str) -> None:
        state = self._table
        if state is None or state.nested_depth:
            return
        if state.current_cell is not None:
            state.current_cell.append(data)
        elif state.caption_parts is not None:
            state.caption_parts.append(data)
        elif _normalize_parts([data]):
            self._mark_table_unsupported(state)

    def _finish_table_cell(self, state: _TableState) -> None:
        if state.current_cell is None:
            return
        if state.current_row is None:
            self._mark_table_unsupported(state)
            state.current_row = []
        state.current_row.append(_normalize_parts(state.current_cell))
        state.current_cell = None

    def _finish_table_row(self, state: _TableState) -> None:
        if state.current_row is None:
            return
        if not state.current_row:
            self._mark_table_unsupported(state)
        else:
            state.rows.append(state.current_row)
        state.current_row = None

    def _finish_table_caption(self, state: _TableState) -> None:
        if state.caption_parts is None:
            return
        caption = _normalize_parts(state.caption_parts)
        if caption:
            state.caption = caption
        state.caption_parts = None

    def _emit_table(self, state: _TableState) -> None:
        if state.rows:
            width = len(state.rows[0])
            if width == 0 or any(len(row) != width for row in state.rows):
                self._mark_table_unsupported(state)
        if state.unsupported:
            return
        if state.caption:
            self._append_event("text", state.caption)
        if state.rows and any(cell for row in state.rows for cell in row):
            table_text = "\n".join("\t".join(row) for row in state.rows)
            self._append_event("text", table_text)

    def _mark_table_unsupported(self, state: _TableState) -> None:
        state.unsupported = True
        self._diagnostics.add("UNSUPPORTED_TABLE_STRUCTURE")
        self._diagnostics.add("NEEDS_REVIEW")

    def _build_sections(self) -> Tuple[NormalizedSection, ...]:
        sections: List[NormalizedSection] = []
        heading: Optional[str] = None
        body_blocks: List[str] = []
        heading_open = False

        for kind, text in self._events:
            if kind == "heading":
                if heading_open and not body_blocks:
                    self._mark_malformed()
                if body_blocks:
                    sections.append(
                        NormalizedSection(
                            heading=heading,
                            text="\n\n".join(body_blocks),
                        )
                    )
                    body_blocks = []
                heading = text
                heading_open = True
            else:
                body_blocks.append(text)

        if heading_open and not body_blocks:
            self._mark_malformed()
        if body_blocks:
            sections.append(
                NormalizedSection(
                    heading=heading,
                    text="\n\n".join(body_blocks),
                )
            )
        return tuple(sections)

    def _mark_malformed(self) -> None:
        self._diagnostics.add("MALFORMED_STRUCTURE")
        self._diagnostics.add("NEEDS_REVIEW")

    def _ordered_diagnostics(self) -> Tuple[str, ...]:
        return tuple(
            code for code in DIAGNOSTIC_CODE_ORDER if code in self._diagnostics
        )


def _attribute_value(
    attrs: Sequence[Tuple[str, Optional[str]]],
    name: str,
) -> Optional[str]:
    for attribute_name, value in attrs:
        if attribute_name == name:
            return value
    return None


def _has_attribute(
    attrs: Sequence[Tuple[str, Optional[str]]],
    name: str,
) -> bool:
    return any(attribute_name == name for attribute_name, _ in attrs)


def _should_drop_subtree(
    tag: str,
    attrs: Sequence[Tuple[str, Optional[str]]],
) -> bool:
    if tag in _DROP_SUBTREE_TAGS:
        return True
    if _has_attribute(attrs, "hidden"):
        return True
    aria_hidden = _attribute_value(attrs, "aria-hidden")
    if aria_hidden is not None:
        normalized = aria_hidden.strip(" \t\n\f\r").lower()
        if normalized == "true":
            return True
    marker = _attribute_value(attrs, "data-mka-boilerplate")
    return marker in _SYNTHETIC_BOILERPLATE_VALUES


def _normalize_parts(parts: Sequence[object]) -> str:
    segments: List[List[str]] = [[]]
    for part in parts:
        if part is _LINE_BREAK:
            segments.append([])
        else:
            segments[-1].append(str(part))

    normalized_segments = []
    for segment in segments:
        text = "".join(segment).replace("\u00a0", " ")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = _HTML_WHITESPACE.sub(" ", text).strip(" ")
        normalized_segments.append(text)
    normalized = "\n".join(normalized_segments).strip(" \n")
    return unicodedata.normalize("NFC", normalized)


def _render_section(section: NormalizedSection) -> str:
    if section.heading is None:
        return section.text
    return f"{section.heading}\n{section.text}"


def _render_sections(sections: Sequence[NormalizedSection]) -> str:
    return "\n\n".join(_render_section(section) for section in sections)


def _is_canonical_single_line(value: object) -> bool:
    return bool(
        type(value) is str
        and value
        and value == value.strip()
        and "\u00a0" not in value
        and unicodedata.normalize("NFC", value) == value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _is_canonical_section_text(value: object) -> bool:
    if type(value) is not str or not value or not value.strip(" \n\t"):
        return False
    if (value[0].isspace() and value[0] != "\t") or (
        value[-1].isspace() and value[-1] != "\t"
    ):
        return False
    if "\r" in value or "\u00a0" in value:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    return not any(
        (ord(character) < 32 and character not in "\n\t")
        or ord(character) == 127
        for character in value
    )


def _validate_normalization_result(result: HtmlNormalizationResult) -> None:
    if type(result.status) is not NormalizationStatus:
        raise HtmlNormalizationError("HTML_NORMALIZATION_RESULT_STATUS_INVALID")
    if result.title is not None and not _is_canonical_single_line(result.title):
        raise HtmlNormalizationError("HTML_NORMALIZATION_RESULT_TITLE_INVALID")
    if type(result.sections) is not tuple or any(
        type(section) is not NormalizedSection for section in result.sections
    ):
        raise HtmlNormalizationError("HTML_NORMALIZATION_RESULT_SECTIONS_INVALID")
    if (
        type(result.parser_version) is not str
        or result.parser_version != HTML_NORMALIZER_VERSION
    ):
        raise HtmlNormalizationError(
            "HTML_NORMALIZATION_RESULT_PARSER_VERSION_INVALID"
        )
    if not _valid_diagnostics(result.diagnostic_codes):
        raise HtmlNormalizationError("HTML_NORMALIZATION_RESULT_DIAGNOSTICS_INVALID")
    if result.clean_body is not None and type(result.clean_body) is not str:
        raise HtmlNormalizationError("HTML_NORMALIZATION_RESULT_CLEAN_BODY_INVALID")

    if result.status is NormalizationStatus.SUCCESS:
        if (
            not result.clean_body
            or not result.clean_body.strip()
            or not result.sections
            or result.diagnostic_codes != ()
        ):
            raise HtmlNormalizationError(
                "HTML_NORMALIZATION_RESULT_STATUS_BODY_INVALID"
            )
        if result.clean_body != _render_sections(result.sections):
            raise HtmlNormalizationError(
                "HTML_NORMALIZATION_RESULT_CLEAN_BODY_MISMATCH"
            )
        return

    if result.status is NormalizationStatus.NO_MEANINGFUL_CONTENT:
        if (
            result.clean_body is not None
            or result.sections != ()
            or result.diagnostic_codes != ("NO_MEANINGFUL_CONTENT",)
        ):
            raise HtmlNormalizationError(
                "HTML_NORMALIZATION_RESULT_STATUS_BODY_INVALID"
            )
        return

    if (
        result.clean_body is not None
        or result.sections != ()
        or not result.diagnostic_codes
        or "NEEDS_REVIEW" not in result.diagnostic_codes
        or "NO_MEANINGFUL_CONTENT" in result.diagnostic_codes
    ):
        raise HtmlNormalizationError("HTML_NORMALIZATION_RESULT_STATUS_BODY_INVALID")


def _valid_diagnostics(diagnostic_codes: object) -> bool:
    if type(diagnostic_codes) is not tuple:
        return False
    if any(type(code) is not str for code in diagnostic_codes):
        return False
    if len(set(diagnostic_codes)) != len(diagnostic_codes):
        return False
    if any(code not in DIAGNOSTIC_CODE_ORDER for code in diagnostic_codes):
        return False
    return diagnostic_codes == tuple(
        code for code in DIAGNOSTIC_CODE_ORDER if code in diagnostic_codes
    )


__all__ = [
    "DIAGNOSTIC_CODE_ORDER",
    "HTML_NORMALIZER_MAX_INPUT_BYTES",
    "HTML_NORMALIZER_VERSION",
    "HtmlNormalizationError",
    "HtmlNormalizationResult",
    "NormalizationStatus",
    "NormalizedSection",
    "normalize_html",
]
