import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.missing_parent_resolution_preview import (
    RESOLUTION_OUTPUT_FILENAMES,
    build_resolution_context,
    generate_missing_parent_resolution_preview,
    preview_resolution_search,
    render_resolution_standard,
)


REVIEWED_AT = "2026-07-17T18:30:00+08:00"


def test_parent_decisions_and_partner_handle_rules_are_authoritative(tmp_path):
    paths = _fixture(tmp_path)
    context = _context(paths)

    parents = {row["record_id"]: row for row in context.parent_decisions}
    assert parents["Sheet:r30"]["proposed_review_decision"] == "exclude"
    assert parents["Sheet:r12"]["proposed_review_decision"] == "approve_internal_only"
    assert parents["Sheet:r122"]["proposed_review_decision"] == "approve"
    assert parents["Sheet:r122"]["entity_type"] == "partner"
    assert parents["Sheet:r122"]["merchant_handle_requirement"] == "not_required"
    assert parents["Sheet:r122"]["merchant_handle"] == ""
    assert parents["Sheet:r7"]["entity_type"] == "partner"
    assert parents["Sheet:r7"]["merchant_handle"] == ""
    assert all(row["merchant_handle"] == "" for row in context.parent_decisions if row["entity_type"] == "partner")


def test_asset_eligibility_is_split_from_parent_decision(tmp_path):
    paths = _fixture(tmp_path)
    context = _context(paths)
    assets = {row["asset_id"]: row for row in context.asset_decisions}

    assert assets["Sheet:r12:article"]["proposed_asset_index_eligibility"] == "include"
    assert assets["Sheet:r12:article"]["proposed_asset_search_eligibility"] == "searchable_internal"
    assert assets["Sheet:r12:video"]["proposed_asset_index_eligibility"] == "hold"
    assert assets["Sheet:r12:video"]["proposed_asset_search_eligibility"] == "not_searchable"
    assert assets["Sheet:r12:video"]["will_enter_asset_apply_manifest"] == "false"
    assert assets["Sheet:r30:article"]["proposed_asset_index_eligibility"] == "exclude"
    assert assets["Sheet:r30:article"]["will_enter_asset_apply_manifest"] == "false"


def test_excluded_name_and_handle_return_no_result(tmp_path):
    context = _context(_fixture(tmp_path))

    for query in ("莉朵花藝", "littlegirl"):
        result = preview_resolution_search(query, context)
        assert result["total_entities"] == 0
        assert result["total_assets"] == 0
        assert result["citations"] == []
        assert result["abstained"] is True


def test_internal_article_is_searchable_but_held_video_is_not(tmp_path):
    context = _context(_fixture(tmp_path))

    for query in ("廣生堂", "111gsttest"):
        result = preview_resolution_search(query, context)
        assert [asset["asset_type"] for asset in result["entities"][0]["assets"]] == ["article"]
        assert result["entities"][0]["assets"][0]["external_usage"] == "不可對外引用"
        assert all(asset["asset_type"] != "video" for asset in result["entities"][0]["assets"])


def test_partner_without_handle_is_searchable_and_keeps_distinct_assets(tmp_path):
    context = _context(_fixture(tmp_path))

    package = preview_resolution_search("Package+", context)
    trade_van = preview_resolution_search("關貿網路", context)

    assert package["entities"][0]["entity_type"] == "partner"
    assert package["entities"][0]["merchant_handle"] == ""
    assert {asset["asset_type"] for asset in package["entities"][0]["assets"]} == {
        "article",
        "video",
        "podcast",
    }
    assert [asset["asset_type"] for asset in trade_van["entities"][0]["assets"]] == ["article"]


def test_alias_resolution_is_exact_case_insensitive_and_not_fuzzy(tmp_path):
    context = _context(_fixture(tmp_path))

    for query in ("SLP", "slp", "SHOPLINE Payments", "shopline payments"):
        result = preview_resolution_search(query, context)
        assert result["entities"][0]["entity_name"] == "聊心茶室（SLP 用戶）"
        assert len(result["entities"][0]["assets"]) == 3
    assert preview_resolution_search("SL", context)["total_assets"] == 0
    assert preview_resolution_search("SHOPLINE Payment", context)["total_assets"] == 0


def test_alias_results_still_apply_governance(tmp_path):
    paths = _fixture(tmp_path, restricted_brand="聊心茶室（SLP 用戶）")
    context = _context(paths)

    result = preview_resolution_search("SLP", context)

    assert result["total_assets"] == 0
    assert result["citations"] == []
    assert result["abstain_reason"] == "governance_blocked"


def test_content_tags_come_only_from_eligible_parent(tmp_path):
    context = _context(_fixture(tmp_path))

    package = preview_resolution_search("Package+", context)
    excluded = preview_resolution_search("莉朵花藝", context)
    held = next(row for row in context.asset_decisions if row["asset_id"] == "Sheet:r12:video")

    assert package["entities"][0]["content_tags"] == ["partner-tag"]
    assert excluded["entities"] == []
    assert held["resolved_content_tags"] == "[]"
    assert all(asset["content_tags"] == ["partner-tag"] for asset in package["entities"][0]["assets"])


def test_apply_counts_reconcile_without_changing_identity(tmp_path):
    context = _context(_fixture(tmp_path))

    assert context.counts["original_eligible_asset_count"] == 9
    assert context.counts["original_approved_url_field_count"] == 18
    assert context.counts["final_eligible_asset_count"] == 8
    assert context.counts["final_hold_asset_count"] == 1
    assert context.counts["final_excluded_asset_count"] == 1
    assert context.counts["final_approved_url_field_count"] == 16
    assert context.counts["identity_added_count"] == 0
    assert context.counts["identity_lost_count"] == 0


def test_standard_preview_uses_clickable_title_and_hides_internal_fields(tmp_path):
    context = _context(_fixture(tmp_path))
    text = render_resolution_standard(preview_resolution_search("Package+", context))

    assert "標題：<https://example.com/r122/article|Package Article>" in text
    assert "內容相關標籤：partner-tag" in text
    assert "查看內容" not in text
    assert "canonical_url" not in text
    assert "asset_id" not in text
    assert "MKT 內容產出資料庫_店家 / 夥伴案例 / 對外數據" in text


def test_alias_match_is_visible_first_without_changing_long_result_totals(tmp_path):
    context = _context(_fixture(tmp_path))
    formal = [_formal_entity(index) for index in range(1, 7)]

    result = preview_resolution_search("SHOPLINE Payments", context, formal_entities=formal)
    text = render_resolution_standard(result)

    assert result["total_entities"] == 7
    assert result["total_assets"] == 9
    assert result["entities"][0]["entity_name"] == "聊心茶室（SLP 用戶）"
    assert "目前顯示 5 個品牌、7 筆內容" in text
    assert "目前只顯示前 5 個品牌" in text
    assert "Formal 5" not in text


def test_decision_proposal_uses_proposed_entity_type_column(tmp_path):
    paths = _fixture(tmp_path)
    _generate(paths)

    rows = _read_csv(paths["output"] / "missing_parent_resolution_decisions.csv")

    assert "proposed_entity_type" in rows[0]
    assert "entity_type" not in rows[0]


def test_generation_is_read_only_deterministic_and_invalidates_old_plan(tmp_path):
    paths = _fixture(tmp_path)
    protected = _protected_hashes(paths)

    first = _generate(paths)
    first_outputs = _output_hashes(paths["output"])
    second = _generate(paths)

    assert first == second
    assert first_outputs == _output_hashes(paths["output"])
    assert set(first_outputs) == set(RESOLUTION_OUTPUT_FILENAMES)
    assert _protected_hashes(paths) == protected
    assert first["old_plan_id"] == "asset-plan-07cd12338615c961"
    assert first["old_plan_status"] == "DO NOT CONFIRM"
    assert first["formal_vault_modified"] is False
    assert first["formal_sqlite_modified"] is False
    assert first["production_slack_renderer_modified"] is False


def test_cli_generates_preview_only_reports(tmp_path, capsys, monkeypatch):
    paths = _fixture(tmp_path)
    monkeypatch.setattr(
        "marketing_knowledge_agent.cli.generate_missing_parent_resolution_preview",
        lambda **kwargs: {
            "preview_only": True,
            "decisions_applied": False,
            "sync_executed": False,
        },
    )

    exit_code = main(
        [
            "preview-missing-parent-resolution",
            "--parent-records", str(paths["parents"]),
            "--review-decisions", str(paths["reviews"]),
            "--inventory", str(paths["inventory"]),
            "--apply-preview", str(paths["apply"]),
            "--blocked-preview", str(paths["blocked"]),
            "--restricted-customers", str(paths["restricted"]),
            "--vault", str(paths["vault"]),
            "--db", str(paths["db"]),
            "--production-slack-renderer", str(paths["renderer"]),
            "--output", str(paths["output"]),
            "--reviewed-at", REVIEWED_AT,
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["preview_only"] is True
    assert payload["decisions_applied"] is False
    assert payload["sync_executed"] is False


def _context(paths):
    return build_resolution_context(
        parent_records_path=paths["parents"],
        review_decisions_path=paths["reviews"],
        inventory_path=paths["inventory"],
        apply_preview_path=paths["apply"],
        blocked_preview_path=paths["blocked"],
        restricted_customers_path=paths["restricted"],
        reviewed_at=REVIEWED_AT,
    )


def _generate(paths):
    return generate_missing_parent_resolution_preview(
        parent_records_path=paths["parents"],
        review_decisions_path=paths["reviews"],
        inventory_path=paths["inventory"],
        apply_preview_path=paths["apply"],
        blocked_preview_path=paths["blocked"],
        restricted_customers_path=paths["restricted"],
        vault_path=paths["vault"],
        db_path=paths["db"],
        production_slack_renderer_path=paths["renderer"],
        output_dir=paths["output"],
        reviewed_at=REVIEWED_AT,
        formal_search_fn=lambda query: [],
    )


def _fixture(tmp_path, restricted_brand=""):
    paths = {
        "parents": tmp_path / "merchant_cases.json",
        "reviews": tmp_path / "review_decisions.csv",
        "inventory": tmp_path / "inventory.csv",
        "apply": tmp_path / "apply_preview.csv",
        "blocked": tmp_path / "blocked.csv",
        "restricted": tmp_path / "restricted.json",
        "vault": tmp_path / "vault",
        "db": tmp_path / "index.sqlite",
        "renderer": tmp_path / "slack_interface.py",
        "output": tmp_path / "output",
    }
    parents = [
        _parent(30, "莉朵花藝", "littlegirl", "現有商家", ["flower-tag"], article="Flower Article"),
        _parent(12, "廣生堂", "111gsttest", "現有商家", ["health-tag"], article="Health Article", invalid_video="審核中", quote=False),
        _parent(122, "Package+", "", "合作夥伴", ["partner-tag"], article="Package Article", video="Package Video", podcast="Package Podcast"),
        _parent(32, "聊心茶室（SLP 用戶）", "", "現有商家", ["payment-tag"], article="Tea Article", video="Tea Video", podcast="Tea Podcast"),
        _parent(7, "關貿網路", "", "合作夥伴", ["integration-tag"], article="Trade Article"),
    ]
    paths["parents"].write_text(json.dumps(parents, ensure_ascii=False), encoding="utf-8")
    reviews = [
        _review(30, "莉朵花藝", "littlegirl", "exclude", True, False, False),
        _review(12, "廣生堂", "111gsttest", "needs_update", True, True, False),
        _review(122, "Package+", "", "enrich_metadata", True, True, True),
        _review(32, "聊心茶室（SLP 用戶）", "", "enrich_metadata", True, True, True),
        _review(7, "關貿網路", "", "enrich_metadata", True, True, True),
    ]
    _write_csv(paths["reviews"], reviews)
    inventory = []
    apply_rows = []
    for parent in parents:
        record_id = f"Sheet:r{parent['source_row']}"
        values = {
            "article": parent.get("article_title"),
            "video": parent.get("video_title") or ("" if parent["source_row"] == 12 else None),
            "podcast": parent.get("podcast_title"),
        }
        for asset_type, title in values.items():
            if title is None:
                continue
            asset_id = f"{record_id}:{asset_type}"
            inventory.append(_inventory(parent, asset_id, asset_type, title))
            if parent["source_row"] == 12 and asset_type == "video":
                continue
            for field in ("asset_url", "canonical_url"):
                apply_rows.append(_apply(parent, asset_id, asset_type, title, field))
    _write_csv(paths["inventory"], inventory)
    _write_csv(paths["apply"], apply_rows)
    _write_csv(paths["blocked"], [
        _blocked("Sheet:r12", "Sheet:r12:video", field) for field in ("asset_url", "canonical_url")
    ])
    restricted = []
    if restricted_brand:
        restricted.append({"brand_name": restricted_brand, "record_type": "restricted_customer"})
    paths["restricted"].write_text(json.dumps(restricted, ensure_ascii=False), encoding="utf-8")
    paths["vault"].mkdir()
    (paths["vault"] / "note.md").write_text("formal vault", encoding="utf-8")
    with sqlite3.connect(paths["db"]) as connection:
        connection.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)")
    paths["renderer"].write_text("# production renderer anchor\n", encoding="utf-8")
    return paths


def _parent(row, brand, handle, status, tags, *, article=None, video=None, podcast=None, invalid_video=None, quote=True):
    return {
        "record_type": "merchant_case",
        "source_sheet": "Sheet",
        "source_row": row,
        "brand_name": brand,
        "merchant_handle": handle or None,
        "merchant_status": status,
        "sales_category_lv1": "Category",
        "sales_category_lv2": "Subcategory",
        "content_tags": tags,
        "article_title": article,
        "video_title": video,
        "podcast_title": podcast,
        "news_title": None,
        "invalid_asset_fields": ["影片"] if invalid_video else [],
        "invalid_asset_values": {"影片": invalid_video} if invalid_video else {},
        "can_enter_content_index": row != 30,
        "can_quote_externally": quote,
        "data_classification": "public",
    }


def _review(row, brand, handle, decision, vault, index, quote):
    return {
        "source_sheet": "Sheet", "source_row": row, "record_type": "merchant_case",
        "brand_name": brand, "merchant_handle": handle, "review_decision": decision,
        "can_enter_vault": str(vault).lower(), "can_enter_content_index": str(index).lower(),
        "can_quote_externally": str(quote).lower(), "reviewer": "Old Reviewer",
        "reviewed_at": "2026-07-10", "notes": "fixture",
    }


def _inventory(parent, asset_id, asset_type, title):
    return {
        "record_id": asset_id.rsplit(":", 1)[0], "asset_id": asset_id,
        "record_type": "merchant_case", "brand_name": parent["brand_name"],
        "asset_type": asset_type, "asset_title": title,
        "source_sheet": "Sheet", "source_row": parent["source_row"],
        "invalid_asset_value": "審核中" if asset_id == "Sheet:r12:video" else "",
    }


def _apply(parent, asset_id, asset_type, title, field):
    return {
        "record_id": asset_id.rsplit(":", 1)[0], "asset_id": asset_id,
        "brand_name": parent["brand_name"], "asset_type": asset_type,
        "asset_title": title, "field": field, "current_value": "",
        "proposed_value": f"https://example.com/r{parent['source_row']}/{asset_type}",
        "review_decision": "approve", "reviewer": "James Huang",
        "reviewed_at": "2026-07-17", "provenance": "fixture",
        "eligibility": "ready_for_apply_preview", "governance_status": "eligible",
        "action": "add", "reason": "fixture",
    }


def _blocked(record_id, asset_id, field):
    return {
        "record_id": record_id, "asset_id": asset_id, "brand_name": "廣生堂",
        "asset_type": "video", "asset_title": "", "field": field,
        "proposed_value": "", "review_decision": "approve", "eligibility": "governance_blocked",
        "governance_status": "blocked", "action": "blocked", "reason": "reviewing",
    }


def _formal_entity(index):
    return {
        "entity_type": "merchant",
        "entity_name": f"Formal {index}",
        "merchant_handle": f"formal{index}",
        "sales_category_lv1": "Category",
        "sales_category_lv2": "Subcategory",
        "content_tags": ["payment-tag"],
        "assets": [
            {
                "record_id": f"Formal:r{index}",
                "asset_id": f"Formal:r{index}:article",
                "asset_type": "article",
                "title": f"Formal Story {index}",
                "asset_url": f"https://example.com/formal/{index}",
                "canonical_url": f"https://example.com/formal/{index}",
                "content_tags": ["payment-tag"],
                "external_usage": "可對外引用",
                "can_quote_externally": True,
            }
        ],
    }


def _write_csv(path, rows):
    rows = list(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _protected_hashes(paths):
    return {key: _hash_path(paths[key]) for key in (
        "parents", "reviews", "inventory", "apply", "blocked", "restricted", "vault", "db", "renderer"
    )}


def _output_hashes(path):
    return {child.name: hashlib.sha256(child.read_bytes()).hexdigest() for child in sorted(path.iterdir())}


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(child.relative_to(path).as_posix().encode())
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()
