from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from marketing_knowledge_agent.agentic import (
    AgenticAnswer,
    AgentReflection,
    AgentTrace,
    QueryAnalysis,
)
from marketing_knowledge_agent.models import Citation, GeneratedAnswer
from marketing_knowledge_agent.pipeline import agent_ask
from marketing_knowledge_agent.slack_interface import (
    SLACK_NO_RESULTS_MESSAGE,
    SlackConfig,
    format_slack_reply,
    SLACK_SEARCH_ASSET_CAP,
    SLACK_SEARCH_PARENT_CAP,
    handle_slack_event,
)


ROOT = Path(__file__).resolve().parents[1]
R15_SHEET = "「可公開」對外數據"
R32_SHEET = "商家夥伴案例資料庫"


@pytest.fixture
def formal_slack_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "content_index.sqlite"
    shutil.copy2(ROOT / ".mka/content_index.sqlite", db_path)
    projection_dir = tmp_path / ".mka"
    projection_dir.mkdir()
    shutil.copy2(
        ROOT / ".mka/search_alias_projection.json",
        projection_dir / "search_alias_projection.json",
    )
    restricted_path = tmp_path / "restricted_customers.json"
    shutil.copy2(
        ROOT / "reports/excel_preview/restricted_customers.json",
        restricted_path,
    )
    monkeypatch.chdir(tmp_path)
    return {
        "db_path": db_path,
        "projection_path": projection_dir / "search_alias_projection.json",
        "restricted_path": restricted_path,
        "audit_path": tmp_path / "slack_audit.csv",
    }


def test_formal_r15_metadata_reproduces_written_channel_conflict(
    formal_slack_snapshot,
):
    metadata = _formal_metadata(formal_slack_snapshot["db_path"], R15_SHEET, 15)

    assert metadata["record_type"] == "public_metric"
    assert metadata["data_classification"] == "public"
    assert metadata["can_enter_content_index"] is True
    assert metadata["can_quote_externally"] is True
    assert metadata["allowed_exposure_channels"] == ["verbal_briefing"]
    assert "不留文字紀錄" in metadata["restricted_note"]


def test_formal_r15_is_removed_from_slp_body_citations_and_groups(
    formal_slack_snapshot,
):
    reply, answer = _slack_query(formal_slack_snapshot, "<@U0BOT123> SLP")

    assert "聊心茶室" in reply["text"]
    assert _has_source(answer, R32_SHEET, 32)
    assert "累計總GMV" not in reply["text"]
    assert not _has_source(answer, R15_SHEET, 15)
    assert not _structured_has_source(answer, R15_SHEET, 15)


def test_formal_r15_generic_semantic_route_is_safe_no_result(
    formal_slack_snapshot,
):
    reply, answer = _slack_query(formal_slack_snapshot, "累計總GMV")

    assert reply["text"] == SLACK_NO_RESULTS_MESSAGE
    assert "300 至 500 億" not in answer.answer
    assert "不留文字紀錄" not in answer.answer
    assert answer.citations == []


def test_restricted_note_blocks_written_route_even_with_written_channel(
    formal_slack_snapshot,
):
    _update_formal_metadata_copy(
        formal_slack_snapshot["db_path"],
        R15_SHEET,
        15,
        allowed_exposure_channels=["press_release"],
    )

    reply, answer = _slack_query(formal_slack_snapshot, "累計總GMV")

    assert reply["text"] == SLACK_NO_RESULTS_MESSAGE
    assert not _has_source(answer, R15_SHEET, 15)


def test_missing_external_reference_metadata_fails_closed_in_written_route(
    formal_slack_snapshot,
):
    _update_formal_metadata_copy(
        formal_slack_snapshot["db_path"],
        R15_SHEET,
        15,
        remove_fields={"can_quote_externally"},
        allowed_exposure_channels=["press_release"],
        restricted_note=None,
    )

    reply, answer = _slack_query(formal_slack_snapshot, "累計總GMV")

    assert reply["text"] == SLACK_NO_RESULTS_MESSAGE
    assert not _has_source(answer, R15_SHEET, 15)


@pytest.mark.parametrize("projection_state", ["missing", "malformed"])
def test_alias_loader_fallback_keeps_written_governance(
    formal_slack_snapshot,
    projection_state,
):
    projection = formal_slack_snapshot["projection_path"]
    if projection_state == "missing":
        projection.unlink()
    else:
        projection.write_text("{", encoding="utf-8")

    reply, answer = _slack_query(formal_slack_snapshot, "SLP")

    assert "累計總GMV" not in reply["text"]
    assert not _has_source(answer, R15_SHEET, 15)


def test_shopline_payments_keeps_r32_and_legal_organic_results(
    formal_slack_snapshot,
):
    first_reply, first_answer = _slack_query(
        formal_slack_snapshot, "SHOPLINE Payments"
    )
    second_reply, second_answer = _slack_query(
        formal_slack_snapshot, "SHOPLINE Payments"
    )

    first_parents = _merchant_parent_ids(first_answer)
    assert first_parents == _merchant_parent_ids(second_answer)
    assert first_parents[0] == 32
    assert any(row != 32 for row in first_parents)
    assert "累計總GMV" not in first_reply["text"]
    assert not _has_source(first_answer, R15_SHEET, 15)
    assert first_reply["text"] == second_reply["text"]


def test_formal_shopline_caps_apply_after_asset_expansion(
    formal_slack_snapshot,
):
    """OLD: the Slack shaping cap clamped this exact-alias result to 5 parents and 10 assets.

    NEW (Slack Search Result Presentation v2): the parent side is unchanged -- the frozen alias
    merge contract in pipeline.search_index still caps this query at 5 parents -- but the Slack
    shaping cap no longer truncates asset expansion inside them, so each parent that survives the
    merge keeps every asset it expands into. That is what lets a brand group stay whole on one
    page. Every governance assertion below is unchanged.
    """
    _, answer = _slack_query(formal_slack_snapshot, "SHOPLINE Payments")
    structured = answer.generated.structured_result

    assert structured is not None
    assert structured.total_entities == 5
    assert structured.total_assets == 11
    assert len(answer.citations) == structured.total_assets
    assert len({entity.entity_name for entity in structured.matched_entities}) == 5
    assert all(entity.assets for entity in structured.matched_entities)
    assert _structured_asset_identities(answer) == set(_citation_asset_identities(answer))
    assert len(_citation_asset_identities(answer)) == len(
        set(_citation_asset_identities(answer))
    )


@pytest.mark.parametrize(
    "projection_state",
    [
        "missing",
        "malformed",
        "unsupported_schema",
        "tampered",
        "stale_decision_store",
        "stale_store_sync",
        "duplicate_alias",
    ],
)
def test_alias_loader_failure_preserves_governed_structured_caps(
    formal_slack_snapshot,
    projection_state,
):
    _set_projection_state(formal_slack_snapshot["projection_path"], projection_state)

    reply, answer = _slack_query(formal_slack_snapshot, "SHOPLINE Payments")
    structured = answer.generated.structured_result

    assert structured is not None
    # OLD: <= 5 parents and <= 10 assets, both from the Slack shaping cap. NEW: a broken alias
    # projection drops this query out of the alias merge entirely, so the Slack display capacity
    # is what bounds it. The bound moved; what the bound protects did not -- every governance
    # assertion below still holds over the wider result.
    assert 0 < structured.total_entities <= SLACK_SEARCH_PARENT_CAP
    assert 0 < structured.total_assets <= SLACK_SEARCH_ASSET_CAP
    assert len(answer.citations) == structured.total_assets
    assert "累計總GMV" not in reply["text"]
    assert not _has_source(answer, R15_SHEET, 15)
    assert _structured_asset_identities(answer) == set(_citation_asset_identities(answer))


@pytest.mark.parametrize("query", ["SL", "SLPP", "SLP123"])
def test_negative_alias_queries_do_not_resolve_r32(
    formal_slack_snapshot,
    query,
):
    _, answer = _slack_query(formal_slack_snapshot, query)

    assert not _has_source(answer, R32_SHEET, 32)


def test_internal_only_and_non_projected_records_stay_out_of_slack(
    formal_slack_snapshot,
):
    r12_reply, r12_answer = _slack_query(formal_slack_snapshot, "廣生堂")
    r30_reply, r30_answer = _slack_query(formal_slack_snapshot, "莉朵花藝")

    assert not _has_source(r12_answer, R32_SHEET, 12)
    assert "審核中" not in r12_reply["text"]
    assert not _has_source(r30_answer, R32_SHEET, 30)
    assert "莉朵花藝" not in r30_reply["text"]
    assert all(citation.record_type != "pending_metric" for citation in r12_answer.citations)
    assert all(citation.record_type != "pending_metric" for citation in r30_answer.citations)


@pytest.mark.parametrize("external_value", [False, None])
def test_slack_renderer_does_not_default_non_true_external_state_to_public(
    external_value,
):
    citation = Citation.model_construct(
        label="[1]",
        title="Governed source",
        source_path="synthetic:1",
        chunk_id="chunk-1",
        status="published",
        source_type="database",
        record_type="public_metric",
        data_classification="public",
        can_quote_externally=external_value,
        publish_date="2026-07-01",
        source_sheet="Synthetic",
        source_row=1,
        freshness_note="最新日期 2026-07-01",
        allowed_exposure_channels=["press_release"],
    )
    answer = _agentic_answer(citation)

    text = format_slack_reply(answer, max_answer_chars=2500)

    assert "· 不可對外引用" in text
    assert "· 可對外引用" not in text


def _slack_query(runtime, query):
    captured = []

    def recording_ask(*args, **kwargs):
        answer = agent_ask(*args, **kwargs)
        captured.append(answer)
        return answer

    reply = handle_slack_event(
        {
            "text": query,
            "channel": "C-GOVERNANCE-OFFLINE",
            "user": "U-GOVERNANCE-OFFLINE",
            "ts": "200.1",
        },
        config=SlackConfig(allowed_channel_ids=["C-GOVERNANCE-OFFLINE"]),
        ask_fn=recording_ask,
        db_path=runtime["db_path"],
        restricted_customers_path=runtime["restricted_path"],
        llm_config_path=Path("missing-llm-config.json"),
        audit_log_path=runtime["audit_path"],
    )
    return reply, captured[0]


def _formal_metadata(db_path, source_sheet, source_row):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        row = connection.execute(
            """
            SELECT metadata_json
            FROM documents
            WHERE json_extract(metadata_json, '$.source_sheet') = ?
              AND CAST(json_extract(metadata_json, '$.source_row') AS INTEGER) = ?
            """,
            (source_sheet, source_row),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return json.loads(row[0])


def _update_formal_metadata_copy(
    db_path,
    source_sheet,
    source_row,
    *,
    remove_fields=None,
    **updates,
):
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT id, metadata_json
            FROM documents
            WHERE json_extract(metadata_json, '$.source_sheet') = ?
              AND CAST(json_extract(metadata_json, '$.source_row') AS INTEGER) = ?
            """,
            (source_sheet, source_row),
        ).fetchone()
        assert row is not None
        metadata = json.loads(row[1])
        for field in remove_fields or set():
            metadata.pop(field, None)
        metadata.update(updates)
        connection.execute(
            "UPDATE documents SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False), row[0]),
        )
        connection.commit()
    finally:
        connection.close()


def _has_source(answer, source_sheet, source_row):
    return any(
        citation.source_sheet == source_sheet and citation.source_row == source_row
        for citation in answer.citations
    )


def _structured_has_source(answer, source_sheet, source_row):
    structured = answer.generated.structured_result
    if structured is None:
        return False
    return any(
        asset.source_sheet == source_sheet and asset.source_row == source_row
        for entity in structured.matched_entities
        for asset in entity.assets
    )


def _merchant_parent_ids(answer):
    rows = []
    for citation in answer.citations:
        if citation.source_sheet != R32_SHEET or citation.source_row is None:
            continue
        if citation.source_row not in rows:
            rows.append(citation.source_row)
    return rows


def _structured_asset_identities(answer):
    structured = answer.generated.structured_result
    assert structured is not None
    return {
        (asset.source_sheet, asset.source_row, asset.asset_type, asset.title)
        for entity in structured.matched_entities
        for asset in entity.assets
    }


def _citation_asset_identities(answer):
    return [
        (
            citation.source_sheet,
            citation.source_row,
            citation.chunk_id.rsplit(":", 1)[-1],
            citation.title,
        )
        for citation in answer.citations
    ]


def _set_projection_state(path, state):
    if state == "missing":
        path.unlink()
        return
    if state == "malformed":
        path.write_text("{", encoding="utf-8")
        return

    payload = json.loads(path.read_text(encoding="utf-8"))
    if state == "unsupported_schema":
        payload["schema_version"] = 2
    elif state == "tampered":
        payload["aliases"][0]["raw_alias"] = "tampered"
        _write_projection(path, payload, refresh_hash=False)
        return
    elif state == "stale_decision_store":
        payload["authority"]["decision_store_sha256"] = "0" * 64
    elif state == "stale_store_sync":
        payload["authority"]["store_sync_execution_root_hash"] = "0" * 64
    elif state == "duplicate_alias":
        payload["aliases"].append(dict(payload["aliases"][0]))
        payload["aliases"].sort(
            key=lambda row: (
                row["normalized_alias"],
                row["parent_record_id"],
                row["raw_alias"],
            )
        )
    else:
        raise AssertionError(f"unsupported projection state: {state}")
    _write_projection(path, payload, refresh_hash=True)


def _write_projection(path, payload, *, refresh_hash):
    if refresh_hash:
        scope = {key: value for key, value in payload.items() if key != "projection_hash"}
        payload["projection_hash"] = hashlib.sha256(
            json.dumps(
                scope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _agentic_answer(citation):
    generated = GeneratedAnswer(
        question="query",
        answer="answer",
        citations=[citation],
        warnings=[],
        governance_checked=True,
    )
    return AgenticAnswer(
        generated=generated,
        trace=AgentTrace(
            mode="fast_path",
            analysis=QueryAnalysis(
                question_type="simple_lookup",
                needs_agent=False,
                reasons=[],
            ),
            plan=[],
            observations=[],
            reflection=AgentReflection(sufficient=True, notes=[]),
        ),
    )
