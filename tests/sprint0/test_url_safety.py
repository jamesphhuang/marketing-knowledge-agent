from __future__ import annotations

import json
from copy import copy, deepcopy
from dataclasses import asdict, replace

import pytest

import marketing_knowledge_agent.url_safety as url_safety
from marketing_knowledge_agent.cell_normalization import (
    InheritanceReason,
    SourceFieldLineage,
    SourceLineage,
)
from marketing_knowledge_agent.link_resolution import (
    AssetSourceSlot,
    LinkCandidate,
    LinkSource,
)
from marketing_knowledge_agent.url_safety import (
    CanonicalURL,
    DEFAULT_URL_POLICY,
    URLPolicy,
    URLRejectionCode,
    URLValidationError,
    validate_and_canonicalize_url,
)


SYNTHETIC_SECRET = "SYNTHETIC_WP7_SECRET_7F3A"


def _candidate(raw_url: str) -> LinkCandidate:
    return LinkCandidate(
        raw_url=raw_url,
        source=LinkSource.CELL_HYPERLINK,
        asset_source_slot=AssetSourceSlot.ARTICLE,
        lineage=SourceLineage(
            spreadsheet_id="synthetic-spreadsheet",
            sheet_id=107,
            sheet_title="Synthetic URL Safety",
            sheet_hidden=False,
            source_row_index=6,
            source_column_index=7,
            source_fingerprint="sha256:synthetic",
            sync_batch_id="synthetic-batch",
        ),
        field_lineage=SourceFieldLineage(
            field_name="article",
            target_row_index=6,
            target_column_index=7,
            value_row_index=6,
            value_column_index=7,
            merge_anchor_row_index=None,
            merge_anchor_column_index=None,
            merge_range=None,
            inherited_from_merge=False,
            inheritance_reason=InheritanceReason.LOCAL,
        ),
    )


def _validate(raw_url: str):
    return validate_and_canonicalize_url(_candidate(raw_url))


def _assert_accepted(raw_url: str, canonical: str):
    result = _validate(raw_url)
    assert result.is_accepted is True
    assert result.is_rejected is False
    assert result.rejection_code is None
    assert result.canonical_url is not None
    assert result.canonical_url.value == canonical
    assert str(result.canonical_url) == canonical
    return result


def _assert_rejected(raw_url: str, code: URLRejectionCode):
    result = _validate(raw_url)
    assert result.is_accepted is False
    assert result.is_rejected is True
    assert result.canonical_url is None
    assert result.rejection_code is code
    return result


def test_sensitive_query_is_rejected_before_tracking_removal():
    result = _assert_rejected(
        f"https://example.test/path?utm_source=x&token={SYNTHETIC_SECRET}",
        URLRejectionCode.SENSITIVE_QUERY,
    )

    assert "utm_source" not in repr(result)
    assert SYNTHETIC_SECRET not in repr(result)


@pytest.mark.parametrize(
    "raw_url",
    [
        f"https://synthetic-user:{SYNTHETIC_SECRET}@example.test/path",
        "https://synthetic-user@example.test/path",
        "https://synthetic-user:@example.test/path",
    ],
)
def test_any_userinfo_is_rejected(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.USERINFO_NOT_ALLOWED)


@pytest.mark.parametrize("hostname", ["localhost", "LOCALHOST", "LocalHost"])
def test_localhost_case_variants_are_rejected(hostname):
    _assert_rejected(
        f"https://{hostname}/path",
        URLRejectionCode.LOCAL_HOST_NOT_ALLOWED,
    )


def test_private_ipv4_is_rejected():
    _assert_rejected(
        "https://10.1.2.3/path",
        URLRejectionCode.NON_PUBLIC_IP_NOT_ALLOWED,
    )


def test_loopback_ipv6_is_rejected():
    _assert_rejected(
        "https://[::1]/path",
        URLRejectionCode.NON_PUBLIC_IP_NOT_ALLOWED,
    )


@pytest.mark.parametrize("character", ["\x00", "\r", "\n", "\t", "\x1f", "\x7f"])
def test_ascii_control_characters_are_rejected_without_parser_repair(character):
    _assert_rejected(
        f"https://example.test/a{character}b",
        URLRejectionCode.CONTROL_CHARACTER,
    )


def test_rejected_result_and_all_supported_serializations_do_not_leak_secret(caplog):
    raw_url = f"https://example.test/path?token={SYNTHETIC_SECRET}"
    result = _assert_rejected(raw_url, URLRejectionCode.SENSITIVE_QUERY)

    rendered = (
        repr(result),
        str(result),
        repr(asdict(result)),
        json.dumps(result.to_dict(), sort_keys=True),
        caplog.text,
    )
    assert all(SYNTHETIC_SECRET not in value for value in rendered)
    assert all(raw_url not in value for value in rendered)


def test_rejected_userinfo_result_does_not_leak_username_or_password(caplog):
    username = "SYNTHETIC_WP7_USER_7F3A"
    raw_url = f"https://{username}:{SYNTHETIC_SECRET}@example.test/path"
    result = _assert_rejected(raw_url, URLRejectionCode.USERINFO_NOT_ALLOWED)

    rendered = (
        repr(result),
        str(result),
        repr(asdict(result)),
        json.dumps(result.to_dict(), sort_keys=True),
        caplog.text,
    )
    assert all(username not in value for value in rendered)
    assert all(SYNTHETIC_SECRET not in value for value in rendered)
    assert all(raw_url not in value for value in rendered)


def test_raw_candidate_and_provenance_remain_unmodified():
    raw_url = "HTTPS://Example.Test:443/%7Ereport?utm_source=x&a=1#fragment"
    candidate = _candidate(raw_url)
    before = asdict(candidate)

    result = validate_and_canonicalize_url(candidate)

    assert result.is_accepted is True
    assert asdict(candidate) == before
    assert candidate.raw_url == raw_url
    assert candidate.lineage.source_coordinate == (6, 7)


@pytest.mark.parametrize(
    ("raw_url", "canonical"),
    [
        ("https://example.test/path", "https://example.test/path"),
        ("http://example.test/path", "http://example.test/path"),
        ("HTTPS://EXAMPLE.TEST/path", "https://example.test/path"),
        ("http://Example.Test:80/path", "http://example.test/path"),
        ("https://Example.Test:443/path", "https://example.test/path"),
        ("https://Example.Test:8443/path", "https://example.test:8443/path"),
        ("https://example.test/path#section", "https://example.test/path"),
        ("https://BÜCHER.Example/path", "https://xn--bcher-kva.example/path"),
        ("https://example.test/%7euser/%41", "https://example.test/~user/A"),
        ("https://example.test/a%2fb", "https://example.test/a%2Fb"),
        (
            "https://example.test/path?b=2&a=1&a=3",
            "https://example.test/path?b=2&a=1&a=3",
        ),
        (
            "https://example.test/path?b=2&utm_source=x&a=1&a=3&fbclid=z",
            "https://example.test/path?b=2&a=1&a=3",
        ),
    ],
)
def test_safe_urls_are_canonicalized_without_resource_semantic_rewrites(
    raw_url, canonical
):
    _assert_accepted(raw_url, canonical)


def test_empty_path_is_not_rewritten_to_a_trailing_slash():
    _assert_accepted("https://example.test", "https://example.test")


@pytest.mark.parametrize(
    "raw_url",
    [
        "/relative/path",
        "#fragment-only",
        "mailto:synthetic@example.test",
        "tel:+886200000000",
        "file:///synthetic/path",
        "ftp://example.test/file",
        "javascript:synthetic",
        "data:text/plain,synthetic",
    ],
)
def test_non_http_or_relative_schemes_are_rejected(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.UNSUPPORTED_SCHEME)


@pytest.mark.parametrize("raw_url", ["https:///path", "https://?q=synthetic"])
def test_http_urls_without_a_hostname_are_rejected(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.MISSING_HOST)


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://example.test:/path",
        "https://example.test:not-a-port/path",
        "https://example.test:65536/path",
        "https://example.test:80:90/path",
    ],
)
def test_invalid_or_ambiguous_ports_are_rejected(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.INVALID_PORT)


def test_dot_local_hostname_is_rejected_at_label_boundary():
    _assert_rejected(
        "https://printer.LOCAL/path",
        URLRejectionCode.LOCAL_HOST_NOT_ALLOWED,
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
    ],
)
def test_blocked_ipv4_classes_are_rejected(address):
    _assert_rejected(
        f"https://{address}/path",
        URLRejectionCode.NON_PUBLIC_IP_NOT_ALLOWED,
    )


@pytest.mark.parametrize(
    "address",
    [
        "::1",
        "fd00::1",
        "fe80::1",
        "ff02::1",
        "::",
        "2001:db8::1",
    ],
)
def test_blocked_ipv6_classes_are_rejected(address):
    _assert_rejected(
        f"https://[{address}]/path",
        URLRejectionCode.NON_PUBLIC_IP_NOT_ALLOWED,
    )


@pytest.mark.parametrize(
    ("raw_url", "canonical"),
    [
        ("https://8.8.8.8/path", "https://8.8.8.8/path"),
        (
            "https://[2606:4700:4700::1111]:443/path",
            "https://[2606:4700:4700::1111]/path",
        ),
    ],
)
def test_public_literal_ips_follow_the_frozen_allow_policy(raw_url, canonical):
    _assert_accepted(raw_url, canonical)


def test_post_idna_loopback_literal_is_reclassified_and_rejected():
    raw_url = "https://１２７.０.０.１/path"

    result = _assert_rejected(
        raw_url,
        URLRejectionCode.NON_PUBLIC_IP_NOT_ALLOWED,
    )

    assert raw_url not in repr(result)
    assert "raw_url" not in result.to_dict()


def test_post_idna_integer_ipv4_form_is_reclassified_as_ambiguous():
    _assert_rejected(
        "https://２１３０７０６４３３/path",
        URLRejectionCode.AMBIGUOUS_URL,
    )


def test_post_idna_public_literal_uses_the_existing_ip_allow_policy():
    _assert_accepted(
        "https://８.８.８.８/path",
        "https://8.8.8.8/path",
    )


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://2130706433/path",
        "https://0x7f000001/path",
        "https://0177.0.0.1/path",
        "https://127.1/path",
        "https://[fe80::1%25eth0]/path",
    ],
)
def test_nonstandard_or_scoped_ip_syntax_is_rejected_as_ambiguous(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.AMBIGUOUS_URL)


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "TOKEN",
        "Api_Key",
        "api-key",
        "api.key",
        "API KEY",
        "api%5Fkey",
        "AUTH",
        "Signature",
        "SESSION",
        "credential",
    ],
)
def test_only_approved_normalized_sensitive_query_base_names_are_rejected(key):
    _assert_rejected(
        f"https://example.test/path?{key}={SYNTHETIC_SECRET}",
        URLRejectionCode.SENSITIVE_QUERY,
    )


def test_sensitive_duplicate_rejects_the_entire_raw_url():
    _assert_rejected(
        f"https://example.test/path?safe=1&token={SYNTHETIC_SECRET}",
        URLRejectionCode.SENSITIVE_QUERY,
    )


@pytest.mark.parametrize(
    "key",
    [
        "access_token",
        "oauth_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "bearer_token",
        "sig",
        "session_id",
        "authorization",
        "credential_id",
    ],
)
def test_compound_sensitive_aliases_and_case_variants_are_rejected(key):
    for variant in (key, key.upper()):
        result = _assert_rejected(
            f"https://example.test/path?{variant}={SYNTHETIC_SECRET}",
            URLRejectionCode.SENSITIVE_QUERY,
        )
        assert variant not in repr(result)
        assert SYNTHETIC_SECRET not in repr(result)
        assert SYNTHETIC_SECRET not in json.dumps(result.to_dict(), sort_keys=True)


@pytest.mark.parametrize(
    "key_parts",
    [
        ("access", "token"),
        ("oauth", "token"),
        ("refresh", "token"),
        ("id", "token"),
        ("auth", "token"),
        ("bearer", "token"),
        ("session", "id"),
        ("credential", "id"),
    ],
)
@pytest.mark.parametrize("separator", ["_", "-", ".", " ", "%5F"])
def test_sensitive_alias_separator_variants_are_rejected(key_parts, separator):
    key = separator.join(key_parts)
    _assert_rejected(
        f"https://example.test/path?{key}={SYNTHETIC_SECRET}",
        URLRejectionCode.SENSITIVE_QUERY,
    )


@pytest.mark.parametrize("key", ["api%255Fkey", "access%255Ftoken"])
def test_nested_percent_encoded_query_keys_are_rejected_as_ambiguous(key):
    _assert_rejected(
        f"https://example.test/path?{key}={SYNTHETIC_SECRET}",
        URLRejectionCode.AMBIGUOUS_URL,
    )


@pytest.mark.parametrize("key", ["tokenizer", "sessional", "signature_version"])
def test_non_sensitive_names_are_not_fuzzily_rejected(key):
    result = _validate(f"https://example.test/path?{key}=synthetic-value")
    assert result.is_accepted is True


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://example.test/%",
        "https://example.test/%2",
        "https://example.test/%GG",
        "https://example.test/path?api%GZkey=value",
    ],
)
def test_malformed_percent_escape_is_rejected_as_ambiguous(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.AMBIGUOUS_URL)


@pytest.mark.parametrize(
    "raw_url",
    [
        " https://example.test/path",
        "https://example.test/path ",
        "https://example.test\\@localhost/path",
        "https://[::1/path",
    ],
)
def test_parser_repair_or_ambiguous_host_path_input_is_rejected(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.AMBIGUOUS_URL)


def test_user_approved_raw_length_boundary_is_checked_before_any_transformation():
    prefix = "https://example.test/"
    at_limit = prefix + "a" * (4096 - len(prefix))
    over_limit = prefix + "a" * (4097 - len(prefix))

    assert len(at_limit) == DEFAULT_URL_POLICY.max_url_length
    assert len(over_limit) == DEFAULT_URL_POLICY.max_url_length + 1
    assert _validate(at_limit).is_accepted is True
    _assert_rejected(over_limit, URLRejectionCode.OVERLONG_URL)


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://admin.example.test/path",
        "https://foo.internal.example.test/path",
        "https://ADMIN.example.test/path",
        "https://example.test/admin/report",
        "https://example.test/ADMIN/report",
        "https://example.test/%61dmin/report",
        "https://example.test/internal/data",
    ],
)
def test_user_approved_internal_admin_label_or_path_segment_is_rejected(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.UNSAFE_INTERNAL_TARGET)


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://admin-guide.example.test/path",
        "https://myadmin.example.test/path",
        "https://internal-tools.example.test/path",
        "https://example.test/admin-guide/report",
        "https://example.test/blog/admin-tools",
        "https://example.test/myadmin/report",
        "https://example.test/safe%2Fadmin/report",
    ],
)
def test_internal_admin_policy_never_uses_substrings_or_decodes_reserved_slash(raw_url):
    assert _validate(raw_url).is_accepted is True


@pytest.mark.parametrize(
    "hostname",
    ["bit.ly", "goo.gl", "reurl.cc", "tinyurl.com"],
)
def test_legacy_exact_shortener_hosts_are_rejected(hostname):
    _assert_rejected(
        f"https://{hostname}/synthetic",
        URLRejectionCode.REDIRECTOR_NOT_ALLOWED,
    )


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://google.com/search?q=synthetic",
        "https://www.google.com/url?q=synthetic",
        "https://www.google.com/%75rl?q=synthetic",
        "https://l.facebook.com/l.php?u=synthetic",
    ],
)
def test_legacy_exact_search_and_tracking_redirects_are_rejected(raw_url):
    _assert_rejected(raw_url, URLRejectionCode.REDIRECTOR_NOT_ALLOWED)


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://google.com/searching?q=synthetic",
        "https://notgoogle.com/search?q=synthetic",
        "https://l.facebook.com/l.phpx?u=synthetic",
    ],
)
def test_redirect_policy_does_not_expand_beyond_exact_legacy_pairs(raw_url):
    assert _validate(raw_url).is_accepted is True


@pytest.mark.parametrize("hostname", ["example.test.", "www.example.test.", "localhost."])
def test_user_approved_trailing_dot_hostname_policy_fails_closed(hostname):
    _assert_rejected(
        f"https://{hostname}/path",
        URLRejectionCode.AMBIGUOUS_URL,
    )


def test_rejection_code_is_stable_and_deterministic():
    candidate = _candidate(f"https://example.test/path?token={SYNTHETIC_SECRET}")

    first = validate_and_canonicalize_url(candidate)
    second = validate_and_canonicalize_url(candidate)

    assert first == second
    assert first.rejection_code.value == "SENSITIVE_QUERY"


def test_canonicalization_is_idempotent_for_equivalent_candidate_input():
    first = _assert_accepted(
        "HTTPS://Example.Test:443/%7euser?utm_source=x&a=1#fragment",
        "https://example.test/~user?a=1",
    )
    assert first.canonical_url is not None

    second = validate_and_canonicalize_url(
        replace(_candidate("https://unused.test"), raw_url=first.canonical_url.value)
    )

    assert second.canonical_url == first.canonical_url


def test_canonical_url_cannot_be_constructed_directly_from_unsafe_input():
    raw_url = f"https://example.test/path?token={SYNTHETIC_SECRET}"

    with pytest.raises(
        URLValidationError,
        match="CANONICAL_URL_VALIDATION_REQUIRED",
    ) as caught:
        CanonicalURL(raw_url)

    assert raw_url not in repr(caught.value)
    assert SYNTHETIC_SECRET not in repr(caught.value)


def test_canonical_url_authorization_is_not_available_through_normal_import():
    assert "_CANONICAL_URL_VALIDATION_TOKEN" not in vars(url_safety)

    with pytest.raises(
        URLValidationError,
        match="CANONICAL_URL_VALIDATION_REQUIRED",
    ):
        CanonicalURL(
            "https://example.test/synthetic",
            _validation_token=object(),
        )


def test_canonical_url_replace_cannot_change_validated_value_but_copy_is_safe():
    result = _assert_accepted(
        "https://example.test/original",
        "https://example.test/original",
    )
    assert result.canonical_url is not None
    canonical_url = result.canonical_url

    with pytest.raises(
        URLValidationError,
        match="CANONICAL_URL_VALIDATION_REQUIRED",
    ):
        replace(canonical_url, value="https://example.test/changed")

    assert copy(canonical_url) == canonical_url
    assert deepcopy(canonical_url) == canonical_url


def test_evidence_validator_returns_only_a_validated_canonical_url():
    canonical_url = url_safety.validate_and_canonicalize_evidence_url(
        "HTTPS://Example.Test:443/evidence?utm_source=synthetic#fragment"
    )

    assert type(canonical_url) is CanonicalURL
    assert canonical_url.value == "https://example.test/evidence"


def test_evidence_validator_rejection_is_payload_free():
    raw_url = f"https://example.test/evidence?access_token={SYNTHETIC_SECRET}"

    with pytest.raises(URLValidationError) as caught:
        url_safety.validate_and_canonicalize_evidence_url(raw_url)

    assert caught.value.code == URLRejectionCode.SENSITIVE_QUERY.value
    assert str(caught.value) == URLRejectionCode.SENSITIVE_QUERY.value
    assert raw_url not in repr(caught.value)
    assert SYNTHETIC_SECRET not in repr(caught.value)


def test_generic_duck_typed_candidate_is_rejected_without_reflecting_payload():
    class ArbitraryCandidate:
        raw_url = f"https://example.test/path?token={SYNTHETIC_SECRET}"
        source = LinkSource.CELL_HYPERLINK

    with pytest.raises(
        URLValidationError,
        match="LINK_CANDIDATE_REQUIRED",
    ) as caught:
        validate_and_canonicalize_url(ArbitraryCandidate())

    assert SYNTHETIC_SECRET not in repr(caught.value)


def test_result_preserves_only_safe_candidate_provenance():
    result = _assert_rejected(
        f"https://example.test/path?token={SYNTHETIC_SECRET}",
        URLRejectionCode.SENSITIVE_QUERY,
    )

    assert result.source is LinkSource.CELL_HYPERLINK
    assert result.asset_source_slot is AssetSourceSlot.ARTICLE
    assert result.lineage.sheet_id == 107
    assert result.field_lineage.target_coordinate == (6, 7)
    assert result.run_start_index is None
    assert result.run_ordinal is None
    assert "raw_url" not in result.to_dict()


def test_each_candidate_is_processed_independently_without_dedupe_or_winner_selection():
    candidates = (
        _candidate("https://example.test/path?utm_source=one"),
        _candidate("https://example.test/path?utm_source=two"),
    )

    results = tuple(validate_and_canonicalize_url(candidate) for candidate in candidates)

    assert len(results) == 2
    assert all(result.is_accepted for result in results)
    assert results[0].canonical_url == results[1].canonical_url


def test_policy_is_the_user_approved_fixed_wp7_contract():
    assert isinstance(DEFAULT_URL_POLICY, URLPolicy)
    assert DEFAULT_URL_POLICY.version == "wp7-v1-2026-08-09"
    assert DEFAULT_URL_POLICY.max_url_length == 4096


def test_validation_is_pure_and_offline_under_the_sprint0_network_guard():
    first = _validate("https://example.test/path?source=synthetic&a=1")
    second = _validate("https://example.test/path?source=synthetic&a=1")

    assert first == second
    assert first.canonical_url is not None
    assert first.canonical_url.value == "https://example.test/path?a=1"
