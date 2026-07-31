from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.production_search_alias_plan_v2_execution import (
    EXPECTED_CONFIRMATION_ID,
    EXPECTED_CONFIRMATION_ROOT_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    ProductionSearchAliasPlanV2ExecutionError,
    _render_projection,
    _render_runtime_files,
    _managed_parent_count,
    _require_exact_authority,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _paths():
    root = _root()
    return {
        "pipeline": root / "src/marketing_knowledge_agent/pipeline.py",
        "confirmation": root / "data/governance/confirmations" / EXPECTED_PLAN_ID,
    }


def test_exact_authority_is_required():
    _require_exact_authority(
        EXPECTED_PLAN_ID, EXPECTED_MANIFEST_HASH,
        EXPECTED_CONFIRMATION_ID, EXPECTED_CONFIRMATION_ROOT_HASH,
    )
    for values in (
        ("wrong", EXPECTED_MANIFEST_HASH, EXPECTED_CONFIRMATION_ID, EXPECTED_CONFIRMATION_ROOT_HASH),
        (EXPECTED_PLAN_ID, "0" * 64, EXPECTED_CONFIRMATION_ID, EXPECTED_CONFIRMATION_ROOT_HASH),
        (EXPECTED_PLAN_ID, EXPECTED_MANIFEST_HASH, "wrong", EXPECTED_CONFIRMATION_ROOT_HASH),
        (EXPECTED_PLAN_ID, EXPECTED_MANIFEST_HASH, EXPECTED_CONFIRMATION_ID, "0" * 64),
    ):
        with pytest.raises(ProductionSearchAliasPlanV2ExecutionError):
            _require_exact_authority(*values)


def test_runtime_payload_scope_and_pipeline_before_checksum():
    paths = _paths()
    paths["pipeline"] = (
        _root()
        / "data/governance/backups"
        / EXPECTED_PLAN_ID
        / "pipeline.py"
    )
    assert hashlib.sha256(paths["pipeline"].read_bytes()).hexdigest() == "01ce71cddd9bb5ab0b4e1f9838e917796c3a13eee8c44389e8b8ceb5d6054fce"
    runtime = _render_runtime_files(paths)
    assert {str(path) for path in runtime} == {
        "src/marketing_knowledge_agent/search_aliases.py",
        "src/marketing_knowledge_agent/pipeline.py",
        "tests/test_production_search_alias_runtime.py",
    }
    assert "slack_interface" not in "\n".join(runtime.values())
    assert "CREATE TABLE" not in "\n".join(runtime.values())
    assert "load_alias_projection" in runtime[next(path for path in runtime if path.name == "pipeline.py")]


def test_projection_is_self_excluding_canonical_and_plan_bound():
    payload_bytes = _render_projection(_paths(), "2026-07-27T15:00:00+08:00")
    assert payload_bytes.endswith(b"\n") and not payload_bytes.endswith(b"\n\n")
    payload = json.loads(payload_bytes)
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    expected = hashlib.sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert payload["projection_hash"] == expected
    assert payload["generated_from_plan_id"] == EXPECTED_PLAN_ID
    assert payload["generated_from_manifest_hash"] == EXPECTED_MANIFEST_HASH
    assert [(row["raw_alias"], row["parent_record_id"]) for row in payload["aliases"]] == [
        ("SHOPLINE Payments", "商家夥伴案例資料庫:r32"),
        ("SLP", "商家夥伴案例資料庫:r32"),
    ]


def test_managed_parent_inventory_uses_frontmatter_contract():
    assert _managed_parent_count(_root() / "obsidian_vault/MKA") == 110
