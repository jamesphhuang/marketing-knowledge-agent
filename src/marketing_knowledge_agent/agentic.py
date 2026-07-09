from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .generation import generate_answer
from .governance import (
    RESTRICTED_RESULT_REMOVAL_WARNING,
    GovernanceIndex,
    apply_governance_to_answer,
    filter_restricted_results,
)
from .indexing import SQLiteIndex
from .models import GeneratedAnswer, SearchFilters, SearchResult


SearchFn = Callable[[str, Path, Optional[SearchFilters], int, str], List[SearchResult]]
AskFn = Callable[[str, Path, Optional[SearchFilters], int, str], GeneratedAnswer]

STATUS_GOVERNANCE_KEYWORDS = (
    "不可直接對外引用",
    "不能引用",
    "不可引用",
    "draft",
    "archived",
    "deprecated",
    "status",
    "狀態",
    "草稿",
    "封存",
    "棄用",
)
COMPARISON_KEYWORDS = ("比較", "差異", "對比", "vs", "versus", "compare", "今年", "去年")
FRESHNESS_KEYWORDS = ("最新", "過期", "新鮮度", "更新", "publish_date", "updated_date", "日期")
SYNTHESIS_KEYWORDS = ("有哪些", "整理", "彙整", "推薦", "適合", "策略", "分析", "跨", "多個", "素材", "漏斗")

NON_PUBLIC_STATUS_ORDER = ("archived", "deprecated", "draft")


@dataclass(frozen=True)
class QueryAnalysis:
    question_type: str
    needs_agent: bool
    reasons: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AgentToolCall:
    tool_name: str
    reason: str
    query: str = ""
    filters: Dict[str, object] = field(default_factory=dict)
    limit: int = 5

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AgentObservation:
    tool_name: str
    query: str
    result_count: int
    source_paths: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AgentReflection:
    sufficient: bool
    notes: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AgentTrace:
    mode: str
    analysis: QueryAnalysis
    plan: List[AgentToolCall]
    observations: List[AgentObservation]
    reflection: AgentReflection

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "analysis": self.analysis.to_dict(),
            "plan": [step.to_dict() for step in self.plan],
            "observations": [observation.to_dict() for observation in self.observations],
            "reflection": self.reflection.to_dict(),
        }


@dataclass(frozen=True)
class AgenticAnswer:
    generated: GeneratedAnswer
    trace: AgentTrace

    @property
    def question(self) -> str:
        return self.generated.question

    @property
    def answer(self) -> str:
        return self.generated.answer

    @property
    def citations(self):
        return self.generated.citations

    @property
    def warnings(self) -> List[str]:
        return self.generated.warnings

    @property
    def governance_checked(self) -> bool:
        return self.generated.governance_checked


def agentic_ask(
    question: str,
    db_path: Path,
    search_fn: SearchFn,
    ask_fn: AskFn,
    filters: Optional[SearchFilters] = None,
    limit: int = 5,
    mode: str = "hybrid",
    governance_index: Optional[GovernanceIndex] = None,
) -> AgenticAnswer:
    filters = filters or SearchFilters()
    analysis = analyze_question(question)

    if not analysis.needs_agent:
        generated = ask_fn(question, db_path, filters, limit, mode)
        generated = apply_governance_to_answer(generated, governance_index)
        trace = AgentTrace(
            mode="fast_path",
            analysis=analysis,
            plan=[],
            observations=[],
            reflection=AgentReflection(
                sufficient=bool(generated.citations),
                notes=["simple_lookup 使用既有單步 RAG 流程。"],
            ),
        )
        return AgenticAnswer(generated=generated, trace=trace)

    plan = build_plan(question, analysis, filters, limit)
    results, observations = execute_plan(plan, db_path, search_fn, mode)
    ranked_results = _dedupe_results(results)[:limit]
    ranked_results, removed_count = filter_restricted_results(ranked_results, governance_index)
    generated = generate_answer(question, ranked_results, citation_limit=min(3, limit))
    _append_removal_warning(generated, removed_count)
    generated = apply_governance_to_answer(generated, governance_index)
    reflection = reflect_results(analysis, ranked_results, generated, filters)

    return AgenticAnswer(
        generated=generated,
        trace=AgentTrace(
            mode="agentic_lite",
            analysis=analysis,
            plan=plan,
            observations=observations,
            reflection=reflection,
        ),
    )


def analyze_question(question: str) -> QueryAnalysis:
    normalized = question.lower()
    reasons: List[str] = []

    if _contains_any(normalized, STATUS_GOVERNANCE_KEYWORDS):
        reasons.append("偵測到 status / 引用限制治理意圖。")
    if _contains_any(normalized, COMPARISON_KEYWORDS):
        reasons.append("偵測到比較或差異分析意圖。")
    if _contains_any(normalized, FRESHNESS_KEYWORDS):
        reasons.append("偵測到資料新鮮度或日期檢查意圖。")
    if _contains_any(normalized, SYNTHESIS_KEYWORDS):
        reasons.append("偵測到跨素材整理或多來源綜合意圖。")

    if not reasons:
        return QueryAnalysis(
            question_type="simple_lookup",
            needs_agent=False,
            reasons=["未偵測到需要多步規劃的意圖。"],
        )

    if any("引用限制" in reason for reason in reasons):
        question_type = "status_governance"
    elif any("比較" in reason for reason in reasons):
        question_type = "comparison"
    elif any("新鮮度" in reason for reason in reasons):
        question_type = "freshness_check"
    else:
        question_type = "multi_source_synthesis"

    return QueryAnalysis(question_type=question_type, needs_agent=True, reasons=reasons)


def build_plan(
    question: str,
    analysis: QueryAnalysis,
    filters: SearchFilters,
    limit: int,
) -> List[AgentToolCall]:
    plan = [
        AgentToolCall(
            tool_name="search",
            reason="先用原始問題取得主要候選 chunks。",
            query=question,
            filters=filters.as_dict(),
            limit=limit,
        )
    ]

    if analysis.question_type == "status_governance":
        statuses = filters.status or list(NON_PUBLIC_STATUS_ORDER)
        for status in statuses:
            plan.append(
                AgentToolCall(
                    tool_name="search",
                    reason=f"補查 status={status} 的不可對外引用資料。",
                    query=question,
                    filters=_override_filters(filters, status=[status]).as_dict(),
                    limit=max(2, limit // 2),
                )
            )
    elif analysis.question_type == "comparison":
        plan.append(
            AgentToolCall(
                tool_name="search",
                reason="補查案例或具體素材脈絡，支援比較。",
                query=f"{question} case study 案例 內容重點",
                filters=filters.as_dict(),
                limit=limit,
            )
        )
        plan.append(
            AgentToolCall(
                tool_name="search",
                reason="補查 blog / guide 類型脈絡，支援比較。",
                query=f"{question} blog guide 指南",
                filters=filters.as_dict(),
                limit=limit,
            )
        )
    elif analysis.question_type == "freshness_check":
        plan.append(
            AgentToolCall(
                tool_name="index_stats",
                reason="讀取 index 規模，讓新鮮度檢查有資料覆蓋背景。",
            )
        )
        plan.append(
            AgentToolCall(
                tool_name="search",
                reason="補查日期、更新與 published 狀態相關內容。",
                query=f"{question} publish_date updated_date status",
                filters=filters.as_dict(),
                limit=limit,
            )
        )
    else:
        plan.append(
            AgentToolCall(
                tool_name="search",
                reason="補查案例、素材與使用情境，支援跨來源綜合。",
                query=f"{question} 案例 素材 使用情境",
                filters=filters.as_dict(),
                limit=limit,
            )
        )
        plan.append(
            AgentToolCall(
                tool_name="search",
                reason="補查策略、指南與漏斗階段脈絡。",
                query=f"{question} 策略 指南 漏斗",
                filters=filters.as_dict(),
                limit=limit,
            )
        )

    return plan[:4]


def execute_plan(
    plan: List[AgentToolCall],
    db_path: Path,
    search_fn: SearchFn,
    mode: str,
) -> Tuple[List[SearchResult], List[AgentObservation]]:
    results: List[SearchResult] = []
    observations: List[AgentObservation] = []

    for step in plan:
        if step.tool_name == "search":
            step_results = search_fn(
                step.query,
                Path(db_path),
                SearchFilters(**step.filters),
                step.limit,
                mode,
            )
            results.extend(step_results)
            observations.append(
                AgentObservation(
                    tool_name=step.tool_name,
                    query=step.query,
                    result_count=len(step_results),
                    source_paths=_unique_source_paths(step_results),
                )
            )
            continue

        if step.tool_name == "index_stats":
            counts = SQLiteIndex(Path(db_path)).counts()
            observations.append(
                AgentObservation(
                    tool_name=step.tool_name,
                    query="",
                    result_count=0,
                    notes=[f"documents={counts['documents']}", f"chunks={counts['chunks']}"],
                )
            )

    return results, observations


def reflect_results(
    analysis: QueryAnalysis,
    results: List[SearchResult],
    generated: GeneratedAnswer,
    filters: SearchFilters,
) -> AgentReflection:
    notes: List[str] = []
    expected_minimum = 2 if analysis.question_type in {"comparison", "multi_source_synthesis"} else 1

    if len(generated.citations) >= expected_minimum:
        notes.append("引用數量足以支撐目前回答。")
    else:
        notes.append("引用數量不足，建議放寬條件或補充資料後再回答。")

    source_types = sorted({result.chunk.metadata.source_type for result in results})
    if len(source_types) > 1:
        notes.append(f"已取得多種 source_type：{', '.join(source_types)}。")
    elif analysis.question_type in {"comparison", "multi_source_synthesis"}:
        notes.append("目前只取得單一 source_type，跨來源完整度有限。")

    risky_statuses = sorted({citation.status for citation in generated.citations if citation.status != "published"})
    if risky_statuses:
        notes.append(f"包含不可直接對外引用狀態：{', '.join(risky_statuses)}。")

    if not filters.is_empty() and not results:
        notes.append("metadata filter 沒有命中資料。")

    return AgentReflection(sufficient=len(generated.citations) >= expected_minimum, notes=notes)


def _contains_any(text: str, keywords: tuple) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _append_removal_warning(answer: GeneratedAnswer, removed_count: int) -> None:
    if not removed_count:
        return
    warning = RESTRICTED_RESULT_REMOVAL_WARNING.format(count=removed_count)
    if warning not in answer.warnings:
        answer.warnings.append(warning)


def _override_filters(filters: SearchFilters, **overrides: List[str]) -> SearchFilters:
    data = filters.as_dict()
    for key, value in overrides.items():
        data[key] = value
    return SearchFilters(**data)


def _dedupe_results(results: List[SearchResult]) -> List[SearchResult]:
    by_chunk_id: Dict[str, SearchResult] = {}
    for result in results:
        chunk_id = result.chunk.id
        current = by_chunk_id.get(chunk_id)
        if current is None or _effective_score(result) > _effective_score(current):
            by_chunk_id[chunk_id] = result

    return sorted(by_chunk_id.values(), key=_effective_score, reverse=True)


def _effective_score(result: SearchResult) -> float:
    return result.rerank_score or result.score


def _unique_source_paths(results: List[SearchResult]) -> List[str]:
    source_paths: List[str] = []
    for result in results:
        source_path = result.chunk.source_path
        if source_path not in source_paths:
            source_paths.append(source_path)
    return source_paths
