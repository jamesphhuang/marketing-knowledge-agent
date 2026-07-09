from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .models import SearchFilters
from .pipeline import ask_index, ingest_vault, search_index


DEFAULT_EVALUATION_CASES = [
    {
        "name": "metadata_filter_showcase",
        "query": "pricing case studies product-a manufacturing",
        "filters": SearchFilters(source_type=["showcase"], product=["product-a"]),
        "expected_source_type": "showcase",
        "expect_warning": False,
    },
    {
        "name": "status_warning_archived",
        "query": "launch social post",
        "filters": SearchFilters(status=["archived"]),
        "expected_status": "archived",
        "expect_warning": True,
    },
    {
        "name": "youtube_transcript",
        "query": "product-c demo transcript",
        "filters": SearchFilters(source_type=["youtube"]),
        "expected_source_type": "youtube",
        "expect_warning": True,
    },
]


def evaluate(vault_path: Path, db_path: Path) -> Dict[str, object]:
    ingest_summary = ingest_vault(vault_path=vault_path, db_path=db_path)
    case_results: List[Dict[str, object]] = []

    for case in DEFAULT_EVALUATION_CASES:
        filters = case["filters"]
        search_results = search_index(case["query"], db_path=db_path, filters=filters, limit=3)
        answer = ask_index(case["query"], db_path=db_path, filters=filters, limit=3)

        has_citation = bool(answer.citations)
        filter_ok = _filter_ok(case, search_results)
        warning_ok = bool(answer.warnings) == bool(case["expect_warning"])
        case_results.append(
            {
                "name": case["name"],
                "has_citation": has_citation,
                "filter_ok": filter_ok,
                "warning_ok": warning_ok,
                "top_source": search_results[0].chunk.source_path if search_results else None,
            }
        )

    total = len(case_results)
    return {
        "ingest": ingest_summary,
        "metrics": {
            "citation_coverage": _ratio(case_results, "has_citation", total),
            "filter_correctness": _ratio(case_results, "filter_ok", total),
            "warning_coverage": _ratio(case_results, "warning_ok", total),
        },
        "cases": case_results,
    }


def _filter_ok(case: dict, search_results: list) -> bool:
    if not search_results:
        return False
    for result in search_results:
        metadata = result.chunk.metadata
        if "expected_source_type" in case and metadata.source_type != case["expected_source_type"]:
            return False
        if "expected_status" in case and metadata.status != case["expected_status"]:
            return False
    return True


def _ratio(case_results: list, key: str, total: int) -> float:
    if total == 0:
        return 0.0
    return round(sum(1 for result in case_results if result[key]) / total, 4)
