from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, fields, replace

import pytest

import marketing_knowledge_agent.capture_policy as capture_policy
from marketing_knowledge_agent.capture_policy import (
    ApprovedDomainRule,
    CaptureMode,
    CapturePolicy,
    CapturePolicyDecision,
    CapturePolicyError,
    CaptureRequest,
    DomainClass,
    FetchClient,
    FetchFailureCategory,
    FetchFailureReason,
    FetchResult,
    PolicyDecisionReason,
    ValidatedCaptureTargetRef,
    classify_fetch_failure,
    evaluate_capture_policy,
)
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
from marketing_knowledge_agent.url_safety import validate_and_canonicalize_url


POLICY_VERSION = "synthetic-policy-v1"
TARGET_SENTINEL = "SYNTHETIC_CAPTURE_TARGET_10F7"
DOMAIN_SENTINEL = "synthetic-approved.example"


def _policy(*rules: ApprovedDomainRule) -> CapturePolicy:
    return CapturePolicy(policy_version=POLICY_VERSION, approved_domain_rules=rules)


def _rule(domain_key: str, mode: CaptureMode) -> ApprovedDomainRule:
    return ApprovedDomainRule(domain_key=domain_key, mode=mode)


def _decision(
    mode: CaptureMode = CaptureMode.FULL_TEXT,
    reason: PolicyDecisionReason = PolicyDecisionReason.SHOPLINE_OWNED,
) -> CapturePolicyDecision:
    return CapturePolicyDecision(
        mode=mode,
        reason=reason,
        policy_version=POLICY_VERSION,
    )


def _target() -> ValidatedCaptureTargetRef:
    return ValidatedCaptureTargetRef(TARGET_SENTINEL)


def _canonical_url():
    result = validate_and_canonicalize_url(
        LinkCandidate(
            raw_url="https://example.test/synthetic-wp10",
            source=LinkSource.CELL_HYPERLINK,
            asset_source_slot=AssetSourceSlot.ARTICLE,
            lineage=SourceLineage(
                spreadsheet_id="synthetic-spreadsheet",
                sheet_id=110,
                sheet_title="Synthetic Capture Policy",
                sheet_hidden=False,
                source_row_index=6,
                source_column_index=7,
                source_fingerprint="sha256:synthetic",
                sync_batch_id="synthetic-wp10-batch",
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
    )
    assert result.canonical_url is not None
    return result.canonical_url


def test_exact_frozen_enum_values():
    assert [mode.value for mode in CaptureMode] == [
        "full_text",
        "metadata_only",
        "unsupported",
        "blocked",
    ]
    assert [domain_class.value for domain_class in DomainClass] == [
        "shopline_owned",
        "approved_third_party",
        "unknown_third_party",
        "authenticated_or_paywalled",
        "unsafe_private_or_internal",
    ]
    assert [category.value for category in FetchFailureCategory] == [
        "temporary",
        "non_temporary",
    ]
    assert [reason.value for reason in PolicyDecisionReason] == [
        "shopline_owned",
        "approved_third_party_rule",
        "needs_policy",
        "authenticated_or_paywalled",
        "unsafe_private_or_internal",
        "policy_missing",
        "unsupported",
    ]
    assert [reason.value for reason in FetchFailureReason] == [
        "timeout",
        "temporary_dns",
        "temporary_network",
        "http_status",
        "policy_blocked",
        "authenticated_or_paywalled",
        "governance_rejected",
        "identity_reconciliation_failed",
        "unsafe_target",
    ]


@pytest.mark.parametrize(
    ("domain_class", "expected_mode", "expected_reason"),
    [
        (
            DomainClass.SHOPLINE_OWNED,
            CaptureMode.FULL_TEXT,
            PolicyDecisionReason.SHOPLINE_OWNED,
        ),
        (
            DomainClass.UNKNOWN_THIRD_PARTY,
            CaptureMode.METADATA_ONLY,
            PolicyDecisionReason.NEEDS_POLICY,
        ),
        (
            DomainClass.AUTHENTICATED_OR_PAYWALLED,
            CaptureMode.BLOCKED,
            PolicyDecisionReason.AUTHENTICATED_OR_PAYWALLED,
        ),
        (
            DomainClass.UNSAFE_PRIVATE_OR_INTERNAL,
            CaptureMode.BLOCKED,
            PolicyDecisionReason.UNSAFE_PRIVATE_OR_INTERNAL,
        ),
    ],
)
def test_fixed_domain_decisions(domain_class, expected_mode, expected_reason):
    decision = evaluate_capture_policy(_policy(), domain_class)

    assert decision.mode is expected_mode
    assert decision.reason is expected_reason
    assert decision.policy_version == POLICY_VERSION


@pytest.mark.parametrize(
    ("domain_class", "expected_mode", "expected_reason"),
    [
        (
            DomainClass.SHOPLINE_OWNED,
            CaptureMode.FULL_TEXT,
            PolicyDecisionReason.SHOPLINE_OWNED,
        ),
        (
            DomainClass.UNKNOWN_THIRD_PARTY,
            CaptureMode.METADATA_ONLY,
            PolicyDecisionReason.NEEDS_POLICY,
        ),
        (
            DomainClass.AUTHENTICATED_OR_PAYWALLED,
            CaptureMode.BLOCKED,
            PolicyDecisionReason.AUTHENTICATED_OR_PAYWALLED,
        ),
        (
            DomainClass.UNSAFE_PRIVATE_OR_INTERNAL,
            CaptureMode.BLOCKED,
            PolicyDecisionReason.UNSAFE_PRIVATE_OR_INTERNAL,
        ),
    ],
)
def test_approved_rule_cannot_override_fixed_domain_class_decision(
    domain_class,
    expected_mode,
    expected_reason,
):
    domain_key = "conflict.example"
    policy = _policy(_rule(domain_key, CaptureMode.FULL_TEXT))

    decision = evaluate_capture_policy(
        policy,
        domain_class,
        domain_key=domain_key,
    )

    assert decision.mode is expected_mode
    assert decision.reason is expected_reason


@pytest.mark.parametrize(
    "mode",
    [CaptureMode.FULL_TEXT, CaptureMode.METADATA_ONLY],
)
def test_approved_third_party_uses_explicit_exact_rule(mode):
    policy = _policy(_rule(DOMAIN_SENTINEL, mode))

    decision = evaluate_capture_policy(
        policy,
        DomainClass.APPROVED_THIRD_PARTY,
        domain_key=DOMAIN_SENTINEL,
    )

    assert decision == CapturePolicyDecision(
        mode=mode,
        reason=PolicyDecisionReason.APPROVED_THIRD_PARTY_RULE,
        policy_version=POLICY_VERSION,
    )


def test_missing_approved_domain_rule_fails_closed_as_policy_decision():
    decision = evaluate_capture_policy(
        _policy(),
        DomainClass.APPROVED_THIRD_PARTY,
        domain_key=DOMAIN_SENTINEL,
    )

    assert decision.mode is CaptureMode.BLOCKED
    assert decision.reason is PolicyDecisionReason.POLICY_MISSING


@pytest.mark.parametrize("different_key", ["Synthetic-approved.example", "xsynthetic-approved.example"])
def test_approved_domain_matching_is_exact_without_case_or_suffix_matching(different_key):
    policy = _policy(_rule(DOMAIN_SENTINEL, CaptureMode.FULL_TEXT))

    decision = evaluate_capture_policy(
        policy,
        DomainClass.APPROVED_THIRD_PARTY,
        domain_key=different_key,
    )

    assert decision.mode is CaptureMode.BLOCKED
    assert decision.reason is PolicyDecisionReason.POLICY_MISSING


def test_shopline_substring_never_changes_domain_classification():
    decision = evaluate_capture_policy(
        _policy(),
        DomainClass.APPROVED_THIRD_PARTY,
        domain_key="evil-shopline.com",
    )

    assert decision.mode is CaptureMode.BLOCKED
    assert decision.reason is PolicyDecisionReason.POLICY_MISSING


@pytest.mark.parametrize("value", ["", "   ", " policy-v1", "policy-v1 ", "policy\x00v1", 1, None])
def test_policy_version_is_strict_and_stable(value):
    with pytest.raises((CapturePolicyError, TypeError), match="CAPTURE_POLICY_VERSION_INVALID"):
        CapturePolicy(policy_version=value, approved_domain_rules=())


@pytest.mark.parametrize("domain_key", ["", "   ", " example.test", "example.test ", "example\n.test", 1, None])
def test_domain_key_is_strict_without_normalization(domain_key):
    with pytest.raises((CapturePolicyError, TypeError), match="CAPTURE_DOMAIN_KEY_INVALID"):
        _rule(domain_key, CaptureMode.FULL_TEXT)


@pytest.mark.parametrize("mode", [CaptureMode.BLOCKED, CaptureMode.UNSUPPORTED])
def test_approved_domain_rules_only_allow_executable_capture_modes(mode):
    with pytest.raises(CapturePolicyError, match="APPROVED_DOMAIN_RULE_MODE_INVALID"):
        _rule(DOMAIN_SENTINEL, mode)


def test_duplicate_approved_domain_rules_are_invalid():
    with pytest.raises(CapturePolicyError, match="CAPTURE_POLICY_DUPLICATE_DOMAIN_RULE"):
        _policy(
            _rule(DOMAIN_SENTINEL, CaptureMode.FULL_TEXT),
            _rule(DOMAIN_SENTINEL, CaptureMode.METADATA_ONLY),
        )


def test_same_mode_duplicate_approved_domain_rules_are_invalid():
    duplicate_rule = _rule(DOMAIN_SENTINEL, CaptureMode.FULL_TEXT)

    with pytest.raises(CapturePolicyError, match="CAPTURE_POLICY_DUPLICATE_DOMAIN_RULE"):
        _policy(duplicate_rule, duplicate_rule)


def test_policy_requires_immutable_tuple_of_exact_rules():
    with pytest.raises(CapturePolicyError, match="CAPTURE_POLICY_RULES_TUPLE_REQUIRED"):
        CapturePolicy(
            policy_version=POLICY_VERSION,
            approved_domain_rules=[_rule(DOMAIN_SENTINEL, CaptureMode.FULL_TEXT)],
        )
    with pytest.raises(CapturePolicyError, match="CAPTURE_POLICY_RULE_REQUIRED"):
        CapturePolicy(
            policy_version=POLICY_VERSION,
            approved_domain_rules=({"domain_key": DOMAIN_SENTINEL},),
        )


def test_evaluator_has_no_hidden_default_policy():
    assert inspect.signature(evaluate_capture_policy).parameters["policy"].default is inspect.Parameter.empty
    with pytest.raises(CapturePolicyError, match="CAPTURE_POLICY_REQUIRED"):
        evaluate_capture_policy(None, DomainClass.SHOPLINE_OWNED)


def test_approved_domain_requires_explicit_valid_domain_key():
    with pytest.raises(CapturePolicyError, match="CAPTURE_DOMAIN_KEY_REQUIRED"):
        evaluate_capture_policy(_policy(), DomainClass.APPROVED_THIRD_PARTY)
    with pytest.raises(CapturePolicyError, match="CAPTURE_DOMAIN_KEY_INVALID"):
        evaluate_capture_policy(
            _policy(),
            DomainClass.APPROVED_THIRD_PARTY,
            domain_key=" example.test",
        )


def test_policy_repr_does_not_expose_domain_rule_payload():
    policy = _policy(_rule(DOMAIN_SENTINEL, CaptureMode.FULL_TEXT))

    assert DOMAIN_SENTINEL not in repr(policy)
    assert DOMAIN_SENTINEL not in repr(policy.approved_domain_rules[0])


@pytest.mark.parametrize("value", [TARGET_SENTINEL, "opaque:target:alpha", "文字目標"])
def test_validated_target_ref_accepts_strict_opaque_text(value):
    target = ValidatedCaptureTargetRef(value)

    assert type(target) is ValidatedCaptureTargetRef
    assert target.to_serializable_value() == value


@pytest.mark.parametrize("value", ["", "   ", " target", "target ", "target\rvalue", 1, None])
def test_validated_target_ref_rejects_malformed_values_without_echo(value):
    with pytest.raises((CapturePolicyError, TypeError), match="CAPTURE_TARGET_REF_INVALID") as exc_info:
        ValidatedCaptureTargetRef(value)

    assert repr(value) not in str(exc_info.value)


def test_target_ref_debug_surfaces_are_redacted(caplog):
    target = _target()
    request = CaptureRequest(target_ref=target, decision=_decision())

    rendered = (repr(target), str(target), repr(request), str(request), caplog.text)
    assert all(TARGET_SENTINEL not in item for item in rendered)


def test_target_ref_pure_and_valid_replace_revalidate_and_preserve_serialization():
    target = _target()

    pure_replacement = replace(target)
    changed_replacement = replace(target, value="other-valid-target")

    assert pure_replacement == target
    assert pure_replacement.to_serializable_value() == TARGET_SENTINEL
    assert changed_replacement.to_serializable_value() == "other-valid-target"


@pytest.mark.parametrize("value", ["", " target", "target ", "target\x00value"])
def test_target_ref_invalid_replace_revalidates_and_fails_closed(value):
    with pytest.raises(CapturePolicyError, match="CAPTURE_TARGET_REF_INVALID"):
        replace(_target(), value=value)


def test_target_ref_remains_frozen_after_replace_refinement():
    target = _target()

    with pytest.raises(FrozenInstanceError):
        target.value = "other-valid-target"


def test_capture_request_rejects_raw_url_string_and_canonical_url():
    for invalid_target in ("https://example.test/raw", _canonical_url()):
        with pytest.raises(CapturePolicyError, match="VALIDATED_CAPTURE_TARGET_REF_REQUIRED") as exc_info:
            CaptureRequest(target_ref=invalid_target, decision=_decision())
        assert "https://example.test" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        (CaptureMode.BLOCKED, PolicyDecisionReason.POLICY_MISSING),
        (CaptureMode.UNSUPPORTED, PolicyDecisionReason.UNSUPPORTED),
    ],
)
def test_non_executable_policy_decision_cannot_form_capture_request(mode, reason):
    with pytest.raises(CapturePolicyError, match="CAPTURE_REQUEST_MODE_NOT_EXECUTABLE"):
        CaptureRequest(target_ref=_target(), decision=_decision(mode, reason))


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        (CaptureMode.FULL_TEXT, PolicyDecisionReason.SHOPLINE_OWNED),
        (CaptureMode.METADATA_ONLY, PolicyDecisionReason.NEEDS_POLICY),
    ],
)
def test_executable_policy_decision_forms_capture_request(mode, reason):
    request = CaptureRequest(target_ref=_target(), decision=_decision(mode, reason))

    assert request.decision.mode is mode
    assert request.policy_version == POLICY_VERSION


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        (CaptureMode.FULL_TEXT, PolicyDecisionReason.NEEDS_POLICY),
        (CaptureMode.METADATA_ONLY, PolicyDecisionReason.SHOPLINE_OWNED),
        (CaptureMode.BLOCKED, PolicyDecisionReason.APPROVED_THIRD_PARTY_RULE),
        (CaptureMode.UNSUPPORTED, PolicyDecisionReason.POLICY_MISSING),
    ],
)
def test_policy_decision_rejects_invalid_mode_reason_pairs(mode, reason):
    with pytest.raises(CapturePolicyError, match="CAPTURE_POLICY_DECISION_INVALID"):
        _decision(mode, reason)


@pytest.mark.parametrize(
    "reason",
    [
        FetchFailureReason.TIMEOUT,
        FetchFailureReason.TEMPORARY_DNS,
        FetchFailureReason.TEMPORARY_NETWORK,
    ],
)
def test_non_http_temporary_failures_are_classified_temporary(reason):
    assert classify_fetch_failure(reason) is FetchFailureCategory.TEMPORARY


@pytest.mark.parametrize("status_code", [429, 500, 599])
def test_authorized_http_failures_are_classified_temporary(status_code):
    assert (
        classify_fetch_failure(FetchFailureReason.HTTP_STATUS, status_code=status_code)
        is FetchFailureCategory.TEMPORARY
    )


@pytest.mark.parametrize("status_code", [400, 404, 408, 499])
def test_other_http_4xx_including_408_are_non_temporary(status_code):
    assert (
        classify_fetch_failure(FetchFailureReason.HTTP_STATUS, status_code=status_code)
        is FetchFailureCategory.NON_TEMPORARY
    )


@pytest.mark.parametrize(
    "reason",
    [
        FetchFailureReason.POLICY_BLOCKED,
        FetchFailureReason.AUTHENTICATED_OR_PAYWALLED,
        FetchFailureReason.GOVERNANCE_REJECTED,
        FetchFailureReason.IDENTITY_RECONCILIATION_FAILED,
        FetchFailureReason.UNSAFE_TARGET,
    ],
)
def test_policy_security_and_governance_failures_are_non_temporary(reason):
    assert classify_fetch_failure(reason) is FetchFailureCategory.NON_TEMPORARY


@pytest.mark.parametrize("status_code", [None, True, False, 200, 399, 600])
def test_http_failure_status_is_required_and_limited_to_400_through_599(status_code):
    with pytest.raises(CapturePolicyError, match="FETCH_FAILURE_HTTP_STATUS_INVALID"):
        classify_fetch_failure(FetchFailureReason.HTTP_STATUS, status_code=status_code)


@pytest.mark.parametrize(
    ("reason", "status_code"),
    [
        (FetchFailureReason.TIMEOUT, 500),
        (FetchFailureReason.TEMPORARY_DNS, 404),
        (FetchFailureReason.POLICY_BLOCKED, 500),
        (FetchFailureReason.UNSAFE_TARGET, 503),
    ],
)
def test_non_http_reason_rejects_http_status(reason, status_code):
    with pytest.raises(CapturePolicyError, match="FETCH_FAILURE_HTTP_STATUS_UNEXPECTED"):
        classify_fetch_failure(reason, status_code=status_code)


def test_unknown_failure_reason_fails_closed():
    with pytest.raises(CapturePolicyError, match="FETCH_FAILURE_REASON_REQUIRED"):
        classify_fetch_failure("timeout")


def test_fetch_result_is_minimal_validated_single_attempt_outcome():
    success = FetchResult(succeeded=True)
    failure = FetchResult(
        succeeded=False,
        failure_reason=FetchFailureReason.HTTP_STATUS,
        http_status_code=429,
    )

    assert success.failure_category is None
    assert failure.failure_category is FetchFailureCategory.TEMPORARY


@pytest.mark.parametrize(
    "reason",
    [
        FetchFailureReason.POLICY_BLOCKED,
        FetchFailureReason.AUTHENTICATED_OR_PAYWALLED,
        FetchFailureReason.GOVERNANCE_REJECTED,
        FetchFailureReason.IDENTITY_RECONCILIATION_FAILED,
        FetchFailureReason.UNSAFE_TARGET,
    ],
)
def test_non_fetch_failures_cannot_claim_a_fetch_result(reason):
    with pytest.raises(CapturePolicyError, match="FETCH_RESULT_REASON_NOT_FETCH_OUTCOME"):
        FetchResult(succeeded=False, failure_reason=reason)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"succeeded": True, "failure_reason": FetchFailureReason.TIMEOUT},
        {"succeeded": True, "http_status_code": 500},
        {"succeeded": False},
        {"succeeded": False, "failure_reason": "timeout"},
        {"succeeded": 1},
    ],
)
def test_fetch_result_rejects_malformed_or_ambiguous_state(kwargs):
    with pytest.raises(CapturePolicyError):
        FetchResult(**kwargs)


def test_fetch_client_is_protocol_only():
    assert getattr(FetchClient, "_is_protocol", False) is True
    with pytest.raises(TypeError):
        FetchClient()
    client_types = [
        value
        for name, value in vars(capture_policy).items()
        if inspect.isclass(value) and name.endswith("Client")
    ]
    assert client_types == [FetchClient]


def test_contract_fields_exclude_credentials_lifecycle_and_captured_content():
    all_field_names = {
        field.name
        for model in (
            CapturePolicyDecision,
            CaptureRequest,
            FetchResult,
        )
        for field in fields(model)
    }
    forbidden = {
        "raw_url",
        "url",
        "headers",
        "cookies",
        "authorization",
        "session",
        "browser_context",
        "retry",
        "backoff",
        "freshness_days",
        "reuse_lkg",
        "stale",
        "previous_capture",
        "last_success",
        "captured_content",
    }

    assert forbidden.isdisjoint(all_field_names)


def test_module_has_no_network_retry_freshness_or_wp11_wp12_imports():
    tree = ast.parse(inspect.getsource(capture_policy))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert imported_roots.isdisjoint(
        {"requests", "httpx", "urllib", "aiohttp", "socket", "time", "datetime"}
    )
    assert "captured_content" not in imported_roots
    assert "html_normalization" not in imported_roots
    assert "content_hashing" not in imported_roots


def test_frozen_dataclass_copy_and_replace_cannot_bypass_validation():
    decision = _decision()
    with pytest.raises(FrozenInstanceError):
        decision.mode = CaptureMode.BLOCKED
    with pytest.raises(CapturePolicyError, match="CAPTURE_POLICY_DECISION_INVALID"):
        replace(decision, mode=CaptureMode.BLOCKED)


def test_all_canonical_frozen_dtos_support_valid_pure_replace():
    rule = _rule(DOMAIN_SENTINEL, CaptureMode.FULL_TEXT)
    policy = _policy(rule)
    decision = evaluate_capture_policy(
        policy,
        DomainClass.APPROVED_THIRD_PARTY,
        domain_key=DOMAIN_SENTINEL,
    )
    target = _target()
    request = CaptureRequest(target_ref=target, decision=decision)
    fetch_result = FetchResult(
        succeeded=False,
        failure_reason=FetchFailureReason.HTTP_STATUS,
        http_status_code=429,
    )

    for value in (rule, policy, decision, target, request, fetch_result):
        assert replace(value) == value


def test_invalid_replace_revalidates_all_cross_field_invariants():
    rule = _rule(DOMAIN_SENTINEL, CaptureMode.FULL_TEXT)
    policy = _policy(rule)
    decision = _decision()
    request = CaptureRequest(target_ref=_target(), decision=decision)
    fetch_result = FetchResult(
        succeeded=False,
        failure_reason=FetchFailureReason.HTTP_STATUS,
        http_status_code=429,
    )
    invalid_replacements = (
        (rule, {"mode": CaptureMode.BLOCKED}, "APPROVED_DOMAIN_RULE_MODE_INVALID"),
        (policy, {"policy_version": " invalid"}, "CAPTURE_POLICY_VERSION_INVALID"),
        (
            policy,
            {"approved_domain_rules": (rule, rule)},
            "CAPTURE_POLICY_DUPLICATE_DOMAIN_RULE",
        ),
        (decision, {"mode": CaptureMode.BLOCKED}, "CAPTURE_POLICY_DECISION_INVALID"),
        (
            request,
            {
                "decision": _decision(
                    CaptureMode.BLOCKED,
                    PolicyDecisionReason.POLICY_MISSING,
                )
            },
            "CAPTURE_REQUEST_MODE_NOT_EXECUTABLE",
        ),
        (
            fetch_result,
            {"failure_reason": FetchFailureReason.TIMEOUT},
            "FETCH_FAILURE_HTTP_STATUS_UNEXPECTED",
        ),
    )

    for value, updates, error_code in invalid_replacements:
        with pytest.raises(CapturePolicyError, match=error_code):
            replace(value, **updates)
