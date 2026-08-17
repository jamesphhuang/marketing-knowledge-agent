"""Sprint P3: the approved asset URL authority is runtime package data, not a test fixture.

The authority used to be resolved by walking up from the module to the repository root and reading
``tests/fixtures/historical_inputs_manifest.json`` plus three gitignored ``reports/`` CSVs. That
worked in a source checkout and silently failed closed in every packaged deployment. These tests
pin the replacement contract: one canonical authority, shipped inside the installed package,
carrying the same hash binding, the same row-level governance and the same fail-closed behaviour.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from marketing_knowledge_agent import slack_output_preview
from marketing_knowledge_agent.governance import metadata_allows_written_external_use
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
    APPROVED_ASSET_URL_INPUTS,
    APPROVED_ASSET_URL_VALUES,
    APPROVED_ASSET_URL_VALUE_COLUMNS,
    AUTHORITY_MANIFEST_FILENAME,
    AUTHORITY_PACKAGE_RELATIVE_DIR,
    ApprovedAssetUrlAuthorityError,
    AssetLookup,
    apply_approved_asset_url_overlay,
    approved_asset_identity,
    load_pinned_approved_asset_url_overlay,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# The 三風製麵 record is the accepted end-to-end anchor: one merchant, two distinct asset URLs.
SANFENG_RECORD = "商家夥伴案例資料庫:r8"
SANFENG_ARTICLE_URL = "https://blog.shopline.tw/merchant-showcase-shanfeng/"
SANFENG_VIDEO_URL = "https://www.youtube.com/watch?v=WIMy_AFA0pE"
SANFENG_BRAND = "三風製麵"
SANFENG_ARTICLE_TITLE = "傳統製麵廠的數位轉型之路！《三風製麵》如何透過 SHOPLINE 提升客單價超過兩成"
SANFENG_VIDEO_TITLE = (
    "傳統製麵廠的數位轉型之路！《三風製麵》如何透過 SHOPLINE 提升客單價超過兩成"
    "｜SHOPLINE TALKS 聊品牌 EP 89"
)
LEGACY_AUTHORITY_MANIFEST = "tests/fixtures/historical_inputs_manifest.json"


def _authority_dir() -> Path:
    return Path(slack_output_preview.__file__).resolve().parent / AUTHORITY_PACKAGE_RELATIVE_DIR


# --------------------------------------------------------------------------------------------
# A. Feature OFF must not touch the authority at all.
# --------------------------------------------------------------------------------------------


def test_feature_off_performs_no_authority_read_but_feature_on_does(monkeypatch, tmp_path):
    """Discriminatory: the same event with the flag flipped is what proves the OFF path is silent."""
    reads = _record_authority_reads(monkeypatch)

    off_assets, _off_citations, off_answer = _structured_answer()
    handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=False),
        ask_fn=lambda *args, **kwargs: off_answer,
        audit_log_path=tmp_path / "off.csv",
    )
    assert reads == []
    assert [asset.url for asset in off_assets] == [None, None]

    on_assets, _on_citations, on_answer = _structured_answer()
    handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: on_answer,
        audit_log_path=tmp_path / "on.csv",
    )
    # Recorded per Path API, and read_bytes goes through open, so compare the set of files touched.
    assert set(reads) == {AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS}
    assert [asset.url for asset in on_assets] == [SANFENG_ARTICLE_URL, SANFENG_VIDEO_URL]


def test_feature_off_survives_an_authority_that_cannot_be_resolved(monkeypatch, tmp_path):
    """Feature OFF must not even reach the resolver, so a broken bundle is irrelevant to it."""
    def explode():
        raise AssertionError("feature OFF must not resolve the approved URL authority")

    monkeypatch.setattr(slack_output_preview, "_authority_root", explode)
    _assets, _citations, answer = _structured_answer()

    reply = handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=False),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=tmp_path / "audit.csv",
    )

    assert "三風製麵" in reply["text"]


# --------------------------------------------------------------------------------------------
# B. Feature ON loads the canonical runtime authority, from inside the package.
# --------------------------------------------------------------------------------------------


def test_authority_resolves_inside_the_installed_package(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    package_dir = Path(slack_output_preview.__file__).resolve().parent

    root = slack_output_preview._authority_root()

    assert root == package_dir / AUTHORITY_PACKAGE_RELATIVE_DIR
    assert root.is_relative_to(package_dir)
    for filename in (AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS):
        assert (root / filename).is_file()


def test_packaged_authority_produces_the_approved_overlay():
    overlay = load_pinned_approved_asset_url_overlay()

    assert overlay.errors == []
    assert overlay.values
    assert overlay.blocked_asset_ids == set()


def test_runtime_no_longer_reads_the_test_fixture_manifest(monkeypatch, tmp_path):
    """The old authority location must be irrelevant: one canonical authority, not two."""
    reads = _record_reads_under(monkeypatch, REPOSITORY_ROOT / "tests" / "fixtures")
    monkeypatch.chdir(tmp_path)

    assert load_pinned_approved_asset_url_overlay().errors == []
    assert reads == []
    source = Path(slack_output_preview.__file__).read_text(encoding="utf-8")
    assert "tests/fixtures" not in source
    assert "reports/" not in source


# --------------------------------------------------------------------------------------------
# C/D/E. Missing, corrupt and hash-mismatched authority all fail closed.
# --------------------------------------------------------------------------------------------


def test_missing_authority_fails_closed_without_breaking_the_slack_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        slack_output_preview, "_authority_root", lambda: tmp_path / "absent-authority"
    )
    assets, citations, answer = _structured_answer()
    audit_path = tmp_path / "audit.csv"

    reply = handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=audit_path,
    )

    assert "三風製麵" in reply["text"]
    assert "開啟連結" not in reply["text"]
    assert [asset.url for asset in assets] == [None, None]
    assert [citation.canonical_url for citation in citations] == [None, None]
    _assert_payload_free_audit(audit_path)


def test_absent_manifest_is_rejected_before_any_artifact_is_trusted(tmp_path, monkeypatch):
    root = _authority_copy(tmp_path)
    (root / AUTHORITY_MANIFEST_FILENAME).unlink()
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: root)

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        load_pinned_approved_asset_url_overlay()


@pytest.mark.parametrize(
    ("damage", "payload"),
    [
        ("not_json", "}{ not json"),
        ("not_an_object", json.dumps([1, 2, 3])),
        ("self_hash_mismatch", json.dumps({"manifest_hash": "0" * 64, "inputs": []})),
        ("absent_self_hash", json.dumps({"inputs": []})),
        ("inputs_not_a_list", None),
        ("duplicate_entry", None),
    ],
)
def test_corrupt_manifest_fails_closed(tmp_path, monkeypatch, damage, payload):
    root = _authority_copy(tmp_path)
    manifest_path = root / AUTHORITY_MANIFEST_FILENAME
    if payload is None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        del manifest["manifest_hash"]
        if damage == "inputs_not_a_list":
            manifest["inputs"] = {"not": "a list"}
        else:
            manifest["inputs"] = list(manifest["inputs"]) + [dict(manifest["inputs"][0])]
        payload = json.dumps(dict(manifest, manifest_hash=_hash_json(manifest)), ensure_ascii=False)
    manifest_path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: root)

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        load_pinned_approved_asset_url_overlay()


@pytest.mark.parametrize("filename", APPROVED_ASSET_URL_INPUTS)
@pytest.mark.parametrize("damage", ["one_byte_flip", "truncated", "removed", "replaced_by_symlink"])
def test_artifact_hash_mismatch_fails_closed(tmp_path, monkeypatch, filename, damage):
    root = _authority_copy(tmp_path)
    target = root / filename
    payload = bytearray(target.read_bytes())
    if damage == "one_byte_flip":
        payload[-1] ^= 0x01
        target.write_bytes(bytes(payload))
    elif damage == "truncated":
        target.write_bytes(bytes(payload[:-1]))
    elif damage == "removed":
        target.unlink()
    else:
        decoy = root / f"decoy-{filename}"
        decoy.write_bytes(bytes(payload))
        target.unlink()
        target.symlink_to(decoy)
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: root)

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        load_pinned_approved_asset_url_overlay()


def test_unpinned_extra_artifact_cannot_join_the_required_set(tmp_path, monkeypatch):
    """Every required artifact must be covered by the manifest; dropping a pin fails closed."""
    root = _authority_copy(tmp_path)
    manifest = json.loads((root / AUTHORITY_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    del manifest["manifest_hash"]
    manifest["inputs"] = [
        entry for entry in manifest["inputs"] if entry["relative_path"] != APPROVED_ASSET_URL_VALUES
    ]
    (root / AUTHORITY_MANIFEST_FILENAME).write_text(
        json.dumps(dict(manifest, manifest_hash=_hash_json(manifest)), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: root)

    with pytest.raises(ApprovedAssetUrlAuthorityError):
        load_pinned_approved_asset_url_overlay()


# --------------------------------------------------------------------------------------------
# F. The verified bytes are what gets parsed.
# --------------------------------------------------------------------------------------------


def test_overlay_parses_verified_bytes_even_if_the_file_is_rewritten_after_verification(
    tmp_path, monkeypatch
):
    """A concurrent rewrite between hashing and parsing must not reach the overlay."""
    root = _authority_copy(tmp_path)
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: root)
    original = slack_output_preview._verified_pinned_bytes
    forged = _forged_apply_rows_csv()

    def rewrite_after_verifying(path, entry):
        verified = original(path, entry)
        # Swap the on-disk artifact the instant its hash has been accepted.
        Path(path).write_bytes(forged)
        return verified

    monkeypatch.setattr(slack_output_preview, "_verified_pinned_bytes", rewrite_after_verifying)

    overlay = load_pinned_approved_asset_url_overlay()

    assert overlay.errors == []
    assert overlay.url(_sanfeng_lookup("article"), "asset_url") == SANFENG_ARTICLE_URL
    assert not any(
        record.proposed_value == "https://attacker.example/pwn" for record in overlay.values.values()
    )
    # The rewrite really did land on disk, so the assertion above is not vacuous.
    assert (root / APPROVED_ASSET_URL_VALUES).read_bytes() == forged


# --------------------------------------------------------------------------------------------
# G. The distributable artifact carries the authority.
# --------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_built_wheel_contains_every_required_authority_artifact(tmp_path):
    """Build the real distributable and inspect it; package-data config alone proves nothing."""
    source = _clean_source_tree(tmp_path)
    wheel_dir = tmp_path / "wheel"
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "-w", str(wheel_dir), str(source)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"wheel build unavailable in this environment: {build.stderr[-400:]}")

    wheels = list(wheel_dir.glob("marketing_knowledge_agent-*.whl"))
    assert len(wheels) == 1
    names = set(zipfile.ZipFile(wheels[0]).namelist())

    expected = {
        f"marketing_knowledge_agent/{AUTHORITY_PACKAGE_RELATIVE_DIR}/{filename}"
        for filename in (AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS)
    }
    assert expected <= names

    with zipfile.ZipFile(wheels[0]) as archive:
        for filename in (AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS):
            packaged = archive.read(
                f"marketing_knowledge_agent/{AUTHORITY_PACKAGE_RELATIVE_DIR}/{filename}"
            )
            assert packaged == (_authority_dir() / filename).read_bytes()

    # The wheel must not smuggle the retired authority location back in.
    assert not [name for name in names if name.startswith(("tests/", "reports/"))]


@pytest.mark.slow
def test_wheel_authority_is_self_sufficient_outside_any_repository(tmp_path):
    """Unpack the wheel somewhere with no repo above it and load the authority from there."""
    source = _clean_source_tree(tmp_path)
    wheel_dir = tmp_path / "wheel"
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "-w", str(wheel_dir), str(source)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"wheel build unavailable in this environment: {build.stderr[-400:]}")

    unpacked = tmp_path / "site-packages"
    with zipfile.ZipFile(next(wheel_dir.glob("*.whl"))) as archive:
        archive.extractall(unpacked)
    installed = unpacked / "marketing_knowledge_agent" / AUTHORITY_PACKAGE_RELATIVE_DIR

    # Nothing resembling the old repository layout exists above the installed package.
    assert not (unpacked.parent / "tests" / "fixtures").exists()
    assert not (unpacked.parent / "reports").exists()

    manifest = json.loads((installed / AUTHORITY_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    assert manifest["manifest_hash"] == _hash_json(body)
    for entry in manifest["inputs"]:
        payload = (installed / entry["relative_path"]).read_bytes()
        assert len(payload) == entry["expected_size"]
        assert hashlib.sha256(payload).hexdigest() == entry["expected_sha256"]


# --------------------------------------------------------------------------------------------
# H/I. The accepted 三風製麵 mappings, with no parent or sibling substitution.
# --------------------------------------------------------------------------------------------


def test_sanfeng_article_and_video_urls_are_exactly_the_accepted_values():
    overlay = load_pinned_approved_asset_url_overlay()

    for field in ("asset_url", "canonical_url"):
        assert overlay.url(_sanfeng_lookup("article"), field) == SANFENG_ARTICLE_URL
        assert overlay.url(_sanfeng_lookup("video"), field) == SANFENG_VIDEO_URL


def test_sanfeng_assets_reach_slack_with_their_own_links(tmp_path):
    assets, citations, answer = _structured_answer()

    reply = handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=tmp_path / "audit.csv",
    )

    assert [asset.url for asset in assets] == [SANFENG_ARTICLE_URL, SANFENG_VIDEO_URL]
    assert [citation.canonical_url for citation in citations] == [SANFENG_ARTICLE_URL, SANFENG_VIDEO_URL]
    assert f"<{SANFENG_ARTICLE_URL}|開啟連結>" in reply["text"]
    assert f"<{SANFENG_VIDEO_URL}|開啟連結>" in reply["text"]


def test_no_merchant_parent_or_sibling_url_is_inherited():
    """Each asset resolves only through its own identity; nothing is inherited or shared."""
    overlay = load_pinned_approved_asset_url_overlay()

    assert approved_asset_identity(
        SANFENG_BRAND, SANFENG_ARTICLE_TITLE, "article"
    ) != approved_asset_identity(SANFENG_BRAND, SANFENG_VIDEO_TITLE, "video")
    assert overlay.url(_sanfeng_lookup("article"), "asset_url") != overlay.url(
        _sanfeng_lookup("video"), "asset_url"
    )
    # The merchant name alone, and an unapproved sibling type, resolve to nothing at all.
    absent = [
        AssetLookup(SANFENG_RECORD, SANFENG_RECORD, SANFENG_BRAND, SANFENG_BRAND, "article"),
        _sanfeng_lookup("podcast", SANFENG_ARTICLE_TITLE),
        _sanfeng_lookup("news", SANFENG_ARTICLE_TITLE),
        # The article's own title under the video's type, and vice versa.
        _sanfeng_lookup("video", SANFENG_ARTICLE_TITLE),
        _sanfeng_lookup("article", SANFENG_VIDEO_TITLE),
    ]
    for lookup in absent:
        for field in ("asset_url", "canonical_url"):
            assert overlay.url(lookup, field) is None


def test_each_packaged_identity_holds_at_most_one_url_per_field():
    """Source-free structural guard: no identity can carry two competing URLs for one field."""
    rows = _packaged_rows(APPROVED_ASSET_URL_VALUES)
    seen = set()
    for row in rows:
        key = (row["asset_identity"], row["field"])
        assert key not in seen, f"duplicate identity/field pair: {key}"
        seen.add(key)
    assert len(seen) == len(rows)


def test_sibling_assets_never_share_a_url_across_every_multi_asset_record():
    """Drift guard over the local governance source, where plaintext identities are available."""
    source = REPOSITORY_ROOT / "reports/asset_metadata_apply_preview/asset_apply_preview.csv"
    if not source.is_file():
        pytest.skip("local governance source is not present in this checkout")
    overlay = load_pinned_approved_asset_url_overlay()

    by_record = {}
    for row in csv.DictReader(source.read_bytes().decode("utf-8-sig").splitlines()):
        if row["field"].strip() != "asset_url":
            continue
        record_id, asset_id = row["record_id"].strip(), row["asset_id"].strip()
        url = overlay.url(_source_lookup(row), "asset_url")
        if url:
            by_record.setdefault(record_id, {})[asset_id] = url

    multi = {record: urls for record, urls in by_record.items() if len(urls) > 1}
    assert multi, "the invariant is vacuous without multi-asset records"
    for record_id, urls in multi.items():
        assert len(set(urls.values())) == len(urls), f"sibling assets share a URL: {record_id}"


def test_an_asset_without_its_own_approved_url_gets_no_url_at_all(tmp_path):
    """The failure mode this guards is falling back to a merchant-level canonical URL."""
    assets, citations, answer = _structured_answer()
    assets[1].title = f"{SANFENG_VIDEO_TITLE} (unapproved)"
    citations[1].title = assets[1].title

    handle_slack_event(
        {"text": "三風製麵", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=tmp_path / "audit.csv",
    )

    assert assets[0].url == SANFENG_ARTICLE_URL
    assert assets[1].url is None
    assert citations[1].canonical_url is None


# --------------------------------------------------------------------------------------------
# J. Row-level governance survives the move.
# --------------------------------------------------------------------------------------------


def test_every_packaged_row_is_inside_the_url_contract():
    """The eligibility contract now runs at build time; what ships is only its approved outcome."""
    rows = _packaged_rows(APPROVED_ASSET_URL_VALUES)
    overlay = load_pinned_approved_asset_url_overlay()

    assert rows
    for row in rows:
        assert row["field"] in {"asset_url", "canonical_url"}
        assert row["url"].startswith(("http://", "https://"))
        assert len(row["asset_identity"]) == 32
        assert set(row["asset_identity"]) <= set("0123456789abcdef")
    assert len(overlay.values) == len(rows)


def test_no_blocked_identity_inventory_is_packaged_at_all():
    """DG-01: blocked rows carry no URL, so their identity would be their entire payload."""
    packaged = sorted(
        path.name
        for path in _authority_dir().iterdir()
        # "._*" are macOS AppleDouble sidecars from the working tree; they are gitignored and the
        # package-data allowlist names files explicitly, so they never reach a distribution.
        if path.is_file() and not path.name.startswith("._")
    )

    assert packaged == [APPROVED_ASSET_URL_VALUES, AUTHORITY_MANIFEST_FILENAME]
    assert "blocked_asset_ids.csv" not in packaged
    assert APPROVED_ASSET_URL_INPUTS == (APPROVED_ASSET_URL_VALUES,)
    assert load_pinned_approved_asset_url_overlay().blocked_asset_ids == set()


def test_no_blocked_asset_has_an_approved_url_to_resolve():
    """The property that makes the inventory redundant, proved against the reviewed source."""
    blocked = _local_blocked_asset_ids()
    overlay = load_pinned_approved_asset_url_overlay()
    rows = _source_rows_by_asset_id()

    resolved = []
    for asset_id in blocked:
        row = rows.get(asset_id)
        if row is None:
            # A blocked asset that never entered Apply Preview has no published triple at all.
            continue
        for field in ("asset_url", "canonical_url"):
            if overlay.url(_source_lookup(row), field) is not None:
                resolved.append((asset_id, field))
    assert blocked, "the invariant is vacuous without blocked assets"
    assert resolved == []


@pytest.mark.parametrize(
    "confusion",
    [
        "its_own_published_fields",
        "sibling_has_an_approved_url",
        "merchant_parent_url",
        "same_merchant_other_type",
        "forged_near_miss_title",
    ],
)
def test_a_blocked_asset_gets_no_url_without_any_blocked_inventory(tmp_path, confusion):
    """Every way a blocked asset could try to acquire a link, with the inventory absent.

    Anchored on the sharpest real case: a source record whose article is approved and whose video
    is governance-blocked. Under the retired coordinate identity the blocked video was suppressed
    by an explicit inventory; it must now be suppressed because it simply has nothing to resolve.
    """
    blocked_asset_id, sibling = _blocked_asset_with_approved_sibling()
    record_id, asset_type = blocked_asset_id.rsplit(":", 1)
    overlay = load_pinned_approved_asset_url_overlay()
    assert overlay.blocked_asset_ids == set(), "this test is only meaningful with no inventory"
    assert overlay.url(_source_lookup(sibling), "asset_url"), "the sibling must really have a URL"

    assets, citations, answer = _structured_answer()
    asset, citation = assets[0], citations[0]
    entity = answer.structured_result.matched_entities[0]
    asset.source_record_id, asset.asset_type = record_id, asset_type
    citation.chunk_id = f"chunk:{asset_type}"
    entity.entity_name = sibling["brand_name"]
    asset.title = citation.title = f"{sibling['brand_name']} 的受限內容"

    if confusion == "sibling_has_an_approved_url":
        pass  # the sibling's approved URL is already loaded; the blocked asset must not see it
    elif confusion == "merchant_parent_url":
        asset.title = citation.title = sibling["brand_name"]
    elif confusion == "same_merchant_other_type":
        asset.asset_type = sibling["asset_type"]
        citation.chunk_id = f"chunk:{sibling['asset_type']}"
    elif confusion == "forged_near_miss_title":
        asset.title = citation.title = f"{sibling['asset_title']}!"

    apply_approved_asset_url_overlay(answer, overlay)

    # Non-vacuous: the sibling's approved URL is loaded and resolvable (asserted above), yet none
    # of it reaches the blocked asset.
    assert asset.url is None
    assert citation.canonical_url is None


def test_a_restricted_asset_is_still_refused_when_the_bundle_is_empty(tmp_path, monkeypatch):
    """A fresh clone with no reports/ and an empty authority still enriches nothing."""
    root = tmp_path / "authority"
    root.mkdir(parents=True)
    (root / APPROVED_ASSET_URL_VALUES).write_bytes(b"asset_identity,field,url\n")
    _write_test_manifest(root)
    monkeypatch.setattr(slack_output_preview, "_authority_root", lambda: root)
    assets, citations, answer = _structured_answer()

    overlay = load_pinned_approved_asset_url_overlay()
    applied = apply_approved_asset_url_overlay(answer, overlay)

    assert overlay.values == {}
    assert applied == 0
    assert [asset.url for asset in assets] == [None, None]


def test_the_local_governance_overlay_still_blocks_on_its_own_identities():
    """Removing the packaged inventory must not disarm the offline preview path."""
    overlay = slack_output_preview._build_asset_url_overlay(
        [], [{"asset_id": "Sheet:r9:video", "action": "blocked"}], []
    )

    assert overlay.is_blocked("Sheet:r9:video")
    assert not overlay.is_blocked("Sheet:r9:article")


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("can_quote_externally", False),
        ("data_classification", "internal"),
        ("status", "draft"),
        ("record_type", "pending_metric"),
        ("allowed_exposure_channels", ["verbal_briefing"]),
    ],
)
def test_restricted_pending_and_exposure_governance_still_blocks_enrichment(attribute, value):
    """URL enrichment runs after governance and must never re-open a gate governance closed."""
    assets, citations, answer = _structured_answer()
    setattr(citations[0], attribute, value)
    assert not metadata_allows_written_external_use(citations[0])

    applied = apply_approved_asset_url_overlay(answer, load_pinned_approved_asset_url_overlay())

    assert applied == 1
    assert assets[0].url is None
    assert citations[0].canonical_url is None
    assert assets[1].url == SANFENG_VIDEO_URL


# --------------------------------------------------------------------------------------------
# DG-01. The distribution must carry no source governance evidence.
# --------------------------------------------------------------------------------------------


# Column names that only ever exist on the local human-review governance path.
PROHIBITED_COLUMNS = (
    "reviewer", "reviewed_at", "review_decision", "review_status", "review_required",
    "proposed_decision", "approved_for_index", "brand_name", "asset_title", "partner_name",
    "interview_date", "interview_status", "published_at", "publication_status", "notes",
    "reason", "provenance", "source_location", "eligibility", "governance_status",
    "current_value", "existing_value", "conflict_status", "confidence", "record_id", "asset_id",
)


def test_packaged_authority_headers_match_a_strict_allowlist():
    """A new authority column must fail this test until it is explicitly reviewed."""
    assert _packaged_header(APPROVED_ASSET_URL_VALUES) == APPROVED_ASSET_URL_VALUE_COLUMNS
    assert set(APPROVED_ASSET_URL_VALUE_COLUMNS) & set(PROHIBITED_COLUMNS) == set()
    assert sorted(
        item.name for item in _authority_dir().iterdir()
        if item.is_file() and not item.name.startswith("._")
    ) == sorted([AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS])


def test_packaged_authority_carries_no_reviewer_or_source_governance_content():
    for filename in APPROVED_ASSET_URL_INPUTS:
        text = (_authority_dir() / filename).read_bytes().decode("utf-8")
        header, _, body = text.partition("\n")
        columns = set(next(csv.reader([header])))
        assert not columns & set(PROHIBITED_COLUMNS), f"{filename} header exposes source columns"
        assert "James Huang" not in text, f"{filename} exposes reviewer identity"
        assert "商家夥伴案例資料庫" not in text, f"{filename} exposes the source workbook sheet"
        assert "excel_hyperlink" not in body
        assert "人工確認" not in body
        # Every non-URL cell must be an opaque identity or a contract field name.
        for row in csv.reader(body.splitlines()):
            for cell in row:
                if cell.startswith("http"):
                    continue
                assert cell in {"asset_url", "canonical_url"} or _is_opaque(cell), cell


def test_no_source_brand_or_title_value_is_packaged_outside_an_approved_url():
    """Brand names may only survive as part of an approved public URL slug, never as a field.

    Compared against the real local governance source, so this catches a regression that
    reintroduced a brand or title column even if its header were renamed.
    """
    source = REPOSITORY_ROOT / "reports/asset_metadata_apply_preview/asset_apply_preview.csv"
    if not source.is_file():
        pytest.skip("local governance source is not present in this checkout")
    values = set()
    for row in csv.DictReader(source.read_bytes().decode("utf-8-sig").splitlines()):
        values.add(row["brand_name"].strip())
        values.add(row["asset_title"].strip())
        values.add(row["reviewer"].strip())
        values.add(row["source_location"].strip())
    values = {value for value in values if len(value) > 2 and not value.startswith("http")}

    for filename in APPROVED_ASSET_URL_INPUTS:
        text = (_authority_dir() / filename).read_bytes().decode("utf-8")
        for row in csv.reader(text.splitlines()[1:]):
            for cell in row:
                if cell.startswith("http"):
                    continue  # an approved public URL may embed the merchant slug
                assert not [value for value in values if value in cell], f"{filename}: {cell!r}"


def test_no_human_review_source_artifact_is_packaged():
    packaged = {item.name for item in _authority_dir().iterdir() if item.is_file()}
    for retired in (
        "human_review_decisions.csv",
        "human_review_template.csv",
        "asset_apply_preview.csv",
        "asset_apply_preview_blocked.csv",
        "asset_metadata_inventory.csv",
        "review_decision_status.csv",
    ):
        assert retired not in packaged


@pytest.mark.slow
def test_built_wheel_contains_no_source_governance_data(tmp_path):
    """Scan the real distributable, not the source tree, for reviewer and customer content."""
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert not [name for name in names if name.startswith(("tests/", "reports/"))]
        # Only data files matter here; modules legitimately share names with the source reports.
        data = [name for name in names if name.endswith((".csv", ".json", ".xlsx", ".sqlite"))]
        assert not [name for name in data if "human_review" in name]
        assert not [name for name in data if "apply_preview" in name]
        assert not [name for name in data if "inventory" in name or "review_decision" in name]
        assert sorted(data) == sorted(
            f"marketing_knowledge_agent/{AUTHORITY_PACKAGE_RELATIVE_DIR}/{filename}"
            for filename in (AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS)
        )

        for name in names:
            if not name.startswith(f"marketing_knowledge_agent/{AUTHORITY_PACKAGE_RELATIVE_DIR}/"):
                continue
            text = archive.read(name).decode("utf-8")
            assert "James Huang" not in text
            if name.endswith(".csv"):
                columns = set(next(csv.reader([text.partition("\n")[0]])))
                assert not columns & set(PROHIBITED_COLUMNS)

        # The runtime authority module must carry no dependency on the retired locations. The
        # local preview CLI still names reports/ paths for the operator's own workflow, which is
        # a local default, not a runtime authority read.
        authority_module = archive.read("marketing_knowledge_agent/slack_output_preview.py").decode(
            "utf-8"
        )
        assert "tests/fixtures" not in authority_module
        assert "reports/" not in authority_module
        assert "human_review" not in authority_module


@pytest.mark.slow
def test_wheel_authority_size_stays_minimal(tmp_path):
    """A regression here means source material crept back into the distribution."""
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        packaged = sum(
            info.file_size
            for info in archive.infolist()
            if AUTHORITY_PACKAGE_RELATIVE_DIR in info.filename
        )
    assert packaged < 100_000, f"packaged authority grew to {packaged} bytes"


# --------------------------------------------------------------------------------------------
# Migration integrity: one canonical authority, derived from the frozen pins.
# --------------------------------------------------------------------------------------------


def test_packaged_authority_is_self_describing_and_hash_bound():
    manifest = json.loads((_authority_dir() / AUTHORITY_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "manifest_hash"}

    assert manifest["manifest_hash"] == _hash_json(body)
    pinned = {entry["relative_path"] for entry in manifest["inputs"]}
    assert pinned == set(APPROVED_ASSET_URL_INPUTS)
    for entry in manifest["inputs"]:
        payload = (_authority_dir() / entry["relative_path"]).read_bytes()
        assert entry["input_type"] == "file"
        assert len(payload) == entry["expected_size"]
        assert hashlib.sha256(payload).hexdigest() == entry["expected_sha256"]


def _builder():
    sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
    try:
        import build_approved_asset_url_authority as builder
    finally:
        sys.path.pop(0)
    for relative in builder.SOURCE_ARTIFACTS:
        if not (REPOSITORY_ROOT / relative).is_file():
            pytest.skip("local governance source is not present in this checkout")
    return builder


def test_packaged_authority_has_not_drifted_from_its_pinned_sources():
    """The bundle must still be exactly what the builder derives from the frozen pins.

    Skips where the local governance reports are absent (a fresh clone or a deployment), because
    the packaged bundle is the authority there -- this guards the machine that regenerates it.
    """
    builder = _builder()

    artifacts = builder.build_authority_bundle(REPOSITORY_ROOT)

    assert builder.check_authority_bundle(REPOSITORY_ROOT, artifacts) == []
    assert set(artifacts) == {AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS}
    for filename, payload in artifacts.items():
        assert (_authority_dir() / filename).read_bytes() == payload


def test_sanitized_authority_is_effectively_identical_to_the_accepted_overlay():
    """DG-01 equivalence: sanitizing must change what is packaged, never what runtime resolves."""
    _builder()  # skips when the local governance source is unavailable

    def rows(relative):
        text = (REPOSITORY_ROOT / relative).read_bytes().decode("utf-8-sig")
        return list(csv.DictReader(text.splitlines()))

    accepted = slack_output_preview._build_asset_url_overlay(
        rows("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
        rows("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
        rows("reports/asset_metadata_preview/human_review_template.csv"),
    )
    packaged = load_pinned_approved_asset_url_overlay()

    source_rows = _source_rows_by_asset_id()

    assert accepted.errors == [] and packaged.errors == []
    assert len(accepted.values) == len(packaged.values) == 412
    # Identity representation changed on purpose; the resolved URL for each asset must not.
    for record_id, asset_id, field in accepted.values:
        assert accepted.value(record_id, asset_id, field) == packaged.url(
            _source_lookup(source_rows[asset_id]), field
        ), f"URL mapping diverged for {asset_id}/{field}"
    # No blocked asset gained a URL now that the inventory is gone.
    for asset_id in accepted.blocked_asset_ids:
        row = source_rows.get(asset_id)
        if row is not None:
            for field in ("asset_url", "canonical_url"):
                assert packaged.url(_source_lookup(row), field) is None
    # Negative control: an asset outside the authority resolves to nothing in both.
    assert accepted.value("X:r1", "X:r1:article", "asset_url") is None
    assert packaged.url(AssetLookup("X:r1", "X:r1:article", "X", "T", "article"), "asset_url") is None


def test_builder_refuses_to_package_prohibited_columns():
    """The build tool must fail rather than emit anything outside the allowlist."""
    builder = _builder()

    prohibited = (
        b"asset_identity,field,url,reviewer\n" + b"0" * 32 + b",asset_url,https://a.example,X\n",
        b"asset_identity,field,url\n" + b"0" * 32 + b",asset_url,javascript:alert(1)\n",
        b"asset_identity,field,url\n" + b"0" * 32 + b",review_status,https://a.example\n",
        b"asset_identity,field,url\nnot-hex,asset_url,https://a.example\n",
    )
    for payload in prohibited:
        with pytest.raises(builder.AuthorityBuildError):
            builder._assert_sanitized(payload)


def test_builder_check_mode_detects_a_stale_bundle(tmp_path):
    builder = _builder()
    artifacts = builder.build_authority_bundle(REPOSITORY_ROOT)
    tampered = dict(artifacts)
    tampered[APPROVED_ASSET_URL_VALUES] = artifacts[APPROVED_ASSET_URL_VALUES] + b"\n"

    assert builder.check_authority_bundle(REPOSITORY_ROOT, tampered) == [APPROVED_ASSET_URL_VALUES]


def test_the_retired_fixture_manifest_is_untouched_and_no_longer_an_authority():
    """The historical manifest stays frozen evidence; it simply stops being the runtime authority."""
    tracked = subprocess.run(
        ["git", "show", f"HEAD:{LEGACY_AUTHORITY_MANIFEST}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
    )
    if tracked.returncode != 0:
        pytest.skip("git object for the historical manifest is unavailable")

    assert (REPOSITORY_ROOT / LEGACY_AUTHORITY_MANIFEST).read_bytes() == tracked.stdout
    assert LEGACY_AUTHORITY_MANIFEST not in Path(slack_output_preview.__file__).read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------------------------
# N. The identity is public-derived: enumeration teaches nothing the link does not already tell.
# --------------------------------------------------------------------------------------------


def test_source_coordinate_enumeration_recovers_nothing():
    """The exact attack that recovered 206/206 v2 identities must now recover zero."""
    identities = {row["asset_identity"] for row in _packaged_rows(APPROVED_ASSET_URL_VALUES)}
    sheets = {"商家夥伴案例資料庫", "Sheet", "商家夥伴案例資料庫 "}
    candidates = set()
    for sheet in sheets:
        for row in range(1, 3001):
            for asset_type in ("article", "video", "podcast", "news", "other"):
                asset_id = f"{sheet}:r{row}:{asset_type}"
                candidates.add(_legacy_identity(asset_id))
                candidates.add(_legacy_identity(f"{sheet}:r{row}"))
                candidates.add(approved_asset_identity(sheet, f"r{row}", asset_type))

    assert len(candidates) > 40000, "the attack must actually be broad to be evidence"
    assert identities & candidates == set()


def test_identity_is_reproducible_from_published_fields_alone():
    """K: the only preimage is the triple already printed next to the link."""
    identities = {row["asset_identity"] for row in _packaged_rows(APPROVED_ASSET_URL_VALUES)}
    rows = _source_rows_by_asset_id()

    derived = {
        approved_asset_identity(row["brand_name"], row["asset_title"], row["asset_type"])
        for row in rows.values()
    }
    assert identities <= derived
    # ...and nothing beyond those fields participates: perturbing only a coordinate is inert.
    sample = next(iter(rows.values()))
    base = approved_asset_identity(sample["brand_name"], sample["asset_title"], sample["asset_type"])
    assert base in identities
    assert base == approved_asset_identity(
        sample["brand_name"], sample["asset_title"], sample["asset_type"]
    )


@pytest.mark.parametrize("coordinate", ["source sheet", "source row", "record_id", "source_location"])
def test_no_source_coordinate_is_recoverable_from_an_identity(coordinate):
    """L/M/N: each prohibited input, replayed against the emitted set."""
    identities = {row["asset_identity"] for row in _packaged_rows(APPROVED_ASSET_URL_VALUES)}
    rows = _source_rows_by_asset_id()

    guesses = set()
    for asset_id, row in rows.items():
        record_id = row["record_id"]
        sheet = record_id.rsplit(":", 1)[0]
        value = {
            "source sheet": sheet,
            "source row": record_id.rsplit(":", 1)[-1],
            "record_id": record_id,
            "source_location": row.get("source_location", ""),
        }[coordinate]
        guesses.update({value, _legacy_identity(value), _legacy_identity(asset_id)})
        guesses.add(approved_asset_identity(value, value, row["asset_type"]))

    assert identities & guesses == set()


# --------------------------------------------------------------------------------------------
# O/P/Q/R. Three-way identity confusion must fail closed, never cross-attach.
# --------------------------------------------------------------------------------------------


def test_same_title_different_merchant_are_distinct_identities():
    a = approved_asset_identity("Merchant A", "Shared Title", "article")
    b = approved_asset_identity("Merchant B", "Shared Title", "article")

    assert a and b and a != b


def test_same_merchant_same_title_different_asset_type_are_distinct_identities():
    article = approved_asset_identity(SANFENG_BRAND, SANFENG_ARTICLE_TITLE, "article")
    video = approved_asset_identity(SANFENG_BRAND, SANFENG_ARTICLE_TITLE, "video")

    assert article and video and article != video


def test_same_merchant_different_title_same_asset_type_are_distinct_identities():
    first = approved_asset_identity(SANFENG_BRAND, SANFENG_ARTICLE_TITLE, "article")
    second = approved_asset_identity(SANFENG_BRAND, SANFENG_VIDEO_TITLE, "article")

    assert first and second and first != second


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # The separator must not be shiftable between components.
        (("a:b", "c", "article"), ("a", "b:c", "article")),
        (("a", "b", "c:article"), ("a", "b:c", "article")),
        (("a\\", ":b", "article"), ("a", "\\:b", "article")),
        # Case and full-width variants are different strings and must stay different identities:
        # folding them would let one merchant's URL attach to another's asset.
        (("acme", "T", "article"), ("ACME", "T", "article")),
        (("ACME", "T", "article"), ("ＡＣＭＥ", "T", "article")),
    ],
)
def test_normalization_ambiguity_never_collides(left, right):
    assert approved_asset_identity(*left) != approved_asset_identity(*right)


def test_canonically_equivalent_spellings_resolve_to_one_identity():
    """NFC only: the same string spelled decomposed must still join, or the URL is silently lost."""
    composed = "\u9ad8\u96c4caf\u00e9"
    decomposed = "\u9ad8\u96c4cafe\u0301"

    assert composed != decomposed
    assert approved_asset_identity(composed, "T", "article") == approved_asset_identity(
        decomposed, "T", "article"
    )


@pytest.mark.parametrize("missing", [("", "T", "article"), ("B", "", "article"), ("B", "T", "")])
def test_an_incomplete_triple_fails_closed(missing):
    overlay = load_pinned_approved_asset_url_overlay()

    assert approved_asset_identity(*missing) == ""
    assert overlay.url(AssetLookup("r", "r:article", *missing), "asset_url") is None


def test_every_packaged_identity_belongs_to_exactly_one_asset():
    """O: collision count across the full authority set."""
    rows = _source_rows_by_asset_id()
    by_identity = {}
    for asset_id, row in rows.items():
        identity = approved_asset_identity(row["brand_name"], row["asset_title"], row["asset_type"])
        by_identity.setdefault(identity, set()).add(asset_id)

    collisions = {i: a for i, a in by_identity.items() if len(a) > 1}
    assert collisions == {}
    assert len(by_identity) == len(rows)


def test_the_builder_rejects_a_source_derived_identity_policy():
    """The sanitizer must catch derivability, not just column names."""
    builder = _builder()
    legacy = {
        "商家夥伴案例資料庫:r8:article": {
            "identity": _legacy_identity("商家夥伴案例資料庫:r8:article"),
            "entity_name": SANFENG_BRAND,
            "title": SANFENG_ARTICLE_TITLE,
            "asset_type": "article",
        }
    }

    with pytest.raises(builder.AuthorityBuildError):
        builder._assert_identity_not_source_derived(legacy)
    # The shipped identities pass the very same policy.
    builder._assert_identity_not_source_derived(
        {
            asset_id: {
                "identity": approved_asset_identity(
                    row["brand_name"], row["asset_title"], row["asset_type"]
                ),
                "entity_name": row["brand_name"],
                "title": row["asset_title"],
                "asset_type": row["asset_type"],
            }
            for asset_id, row in _source_rows_by_asset_id().items()
        }
    )


def test_the_builder_rejects_a_blocked_asset_in_the_approved_mapping():
    builder = _builder()
    blocked_id = _local_blocked_asset_ids()[0]
    overlay = slack_output_preview._build_asset_url_overlay(
        [], [{"asset_id": blocked_id, "action": "blocked"}], []
    )

    with pytest.raises(builder.AuthorityBuildError):
        builder._assert_blocked_never_approved(
            overlay,
            {blocked_id: {"identity": "0" * 32, "entity_name": "B", "title": "T", "asset_type": "article"}},
        )


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------


def _record_authority_reads(monkeypatch):
    return _record_reads_under(monkeypatch, _authority_dir())


def _record_reads_under(monkeypatch, directory):
    """Record every read of a file below ``directory``, whichever Path API performs it."""
    directory = Path(directory).resolve()
    reads = []

    for method_name in ("read_bytes", "read_text", "open"):
        original = getattr(Path, method_name)

        def make(original):
            def wrapper(self, *args, **kwargs):
                try:
                    if Path(self).resolve().is_relative_to(directory):
                        reads.append(Path(self).name)
                except OSError:
                    pass
                return original(self, *args, **kwargs)

            return wrapper

        monkeypatch.setattr(Path, method_name, make(original))
    return reads


def _authority_copy(tmp_path) -> Path:
    """A writable copy of the real packaged authority, so damage tests stay realistic."""
    destination = tmp_path / "authority"
    destination.mkdir(parents=True, exist_ok=True)
    for filename in (AUTHORITY_MANIFEST_FILENAME, *APPROVED_ASSET_URL_INPUTS):
        shutil.copyfile(_authority_dir() / filename, destination / filename)
    return destination


def _clean_source_tree(tmp_path) -> Path:
    """Copy the version-controlled source tree, so the build sees no local build residue."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
    )
    if listed.returncode != 0:
        pytest.skip("git is unavailable, cannot assemble a clean source tree")

    destination = tmp_path / "source"
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        origin = REPOSITORY_ROOT / relative
        if not origin.is_file() or origin.name.startswith("._"):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    return destination


def _build_wheel(tmp_path):
    """Build the real distributable from tracked files only; skip if the toolchain is absent."""
    source = _clean_source_tree(tmp_path)
    wheel_dir = tmp_path / "wheel"
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "-w", str(wheel_dir), str(source)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"wheel build unavailable in this environment: {build.stderr[-400:]}")
    wheels = list(wheel_dir.glob("marketing_knowledge_agent-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _is_opaque(value):
    return len(value) == 32 and set(value) <= set("0123456789abcdef")


def _write_test_manifest(root):
    inputs = []
    for filename in APPROVED_ASSET_URL_INPUTS:
        payload = (root / filename).read_bytes()
        inputs.append(
            {
                "relative_path": filename,
                "input_type": "file",
                "expected_size": len(payload),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    body = {"schema_version": 3, "authority": "approved_asset_urls", "inputs": inputs}
    (root / AUTHORITY_MANIFEST_FILENAME).write_text(
        json.dumps(dict(body, manifest_hash=_hash_json(body)), ensure_ascii=False),
        encoding="utf-8",
    )


def _local_blocked_asset_ids():
    """Recover the plaintext blocked identities from the local governance source, or skip.

    These live only in the reviewed local report. Nothing derived from them ships.
    """
    source = REPOSITORY_ROOT / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"
    if not source.is_file():
        pytest.skip("local governance source is not present in this checkout")
    rows = list(csv.DictReader(source.read_bytes().decode("utf-8-sig").splitlines()))
    blocked = sorted(
        row["asset_id"].strip() for row in rows if row["action"].strip() == "blocked"
    )
    assert blocked
    return blocked


def _source_rows_by_asset_id():
    """The reviewed Apply Preview rows, keyed by asset, or skip."""
    source = REPOSITORY_ROOT / "reports/asset_metadata_apply_preview/asset_apply_preview.csv"
    if not source.is_file():
        pytest.skip("local governance source is not present in this checkout")
    rows = {}
    for row in csv.DictReader(source.read_bytes().decode("utf-8-sig").splitlines()):
        rows.setdefault(row["asset_id"].strip(), {key: (value or "").strip() for key, value in row.items()})
    return rows


def _blocked_asset_with_approved_sibling():
    """A governance-blocked asset whose own source record also holds an approved one.

    This is the case that most needs proving: the merchant is publicly named by the sibling's
    approved URL, so the blocked asset is the one with something to lose.
    """
    approved = _source_rows_by_asset_id()
    by_record = {}
    for row in approved.values():
        by_record.setdefault(row["record_id"], row)
    for asset_id in _local_blocked_asset_ids():
        sibling = by_record.get(asset_id.rsplit(":", 1)[0])
        if sibling is not None:
            return asset_id, sibling
    pytest.skip("no blocked asset shares a source record with an approved one")


def _source_url(row):
    return row["proposed_value"]


def _source_lookup(row):
    record_id = row["record_id"].strip()
    return AssetLookup(
        record_id=record_id,
        asset_id=row["asset_id"].strip(),
        entity_name=row["brand_name"].strip(),
        title=row["asset_title"].strip(),
        asset_type=row["asset_type"].strip(),
    )


def _sanfeng_lookup(asset_type, title=None):
    if title is None:
        title = SANFENG_ARTICLE_TITLE if asset_type == "article" else SANFENG_VIDEO_TITLE
    return AssetLookup(
        record_id=SANFENG_RECORD,
        asset_id=f"{SANFENG_RECORD}:{asset_type}",
        entity_name=SANFENG_BRAND,
        title=title,
        asset_type=asset_type,
    )


def _legacy_identity(asset_id):
    """The retired v2 derivation, kept only so tests can prove it is no longer in use."""
    return hashlib.sha256(f"mka:approved-asset-url:v1:{asset_id}".encode("utf-8")).hexdigest()[:32]


def _builder():
    sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
    try:
        import build_approved_asset_url_authority as builder
    finally:
        sys.path.pop(0)
    return builder


def _packaged_rows(filename):
    text = (_authority_dir() / filename).read_bytes().decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def _packaged_header(filename):
    text = (_authority_dir() / filename).read_bytes().decode("utf-8-sig")
    return tuple(next(csv.reader(text.splitlines())))


def _forged_apply_rows_csv() -> bytes:
    header = (
        "record_id,asset_id,brand_name,asset_type,asset_title,field,current_value,proposed_value,"
        "review_decision,reviewer,reviewed_at,provenance,source_location,eligibility,"
        "governance_status,action,reason"
    )
    row = (
        f"{SANFENG_RECORD},{SANFENG_RECORD}:article,三風製麵,article,forged,asset_url,,"
        "https://attacker.example/pwn,approve,attacker,2026-01-01,forged,forged,"
        "ready_for_apply_preview,eligible,add,forged"
    )
    return ("﻿" + header + "\r\n" + row + "\r\n").encode("utf-8")


def _assert_payload_free_audit(audit_path):
    rows = list(csv.reader(Path(audit_path).read_text(encoding="utf-8").splitlines()))
    events = [row[1] for row in rows[1:] if len(row) > 1]
    assert APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE in events
    assert "slack_qa" in events
    failure_row = next(row for row in rows[1:] if row[1] == APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE)
    assert failure_row[-1] == ""
    joined = ",".join(failure_row)
    for leak in ("authority", "manifest", ".csv", "sha256", "http"):
        assert leak not in joined.casefold()


def _hash_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _structured_answer():
    """A 三風製麵 answer whose asset identities match the real packaged authority."""
    assets = [
        StructuredAsset(
            asset_type=asset_type,
            title=title,
            external_usage_status="可對外引用",
            source_record_id=SANFENG_RECORD,
            source_sheet="商家夥伴案例資料庫",
            source_row=8,
            citation_label=label,
        )
        for asset_type, title, label in (
            ("article", SANFENG_ARTICLE_TITLE, "[1]"),
            ("video", SANFENG_VIDEO_TITLE, "[2]"),
        )
    ]
    citations = []
    for title, label, asset_type in (
        (SANFENG_ARTICLE_TITLE, "[1]", "article"),
        (SANFENG_VIDEO_TITLE, "[2]", "video"),
    ):
        citation = Citation(
            label=label,
            title=title,
            source_path=f"synthetic:{title}",
            chunk_id=f"chunk-r8:{asset_type}",
            status="published",
            source_type="database",
            record_type="merchant_case",
            data_classification="public",
            can_quote_externally=True,
            publish_date="2026-07-01",
            source_sheet="商家夥伴案例資料庫",
            source_row=8,
            freshness_note="最新日期 2026-07-01",
        )
        citations.append(citation)
    structured = StructuredRetrievalResult(
        query_plan={"raw_query": "三風製麵", "supported_constraints": []},
        matched_entities=[
            StructuredEntity(entity_type="merchant", entity_name="三風製麵", assets=assets)
        ],
        total_entities=1,
        total_assets=2,
    )
    answer = GeneratedAnswer(
        question="三風製麵",
        answer="unused",
        citations=citations,
        warnings=[],
        governance_checked=True,
        structured_result=structured,
    )
    return assets, citations, answer
