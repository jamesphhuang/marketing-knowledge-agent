from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backfill import generate_backfill_report
from .evaluation import evaluate
from .excel_preview import ExcelPreviewError, generate_excel_preview
from .ingestion import IngestionError
from .models import SearchFilters
from .pipeline import DEFAULT_RESTRICTED_CUSTOMERS_PATH, agent_ask, ask_index, ingest_vault, search_index
from .retrieval import result_to_dict
from .review_template import ReviewTemplateError, generate_review_template
from .review_decision_validation import ReviewDecisionValidationError, validate_review_decisions
from .validation import validate_vault


DEFAULT_VAULT = Path("data/mock_vault")
DEFAULT_DB = Path(".mka/index.sqlite")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "ingest":
            summary = ingest_vault(vault_path=args.vault, db_path=args.db)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "search":
            filters = _filters_from_args(args)
            results = search_index(
                args.query,
                db_path=args.db,
                filters=filters,
                limit=args.limit,
                mode=args.mode,
            )
            payload = [result_to_dict(result) for result in results]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "ask":
            filters = _filters_from_args(args)
            answer = ask_index(
                args.question,
                db_path=args.db,
                filters=filters,
                limit=args.limit,
                mode=args.mode,
                restricted_customers_path=args.restricted_customers,
            )
            _print_answer(answer)
            return 0

        if args.command == "agent-ask":
            filters = _filters_from_args(args)
            answer = agent_ask(
                args.question,
                db_path=args.db,
                filters=filters,
                limit=args.limit,
                mode=args.mode,
                restricted_customers_path=args.restricted_customers,
            )
            _print_answer(answer)
            if args.show_trace:
                print("\nAgent Trace:")
                print(json.dumps(answer.trace.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate":
            report = validate_vault(vault_path=args.vault)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["summary"]["invalid"] == 0 else 1

        if args.command == "backfill-report":
            report = generate_backfill_report(vault_path=args.vault, output_path=args.output)
            payload = {
                "vault_path": report["vault_path"],
                "output_path": report["output_path"],
                "candidate_count": report["candidate_count"],
                "validation_summary": report["validation_summary"],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "evaluate":
            report = evaluate(vault_path=args.vault, db_path=args.db)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        if args.command == "excel-preview":
            summary = generate_excel_preview(
                workbook_path=args.workbook,
                output_dir=args.output,
                captured_date=args.captured_date,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "review-template":
            summary = generate_review_template(
                preview_dir=args.preview_dir,
                output_path=args.output,
                summary_output_path=args.summary_output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate-review-decisions":
            summary = validate_review_decisions(
                decisions_path=args.decisions,
                output_path=args.output,
                preview_dir=args.preview_dir,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["error_count"] == 0 else 1

    except IngestionError as exc:
        print(f"ingestion error: {exc}", file=sys.stderr)
        return 2
    except ExcelPreviewError as exc:
        print(f"excel preview error: {exc}", file=sys.stderr)
        return 2
    except ReviewTemplateError as exc:
        print(f"review template error: {exc}", file=sys.stderr)
        return 2
    except ReviewDecisionValidationError as exc:
        print(f"review decision validation error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"file error: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mka", description="Marketing Knowledge Agent prototype")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Build a SQLite index from a Markdown vault")
    ingest_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ingest_parser.add_argument("--db", type=Path, default=DEFAULT_DB)

    validate_parser = subparsers.add_parser("validate", help="Validate Markdown vault metadata")
    validate_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)

    backfill_parser = subparsers.add_parser(
        "backfill-report",
        help="Generate review-only metadata backfill candidates",
    )
    backfill_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    backfill_parser.add_argument("--output", type=Path, default=Path("reports/metadata_backfill_candidates.md"))

    search_parser = subparsers.add_parser("search", help="Search indexed marketing knowledge")
    search_parser.add_argument("query")
    _add_retrieval_args(search_parser)

    ask_parser = subparsers.add_parser("ask", help="Generate a mock RAG answer with citations")
    ask_parser.add_argument("question")
    _add_retrieval_args(ask_parser)
    _add_governance_args(ask_parser)

    agent_ask_parser = subparsers.add_parser(
        "agent-ask",
        help="Run an offline agentic-lite RAG answer with citations and optional trace",
    )
    agent_ask_parser.add_argument("question")
    _add_retrieval_args(agent_ask_parser)
    _add_governance_args(agent_ask_parser)
    agent_ask_parser.add_argument("--show-trace", action="store_true")

    evaluate_parser = subparsers.add_parser("evaluate", help="Run built-in prototype evaluation cases")
    evaluate_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    evaluate_parser.add_argument("--db", type=Path, default=Path(".mka/eval.sqlite"))

    excel_preview_parser = subparsers.add_parser(
        "excel-preview",
        help="Create JSON and Markdown review previews from the marketing Excel workbook",
    )
    excel_preview_parser.add_argument("--workbook", type=Path, required=True)
    excel_preview_parser.add_argument("--output", type=Path, default=Path("reports/excel_preview"))
    excel_preview_parser.add_argument("--captured-date", type=_date_arg, default=None)

    review_template_parser = subparsers.add_parser(
        "review-template",
        help="Create a human review decisions CSV from Excel preview JSON files",
    )
    review_template_parser.add_argument("--preview-dir", type=Path, default=Path("reports/excel_preview"))
    review_template_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    review_template_parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/excel_preview/review_summary.md"),
    )

    validate_decisions_parser = subparsers.add_parser(
        "validate-review-decisions",
        help="Validate a human-filled review decisions CSV without applying decisions",
    )
    validate_decisions_parser.add_argument("--decisions", type=Path, required=True)
    validate_decisions_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_validation.md"),
    )
    validate_decisions_parser.add_argument("--preview-dir", type=Path, default=Path("reports/excel_preview"))

    return parser


def _add_retrieval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--mode", choices=["keyword", "vector", "hybrid"], default="hybrid")
    parser.add_argument("--record-type", action="append", default=[])
    parser.add_argument("--source-type", action="append", default=[])
    parser.add_argument("--content-category", action="append", default=[])
    parser.add_argument("--parent-source-type", action="append", default=[])
    parser.add_argument("--brand-name", action="append", default=[])
    parser.add_argument("--merchant-handle", action="append", default=[])
    parser.add_argument("--merchant-status", action="append", default=[])
    parser.add_argument("--product", action="append", default=[])
    parser.add_argument("--industry", action="append", default=[])
    parser.add_argument("--sales-category-lv1", action="append", default=[])
    parser.add_argument("--sales-category-lv2", action="append", default=[])
    parser.add_argument("--content-tag", action="append", default=[])
    parser.add_argument("--topic", action="append", default=[])
    parser.add_argument("--metric-type", action="append", default=[])
    parser.add_argument("--metric-name", action="append", default=[])
    parser.add_argument("--claim-status", action="append", default=[])
    parser.add_argument("--data-classification", action="append", default=[])
    parser.add_argument("--exposure-channel", action="append", default=[])
    parser.add_argument("--funnel-stage", action="append", default=[])
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--can-quote-externally", action=argparse.BooleanOptionalAction, default=None)


def _add_governance_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
        help="Path to restricted customer denylist preview JSON",
    )


def _filters_from_args(args: argparse.Namespace) -> SearchFilters:
    return SearchFilters(
        record_type=args.record_type,
        source_type=args.source_type,
        content_category=args.content_category,
        parent_source_type=args.parent_source_type,
        brand_name=args.brand_name,
        merchant_handle=args.merchant_handle,
        merchant_status=args.merchant_status,
        product=args.product,
        industry=args.industry,
        sales_category_lv1=args.sales_category_lv1,
        sales_category_lv2=args.sales_category_lv2,
        content_tags=args.content_tag,
        topic=args.topic,
        metric_type=args.metric_type,
        metric_name=args.metric_name,
        claim_status=args.claim_status,
        data_classification=args.data_classification,
        exposure_channel=args.exposure_channel,
        funnel_stage=args.funnel_stage,
        status=args.status,
        can_quote_externally=args.can_quote_externally,
    )


def _print_answer(answer) -> None:
    print(answer.answer)
    if not getattr(answer, "governance_checked", False):
        print("\n!!! WARNING: restricted denylist 未完成檢查，請勿直接對外使用。")
    print("\nCitations:")
    for citation in answer.citations:
        print(
            f"- {citation.label} {citation.title} "
            f"({citation.source_type}, record_type={citation.record_type}, "
            f"classification={citation.data_classification}, "
            f"can_quote={citation.can_quote_externally}, status={citation.status}) "
            f"{citation.source_path}; sheet={citation.source_sheet}, row={citation.source_row}; "
            f"{citation.freshness_note}"
        )
    if answer.warnings:
        print("\nWarnings:")
        for warning in answer.warnings:
            print(f"- {warning}")


def _date_arg(value: str):
    from datetime import date

    return date.fromisoformat(value)


if __name__ == "__main__":
    raise SystemExit(main())
