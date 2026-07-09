from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .ingestion import discover_markdown_files
from .validation import validate_vault


KEYWORD_RULES = {
    "product": [
        ("shopline-payments", ["shopline payments", "payments", "金流"]),
        ("pos", ["pos", "快閃店", "門市", "收銀"]),
        ("omo", ["omo", "全通路", "線上線下"]),
        ("line", ["line"]),
        ("youtube-shopping", ["youtube shopping"]),
        ("shopper-app", ["app", "會員 app"]),
        ("shop-builder", ["shop builder", "促購元件"]),
        ("smart-omo", ["smart omo", "會員導購"]),
        ("shopline", ["shopline"]),
    ],
    "industry": [
        ("sports", ["運動", "棒球", "馬拉松", "球迷"]),
        ("beauty", ["美妝", "保養", "肌膚", "頭皮", "養肌"]),
        ("food", ["食品", "燕窩", "雞", "茶", "漢餅", "超市"]),
        ("home-living", ["家居", "家電", "生活單品"]),
        ("fashion", ["飾品", "服飾", "配件"]),
        ("education", ["課程", "購課"]),
        ("health", ["健康", "心理", "療癒"]),
        ("retail", ["零售", "門市", "品牌"]),
        ("ecommerce", ["電商", "官網", "購物", "銷售"]),
    ],
    "topic": [
        ("case-study", ["案例", "看《", "如何", "助品牌"]),
        ("omo", ["omo", "全通路", "線上線下"]),
        ("pos", ["pos", "快閃店", "門市"]),
        ("payments", ["payments", "金流", "支付", "分期"]),
        ("line-commerce", ["line", "直播"]),
        ("youtube-shopping", ["youtube shopping", "youtube"]),
        ("member-retention", ["會員", "回購", "crm"]),
        ("group-buying", ["團購", "kol", "開團"]),
        ("conversion", ["轉換", "成長", "業績", "銷量"]),
        ("brand-story", ["品牌", "創辦", "故事"]),
        ("ai", ["ai agent", "ai"]),
    ],
}


def generate_backfill_report(vault_path: Path, output_path: Optional[Path] = None) -> Dict[str, object]:
    vault_path = Path(vault_path)
    validation_report = validate_vault(vault_path)
    candidates = [
        candidate
        for candidate in (
            _candidate_from_validation_result(vault_path, result)
            for result in validation_report["files"]
        )
        if candidate is not None
    ]
    text = render_backfill_report(vault_path, validation_report, candidates)

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

    return {
        "vault_path": str(vault_path),
        "output_path": str(output_path) if output_path else None,
        "candidate_count": len(candidates),
        "validation_summary": validation_report["summary"],
        "candidates": candidates,
        "report": text,
    }


def render_backfill_report(
    vault_path: Path,
    validation_report: Dict[str, object],
    candidates: List[Dict[str, object]],
) -> str:
    lines = [
        "# Metadata Backfill Candidates",
        "",
        f"- Vault: `{vault_path}`",
        f"- Generated date: `{date.today().isoformat()}`",
        f"- Validation summary: `{validation_report['summary']}`",
        f"- Candidate count: `{len(candidates)}`",
        "",
        "本報告只提供候選 metadata，不會修改原始 Markdown。`TODO` 欄位需要人工確認後才能寫回 frontmatter。",
        "",
    ]

    if not candidates:
        lines.extend(["No missing-frontmatter Markdown files found.", ""])
        return "\n".join(lines)

    for index, candidate in enumerate(candidates, start=1):
        lines.extend(
            [
                f"## {index}. {candidate['path']}",
                "",
                f"- Source heading: `{candidate['source_heading']}`",
                f"- Confidence: `{candidate['confidence']}`",
                "- Review notes:",
            ]
        )
        for note in candidate["review_notes"]:
            lines.append(f"  - {note}")
        lines.extend(["", "```yaml"])
        lines.extend(_frontmatter_lines(candidate["metadata"]))
        lines.extend(["```", "", "### Preview", "", candidate["preview"], ""])
    return "\n".join(lines)


def _candidate_from_validation_result(
    vault_path: Path,
    result: Dict[str, object],
) -> Optional[Dict[str, object]]:
    if result.get("status") != "invalid":
        return None
    errors = result.get("errors", [])
    if not any(error.get("code") == "missing_frontmatter" for error in errors):
        return None

    relative_path = result["path"]
    path = vault_path / relative_path
    if path not in set(discover_markdown_files(vault_path)):
        return None

    text = path.read_text(encoding="utf-8")
    heading = _extract_heading(text) or Path(relative_path).stem
    combined_text = f"{heading}\n{text}"
    metadata = _candidate_metadata(relative_path, heading, combined_text)
    return {
        "path": relative_path,
        "source_heading": heading,
        "confidence": "low" if "TODO" in metadata.values() else "medium",
        "metadata": metadata,
        "review_notes": _review_notes(metadata),
        "preview": _preview(text),
    }


def _candidate_metadata(relative_path: str, title: str, text: str) -> Dict[str, object]:
    source_type = _source_type_from_path(relative_path)
    metadata: Dict[str, object] = {
        "title": title,
        "source_type": source_type,
    }
    if source_type == "showcase":
        metadata["content_category"] = "showcase"
        metadata["parent_source_type"] = "blog"

    metadata.update(
        {
            "product": _detect_values(text, KEYWORD_RULES["product"], default=["shopline"]),
            "industry": _detect_values(text, KEYWORD_RULES["industry"], default=["retail", "ecommerce"]),
            "topic": _detect_values(text, KEYWORD_RULES["topic"], default=["case-study"]),
            "funnel_stage": _detect_funnel_stage(text, source_type),
            "status": "draft",
            "publish_date": "TODO",
            "updated_date": "TODO",
            "canonical_url": "TODO",
            "language": "zh-TW",
            "author": "SHOPLINE",
        }
    )
    return metadata


def _source_type_from_path(relative_path: str) -> str:
    top_level = relative_path.split("/", 1)[0].lower()
    if top_level in {"blog", "showcase", "social", "podcast", "website", "youtube", "design"}:
        return top_level
    return "blog"


def _detect_values(
    text: str,
    rules: Iterable[tuple],
    default: List[str],
    limit: int = 5,
) -> List[str]:
    lowered = text.lower()
    values: List[str] = []
    for value, keywords in rules:
        if any(keyword.lower() in lowered for keyword in keywords):
            values.append(value)
    return values[:limit] or default


def _detect_funnel_stage(text: str, source_type: str) -> List[str]:
    lowered = text.lower()
    stages: List[str] = []
    if source_type in {"website", "social"} or any(word in lowered for word in ["認識", "品牌故事"]):
        stages.append("awareness")
    if source_type == "showcase" or any(word in lowered for word in ["案例", "比較", "解決方案"]):
        stages.append("consideration")
    if any(word in lowered for word in ["方案", "費用", "價格", "導入", "串接", "open api"]):
        stages.append("decision")
    return stages or ["consideration"]


def _extract_heading(text: str) -> Optional[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _review_notes(metadata: Dict[str, object]) -> List[str]:
    notes = [
        "publish_date、updated_date、canonical_url 必須人工確認。",
        "status 預設 draft，審核通過前不可直接對外引用。",
    ]
    if metadata.get("source_type") == "showcase":
        notes.append("已標示 parent_source_type=blog 與 content_category=showcase。")
    return notes


def _preview(text: str, max_length: int = 320) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 1].rstrip()}..."


def _frontmatter_lines(metadata: Dict[str, object]) -> List[str]:
    lines: List[str] = []
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f'{key}: "{_escape_yaml_string(str(value))}"')
    return lines


def _escape_yaml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
