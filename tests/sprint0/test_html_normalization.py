from __future__ import annotations

import hashlib
import inspect
import socket
import urllib.request
from dataclasses import FrozenInstanceError, fields, replace

import pytest

import marketing_knowledge_agent.html_normalization as html_normalization
from marketing_knowledge_agent.html_normalization import (
    DIAGNOSTIC_CODE_ORDER,
    HTML_NORMALIZER_VERSION,
    HTML_NORMALIZER_MAX_INPUT_BYTES,
    HtmlNormalizationError,
    HtmlNormalizationResult,
    NormalizationStatus,
    NormalizedSection,
    normalize_html,
)


def _normalize(html: str) -> HtmlNormalizationResult:
    return normalize_html(
        html,
        expected_parser_version=HTML_NORMALIZER_VERSION,
    )


def test_exact_version_status_diagnostics_and_immutable_contract():
    assert HTML_NORMALIZER_VERSION == "html-normalizer-v1"
    assert [(item.name, item.value) for item in NormalizationStatus] == [
        ("SUCCESS", "success"),
        ("NO_MEANINGFUL_CONTENT", "no_meaningful_content"),
        ("NEEDS_REVIEW", "needs_review"),
    ]
    assert DIAGNOSTIC_CODE_ORDER == (
        "NO_MEANINGFUL_CONTENT",
        "UNSUPPORTED_TABLE_STRUCTURE",
        "MALFORMED_STRUCTURE",
        "INPUT_TOO_LARGE",
        "NEEDS_REVIEW",
    )
    assert tuple(field.name for field in fields(NormalizedSection)) == (
        "heading",
        "text",
    )
    assert tuple(field.name for field in fields(HtmlNormalizationResult)) == (
        "status",
        "title",
        "clean_body",
        "sections",
        "parser_version",
        "diagnostic_codes",
    )

    result = _normalize("<p>Body</p>")
    with pytest.raises(FrozenInstanceError):
        result.clean_body = "Changed"
    with pytest.raises(FrozenInstanceError):
        result.sections[0].text = "Changed"


def test_parser_version_mismatch_is_payload_free():
    sentinel = "RAW_HTML_SENTINEL_VERSION_91C7"
    with pytest.raises(HtmlNormalizationError) as captured:
        normalize_html(
            f"<p>{sentinel}</p>",
            expected_parser_version="html-normalizer-v999",
        )

    assert captured.value.code == "PARSER_VERSION_UNSUPPORTED"
    assert str(captured.value) == "PARSER_VERSION_UNSUPPORTED"
    assert sentinel not in repr(captured.value)


def test_basic_h1_wins_title_and_remains_a_source_heading():
    result = _normalize(
        "<html><head><title>Document title</title></head>"
        "<body><h1>Brand Alpha</h1><p>Feature Omega</p></body></html>"
    )

    assert result.status is NormalizationStatus.SUCCESS
    assert result.title == "Brand Alpha"
    assert result.clean_body == "Brand Alpha\nFeature Omega"
    assert result.sections == (
        NormalizedSection(heading="Brand Alpha", text="Feature Omega"),
    )
    assert result.parser_version == HTML_NORMALIZER_VERSION
    assert result.diagnostic_codes == ()


def test_document_title_is_fallback_when_no_meaningful_h1():
    result = _normalize(
        "<html><head><title>  Document   Alpha  </title></head>"
        "<body><h1> </h1><p>Body</p></body></html>"
    )

    assert result.title == "Document Alpha"
    assert result.clean_body == "Body"
    assert result.sections == (NormalizedSection(heading=None, text="Body"),)


def test_clean_body_exact_rendering_and_paragraph_boundaries():
    result = _normalize(
        "<p>Intro one</p><p>Intro two</p>"
        "<h2>品牌故事</h2><p>正文一</p><p>正文二</p>"
        "<h3>營運成果</h3><p>300%</p>"
    )

    assert result.sections == (
        NormalizedSection(heading=None, text="Intro one\n\nIntro two"),
        NormalizedSection(heading="品牌故事", text="正文一\n\n正文二"),
        NormalizedSection(heading="營運成果", text="300%"),
    )
    assert result.clean_body == (
        "Intro one\n\nIntro two\n\n"
        "品牌故事\n正文一\n\n正文二\n\n"
        "營運成果\n300%"
    )


def test_sections_preserve_source_order_without_semantic_reordering():
    result = _normalize(
        "<h2>Zulu</h2><p>First</p>"
        "<h2>Alpha</h2><p>Second</p>"
    )

    assert [section.heading for section in result.sections] == ["Zulu", "Alpha"]
    assert [section.text for section in result.sections] == ["First", "Second"]


def test_unicode_is_normalized_to_nfc_without_compatibility_changes():
    result = _normalize(
        "<title>Cafe\u0301 ①</title><h1>Cafe\u0301 ①</h1>"
        "<p>Feature O\u0308mega 32 units</p>"
    )

    assert result.title == "Café ①"
    assert result.clean_body == "Café ①\nFeature Ömega 32 units"
    assert "1" not in result.title


def test_crlf_cr_and_html_whitespace_runs_collapse_without_carriage_returns():
    result = _normalize("<p>Brand\r\n  Alpha\r\tFeature\n Omega</p>")

    assert result.clean_body == "Brand Alpha Feature Omega"
    assert "\r" not in result.clean_body


def test_inline_flow_preserves_existing_single_space_and_does_not_invent_one():
    result = _normalize(
        "<p>Brand <strong>Alpha</strong> <em>300%</em></p>"
        "<p>Feature<span>Omega</span></p>"
    )

    assert result.clean_body == "Brand Alpha 300%\n\nFeatureOmega"


def test_nbsp_becomes_u0020():
    result = _normalize("<p>Brand&nbsp;&nbsp;Alpha\u00a0Feature</p>")

    assert result.clean_body == "Brand Alpha Feature"
    assert "\u00a0" not in result.clean_body


def test_br_is_exactly_one_lf_inside_a_block():
    result = _normalize("<p>Line one<br>Line two<br/>Line three</p>")

    assert result.clean_body == "Line one\nLine two\nLine three"


def test_flat_unordered_list_uses_bullets_and_line_delimiters():
    result = _normalize("<p>Intro</p><ul><li>Brand Alpha</li><li>32 units</li></ul>")

    assert result.clean_body == "Intro\n\n• Brand Alpha\n• 32 units"


def test_flat_ordered_list_uses_default_sequence():
    result = _normalize(
        '<ol start="1"><li value="1">First</li><li value="2">Second</li></ol>'
    )

    assert result.status is NormalizationStatus.SUCCESS
    assert result.clean_body == "1. First\n2. Second"


def test_nested_list_requires_review_without_provisional_body():
    result = _normalize("<ul><li>Outer<ul><li>Inner</li></ul></li></ul>")

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == ("NEEDS_REVIEW",)


@pytest.mark.parametrize(
    "html",
    [
        '<ol start="2"><li>Second</li></ol>',
        "<ol start><li>First</li></ol>",
        '<ol><li value="4">Fourth</li></ol>',
        "<ol><li value>First</li></ol>",
        '<ul><li value="1">Item</li></ul>',
    ],
)
def test_nondefault_ol_start_or_li_value_requires_review(html):
    result = _normalize(html)

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.diagnostic_codes == ("NEEDS_REVIEW",)


def test_simple_rectangular_table_uses_tabs_and_lfs():
    result = _normalize(
        "<table><thead><tr><th>Metric</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>Growth</td><td>300%</td></tr></tbody></table>"
    )

    assert result.clean_body == "Metric\tValue\nGrowth\t300%"


def test_inconsistent_table_requires_review():
    result = _normalize(
        "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>"
    )

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.diagnostic_codes == (
        "UNSUPPORTED_TABLE_STRUCTURE",
        "NEEDS_REVIEW",
    )


@pytest.mark.parametrize(
    "attribute",
    [
        'rowspan="2"',
        'colspan="2"',
        'rowspan="0"',
        'colspan="01"',
        "rowspan",
        "colspan",
    ],
)
def test_nonunit_rowspan_or_colspan_requires_review(attribute):
    result = _normalize(f"<table><tr><td {attribute}>A</td></tr></table>")

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.diagnostic_codes == (
        "UNSUPPORTED_TABLE_STRUCTURE",
        "NEEDS_REVIEW",
    )


def test_unit_rowspan_and_colspan_are_accepted():
    result = _normalize(
        '<table><tr><td rowspan="1">A</td><td colspan="1">B</td></tr></table>'
    )

    assert result.clean_body == "A\tB"


def test_nested_table_requires_review():
    result = _normalize(
        "<table><tr><td>Outer<table><tr><td>Inner</td></tr></table></td></tr></table>"
    )

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.diagnostic_codes == (
        "UNSUPPORTED_TABLE_STRUCTURE",
        "NEEDS_REVIEW",
    )


@pytest.mark.parametrize(
    "cell_html",
    [
        "A<br>B",
        "<p>A</p>",
        "<ul><li>A</li></ul>",
        "<div>A</div>",
    ],
)
def test_table_cell_block_or_newline_structure_requires_review(cell_html):
    result = _normalize(f"<table><tr><td>{cell_html}</td></tr></table>")

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.diagnostic_codes == (
        "UNSUPPORTED_TABLE_STRUCTURE",
        "NEEDS_REVIEW",
    )


def test_meaningful_caption_is_a_source_block_before_table():
    result = _normalize(
        "<table><caption>Monthly metrics</caption>"
        "<tr><td>Units</td><td>32</td></tr></table>"
    )

    assert result.clean_body == "Monthly metrics\n\nUnits\t32"
    assert "Caption:" not in result.clean_body


def test_anchor_preserves_visible_text_but_not_href():
    href_sentinel = "https://example.test/HREF_SENTINEL_84A1"
    result = _normalize(
        f'<p><a href="{href_sentinel}">Brand Alpha</a> '
        '<a href="https://example.test/visible">https://example.test/visible</a>'
        '<a href="https://example.test/empty"></a></p>'
    )

    assert result.clean_body == "Brand Alpha https://example.test/visible"
    assert href_sentinel not in result.clean_body


def test_image_and_alt_do_not_produce_body_text():
    result = _normalize(
        '<p>Before<img src="synthetic.png" alt="ALT_SENTINEL_2B11">After</p>'
    )

    assert result.clean_body == "BeforeAfter"
    assert "ALT_SENTINEL_2B11" not in result.clean_body


def test_meaningful_figcaption_is_preserved_without_an_artificial_label():
    result = _normalize(
        '<figure><img src="synthetic.png" alt="Ignored">'
        "<figcaption>32 units</figcaption></figure>"
    )

    assert result.clean_body == "32 units"
    assert "Figure:" not in result.clean_body


def test_script_and_style_subtrees_are_dropped():
    result = _normalize(
        "<p>Before<script>DROP_SCRIPT</script>Mid"
        "<style>DROP_STYLE</style>After</p>"
    )

    assert result.clean_body == "BeforeMidAfter"
    assert "DROP_" not in result.clean_body


@pytest.mark.parametrize("tag", ["iframe", "object", "canvas", "svg"])
def test_active_content_subtrees_are_dropped(tag):
    result = _normalize(f"<p>Before<{tag}>DROP_ACTIVE</{tag}>After</p>")

    assert result.clean_body == "BeforeAfter"


def test_embed_is_dropped_without_suppressing_following_content():
    result = _normalize("<p>Before<embed src='synthetic'>After</p>")

    assert result.clean_body == "BeforeAfter"


def test_self_closing_embed_is_an_ordinary_void_element():
    result = _normalize("<p>Before<embed/>After</p>")

    assert result.status is NormalizationStatus.SUCCESS
    assert result.clean_body == "BeforeAfter"


def test_nav_and_footer_subtrees_are_dropped():
    result = _normalize(
        "<nav><p>DROP_NAV</p></nav><main><p>Keep main</p></main>"
        "<footer><p>DROP_FOOTER</p></footer>"
    )

    assert result.clean_body == "Keep main"


def test_header_and_aside_are_preserved_by_default():
    result = _normalize(
        "<header><p>Header source</p></header>"
        "<aside><p>Aside source</p></aside>"
    )

    assert result.clean_body == "Header source\n\nAside source"


@pytest.mark.parametrize(
    "marker",
    [
        "cookie",
        "ad",
        "social",
        "related",
        "tracking",
        "duplicate-header",
        "duplicate-footer",
        "cta",
    ],
)
def test_exact_synthetic_boilerplate_marker_drops_subtree(marker):
    result = _normalize(
        f'<div data-mka-boilerplate="{marker}"><p>DROP_MARKER</p></div>'
        "<p>Keep</p>"
    )

    assert result.clean_body == "Keep"


@pytest.mark.parametrize("marker", ["Cookie", " cookie", "cookie-banner", "cta "])
def test_nonmatching_synthetic_marker_is_preserved(marker):
    result = _normalize(
        f'<div data-mka-boilerplate="{marker}"><p>Preserve marker body</p></div>'
    )

    assert result.clean_body == "Preserve marker body"


@pytest.mark.parametrize("attribute", ["hidden", 'hidden="false"'])
def test_hidden_attribute_presence_drops_subtree(attribute):
    result = _normalize(f"<p {attribute}>DROP_HIDDEN</p><p>Keep</p>")

    assert result.clean_body == "Keep"


@pytest.mark.parametrize("value", ["true", " TRUE ", "\tTrUe\n"])
def test_aria_hidden_true_is_trimmed_and_ascii_case_insensitive(value):
    result = _normalize(
        f'<p aria-hidden="{value}">DROP_ARIA</p><p>Keep</p>'
    )

    assert result.clean_body == "Keep"


def test_style_hidden_is_not_guessed():
    result = _normalize(
        '<p style="display:none; visibility:hidden" class="hidden">'
        "Preserve style body</p>"
    )

    assert result.clean_body == "Preserve style body"


def test_empty_body_retains_title_and_returns_no_meaningful_content():
    result = _normalize(
        "<html><head><title>Document Alpha</title></head>"
        "<body><script>ignored</script><p> </p></body></html>"
    )

    assert result.status is NormalizationStatus.NO_MEANINGFUL_CONTENT
    assert result.title == "Document Alpha"
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == ("NO_MEANINGFUL_CONTENT",)


def test_oversized_utf8_input_is_not_partially_parsed():
    html = "é" * (HTML_NORMALIZER_MAX_INPUT_BYTES // 2 + 1)

    result = _normalize(html)

    assert len(html.encode("utf-8")) > 1_048_576
    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.title is None
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == ("INPUT_TOO_LARGE", "NEEDS_REVIEW")


@pytest.mark.parametrize("value", [b"<p>bytes</p>", None, 1, True])
def test_input_requires_exact_str(value):
    with pytest.raises(HtmlNormalizationError) as captured:
        normalize_html(value, expected_parser_version=HTML_NORMALIZER_VERSION)

    assert captured.value.code == "HTML_INPUT_TEXT_REQUIRED"
    assert str(captured.value) == "HTML_INPUT_TEXT_REQUIRED"


def test_invalid_strict_utf8_text_is_rejected_without_payload():
    sentinel = "\ud800"
    with pytest.raises(HtmlNormalizationError) as captured:
        _normalize(f"<p>{sentinel}</p>")

    assert captured.value.code == "HTML_TEXT_INVALID"
    assert str(captured.value) == "HTML_TEXT_INVALID"


def test_malformed_but_safe_unclosed_paragraph_normalizes_deterministically():
    result = _normalize("<h1>Brand Alpha</h1><p>Feature Omega")

    assert result.status is NormalizationStatus.SUCCESS
    assert result.title == "Brand Alpha"
    assert result.clean_body == "Brand Alpha\nFeature Omega"


def test_malformed_unsafe_heading_boundary_requires_review():
    result = _normalize("<h1>Brand Alpha<p>Feature Omega</p>")

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == (
        "MALFORMED_STRUCTURE",
        "NEEDS_REVIEW",
    )


def test_diagnostics_are_deduplicated_in_fixed_declaration_order():
    result = _normalize(
        '<table><tr><td rowspan="2">A<br>B</td><td colspan="2">C'
    )

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.diagnostic_codes == (
        "UNSUPPORTED_TABLE_STRUCTURE",
        "MALFORMED_STRUCTURE",
        "NEEDS_REVIEW",
    )


def test_raw_html_and_derived_text_do_not_leak_through_repr_or_review_result():
    attribute_sentinel = "RAW_ATTRIBUTE_SENTINEL_E1B2"
    text_sentinel = "RAW_TEXT_SENTINEL_C3D4"
    reviewed = _normalize(
        f'<table data-source="{attribute_sentinel}"><tr><td colspan="2">'
        f"{text_sentinel}</td></tr></table>"
    )

    assert reviewed.status is NormalizationStatus.NEEDS_REVIEW
    assert reviewed.title is None
    assert reviewed.clean_body is None
    assert reviewed.sections == ()
    assert attribute_sentinel not in repr(reviewed)
    assert text_sentinel not in repr(reviewed)

    successful = _normalize(f"<h1>{text_sentinel}</h1><p>Body</p>")
    assert text_sentinel not in repr(successful)
    assert text_sentinel not in repr(successful.sections[0])
    assert "<h1>" not in repr(successful)


def test_repeated_normalization_returns_exactly_equal_results():
    html = (
        "<h1>Brand Alpha</h1><p>Feature Omega</p>"
        "<ol><li>2 months</li><li>32 units</li></ol>"
    )

    first = _normalize(html)
    for _ in range(20):
        assert _normalize(html) == first


def test_normalizer_has_no_network_or_filesystem_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("WP11 boundary side effect")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)

    result = _normalize(
        '<p><a href="https://example.test/source">Offline visible text</a></p>'
    )

    assert result.clean_body == "Offline visible text"


def test_normalizer_does_not_hash_input(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("WP11 must not hash HTML")

    monkeypatch.setattr(hashlib, "sha256", unexpected)

    assert _normalize("<p>Body</p>").clean_body == "Body"


def test_production_module_has_no_golden_corpus_or_forbidden_boundary_dependency():
    source = inspect.getsource(html_normalization).lower()
    forbidden = {
        "obsidian_vault",
        "capture-parity-review",
        "title_semantic_token_preserved",
        "numeric_token_preserved",
        "feature_name_preserved",
        "requests",
        "httpx",
        "urllib.request",
        "aiohttp",
        "socket",
        "playwright",
        "selenium",
        "hashlib",
        "capturedcontent",
        "capturestatus",
        "obsidian",
        "sqlite",
        "vector",
        "slack",
    }

    assert all(term not in source for term in forbidden)


def test_wp11_result_has_no_raw_html_or_wp12_fields():
    result_fields = {field.name for field in fields(HtmlNormalizationResult)}
    forbidden = {
        "raw_html",
        "html",
        "content_hash",
        "capture_status",
        "captured_at",
        "searchable",
        "canonical_url",
        "source_http_metadata",
        "last_known_good",
    }

    assert forbidden.isdisjoint(result_fields)


@pytest.mark.parametrize(
    "heading",
    [
        "",
        "   ",
        " Heading",
        "Heading ",
        "Head\rLine",
        "Head\nLine",
        "Head\tLine",
        "Head\x00Line",
        "Cafe\u0301",
        "Head\u00a0Line",
        1,
    ],
)
def test_normalized_section_direct_constructor_rejects_noncanonical_heading(
    heading,
):
    sentinel = "SYNTHETIC_DTO_SECRET_58B2"
    with pytest.raises(HtmlNormalizationError) as captured:
        NormalizedSection(heading=heading, text=f"Body {sentinel}")

    assert captured.value.code == "NORMALIZED_SECTION_HEADING_INVALID"
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        " Body",
        "Body ",
        "　Body",
        "Body　",
        "Body\rLine",
        "Body\u00a0Line",
        "Body\x00Line",
        "Cafe\u0301",
        1,
    ],
)
def test_normalized_section_direct_constructor_rejects_noncanonical_text(text):
    sentinel = "SYNTHETIC_DTO_SECRET_58B2"
    with pytest.raises(HtmlNormalizationError) as captured:
        NormalizedSection(heading=f"Heading {sentinel}", text=text)

    assert captured.value.code == "NORMALIZED_SECTION_TEXT_INVALID"
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


def test_normalized_section_direct_constructor_accepts_canonical_lf_and_tab():
    section = NormalizedSection(heading="Heading", text="A\n\nB\tC")

    assert section.heading == "Heading"
    assert section.text == "A\n\nB\tC"
    assert replace(section) == section


def _direct_success_result(**overrides):
    sections = (NormalizedSection(heading="Heading", text="Body"),)
    values = {
        "status": NormalizationStatus.SUCCESS,
        "title": "Title",
        "clean_body": "Heading\nBody",
        "sections": sections,
        "parser_version": HTML_NORMALIZER_VERSION,
        "diagnostic_codes": (),
    }
    values.update(overrides)
    return HtmlNormalizationResult(**values)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"status": "success"}, "HTML_NORMALIZATION_RESULT_STATUS_INVALID"),
        ({"title": ""}, "HTML_NORMALIZATION_RESULT_TITLE_INVALID"),
        ({"title": " Title"}, "HTML_NORMALIZATION_RESULT_TITLE_INVALID"),
        ({"title": "Title\n"}, "HTML_NORMALIZATION_RESULT_TITLE_INVALID"),
        ({"title": "Cafe\u0301"}, "HTML_NORMALIZATION_RESULT_TITLE_INVALID"),
        ({"title": "Title\u00a0Text"}, "HTML_NORMALIZATION_RESULT_TITLE_INVALID"),
        ({"sections": []}, "HTML_NORMALIZATION_RESULT_SECTIONS_INVALID"),
        ({"sections": ("not-section",)}, "HTML_NORMALIZATION_RESULT_SECTIONS_INVALID"),
        (
            {"parser_version": "html-normalizer-v999"},
            "HTML_NORMALIZATION_RESULT_PARSER_VERSION_INVALID",
        ),
        (
            {"diagnostic_codes": []},
            "HTML_NORMALIZATION_RESULT_DIAGNOSTICS_INVALID",
        ),
        (
            {"diagnostic_codes": ("ARBITRARY",)},
            "HTML_NORMALIZATION_RESULT_DIAGNOSTICS_INVALID",
        ),
        (
            {"diagnostic_codes": ("NEEDS_REVIEW", "NEEDS_REVIEW")},
            "HTML_NORMALIZATION_RESULT_DIAGNOSTICS_INVALID",
        ),
        (
            {
                "diagnostic_codes": (
                    "NEEDS_REVIEW",
                    "MALFORMED_STRUCTURE",
                )
            },
            "HTML_NORMALIZATION_RESULT_DIAGNOSTICS_INVALID",
        ),
    ],
)
def test_result_direct_constructor_rejects_invalid_types_and_values(overrides, code):
    sentinel = "SYNTHETIC_DTO_SECRET_58B2"
    overrides = dict(overrides)
    overrides.setdefault("clean_body", f"Heading\nBody {sentinel}")
    with pytest.raises(HtmlNormalizationError) as captured:
        _direct_success_result(**overrides)

    assert captured.value.code == code
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        (
            {"clean_body": None},
            "HTML_NORMALIZATION_RESULT_STATUS_BODY_INVALID",
        ),
        (
            {"sections": ()},
            "HTML_NORMALIZATION_RESULT_STATUS_BODY_INVALID",
        ),
        (
            {"diagnostic_codes": ("NEEDS_REVIEW",)},
            "HTML_NORMALIZATION_RESULT_STATUS_BODY_INVALID",
        ),
        (
            {"clean_body": "Different"},
            "HTML_NORMALIZATION_RESULT_CLEAN_BODY_MISMATCH",
        ),
    ],
)
def test_success_direct_constructor_enforces_body_matrix(overrides, code):
    with pytest.raises(HtmlNormalizationError) as captured:
        _direct_success_result(**overrides)

    assert captured.value.code == code


def test_non_success_direct_constructor_enforces_exact_body_and_diagnostic_matrix():
    no_content = HtmlNormalizationResult(
        status=NormalizationStatus.NO_MEANINGFUL_CONTENT,
        title="Title",
        clean_body=None,
        sections=(),
        parser_version=HTML_NORMALIZER_VERSION,
        diagnostic_codes=("NO_MEANINGFUL_CONTENT",),
    )
    generic_review = HtmlNormalizationResult(
        status=NormalizationStatus.NEEDS_REVIEW,
        title=None,
        clean_body=None,
        sections=(),
        parser_version=HTML_NORMALIZER_VERSION,
        diagnostic_codes=("NEEDS_REVIEW",),
    )

    assert no_content.status is NormalizationStatus.NO_MEANINGFUL_CONTENT
    assert generic_review.diagnostic_codes == ("NEEDS_REVIEW",)

    invalid_results = [
        {
            "status": NormalizationStatus.NO_MEANINGFUL_CONTENT,
            "clean_body": "Provisional",
            "sections": (),
            "diagnostic_codes": ("NO_MEANINGFUL_CONTENT",),
        },
        {
            "status": NormalizationStatus.NO_MEANINGFUL_CONTENT,
            "clean_body": None,
            "sections": (NormalizedSection(None, "Body"),),
            "diagnostic_codes": ("NO_MEANINGFUL_CONTENT",),
        },
        {
            "status": NormalizationStatus.NO_MEANINGFUL_CONTENT,
            "clean_body": None,
            "sections": (),
            "diagnostic_codes": ("NEEDS_REVIEW",),
        },
        {
            "status": NormalizationStatus.NEEDS_REVIEW,
            "clean_body": "Provisional",
            "sections": (),
            "diagnostic_codes": ("NEEDS_REVIEW",),
        },
        {
            "status": NormalizationStatus.NEEDS_REVIEW,
            "clean_body": None,
            "sections": (NormalizedSection(None, "Body"),),
            "diagnostic_codes": ("NEEDS_REVIEW",),
        },
        {
            "status": NormalizationStatus.NEEDS_REVIEW,
            "clean_body": None,
            "sections": (),
            "diagnostic_codes": (),
        },
        {
            "status": NormalizationStatus.NEEDS_REVIEW,
            "clean_body": None,
            "sections": (),
            "diagnostic_codes": ("NO_MEANINGFUL_CONTENT", "NEEDS_REVIEW"),
        },
    ]
    for values in invalid_results:
        with pytest.raises(
            HtmlNormalizationError,
            match="HTML_NORMALIZATION_RESULT_STATUS_BODY_INVALID",
        ):
            HtmlNormalizationResult(
                title=None,
                parser_version=HTML_NORMALIZER_VERSION,
                **values,
            )


def test_specific_diagnostics_are_bound_to_needs_review_status():
    for specific in (
        "UNSUPPORTED_TABLE_STRUCTURE",
        "MALFORMED_STRUCTURE",
        "INPUT_TOO_LARGE",
    ):
        result = HtmlNormalizationResult(
            status=NormalizationStatus.NEEDS_REVIEW,
            title=None,
            clean_body=None,
            sections=(),
            parser_version=HTML_NORMALIZER_VERSION,
            diagnostic_codes=(specific, "NEEDS_REVIEW"),
        )
        assert result.diagnostic_codes[-1] == "NEEDS_REVIEW"


def test_dataclasses_replace_revalidates_result_invariants():
    result = _direct_success_result()

    assert replace(result) == result
    assert replace(result, title=None).title is None
    invalid_replacements = [
        {"clean_body": None},
        {"clean_body": "Different"},
        {"parser_version": "wrong"},
        {
            "status": NormalizationStatus.NEEDS_REVIEW,
            "clean_body": "Provisional",
        },
        {"sections": []},
        {"diagnostic_codes": ("ARBITRARY",)},
    ]
    for update in invalid_replacements:
        with pytest.raises(HtmlNormalizationError):
            replace(result, **update)


def test_result_rejects_caller_owned_mutable_sections_and_diagnostics():
    sections = [NormalizedSection(heading=None, text="Body")]
    diagnostics = []

    with pytest.raises(
        HtmlNormalizationError,
        match="HTML_NORMALIZATION_RESULT_SECTIONS_INVALID",
    ):
        _direct_success_result(sections=sections)
    with pytest.raises(
        HtmlNormalizationError,
        match="HTML_NORMALIZATION_RESULT_DIAGNOSTICS_INVALID",
    ):
        _direct_success_result(diagnostic_codes=diagnostics)


def test_clean_body_is_exact_renderer_of_validated_sections():
    result = _normalize(
        "<p>Intro</p><h2>Heading</h2><p>A</p><p>B</p>"
    )
    rendered = "\n\n".join(
        section.text
        if section.heading is None
        else f"{section.heading}\n{section.text}"
        for section in result.sections
    )

    assert result.clean_body == rendered


@pytest.mark.parametrize(
    "html",
    [
        "<p>Before<embed>SECRET</embed>After</p>",
        "<embed></embed>",
        "</embed>",
    ],
)
def test_paired_or_orphan_closing_embed_requires_review(html):
    result = _normalize(html)

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == (
        "MALFORMED_STRUCTURE",
        "NEEDS_REVIEW",
    )
    assert "SECRET" not in repr(result)


@pytest.mark.parametrize(
    "html",
    [
        "<li>Orphan</li>",
        "</li>",
        "<tr><td>A</td></tr>",
        "<td>A</td>",
        "<th>A</th>",
        "<tbody><tr><td>A</td></tr></tbody>",
        "<caption>Caption</caption>",
    ],
)
def test_orphan_list_or_table_structure_requires_review(html):
    result = _normalize(html)

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == (
        "MALFORMED_STRUCTURE",
        "NEEDS_REVIEW",
    )


def test_well_formed_suppression_stack_drops_nested_content_and_resumes():
    result = _normalize(
        "<nav><script>SECRET</script><div>hidden</div></nav><p>Visible</p>"
    )

    assert result.status is NormalizationStatus.SUCCESS
    assert result.clean_body == "Visible"


def test_malformed_suppression_inner_imbalance_requires_review():
    result = _normalize("<p>Intro</p><nav><div>hidden</nav><p>Visible</p>")

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == (
        "MALFORMED_STRUCTURE",
        "NEEDS_REVIEW",
    )


def test_eof_while_suppressed_requires_review_without_provisional_body():
    result = _normalize("<p>Intro</p><footer><div>hidden</div>")

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == (
        "MALFORMED_STRUCTURE",
        "NEEDS_REVIEW",
    )


def test_exact_ascii_and_multibyte_utf8_byte_limit_is_allowed():
    for html in (
        "A" * HTML_NORMALIZER_MAX_INPUT_BYTES,
        "中" * 349_525 + "A",
    ):
        result = _normalize(html)

        assert len(html.encode("utf-8")) == HTML_NORMALIZER_MAX_INPUT_BYTES
        assert result.status is NormalizationStatus.SUCCESS


def test_entities_decode_once_to_visible_source_semantics():
    result = _normalize("<p>&amp; A&nbsp;B &#37; &#x4E2D;</p>")

    assert result.clean_body == "& A B % 中"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>品牌<strong>名稱</strong></p>", "品牌名稱"),
        ("<p>品牌 SHOPLINE</p>", "品牌 SHOPLINE"),
        ("<p>SHOPLINE品牌</p>", "SHOPLINE品牌"),
        ("<p>SHOPLINE 品牌</p>", "SHOPLINE 品牌"),
        ("<p>300　%</p>", "300　%"),
    ],
)
def test_cjk_latin_inline_boundaries_do_not_invent_or_remove_spaces(html, expected):
    assert _normalize(html).clean_body == expected


def test_consecutive_br_preserves_two_lfs_and_trims_block_edges():
    result = _normalize("<p><br>A<br><br>B<br></p>")

    assert result.clean_body == "A\n\nB"


def test_multiple_h1_uses_first_title_and_preserves_each_source_heading_once():
    result = _normalize(
        "<h1>First</h1><p>A</p><h1>Second</h1><p>B</p>"
    )

    assert result.title == "First"
    assert result.clean_body == "First\nA\n\nSecond\nB"


def test_sequential_lists_reset_state_and_numbering():
    result = _normalize(
        "<ul><li>U1</li><li>U2</li></ul>"
        "<ol><li>O1</li><li>O2</li></ol>"
    )

    assert result.clean_body == "• U1\n• U2\n\n1. O1\n2. O2"


def test_sequential_tables_reset_state_and_preserve_empty_cell_position():
    result = _normalize(
        "<table><tr><td>A</td></tr></table>"
        "<table><tr><td></td><td>B</td></tr></table>"
    )

    assert result.clean_body == "A\n\n\tB"


@pytest.mark.parametrize("value", ["false", "1", "yes"])
def test_nontrue_aria_hidden_values_are_preserved(value):
    result = _normalize(f'<p aria-hidden="{value}">Visible</p>')

    assert result.clean_body == "Visible"


@pytest.mark.parametrize(
    "html",
    [
        "<p>A\x00B</p>",
        "<h1>A<br>B</h1><p>Body</p>",
        "<title>A<br>B</title><p>Body</p>",
        "<h1>　A　</h1><p>Body</p>",
    ],
)
def test_parser_generated_noncanonical_dto_text_fails_closed(html):
    result = _normalize(html)

    assert result.status is NormalizationStatus.NEEDS_REVIEW
    assert result.clean_body is None
    assert result.sections == ()
    assert result.diagnostic_codes == (
        "MALFORMED_STRUCTURE",
        "NEEDS_REVIEW",
    )
