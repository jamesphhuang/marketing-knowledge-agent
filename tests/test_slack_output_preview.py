import csv
import hashlib
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.models import (
    Citation,
    GeneratedAnswer,
    StructuredAsset,
    StructuredEntity,
    StructuredRetrievalResult,
)
from marketing_knowledge_agent.slack_output_preview import (
    AssetUrlOverlay,
    AssetUrlRecord,
    apply_approved_asset_url_overlay,
    build_slack_preview_payload,
    generate_slack_output_preview,
    load_asset_url_overlay,
    render_slack_preview,
)
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.query_gating import RESTRICTED_QUERY_REFUSAL
from marketing_knowledge_agent.slack_output_preview_reports import OUTPUT_FILENAMES


def test_brand_query_only_shows_matched_brand():
    answer = _answer(
        [_entity("Merchant A", "handle-a", [_asset("article", "Story A", "r8", "[1]")])],
        constraints=[_constraint("entity_name", "Merchant A", "商家／夥伴名稱")],
    )
    bundle = build_slack_preview_payload("提供我 Merchant A 的內容", answer, _overlay("r8", "article"))

    text = render_slack_preview(bundle.payload, "standard")

    assert "Merchant A" in text
    assert "Merchant B" not in text
    assert bundle.payload["total_entities"] == 1


def test_handle_query_shows_resolved_merchant():
    answer = _answer(
        [_entity("Merchant A", "handle-a", [_asset("article", "Story A", "r8", "[1]")])],
        resolved_entities=[{"entity_type": "merchant", "canonical_name": "handle-a", "matched_text": "handle-a", "source": "merchant_handle", "confidence": 1.0}],
        constraints=[_constraint("merchant_handle", "handle-a", "Merchant Handle")],
    )
    bundle = build_slack_preview_payload("handle-a", answer, _overlay("r8", "article"))

    text = render_slack_preview(bundle.payload, "standard")

    assert "Handle「handle-a」對應品牌：Merchant A" in text


def test_category_query_groups_by_merchant():
    entities = [
        _entity("Merchant A", "a", [_asset("article", "Story A", "r8", "[1]")]),
        _entity("Merchant B", "b", [_asset("video", "Story B", "r9", "[2]")]),
    ]
    answer = _answer(entities, constraints=[_constraint("sales_category_lv1", "居家生活", "Sales Category LV1")])
    overlay = _merge_overlay(_overlay("r8", "article"), _overlay("r9", "video"))

    text = render_slack_preview(build_slack_preview_payload("居家生活", answer, overlay).payload, "detailed")

    assert text.count("品牌：Merchant A") == 1
    assert text.count("品牌：Merchant B") == 1
    assert "Sales Category LV1：居家生活" in text


def test_asset_type_query_hides_unrelated_asset_types_and_empty_sections():
    answer = _answer(
        [_entity("Merchant A", "a", [_asset("video", "Video A", "r8", "[1]")])],
        constraints=[_constraint("asset_type", "video", "內容類型")],
    )
    bundle = build_slack_preview_payload("居家生活 影片", answer, _overlay("r8", "video"))

    text = render_slack_preview(bundle.payload, "standard")

    assert "影片" in text
    assert "文章" not in text
    assert "Podcast" not in text
    assert "新聞" not in text


def test_approved_asset_url_is_clickable_and_canonical_is_not_user_facing():
    answer = _answer([_entity("Merchant A", "a", [_asset("article", "Story A", "r8", "[1]")])])
    overlay = _overlay(
        "r8",
        "article",
        asset_url="https://example.com/display",
        canonical_url="https://example.com/backend-canonical",
    )

    bundle = build_slack_preview_payload("Merchant A", answer, overlay)
    texts = [render_slack_preview(bundle.payload, variant) for variant in ("concise", "standard", "detailed")]

    assert all("https://example.com/display" in text for text in texts)
    assert all("backend-canonical" not in text for text in texts)
    assert "canonical_url" not in json.dumps(bundle.payload, ensure_ascii=False)
    assert bundle.backend_citations[0]["canonical_url"] == "https://example.com/backend-canonical"


def test_missing_asset_url_is_explicit_and_does_not_fall_back_to_canonical():
    answer = _answer([_entity("Merchant A", "a", [_asset("article", "Story A", "r8", "[1]")])])
    full_overlay = _overlay(
        "r8",
        "article",
        canonical_url="https://example.com/backend-only",
    )
    full_overlay.values.pop(("Sheet:r8", "Sheet:r8:article", "asset_url"))

    bundle = build_slack_preview_payload("Merchant A", answer, full_overlay)
    text = render_slack_preview(bundle.payload, "standard")

    assert "連結未提供" in text
    assert "backend-only" not in text
    assert any(item["code"] == "url_overlay_missing" for item in bundle.warnings)


def test_approved_overlay_updates_each_asset_and_matching_citation_independently():
    assets = [
        _asset("article", "Story A", "r8", "[1]"),
        _asset("video", "Story B", "r8", "[2]"),
    ]
    answer = _answer([_entity("Merchant A", "a", assets)])
    overlay = _merge_overlay(
        _overlay(
            "r8",
            "article",
            asset_url="https://example.com/article-direct",
            canonical_url="https://example.com/article-canonical",
        ),
        _overlay(
            "r8",
            "video",
            asset_url="https://example.com/video-direct",
            canonical_url="https://example.com/video-canonical",
        ),
    )

    assert apply_approved_asset_url_overlay(answer, overlay) == 2
    assert [asset.url for asset in assets] == [
        "https://example.com/article-canonical",
        "https://example.com/video-canonical",
    ]
    assert [citation.canonical_url for citation in answer.citations] == [
        "https://example.com/article-canonical",
        "https://example.com/video-canonical",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///internal/content.md",
        "data:text/plain,unsafe",
        "https://user:password@example.com/path",
        "http://[invalid",
    ],
)
def test_invalid_overlay_url_never_reaches_structured_assets_or_citations(url):
    asset = _asset("article", "Story A", "r8", "[1]")
    answer = _answer([_entity("Merchant A", "a", [asset])])
    asset.url = None
    answer.citations[0].canonical_url = None
    record_id = "Sheet:r8"
    asset_id = f"{record_id}:article"
    overlay = AssetUrlOverlay(
        values={
            (record_id, asset_id, "asset_url"): AssetUrlRecord(
                record_id, asset_id, "asset_url", url, "Reviewer", "2026-07-17"
            )
        }
    )

    assert apply_approved_asset_url_overlay(answer, overlay) == 0
    assert asset.url is None
    assert answer.citations[0].canonical_url is None


def test_governance_blocked_or_non_external_asset_cannot_receive_an_overlay_url():
    asset = _asset("article", "Secret Story", "r8", "[1]")
    answer = _answer([_entity("Restricted Brand", "secret", [asset])])
    asset.url = None
    answer.citations[0].canonical_url = None
    answer.citations[0].can_quote_externally = False
    overlay = _overlay("r8", "article")

    assert apply_approved_asset_url_overlay(answer, overlay) == 0
    assert asset.url is None
    assert answer.citations[0].canonical_url is None


def test_pending_source_url_cannot_receive_an_overlay_url():
    asset = _asset("article", "Pending Story", "r8", "[1]")
    answer = _answer([_entity("Merchant A", "a", [asset])])
    asset.url = None
    answer.citations[0].canonical_url = None
    answer.citations[0].record_type = "pending_metric"
    overlay = _overlay("r8", "article")

    assert apply_approved_asset_url_overlay(answer, overlay) == 0
    assert asset.url is None
    assert answer.citations[0].canonical_url is None


def test_governance_blocked_or_restricted_asset_is_omitted():
    answer = _answer([_entity("Restricted Brand", "secret", [_asset("article", "Secret Story", "r8", "[1]")])])
    overlay = _overlay("r8", "article", blocked=True)

    bundle = build_slack_preview_payload("一般查詢", answer, overlay)
    text = render_slack_preview(bundle.payload, "standard")

    assert "Restricted Brand" not in text
    assert "Secret Story" not in text
    assert bundle.payload["total_assets"] == 0
    assert bundle.payload["citations"] == []
    assert any(item["code"] == "governance_blocked_asset" for item in bundle.warnings)


def test_restricted_query_refusal_does_not_echo_sensitive_query():
    answer = GeneratedAnswer(
        question="Restricted Brand",
        answer=RESTRICTED_QUERY_REFUSAL,
        citations=[],
        warnings=[],
        governance_checked=True,
    )

    bundle = build_slack_preview_payload("Restricted Brand", answer, AssetUrlOverlay())
    text = render_slack_preview(bundle.payload, "standard")

    assert text == RESTRICTED_QUERY_REFUSAL
    assert "Restricted Brand" not in json.dumps(bundle.payload, ensure_ascii=False)
    assert bundle.errors == []


def test_no_result_and_unsupported_answers_have_no_citations():
    no_result = _answer(
        [],
        abstained=True,
        abstain_reason="unresolved_structured_lookup",
        execution_blocked=True,
    )
    unsupported = _answer(
        [],
        abstained=True,
        abstain_reason="unsupported_hard_constraint",
        execution_blocked=True,
        unsupported=[_constraint("publication_status", "published", "內容上線狀態", status="unsupported")],
    )

    no_result_bundle = build_slack_preview_payload("Missing Brand", no_result, AssetUrlOverlay())
    unsupported_bundle = build_slack_preview_payload("已上線的影片", unsupported, AssetUrlOverlay())

    assert "精確匹配" in render_slack_preview(no_result_bundle.payload, "standard")
    assert "內容上線狀態" in render_slack_preview(unsupported_bundle.payload, "standard")
    assert no_result_bundle.payload["citations"] == []
    assert unsupported_bundle.payload["citations"] == []


def test_zero_intersection_does_not_become_or():
    constraints = [
        _constraint("interview_year", 2025, "採訪年份"),
        _constraint("sales_category_lv1", "居家生活", "Sales Category LV1"),
        _constraint("asset_type", "podcast", "內容類型"),
    ]
    answer = _answer([], constraints=constraints, abstained=True, abstain_reason="no_constraint_intersection")

    bundle = build_slack_preview_payload("2025 居家生活 Podcast", answer, AssetUrlOverlay())
    text = render_slack_preview(bundle.payload, "standard")

    assert "同時符合所有條件" in text
    assert bundle.payload["total_assets"] == 0
    assert bundle.payload["citations"] == []


def test_source_records_are_deduplicated_and_raw_markdown_is_not_rendered():
    assets = [_asset("article", "Story A", "r8", "[1]"), _asset("video", "Story B", "r8", "[2]")]
    answer = _answer([_entity("Merchant A", "a", assets)], raw_answer="# Content Assets\n| Asset | Title |")
    overlay = _merge_overlay(_overlay("r8", "article"), _overlay("r8", "video"))

    text = render_slack_preview(build_slack_preview_payload("Merchant A", answer, overlay).payload, "concise")

    assert text.count("資料來源：Sheet r8") == 1
    assert "Content Assets" not in text
    assert "| Asset |" not in text


def test_internal_identifiers_and_technical_fields_are_not_rendered():
    answer = _answer([_entity("Merchant A", "a", [_asset("article", "Story A", "r8", "[1]")])])
    bundle = build_slack_preview_payload("Merchant A", answer, _overlay("r8", "article"))

    text = render_slack_preview(bundle.payload, "detailed")

    for forbidden in ("asset_id", "record_id", "metadata_json", "retrieval score", "source_location", "provenance"):
        assert forbidden not in text
        assert forbidden not in json.dumps(bundle.payload, ensure_ascii=False)


def test_all_variants_use_identical_result_sets_and_do_not_mutate_payload():
    answer = _answer([_entity("Merchant A", "a", [_asset("article", "Story A", "r8", "[1]")])])
    bundle = build_slack_preview_payload("Merchant A", answer, _overlay("r8", "article"))
    before = json.dumps(bundle.payload, ensure_ascii=False, sort_keys=True)

    outputs = [render_slack_preview(bundle.payload, variant) for variant in ("concise", "standard", "detailed")]

    assert all("Story A" in text for text in outputs)
    assert json.dumps(bundle.payload, ensure_ascii=False, sort_keys=True) == before


def test_long_category_result_reports_total_and_display_limits():
    entities = []
    overlay = AssetUrlOverlay()
    for index in range(1, 8):
        record = f"r{index}"
        entities.append(_entity(f"Merchant {index}", f"h{index}", [_asset("article", f"Story {index}", record, f"[{index}]")]))
        overlay = _merge_overlay(overlay, _overlay(record, "article"))
    answer = _answer(entities, constraints=[_constraint("sales_category_lv1", "居家生活", "Sales Category LV1")])

    text = render_slack_preview(build_slack_preview_payload("居家生活", answer, overlay).payload, "standard")

    assert "共找到 7 個品牌、7 筆內容" in text
    assert "目前只顯示前 5 個品牌" in text
    assert "Merchant 6" not in text


def test_load_overlay_uses_record_asset_field_join_and_only_approved_ready_rows(tmp_path):
    apply_path = tmp_path / "asset_apply_preview.csv"
    blocked_path = tmp_path / "asset_apply_preview_blocked.csv"
    decisions_path = tmp_path / "human_review_template.csv"
    apply_rows = [_apply_row("r8", "r8:article", field) for field in ("asset_url", "canonical_url")]
    decisions = [_decision_row(row) for row in apply_rows]
    _write_csv(apply_path, apply_rows)
    _write_csv(blocked_path, [_apply_row("r9", "r9:video", "asset_url", action="blocked", eligibility="governance_blocked")])
    _write_csv(decisions_path, decisions)

    overlay = load_asset_url_overlay(apply_path, blocked_path, decisions_path)

    assert overlay.value("r8", "r8:article", "asset_url") == "https://example.com/r8/article/asset_url"
    assert "r9:video" in overlay.blocked_asset_ids
    assert overlay.errors == []


def test_preview_generation_is_deterministic_and_does_not_modify_sources(tmp_path):
    paths = _preview_fixture(tmp_path)
    source_hashes = {name: _hash(path) for name, path in paths.items() if path.is_file()}

    first = generate_slack_output_preview(
        queries=["Merchant A"],
        db_path=paths["db"],
        apply_preview_path=paths["apply"],
        blocked_preview_path=paths["blocked"],
        decisions_path=paths["decisions"],
        restricted_customers_path=paths["restricted"],
        output_dir=paths["output"],
        ask_fn=lambda *args, **kwargs: _answer([_entity("Merchant A", "a", [_asset("article", "Story A", "r8", "[1]")])]),
    )
    first_outputs = _output_hashes(paths["output"])
    second = generate_slack_output_preview(
        queries=["Merchant A"],
        db_path=paths["db"],
        apply_preview_path=paths["apply"],
        blocked_preview_path=paths["blocked"],
        decisions_path=paths["decisions"],
        restricted_customers_path=paths["restricted"],
        output_dir=paths["output"],
        ask_fn=lambda *args, **kwargs: _answer([_entity("Merchant A", "a", [_asset("article", "Story A", "r8", "[1]")])]),
    )

    assert first == second
    assert first_outputs == _output_hashes(paths["output"])
    assert set(first_outputs) == set(OUTPUT_FILENAMES)
    assert source_hashes == {name: _hash(path) for name, path in paths.items() if name in source_hashes}


def test_preview_module_does_not_read_tokens_or_call_slack_api():
    source = Path("src/marketing_knowledge_agent/slack_output_preview.py").read_text(encoding="utf-8")

    for forbidden in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "slack_bolt", "chat_postMessage", "socket_mode"):
        assert forbidden not in source


def test_preview_cli_does_not_require_slack_tokens(monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    monkeypatch.setattr(
        "marketing_knowledge_agent.cli.preview_slack_query",
        lambda **kwargs: "offline preview",
    )

    exit_code = main(["preview-slack-output", "--query", "Merchant A", "--variant", "standard"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "offline preview"


def _answer(
    entities,
    *,
    constraints=None,
    resolved_entities=None,
    unsupported=None,
    abstained=False,
    abstain_reason=None,
    execution_blocked=False,
    raw_answer="unused structured answer",
):
    constraints = constraints or []
    resolved_entities = resolved_entities or []
    unsupported = unsupported or []
    plan = {
        "raw_query": "query",
        "normalized_query": "query",
        "query_mode": "structured_lookup",
        "resolved_entities": resolved_entities,
        "constraints": constraints + unsupported,
        "hard_filters": constraints + unsupported,
        "supported_constraints": constraints,
        "unsupported_constraints": unsupported,
        "ambiguous_constraints": [],
        "invalid_constraints": [],
        "operator": "AND",
        "requested_asset_types": [],
        "abstain_reason": abstain_reason,
    }
    citations = []
    for entity in entities:
        for asset in entity.assets:
            citations.append(
                _citation(
                    asset.citation_label,
                    asset.title,
                    asset.source_row or 0,
                    asset.asset_type,
                )
            )
    structured = StructuredRetrievalResult(
        query_plan=plan,
        matched_entities=entities,
        total_entities=len(entities),
        total_assets=sum(len(entity.assets) for entity in entities),
        unsupported_constraints=unsupported,
        execution_blocked=execution_blocked,
        abstained=abstained,
        abstain_reason=abstain_reason,
    )
    return GeneratedAnswer(
        question="query",
        answer=raw_answer,
        citations=citations,
        warnings=[],
        governance_checked=True,
        query_plan=plan,
        structured_result=structured,
    )


def _entity(name, handle, assets):
    return StructuredEntity(
        entity_type="merchant",
        entity_name=name,
        merchant_handle=handle,
        sales_category_lv1="居家生活",
        sales_category_lv2="生活日用品",
        interview_year=2025,
        assets=assets,
    )


def _asset(asset_type, title, record, label):
    row = int(record.lstrip("r"))
    return StructuredAsset(
        asset_type=asset_type,
        title=title,
        url=None,
        published_at=None,
        publication_status=None,
        external_usage_status="可對外引用",
        source_record_id=f"Sheet:{record}",
        source_sheet="Sheet",
        source_row=row,
        citation_label=label,
    )


def _citation(label, title, row, asset_type):
    return Citation(
        label=label,
        title=title,
        source_path=f"internal/{row}.md",
        chunk_id=f"chunk-{row}:{asset_type}",
        status="published",
        source_type="database",
        record_type="merchant_case",
        data_classification="public",
        can_quote_externally=True,
        publish_date="2026-07-01",
        source_sheet="Sheet",
        source_row=row,
        canonical_url="https://old.example.com/record",
        freshness_note="fresh",
    )


def _constraint(field, value, label, status="supported"):
    return {
        "field": field,
        "value": value,
        "normalized_value": value,
        "operator": "eq" if field == "interview_year" else "exact",
        "match_type": "canonical_exact",
        "hard_filter": True,
        "source": "test",
        "confidence": 1.0,
        "support_status": status,
        "reason": "not available" if status != "supported" else None,
        "raw_value": value,
        "output_label": label,
    }


def _overlay(record, asset_type, *, asset_url=None, canonical_url=None, blocked=False):
    record_id = f"Sheet:{record}"
    asset_id = f"{record_id}:{asset_type}"
    values = {}
    if not blocked:
        values[(record_id, asset_id, "asset_url")] = AssetUrlRecord(
            record_id, asset_id, "asset_url", asset_url or f"https://example.com/{record}/{asset_type}", "Reviewer", "2026-07-17"
        )
        values[(record_id, asset_id, "canonical_url")] = AssetUrlRecord(
            record_id, asset_id, "canonical_url", canonical_url or f"https://example.com/{record}/{asset_type}/canonical", "Reviewer", "2026-07-17"
        )
    return AssetUrlOverlay(values=values, blocked_asset_ids={asset_id} if blocked else set())


def _merge_overlay(*overlays):
    values = {}
    blocked = set()
    errors = []
    warnings = []
    for overlay in overlays:
        values.update(overlay.values)
        blocked.update(overlay.blocked_asset_ids)
        errors.extend(overlay.errors)
        warnings.extend(overlay.warnings)
    return AssetUrlOverlay(values=values, blocked_asset_ids=blocked, errors=errors, warnings=warnings)


def _apply_row(record_id, asset_id, field, *, action="add", eligibility="ready_for_apply_preview"):
    return {
        "record_id": record_id,
        "asset_id": asset_id,
        "brand_name": "Merchant A",
        "asset_type": asset_id.rsplit(":", 1)[-1],
        "asset_title": "Story A",
        "field": field,
        "current_value": "",
        "proposed_value": f"https://example.com/{record_id}/{asset_id.rsplit(':', 1)[-1]}/{field}",
        "review_decision": "approve" if action != "blocked" else "exclude_asset",
        "reviewer": "Reviewer",
        "reviewed_at": "2026-07-17",
        "provenance": "reviewed",
        "source_location": "Sheet!H8",
        "eligibility": eligibility,
        "governance_status": "eligible" if action != "blocked" else "blocked",
        "action": action,
        "reason": "preview",
    }


def _decision_row(apply_row):
    return {
        "record_id": apply_row["record_id"],
        "asset_id": apply_row["asset_id"],
        "brand_name": apply_row["brand_name"],
        "asset_type": apply_row["asset_type"],
        "field": apply_row["field"],
        "existing_value": apply_row["current_value"],
        "proposed_value": apply_row["proposed_value"],
        "source": "excel_hyperlink",
        "source_location": apply_row["source_location"],
        "provenance": apply_row["provenance"],
        "confidence": "high",
        "conflict_status": "none",
        "review_required": "true",
        "reason": "candidate",
        "proposed_decision": "approve_candidate",
        "approved_for_index": "false",
        "review_decision": apply_row["review_decision"],
        "reviewer": apply_row["reviewer"],
        "reviewed_at": apply_row["reviewed_at"],
        "notes": "",
    }


def _preview_fixture(tmp_path):
    apply_rows = [_apply_row("Sheet:r8", "Sheet:r8:article", field) for field in ("asset_url", "canonical_url")]
    paths = {
        "apply": tmp_path / "asset_apply_preview.csv",
        "blocked": tmp_path / "asset_apply_preview_blocked.csv",
        "decisions": tmp_path / "human_review_template.csv",
        "restricted": tmp_path / "restricted_customers.json",
        "db": tmp_path / "content_index.sqlite",
        "output": tmp_path / "slack_output_preview",
    }
    _write_csv(paths["apply"], apply_rows)
    _write_csv(paths["blocked"], [_apply_row("Sheet:r9", "Sheet:r9:video", "asset_url", action="blocked", eligibility="governance_blocked")])
    _write_csv(paths["decisions"], [_decision_row(row) for row in apply_rows])
    paths["restricted"].write_text("[]", encoding="utf-8")
    paths["db"].write_bytes(b"read-only sentinel")
    return paths


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _output_hashes(path):
    return {
        item.name: _hash(item)
        for item in sorted(path.iterdir())
        if item.is_file() and not item.name.startswith("._")
    }
