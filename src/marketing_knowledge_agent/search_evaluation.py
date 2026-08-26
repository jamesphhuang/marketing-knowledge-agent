"""Deterministic Golden / Negative search-quality evaluation.

This harness answers *what did the system actually do for this query?* rather than *did the parser
return PASS?* Every case is observed at two levels at once -- the typed query plan and the retrieval
it produced -- because a plan that abstains correctly while the pipeline still returns unrelated
records is a failure the plan alone cannot show.

The harness only reads. It opens the index through the ordinary ``SQLiteIndex`` reader, which
connects read-write and may create journal sidecars, so a caller evaluating a production index must
hand this function a scratch copy rather than the production file itself.

Nothing here relaxes a refusal. A case whose expectation is "abstain" passes only by abstaining, and
a case whose expectation is "no taxonomy constraint" fails loudly when one is bound -- the dataset
records the behaviour the system should have, not the behaviour it currently has.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .indexing import SQLiteIndex
from .models import SearchFilters, SearchResult
from .pipeline import build_index_query_plan, search_index
from .query_planning import (
    TAXONOMY_FIELDS,
    TAXONOMY_SOURCE,
    QueryCatalog,
    TypedQueryPlan,
    normalize_exact_value,
)


class SearchEvaluationError(ValueError):
    """Raised when a case set cannot be trusted as an evaluation input."""


CASE_CLASSES = ("golden", "negative")

# What a case asserts about the system's behaviour.
BEHAVIOR_CONSTRAINT_AND_RESULTS = "constraint_and_results"
BEHAVIOR_ABSTAIN = "abstain"
BEHAVIOR_IDENTITY = "identity"
BEHAVIOR_SUGGESTION_ONLY = "suggestion_only"
BEHAVIOR_SEMANTIC_BASELINE = "semantic_baseline"
EXPECTED_BEHAVIORS = (
    BEHAVIOR_CONSTRAINT_AND_RESULTS,
    BEHAVIOR_ABSTAIN,
    BEHAVIOR_IDENTITY,
    BEHAVIOR_SUGGESTION_ONLY,
    BEHAVIOR_SEMANTIC_BASELINE,
)

# Failure classes. A raw pass rate hides which of these is moving, and they need different owners:
# a taxonomy defect is this candidate's problem, an index gap is a re-index decision, and an
# ingestion defect is upstream of search entirely.
FAILURE_TAXONOMY_RESOLUTION = "taxonomy_resolution"
FAILURE_RUNTIME_CATALOG_GAP = "runtime_catalog_gap"
FAILURE_UNEXPECTED_SEMANTIC_FALLBACK = "unexpected_semantic_fallback"
FAILURE_UNEXPECTED_AMBIGUITY = "unexpected_ambiguity"
FAILURE_UNEXPECTED_RESULT = "unexpected_result"
FAILURE_WRONG_CONSTRAINT = "wrong_constraint"
FAILURE_MERCHANT_PRECEDENCE = "merchant_precedence"
FAILURE_DATA_QUALITY = "data_quality"
FAILURE_INGESTION_QUALITY = "ingestion_quality"
# A refusal is only a refusal if nothing came back. Observing the plan alone cannot tell you that:
# a plan can abstain while some later stage still hands results to the caller, and that is the
# failure a plan-only assertion is structurally unable to see.
FAILURE_BLOCKED_QUERY_RETURNED_RESULTS = "blocked_query_returned_results"
FAILURE_REASONS = (
    FAILURE_TAXONOMY_RESOLUTION,
    FAILURE_RUNTIME_CATALOG_GAP,
    FAILURE_UNEXPECTED_SEMANTIC_FALLBACK,
    FAILURE_UNEXPECTED_AMBIGUITY,
    FAILURE_UNEXPECTED_RESULT,
    FAILURE_WRONG_CONSTRAINT,
    FAILURE_MERCHANT_PRECEDENCE,
    FAILURE_DATA_QUALITY,
    FAILURE_INGESTION_QUALITY,
    FAILURE_BLOCKED_QUERY_RETURNED_RESULTS,
)

# Which failure class an unexpected refusal belongs to, keyed by the plan's own abstain reason.
_BLOCK_REASON_CLASSES = {
    "taxonomy_known_but_not_indexed": FAILURE_RUNTIME_CATALOG_GAP,
    "ambiguous_taxonomy_term": FAILURE_UNEXPECTED_AMBIGUITY,
    "unresolved_structured_lookup": FAILURE_TAXONOMY_RESOLUTION,
}

CASE_FIELDS = {
    "id",
    "query",
    "case_class",
    "case_type",
    "expected_behavior",
    "expected_field",
    "expected_canonical",
    "expected_operator",
    "expect_blocked",
    "expected_abstain_reason",
    "forbid_semantic_fallback",
    "forbid_taxonomy_constraint",
    "forbid_extra_taxonomy_fields",
    "expected_failure_reason",
    "notes",
}
REQUIRED_CASE_FIELDS = {"id", "query", "case_class", "case_type", "expected_behavior", "notes"}


@dataclass(frozen=True)
class SearchCase:
    id: str
    query: str
    case_class: str
    case_type: str
    expected_behavior: str
    notes: str
    expected_field: Optional[str] = None
    expected_canonical: Optional[str] = None
    expected_operator: Optional[str] = None
    expect_blocked: bool = False
    expected_abstain_reason: Optional[str] = None
    forbid_semantic_fallback: bool = False
    forbid_taxonomy_constraint: bool = False
    # True when the query names exactly one taxonomy field and no other taxonomy field may be
    # constrained -- an explicitly scoped ``sales_category_lv2=…`` must not also acquire an LV1
    # filter, whichever stage bound it.
    forbid_extra_taxonomy_fields: bool = False
    # Set when the dataset records a behaviour the system does not yet have. The case still fails;
    # this only says which bucket the known gap belongs to, so a expected-to-fail case cannot be
    # quietly reclassified as a pass.
    expected_failure_reason: Optional[str] = None


@dataclass(frozen=True)
class CaseObservation:
    """Everything one case revealed, plan and retrieval together."""

    raw_query: str
    normalized_query: str
    query_mode: str
    taxonomy_constraints: Tuple[Dict[str, Any], ...]
    all_constraints: Tuple[Dict[str, Any], ...]
    resolved_entities: Tuple[Dict[str, Any], ...]
    ambiguity_flags: Tuple[str, ...]
    parser_warnings: Tuple[str, ...]
    abstain_reason: Optional[str]
    execution_blocked: bool
    semantic_fallback: bool
    result_count: int
    result_document_ids: Tuple[str, ...]
    result_brands: Tuple[str, ...]
    result_titles: Tuple[str, ...]
    offending_results: Tuple[str, ...]
    index_match_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "normalized_query": self.normalized_query,
            "query_mode": self.query_mode,
            "taxonomy_constraints": [dict(item) for item in self.taxonomy_constraints],
            "all_constraints": [dict(item) for item in self.all_constraints],
            "resolved_entities": [dict(item) for item in self.resolved_entities],
            "ambiguity_flags": list(self.ambiguity_flags),
            "parser_warnings": list(self.parser_warnings),
            "abstain_reason": self.abstain_reason,
            "execution_blocked": self.execution_blocked,
            "semantic_fallback": self.semantic_fallback,
            "result_count": self.result_count,
            "result_document_ids": list(self.result_document_ids),
            "result_brands": list(self.result_brands),
            "result_titles": list(self.result_titles),
            "offending_results": list(self.offending_results),
            "index_match_count": self.index_match_count,
        }


@dataclass(frozen=True)
class CaseOutcome:
    case: SearchCase
    status: str
    observation: CaseObservation
    failure_reason: Optional[str] = None
    failure_detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case.id,
            "case_class": self.case.case_class,
            "case_type": self.case.case_type,
            "query": self.case.query,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "failure_detail": self.failure_detail,
            "notes": self.case.notes,
            "observation": self.observation.to_dict(),
        }


@dataclass(frozen=True)
class EvaluationReport:
    outcomes: Tuple[CaseOutcome, ...]
    index_path: str
    taxonomy_workbook_path: Optional[str]
    taxonomy_workbook_sha256: Optional[str]

    def _by_class(self, case_class: str, status: str) -> int:
        return sum(
            1
            for outcome in self.outcomes
            if outcome.case.case_class == case_class and outcome.status == status
        )

    @property
    def unexpected_failures(self) -> Tuple[CaseOutcome, ...]:
        """Failing cases the dataset did not already record, by exact failure class.

        ``expected_failure_reason`` is a narrow acknowledgement of one known gap, not an amnesty:
        it excuses a case only when the failure observed is the very one recorded. A case that
        starts failing for a *different* reason is a new regression wearing an old label, and is
        reported here so the exit gate sees it.
        """
        return tuple(
            outcome
            for outcome in self.outcomes
            if outcome.status == "FAIL"
            and outcome.case.expected_failure_reason != outcome.failure_reason
        )

    @property
    def summary(self) -> Dict[str, Any]:
        golden_pass = self._by_class("golden", "PASS")
        golden_fail = self._by_class("golden", "FAIL")
        negative_pass = self._by_class("negative", "PASS")
        negative_fail = self._by_class("negative", "FAIL")
        total = len(self.outcomes)
        passed = golden_pass + negative_pass
        failure_counts: Dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.failure_reason:
                failure_counts[outcome.failure_reason] = (
                    failure_counts.get(outcome.failure_reason, 0) + 1
                )
        unexpected = self.unexpected_failures
        return {
            "golden_cases": golden_pass + golden_fail,
            "golden_pass": golden_pass,
            "golden_fail": golden_fail,
            "negative_cases": negative_pass + negative_fail,
            "negative_pass": negative_pass,
            "negative_fail": negative_fail,
            "total": total,
            # The exit gate reads these two, not the pass rate. A Negative case is the guard
            # against answering when the system should refuse, so a new Negative failure has to
            # fail the command as loudly as a Golden one.
            "unexpected_failures": len(unexpected),
            "unexpected_failure_ids": [outcome.case.id for outcome in unexpected],
            "known_expected_failure_ids": [
                outcome.case.id
                for outcome in self.outcomes
                if outcome.status == "FAIL" and outcome.case.expected_failure_reason
                and outcome.case.expected_failure_reason == outcome.failure_reason
            ],
            # Reported, but never the headline: the failure breakdown below is what says whether a
            # miss belongs to search, to the index, or upstream of both.
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "failure_reasons": dict(sorted(failure_counts.items())),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index_path": self.index_path,
            "taxonomy_workbook_path": self.taxonomy_workbook_path,
            "taxonomy_workbook_sha256": self.taxonomy_workbook_sha256,
            "summary": self.summary,
            "cases": [outcome.to_dict() for outcome in self.outcomes],
        }


def load_search_quality_cases(path: Path) -> Tuple[SearchCase, ...]:
    """Load and validate a case set. An unreadable or self-contradictory set is refused."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchEvaluationError(f"case set {path} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise SearchEvaluationError(f"case set {path} must be an object with a 'cases' list")

    cases: List[SearchCase] = []
    seen_ids: Dict[str, int] = {}
    for index, raw in enumerate(payload["cases"], start=1):
        if not isinstance(raw, dict):
            raise SearchEvaluationError(f"case {index} in {path} is not an object")
        unknown = set(raw) - CASE_FIELDS
        if unknown:
            raise SearchEvaluationError(f"case {index} in {path} has unknown fields {sorted(unknown)}")
        missing = REQUIRED_CASE_FIELDS - set(raw)
        if missing:
            raise SearchEvaluationError(f"case {index} in {path} is missing {sorted(missing)}")
        case = SearchCase(**raw)
        if case.id in seen_ids:
            raise SearchEvaluationError(
                f"duplicate case id {case.id!r} in {path} (also case {seen_ids[case.id]})"
            )
        seen_ids[case.id] = index
        _validate_case(case, path)
        cases.append(case)

    if not cases:
        raise SearchEvaluationError(f"case set {path} contains no cases")
    # Ordering is part of the contract: a reproducible baseline must not depend on file order.
    return tuple(sorted(cases, key=lambda item: item.id))


def _validate_case(case: SearchCase, path: Path) -> None:
    if case.case_class not in CASE_CLASSES:
        raise SearchEvaluationError(f"case {case.id} in {path} has unknown case_class")
    if case.expected_behavior not in EXPECTED_BEHAVIORS:
        raise SearchEvaluationError(f"case {case.id} in {path} has unknown expected_behavior")
    if case.expected_field is not None and case.expected_field not in TAXONOMY_FIELDS:
        raise SearchEvaluationError(f"case {case.id} in {path} names a non-taxonomy expected_field")
    if case.expected_failure_reason is not None and case.expected_failure_reason not in FAILURE_REASONS:
        raise SearchEvaluationError(
            f"case {case.id} in {path} has unknown expected_failure_reason"
        )
    if case.expected_behavior == BEHAVIOR_CONSTRAINT_AND_RESULTS and not (
        case.expected_field and case.expected_canonical and case.expected_operator
    ):
        raise SearchEvaluationError(
            f"case {case.id} in {path} expects a constraint but does not say which one"
        )
    if case.expected_behavior == BEHAVIOR_ABSTAIN and not case.expect_blocked:
        raise SearchEvaluationError(
            f"case {case.id} in {path} expects an abstain but does not expect execution to block"
        )
    if case.expect_blocked and case.expected_behavior == BEHAVIOR_CONSTRAINT_AND_RESULTS:
        raise SearchEvaluationError(
            f"case {case.id} in {path} expects results from a blocked plan"
        )


def evaluate_search_cases(
    cases: Sequence[SearchCase],
    *,
    db_path: Path,
    taxonomy=None,
    limit: int = 20,
    filters: Optional[SearchFilters] = None,
) -> EvaluationReport:
    """Run every case through the real planner and the real retrieval path, and classify the result.

    ``db_path`` must be a copy when the index being measured is a production one: this reads through
    the ordinary index reader, which does not open read-only.
    """
    db_path = Path(db_path)
    catalog = QueryCatalog.from_metadata(
        item.chunk.metadata for item in SQLiteIndex(db_path).load_chunks()
    )
    index_matches = _index_match_counts(db_path)

    outcomes = []
    for case in cases:
        plan = build_index_query_plan(case.query, db_path, filters, taxonomy=taxonomy)
        results = search_index(
            case.query,
            db_path=db_path,
            filters=filters,
            limit=limit,
            mode="hybrid",
            query_plan=plan,
            taxonomy=taxonomy,
        )
        observation = _observe(case, plan, results, catalog, index_matches)
        failure_reason, failure_detail = _judge(case, observation)
        outcomes.append(
            CaseOutcome(
                case=case,
                status="FAIL" if failure_reason else "PASS",
                observation=observation,
                failure_reason=failure_reason,
                failure_detail=failure_detail,
            )
        )

    return EvaluationReport(
        outcomes=tuple(outcomes),
        index_path=str(db_path),
        taxonomy_workbook_path=getattr(taxonomy, "workbook_path", None),
        taxonomy_workbook_sha256=getattr(taxonomy, "workbook_sha256", None),
    )


def _observe(
    case: SearchCase,
    plan: TypedQueryPlan,
    results: Sequence[SearchResult],
    catalog: QueryCatalog,
    index_matches: Dict[Tuple[str, str], int],
) -> CaseObservation:
    constraints = tuple(item.to_dict() for item in plan.validated_constraints)
    taxonomy_constraints = tuple(
        item for item in constraints if item.get("source") == TAXONOMY_SOURCE
    )
    offending = tuple(
        _result_identity(result)
        for result in results
        if case.expected_field
        and case.expected_canonical
        and not _result_matches_expectation(result, case)
    )
    return CaseObservation(
        raw_query=plan.raw_query,
        normalized_query=plan.normalized_query,
        query_mode=plan.query_mode,
        taxonomy_constraints=taxonomy_constraints,
        all_constraints=constraints,
        resolved_entities=tuple(
            {"entity_type": entity.entity_type, "canonical_name": entity.canonical_name}
            for entity in plan.resolved_entities
        ),
        ambiguity_flags=tuple(plan.ambiguity_flags),
        parser_warnings=tuple(plan.parser_warnings),
        abstain_reason=plan.effective_abstain_reason,
        execution_blocked=plan.execution_blocked,
        # A result set produced with no hard filter at all is the system having decided to answer
        # from similarity. For a case that forbids that, it is the failure itself.
        semantic_fallback=bool(results) and not plan.hard_constraints,
        result_count=len(results),
        result_document_ids=tuple(result.chunk.document_id for result in results),
        result_brands=tuple(
            _unique_preserving(result.chunk.metadata.brand_name or "" for result in results)
        ),
        result_titles=tuple(
            _unique_preserving(result.chunk.metadata.title or "" for result in results)
        ),
        offending_results=offending,
        index_match_count=(
            index_matches.get(
                (case.expected_field, normalize_exact_value(case.expected_canonical)), 0
            )
            if case.expected_field and case.expected_canonical
            else 0
        ),
    )


def _judge(case: SearchCase, observation: CaseObservation) -> Tuple[Optional[str], Optional[str]]:
    """Decide PASS/FAIL for one case. Returns ``(failure_reason, detail)`` or ``(None, None)``."""
    if case.forbid_semantic_fallback and observation.semantic_fallback:
        return (
            FAILURE_UNEXPECTED_SEMANTIC_FALLBACK,
            f"{observation.result_count} results returned with no hard constraint",
        )
    if case.forbid_taxonomy_constraint and observation.taxonomy_constraints:
        bound = ", ".join(
            f"{item['field']}={item['value']!r}" for item in observation.taxonomy_constraints
        )
        return (
            FAILURE_WRONG_CONSTRAINT,
            f"a taxonomy constraint was bound from unrelated text: {bound}",
        )
    if case.forbid_extra_taxonomy_fields:
        # Checked over every constraint, not only the ones the Authority sourced: the point is that
        # no stage may widen an explicitly scoped query into a second taxonomy field.
        extra = sorted(
            {
                str(item["field"])
                for item in observation.all_constraints
                if item.get("field") in TAXONOMY_FIELDS and item.get("field") != case.expected_field
            }
        )
        if extra:
            return (
                FAILURE_WRONG_CONSTRAINT,
                f"query scoped to {case.expected_field} also constrained {extra}",
            )

    if case.expect_blocked and not observation.execution_blocked:
        return (
            FAILURE_UNEXPECTED_RESULT,
            f"execution was not blocked; abstain_reason={observation.abstain_reason!r}",
        )
    if case.expect_blocked and observation.result_count:
        # Asserted separately from the plan, and separately from ``forbid_semantic_fallback``:
        # that flag only fires when there is no hard constraint at all, so a blocked plan that
        # returned results *with* a constraint would otherwise pass silently.
        return (
            FAILURE_BLOCKED_QUERY_RETURNED_RESULTS,
            f"plan abstained with {observation.abstain_reason!r} but retrieval returned "
            f"{observation.result_count} results: "
            + ", ".join(observation.result_document_ids[:5]),
        )
    if not case.expect_blocked and observation.execution_blocked:
        # Classified by *why* it blocked. A refusal because nothing in the index carries the value
        # belongs to whoever owns the next re-index, not to whoever owns the resolver, and reading
        # every unexpected block as "ambiguity" would hide that split.
        return (
            _BLOCK_REASON_CLASSES.get(observation.abstain_reason, FAILURE_UNEXPECTED_AMBIGUITY),
            f"execution blocked unexpectedly: {observation.abstain_reason!r}",
        )
    if (
        case.expected_abstain_reason
        and observation.abstain_reason != case.expected_abstain_reason
    ):
        return (
            FAILURE_TAXONOMY_RESOLUTION,
            f"expected abstain_reason {case.expected_abstain_reason!r}, "
            f"observed {observation.abstain_reason!r}",
        )

    if case.expected_behavior == BEHAVIOR_CONSTRAINT_AND_RESULTS:
        return _judge_constraint_case(case, observation)
    if case.expected_behavior == BEHAVIOR_IDENTITY:
        return _judge_identity_case(case, observation)
    if case.expected_behavior == BEHAVIOR_SUGGESTION_ONLY:
        return _judge_suggestion_case(observation)
    # abstain and semantic_baseline are fully covered by the shared checks above; a semantic
    # baseline records ranking without asserting subjective relevance in v1.
    return None, None


def _judge_constraint_case(
    case: SearchCase, observation: CaseObservation
) -> Tuple[Optional[str], Optional[str]]:
    matching = [
        item
        for item in observation.all_constraints
        if item.get("field") == case.expected_field
        and normalize_exact_value(item.get("value")) == normalize_exact_value(case.expected_canonical)
    ]
    if not matching:
        bound = ", ".join(
            f"{item['field']}={item['value']!r}" for item in observation.all_constraints
        ) or "none"
        reason = (
            FAILURE_RUNTIME_CATALOG_GAP
            if observation.index_match_count == 0
            else FAILURE_TAXONOMY_RESOLUTION
        )
        return reason, f"expected {case.expected_field}={case.expected_canonical!r}; bound {bound}"
    if case.expected_operator and matching[0].get("operator") != case.expected_operator:
        return (
            FAILURE_WRONG_CONSTRAINT,
            f"expected operator {case.expected_operator!r}, "
            f"observed {matching[0].get('operator')!r}",
        )
    if matching[0].get("support_status") != "supported":
        return (
            FAILURE_WRONG_CONSTRAINT,
            f"constraint support_status={matching[0].get('support_status')!r}",
        )
    if not matching[0].get("hard_filter"):
        return FAILURE_WRONG_CONSTRAINT, "expected a hard filter, observed a soft one"
    # Section 12: a top hit that is right does not excuse unrelated records behind it, and an
    # empty answer is a failure only when the index really does hold matching records.
    if observation.offending_results:
        return (
            FAILURE_UNEXPECTED_RESULT,
            f"{len(observation.offending_results)} returned records do not match the constraint: "
            + ", ".join(observation.offending_results[:5]),
        )
    if observation.result_count == 0 and observation.index_match_count > 0:
        return (
            FAILURE_UNEXPECTED_RESULT,
            f"index holds {observation.index_match_count} matching records but none were returned",
        )
    if observation.result_count == 0:
        return FAILURE_RUNTIME_CATALOG_GAP, "index holds no record carrying this value"
    return None, None


def _judge_identity_case(
    case: SearchCase, observation: CaseObservation
) -> Tuple[Optional[str], Optional[str]]:
    identity_fields = {"entity_name", "merchant_name", "merchant_handle"}
    bound = [
        item for item in observation.all_constraints if item.get("field") in identity_fields
    ]
    if not bound:
        return (
            FAILURE_MERCHANT_PRECEDENCE,
            "no merchant identity constraint was bound",
        )
    if observation.taxonomy_constraints:
        rebound = ", ".join(
            f"{item['field']}={item['value']!r}" for item in observation.taxonomy_constraints
        )
        return (
            FAILURE_MERCHANT_PRECEDENCE,
            f"taxonomy also claimed part of an identity query: {rebound}",
        )
    if observation.result_count == 0:
        return FAILURE_UNEXPECTED_RESULT, "identity query returned nothing"
    return None, None


def _judge_suggestion_case(
    observation: CaseObservation,
) -> Tuple[Optional[str], Optional[str]]:
    if observation.taxonomy_constraints:
        bound = ", ".join(
            f"{item['field']}={item['value']!r}" for item in observation.taxonomy_constraints
        )
        return FAILURE_WRONG_CONSTRAINT, f"a typo was auto-corrected into a constraint: {bound}"
    if not any(
        flag.startswith("taxonomy_typo_suggestion:") for flag in observation.ambiguity_flags
    ):
        return FAILURE_TAXONOMY_RESOLUTION, "no suggestion was offered"
    return None, None


def _result_matches_expectation(result: SearchResult, case: SearchCase) -> bool:
    metadata = result.chunk.metadata
    expected = normalize_exact_value(case.expected_canonical)
    if case.expected_field == "content_tags":
        return expected in {normalize_exact_value(tag) for tag in metadata.content_tags}
    return normalize_exact_value(getattr(metadata, case.expected_field or "", None)) == expected


def _index_match_counts(db_path: Path) -> Dict[Tuple[str, str], int]:
    """How many distinct source records carry each indexed taxonomy value.

    This is what separates "the search is broken" from "nothing in the index has that value".
    """
    counts: Dict[Tuple[str, str], set] = {}
    for item in SQLiteIndex(db_path).load_chunks():
        metadata = item.chunk.metadata
        identity = f"{metadata.source_sheet}:r{metadata.source_row}"
        for field_name in ("sales_category_lv1", "sales_category_lv2"):
            value = getattr(metadata, field_name)
            if value:
                counts.setdefault((field_name, normalize_exact_value(value)), set()).add(identity)
        for tag in metadata.content_tags:
            counts.setdefault(("content_tags", normalize_exact_value(tag)), set()).add(identity)
    return {key: len(value) for key, value in counts.items()}


def _result_identity(result: SearchResult) -> str:
    metadata = result.chunk.metadata
    return f"{metadata.brand_name or result.chunk.document_id} ({result.chunk.document_id})"


def _unique_preserving(values: Iterable[str]) -> List[str]:
    seen = set()
    ordered = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def render_evaluation_markdown(report: EvaluationReport) -> str:
    """A reviewer-readable summary. Failure classes lead; the pass rate is a footnote."""
    summary = report.summary
    lines = [
        "# Search Quality Evaluation",
        "",
        f"- Index: `{report.index_path}`",
        f"- Taxonomy workbook: `{report.taxonomy_workbook_path or 'not pinned'}`",
        f"- Taxonomy sha256: `{report.taxonomy_workbook_sha256 or 'n/a'}`",
        "",
        "## Summary",
        "",
        f"- Golden: {summary['golden_pass']}/{summary['golden_cases']} pass",
        f"- Negative: {summary['negative_pass']}/{summary['negative_cases']} pass",
        f"- Total: {summary['total']} cases, pass rate {summary['pass_rate']}",
        f"- Unexpected failures: {summary['unexpected_failures']} "
        f"{summary['unexpected_failure_ids'] or ''}".rstrip(),
        f"- Known expected failures: {summary['known_expected_failure_ids'] or 'none'}",
        "",
        "## Failure classes",
        "",
    ]
    if summary["failure_reasons"]:
        lines.extend(
            f"- `{reason}`: {count}" for reason, count in summary["failure_reasons"].items()
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Failing cases", ""])
    failures = [outcome for outcome in report.outcomes if outcome.status == "FAIL"]
    if not failures:
        lines.append("None.")
    for outcome in failures:
        lines.extend(
            [
                f"### {outcome.case.id} — `{outcome.failure_reason}`",
                "",
                f"- Query: `{outcome.case.query}`",
                f"- Case type: {outcome.case.case_type} ({outcome.case.case_class})",
                f"- Detail: {outcome.failure_detail}",
                f"- Observed constraints: {outcome.observation.all_constraints or 'none'}",
                f"- Abstain reason: {outcome.observation.abstain_reason}",
                f"- Results: {outcome.observation.result_count}",
                f"- Notes: {outcome.case.notes}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"
