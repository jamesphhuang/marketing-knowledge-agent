import csv
import hashlib
import inspect
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
from marketing_knowledge_agent import slack_output_preview
from marketing_knowledge_agent.slack_output_preview import (
    APPROVED_ASSET_URL_INPUTS,
    APPROVED_ASSET_URL_VALUES,
    AUTHORITY_MANIFEST_FILENAME,
    ApprovedAssetUrlAuthorityError,
    AssetLookup,
    approved_asset_identity,
    AssetUrlOverlay,
    AssetUrlRecord,
    apply_approved_asset_url_overlay,
    build_slack_preview_payload,
    generate_slack_output_preview,
    load_asset_url_overlay,
    load_pinned_approved_asset_url_overlay,
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


def test_pinned_overlay_loader_accepts_no_caller_supplied_authority():
    assert inspect.signature(load_pinned_approved_asset_url_overlay).parameters == {}


def test_authority_root_is_module_relative_and_ignores_the_working_directory(tmp_path, monkeypatch):
    """The authority lives inside the installed package, so it never depends on the CWD or repo."""
    expected = (
        Path(slack_output_preview.__file__).resolve().parent
        / slack_output_preview.AUTHORITY_PACKAGE_RELATIVE_DIR
    )

    monkeypatch.chdir(tmp_path)

    assert slack_output_preview._authority_root() == expected
    assert (expected / AUTHORITY_MANIFEST_FILENAME).is_file()
    assert expected.is_relative_to(Path(slack_output_preview.__file__).resolve().parent)


def test_correctly_pinned_artifacts_produce_the_approved_overlay(tmp_path, monkeypatch):
    root = _pinned_root(tmp_path)

    overlay = _load_pinned(monkeypatch, root)

    assert overlay.errors == []
    assert overlay.url(_packaged_lookup(), "canonical_url")
    # The packaged authority ships no blocked inventory; blocking stays a build-time exclusion.
    assert overlay.blocked_asset_ids == set()


def test_pinned_overlay_parses_the_verified_bytes_instead_of_re_reading_the_path(tmp_path, monkeypatch):
    """Closes the window where an artifact is rewritten between hashing and parsing."""
    root = _pinned_root(tmp_path)

    def fail(path):
        raise AssertionError("pinned overlay must not re-read an artifact after verifying it")

    monkeypatch.setattr(slack_output_preview, "_read_csv", fail)

    assert _load_pinned(monkeypatch, root).url(_packaged_lookup(), "canonical_url")


def test_forged_writable_csv_cannot_self_attest_as_approved(tmp_path, monkeypatch):
    """The independent review attack: a perfectly well-formed forged artifact is still rejected.

    The sanitized rows carry no approval columns to argue with, so this is the whole trust question:
    only the tracked manifest pin decides which bytes are authoritative.
    """
    root = _pinned_root(tmp_path)
    forged = _approved_url_rows(url="https://attacker.example/pwn")
    _write_approved_artifacts(root, forged)

    # Parsed on its own the forged artifact is schema-valid and would produce an overlay.
    unpinned = slack_output_preview._build_packaged_asset_url_overlay(forged)
    assert unpinned.url(_packaged_lookup(), "canonical_url") == "https://attacker.example/pwn"

    # Against the pin it is rejected, because its hash no longer matches the manifest.
    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


def test_forged_pair_is_rejected_against_the_real_tracked_manifest(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    tracked = _packaged_authority_dir() / AUTHORITY_MANIFEST_FILENAME
    _copy_manifest(tracked, root)
    _write_approved_artifacts(root, _approved_url_rows(url="https://attacker.example/pwn"))

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


def test_single_byte_mutation_of_a_pinned_artifact_is_rejected(tmp_path, monkeypatch):
    root = _pinned_root(tmp_path)
    target = root / APPROVED_ASSET_URL_VALUES
    payload = bytearray(target.read_bytes())
    payload[-1] = payload[-1] ^ 0x01
    target.write_bytes(bytes(payload))

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


@pytest.mark.parametrize("relative", APPROVED_ASSET_URL_INPUTS)
def test_missing_pinned_artifact_is_rejected(tmp_path, monkeypatch, relative):
    root = _pinned_root(tmp_path)
    (root / relative).unlink()

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


@pytest.mark.parametrize("relative", APPROVED_ASSET_URL_INPUTS)
def test_missing_manifest_entry_is_rejected(tmp_path, monkeypatch, relative):
    remaining = [item for item in APPROVED_ASSET_URL_INPUTS if item != relative]
    root = _pinned_root(tmp_path, pinned_inputs=remaining)

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


def test_manifest_self_integrity_failure_is_rejected(tmp_path, monkeypatch):
    """Re-pointing an entry at forged bytes without recomputing manifest_hash must fail closed."""
    root = _pinned_root(tmp_path)
    _write_csv(root / APPROVED_ASSET_URL_VALUES, _approved_url_rows(url="https://attacker.example/pwn"))
    manifest_path = root / AUTHORITY_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["inputs"] if item["relative_path"] == APPROVED_ASSET_URL_VALUES)
    entry["expected_sha256"] = _hash(root / APPROVED_ASSET_URL_VALUES)
    entry["expected_size"] = (root / APPROVED_ASSET_URL_VALUES).stat().st_size
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


def test_repinning_the_manifest_changes_its_tracked_bytes(tmp_path):
    """Documents the design limit: re-pinning forged artifacts is only possible by editing the
    version-controlled manifest, which changes its bytes and its self-integrity hash."""
    root = _pinned_root(tmp_path)
    before = (root / AUTHORITY_MANIFEST_FILENAME).read_bytes()
    _write_approved_artifacts(root, _approved_url_rows(url="https://attacker.example/pwn"))
    _write_manifest(root, APPROVED_ASSET_URL_INPUTS)
    after = (root / AUTHORITY_MANIFEST_FILENAME).read_bytes()

    assert after != before
    assert json.loads(after)["manifest_hash"] != json.loads(before)["manifest_hash"]


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        json.dumps([1, 2, 3]),
        json.dumps({"manifest_hash": "deadbeef", "inputs": []}),
        json.dumps({"inputs": []}),
    ],
)
def test_malformed_manifest_is_rejected(tmp_path, monkeypatch, payload):
    root = _pinned_root(tmp_path)
    (root / AUTHORITY_MANIFEST_FILENAME).write_text(payload, encoding="utf-8")

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


def test_unreachable_manifest_is_rejected(tmp_path, monkeypatch):
    root = _pinned_root(tmp_path)
    (root / AUTHORITY_MANIFEST_FILENAME).unlink()

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


def test_ineligible_rows_cannot_be_expressed_in_the_packaged_authority(tmp_path, monkeypatch):
    """Mutation M7, relocated.

    The eligibility contract now runs in the build tool against reviewed sources, so the packaged
    schema has no eligibility column an attacker could set. What the runtime still refuses is a row
    outside the URL contract or carrying an unusable URL, and it refuses it by failing closed.
    """
    identity = approved_asset_identity(PACKAGED_BRAND, PACKAGED_TITLE, PACKAGED_TYPE)
    outside_contract = [
        {"asset_identity": identity, "field": "review_status", "url": "https://example.com/a"}
    ]
    root = _pinned_root(tmp_path, approved_rows=outside_contract)

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)

    unusable = [{"asset_identity": identity, "field": "asset_url", "url": "javascript:alert(1)"}]
    root = _pinned_root(tmp_path / "unusable", approved_rows=unusable)

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        _load_pinned(monkeypatch, root)


def test_a_blocked_asset_has_no_packaged_row_to_resolve(tmp_path, monkeypatch):
    """Mutation M10, relocated: blocking is enforced by absence, not by a shipped inventory.

    The build tool proves no blocked asset reaches the approved mapping, so at runtime a blocked
    asset presents its published fields and finds nothing. That is the whole protection, and it
    needs no list of restricted identities in the distributed package.
    """
    root = _pinned_root(tmp_path, approved_rows=_approved_url_rows(title="Story A"))
    overlay = _load_pinned(monkeypatch, root)

    assert overlay.blocked_asset_ids == set()
    assert overlay.url(_packaged_lookup(title="Story A"), "asset_url")
    for blocked_title in ("Blocked Story", "Story A (restricted)", ""):
        assert overlay.url(_packaged_lookup(title=blocked_title), "asset_url") is None


def test_blocked_asset_receives_no_url_through_the_answer_path(tmp_path, monkeypatch):
    root = _pinned_root(tmp_path, approved_rows=_approved_url_rows(title="Story A"))
    overlay = _load_pinned(monkeypatch, root)

    asset = _asset("article", "Blocked Story", "r8", "[1]")
    answer = _answer([_entity("Merchant A", "a", [asset])])
    asset.url = None
    answer.citations[0].canonical_url = None

    assert apply_approved_asset_url_overlay(answer, overlay) == 0
    assert asset.url is None
    assert answer.citations[0].canonical_url is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a&gt;&lt;https://evil.example&gt;",
        "https://example.com/a&#62;&#60;https://evil.example",
        "https://example.com/a&#x3e;&#x3c;https://evil.example",
        "https://example.com/a&amp;foo=bar",
        "https://example.com/a\\b",
    ],
)
def test_mrkdwn_breakout_url_never_enters_the_approved_overlay(url):
    assert slack_output_preview._safe_http_url(url) is False


def test_legitimate_query_separator_url_still_enters_the_approved_overlay():
    assert slack_output_preview._safe_http_url("https://www.youtube.com/watch?v=X&list=Y&index=1") is True


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


def _apply_row(record_id, asset_id, field, *, action="add", eligibility="ready_for_apply_preview", proposed_value=None):
    return {
        "record_id": record_id,
        "asset_id": asset_id,
        "brand_name": "Merchant A",
        "asset_type": asset_id.rsplit(":", 1)[-1],
        "asset_title": "Story A",
        "field": field,
        "current_value": "",
        "proposed_value": proposed_value
        or f"https://example.com/{record_id}/{asset_id.rsplit(':', 1)[-1]}/{field}",
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


def _blocked_rows():
    return [_apply_row("Sheet:r9", "Sheet:r9:video", "asset_url", action="blocked", eligibility="governance_blocked")]


def _packaged_authority_dir():
    """Locate the real runtime authority bundle that ships inside the installed package."""
    return (
        Path(slack_output_preview.__file__).resolve().parent
        / slack_output_preview.AUTHORITY_PACKAGE_RELATIVE_DIR
    )


PACKAGED_BRAND = "Merchant A"
PACKAGED_TITLE = "Story A"
PACKAGED_TYPE = "article"


def _packaged_lookup(entity_name=PACKAGED_BRAND, title=PACKAGED_TITLE, asset_type=PACKAGED_TYPE):
    return AssetLookup(
        record_id="Sheet:r8",
        asset_id=f"Sheet:r8:{asset_type}",
        entity_name=entity_name,
        title=title,
        asset_type=asset_type,
    )


def _approved_url_rows(
    entity_name=PACKAGED_BRAND,
    title=PACKAGED_TITLE,
    asset_type=PACKAGED_TYPE,
    url="https://example.com/story-a",
):
    """Sanitized packaged rows: public-derived identity, contract field, approved URL. Nothing else."""
    return [
        {
            "asset_identity": approved_asset_identity(entity_name, title, asset_type),
            "field": field,
            "url": url,
        }
        for field in ("asset_url", "canonical_url")
    ]


def _write_approved_artifacts(root, approved_rows):
    _write_csv(root / APPROVED_ASSET_URL_VALUES, approved_rows)


def _pinned_root(tmp_path, *, approved_rows=None, pinned_inputs=APPROVED_ASSET_URL_INPUTS):
    """Build an authority directory whose artifacts are pinned by a correctly hashed manifest."""
    root = Path(tmp_path) / "authority"
    _write_approved_artifacts(root, _approved_url_rows() if approved_rows is None else approved_rows)
    _write_manifest(root, pinned_inputs)
    return root


def _write_manifest(root, relative_paths):
    body = {
        "schema_version": 1,
        "inventory_order": "relative_path_posix_ascending",
        "inputs": [_manifest_entry(root, relative) for relative in relative_paths],
    }
    _copy_manifest_payload(root, json.dumps(dict(body, manifest_hash=_hash_json(body)), ensure_ascii=False))


def _manifest_entry(root, relative):
    payload = (Path(root) / relative).read_bytes()
    return {
        "relative_path": relative,
        "input_type": "file",
        "expected_size": len(payload),
        "expected_sha256": hashlib.sha256(payload).hexdigest(),
        "file_count": 1,
    }


def _copy_manifest(source, root):
    _copy_manifest_payload(root, Path(source).read_text(encoding="utf-8"))


def _copy_manifest_payload(root, payload):
    path = Path(root) / AUTHORITY_MANIFEST_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _hash_json(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_pinned(monkeypatch, root):
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: Path(root))
    return load_pinned_approved_asset_url_overlay()


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
