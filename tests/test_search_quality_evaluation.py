"""Tests for the Golden / Negative search-quality harness and its case set.

Two things are checked here and they are deliberately separate. The case set is validated on its own
terms -- shape, uniqueness, self-consistency -- so a case that contradicts itself cannot be scored.
The harness is exercised against a synthetic index and a synthetic Authority, because the value of a
harness is that it *fails* when the system misbehaves; a harness only ever run on passing input has
not been tested at all.

The formal baseline against the pinned Authority and the real index is a separate, conditional test:
it skips where that evidence is not present rather than pretending to have measured it.
"""

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance import GovernanceIndex, RestrictedCustomerRecord
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata, SearchResult
from marketing_knowledge_agent.pipeline import ask_index
from marketing_knowledge_agent.query_planning import TAXONOMY_FIELDS, build_query_plan
from marketing_knowledge_agent import search_evaluation
from marketing_knowledge_agent.search_evaluation import (
    FAILURE_BLOCKED_QUERY_RETURNED_RESULTS,
    FAILURE_UNEXPECTED_RESULT,
    FAILURE_MERCHANT_PRECEDENCE,
    FAILURE_RUNTIME_CATALOG_GAP,
    FAILURE_UNEXPECTED_SEMANTIC_FALLBACK,
    FAILURE_WRONG_CONSTRAINT,
    SearchCase,
    SearchEvaluationError,
    evaluate_search_cases,
    load_search_quality_cases,
    render_evaluation_markdown,
)
from marketing_knowledge_agent.search_taxonomy import load_search_taxonomy

from test_search_taxonomy import write_taxonomy_workbook


CASE_SET_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/search_quality_cases.json"
FORMAL_WORKBOOK = Path(
    "/Volumes/T7/MKA Authority/Search Taxonomy/2026-08-21/"
    "MKA_Search_Taxonomy_Authority_2026-08-21.xlsx"
)
FORMAL_WORKBOOK_SHA256 = "7e6ecffc2d4ad9b931c8abc2d75345305c0a93026ecb1cb10841aa5e8c6597a3"
FORMAL_INDEX = Path(__file__).resolve().parents[1] / ".mka/content_index.sqlite"


@pytest.fixture
def taxonomy(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    return load_search_taxonomy(
        workbook_path=path, expected_sha256=write_taxonomy_workbook(path)
    )


@pytest.fixture
def indexed_db(tmp_path):
    records = [
        _record("莉朵花藝", "lido", "居家生活", "居家生活相關", ["團購解決方案"], 1),
        _record("三風製麵", "sanfong", "美食", "食品/飲料", [], 2),
        # Grocery is a canonical at both levels in the synthetic Authority and exists at both
        # levels here, which is what makes the explicit-scope regression observable.
        _record("好雜貨", "grocerystore", "Grocery", "Grocery", [], 3),
        _record("衣衣選品", "yiyi", "美食", "女裝", [], 4),
    ]
    documents = [
        Document(id=f"doc-{index}", metadata=metadata, content=content)
        for index, (metadata, content) in enumerate(records, start=1)
    ]
    db_path = tmp_path / "quality-index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return db_path


def _run(cases, db_path, taxonomy=None):
    return evaluate_search_cases(cases, db_path=db_path, taxonomy=taxonomy)


def _only(report):
    assert len(report.outcomes) == 1
    return report.outcomes[0]


# --------------------------------------------------------------------------------------
# Case set integrity
# --------------------------------------------------------------------------------------


def test_case_set_loads_and_is_deterministically_ordered():
    cases = load_search_quality_cases(CASE_SET_PATH)
    assert cases
    assert [case.id for case in cases] == sorted(case.id for case in cases)
    assert len({case.id for case in cases}) == len(cases)


def test_case_set_covers_both_classes_and_the_required_case_types():
    cases = load_search_quality_cases(CASE_SET_PATH)
    by_class = {"golden": set(), "negative": set()}
    for case in cases:
        by_class[case.case_class].add(case.case_type)
    for required in (
        "lv1_canonical",
        "lv1_expansion",
        "lv2_canonical",
        "content_tag_canonical",
        "merchant_name",
        "merchant_handle",
    ):
        assert required in by_class["golden"], required
    for required in (
        "cross_level_ambiguity",
        "intra_field_collision",
        "known_but_not_indexed",
        "typo_suggestion_only",
        "typo_ambiguous_suggestion",
        "unknown_term",
    ):
        assert required in by_class["negative"], required


def test_every_abstain_case_names_the_reason_it_expects():
    for case in load_search_quality_cases(CASE_SET_PATH):
        if case.expected_behavior == "abstain":
            assert case.expect_blocked
            assert case.expected_abstain_reason


def test_known_but_not_indexed_is_never_conflated_with_an_unknown_term():
    cases = {case.id: case for case in load_search_quality_cases(CASE_SET_PATH)}
    assert cases["N-NOTIDX-01"].expected_abstain_reason == "taxonomy_known_but_not_indexed"
    assert cases["N-UNKNOWN-01"].expected_abstain_reason == "unresolved_structured_lookup"


def test_the_case_set_records_no_known_gap():
    """R1 closed the short-alias gap, so nothing in the dataset may still be excused as known.

    A declared gap is a standing exemption from the exit gate. Leaving one behind after its defect
    is fixed is how a dataset goes quietly green, so the dataset must carry none.
    """
    declared = [
        case.id
        for case in load_search_quality_cases(CASE_SET_PATH)
        if case.expected_failure_reason is not None
    ]
    assert declared == []


def test_the_short_alias_cases_assert_no_taxonomy_constraint_and_a_refusal():
    """The B1 regression cases, named so a later edit cannot weaken them silently."""
    cases = {case.id: case for case in load_search_quality_cases(CASE_SET_PATH)}
    for case_id in ("N-SHORT-01", "N-SHORT-03", "N-SHORT-04", "N-SHORT-05"):
        case = cases[case_id]
        assert case.forbid_taxonomy_constraint, case_id
        assert case.expect_blocked, case_id
        assert case.expected_abstain_reason == "unresolved_structured_lookup", case_id
    # One case covers the other post-suppression path: the planner's own semantics is not a
    # refusal here, and what matters is still that no taxonomy filter was bound.
    assert cases["N-SHORT-06"].forbid_taxonomy_constraint
    assert not cases["N-SHORT-06"].expect_blocked


def test_loader_refuses_a_duplicate_case_id(tmp_path):
    payload = {"cases": [_raw_case("dup"), _raw_case("dup")]}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SearchEvaluationError, match="duplicate case id"):
        load_search_quality_cases(path)


def test_loader_refuses_an_unknown_case_field(tmp_path):
    raw = _raw_case("x")
    raw["expect_miracle"] = True
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [raw]}), encoding="utf-8")
    with pytest.raises(SearchEvaluationError, match="unknown fields"):
        load_search_quality_cases(path)


def test_loader_refuses_a_case_that_expects_results_from_a_blocked_plan(tmp_path):
    raw = _raw_case("x")
    raw.update(
        expected_behavior="constraint_and_results",
        expected_field="content_tags",
        expected_canonical="團購解決方案",
        expected_operator="contains_exact_tag",
        expect_blocked=True,
    )
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [raw]}), encoding="utf-8")
    with pytest.raises(SearchEvaluationError, match="expects results from a blocked plan"):
        load_search_quality_cases(path)


def test_loader_refuses_a_constraint_case_that_does_not_say_which_constraint(tmp_path):
    raw = _raw_case("x")
    raw["expected_behavior"] = "constraint_and_results"
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [raw]}), encoding="utf-8")
    with pytest.raises(SearchEvaluationError, match="does not say which one"):
        load_search_quality_cases(path)


# --------------------------------------------------------------------------------------
# The harness must fail when the system misbehaves
# --------------------------------------------------------------------------------------


def test_harness_passes_a_clean_constraint_case(indexed_db, taxonomy):
    case = _case(
        "ok",
        "家居生活",
        expected_behavior="constraint_and_results",
        expected_field="sales_category_lv1",
        expected_canonical="居家生活",
        expected_operator="canonical_exact",
        forbid_semantic_fallback=True,
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "PASS"
    assert outcome.observation.result_count == 1
    assert outcome.observation.result_brands == ("莉朵花藝",)


def test_harness_reports_a_semantic_fallback_when_one_is_forbidden(indexed_db):
    """Results with no hard filter at all are the system answering from similarity."""
    case = _case(
        "fallback",
        "如何提升整體成效",
        expected_behavior="semantic_baseline",
        forbid_semantic_fallback=True,
    )
    outcome = _only(_run([case], indexed_db))
    assert outcome.status == "FAIL"
    assert outcome.failure_reason == FAILURE_UNEXPECTED_SEMANTIC_FALLBACK
    assert outcome.observation.semantic_fallback is True


def test_harness_reports_a_taxonomy_constraint_bound_where_none_was_wanted(
    indexed_db, taxonomy
):
    case = _case(
        "unwanted",
        "家居生活",
        expected_behavior="semantic_baseline",
        forbid_taxonomy_constraint=True,
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "FAIL"
    assert outcome.failure_reason == FAILURE_WRONG_CONSTRAINT


def test_harness_separates_an_index_gap_from_a_resolution_failure(indexed_db, taxonomy):
    """A value nothing in the index carries is a coverage gap, not a broken resolver."""
    case = _case(
        "gap",
        "只有權威沒有索引",
        expected_behavior="constraint_and_results",
        expected_field="sales_category_lv1",
        expected_canonical="未進索引大類",
        expected_operator="canonical_exact",
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "FAIL"
    assert outcome.failure_reason == FAILURE_RUNTIME_CATALOG_GAP
    assert outcome.observation.index_match_count == 0


def test_harness_reports_lost_merchant_precedence(indexed_db, taxonomy):
    case = _case("identity", "三風製麵", expected_behavior="identity")
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "PASS"

    broken = _case("identity-miss", "居家生活相關", expected_behavior="identity")
    outcome = _only(_run([broken], indexed_db, taxonomy))
    assert outcome.status == "FAIL"
    assert outcome.failure_reason == FAILURE_MERCHANT_PRECEDENCE


def test_harness_reports_a_typo_that_became_a_constraint(indexed_db, taxonomy):
    good = _case("typo", "未進索引標簽", expected_behavior="suggestion_only", expect_blocked=True)
    assert _only(_run([good], indexed_db, taxonomy)).status == "PASS"

    # "團購" is a real alias, so this query legitimately binds a tag -- which is exactly what a
    # suggestion-only case must refuse to accept.
    bad = _case("typo-bound", "團購", expected_behavior="suggestion_only")
    outcome = _only(_run([bad], indexed_db, taxonomy))
    assert outcome.status == "FAIL"
    assert outcome.failure_reason == FAILURE_WRONG_CONSTRAINT


def test_harness_counts_index_matches_so_an_empty_answer_can_be_judged(indexed_db, taxonomy):
    case = _case(
        "counted",
        "團購",
        expected_behavior="constraint_and_results",
        expected_field="content_tags",
        expected_canonical="團購解決方案",
        expected_operator="contains_exact_tag",
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "PASS"
    assert outcome.observation.index_match_count == 1


def test_report_summary_and_markdown_expose_failure_classes(indexed_db, taxonomy):
    cases = [
        _case(
            "g1",
            "家居生活",
            expected_behavior="constraint_and_results",
            expected_field="sales_category_lv1",
            expected_canonical="居家生活",
            expected_operator="canonical_exact",
        ),
        _case(
            "n1",
            "居家生活",
            case_class="negative",
            expected_behavior="abstain",
            expect_blocked=True,
            expected_abstain_reason="ambiguous_taxonomy_term",
        ),
        _case(
            "n2",
            "如何提升整體成效",
            case_class="negative",
            expected_behavior="semantic_baseline",
            forbid_semantic_fallback=True,
        ),
    ]
    report = _run(cases, indexed_db, taxonomy)
    summary = report.summary
    assert summary["golden_pass"] == 1
    assert summary["negative_pass"] == 1
    assert summary["negative_fail"] == 1
    assert summary["failure_reasons"] == {FAILURE_UNEXPECTED_SEMANTIC_FALLBACK: 1}
    markdown = render_evaluation_markdown(report)
    assert FAILURE_UNEXPECTED_SEMANTIC_FALLBACK in markdown
    assert "n2" in markdown


# --------------------------------------------------------------------------------------
# Regression: an explicitly scoped query must not widen into the other level
# --------------------------------------------------------------------------------------


def test_explicit_lv2_scope_does_not_also_bind_lv1(indexed_db, taxonomy):
    """Grocery is canonical at both levels and indexed at both; scoping must still hold."""
    case = _case(
        "scope-lv2",
        "sales_category_lv2=grocery",
        expected_behavior="constraint_and_results",
        expected_field="sales_category_lv2",
        expected_canonical="Grocery",
        expected_operator="canonical_exact",
        forbid_extra_taxonomy_fields=True,
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "PASS", outcome.failure_detail


def test_explicit_lv1_scope_does_not_also_bind_lv2(indexed_db, taxonomy):
    case = _case(
        "scope-lv1",
        "sales_category_lv1=grocery",
        expected_behavior="constraint_and_results",
        expected_field="sales_category_lv1",
        expected_canonical="Grocery ",
        expected_operator="canonical_exact",
        forbid_extra_taxonomy_fields=True,
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "PASS", outcome.failure_detail


def test_explicitly_scoped_plan_constrains_exactly_one_taxonomy_field(indexed_db, taxonomy):
    from marketing_knowledge_agent.pipeline import build_index_query_plan

    plan = build_index_query_plan("sales_category_lv2=grocery", indexed_db, taxonomy=taxonomy)
    fields = [item.field for item in plan.constraints if item.field in TAXONOMY_FIELDS]
    assert fields == ["sales_category_lv2"]


def test_the_scope_fix_did_not_disable_the_catalog_pass(indexed_db, taxonomy):
    """A second, genuinely different term in the same query must still resolve."""
    plan = build_query_plan(
        "sales_category_lv1=美食 團購",
        _catalog(indexed_db),
        taxonomy=taxonomy,
    )
    bound = {item.field: item.value for item in plan.constraints}
    assert bound["sales_category_lv1"] == "美食"
    assert bound["content_tags"] == "團購解決方案"


# --------------------------------------------------------------------------------------
# Governance safety
# --------------------------------------------------------------------------------------


def test_a_taxonomy_constraint_does_not_bypass_the_restricted_denylist(indexed_db, taxonomy):
    """The Authority decides what a word means. It never decides who may be shown."""
    governance = GovernanceIndex(
        [RestrictedCustomerRecord(brand_name="莉朵花藝", source_sheet="test", source_row=1)]
    )
    answer = ask_index(
        "家居生活",
        db_path=indexed_db,
        governance_index=governance,
        taxonomy=taxonomy,
    )
    assert all("莉朵花藝" not in citation.title for citation in answer.citations)
    assert answer.citations == []


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_cli_requires_an_explicit_index(capsys):
    with pytest.raises(SystemExit):
        main(["evaluate-search", "--cases", str(CASE_SET_PATH)])


def test_cli_reports_an_unreadable_case_set(tmp_path, indexed_db, capsys):
    missing = tmp_path / "absent.json"
    assert main(["evaluate-search", "--db", str(indexed_db), "--cases", str(missing)]) == 2
    assert "search evaluation error" in capsys.readouterr().err


def test_cli_writes_a_report_and_signals_golden_regressions(tmp_path, indexed_db, capsys):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "g1",
                        "query": "如何提升整體成效",
                        "case_class": "golden",
                        "case_type": "semantic_question_baseline",
                        "expected_behavior": "semantic_baseline",
                        "forbid_semantic_fallback": True,
                        "notes": "deliberately failing",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report"
    exit_code = main(
        [
            "evaluate-search",
            "--db",
            str(indexed_db),
            "--cases",
            str(cases_path),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 1
    assert (output / "search_evaluation.json").is_file()
    assert (output / "search_evaluation.md").is_file()
    assert json.loads(capsys.readouterr().out)["golden_fail"] == 1


# --------------------------------------------------------------------------------------
# Explicit field isolation, fall-through branch (R1, N1)
# --------------------------------------------------------------------------------------
#
# The earlier fix only covered the branch where the Authority resolves the value inside the named
# field. When it does not -- because the value belongs to a different field, or to none -- the
# explicit parser used to fall through and leave its own text readable, so the catalog pass bound
# a second taxonomy field from the span the user had already scoped.


def _taxonomy_fields(plan):
    return sorted({item.field for item in plan.constraints if item.field in TAXONOMY_FIELDS})


@pytest.mark.parametrize(
    ("query", "expected_field"),
    [
        # 女裝 is an LV2 canonical in the synthetic Authority, scoped here to LV1.
        ("sales_category_lv1=女裝", "sales_category_lv1"),
        # 美食 is an LV1 canonical, scoped here to LV2.
        ("sales_category_lv2=美食", "sales_category_lv2"),
        # A value the Authority does not know at all still may not widen.
        ("sales_category_lv1=居家生活相關", "sales_category_lv1"),
    ],
)
def test_an_explicitly_scoped_value_the_authority_places_elsewhere_binds_one_field(
    query, expected_field, indexed_db, taxonomy
):
    plan = build_query_plan(query, _catalog(indexed_db), taxonomy=taxonomy)
    assert _taxonomy_fields(plan) == [expected_field]


def test_the_same_isolation_holds_without_an_authority(indexed_db):
    """The rule is about who owns the text, so it cannot depend on a taxonomy being supplied."""
    for query, expected_field in (
        ("sales_category_lv1=女裝", "sales_category_lv1"),
        ("sales_category_lv2=美食", "sales_category_lv2"),
    ):
        plan = build_query_plan(query, _catalog(indexed_db))
        assert _taxonomy_fields(plan) == [expected_field], query


def test_an_explicit_scope_beside_free_text_claims_only_its_own_span(indexed_db, taxonomy):
    """The claimed span is spent; genuinely unclaimed text is still read normally."""
    plan = build_query_plan(
        "sales_category_lv1=美食 團購", _catalog(indexed_db), taxonomy=taxonomy
    )
    bound = {item.field: item.value for item in plan.constraints}
    assert bound["sales_category_lv1"] == "美食"
    assert bound["content_tags"] == "團購解決方案"


def test_an_explicit_scope_in_an_or_query_does_not_rescope_its_own_value(indexed_db, taxonomy):
    """OR is where a leaked mirror constraint would actually return records rather than none.

    Under AND a spurious second constraint merely empties the answer; under OR it widens it, so
    the mirror of an explicitly scoped value is what must be absent. 美食 is an LV1 canonical here,
    so a mirror would land in ``sales_category_lv1``; the genuinely unclaimed 團購 beside it is
    still read normally.
    """
    plan = build_query_plan(
        "sales_category_lv2=美食 或 團購", _catalog(indexed_db), taxonomy=taxonomy
    )
    assert plan.operator == "OR"
    assert _taxonomy_fields(plan) == ["content_tags", "sales_category_lv2"]


def test_an_explicit_scope_beside_a_merchant_keeps_identity_precedence(indexed_db, taxonomy):
    plan = build_query_plan(
        "sales_category_lv1=女裝 三風製麵", _catalog(indexed_db), taxonomy=taxonomy
    )
    assert _taxonomy_fields(plan) == ["sales_category_lv1"]
    assert [entity.canonical_name for entity in plan.resolved_entities] == ["三風製麵"]


# --------------------------------------------------------------------------------------
# Blocked queries are asserted at retrieval, not only in the plan (R1, N2)
# --------------------------------------------------------------------------------------


def test_a_blocked_case_fails_when_retrieval_still_returns_results(
    monkeypatch, indexed_db, taxonomy
):
    """The must-fail test for the assertion itself.

    A plan that abstains while some later stage still hands results to the caller is exactly the
    failure a plan-only assertion cannot see, so the harness is made to observe it here.
    """
    leaked = [
        SearchResult(chunk=item.chunk, score=1.0)
        for item in SQLiteIndex(indexed_db).load_chunks()[:2]
    ]
    monkeypatch.setattr(search_evaluation, "search_index", lambda *args, **kwargs: leaked)
    case = _case(
        "blocked-but-answered",
        "居家生活",
        case_class="negative",
        expected_behavior="abstain",
        expect_blocked=True,
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "FAIL"
    assert outcome.failure_reason == FAILURE_BLOCKED_QUERY_RETURNED_RESULTS


def test_a_blocked_case_passes_only_with_an_empty_result_set(indexed_db, taxonomy):
    case = _case(
        "blocked-and-empty",
        "居家生活",
        case_class="negative",
        expected_behavior="abstain",
        expect_blocked=True,
    )
    outcome = _only(_run([case], indexed_db, taxonomy))
    assert outcome.status == "PASS"
    assert outcome.observation.execution_blocked
    assert outcome.observation.result_count == 0


# --------------------------------------------------------------------------------------
# Exit gate: a Negative regression must not pass silently (R1, N3)
# --------------------------------------------------------------------------------------


def _failing_negative(case_id, **kwargs):
    """A Negative case that fails as ``unexpected_result``: it demands a block it will not get."""
    return _case(
        case_id,
        "三風製麵",
        case_class="negative",
        expected_behavior="abstain",
        expect_blocked=True,
        **kwargs,
    )


def test_an_unrecorded_negative_failure_counts_as_unexpected(indexed_db, taxonomy):
    report = _run([_failing_negative("n1")], indexed_db, taxonomy)
    assert report.summary["negative_fail"] == 1
    assert report.summary["unexpected_failures"] == 1
    assert report.summary["unexpected_failure_ids"] == ["n1"]


def test_a_declared_gap_is_excused_only_for_the_exact_failure_it_declares(
    indexed_db, taxonomy
):
    """``expected_failure_reason`` is an acknowledgement of one gap, not a blanket amnesty."""
    exact = _run(
        [_failing_negative("n1", expected_failure_reason=FAILURE_UNEXPECTED_RESULT)],
        indexed_db,
        taxonomy,
    )
    assert exact.summary["unexpected_failures"] == 0
    assert exact.summary["known_expected_failure_ids"] == ["n1"]
    # The status is still FAIL: a declaration never turns a failure into a pass.
    assert _only(exact).status == "FAIL"

    mismatched = _run(
        [_failing_negative("n1", expected_failure_reason=FAILURE_WRONG_CONSTRAINT)],
        indexed_db,
        taxonomy,
    )
    assert mismatched.summary["unexpected_failures"] == 1
    assert mismatched.summary["known_expected_failure_ids"] == []


def test_cli_exits_nonzero_on_an_unexpected_negative_failure(tmp_path, indexed_db, capsys):
    exit_code = _run_cli(tmp_path, indexed_db, _raw_failing_negative())
    assert exit_code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["golden_fail"] == 0
    assert summary["unexpected_failures"] == 1


def test_cli_exits_nonzero_when_a_declared_gap_fails_for_a_different_reason(
    tmp_path, indexed_db, capsys
):
    raw = _raw_failing_negative()
    raw["expected_failure_reason"] = FAILURE_WRONG_CONSTRAINT
    exit_code = _run_cli(tmp_path, indexed_db, raw)
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["unexpected_failures"] == 1


def test_cli_exits_zero_for_a_gap_declared_by_its_exact_failure_reason(
    tmp_path, indexed_db, capsys
):
    raw = _raw_failing_negative()
    raw["expected_failure_reason"] = FAILURE_UNEXPECTED_RESULT
    exit_code = _run_cli(tmp_path, indexed_db, raw)
    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["negative_fail"] == 1
    assert summary["unexpected_failures"] == 0
    assert summary["known_expected_failure_ids"] == ["n1"]


def _raw_failing_negative():
    return {
        "id": "n1",
        "query": "三風製麵",
        "case_class": "negative",
        "case_type": "synthetic",
        "expected_behavior": "abstain",
        "expect_blocked": True,
        "notes": "deliberately failing negative",
    }


def _run_cli(tmp_path, indexed_db, raw_case):
    cases_path = tmp_path / "exit-gate-cases.json"
    cases_path.write_text(
        json.dumps({"cases": [raw_case]}, ensure_ascii=False), encoding="utf-8"
    )
    return main(
        ["evaluate-search", "--db", str(indexed_db), "--cases", str(cases_path)]
    )


# --------------------------------------------------------------------------------------
# Formal baseline (conditional)
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(
    not FORMAL_WORKBOOK.is_file() or not FORMAL_INDEX.is_file(),
    reason="formal Search Taxonomy Authority or formal content index is not present on this machine",
)
def test_formal_baseline_has_no_golden_regression(tmp_path):
    """Runs the real case set against a copy of the real index. Never the production file itself."""
    scratch = tmp_path / "content_index.sqlite"
    shutil.copyfile(FORMAL_INDEX, scratch)
    before = hashlib.sha256(FORMAL_INDEX.read_bytes()).hexdigest()

    formal_taxonomy = load_search_taxonomy(
        workbook_path=FORMAL_WORKBOOK, expected_sha256=FORMAL_WORKBOOK_SHA256
    )
    report = evaluate_search_cases(
        load_search_quality_cases(CASE_SET_PATH), db_path=scratch, taxonomy=formal_taxonomy
    )
    summary = report.summary
    assert summary["golden_fail"] == 0, [
        (item.case.id, item.failure_detail)
        for item in report.outcomes
        if item.status == "FAIL" and item.case.case_class == "golden"
    ]
    # Every negative case passes except the recorded one-character-alias gap, which must keep
    # failing until that matching rule is decided rather than being quietly accepted.
    failing = {item.case.id for item in report.outcomes if item.status == "FAIL"}
    assert failing == {"N-SHORT-01"}
    assert hashlib.sha256(FORMAL_INDEX.read_bytes()).hexdigest() == before


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _catalog(db_path):
    from marketing_knowledge_agent.query_planning import QueryCatalog

    return QueryCatalog.from_metadata(
        item.chunk.metadata for item in SQLiteIndex(db_path).load_chunks()
    )


def _case(case_id, query, *, case_class="golden", **kwargs):
    return SearchCase(
        id=case_id,
        query=query,
        case_class=case_class,
        case_type="synthetic",
        notes="synthetic harness test",
        **kwargs,
    )


def _raw_case(case_id):
    return {
        "id": case_id,
        "query": "團購",
        "case_class": "golden",
        "case_type": "synthetic",
        "expected_behavior": "semantic_baseline",
        "notes": "synthetic",
    }


def _record(brand_name, handle, lv1, lv2, content_tags, source_row):
    title = f"{brand_name}案例"
    metadata = DocumentMetadata(
        title=title,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 7, 1),
        source_path=f"商家夥伴案例資料庫:{source_row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=source_row,
        brand_name=brand_name,
        merchant_handle=handle,
        merchant_status="現有商家",
        interview_year=2026,
        sales_category_lv1=lv1,
        sales_category_lv2=lv2,
        content_tags=content_tags,
        article_title=title,
        data_classification="public",
        can_quote_externally=True,
    )
    return metadata, f"{title}\n{brand_name}"
