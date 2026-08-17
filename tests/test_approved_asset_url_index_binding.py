"""P2: a stale authority may omit links, but must never attach another asset's URL.

The packaged join identity is a pure function of published index content (entity name, title, asset
type). That makes it safe to distribute, but it also means a static authority read against a drifted
index can resolve the WRONG asset: when one asset's indexed title becomes another already-approved
asset's title, it computes that asset's identity and inherits its link. The reproduction was
簡單 JAN DAN, which legitimately has two approved articles across two source records.

The fix binds the authority to the exact index surface it was built against. These tests drive the
real packaged authority against real copies of the real content index, mutating only the index.
"""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from marketing_knowledge_agent import slack_output_preview
from marketing_knowledge_agent.models import (
    Citation,
    GeneratedAnswer,
    StructuredAsset,
    StructuredEntity,
    StructuredRetrievalResult,
)
from marketing_knowledge_agent.slack_interface import (
    APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE,
    SlackConfig,
    handle_slack_event,
)
from marketing_knowledge_agent.slack_output_preview import (
    ApprovedAssetUrlIndexBindingError,
    AssetLookup,
    apply_approved_asset_url_overlay,
    approved_asset_identity,
    compute_index_binding_digest,
    index_asset_surface,
    load_index_bound_approved_asset_url_overlay,
    load_pinned_approved_asset_url_overlay,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LIVE_INDEX = REPOSITORY_ROOT / ".mka/content_index.sqlite"
APPLY_PREVIEW = REPOSITORY_ROOT / "reports/asset_metadata_apply_preview/asset_apply_preview.csv"
SHEET = "商家夥伴案例資料庫"
JAN_DAN = "簡單 JAN DAN"


# --------------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------------


@pytest.fixture
def index_copy(tmp_path):
    """A writable copy of the real content index; the authority is left exactly as packaged."""
    if not LIVE_INDEX.is_file():
        pytest.skip("content index is not present in this checkout")
    destination = tmp_path / "content_index.sqlite"
    shutil.copyfile(LIVE_INDEX, destination)
    return destination


def _jan_dan_articles():
    """The two approved 簡單 JAN DAN articles: (record_id, title, url) for A and B."""
    if not APPLY_PREVIEW.is_file():
        pytest.skip("local governance source is not present in this checkout")
    found = {}
    for row in csv.DictReader(APPLY_PREVIEW.read_bytes().decode("utf-8-sig").splitlines()):
        if (
            row["brand_name"].strip() == JAN_DAN
            and row["field"].strip() == "asset_url"
            and row["asset_type"].strip() == "article"
        ):
            found[row["record_id"].strip()] = (row["asset_title"].strip(), row["proposed_value"].strip())
    assert len(found) == 2, "the regression needs two same-brand same-type approved assets"
    a_record, b_record = f"{SHEET}:r38", f"{SHEET}:r68"
    assert a_record in found and b_record in found
    return (a_record, *found[a_record]), (b_record, *found[b_record])


def _mutate_asset(db_path, source_row, **fields):
    """Rewrite one indexed document's metadata, exactly as a re-index with new content would."""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT id, metadata_json FROM documents").fetchall()
        for document_id, metadata_json in rows:
            metadata = json.loads(metadata_json)
            if metadata.get("source_sheet") == SHEET and metadata.get("source_row") == source_row:
                for key, value in fields.items():
                    if value is None:
                        metadata.pop(key, None)
                    else:
                        metadata[key] = value
                connection.execute(
                    "UPDATE documents SET metadata_json = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), document_id),
                )
                connection.commit()
                return
        raise AssertionError(f"no indexed document for {SHEET} row {source_row}")
    finally:
        connection.close()


def _url(overlay, record_id, entity_name, title, asset_type="article"):
    lookup = AssetLookup(
        record_id=record_id,
        asset_id=f"{record_id}:{asset_type}",
        entity_name=entity_name,
        title=title,
        asset_type=asset_type,
    )
    return overlay.url(lookup, "asset_url")


# --------------------------------------------------------------------------------------------
# The exact reproduction.
# --------------------------------------------------------------------------------------------


def test_stale_authority_never_attaches_another_assets_url(index_copy):
    """THE regression: A's indexed title becomes B's approved title, authority not rebuilt.

    Expected A.url is None -- not URL B, and not URL A.
    """
    (a_record, a_title, a_url), (b_record, b_title, b_url) = _jan_dan_articles()
    assert a_url != b_url

    # Built against the original index state: both assets resolve their own URL.
    overlay = load_index_bound_approved_asset_url_overlay(index_copy)
    assert _url(overlay, a_record, JAN_DAN, a_title) == a_url
    assert _url(overlay, b_record, JAN_DAN, b_title) == b_url

    # Mutate ONLY the runtime/index-facing title of A to B's title. No authority rebuild.
    _mutate_asset(index_copy, 38, article_title=b_title)

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)

    # And through the Slack path, which is what actually renders: no URL at all.
    assets, citations, answer = _structured_answer(a_record, JAN_DAN, b_title)
    reply = handle_slack_event(
        {"text": JAN_DAN, "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        db_path=index_copy,
        audit_log_path=index_copy.parent / "audit.csv",
    )
    assert assets[0].url is None
    assert assets[0].url != b_url
    assert assets[0].url != a_url
    assert citations[0].canonical_url is None
    assert b_url not in reply["text"] and a_url not in reply["text"]


def test_the_unbound_loader_is_what_used_to_attach_the_wrong_url(index_copy):
    """Discriminatory control: without the binding this exact input resolves B's URL.

    If this ever stops resolving B's URL the regression test above has become vacuous.
    """
    (a_record, _a_title, _a_url), (_b_record, b_title, b_url) = _jan_dan_articles()

    unbound = load_pinned_approved_asset_url_overlay()

    assert _url(unbound, a_record, JAN_DAN, b_title) == b_url


# --------------------------------------------------------------------------------------------
# WAL: the reason there is no verification cache.
# --------------------------------------------------------------------------------------------


def _wal_writer(db_path):
    """Open a writer that keeps the database in WAL mode, and materialise the -wal/-shm sidecars.

    The touch write matters: a read-only connection can only follow a WAL database once -shm
    exists, and without it the binding read fails closed instead of observing the new content.
    """
    writer = sqlite3.connect(db_path)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    writer.execute("UPDATE documents SET metadata_json = metadata_json WHERE rowid = 1")
    writer.commit()
    return writer


def _wal_commit_title(writer, source_row, title):
    document_id, metadata_json = writer.execute(
        "SELECT id, metadata_json FROM documents"
        " WHERE json_extract(metadata_json, '$.source_row') = ?"
        "   AND json_extract(metadata_json, '$.source_sheet') = ?",
        (source_row, SHEET),
    ).fetchone()
    metadata = json.loads(metadata_json)
    metadata["article_title"] = title
    writer.execute(
        "UPDATE documents SET metadata_json = ? WHERE id = ?",
        (json.dumps(metadata, ensure_ascii=False), document_id),
    )
    writer.commit()
    return document_id


def _main_db_fingerprint(db_path):
    """The stat identity the removed cache was keyed on."""
    status = Path(db_path).stat()
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def test_wal_commit_changes_effective_content_while_the_main_file_stat_stays_put(index_copy):
    """The premise of the removed cache, disproved.

    This is what made a stat-keyed memo unsound: the writer appends the commit to -wal, so the
    main .sqlite file's size and mtime do not move, yet a fresh reader sees the new content.
    """
    (_a, _at, _au), (_b, b_title, _bu) = _jan_dan_articles()
    writer = _wal_writer(index_copy)
    try:
        before = _main_db_fingerprint(index_copy)
        document_id = _wal_commit_title(writer, 38, b_title)
        after = _main_db_fingerprint(index_copy)

        assert before == after, "the stat fingerprint the cache keyed on did not move"
        assert Path(f"{index_copy}-wal").is_file()

        reader = sqlite3.connect(f"file:{index_copy}?mode=ro", uri=True)
        try:
            stored = reader.execute(
                "SELECT metadata_json FROM documents WHERE id = ?", (document_id,)
            ).fetchone()[0]
        finally:
            reader.close()
        assert json.loads(stored)["article_title"] == b_title, "effective content did change"
    finally:
        writer.close()


def test_jan_dan_wal_regression_attaches_no_url_in_the_same_process(index_copy):
    """The blocking P1, as an integration test.

    Request 1 verifies and would have populated the removed memo; the drift then lands through WAL
    without moving the main file's stat. Request 2, in the same process against the same authority,
    must re-evaluate the binding and refuse. Under 6814009 it served the memo and attached URL B.
    """
    (a_record, a_title, a_url), (_b_record, b_title, b_url) = _jan_dan_articles()
    writer = _wal_writer(index_copy)
    try:
        # Request 1: bound index, links available.
        first = load_index_bound_approved_asset_url_overlay(index_copy)
        assert _url(first, a_record, JAN_DAN, a_title) == a_url
        fingerprint_before = _main_db_fingerprint(index_copy)

        # Effective content drifts through WAL; the main file's stat does not move.
        _wal_commit_title(writer, 38, b_title)
        assert _main_db_fingerprint(index_copy) == fingerprint_before

        # Request 2: same process, same application instance, same authority.
        with pytest.raises(ApprovedAssetUrlIndexBindingError):
            load_index_bound_approved_asset_url_overlay(index_copy)

        # And through the Slack path: r38 gets no URL, neither its own nor r68's.
        assets, citations, answer = _structured_answer(a_record, JAN_DAN, b_title)
        reply = handle_slack_event(
            {"text": JAN_DAN, "channel": "C123", "user": "U123", "ts": "11"},
            config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
            ask_fn=lambda *args, **kwargs: answer,
            db_path=index_copy,
            audit_log_path=index_copy.parent / "wal-audit.csv",
        )
        assert assets[0].url is None
        assert assets[0].url != b_url
        assert assets[0].url != a_url
        assert citations[0].canonical_url is None
        assert b_url not in reply["text"] and a_url not in reply["text"]
    finally:
        writer.close()


def test_repeated_requests_in_one_process_follow_the_effective_database(index_copy):
    """Request 1 succeeds, the effective database changes, request 2 refuses -- no fresh process."""
    (a_record, a_title, a_url), (_b_record, b_title, _b_url) = _jan_dan_articles()
    writer = _wal_writer(index_copy)
    try:
        assert _url(load_index_bound_approved_asset_url_overlay(index_copy), a_record, JAN_DAN, a_title) == a_url

        _wal_commit_title(writer, 38, b_title)

        with pytest.raises(ApprovedAssetUrlIndexBindingError):
            load_index_bound_approved_asset_url_overlay(index_copy)

        # Restoring the effective content restores the binding, in the same process.
        _wal_commit_title(writer, 38, a_title)
        restored = load_index_bound_approved_asset_url_overlay(index_copy)
        assert _url(restored, a_record, JAN_DAN, a_title) == a_url
    finally:
        writer.close()


def test_delete_mode_control_succeeds_when_bound_and_fails_closed_after_a_change(index_copy):
    """journal_mode=DELETE control: ordinary content change must still fail closed."""
    (a_record, a_title, a_url), (_b_record, b_title, _b_url) = _jan_dan_articles()
    writer = sqlite3.connect(index_copy)
    try:
        assert writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() == "delete"
    finally:
        writer.close()

    bound = load_index_bound_approved_asset_url_overlay(index_copy)
    assert _url(bound, a_record, JAN_DAN, a_title) == a_url
    assert not Path(f"{index_copy}-wal").exists()

    _mutate_asset(index_copy, 38, article_title=b_title)

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)


# --------------------------------------------------------------------------------------------
# The other stale cases.
# --------------------------------------------------------------------------------------------


def test_1_title_drifts_to_an_unrelated_new_title(index_copy):
    _mutate_asset(index_copy, 38, article_title="完全不相關的新標題 2026")

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)


def test_3_entity_name_drift_invalidates_the_binding(index_copy):
    _mutate_asset(index_copy, 38, brand_name="改名後的品牌")

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)


def test_4_asset_type_drift_resolves_no_cross_type_url(index_copy):
    """The article title moves onto the video field: neither may inherit the other's URL."""
    (a_record, a_title, a_url), _b = _jan_dan_articles()
    _mutate_asset(index_copy, 38, article_title=None, video_title=a_title)

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)

    # Even unbound, the approved article URL never resolves under the video type.
    unbound = load_pinned_approved_asset_url_overlay()
    assert _url(unbound, a_record, JAN_DAN, a_title, "video") is None
    assert _url(unbound, a_record, JAN_DAN, a_title, "article") == a_url


def test_5_a_disappearing_asset_is_harmless(index_copy):
    """Removal fails enrichment closed; it must never hand its URL to a surviving asset."""
    (a_record, a_title, a_url), (b_record, b_title, b_url) = _jan_dan_articles()
    _mutate_asset(index_copy, 38, article_title=None)

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)

    # B never acquires A's URL under any identity it can present.
    unbound = load_pinned_approved_asset_url_overlay()
    assert _url(unbound, b_record, JAN_DAN, b_title) == b_url
    assert _url(unbound, b_record, JAN_DAN, b_title) != a_url


def test_6_a_newly_indexed_asset_matching_an_approved_triple_invalidates_the_binding(index_copy):
    """A different record starts presenting an already-approved public triple."""
    (_a_record, _a_title, _a_url), (_b_record, b_title, _b_url) = _jan_dan_articles()
    # r10 is a different merchant record; give it JAN DAN's approved identity.
    _mutate_asset(index_copy, 10, brand_name=JAN_DAN, article_title=b_title)

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)


def test_7_an_unchanged_index_keeps_every_accepted_url_working(index_copy):
    overlay = load_index_bound_approved_asset_url_overlay(index_copy)

    assert len(overlay.values) == 412
    assert overlay.errors == []
    assert (
        _url(overlay, f"{SHEET}:r8", "三風製麵", _title_for(8, "article_title"))
        == "https://blog.shopline.tw/merchant-showcase-shanfeng/"
    )
    assert (
        _url(overlay, f"{SHEET}:r8", "三風製麵", _title_for(8, "video_title"), "video")
        == "https://www.youtube.com/watch?v=WIMy_AFA0pE"
    )


def test_r30_absent_approved_asset_stays_out_of_the_surface_and_is_never_inherited(index_copy):
    """An approved asset absent from the index must be harmless, not a source of inherited URLs."""
    overlay = load_index_bound_approved_asset_url_overlay(index_copy)
    approved = {key[0] for key in overlay.values}
    surface = index_asset_surface(index_copy)
    indexed = {approved_asset_identity(entity, title, kind) for _record, entity, title, kind in surface}

    absent = approved - indexed
    assert len(absent) == 1, "the r30 case is the one absent approved asset"

    # It contributes nothing to the binding, and no indexed asset can resolve it.
    digest_all, bound = compute_index_binding_digest(index_copy, approved)
    digest_reachable, bound_reachable = compute_index_binding_digest(index_copy, approved & indexed)
    assert digest_all == digest_reachable
    assert bound == bound_reachable == len(indexed)


# --------------------------------------------------------------------------------------------
# Normal multiplicity must keep working.
# --------------------------------------------------------------------------------------------


def test_a_merchant_may_keep_multiple_articles_and_videos(index_copy):
    """簡單 JAN DAN has two approved articles; both resolve, each to its own URL."""
    (a_record, a_title, a_url), (b_record, b_title, b_url) = _jan_dan_articles()

    overlay = load_index_bound_approved_asset_url_overlay(index_copy)

    assert _url(overlay, a_record, JAN_DAN, a_title) == a_url
    assert _url(overlay, b_record, JAN_DAN, b_title) == b_url
    assert a_url != b_url

    # And multiplicity is not rare enough to be a special case.
    surface = index_asset_surface(index_copy)
    by_entity_type = {}
    for _record, entity, _title, asset_type in surface:
        by_entity_type.setdefault((entity, asset_type), 0)
        by_entity_type[(entity, asset_type)] += 1
    assert any(count > 1 for count in by_entity_type.values())


# --------------------------------------------------------------------------------------------
# Binding mechanics, cost and privacy.
# --------------------------------------------------------------------------------------------


def test_every_enrichment_attempt_reverifies_the_binding(index_copy):
    """There must be no previously-successful verification that lets a later request skip the check.

    The memo this replaces was unsound: a stat fingerprint of the main database file does not move
    when a WAL commit lands, so a "verified" answer outlived the content it stood for.
    """
    calls = []
    original = slack_output_preview.compute_index_binding_digest

    def counting(db_path, approved):
        calls.append(str(db_path))
        return original(db_path, approved)

    slack_output_preview.compute_index_binding_digest = counting
    try:
        for _ in range(5):
            load_index_bound_approved_asset_url_overlay(index_copy)
        assert len(calls) == 5, "every attempt must recompute the binding from the live index"
    finally:
        slack_output_preview.compute_index_binding_digest = original


def test_no_binding_cache_state_survives_in_the_module():
    """Guards against a cache being reintroduced without a soundness argument."""
    for attribute in dir(slack_output_preview):
        lowered = attribute.lower()
        assert "memo" not in lowered, attribute
        assert "fingerprint" not in lowered, attribute
    source = Path(slack_output_preview.__file__).read_text(encoding="utf-8")
    assert "st_mtime_ns" not in source
    assert "_INDEX_BINDING_MEMO" not in source


def test_binding_digest_is_order_independent_and_deterministic(index_copy):
    overlay = load_pinned_approved_asset_url_overlay()
    approved = {key[0] for key in overlay.values}

    first, count = compute_index_binding_digest(index_copy, approved)
    second, again = compute_index_binding_digest(index_copy, approved)

    assert first == second and count == again
    assert len(first) == 64


def test_manifest_carries_one_aggregate_digest_and_no_per_record_hash():
    """DG-01 must stay closed: the binding adds one digest, not a per-row inventory."""
    manifest = json.loads(
        (
            Path(slack_output_preview.__file__).resolve().parent
            / slack_output_preview.AUTHORITY_PACKAGE_RELATIVE_DIR
            / slack_output_preview.AUTHORITY_MANIFEST_FILENAME
        ).read_text(encoding="utf-8")
    )
    binding = manifest["index_binding"]

    assert isinstance(binding["digest"], str) and len(binding["digest"]) == 64
    # Exactly one hash-shaped value in the whole binding block.
    hashes = [
        value
        for value in json.dumps(binding).split('"')
        if len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    ]
    assert len(hashes) == 1
    assert SHEET not in json.dumps(binding, ensure_ascii=False)


def test_a_single_source_coordinate_is_not_recoverable_from_the_aggregate_digest(index_copy):
    """The DG-01 attack replayed: per-row hashes fell to enumeration; one aggregate digest does not.

    Enumerating the whole coordinate domain cannot confirm any individual record, because the digest
    commits to every record's coordinate and free-text title simultaneously.
    """
    overlay = load_pinned_approved_asset_url_overlay()
    approved = {key[0] for key in overlay.values}
    digest, _bound = compute_index_binding_digest(index_copy, approved)
    surface = index_asset_surface(index_copy)

    # An attacker holding the digest and guessing one record's coordinate learns nothing: a digest
    # over any strict subset or single row never equals the published one.
    import hashlib

    for record_id, entity, title, asset_type in surface[:50]:
        line = ":".join(
            slack_output_preview._identity_component(part)
            for part in (record_id, entity, title, asset_type)
        )
        assert hashlib.sha256(line.encode("utf-8")).hexdigest() != digest
        assert hashlib.sha256(record_id.encode("utf-8")).hexdigest() != digest

    # And dropping even one row from the full surface changes the digest completely, so the digest
    # cannot be matched without already knowing every row.
    partial, _ = compute_index_binding_digest(
        index_copy, approved - {approved_asset_identity(surface[0][1], surface[0][2], surface[0][3])}
    )
    assert partial != digest


def test_feature_off_performs_no_authority_or_index_binding_read(monkeypatch, tmp_path, index_copy):
    reads = []
    original_surface = slack_output_preview.index_asset_surface
    original_load = slack_output_preview.load_index_bound_approved_asset_url_overlay

    monkeypatch.setattr(
        slack_output_preview,
        "index_asset_surface",
        lambda db: (reads.append("surface"), original_surface(db))[1],
    )
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface.load_index_bound_approved_asset_url_overlay",
        lambda db: (reads.append("authority"), original_load(db))[1],
    )
    assets, _citations, answer = _structured_answer(f"{SHEET}:r8", "三風製麵", "any")

    handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=False),
        ask_fn=lambda *args, **kwargs: answer,
        db_path=index_copy,
        audit_log_path=tmp_path / "audit.csv",
    )

    assert reads == []
    assert assets[0].url is None


def test_binding_mismatch_keeps_the_normal_answer_and_a_payload_free_audit(index_copy, tmp_path):
    _mutate_asset(index_copy, 38, article_title="drifted")
    assets, citations, answer = _structured_answer(f"{SHEET}:r8", "三風製麵", "any")
    audit_path = tmp_path / "audit.csv"

    reply = handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        db_path=index_copy,
        audit_log_path=audit_path,
    )

    assert "三風製麵" in reply["text"]
    assert "開啟連結" not in reply["text"]
    assert assets[0].url is None and citations[0].canonical_url is None

    rows = list(csv.reader(audit_path.read_text(encoding="utf-8").splitlines()))
    events = [row[1] for row in rows[1:] if len(row) > 1]
    assert APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE in events
    assert "slack_qa" in events
    failure = next(row for row in rows[1:] if row[1] == APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE)
    joined = ",".join(failure).casefold()
    for leak in ("digest", "binding", "sqlite", "index", ".mka", "http"):
        assert leak not in joined


@pytest.mark.parametrize(
    "damage", ["missing_index", "unreadable_index", "not_a_database", "index_is_a_symlink"]
)
def test_an_unusable_index_fails_enrichment_closed(tmp_path, index_copy, damage):
    if damage == "missing_index":
        target = tmp_path / "absent.sqlite"
    elif damage == "unreadable_index":
        target = tmp_path / "empty.sqlite"
        target.write_bytes(b"")
    elif damage == "not_a_database":
        target = tmp_path / "garbage.sqlite"
        target.write_bytes(b"definitely not sqlite" * 100)
    else:
        target = tmp_path / "link.sqlite"
        target.symlink_to(index_copy)

    with pytest.raises(slack_output_preview.ApprovedAssetUrlAuthorityError):
        load_index_bound_approved_asset_url_overlay(target)


def test_a_manifest_without_a_binding_block_fails_closed(tmp_path, monkeypatch, index_copy):
    """An authority built before index binding must not silently run unbound."""
    root = tmp_path / "authority"
    root.mkdir()
    source = (
        Path(slack_output_preview.__file__).resolve().parent
        / slack_output_preview.AUTHORITY_PACKAGE_RELATIVE_DIR
    )
    shutil.copyfile(
        source / slack_output_preview.APPROVED_ASSET_URL_VALUES,
        root / slack_output_preview.APPROVED_ASSET_URL_VALUES,
    )
    manifest = json.loads(
        (source / slack_output_preview.AUTHORITY_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    del manifest["manifest_hash"]
    del manifest["index_binding"]
    body = dict(manifest)
    manifest["manifest_hash"] = slack_output_preview._hash_json(body)
    (root / slack_output_preview.AUTHORITY_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: root)

    with pytest.raises(ApprovedAssetUrlIndexBindingError):
        load_index_bound_approved_asset_url_overlay(index_copy)


def test_the_slack_runtime_uses_only_the_index_bound_entry_point():
    source = (
        Path(slack_output_preview.__file__).resolve().parent / "slack_interface.py"
    ).read_text(encoding="utf-8")

    assert "load_index_bound_approved_asset_url_overlay" in source
    assert "load_pinned_approved_asset_url_overlay" not in source


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------


def _title_for(source_row, field_name):
    connection = sqlite3.connect(LIVE_INDEX)
    try:
        for (metadata_json,) in connection.execute("SELECT metadata_json FROM documents"):
            metadata = json.loads(metadata_json)
            if metadata.get("source_sheet") == SHEET and metadata.get("source_row") == source_row:
                return metadata.get(field_name)
    finally:
        connection.close()
    raise AssertionError(f"no indexed document for row {source_row}")


def _structured_answer(record_id, entity_name, title, asset_type="article"):
    asset = StructuredAsset(
        asset_type=asset_type,
        title=title,
        external_usage_status="可對外引用",
        source_record_id=record_id,
        source_sheet=SHEET,
        source_row=int(record_id.rsplit(":r", 1)[-1]),
        citation_label="[1]",
    )
    citation = Citation(
        label="[1]",
        title=title,
        source_path=f"synthetic:{title}",
        chunk_id=f"chunk:{asset_type}",
        status="published",
        source_type="database",
        record_type="merchant_case",
        data_classification="public",
        can_quote_externally=True,
        publish_date="2026-07-01",
        source_sheet=SHEET,
        source_row=int(record_id.rsplit(":r", 1)[-1]),
        freshness_note="最新日期 2026-07-01",
    )
    structured = StructuredRetrievalResult(
        query_plan={"raw_query": entity_name, "supported_constraints": []},
        matched_entities=[
            StructuredEntity(entity_type="merchant", entity_name=entity_name, assets=[asset])
        ],
        total_entities=1,
        total_assets=1,
    )
    answer = GeneratedAnswer(
        question=entity_name,
        answer="unused",
        citations=[citation],
        warnings=[],
        governance_checked=True,
        structured_result=structured,
    )
    return [asset], [citation], answer
