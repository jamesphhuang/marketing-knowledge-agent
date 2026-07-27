from __future__ import annotations

import hashlib
import json
from datetime import date

from marketing_knowledge_agent.models import Chunk, DocumentMetadata, SearchResult
from marketing_knowledge_agent.search_aliases import (
    EXPECTED_ALIAS_AUTHORITY,
    EXPECTED_ALIAS_BINDING,
    load_alias_projection,
    merge_rank_and_cap_alias_results,
    normalize_alias_value,
    resolve_exact_alias_parent_ids,
)


def _projection():
    payload = {
        "schema_version": 1,
        "projection_type": "production_search_aliases",
        "authority": dict(EXPECTED_ALIAS_AUTHORITY),
        "normalization_contract": {"version": "alias-normalization-v1", "hash": "b4f05430b26bde6be675ca6d9647044048c752d724ef7c4688afb50d34941bc6"},
        "query_semantics_contract": {"version": "alias-query-semantics-v1", "hash": "b52429126c031079a0034eb125573bc5252d2514eb075237af82d8f79e7bfecc"},
        "aliases": [
            {"raw_alias": "SHOPLINE Payments", "normalized_alias": "shopline payments", "parent_record_id": "商家夥伴案例資料庫:r32", "active": True, "reviewer": "Admin", "reviewed_at": "2026-07-18T00:33:08+08:00", "provenance": "admin_resolution", "authority_reference": "event-a"},
            {"raw_alias": "SLP", "normalized_alias": "slp", "parent_record_id": "商家夥伴案例資料庫:r32", "active": True, "reviewer": "Admin", "reviewed_at": "2026-07-18T00:33:08+08:00", "provenance": "admin_resolution", "authority_reference": "event-b"},
        ],
        **EXPECTED_ALIAS_BINDING,
        "generated_at": "2026-07-27T15:00:00+08:00",
        "runtime_compatibility_version": "production-search-alias-runtime-v1",
        "projection_hash_algorithm": "sha256",
        "projection_hash_scope": "canonical_json_utf8_sorted_keys_compact_without_projection_hash_no_trailing_newline",
        "projection_hash": "",
    }
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    payload["projection_hash"] = hashlib.sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _result(row, document, score=0.0):
    metadata = DocumentMetadata(title=document, source_type="database", record_type="merchant_case", publish_date=date(2026, 1, 1), source_sheet="商家夥伴案例資料庫", source_row=row, source_path=f"record-r{row}.md")
    chunk = Chunk(id=f"chunk-{document}", document_id=f"doc-{document}", chunk_index=0, text=document, metadata=metadata)
    return SearchResult(chunk=chunk, score=score)


def test_loader_failure_contract(tmp_path):
    target = tmp_path / "aliases.json"
    assert load_alias_projection(target)[0] is None
    target.write_text("{", encoding="utf-8")
    assert load_alias_projection(target)[0] is None
    for mutation in ("schema", "hash", "authority", "duplicate"):
        payload = _projection()
        if mutation == "schema":
            payload["schema_version"] = 2
        elif mutation == "hash":
            payload["aliases"][0]["raw_alias"] = "tampered"
        elif mutation == "authority":
            payload["authority"]["decision_store_sha256"] = "0" * 64
        else:
            payload["aliases"].append(dict(payload["aliases"][0]))
        _write(target, payload)
        assert load_alias_projection(target)[0] is None


def test_exact_alias_resolution():
    projection = _projection()
    for query in ("SLP", "slp", "SlP", "  SLP  ", "SHOPLINE Payments", "shopline payments"):
        assert resolve_exact_alias_parent_ids(query, None, projection) == ["商家夥伴案例資料庫:r32"]
    for query in ("SL", "SLPP", "SLP123", "SHOPLINE Payment", "SHOPLINE", "Payments", "請提供 SLP 的資料"):
        assert resolve_exact_alias_parent_ids(query, None, projection) == []
    assert normalize_alias_value("  ＳＬＰ  ") == "slp"


def test_alias_merge_ranking_governance_and_caps():
    alias = [_result(32, "r32", 0.0), _result(32, "r32", 0.0)]
    organic = [_result(row, f"r{row}", 100.0 - row) for row in range(1, 8)] + [_result(32, "r32", 200.0)]
    merged = merge_rank_and_cap_alias_results(alias, organic, parent_cap=5, asset_cap=10)
    parents = [item.chunk.metadata.source_row for item in merged]
    assert parents[0] == 32
    assert len(set(parents)) == len(parents) == 5
    assert len({item.chunk.document_id for item in merged}) == len(merged)
