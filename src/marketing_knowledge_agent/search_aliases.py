from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional


class AliasProjectionError(ValueError):
    pass


DEFAULT_ALIAS_PROJECTION_PATH = Path(".mka/search_alias_projection.json")
EXPECTED_ALIAS_AUTHORITY = {
    "decision_store_sha256": "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9",
    "store_sync_execution_id": "store-data-sync-execution-01bbb9e3c641a6b4",
    "store_sync_execution_root_hash": "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30",
}
EXPECTED_ALIAS_BINDING = {
    "generated_from_plan_id": "production-search-alias-plan-v2-668c2856f39124ae",
    "generated_from_manifest_hash": "58b6a1c422f7d6d68ce5ea9f960d9afbdce67c9e446c4b09851b60e6a5c613e5",
}
EXPECTED_NORMALIZATION_HASH = "b4f05430b26bde6be675ca6d9647044048c752d724ef7c4688afb50d34941bc6"
EXPECTED_QUERY_SEMANTICS_HASH = "b52429126c031079a0034eb125573bc5252d2514eb075237af82d8f79e7bfecc"
PROJECTION_FIELDS = {
    "schema_version", "projection_type", "authority", "normalization_contract",
    "query_semantics_contract", "aliases", "generated_from_plan_id",
    "generated_from_manifest_hash", "generated_at", "runtime_compatibility_version",
    "projection_hash_algorithm", "projection_hash_scope", "projection_hash",
}
AUTHORITY_FIELDS = {
    "decision_store_sha256", "store_sync_execution_id", "store_sync_execution_root_hash",
}
CONTRACT_FIELDS = {"version", "hash"}
ALIAS_FIELDS = {
    "raw_alias", "normalized_alias", "parent_record_id", "active", "reviewer",
    "reviewed_at", "provenance", "authority_reference",
}


def normalize_alias_value(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AliasProjectionError(f"duplicate key: {key}")
        result[key] = value
    return result


def validate_projection_payload(
    payload: object,
    expected_authority: Mapping[str, str],
    expected_binding: Mapping[str, str],
) -> dict:
    if not isinstance(payload, dict) or set(payload) != PROJECTION_FIELDS:
        raise AliasProjectionError("missing or unknown projection field")
    if payload.get("schema_version") != 1:
        raise AliasProjectionError("unsupported schema")
    if payload.get("projection_type") != "production_search_aliases":
        raise AliasProjectionError("wrong projection type")
    if payload.get("runtime_compatibility_version") != "production-search-alias-runtime-v1":
        raise AliasProjectionError("unsupported runtime compatibility")
    if payload.get("projection_hash_algorithm") != "sha256":
        raise AliasProjectionError("unsupported projection hash algorithm")
    if payload.get("projection_hash_scope") != "canonical_json_utf8_sorted_keys_compact_without_projection_hash_no_trailing_newline":
        raise AliasProjectionError("unsupported projection hash scope")
    if not isinstance(payload.get("authority"), dict) or set(payload["authority"]) != AUTHORITY_FIELDS:
        raise AliasProjectionError("invalid authority object")
    for key in ("normalization_contract", "query_semantics_contract"):
        if not isinstance(payload.get(key), dict) or set(payload[key]) != CONTRACT_FIELDS:
            raise AliasProjectionError(f"invalid {key}")
    if payload["normalization_contract"] != {
        "version": "alias-normalization-v1", "hash": EXPECTED_NORMALIZATION_HASH,
    }:
        raise AliasProjectionError("normalization contract mismatch")
    if payload["query_semantics_contract"] != {
        "version": "alias-query-semantics-v1", "hash": EXPECTED_QUERY_SEMANTICS_HASH,
    }:
        raise AliasProjectionError("query semantics contract mismatch")
    aliases = payload.get("aliases")
    if not isinstance(aliases, list) or not aliases:
        raise AliasProjectionError("aliases must be a non-empty list")
    for row in aliases:
        if not isinstance(row, dict) or set(row) != ALIAS_FIELDS:
            raise AliasProjectionError("missing or unknown Alias field")
        if not isinstance(row["active"], bool):
            raise AliasProjectionError("Alias active must be boolean")
        if any(not isinstance(row[key], str) or not row[key] for key in ALIAS_FIELDS - {"active"}):
            raise AliasProjectionError("Alias string field is empty")
        if normalize_alias_value(row["raw_alias"]) != row["normalized_alias"]:
            raise AliasProjectionError("Alias normalization mismatch")
        try:
            reviewed_at = datetime.fromisoformat(row["reviewed_at"])
        except ValueError as exc:
            raise AliasProjectionError("invalid Alias reviewed_at") from exc
        if reviewed_at.tzinfo is None:
            raise AliasProjectionError("Alias reviewed_at requires timezone")
    ordered = sorted(
        aliases,
        key=lambda row: (row["normalized_alias"], row["parent_record_id"], row["raw_alias"]),
    )
    if aliases != ordered:
        raise AliasProjectionError("Alias ordering mismatch")
    normalized = [row["normalized_alias"] for row in aliases if row["active"]]
    if len(normalized) != len(set(normalized)):
        raise AliasProjectionError("duplicate normalized Alias")
    if dict(payload["authority"]) != dict(expected_authority):
        raise AliasProjectionError("stale authority binding")
    binding = {
        "generated_from_plan_id": payload["generated_from_plan_id"],
        "generated_from_manifest_hash": payload["generated_from_manifest_hash"],
    }
    if binding != dict(expected_binding):
        raise AliasProjectionError("stale Plan binding")
    try:
        generated_at = datetime.fromisoformat(payload["generated_at"])
    except (TypeError, ValueError) as exc:
        raise AliasProjectionError("invalid generated_at") from exc
    if generated_at.tzinfo is None:
        raise AliasProjectionError("generated_at requires timezone")
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    expected_hash = hashlib.sha256(_canonical_bytes(scope)).hexdigest()
    if payload.get("projection_hash") != expected_hash:
        raise AliasProjectionError("projection hash mismatch")
    return payload


def load_alias_projection(
    path: Path = DEFAULT_ALIAS_PROJECTION_PATH,
    expected_authority: Optional[Mapping[str, str]] = None,
    expected_binding: Optional[Mapping[str, str]] = None,
):
    path = Path(path)
    if not path.exists():
        return None, "alias_projection_missing"
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
        validated = validate_projection_payload(
            payload,
            expected_authority or EXPECTED_ALIAS_AUTHORITY,
            expected_binding or EXPECTED_ALIAS_BINDING,
        )
        return validated, "alias_projection_loaded"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AliasProjectionError) as exc:
        return None, f"alias_projection_disabled:{type(exc).__name__}"


def resolve_exact_alias_parent_ids(query, query_plan, projection):
    if projection is None:
        return []
    values = [query]
    entities = getattr(query_plan, "resolved_entities", []) if query_plan is not None else []
    ambiguity = getattr(query_plan, "ambiguity_flags", []) if query_plan is not None else []
    if len(entities) == 1 and not ambiguity and getattr(entities[0], "confidence", 0) == 1.0:
        canonical_name = getattr(entities[0], "canonical_name", None)
        if canonical_name:
            values.insert(0, canonical_name)
    lookup = {normalize_alias_value(value) for value in values if value}
    owners = {
        row["parent_record_id"]
        for row in projection["aliases"]
        if row["active"] and row["normalized_alias"] in lookup
    }
    return sorted(owners)


def alias_results_for_parent_ids(db_path, parent_ids, filters, query_plan):
    from .indexing import SQLiteIndex
    from .models import SearchResult
    from .retrieval import matches_filters

    del query_plan
    owners = set(parent_ids)
    results = []
    seen = set()
    for indexed in SQLiteIndex(Path(db_path)).load_chunks():
        metadata = indexed.chunk.metadata
        record_id = (
            f"{metadata.source_sheet}:r{int(metadata.source_row)}"
            if metadata.source_sheet and metadata.source_row is not None
            else ""
        )
        identity = (indexed.chunk.document_id, indexed.chunk.id)
        if (
            record_id in owners
            and identity not in seen
            and metadata.can_enter_content_index
            and metadata.record_type not in {"restricted_customer", "pending_metric", "handle_mapping"}
            and matches_filters(metadata, filters)
        ):
            seen.add(identity)
            results.append(SearchResult(chunk=indexed.chunk, score=0.0))
    return results


def merge_rank_and_cap_alias_results(
    alias_results, organic_results, parent_cap=5, asset_cap=10
):
    alias_parents = {_parent_identity(result) for result in alias_results}
    merged = {}
    for result in list(alias_results) + list(organic_results):
        merged.setdefault(result.chunk.document_id, result)

    def key(result):
        parent = _parent_identity(result)
        alias_tier = 0 if parent in alias_parents else 1
        score = result.rerank_score or result.score or 0.0
        return (alias_tier, -float(score), parent, result.chunk.document_id, result.chunk.id)

    ordered = sorted(merged.values(), key=key)
    visible_parents, output = set(), []
    for result in ordered:
        parent = _parent_identity(result)
        if parent not in visible_parents and len(visible_parents) >= parent_cap:
            continue
        visible_parents.add(parent)
        output.append(result)
        if len(output) >= asset_cap:
            break
    return output


def alias_merge_candidate_count(alias_results, organic_results) -> int:
    """How many distinct candidates the alias merge is asked to admit, before any cap applies.

    Deduplication by ``document_id`` is the merge's own first step, so counting it here exactly
    the same way is what makes ``len(admitted) < candidate_count`` a statement about the caps
    rather than an accident of duplicated input. The merge drops a candidate for only two
    reasons -- the parent cap refusing a new parent, and the asset cap ending the loop -- so that
    comparison is precisely "a retrieval cap bound on this query".
    """
    return len({result.chunk.document_id for result in list(alias_results) + list(organic_results)})


def _parent_identity(result) -> str:
    metadata = result.chunk.metadata
    if metadata.source_sheet and metadata.source_row is not None:
        return f"{metadata.source_sheet}:r{int(metadata.source_row)}"
    return result.chunk.document_id
