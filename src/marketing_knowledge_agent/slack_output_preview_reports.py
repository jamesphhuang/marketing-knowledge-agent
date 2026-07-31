from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Mapping, Sequence


OUTPUT_FILENAMES = (
    "slack_output_preview_summary.md",
    "slack_output_variant_a_concise.md",
    "slack_output_variant_b_standard.md",
    "slack_output_variant_c_detailed.md",
    "slack_output_comparison.md",
    "slack_output_contract.md",
    "slack_output_payload_preview.json",
    "slack_output_preview_errors.csv",
    "slack_output_preview_warnings.csv",
    "recommended_production_format.md",
)
ISSUE_COLUMNS = ("code", "query", "field", "message")


def write_slack_output_preview_reports(
    output_dir: Path,
    summary: Mapping[str, object],
    payloads: Sequence[dict],
    errors: Sequence[dict],
    warnings: Sequence[dict],
) -> None:
    from .slack_output_preview import render_slack_preview

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = {
        variant: [render_slack_preview(payload, variant) for payload in payloads]
        for variant in ("concise", "standard", "detailed")
    }
    (output_dir / "slack_output_preview_summary.md").write_text(
        _summary(summary, payloads), encoding="utf-8"
    )
    for variant, filename, title in (
        ("concise", "slack_output_variant_a_concise.md", "A. Concise"),
        ("standard", "slack_output_variant_b_standard.md", "B. Standard"),
        ("detailed", "slack_output_variant_c_detailed.md", "C. Detailed"),
    ):
        (output_dir / filename).write_text(
            _variant_document(title, payloads, rendered[variant]), encoding="utf-8"
        )
    (output_dir / "slack_output_comparison.md").write_text(
        _comparison(), encoding="utf-8"
    )
    (output_dir / "slack_output_contract.md").write_text(
        _contract(), encoding="utf-8"
    )
    (output_dir / "slack_output_payload_preview.json").write_text(
        json.dumps(
            {
                "preview_only": True,
                "slack_api_called": False,
                "asset_urls_applied": False,
                "payloads": list(payloads),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_csv(output_dir / "slack_output_preview_errors.csv", errors)
    _write_csv(output_dir / "slack_output_preview_warnings.csv", warnings)
    (output_dir / "recommended_production_format.md").write_text(
        _recommendation(), encoding="utf-8"
    )


def _summary(summary: Mapping[str, object], payloads: Sequence[dict]) -> str:
    lines = [
        "# Slack Output Renderer Preview",
        "",
        "> Offline preview only. No Slack API call, token read, URL Apply, Vault write or formal index rebuild occurred.",
        "",
        f"- Conclusion: **{summary['conclusion']}**",
        f"- Queries: {summary['query_count']}",
        f"- Payloads: {summary['payload_count']}",
        f"- Approved overlay fields: {summary['approved_overlay_field_count']}",
        f"- Approved overlay assets: {summary['approved_overlay_asset_count']}",
        f"- Governance-blocked assets excluded: {summary['governance_blocked_asset_count']}",
        f"- Assets across query payloads: {summary['result_asset_count']}",
        f"- Backend citation mappings: {summary['backend_citation_count']}",
        f"- Abstained queries: {summary['abstained_query_count']}",
        f"- Errors: {summary['error_count']}",
        f"- Warnings: {summary['warning_count']}",
        f"- Source files unchanged: {_yes_no(not summary['source_files_modified'])}",
        f"- Three variants share one payload per query: {_yes_no(summary['variant_result_sets_identical'])}",
        "",
        "## Query Results",
        "",
        "| Query | Entities | Assets | Result |",
        "| --- | ---: | ---: | --- |",
    ]
    for payload in payloads:
        result = payload.get("abstain_kind") or "matched"
        lines.append(
            f"| {_cell(payload.get('query'))} | {payload.get('total_entities', 0)} | "
            f"{payload.get('total_assets', 0)} | `{result}` |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- User-facing payload omits asset/record IDs, canonical URL, provenance, confidence, internal paths and retrieval scores.",
            "- Approved `asset_url` is an in-memory display overlay; approved `canonical_url` remains backend-only.",
            "- Unsupported and zero-intersection queries remain fail closed with zero citations.",
            "- Governance-blocked assets never enter the user-facing payload.",
        ]
    )
    return "\n".join(lines) + "\n"


def _variant_document(title: str, payloads: Sequence[dict], outputs: Sequence[str]) -> str:
    lines = [f"# {title} Slack Preview", "", "> Same retrieval payload as the other variants; formatting only."]
    for index, (payload, output) in enumerate(zip(payloads, outputs), 1):
        lines.extend(
            [
                "",
                f"## {index}. {_safe_text(payload.get('query'))}",
                "",
                "```text",
                output,
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _comparison() -> str:
    return """# Slack Output Variant Comparison

All variants consume the same structured retrieval payload, URL overlay, governance result, citations and constraints.

| Dimension | A. Concise | B. Standard | C. Detailed |
| --- | --- | --- | --- |
| Primary use | quick lookup | general production candidate | internal source confirmation |
| Per asset | type, linked title, quote status | type, title, open link, quote status | brand/category/handle plus asset details |
| Source display | deduplicated footer | deduplicated footer | deduplicated per brand group |
| Technical fields | never | never | never |
| Canonical URL | hidden | hidden | hidden |
| Result limits | 5 brands / 10 assets | 5 brands / 10 assets | 5 brands / 10 assets |

Variant B is recommended because it preserves scannability while making title, link and external-usage status explicit.
"""


def _contract() -> str:
    return """# Slack Output Preview Contract

## Input

1. Existing external-intent `StructuredRetrievalResult` from `ask_index`.
2. Validated Asset Apply Preview rows joined by `(record_id, asset_id, field)`.
3. Existing governance-filtered citations.

## In-memory Overlay

- Only `ready_for_apply_preview + approve + eligible` rows for `asset_url` and `canonical_url` are accepted.
- `asset_url` is user-facing; `canonical_url` remains backend citation metadata and is omitted from rendered text and JSON payload.
- Missing identity or URL creates a preview warning and never triggers inference.
- Governance-blocked IDs and assets without an external-safe citation are omitted.

## User-safe Payload

The payload contains query text, simplified applied conditions, entities, category, handle, asset type/title/display URL, external-usage label, simplified source and citation label. It excludes asset/record IDs, source paths, canonical URL, provenance, confidence, internal paths, query plans and scores.

## Boundaries

This module does not import the production Slack interface, read environment tokens, call Slack, mutate the structured result, Apply URL decisions, enable query constraints, write Vault, or open SQLite for writing.
"""


def _recommendation() -> str:
    return """# Recommended Production Format

## Recommendation

Adopt **B. Standard** after human format approval and the separate Asset Metadata Apply Sprint. It gives each asset a clear title, link and external-usage status without exposing internal fields.

## Display Limits

- One Slack reply: at most 10 displayed assets.
- Brand groups: at most 5 in the first reply.
- Long titles: preserve the first context and final 24 characters; soft limit 160 characters.
- Always show actual total entities/assets and current displayed counts.
- Over limit: ask the user to narrow by brand or asset type; never change constraints or pad results.
- Thread continuation: recommended only as a later explicit feature for the remaining results. Do not add buttons or pagination in this sprint.

## Missing Data

- Missing asset URL: show the title with `連結未提供`; do not fall back to record-level canonical URL.
- Do not show publication date/status, interview date/status, review status or partner name until authoritative asset-level data exists and query support is separately activated.
"""


def _write_csv(path: Path, rows: Sequence[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ISSUE_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _safe_csv(row.get(column)) for column in ISSUE_COLUMNS})


def _safe_csv(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "[unsafe input redacted]"
    return text


def _cell(value: object) -> str:
    return _safe_text(value).replace("|", "\\|").replace("\n", " ")


def _safe_text(value: object) -> str:
    return "" if value is None else str(value)


def _yes_no(value: object) -> str:
    return "yes" if value else "no"
