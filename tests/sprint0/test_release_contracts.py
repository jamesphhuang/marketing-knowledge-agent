from __future__ import annotations

import hashlib
import inspect
import json
import socket
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Union

import pytest

import marketing_knowledge_agent.captured_chunks as captured_chunks
import marketing_knowledge_agent.release_contracts as release_contracts
from marketing_knowledge_agent.canonical_models import (
    AssetType,
    BrandId,
    BrandIdentityDecision,
    CanonicalSourceLineage,
    ContentAssetKey,
    LifecycleStatus,
    MetricId,
    PublishEligibility,
    ReviewStatus,
    SourceRecord,
    SourceRecordId,
)
from marketing_knowledge_agent.capture_policy import (
    CaptureMode,
    CapturePolicyDecision,
    FetchFailureCategory,
    PolicyDecisionReason,
)
from marketing_knowledge_agent.captured_chunks import (
    CapturedChunk,
    CapturedChunkSourceLineage,
    SectionAnchor,
    SyntheticChunkSpan,
    build_captured_chunk,
)
from marketing_knowledge_agent.captured_content import (
    AuthorityRole,
    CapturedContent,
    CapturedContentId,
    CaptureStatus,
    EvidenceRelationshipId,
    SafeHttpMetadata,
    Section,
)
from marketing_knowledge_agent.cell_normalization import (
    InheritanceReason,
    SourceFieldLineage,
    SourceLineage,
)
from marketing_knowledge_agent.content_hashing import (
    ApprovedLkgFreshnessPolicy,
    CaptureContentHash,
    CaptureRevisionRef,
    LkgEligibilityInput,
    StaleLkgCandidate,
    evaluate_lkg_reuse,
)
from marketing_knowledge_agent.link_resolution import (
    AssetSourceSlot,
    LinkCandidate,
    LinkSource,
)
from marketing_knowledge_agent.release_contracts import (
    ArtifactRef,
    ArtifactRole,
    CanonicalReleaseInputs,
    CapturedRevisionManifestEntry,
    ChunkSetHash,
    ReleaseContractError,
    ReleaseId,
    ReleaseManifest,
    ReleaseManifestHash,
    ReleasePublishState,
    build_release_manifest,
    compute_release_manifest_hash,
    serialize_release_manifest,
)
from marketing_knowledge_agent.url_safety import validate_and_canonicalize_url


UTC = timezone.utc
BATCH = "SYNTHETIC-WP14-BATCH"
SOURCE_FINGERPRINT = "sha256:" + "a" * 64
CAPTURED_AT = datetime(2026, 8, 1, 1, 2, 3, 4005, tzinfo=UTC)
LAST_SUCCESS = datetime(2026, 8, 8, 2, 3, 4, 5006, tzinfo=UTC)
LAST_ATTEMPT = datetime(2026, 8, 10, 3, 4, 5, 6007, tzinfo=UTC)
CREATED_AT = datetime(2026, 8, 11, 4, 5, 6, tzinfo=UTC)
BODY_SENTINEL = "SYNTHETIC_WP14_BODY_SECRET_17A9"
TITLE_SENTINEL = "SYNTHETIC_WP14_TITLE_SECRET_37C2"
URL_SENTINEL = "synthetic-wp14-url-secret-51d4"
CHUNK_TEXT_SENTINEL = "SYNTHETIC_WP14_CHUNK_SECRET_83E6"
BODY = f"{BODY_SENTINEL} Intro text. {CHUNK_TEXT_SENTINEL} Closing text."
CAPTURE_HASH = CaptureContentHash("sha256:" + "1" * 64)
PARSER_VERSION = "synthetic-parser-v1"
POLICY_VERSION = "synthetic-capture-policy-v1"


class _MutableSyntheticTimezone(tzinfo):
    def __init__(self, offset: timedelta) -> None:
        self.offset = offset

    def utcoffset(self, value):
        return self.offset

    def dst(self, value):
        return timedelta(0)


class _FailingSyntheticTimezone(tzinfo):
    def __init__(self) -> None:
        self.utcoffset_calls = 0

    def utcoffset(self, value):
        self.utcoffset_calls += 1
        if self.utcoffset_calls > 1:
            raise RuntimeError("SYNTHETIC_TZ_EXCEPTION_PAYLOAD")
        return timedelta(0)

    def dst(self, value):
        return timedelta(0)


def _canonical_url(captured_content_id: str, suffix: str = "canonical"):
    raw_url = f"https://example.test/{captured_content_id}/{suffix}"
    result = validate_and_canonicalize_url(
        LinkCandidate(
            raw_url=raw_url,
            source=LinkSource.CELL_HYPERLINK,
            asset_source_slot=AssetSourceSlot.ARTICLE,
            lineage=SourceLineage(
                spreadsheet_id="synthetic-spreadsheet",
                sheet_id=114,
                sheet_title="Synthetic WP14",
                sheet_hidden=False,
                source_row_index=6,
                source_column_index=7,
                source_fingerprint=SOURCE_FINGERPRINT,
                sync_batch_id=BATCH,
            ),
            field_lineage=SourceFieldLineage(
                field_name="article",
                target_row_index=6,
                target_column_index=7,
                value_row_index=6,
                value_column_index=7,
                merge_anchor_row_index=None,
                merge_anchor_column_index=None,
                merge_range=None,
                inherited_from_merge=False,
                inheritance_reason=InheritanceReason.LOCAL,
            ),
        )
    )
    assert result.canonical_url is not None
    return result.canonical_url


def _lineage(*, sync_batch_id: str = BATCH) -> CanonicalSourceLineage:
    return CanonicalSourceLineage(
        spreadsheet_id_hash="sha256:" + "b" * 64,
        sheet_id=114,
        sheet_title="Synthetic WP14",
        source_row=7,
        source_columns={"source_record_id": "M", "article": "H"},
        source_ranges={"article": "H7"},
        source_fingerprint=SOURCE_FINGERPRINT,
        sync_batch_id=sync_batch_id,
    )


def _source_record(
    source_record_id: str,
    *,
    sync_batch_id: str = BATCH,
) -> SourceRecord:
    numeric_suffix = source_record_id.split("-")[-1]
    return SourceRecord(
        source_record_id=SourceRecordId(source_record_id),
        brand_identity=BrandIdentityDecision(
            review_status=ReviewStatus.APPROVED,
            brand_id=BrandId(f"BRD-{numeric_suffix}"),
        ),
        interview_year=2026,
        source_name="Synthetic Source Record",
        sales_category_lv1=None,
        sales_category_lv2=None,
        tags=(),
        lifecycle_status=LifecycleStatus.ACTIVE,
        review_status=ReviewStatus.APPROVED,
        publish_eligibility=PublishEligibility.ELIGIBLE,
        source_lineage=_lineage(sync_batch_id=sync_batch_id),
    )


def _captured_payload(
    *,
    captured_content_id: str = "capture-wp14-001",
    source_record_id: str = "MREC-0014",
    asset_type: AssetType = AssetType.ARTICLE,
    authority_role: AuthorityRole = AuthorityRole.PRIMARY_CONTENT,
    metric_id: str = "MET-0014",
    evidence_relationship_id: str = "evidence-wp14-001",
    capture_status: CaptureStatus = CaptureStatus.SUCCESS,
    searchable: bool = True,
    sync_batch_id: str = BATCH,
    lineage_sync_batch_id: Optional[str] = None,
    content_hash: str = str(CAPTURE_HASH),
    parser_version: str = PARSER_VERSION,
    last_capture_attempt_at: datetime = LAST_ATTEMPT,
):
    full_text = capture_status in (CaptureStatus.SUCCESS, CaptureStatus.STALE)
    primary = authority_role is AuthorityRole.PRIMARY_CONTENT
    return {
        "captured_content_id": CapturedContentId(captured_content_id),
        "asset_key": (
            ContentAssetKey(SourceRecordId(source_record_id), asset_type)
            if primary
            else None
        ),
        "metric_id": None if primary else MetricId(metric_id),
        "evidence_relationship_id": (
            None
            if primary
            else EvidenceRelationshipId(evidence_relationship_id)
        ),
        "authority_role": authority_role,
        "source_url": _canonical_url(captured_content_id, "source"),
        "canonical_url": _canonical_url(captured_content_id),
        "source_domain": "example.test",
        "content_type": "text/html",
        "title": TITLE_SENTINEL,
        "clean_body": BODY if full_text else None,
        "section_structure": (
            (Section(heading="Synthetic", text=BODY),) if full_text else ()
        ),
        "capture_status": capture_status,
        "captured_at": CAPTURED_AT if full_text else None,
        "last_successful_capture_at": LAST_SUCCESS,
        "last_capture_attempt_at": last_capture_attempt_at,
        "content_hash": content_hash if full_text else None,
        "parser_version": parser_version if full_text else None,
        "source_http_metadata": SafeHttpMetadata(status_code=200),
        "previous_content_hash": None,
        "searchable": searchable,
        "source_lineage": _lineage(
            sync_batch_id=(
                sync_batch_id
                if lineage_sync_batch_id is None
                else lineage_sync_batch_id
            )
        ),
        "sync_batch_id": sync_batch_id,
    }


def _captured(**overrides) -> CapturedContent:
    return CapturedContent(**_captured_payload(**overrides))


def _revision(parent: CapturedContent) -> CaptureRevisionRef:
    return CaptureRevisionRef(
        captured_content_id=parent.captured_content_id,
        content_hash=CaptureContentHash(parent.content_hash),
        parser_version=parent.parser_version,
    )


def _policy(
    *,
    version: str = POLICY_VERSION,
    mode: CaptureMode = CaptureMode.FULL_TEXT,
) -> CapturePolicyDecision:
    reason = (
        PolicyDecisionReason.SHOPLINE_OWNED
        if mode is CaptureMode.FULL_TEXT
        else PolicyDecisionReason.NEEDS_POLICY
    )
    return CapturePolicyDecision(
        mode=mode,
        reason=reason,
        policy_version=version,
    )


def _span(*, text: str = CHUNK_TEXT_SENTINEL, anchor: str = "section-alpha"):
    start = BODY.index(text)
    return SyntheticChunkSpan(
        text=text,
        start=start,
        end=start + len(text),
        section_anchor=SectionAnchor(anchor),
        section_heading="Synthetic",
        ordinal=0,
    )


def _chunk(
    parent: CapturedContent,
    *,
    span: Optional[SyntheticChunkSpan] = None,
    stale_input: Optional[LkgEligibilityInput] = None,
    stale_result=None,
) -> CapturedChunk:
    return build_captured_chunk(
        captured_content=parent,
        revision_ref=_revision(parent),
        span=span or _span(),
        primary_source_record=(
            _source_record(
                str(parent.asset_key.source_record_id),
                sync_batch_id=parent.sync_batch_id,
            )
            if parent.authority_role is AuthorityRole.PRIMARY_CONTENT
            else None
        ),
        stale_lkg_input=stale_input,
        stale_lkg_result=stale_result,
    )


def _artifacts():
    return (
        ArtifactRef(
            ArtifactRole.OBSIDIAN_TREE,
            "obsidian_tree",
            "sha256:" + "2" * 64,
        ),
        ArtifactRef(
            ArtifactRole.OFFICIAL_SQLITE,
            "official.sqlite",
            "sha256:" + "3" * 64,
        ),
        ArtifactRef(
            ArtifactRole.OFFICIAL_VECTOR,
            "official.vector",
            "sha256:" + "4" * 64,
        ),
    )


_DEFAULT = object()


def _inputs(
    *,
    release_id: Union[str, ReleaseId] = "release-wp14-alpha",
    captured_contents=None,
    capture_policy_decisions=None,
    stale_proofs=(),
    captured_chunks=None,
    artifacts=None,
    source_row_counts=((9, 1), (3, 2)),
    entity_counts=None,
    excluded_counts=(("unavailable", 0), ("oral_only", 0)),
    validator_versions=(("url_safety", "wp7-v1"), ("capture_policy", "wp10-v1")),
    previous_release=None,
    created_at=CREATED_AT,
    metadata_sync_batch_id=BATCH,
):
    parents = (
        (_captured(),)
        if captured_contents is None
        else captured_contents
    )
    chunks = (
        tuple(_chunk(parent) for parent in parents)
        if captured_chunks is None
        else captured_chunks
    )
    policies = (
        tuple((parent.captured_content_id, _policy()) for parent in parents)
        if capture_policy_decisions is None
        else capture_policy_decisions
    )
    counts = (
        (("chunk", len(chunks)), ("brand", 1), ("captured_content", len(parents)))
        if entity_counts is None
        else entity_counts
    )
    return CanonicalReleaseInputs(
        release_id=(
            release_id if type(release_id) is ReleaseId else ReleaseId(release_id)
        ),
        metadata_sync_batch_id=metadata_sync_batch_id,
        source_fingerprint=SOURCE_FINGERPRINT,
        source_row_counts=source_row_counts,
        entity_counts=counts,
        excluded_counts=excluded_counts,
        captured_contents=parents,
        capture_policy_decisions=policies,
        stale_proofs=stale_proofs,
        captured_chunks=chunks,
        artifacts=_artifacts() if artifacts is None else artifacts,
        validator_versions=validator_versions,
        previous_release=previous_release,
        created_at=created_at,
    )


def _stale_bundle(
    *,
    captured_content_id="capture-wp14-stale-001",
    source_record_id="MREC-0114",
    last_attempt=LAST_ATTEMPT,
):
    previous = _captured(
        captured_content_id=captured_content_id,
        source_record_id=source_record_id,
        last_capture_attempt_at=LAST_SUCCESS,
    )
    decision = _policy()
    input_value = LkgEligibilityInput(
        current_canonical_url=previous.canonical_url,
        previous_success=previous,
        current_capture_policy=decision,
        current_failure_category=FetchFailureCategory.TEMPORARY,
        governance_allowed=True,
        identity_reconciled=True,
        freshness_policy=ApprovedLkgFreshnessPolicy(
            policy_version="synthetic-freshness-v1",
            max_age=timedelta(days=30),
        ),
        current_attempt_at=last_attempt,
    )
    result = evaluate_lkg_reuse(input_value)
    stale = _captured(
        captured_content_id=captured_content_id,
        source_record_id=source_record_id,
        capture_status=CaptureStatus.STALE,
        last_capture_attempt_at=last_attempt,
    )
    chunk = _chunk(
        stale,
        stale_input=input_value,
        stale_result=result,
    )
    return stale, decision, input_value, result, chunk


def _input_values(inputs: CanonicalReleaseInputs):
    return {field.name: getattr(inputs, field.name) for field in fields(inputs)}


def _entry_values(entry: CapturedRevisionManifestEntry):
    return {field.name: getattr(entry, field.name) for field in fields(entry)}


def _manifest_values(manifest: ReleaseManifest):
    return {field.name: getattr(manifest, field.name) for field in fields(manifest)}


def _assert_error(code, function, /, *args, **kwargs):
    with pytest.raises(ReleaseContractError) as captured:
        function(*args, **kwargs)
    assert captured.value.code == code
    assert str(captured.value) == code


def _two_primary_parents():
    first = _captured(
        captured_content_id="capture-wp14-001",
        source_record_id="MREC-0014",
        asset_type=AssetType.ARTICLE,
    )
    second = _captured(
        captured_content_id="capture-wp14-002",
        source_record_id="MREC-0015",
        asset_type=AssetType.VIDEO,
    )
    chunks = (_chunk(first), _chunk(second))
    policies = (
        (first.captured_content_id, _policy()),
        (second.captured_content_id, _policy()),
    )
    return (first, second), chunks, policies


def test_public_surface_is_exact():
    assert release_contracts.__all__ == [
        "ReleaseContractError",
        "ReleaseId",
        "ReleaseManifestHash",
        "ChunkSetHash",
        "ArtifactRole",
        "ArtifactRef",
        "CapturedRevisionManifestEntry",
        "CanonicalReleaseInputs",
        "ReleaseManifest",
        "ReleasePublishState",
        "build_release_manifest",
        "serialize_release_manifest",
        "compute_release_manifest_hash",
    ]


@pytest.mark.parametrize(
    "value",
    ["synthetic-release", "opaque:release:alpha", "文字-release", "0"],
)
def test_release_id_accepts_opaque_stable_text(value):
    release_id = ReleaseId(value)

    assert type(release_id) is ReleaseId
    assert str(release_id) == value


@pytest.mark.parametrize(
    "value",
    ["", "   ", " release", "release ", "release\n", "release\x00", 1, True, None],
)
def test_release_id_rejects_invalid_text(value):
    _assert_error("RELEASE_ID_INVALID", ReleaseId, value)


@pytest.mark.parametrize(
    ("hash_type", "valid"),
    [
        (ReleaseManifestHash, "release-manifest:v1:sha256:" + "a" * 64),
        (ChunkSetHash, "chunkset:v1:sha256:" + "b" * 64),
    ],
)
def test_public_hash_types_accept_exact_wire(hash_type, valid):
    assert str(hash_type(valid)) == valid


@pytest.mark.parametrize(
    ("hash_type", "value"),
    [
        (ReleaseManifestHash, "release-manifest:v1:sha256:" + "A" * 64),
        (ReleaseManifestHash, "release-manifest:v1:sha256:" + "a" * 63),
        (ReleaseManifestHash, "sha256:" + "a" * 64),
        (ReleaseManifestHash, " release-manifest:v1:sha256:" + "a" * 64),
        (ChunkSetHash, "chunkset:v1:sha256:" + "A" * 64),
        (ChunkSetHash, "chunkset:v1:sha256:" + "b" * 65),
        (ChunkSetHash, "sha256:" + "b" * 64),
        (ChunkSetHash, "chunkset:v1:sha256:" + "b" * 64 + " "),
    ],
)
def test_public_hash_types_reject_noncanonical_wire(hash_type, value):
    with pytest.raises(ReleaseContractError):
        hash_type(value)


def test_artifact_role_taxonomy_is_exact_and_has_no_fts_role():
    assert [(item.name, item.value) for item in ArtifactRole] == [
        ("OBSIDIAN_TREE", "obsidian_tree"),
        ("OFFICIAL_SQLITE", "official_sqlite"),
        ("OFFICIAL_VECTOR", "official_vector"),
    ]
    assert "official_fts" not in {item.value for item in ArtifactRole}


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        " /absolute",
        "/absolute",
        "relative/path ",
        "relative\\path",
        ".",
        "..",
        "relative/./path",
        "relative/../path",
        "relative/path\n",
    ],
)
def test_artifact_ref_rejects_invalid_relative_paths(path):
    _assert_error(
        "ARTIFACT_RELATIVE_PATH_INVALID",
        ArtifactRef,
        ArtifactRole.OBSIDIAN_TREE,
        path,
        "sha256:" + "2" * 64,
    )


@pytest.mark.parametrize(
    "checksum",
    [
        "",
        "2" * 64,
        "sha256:" + "2" * 63,
        "sha256:" + "2" * 65,
        "sha256:" + "A" * 64,
        "sha512:" + "2" * 64,
        " sha256:" + "2" * 64,
    ],
)
def test_artifact_ref_rejects_invalid_checksum(checksum):
    _assert_error(
        "ARTIFACT_CHECKSUM_INVALID",
        ArtifactRef,
        ArtifactRole.OBSIDIAN_TREE,
        "obsidian_tree",
        checksum,
    )


def test_artifact_ref_is_direct_frozen_structural_dto():
    artifact = _artifacts()[0]

    assert [field.name for field in fields(ArtifactRef)] == [
        "role",
        "relative_path",
        "checksum",
    ]
    with pytest.raises(Exception):
        artifact.relative_path = "changed"


@pytest.mark.parametrize(
    "field_name",
    [
        "source_row_counts",
        "entity_counts",
        "excluded_counts",
        "captured_contents",
        "capture_policy_decisions",
        "stale_proofs",
        "captured_chunks",
        "artifacts",
        "validator_versions",
    ],
)
def test_canonical_release_inputs_collections_are_tuple_only(field_name):
    values = _input_values(_inputs())
    values[field_name] = list(values[field_name])

    with pytest.raises(ReleaseContractError, match="TUPLE_REQUIRED"):
        CanonicalReleaseInputs(**values)


def test_policy_and_stale_association_entries_require_exact_tuples():
    values = _input_values(_inputs())
    values["capture_policy_decisions"] = (
        list(values["capture_policy_decisions"][0]),
    )
    with pytest.raises(ReleaseContractError, match="ASSOCIATION_INVALID"):
        CanonicalReleaseInputs(**values)

    stale, decision, input_value, result, chunk = _stale_bundle()
    values = _input_values(
        _inputs(
            captured_contents=(stale,),
            capture_policy_decisions=((stale.captured_content_id, decision),),
            stale_proofs=((stale.captured_content_id, input_value, result),),
            captured_chunks=(chunk,),
        )
    )
    values["stale_proofs"] = (list(values["stale_proofs"][0]),)
    with pytest.raises(ReleaseContractError, match="ASSOCIATION_INVALID"):
        CanonicalReleaseInputs(**values)


@pytest.mark.parametrize(
    "fingerprint",
    [
        "",
        "a" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        " sha256:" + "a" * 64,
        "sha512:" + "a" * 64,
    ],
)
def test_source_fingerprint_requires_wp2_wire(fingerprint):
    values = _input_values(_inputs())
    values["source_fingerprint"] = fingerprint
    _assert_error(
        "SOURCE_FINGERPRINT_INVALID",
        CanonicalReleaseInputs,
        **values,
    )


@pytest.mark.parametrize(
    "source_row_counts",
    [
        ((True, 1),),
        ((-1, 1),),
        ((1, True),),
        ((1, -1),),
        ((1, 1), (1, 2)),
        ((1, 1, 2),),
    ],
)
def test_source_row_counts_validate_types_values_and_uniqueness(source_row_counts):
    values = _input_values(_inputs())
    values["source_row_counts"] = source_row_counts
    with pytest.raises(ReleaseContractError):
        CanonicalReleaseInputs(**values)


@pytest.mark.parametrize(
    "named_counts",
    [
        (("UPPER", 1),),
        (("two-words", 1),),
        (("_leading", 1),),
        (("", 1),),
        (("valid", True),),
        (("valid", -1),),
        (("valid", 1), ("valid", 2)),
        (("a" * 65, 1),),
    ],
)
def test_named_counts_validate_names_values_and_uniqueness(named_counts):
    for field_name in ("entity_counts", "excluded_counts"):
        values = _input_values(_inputs())
        values[field_name] = named_counts
        with pytest.raises(ReleaseContractError):
            CanonicalReleaseInputs(**values)


def test_builder_canonicalizes_counts_and_validator_versions():
    manifest = build_release_manifest(_inputs())

    assert manifest.source_row_counts == ((3, 2), (9, 1))
    assert manifest.entity_counts == (
        ("brand", 1),
        ("captured_content", 1),
        ("chunk", 1),
    )
    assert manifest.excluded_counts == (("oral_only", 0), ("unavailable", 0))
    assert manifest.validator_versions == (
        ("capture_policy", "wp10-v1"),
        ("release_contract", "wp14-v1"),
        ("url_safety", "wp7-v1"),
    )


def test_caller_cannot_override_release_contract_validator():
    values = _input_values(_inputs())
    values["validator_versions"] = (("release_contract", "caller-value"),)

    _assert_error(
        "RELEASE_VALIDATOR_OVERRIDE_NOT_ALLOWED",
        CanonicalReleaseInputs,
        **values,
    )


def test_validator_versions_require_strict_unique_text():
    for versions in (
        (("validator", ""),),
        ((" validator", "v1"),),
        (("validator", "v1\n"),),
        (("validator", "v1"), ("validator", "v2")),
    ):
        values = _input_values(_inputs())
        values["validator_versions"] = versions
        with pytest.raises(ReleaseContractError):
            CanonicalReleaseInputs(**values)


def test_complete_artifact_set_is_required_and_canonicalized():
    manifest = build_release_manifest(_inputs(artifacts=tuple(reversed(_artifacts()))))

    assert [item.role.value for item in manifest.artifacts] == [
        "obsidian_tree",
        "official_sqlite",
        "official_vector",
    ]


def test_missing_artifact_is_rejected():
    _assert_error(
        "ARTIFACT_SET_INCOMPLETE",
        build_release_manifest,
        _inputs(artifacts=_artifacts()[:-1]),
    )


def test_duplicate_artifact_role_is_rejected():
    artifacts = _artifacts()
    _assert_error(
        "ARTIFACT_ROLE_DUPLICATE",
        build_release_manifest,
        _inputs(artifacts=artifacts + (artifacts[0],)),
    )


def test_duplicate_captured_content_id_is_rejected():
    parent = _captured()
    _assert_error(
        "CAPTURED_CONTENT_ID_DUPLICATE",
        build_release_manifest,
        _inputs(captured_contents=(parent, parent), captured_chunks=()),
    )


def test_duplicate_primary_parent_is_rejected():
    first = _captured(captured_content_id="capture-wp14-parent-a")
    second = _captured(captured_content_id="capture-wp14-parent-b")
    _assert_error(
        "CAPTURE_AUTHORITY_PARENT_DUPLICATE",
        build_release_manifest,
        _inputs(captured_contents=(first, second), captured_chunks=()),
    )


def test_duplicate_evidence_parent_is_rejected():
    first = _captured(
        captured_content_id="capture-wp14-evidence-a",
        authority_role=AuthorityRole.EVIDENCE,
    )
    second = _captured(
        captured_content_id="capture-wp14-evidence-b",
        authority_role=AuthorityRole.EVIDENCE,
    )
    _assert_error(
        "CAPTURE_AUTHORITY_PARENT_DUPLICATE",
        build_release_manifest,
        _inputs(captured_contents=(first, second), captured_chunks=()),
    )


def test_policy_decision_missing_extra_and_duplicate_are_rejected():
    parent = _captured()
    _assert_error(
        "CAPTURE_POLICY_DECISION_MISSING",
        build_release_manifest,
        _inputs(capture_policy_decisions=()),
    )
    extra_id = CapturedContentId("capture-wp14-extra-policy")
    _assert_error(
        "CAPTURE_POLICY_DECISION_EXTRA",
        build_release_manifest,
        _inputs(
            capture_policy_decisions=(
                (parent.captured_content_id, _policy()),
                (extra_id, _policy()),
            )
        ),
    )
    _assert_error(
        "CAPTURE_POLICY_DECISION_DUPLICATE",
        build_release_manifest,
        _inputs(
            capture_policy_decisions=(
                (parent.captured_content_id, _policy()),
                (parent.captured_content_id, _policy()),
            )
        ),
    )


def test_policy_mode_must_be_full_text():
    parent = _captured()
    _assert_error(
        "CAPTURE_POLICY_MODE_NOT_FULL_TEXT",
        build_release_manifest,
        _inputs(
            capture_policy_decisions=(
                (
                    parent.captured_content_id,
                    _policy(mode=CaptureMode.METADATA_ONLY),
                ),
            )
        ),
    )


def test_mixed_policy_versions_are_rejected():
    parents, chunks, _ = _two_primary_parents()
    policies = (
        (parents[0].captured_content_id, _policy(version="policy-v1")),
        (parents[1].captured_content_id, _policy(version="policy-v2")),
    )
    _assert_error(
        "CAPTURE_POLICY_VERSION_MIXED",
        build_release_manifest,
        _inputs(
            captured_contents=parents,
            captured_chunks=chunks,
            capture_policy_decisions=policies,
        ),
    )


def test_mixed_parser_versions_are_rejected():
    first = _captured(
        captured_content_id="capture-wp14-parser-a",
        source_record_id="MREC-0014",
    )
    second = _captured(
        captured_content_id="capture-wp14-parser-b",
        source_record_id="MREC-0015",
        parser_version="synthetic-parser-v2",
    )
    policies = (
        (first.captured_content_id, _policy()),
        (second.captured_content_id, _policy()),
    )
    _assert_error(
        "PARSER_VERSION_MIXED",
        build_release_manifest,
        _inputs(
            captured_contents=(first, second),
            captured_chunks=(),
            capture_policy_decisions=policies,
        ),
    )


def test_metadata_batch_mismatch_is_rejected():
    parent = _captured(
        sync_batch_id="SYNTHETIC-OTHER-BATCH",
        lineage_sync_batch_id="SYNTHETIC-OTHER-BATCH",
    )
    _assert_error(
        "METADATA_SYNC_BATCH_MISMATCH",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=()),
    )


def test_lineage_batch_mismatch_is_rejected_even_for_unsafe_wp9_construct():
    payload = _captured_payload()
    payload["source_lineage"] = _lineage(sync_batch_id="SYNTHETIC-OTHER-BATCH")
    unsafe = CapturedContent.model_construct(**payload)

    _assert_error(
        "METADATA_SYNC_BATCH_MISMATCH",
        build_release_manifest,
        _inputs(captured_contents=(unsafe,), captured_chunks=()),
    )


def test_success_builds_body_free_revision_entry():
    manifest = build_release_manifest(_inputs())
    entry = manifest.captured_revisions[0]

    assert type(entry) is CapturedRevisionManifestEntry
    assert entry.capture_status is CaptureStatus.SUCCESS
    assert entry.freshness_policy_version is None
    assert entry.revision_ref == _revision(_inputs().captured_contents[0])
    assert entry.chunk_count == 1
    assert [field.name for field in fields(entry)] == [
        "revision_ref",
        "capture_status",
        "authority_role",
        "asset_key",
        "metric_id",
        "evidence_relationship_id",
        "sync_batch_id",
        "captured_at",
        "last_successful_capture_at",
        "last_capture_attempt_at",
        "freshness_policy_version",
        "chunk_count",
        "chunk_set_hash",
    ]


def test_success_rejects_stale_proof():
    parent = _captured()
    stale, _, input_value, result, _ = _stale_bundle()
    del stale
    _assert_error(
        "STALE_PROOF_EXTRA",
        build_release_manifest,
        _inputs(
            stale_proofs=((parent.captured_content_id, input_value, result),),
        ),
    )


def test_stale_evaluator_issued_proof_builds_entry():
    stale, decision, input_value, result, chunk = _stale_bundle()
    manifest = build_release_manifest(
        _inputs(
            captured_contents=(stale,),
            capture_policy_decisions=((stale.captured_content_id, decision),),
            stale_proofs=((stale.captured_content_id, input_value, result),),
            captured_chunks=(chunk,),
        )
    )
    entry = manifest.captured_revisions[0]

    assert entry.capture_status is CaptureStatus.STALE
    assert entry.freshness_policy_version == "synthetic-freshness-v1"
    assert entry.revision_ref == _revision(stale)


def test_stale_missing_proof_is_rejected():
    stale, decision, _, _, chunk = _stale_bundle()
    _assert_error(
        "STALE_PROOF_MISSING",
        build_release_manifest,
        _inputs(
            captured_contents=(stale,),
            capture_policy_decisions=((stale.captured_content_id, decision),),
            captured_chunks=(chunk,),
        ),
    )


def test_stale_cross_target_proof_is_rejected():
    stale_a, _, input_a, result_a, _ = _stale_bundle(
        captured_content_id="capture-wp14-stale-a",
        source_record_id="MREC-0114",
    )
    stale_b, decision_b, input_b, result_b, chunk_b = _stale_bundle(
        captured_content_id="capture-wp14-stale-b",
        source_record_id="MREC-0115",
    )
    del stale_a, input_b, result_b
    _assert_error(
        "STALE_PROOF_BINDING_MISMATCH",
        build_release_manifest,
        _inputs(
            captured_contents=(stale_b,),
            capture_policy_decisions=((stale_b.captured_content_id, decision_b),),
            stale_proofs=((stale_b.captured_content_id, input_a, result_a),),
            captured_chunks=(chunk_b,),
        ),
    )


def test_stale_mismatched_attempt_time_is_rejected():
    stale, decision, _, _, chunk = _stale_bundle()
    _, _, other_input, other_result, _ = _stale_bundle(
        last_attempt=LAST_ATTEMPT + timedelta(seconds=1)
    )
    _assert_error(
        "STALE_PROOF_BINDING_MISMATCH",
        build_release_manifest,
        _inputs(
            captured_contents=(stale,),
            capture_policy_decisions=((stale.captured_content_id, decision),),
            stale_proofs=((stale.captured_content_id, other_input, other_result),),
            captured_chunks=(chunk,),
        ),
    )


def test_hand_made_stale_candidate_cannot_authorize_release():
    stale, decision, input_value, result, chunk = _stale_bundle()
    candidate = StaleLkgCandidate(
        revision_ref=_revision(stale),
        capture_status=CaptureStatus.STALE,
        captured_at=CAPTURED_AT,
        last_successful_capture_at=LAST_SUCCESS,
        last_capture_attempt_at=LAST_ATTEMPT,
        previous_content_hash=None,
        searchable=True,
        freshness_policy_version="synthetic-freshness-v1",
    )
    values = _input_values(
        _inputs(
            captured_contents=(stale,),
            capture_policy_decisions=((stale.captured_content_id, decision),),
            stale_proofs=((stale.captured_content_id, input_value, result),),
            captured_chunks=(chunk,),
        )
    )
    values["stale_proofs"] = ((stale.captured_content_id, input_value, candidate),)

    _assert_error(
        "STALE_PROOF_RESULT_INVALID",
        CanonicalReleaseInputs,
        **values,
    )


def test_wp14_does_not_import_or_call_lkg_or_policy_evaluators():
    source = inspect.getsource(release_contracts)

    assert "evaluate_lkg_reuse" not in source
    assert "evaluate_capture_policy" not in source
    assert "compose_stale_lkg" in source


@pytest.mark.parametrize(
    "status",
    [
        CaptureStatus.UNAVAILABLE,
        CaptureStatus.BLOCKED,
        CaptureStatus.METADATA_ONLY,
        CaptureStatus.NEEDS_REVIEW,
    ],
)
def test_bodyless_capture_statuses_are_rejected(status):
    parent = _captured(capture_status=status, searchable=False)
    _assert_error(
        "CAPTURE_STATUS_NOT_RELEASE_PINNED",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=()),
    )


def test_non_searchable_full_text_capture_is_rejected():
    parent = _captured(searchable=False)
    _assert_error(
        "CAPTURED_CONTENT_NOT_SEARCHABLE",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=()),
    )


def test_zero_capture_release_fails_closed_without_default_versions():
    _assert_error(
        "RELEASE_CAPTURED_CONTENT_REQUIRED",
        build_release_manifest,
        _inputs(
            captured_contents=(),
            capture_policy_decisions=(),
            captured_chunks=(),
            entity_counts=(("captured_content", 0), ("chunk", 0)),
        ),
    )


def test_invalid_wp9_string_hash_maps_to_release_error():
    parent = _captured(content_hash="synthetic-not-a-capture-hash")
    _assert_error(
        "CAPTURE_REVISION_INVALID",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=()),
    )


def test_every_revision_requires_at_least_one_chunk():
    _assert_error(
        "CAPTURED_REVISION_CHUNKS_MISSING",
        build_release_manifest,
        _inputs(captured_chunks=(), entity_counts=(("captured_content", 1), ("chunk", 0))),
    )


def test_orphan_chunk_is_rejected():
    parent = _captured()
    other = _captured(
        captured_content_id="capture-wp14-orphan",
        source_record_id="MREC-0099",
    )
    _assert_error(
        "CAPTURED_CHUNK_ORPHAN",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=(_chunk(other),)),
    )


def test_chunk_revision_mismatch_is_rejected():
    parent = _captured()
    alternate = _captured(
        content_hash="sha256:" + "9" * 64,
        parser_version="synthetic-parser-v2",
    )
    _assert_error(
        "CAPTURED_CHUNK_REVISION_MISMATCH",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=(_chunk(alternate),)),
    )


def _forged_chunk(chunk: CapturedChunk, **metadata_overrides) -> CapturedChunk:
    metadata_values = {
        field.name: getattr(chunk.metadata, field.name)
        for field in fields(chunk.metadata)
    }
    metadata_values.update(metadata_overrides)
    metadata = captured_chunks._create_captured_chunk_metadata(**metadata_values)
    return captured_chunks._create_captured_chunk(text=chunk.text, metadata=metadata)


def test_chunk_authority_mismatch_is_rejected():
    parent = _captured()
    chunk = _chunk(parent)
    forged = _forged_chunk(
        chunk,
        asset_key=None,
        metric_id=MetricId("MET-0099"),
        evidence_relationship_id=EvidenceRelationshipId("evidence-wp14-099"),
        brand_id=None,
        source_record_id=None,
        authority_role=AuthorityRole.EVIDENCE,
    )
    _assert_error(
        "CAPTURED_CHUNK_AUTHORITY_MISMATCH",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=(forged,)),
    )


def test_chunk_parent_mismatch_is_rejected():
    parent = _captured()
    chunk = _chunk(parent)
    other_key = ContentAssetKey(SourceRecordId("MREC-0099"), AssetType.ARTICLE)
    forged = _forged_chunk(
        chunk,
        asset_key=other_key,
        source_record_id=SourceRecordId("MREC-0099"),
    )
    _assert_error(
        "CAPTURED_CHUNK_PARENT_MISMATCH",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=(forged,)),
    )


def test_chunk_batch_mismatch_is_rejected():
    parent = _captured()
    chunk = _chunk(parent)
    lineage = chunk.metadata.source_lineage
    forged_lineage = CapturedChunkSourceLineage(
        spreadsheet_id_hash=lineage.spreadsheet_id_hash,
        sheet_id=lineage.sheet_id,
        sheet_title=lineage.sheet_title,
        source_row=lineage.source_row,
        source_columns=lineage.source_columns,
        source_ranges=lineage.source_ranges,
        source_fingerprint=lineage.source_fingerprint,
        sync_batch_id="SYNTHETIC-OTHER-BATCH",
    )
    forged = _forged_chunk(chunk, source_lineage=forged_lineage)
    _assert_error(
        "CAPTURED_CHUNK_BATCH_MISMATCH",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=(forged,)),
    )


def test_duplicate_chunk_id_is_rejected():
    parent = _captured()
    chunk = _chunk(parent)
    _assert_error(
        "CAPTURED_CHUNK_ID_DUPLICATE",
        build_release_manifest,
        _inputs(captured_contents=(parent,), captured_chunks=(chunk, chunk)),
    )


def test_chunk_set_hash_is_deterministic_under_chunk_reorder():
    parent = _captured()
    first = _chunk(parent)
    second_text = "Closing text"
    second = _chunk(
        parent,
        span=_span(text=second_text, anchor="section-beta"),
    )
    first_manifest = build_release_manifest(
        _inputs(captured_contents=(parent,), captured_chunks=(first, second))
    )
    second_manifest = build_release_manifest(
        _inputs(captured_contents=(parent,), captured_chunks=(second, first))
    )

    assert first_manifest.captured_revisions[0].chunk_count == 2
    assert (
        first_manifest.captured_revisions[0].chunk_set_hash
        == second_manifest.captured_revisions[0].chunk_set_hash
    )


def test_chunk_set_hash_changes_when_chunk_identity_changes():
    parent = _captured()
    first = _chunk(parent)
    changed = _chunk(parent, span=_span(anchor="section-changed"))
    first_manifest = build_release_manifest(
        _inputs(captured_contents=(parent,), captured_chunks=(first,))
    )
    changed_manifest = build_release_manifest(
        _inputs(captured_contents=(parent,), captured_chunks=(changed,))
    )

    assert (
        first_manifest.captured_revisions[0].chunk_set_hash
        != changed_manifest.captured_revisions[0].chunk_set_hash
    )


def test_chunk_set_hash_golden_vector():
    manifest = build_release_manifest(_inputs())
    chunk_id = str(_inputs().captured_chunks[0].metadata.chunk_id)
    encoded = chunk_id.encode("utf-8")
    independent_payload = (
        b"MKA_CAPTURED_CHUNK_SET_V1\x00"
        + (1).to_bytes(8, "big")
        + len(encoded).to_bytes(8, "big")
        + encoded
    )
    expected = "chunkset:v1:sha256:" + hashlib.sha256(independent_payload).hexdigest()

    assert str(manifest.captured_revisions[0].chunk_set_hash) == expected
    assert str(manifest.captured_revisions[0].chunk_set_hash) == (
        "chunkset:v1:sha256:"
        "4fd39b04c0a43482171a8cba49069653ea7b581c032ae00eca34b512125d5e98"
    )


def test_required_entity_counts_are_present_and_conserved():
    for counts, code in (
        (("captured_content", 1), "REQUIRED_ENTITY_COUNT_MISSING"),
        (
            (("captured_content", 2), ("chunk", 1)),
            "CAPTURED_CONTENT_COUNT_MISMATCH",
        ),
        (
            (("captured_content", 1), ("chunk", 2)),
            "CHUNK_COUNT_MISMATCH",
        ),
    ):
        normalized = counts if type(counts[0]) is tuple else (counts,)
        _assert_error(
            code,
            build_release_manifest,
            _inputs(entity_counts=normalized),
        )


def test_previous_release_none_is_allowed_and_self_reference_is_rejected():
    assert build_release_manifest(_inputs(previous_release=None)).previous_release is None
    release_id = ReleaseId("release-wp14-self")
    _assert_error(
        "PREVIOUS_RELEASE_SELF_REFERENCE",
        build_release_manifest,
        _inputs(release_id=release_id, previous_release=release_id),
    )


def test_created_at_requires_exact_aware_datetime():
    values = _input_values(_inputs())
    for invalid in (
        datetime(2026, 8, 11, 4, 5, 6),
        "2026-08-11T04:05:06Z",
    ):
        values["created_at"] = invalid
        _assert_error("CREATED_AT_INVALID", CanonicalReleaseInputs, **values)


def test_builder_snapshots_utc_timestamps_to_fresh_instances():
    inputs = _inputs()
    parent = inputs.captured_contents[0]
    manifest = build_release_manifest(inputs)
    entry = manifest.captured_revisions[0]

    timestamp_pairs = (
        (manifest.created_at, inputs.created_at),
        (entry.captured_at, parent.captured_at),
        (
            entry.last_successful_capture_at,
            parent.last_successful_capture_at,
        ),
        (entry.last_capture_attempt_at, parent.last_capture_attempt_at),
    )
    for canonical_timestamp, caller_timestamp in timestamp_pairs:
        assert canonical_timestamp is not caller_timestamp
        assert canonical_timestamp == caller_timestamp
        assert canonical_timestamp.tzinfo is timezone.utc


def test_builder_snapshots_all_manifest_timestamps_to_fixed_utc():
    mutable_timezone = _MutableSyntheticTimezone(timedelta(0))
    payload = _captured_payload()
    payload["captured_at"] = CAPTURED_AT.replace(tzinfo=mutable_timezone)
    payload["last_successful_capture_at"] = LAST_SUCCESS.replace(
        tzinfo=mutable_timezone
    )
    payload["last_capture_attempt_at"] = LAST_ATTEMPT.replace(
        tzinfo=mutable_timezone
    )
    parent = CapturedContent(**payload)
    manifest = build_release_manifest(
        _inputs(
            captured_contents=(parent,),
            captured_chunks=(_chunk(parent),),
            created_at=CREATED_AT.replace(tzinfo=mutable_timezone),
        )
    )
    entry = manifest.captured_revisions[0]
    before_bytes = serialize_release_manifest(manifest)
    before_hash = compute_release_manifest_hash(manifest)

    mutable_timezone.offset = timedelta(hours=1)

    assert serialize_release_manifest(manifest) == before_bytes
    assert compute_release_manifest_hash(manifest) == before_hash
    assert manifest.created_at.tzinfo is timezone.utc
    assert entry.captured_at.tzinfo is timezone.utc
    assert entry.last_successful_capture_at.tzinfo is timezone.utc
    assert entry.last_capture_attempt_at.tzinfo is timezone.utc


def test_created_at_utc_underflow_maps_to_release_error():
    inputs = _inputs(
        created_at=datetime(
            1,
            1,
            1,
            tzinfo=timezone(timedelta(hours=1)),
        )
    )

    with pytest.raises(ReleaseContractError) as captured:
        manifest = build_release_manifest(inputs)
        serialize_release_manifest(manifest)

    assert captured.value.code == "RELEASE_DATETIME_CANONICALIZATION_FAILED"
    assert str(captured.value) == "RELEASE_DATETIME_CANONICALIZATION_FAILED"


def test_revision_timestamp_utc_underflow_maps_to_release_error():
    offset = timezone(timedelta(hours=1))
    payload = _captured_payload()
    payload["captured_at"] = datetime(1, 1, 1, tzinfo=offset)
    payload["last_successful_capture_at"] = datetime(1, 1, 2, tzinfo=offset)
    payload["last_capture_attempt_at"] = datetime(1, 1, 3, tzinfo=offset)
    parent = CapturedContent(**payload)
    inputs = _inputs(
        captured_contents=(parent,),
        captured_chunks=(_chunk(parent),),
    )

    with pytest.raises(ReleaseContractError) as captured:
        manifest = build_release_manifest(inputs)
        serialize_release_manifest(manifest)

    assert captured.value.code == "RELEASE_DATETIME_CANONICALIZATION_FAILED"
    assert str(captured.value) == "RELEASE_DATETIME_CANONICALIZATION_FAILED"


def test_custom_timezone_exception_maps_to_payload_free_release_error():
    custom_timezone = _FailingSyntheticTimezone()
    inputs = _inputs(
        created_at=CREATED_AT.replace(tzinfo=custom_timezone),
    )

    with pytest.raises(ReleaseContractError) as captured:
        build_release_manifest(inputs)

    assert captured.value.code == "RELEASE_DATETIME_CANONICALIZATION_FAILED"
    assert str(captured.value) == "RELEASE_DATETIME_CANONICALIZATION_FAILED"
    assert "SYNTHETIC_TZ_EXCEPTION_PAYLOAD" not in repr(captured.value)


def test_schema_version_and_publish_state_are_builder_fixed():
    manifest = build_release_manifest(_inputs())

    assert manifest.schema_version == "release-manifest-v1"
    assert manifest.publish_state is ReleasePublishState.CANDIDATE
    assert [state.value for state in ReleasePublishState] == ["candidate"]


def test_captured_revisions_are_deterministic_under_parent_reorder():
    parents, chunks, policies = _two_primary_parents()
    first = build_release_manifest(
        _inputs(
            captured_contents=parents,
            captured_chunks=chunks,
            capture_policy_decisions=policies,
        )
    )
    second = build_release_manifest(
        _inputs(
            captured_contents=tuple(reversed(parents)),
            captured_chunks=tuple(reversed(chunks)),
            capture_policy_decisions=tuple(reversed(policies)),
        )
    )

    assert serialize_release_manifest(first) == serialize_release_manifest(second)
    assert [
        str(entry.revision_ref.captured_content_id)
        for entry in first.captured_revisions
    ] == ["capture-wp14-001", "capture-wp14-002"]


def test_manifest_json_has_exact_top_level_and_nested_shape():
    payload = json.loads(serialize_release_manifest(build_release_manifest(_inputs())))

    assert list(sorted(payload)) == sorted(
        [
            "release_id",
            "schema_version",
            "metadata_sync_batch_id",
            "source_fingerprint",
            "source_row_counts",
            "entity_counts",
            "excluded_counts",
            "capture_policy_version",
            "parser_version",
            "captured_revisions",
            "artifacts",
            "validator_versions",
            "previous_release",
            "created_at",
            "publish_state",
        ]
    )
    entry = payload["captured_revisions"][0]
    assert entry["revision_ref"] == {
        "captured_content_id": "capture-wp14-001",
        "content_hash": str(CAPTURE_HASH),
        "parser_version": PARSER_VERSION,
    }
    assert entry["freshness_policy_version"] is None
    assert payload["previous_release"] is None
    assert payload["publish_state"] == "candidate"
    assert payload["entity_counts"] == [
        ["brand", 1],
        ["captured_content", 1],
        ["chunk", 1],
    ]


def test_manifest_serialization_is_compact_utf8_without_newline_or_normalization():
    manifest = build_release_manifest(_inputs(release_id="發布-e\u0301"))
    serialized = serialize_release_manifest(manifest)

    assert serialized == serialize_release_manifest(manifest)
    assert "發布-e\u0301".encode("utf-8") in serialized
    assert "發布-é".encode("utf-8") not in serialized
    assert b"\n" not in serialized
    assert b" " not in serialized
    assert not serialized.endswith(b"\n")


def test_same_instant_different_offsets_have_same_serialization():
    offset = timezone(timedelta(hours=8))
    local_time = CREATED_AT.astimezone(offset)
    utc_manifest = build_release_manifest(_inputs(created_at=CREATED_AT))
    local_manifest = build_release_manifest(_inputs(created_at=local_time))

    assert serialize_release_manifest(utc_manifest) == serialize_release_manifest(
        local_manifest
    )
    payload = json.loads(serialize_release_manifest(local_manifest))
    assert payload["created_at"] == "2026-08-11T04:05:06.000000Z"


def test_manifest_hash_is_deterministic_and_changes_with_frozen_inputs():
    baseline = build_release_manifest(_inputs())
    same = build_release_manifest(_inputs())
    changed_release = build_release_manifest(_inputs(release_id="release-wp14-beta"))
    changed_previous = build_release_manifest(
        _inputs(previous_release=ReleaseId("release-wp14-previous"))
    )
    changed_created = build_release_manifest(
        _inputs(created_at=CREATED_AT + timedelta(microseconds=1))
    )

    assert compute_release_manifest_hash(baseline) == compute_release_manifest_hash(same)
    assert compute_release_manifest_hash(baseline) != compute_release_manifest_hash(
        changed_release
    )
    assert compute_release_manifest_hash(baseline) != compute_release_manifest_hash(
        changed_previous
    )
    assert compute_release_manifest_hash(baseline) != compute_release_manifest_hash(
        changed_created
    )


def test_manifest_hash_golden_vector_and_independent_framing():
    manifest = build_release_manifest(_inputs())
    canonical = serialize_release_manifest(manifest)
    independent_payload = (
        b"MKA_RELEASE_MANIFEST_V1\x00"
        + len(canonical).to_bytes(8, "big")
        + canonical
    )
    expected = "release-manifest:v1:sha256:" + hashlib.sha256(
        independent_payload
    ).hexdigest()

    assert str(compute_release_manifest_hash(manifest)) == expected
    assert str(compute_release_manifest_hash(manifest)) == (
        "release-manifest:v1:sha256:"
        "2f320b458939bdddd7c0fe057a282bd7934b6b51f63d6f40ca84f2df74013b41"
    )


def test_serializer_and_hash_require_exact_manifest_type():
    for function in (serialize_release_manifest, compute_release_manifest_hash):
        _assert_error("RELEASE_MANIFEST_REQUIRED", function, {})


def test_canonical_outputs_require_builder_and_replace_fails_closed():
    manifest = build_release_manifest(_inputs())
    entry = manifest.captured_revisions[0]

    _assert_error(
        "RELEASE_ENTRY_REQUIRES_BUILDER",
        CapturedRevisionManifestEntry,
        **_entry_values(entry),
    )
    _assert_error(
        "RELEASE_MANIFEST_REQUIRES_BUILDER",
        ReleaseManifest,
        **_manifest_values(manifest),
    )
    _assert_error("RELEASE_ENTRY_REQUIRES_BUILDER", replace, entry)
    _assert_error("RELEASE_MANIFEST_REQUIRES_BUILDER", replace, manifest)


def test_manifest_and_entry_repr_and_serialization_are_body_free():
    manifest = build_release_manifest(_inputs())
    entry = manifest.captured_revisions[0]
    serialized = serialize_release_manifest(manifest).decode("utf-8")
    rendered = (repr(entry), str(entry), repr(manifest), str(manifest), serialized)

    forbidden = (
        BODY_SENTINEL,
        TITLE_SENTINEL,
        URL_SENTINEL,
        CHUNK_TEXT_SENTINEL,
        "clean_body",
        "section_structure",
        "source_url",
        "canonical_url",
        "source_http_metadata",
    )
    assert all(token not in value for value in rendered for token in forbidden)


def test_errors_never_echo_caller_payload():
    sentinel = "SYNTHETIC-WP14-ERROR-PAYLOAD-91F3"
    with pytest.raises(ReleaseContractError) as captured:
        ReleaseId(f"{sentinel}\n")

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


def test_module_has_no_markdown_legacy_or_runtime_authority_surface():
    source = inspect.getsource(release_contracts)
    forbidden = (
        "Document",
        "frontmatter",
        "obsidian_sync",
        "content_index",
        "indexing",
        "retrieval",
        "sqlite3",
        "pathlib",
        "requests",
        "socket",
        "slack",
        "uuid",
        "random",
        "datetime.now",
        "datetime.utcnow",
        "open(",
        ".resolve(",
        ".exists(",
        ".stat(",
    )
    assert all(token not in source for token in forbidden)


def test_release_contracts_have_no_filesystem_network_or_clock_side_effects(
    monkeypatch,
):
    def unexpected(*args, **kwargs):
        raise AssertionError("WP14 attempted an external side effect")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)

    manifest = build_release_manifest(_inputs())
    assert serialize_release_manifest(manifest)
    assert compute_release_manifest_hash(manifest)
