from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .generation import generate_answer
from .llm import LLMConfig, LLMProvider, build_provider, validate_provider_policy
from .models import GeneratedAnswer, SearchFilters, SearchResult


PROMPT_VERSION = "mka-grounded-v1"
PROMPT_TEMPLATE = """Marketing Knowledge Agent grounded answer task ({version})

只根據下列來源回答。使用 [n] 標註依據；資訊不足時明確說明不足，不得補充來源外的事實。

Question:
{question}

Sources:
{sources}
"""
INTERNAL_DATA_REMOVAL_WARNING = "{count} 筆內部資料未送外部模型"
UNKNOWN_CITATION_WARNING = "LLM 產生不存在的引用標籤，已替換為無對應來源。"


def generate_answer_with_llm(
    question: str,
    results: Iterable[SearchResult],
    filters: SearchFilters,
    provider_name: str,
    config: LLMConfig,
    provider: Optional[LLMProvider] = None,
    dry_run: bool = False,
    citation_limit: int = 3,
    internal_result_count: int = 0,
    audit_log_path: Path = Path("reports/audit_log.csv"),
    command: str = "ask",
) -> GeneratedAnswer:
    results = list(results)
    if provider_name == "mock" and not dry_run:
        return generate_answer(
            question,
            results,
            citation_limit=citation_limit,
            filters=filters,
            internal_result_count=internal_result_count,
        )
    if not results:
        return generate_answer(
            question,
            [],
            filters=filters,
            internal_result_count=internal_result_count,
        )

    eligible_results, internal_removed_count = _filter_payload_results(results, config)
    removal_warning = (
        INTERNAL_DATA_REMOVAL_WARNING.format(count=internal_removed_count)
        if internal_removed_count
        else None
    )
    if not eligible_results:
        answer = generate_answer(question, [], filters=filters, internal_result_count=internal_result_count)
        _append_warning(answer, removal_warning)
        return answer

    payload_results = eligible_results[:citation_limit]
    prompt = build_llm_prompt(question, payload_results)
    if dry_run:
        answer = GeneratedAnswer(
            question=question,
            answer=json.dumps(
                {
                    "prompt_version": PROMPT_VERSION,
                    "provider": provider_name,
                    "model": config.model,
                    "payload_chunk_count": len(payload_results),
                    "internal_removed_count": internal_removed_count,
                    "prompt": prompt,
                },
                ensure_ascii=False,
                indent=2,
            ),
            citations=[],
            warnings=[],
        )
        _append_warning(answer, removal_warning)
        return answer

    validate_provider_policy(config, provider_name)
    baseline = generate_answer(
        question,
        payload_results,
        citation_limit=citation_limit,
        filters=filters,
        internal_result_count=internal_result_count,
    )
    _append_warning(baseline, removal_warning)
    if not baseline.citations:
        return baseline

    resolved_provider = provider or build_provider(provider_name, config)
    generated_text = resolved_provider.generate(prompt)
    generated_text, unknown_label_count = _replace_unknown_citation_labels(
        generated_text,
        valid_count=len(baseline.citations),
    )
    if unknown_label_count:
        _append_warning(baseline, UNKNOWN_CITATION_WARNING)
    baseline.answer = _compose_local_answer(generated_text, baseline.warnings)
    append_llm_audit(
        audit_log_path,
        command=command,
        provider=provider_name,
        model=config.model or "",
        payload_chunk_count=len(payload_results),
        internal_removed_count=internal_removed_count,
    )
    return baseline


def build_llm_prompt(question: str, results: Iterable[SearchResult]) -> str:
    sources = []
    for index, result in enumerate(results, start=1):
        sources.extend(
            [
                f"[{index}]",
                f"title: {result.chunk.metadata.title}",
                f"text: {result.chunk.text}",
                "",
            ]
        )
    return PROMPT_TEMPLATE.format(
        version=PROMPT_VERSION,
        question=question,
        sources="\n".join(sources).rstrip(),
    )


def append_llm_audit(
    path: Path,
    command: str,
    provider: str,
    model: str,
    payload_chunk_count: int,
    internal_removed_count: int,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    header = []
    if exists:
        with path.open("r", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        llm_header = [
            "timestamp",
            "command",
            "provider",
            "model",
            "payload_chunk_count",
            "internal_removed_count",
        ]
        if not exists:
            header = llm_header
            writer.writerow(header)
        if header == llm_header:
            writer.writerow(
                [_utc_now(), command, provider, model, payload_chunk_count, internal_removed_count]
            )
            return
        if header == ["timestamp", "batch_id", "action", "add", "update", "archive", "operator", "plan_path"]:
            writer.writerow(
                [
                    _utc_now(),
                    "",
                    "llm_call",
                    payload_chunk_count,
                    internal_removed_count,
                    0,
                    provider,
                    f"{command}:{model}",
                ]
            )
            return
        if header == ["timestamp", "command", "event", "match_count"]:
            writer.writerow(
                [
                    _utc_now(),
                    command,
                    f"llm_call:{provider}:{model}",
                    f"payload={payload_chunk_count};internal_removed={internal_removed_count}",
                ]
            )
            return
        if header == ["timestamp", "command", "index_count", "db_path"]:
            writer.writerow(
                [
                    _utc_now(),
                    command,
                    f"llm_call:{provider}:{model}",
                    f"payload={payload_chunk_count};internal_removed={internal_removed_count}",
                ]
            )
            return
        raise ValueError(f"unsupported audit log header: {header}")


def _filter_payload_results(results, config: LLMConfig):
    if config.allow_internal_data_to_llm:
        return list(results), 0
    kept = [result for result in results if result.chunk.metadata.data_classification == "public"]
    return kept, len(results) - len(kept)


def _replace_unknown_citation_labels(text: str, valid_count: int):
    unknown_count = 0

    def replace(match):
        nonlocal unknown_count
        if 1 <= int(match.group(1)) <= valid_count:
            return match.group(0)
        unknown_count += 1
        return "(無對應來源)"

    return re.sub(r"\[(\d+)\]", replace, text), unknown_count


def _compose_local_answer(generated_text: str, warnings) -> str:
    lines = [generated_text.strip()]
    if warnings:
        lines.extend(["", "引用限制提醒："])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(["", "請在正式對外使用前，由內容 owner 複核引用與資料新鮮度。"])
    return "\n".join(lines)


def _append_warning(answer: GeneratedAnswer, warning: Optional[str]) -> None:
    if warning and warning not in answer.warnings:
        answer.warnings.append(warning)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
