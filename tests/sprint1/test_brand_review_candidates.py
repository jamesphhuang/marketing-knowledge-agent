from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import re

import pytest

import marketing_knowledge_agent.brand_review_candidates as brand_module

from marketing_knowledge_agent.brand_review_candidates import (
    BRAND_REVIEW_CANDIDATE_SCHEMA_VERSION,
    BrandCandidateClassification,
    BrandCandidateReason,
    BrandReviewCandidate,
    SafeSourceRef,
)
from marketing_knowledge_agent.google_sheets_canonical_normalization import (
    normalize_coverage_proven_batch,
)
from sprint1.test_google_sheets_canonical_normalization import (
    full_rows,
    synthetic_context,
    text,
)


_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def candidates(rows=None, *, reverse_sheets=False):
    return normalize_coverage_proven_batch(
        synthetic_context(rows, reverse_sheets=reverse_sheets)
    ).brand_review_candidates


def test_unique_evidence_is_redacted_non_authoritative_and_still_requires_human_review():
    result = candidates()
    assert len(result) == 1
    candidate = result[0]
    assert type(candidate) is BrandReviewCandidate
    assert candidate.schema_version == BRAND_REVIEW_CANDIDATE_SCHEMA_VERSION
    assert candidate.candidate_kind == "BRAND_IDENTITY_EVIDENCE"
    assert candidate.authority == "NON_AUTHORITATIVE"
    assert candidate.review_action == "HUMAN_REVIEW_REQUIRED"
    assert candidate.classification is BrandCandidateClassification.UNIQUE_EVIDENCE
    assert candidate.normalized_handle == "@example"
    assert candidate.website_hosts == ("shop.example",)
    assert _HASH.fullmatch(candidate.candidate_ref)
    assert all(_HASH.fullmatch(value) for value in candidate.website_refs)
    assert set(candidate.reason_codes) >= {
        BrandCandidateReason.EXACT_HANDLE_EVIDENCE.value,
        BrandCandidateReason.SAFE_WEBSITE_EVIDENCE.value,
        BrandCandidateReason.BRD_AUTHORITY_DEFERRED.value,
        BrandCandidateReason.HANDLE_MAPPING_EVIDENCE_ONLY.value,
    }
    assert not hasattr(candidate, "brand_id")
    assert not hasattr(candidate, "approved")


def test_no_handle_and_no_url_is_ambiguous_even_when_name_exists():
    rows = full_rows()
    rows["merchant_case"][0][2] = text("Name Only")
    rows["merchant_case"][0][3] = {}
    rows["handle_mapping"] = []
    candidate = candidates(rows)[0]
    assert candidate.classification is BrandCandidateClassification.AMBIGUOUS
    assert candidate.normalized_handle is None
    assert candidate.website_hosts == ()
    assert "INSUFFICIENT_IDENTITY_EVIDENCE" in candidate.reason_codes


def test_unsafe_identity_url_is_ambiguous_and_raw_url_never_escapes():
    rows = full_rows()
    unsafe = "https://admin.internal.example/credential?Authorization=SECRET"
    rows["merchant_case"][0][2] = text("Unsafe", hyperlink=unsafe)
    rows["merchant_case"][0][3] = {}
    rows["handle_mapping"] = []
    candidate = candidates(rows)[0]
    assert candidate.classification is BrandCandidateClassification.AMBIGUOUS
    assert candidate.website_hosts == ()
    assert "UNSAFE_WEBSITE_EVIDENCE" in candidate.reason_codes
    assert unsafe not in repr(candidate)
    assert "SECRET" not in repr(candidate)

    rows["merchant_case"][0][3] = text("@unsafe-handle")
    candidate = candidates(rows)[0]
    assert candidate.classification is BrandCandidateClassification.AMBIGUOUS


def test_exact_handle_equality_connects_sources_and_distinct_websites_conflict():
    rows = full_rows()
    rows["merchant_case"][0][2] = text("First", hyperlink="https://one.example")
    rows["merchant_case"][0][3] = text("@same")
    rows["handle_mapping"][0][0] = text(" @SAME ")
    rows["handle_mapping"][0][1] = text("Second", hyperlink="https://two.example")
    candidate = candidates(rows)[0]
    assert candidate.classification is BrandCandidateClassification.CONFLICTING
    assert candidate.normalized_handle == "@same"
    assert candidate.website_hosts == ("one.example", "two.example")
    assert set(candidate.reason_codes) >= {
        "MULTIPLE_SAFE_WEBSITES",
        "HANDLE_TO_WEBSITE_CONFLICT",
    }


def test_exact_url_equality_connects_multiple_handles_as_conflicting_many_to_one():
    rows = full_rows()
    rows["merchant_case"][0][2] = text("First", hyperlink="https://same.example/path")
    rows["merchant_case"][0][3] = text("@first")
    rows["handle_mapping"][0][0] = text("@second")
    rows["handle_mapping"][0][1] = text("Second", hyperlink="https://same.example/path")
    candidate = candidates(rows)[0]
    assert candidate.classification is BrandCandidateClassification.CONFLICTING
    assert candidate.normalized_handle is None
    assert candidate.website_hosts == ("same.example",)
    assert "WEBSITE_TO_HANDLE_CONFLICT" in candidate.reason_codes


def test_one_source_cell_with_multiple_distinct_safe_urls_is_conflicting():
    rows = full_rows()
    rows["merchant_case"][0][2] = text(
        "Rich links",
        textFormatRuns=[
            {"startIndex": 0, "format": {"link": {"uri": "https://one.example"}}},
            {"startIndex": 4, "format": {"link": {"uri": "https://two.example"}}},
        ],
    )
    rows["handle_mapping"] = []
    candidate = candidates(rows)[0]
    assert candidate.classification is BrandCandidateClassification.CONFLICTING
    assert candidate.website_hosts == ("one.example", "two.example")
    assert "MULTIPLE_SAFE_WEBSITES" in candidate.reason_codes


def test_name_is_not_a_graph_edge_and_collision_marks_separate_candidates_ambiguous():
    rows = full_rows()
    rows["merchant_case"][0][2] = text("Same Name")
    rows["merchant_case"][0][3] = text("@first")
    rows["handle_mapping"][0][0] = text("@second")
    rows["handle_mapping"][0][1] = text("Same Name")
    result = candidates(rows)
    assert len(result) == 2
    assert all(item.classification is BrandCandidateClassification.AMBIGUOUS for item in result)
    assert all("NAME_COLLISION_ACROSS_CANDIDATES" in item.reason_codes for item in result)
    assert {item.normalized_handle for item in result} == {"@first", "@second"}


def test_duplicate_name_without_exact_handle_or_url_never_auto_merges():
    rows = full_rows()
    first = list(rows["merchant_case"][0])
    first[2] = text("Repeated Name")
    first[3] = text("@first")
    second = list(first)
    second[3] = text("@second")
    second[0] = text("2025")
    rows["merchant_case"] = [first, second]
    rows["handle_mapping"] = []
    result = candidates(rows)
    assert len(result) == 2
    assert {item.normalized_handle for item in result} == {"@first", "@second"}


def test_restricted_pending_and_public_metric_sources_never_enter_brand_candidates():
    rows = full_rows()
    rows["merchant_case"] = []
    rows["handle_mapping"] = []
    assert candidates(rows) == ()


def test_candidate_order_and_hash_are_independent_of_snapshot_sheet_order():
    normal = candidates()
    reversed_order = candidates(reverse_sheets=True)
    assert [item.candidate_ref for item in normal] == [
        item.candidate_ref for item in reversed_order
    ]
    assert [item.source_refs for item in normal] == [
        item.source_refs for item in reversed_order
    ]


def test_deterministic_hashes_are_domain_separated_review_refs_not_permanent_ids():
    candidate = candidates()[0]
    assert candidate.candidate_ref not in candidate.website_refs
    assert all(source.source_ref != candidate.candidate_ref for source in candidate.source_refs)
    assert all(type(source) is SafeSourceRef for source in candidate.source_refs)
    assert all(not source.source_ref.startswith(("BRD-", "MREC-", "MET-")) for source in candidate.source_refs)
    assert not candidate.candidate_ref.startswith(("BRD-", "MREC-", "MET-"))
    assert not any(value.startswith(("BRD-", "MREC-", "MET-")) for value in candidate.website_refs)


def test_owner_decision_one_exact_allowlist_exposes_hostname_not_name_or_full_url():
    candidate = candidates()[0]
    source = candidate.source_refs[0]
    allowed_candidate_fields = {
        "schema_version", "candidate_kind", "authority", "review_action", "candidate_ref",
        "classification", "source_refs", "normalized_handle", "website_hosts", "website_refs",
        "reason_codes",
    }
    public_candidate_fields = {
        name for name in dir(candidate)
        if not name.startswith("_") and not callable(getattr(candidate, name))
    }
    assert public_candidate_fields == allowed_candidate_fields
    assert {
        name for name in dir(source)
        if not name.startswith("_") and not callable(getattr(source, name))
    } == {"source_class", "sheet_id", "source_row", "source_ref"}
    rendered = repr(candidate) + repr(candidate.source_refs)
    assert "Merchant Secret" not in rendered
    assert "Mapping Secret" not in rendered
    assert "https://" not in rendered
    assert "/about" not in rendered
    assert "?" not in rendered


def test_reason_codes_are_exactly_allowlisted_and_never_interpolate_payload():
    allowlist = {item.value for item in BrandCandidateReason}
    rows = full_rows()
    rows["merchant_case"][0][2] = text("PAYLOAD_REASON_SENTINEL", hyperlink="https://one.example")
    rows["handle_mapping"][0][1] = text("PAYLOAD_REASON_SENTINEL", hyperlink="https://two.example")
    for candidate in candidates(rows):
        assert set(candidate.reason_codes) <= allowlist
        assert all("PAYLOAD_REASON_SENTINEL" not in code for code in candidate.reason_codes)


def test_candidate_and_safe_source_ref_construction_is_forbidden_to_callers():
    with pytest.raises(TypeError, match="SAFE_SOURCE_REF_CONSTRUCTION_FORBIDDEN"):
        SafeSourceRef("merchant_case", 0, 7, "sha256:" + "0" * 64)
    with pytest.raises(TypeError, match="BRAND_REVIEW_CANDIDATE_CONSTRUCTION_FORBIDDEN"):
        BrandReviewCandidate({"authority": "NON_AUTHORITATIVE"})


def test_trusted_brand_factories_are_absent_and_pure_digests_cannot_mint_objects():
    for name in (
        "_new_safe_source_ref",
        "_new_brand_evidence",
        "_build_brand_review_candidates",
    ):
        assert not hasattr(brand_module, name)

    fake_digest = brand_module._safe_source_ref_digest(
        "merchant_case",
        0,
        7,
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
    )
    assert _HASH.fullmatch(fake_digest)
    with pytest.raises(TypeError, match="SAFE_SOURCE_REF_CONSTRUCTION_FORBIDDEN"):
        SafeSourceRef(
            source_class="merchant_case",
            sheet_id=0,
            source_row=7,
            source_ref=fake_digest,
        )
    with pytest.raises(TypeError, match="BRAND_REVIEW_CANDIDATE_CONSTRUCTION_FORBIDDEN"):
        BrandReviewCandidate(
            candidate_ref="sha256:" + "3" * 64,
            website_ref="sha256:" + "4" * 64,
            hostname="fake.example",
        )

    genuine_ref = candidates()[0].source_refs[0]
    untrusted = brand_module._UntrustedBrandEvidence(
        source_ref=genuine_ref,
        normalized_name="caller-selected",
        normalized_handle="@caller-selected",
        canonical_urls=("https://caller-selected.example/path",),
        unsafe_website_evidence=False,
        handle_mapping=False,
        multiple_urls_in_one_cell=False,
    )
    projection = brand_module._project_brand_review_candidates((untrusted,))[0]
    assert type(projection).__name__ == "_UntrustedBrandCandidateProjection"
    assert not isinstance(projection, BrandReviewCandidate)


def test_candidate_source_ref_subclass_replace_copy_and_deepcopy_cannot_mint_authority():
    candidate = candidates()[0]
    source_ref = candidate.source_refs[0]

    class CandidateSubclass(BrandReviewCandidate):
        pass

    with pytest.raises(TypeError, match="CONSTRUCTION_FORBIDDEN"):
        CandidateSubclass()
    with pytest.raises(TypeError):
        replace(candidate, candidate_ref="sha256:" + "0" * 64)
    for value in (candidate, source_ref):
        with pytest.raises(TypeError):
            copy.copy(value)
        with pytest.raises(TypeError):
            copy.deepcopy(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("merchant123", "merchant123"),
        ("Merchant123", "merchant123"),
        ("@merchant123", "@merchant123"),
        ("shop_line", "shop_line"),
        ("shop-line", "shop-line"),
        ("shop.line", "shop.line"),
        ("＠Ｍｅｒｃｈａｎｔ１２３", "@merchant123"),
        ("商店１２３", "商店123"),
    ],
)
def test_owner_decision_two_valid_safe_handles(raw, expected):
    rows = full_rows()
    rows["merchant_case"][0][2] = text("Safe Handle")
    rows["merchant_case"][0][3] = text(raw)
    rows["handle_mapping"] = []
    candidate = candidates(rows)[0]
    assert candidate.normalized_handle == expected
    assert candidate.classification is BrandCandidateClassification.UNIQUE_EVIDENCE


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com",
        "http://example.com",
        "example.com/path?secret=x",
        "foo/bar",
        "foo\\bar",
        "foo?bar",
        "foo#bar",
        "foo&bar",
        "foo=bar",
        "foo%20bar",
        "foo:bar",
        "foo@@bar",
        "foo bar",
        "foo\tbar",
        "foo\nbar",
        "foo\n",
        "\nfoo",
        "foo\rbar",
        "foo\x00bar",
        "foo\x1fbar",
        "foo\u2028bar",
        "foo\u2029bar",
        "a" * 129,
        "field: value\nsecret: payload",
    ],
)
def test_owner_decision_two_invalid_handles_never_enter_safe_candidate(raw):
    rows = full_rows()
    rows["merchant_case"][0][2] = text("Invalid Handle")
    rows["merchant_case"][0][3] = text(raw)
    rows["handle_mapping"] = []
    candidate = candidates(rows)[0]
    rendered = repr(candidate) + repr(candidate.source_refs) + repr(candidate.reason_codes)

    assert candidate.normalized_handle is None
    assert candidate.classification is BrandCandidateClassification.AMBIGUOUS
    assert "INSUFFICIENT_IDENTITY_EVIDENCE" in candidate.reason_codes
    assert raw not in rendered


def test_same_invalid_handle_never_creates_a_brand_graph_edge():
    rows = full_rows()
    first = list(rows["merchant_case"][0])
    first[2] = text("First invalid handle")
    first[3] = text("https://payload.example/secret?token=one")
    second = list(first)
    second[0] = text("2025")
    second[2] = text("Second invalid handle")
    rows["merchant_case"] = [first, second]
    rows["handle_mapping"] = []

    result = candidates(rows)
    assert len(result) == 2
    assert all(candidate.normalized_handle is None for candidate in result)
    assert all(
        candidate.classification is BrandCandidateClassification.AMBIGUOUS
        for candidate in result
    )


def test_hash_shape_is_sha256_and_not_correlation_uuid_material():
    candidate = candidates()[0]
    uuid_text = "12345678-1234-4234-9234-123456789abc"
    assert uuid_text not in candidate.candidate_ref
    assert all(uuid_text not in value for value in candidate.website_refs)
    assert hashlib.sha256(uuid_text.encode()).hexdigest() not in candidate.candidate_ref
