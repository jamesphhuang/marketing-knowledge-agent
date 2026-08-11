"""Pure, deterministic contracts for one complete candidate Release."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple

from .canonical_models import ContentAssetKey, MetricId
from .capture_policy import CaptureMode, CapturePolicyDecision
from .captured_chunks import CapturedChunk, CapturedChunkId
from .captured_content import (
    AuthorityRole,
    CapturedContent,
    CapturedContentId,
    CaptureStatus,
    EvidenceRelationshipId,
)
from .content_hashing import (
    CaptureContentHash,
    CaptureRevisionRef,
    ContentHashingError,
    LkgEligibilityInput,
    LkgEligibilityResult,
    compose_stale_lkg,
)


_RELEASE_SCHEMA_VERSION = "release-manifest-v1"
_RELEASE_VALIDATOR_VERSION = "wp14-v1"
_DATETIME_CANONICALIZATION_ERROR = (
    "RELEASE_DATETIME_CANONICALIZATION_FAILED"
)
_RELEASE_HASH_DOMAIN = b"MKA_RELEASE_MANIFEST_V1\x00"
_CHUNK_SET_HASH_DOMAIN = b"MKA_CAPTURED_CHUNK_SET_V1\x00"
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_RELEASE_HASH_PATTERN = re.compile(
    r"release-manifest:v1:sha256:[0-9a-f]{64}"
)
_CHUNK_SET_HASH_PATTERN = re.compile(r"chunkset:v1:sha256:[0-9a-f]{64}")
_COUNT_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ERROR_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
_REQUIRED_ARTIFACT_ROLES = frozenset(
    {
        "obsidian_tree",
        "official_sqlite",
        "official_vector",
    }
)


class ReleaseContractError(ValueError):
    """Stable, caller-payload-free WP14 contract failure."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _ERROR_CODE_PATTERN.fullmatch(code) is None:
            code = "RELEASE_CONTRACT_ERROR_CODE_INVALID"
        self.code = code
        super().__init__(code)


class ReleaseId(str):
    """Caller-supplied opaque identity for one complete Release."""

    __slots__ = ()

    def __new__(cls, value: str):
        if type(value) is cls:
            return value
        if (
            type(value) is not str
            or not value
            or not value.strip()
            or value != value.strip()
            or _contains_ascii_control(value)
        ):
            raise ReleaseContractError("RELEASE_ID_INVALID")
        return str.__new__(cls, value)


class ReleaseManifestHash(str):
    """Strict serialized identity of canonical ReleaseManifest bytes."""

    __slots__ = ()

    def __new__(cls, value: str):
        if type(value) is cls:
            return value
        if (
            type(value) is not str
            or _RELEASE_HASH_PATTERN.fullmatch(value) is None
        ):
            raise ReleaseContractError("RELEASE_MANIFEST_HASH_INVALID")
        return str.__new__(cls, value)


class ChunkSetHash(str):
    """Strict identity of the sorted chunk IDs for one capture revision."""

    __slots__ = ()

    def __new__(cls, value: str):
        if type(value) is cls:
            return value
        if (
            type(value) is not str
            or _CHUNK_SET_HASH_PATTERN.fullmatch(value) is None
        ):
            raise ReleaseContractError("CHUNK_SET_HASH_INVALID")
        return str.__new__(cls, value)


class ArtifactRole(str, Enum):
    OBSIDIAN_TREE = "obsidian_tree"
    OFFICIAL_SQLITE = "official_sqlite"
    OFFICIAL_VECTOR = "official_vector"


@dataclass(frozen=True)
class ArtifactRef:
    """Logical, checksum-pinned sibling reference with no filesystem access."""

    role: ArtifactRole
    relative_path: str
    checksum: str

    def __post_init__(self) -> None:
        if type(self.role) is not ArtifactRole:
            raise ReleaseContractError("ARTIFACT_ROLE_INVALID")
        _validate_relative_posix_path(self.relative_path)
        if (
            type(self.checksum) is not str
            or _SHA256_PATTERN.fullmatch(self.checksum) is None
        ):
            raise ReleaseContractError("ARTIFACT_CHECKSUM_INVALID")


class ReleasePublishState(str, Enum):
    CANDIDATE = "candidate"


PolicyDecisionAssociation = Tuple[CapturedContentId, CapturePolicyDecision]
StaleProofAssociation = Tuple[
    CapturedContentId,
    LkgEligibilityInput,
    LkgEligibilityResult,
]
IntegerCountPair = Tuple[int, int]
NamedCountPair = Tuple[str, int]
ValidatorVersionPair = Tuple[str, str]


@dataclass(frozen=True, repr=False)
class CanonicalReleaseInputs:
    """Immutable structural inputs; not a validated or publishable Release."""

    release_id: ReleaseId
    metadata_sync_batch_id: str
    source_fingerprint: str
    source_row_counts: Tuple[IntegerCountPair, ...]
    entity_counts: Tuple[NamedCountPair, ...]
    excluded_counts: Tuple[NamedCountPair, ...]
    captured_contents: Tuple[CapturedContent, ...]
    capture_policy_decisions: Tuple[PolicyDecisionAssociation, ...]
    stale_proofs: Tuple[StaleProofAssociation, ...]
    captured_chunks: Tuple[CapturedChunk, ...]
    artifacts: Tuple[ArtifactRef, ...]
    validator_versions: Tuple[ValidatorVersionPair, ...]
    previous_release: Optional[ReleaseId]
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.release_id) is not ReleaseId:
            raise ReleaseContractError("RELEASE_ID_TYPE_INVALID")
        _validate_strict_text(
            self.metadata_sync_batch_id,
            "METADATA_SYNC_BATCH_ID_INVALID",
        )
        if (
            type(self.source_fingerprint) is not str
            or _SHA256_PATTERN.fullmatch(self.source_fingerprint) is None
        ):
            raise ReleaseContractError("SOURCE_FINGERPRINT_INVALID")
        _validate_source_row_counts(self.source_row_counts)
        _validate_named_counts(self.entity_counts, "ENTITY_COUNTS")
        _validate_named_counts(self.excluded_counts, "EXCLUDED_COUNTS")
        _validate_exact_tuple(self.captured_contents, "CAPTURED_CONTENTS")
        for item in self.captured_contents:
            if type(item) is not CapturedContent:
                raise ReleaseContractError("CAPTURED_CONTENT_TYPE_INVALID")
        _validate_policy_associations(self.capture_policy_decisions)
        _validate_stale_proof_associations(self.stale_proofs)
        _validate_exact_tuple(self.captured_chunks, "CAPTURED_CHUNKS")
        for item in self.captured_chunks:
            if type(item) is not CapturedChunk:
                raise ReleaseContractError("CAPTURED_CHUNK_TYPE_INVALID")
        _validate_exact_tuple(self.artifacts, "ARTIFACTS")
        for item in self.artifacts:
            if type(item) is not ArtifactRef:
                raise ReleaseContractError("ARTIFACT_REF_TYPE_INVALID")
        _validate_validator_versions(self.validator_versions)
        if self.previous_release is not None and type(
            self.previous_release
        ) is not ReleaseId:
            raise ReleaseContractError("PREVIOUS_RELEASE_TYPE_INVALID")
        _validate_aware_datetime(self.created_at, "CREATED_AT_INVALID")

    def __repr__(self) -> str:
        return (
            "CanonicalReleaseInputs("
            f"release_id={str(self.release_id)!r}, "
            f"captured_content_count={len(self.captured_contents)}, "
            f"captured_chunk_count={len(self.captured_chunks)}, "
            f"artifact_count={len(self.artifacts)}, "
            "payload=<redacted>)"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False, init=False)
class CapturedRevisionManifestEntry:
    """Body-free, validated membership for one full-text capture revision."""

    revision_ref: CaptureRevisionRef
    capture_status: CaptureStatus
    authority_role: AuthorityRole
    asset_key: Optional[ContentAssetKey]
    metric_id: Optional[MetricId]
    evidence_relationship_id: Optional[EvidenceRelationshipId]
    sync_batch_id: str
    captured_at: datetime
    last_successful_capture_at: datetime
    last_capture_attempt_at: datetime
    freshness_policy_version: Optional[str]
    chunk_count: int
    chunk_set_hash: ChunkSetHash

    def __post_init__(self) -> None:
        if type(self.revision_ref) is not CaptureRevisionRef:
            raise ReleaseContractError("RELEASE_REVISION_REF_INVALID")
        if self.capture_status not in (CaptureStatus.SUCCESS, CaptureStatus.STALE):
            raise ReleaseContractError("RELEASE_CAPTURE_STATUS_INVALID")
        _validate_entry_authority(self)
        _validate_strict_text(self.sync_batch_id, "RELEASE_ENTRY_BATCH_INVALID")
        _validate_aware_datetime(self.captured_at, "RELEASE_ENTRY_TIME_INVALID")
        _validate_aware_datetime(
            self.last_successful_capture_at,
            "RELEASE_ENTRY_TIME_INVALID",
        )
        _validate_aware_datetime(
            self.last_capture_attempt_at,
            "RELEASE_ENTRY_TIME_INVALID",
        )
        if not (
            self.captured_at
            <= self.last_successful_capture_at
            <= self.last_capture_attempt_at
        ):
            raise ReleaseContractError("RELEASE_ENTRY_TIME_INVALID")
        if self.capture_status is CaptureStatus.SUCCESS:
            if self.freshness_policy_version is not None:
                raise ReleaseContractError("SUCCESS_FRESHNESS_VERSION_NOT_ALLOWED")
        else:
            _validate_strict_text(
                self.freshness_policy_version,
                "STALE_FRESHNESS_VERSION_REQUIRED",
            )
        if type(self.chunk_count) is not int or self.chunk_count <= 0:
            raise ReleaseContractError("RELEASE_ENTRY_CHUNK_COUNT_INVALID")
        if type(self.chunk_set_hash) is not ChunkSetHash:
            raise ReleaseContractError("RELEASE_ENTRY_CHUNK_SET_HASH_INVALID")

    def __repr__(self) -> str:
        return (
            "CapturedRevisionManifestEntry("
            f"captured_content_id={str(self.revision_ref.captured_content_id)!r}, "
            f"capture_status={self.capture_status.value!r}, "
            f"authority_role={self.authority_role.value!r}, "
            f"chunk_count={self.chunk_count!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False, init=False)
class ReleaseManifest:
    """Canonical, cross-field-validated candidate Release composition."""

    release_id: ReleaseId
    schema_version: str
    metadata_sync_batch_id: str
    source_fingerprint: str
    source_row_counts: Tuple[IntegerCountPair, ...]
    entity_counts: Tuple[NamedCountPair, ...]
    excluded_counts: Tuple[NamedCountPair, ...]
    capture_policy_version: str
    parser_version: str
    captured_revisions: Tuple[CapturedRevisionManifestEntry, ...]
    artifacts: Tuple[ArtifactRef, ...]
    validator_versions: Tuple[ValidatorVersionPair, ...]
    previous_release: Optional[ReleaseId]
    created_at: datetime
    publish_state: ReleasePublishState

    def __post_init__(self) -> None:
        if type(self.release_id) is not ReleaseId:
            raise ReleaseContractError("RELEASE_ID_TYPE_INVALID")
        if self.schema_version != _RELEASE_SCHEMA_VERSION:
            raise ReleaseContractError("RELEASE_SCHEMA_VERSION_INVALID")
        _validate_strict_text(
            self.metadata_sync_batch_id,
            "METADATA_SYNC_BATCH_ID_INVALID",
        )
        if (
            type(self.source_fingerprint) is not str
            or _SHA256_PATTERN.fullmatch(self.source_fingerprint) is None
        ):
            raise ReleaseContractError("SOURCE_FINGERPRINT_INVALID")
        _validate_source_row_counts(self.source_row_counts)
        _validate_named_counts(self.entity_counts, "ENTITY_COUNTS")
        _validate_named_counts(self.excluded_counts, "EXCLUDED_COUNTS")
        _validate_strict_text(
            self.capture_policy_version,
            "CAPTURE_POLICY_VERSION_INVALID",
        )
        _validate_strict_text(self.parser_version, "PARSER_VERSION_INVALID")
        _validate_exact_tuple(self.captured_revisions, "CAPTURED_REVISIONS")
        for entry in self.captured_revisions:
            if type(entry) is not CapturedRevisionManifestEntry:
                raise ReleaseContractError("RELEASE_ENTRY_TYPE_INVALID")
        if self.captured_revisions != tuple(
            sorted(
                self.captured_revisions,
                key=lambda entry: str(entry.revision_ref.captured_content_id),
            )
        ):
            raise ReleaseContractError("CAPTURED_REVISIONS_NOT_CANONICAL")
        _validate_exact_tuple(self.artifacts, "ARTIFACTS")
        if self.artifacts != tuple(
            sorted(self.artifacts, key=lambda artifact: artifact.role.value)
        ):
            raise ReleaseContractError("ARTIFACTS_NOT_CANONICAL")
        _validate_validator_versions(self.validator_versions, allow_release=True)
        if self.validator_versions != tuple(
            sorted(self.validator_versions, key=lambda item: item[0])
        ):
            raise ReleaseContractError("VALIDATOR_VERSIONS_NOT_CANONICAL")
        if self.previous_release is not None and type(
            self.previous_release
        ) is not ReleaseId:
            raise ReleaseContractError("PREVIOUS_RELEASE_TYPE_INVALID")
        _validate_aware_datetime(self.created_at, "CREATED_AT_INVALID")
        if self.publish_state is not ReleasePublishState.CANDIDATE:
            raise ReleaseContractError("RELEASE_PUBLISH_STATE_INVALID")

    def __repr__(self) -> str:
        return (
            "ReleaseManifest("
            f"release_id={str(self.release_id)!r}, "
            f"schema_version={self.schema_version!r}, "
            f"captured_revision_count={len(self.captured_revisions)}, "
            f"artifact_count={len(self.artifacts)}, "
            f"publish_state={self.publish_state.value!r})"
        )

    __str__ = __repr__


def _build_canonical_constructors():
    authorization = object()

    def entry_init(
        self,
        *,
        revision_ref: CaptureRevisionRef,
        capture_status: CaptureStatus,
        authority_role: AuthorityRole,
        asset_key: Optional[ContentAssetKey],
        metric_id: Optional[MetricId],
        evidence_relationship_id: Optional[EvidenceRelationshipId],
        sync_batch_id: str,
        captured_at: datetime,
        last_successful_capture_at: datetime,
        last_capture_attempt_at: datetime,
        freshness_policy_version: Optional[str],
        chunk_count: int,
        chunk_set_hash: ChunkSetHash,
        _wp14_gate=None,
    ) -> None:
        if _wp14_gate is not authorization:
            raise ReleaseContractError("RELEASE_ENTRY_REQUIRES_BUILDER")
        values = (
            ("revision_ref", revision_ref),
            ("capture_status", capture_status),
            ("authority_role", authority_role),
            ("asset_key", asset_key),
            ("metric_id", metric_id),
            ("evidence_relationship_id", evidence_relationship_id),
            ("sync_batch_id", sync_batch_id),
            ("captured_at", captured_at),
            ("last_successful_capture_at", last_successful_capture_at),
            ("last_capture_attempt_at", last_capture_attempt_at),
            ("freshness_policy_version", freshness_policy_version),
            ("chunk_count", chunk_count),
            ("chunk_set_hash", chunk_set_hash),
        )
        for name, value in values:
            object.__setattr__(self, name, value)
        self.__post_init__()

    def manifest_init(
        self,
        *,
        release_id: ReleaseId,
        schema_version: str,
        metadata_sync_batch_id: str,
        source_fingerprint: str,
        source_row_counts: Tuple[IntegerCountPair, ...],
        entity_counts: Tuple[NamedCountPair, ...],
        excluded_counts: Tuple[NamedCountPair, ...],
        capture_policy_version: str,
        parser_version: str,
        captured_revisions: Tuple[CapturedRevisionManifestEntry, ...],
        artifacts: Tuple[ArtifactRef, ...],
        validator_versions: Tuple[ValidatorVersionPair, ...],
        previous_release: Optional[ReleaseId],
        created_at: datetime,
        publish_state: ReleasePublishState,
        _wp14_gate=None,
    ) -> None:
        if _wp14_gate is not authorization:
            raise ReleaseContractError("RELEASE_MANIFEST_REQUIRES_BUILDER")
        values = (
            ("release_id", release_id),
            ("schema_version", schema_version),
            ("metadata_sync_batch_id", metadata_sync_batch_id),
            ("source_fingerprint", source_fingerprint),
            ("source_row_counts", source_row_counts),
            ("entity_counts", entity_counts),
            ("excluded_counts", excluded_counts),
            ("capture_policy_version", capture_policy_version),
            ("parser_version", parser_version),
            ("captured_revisions", captured_revisions),
            ("artifacts", artifacts),
            ("validator_versions", validator_versions),
            ("previous_release", previous_release),
            ("created_at", created_at),
            ("publish_state", publish_state),
        )
        for name, value in values:
            object.__setattr__(self, name, value)
        self.__post_init__()

    def create_entry(**values) -> CapturedRevisionManifestEntry:
        return CapturedRevisionManifestEntry(_wp14_gate=authorization, **values)

    def create_manifest(**values) -> ReleaseManifest:
        return ReleaseManifest(_wp14_gate=authorization, **values)

    entry_init.__name__ = "__init__"
    entry_init.__qualname__ = "CapturedRevisionManifestEntry.__init__"
    manifest_init.__name__ = "__init__"
    manifest_init.__qualname__ = "ReleaseManifest.__init__"
    return entry_init, manifest_init, create_entry, create_manifest


(
    CapturedRevisionManifestEntry.__init__,
    ReleaseManifest.__init__,
    _create_revision_entry,
    _create_release_manifest,
) = _build_canonical_constructors()
del _build_canonical_constructors


def build_release_manifest(inputs: CanonicalReleaseInputs) -> ReleaseManifest:
    """Validate one complete in-memory composition and return its manifest."""

    if type(inputs) is not CanonicalReleaseInputs:
        raise ReleaseContractError("CANONICAL_RELEASE_INPUTS_REQUIRED")
    if inputs.previous_release == inputs.release_id:
        raise ReleaseContractError("PREVIOUS_RELEASE_SELF_REFERENCE")

    parents, revisions = _validate_captured_contents(inputs)
    capture_policy_version = _validate_capture_policy_decisions(inputs, parents)
    parser_version = _derive_parser_version(revisions)
    freshness_versions = _validate_stale_proofs(inputs, parents, revisions)
    chunks_by_parent = _validate_and_group_chunks(inputs, parents, revisions)

    entries = []
    for captured_content_id, parent in parents.items():
        chunks = chunks_by_parent[captured_content_id]
        captured_at = _snapshot_utc_datetime(
            parent.captured_at,
            _DATETIME_CANONICALIZATION_ERROR,
        )
        last_successful_capture_at = _snapshot_utc_datetime(
            parent.last_successful_capture_at,
            _DATETIME_CANONICALIZATION_ERROR,
        )
        last_capture_attempt_at = _snapshot_utc_datetime(
            parent.last_capture_attempt_at,
            _DATETIME_CANONICALIZATION_ERROR,
        )
        entries.append(
            _create_revision_entry(
                revision_ref=revisions[captured_content_id],
                capture_status=parent.capture_status,
                authority_role=parent.authority_role,
                asset_key=parent.asset_key,
                metric_id=parent.metric_id,
                evidence_relationship_id=parent.evidence_relationship_id,
                sync_batch_id=parent.sync_batch_id,
                captured_at=captured_at,
                last_successful_capture_at=last_successful_capture_at,
                last_capture_attempt_at=last_capture_attempt_at,
                freshness_policy_version=freshness_versions.get(
                    captured_content_id
                ),
                chunk_count=len(chunks),
                chunk_set_hash=_compute_chunk_set_hash(chunks),
            )
        )
    captured_revisions = tuple(
        sorted(
            entries,
            key=lambda entry: str(entry.revision_ref.captured_content_id),
        )
    )

    entity_counts = tuple(sorted(inputs.entity_counts, key=lambda item: item[0]))
    entity_count_map = dict(entity_counts)
    if "captured_content" not in entity_count_map or "chunk" not in entity_count_map:
        raise ReleaseContractError("REQUIRED_ENTITY_COUNT_MISSING")
    if entity_count_map["captured_content"] != len(captured_revisions):
        raise ReleaseContractError("CAPTURED_CONTENT_COUNT_MISMATCH")
    if entity_count_map["chunk"] != sum(
        entry.chunk_count for entry in captured_revisions
    ):
        raise ReleaseContractError("CHUNK_COUNT_MISMATCH")

    artifacts = _validate_and_sort_artifacts(inputs.artifacts)
    validator_versions = tuple(
        sorted(
            inputs.validator_versions
            + (("release_contract", _RELEASE_VALIDATOR_VERSION),),
            key=lambda item: item[0],
        )
    )
    return _create_release_manifest(
        release_id=inputs.release_id,
        schema_version=_RELEASE_SCHEMA_VERSION,
        metadata_sync_batch_id=inputs.metadata_sync_batch_id,
        source_fingerprint=inputs.source_fingerprint,
        source_row_counts=tuple(sorted(inputs.source_row_counts)),
        entity_counts=entity_counts,
        excluded_counts=tuple(
            sorted(inputs.excluded_counts, key=lambda item: item[0])
        ),
        capture_policy_version=capture_policy_version,
        parser_version=parser_version,
        captured_revisions=captured_revisions,
        artifacts=artifacts,
        validator_versions=validator_versions,
        previous_release=inputs.previous_release,
        created_at=_snapshot_utc_datetime(
            inputs.created_at,
            _DATETIME_CANONICALIZATION_ERROR,
        ),
        publish_state=ReleasePublishState.CANDIDATE,
    )


def serialize_release_manifest(manifest: ReleaseManifest) -> bytes:
    """Return the exact canonical JSON bytes for a validated manifest."""

    if type(manifest) is not ReleaseManifest:
        raise ReleaseContractError("RELEASE_MANIFEST_REQUIRED")
    try:
        serialized = json.dumps(
            _manifest_payload(manifest),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return serialized.encode("utf-8", errors="strict")
    except ReleaseContractError:
        raise
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        raise ReleaseContractError("RELEASE_MANIFEST_SERIALIZATION_FAILED") from None


def compute_release_manifest_hash(
    manifest: ReleaseManifest,
) -> ReleaseManifestHash:
    """Hash exact canonical manifest bytes with the frozen WP14 framing."""

    if type(manifest) is not ReleaseManifest:
        raise ReleaseContractError("RELEASE_MANIFEST_REQUIRED")
    canonical_bytes = serialize_release_manifest(manifest)
    payload = _RELEASE_HASH_DOMAIN + _uint64(len(canonical_bytes)) + canonical_bytes
    digest = hashlib.sha256(payload).hexdigest()
    return ReleaseManifestHash(f"release-manifest:v1:sha256:{digest}")


def _validate_captured_contents(inputs):
    if not inputs.captured_contents:
        raise ReleaseContractError("RELEASE_CAPTURED_CONTENT_REQUIRED")
    parents = {}
    revisions = {}
    parent_keys = set()
    for parent in inputs.captured_contents:
        captured_content_id = parent.captured_content_id
        if captured_content_id in parents:
            raise ReleaseContractError("CAPTURED_CONTENT_ID_DUPLICATE")
        if parent.capture_status not in (CaptureStatus.SUCCESS, CaptureStatus.STALE):
            raise ReleaseContractError("CAPTURE_STATUS_NOT_RELEASE_PINNED")
        if parent.searchable is not True:
            raise ReleaseContractError("CAPTURED_CONTENT_NOT_SEARCHABLE")
        if any(
            value is None
            for value in (
                parent.clean_body,
                parent.content_hash,
                parent.parser_version,
                parent.captured_at,
                parent.last_successful_capture_at,
                parent.last_capture_attempt_at,
            )
        ):
            raise ReleaseContractError("CAPTURED_CONTENT_REVISION_INCOMPLETE")
        if (
            parent.sync_batch_id != inputs.metadata_sync_batch_id
            or parent.source_lineage.sync_batch_id
            != inputs.metadata_sync_batch_id
        ):
            raise ReleaseContractError("METADATA_SYNC_BATCH_MISMATCH")
        parent_key = _authority_parent_key(parent)
        if parent_key in parent_keys:
            raise ReleaseContractError("CAPTURE_AUTHORITY_PARENT_DUPLICATE")
        parent_keys.add(parent_key)
        try:
            revision_ref = CaptureRevisionRef(
                captured_content_id=captured_content_id,
                content_hash=CaptureContentHash(parent.content_hash),
                parser_version=parent.parser_version,
            )
        except ContentHashingError:
            raise ReleaseContractError("CAPTURE_REVISION_INVALID") from None
        parents[captured_content_id] = parent
        revisions[captured_content_id] = revision_ref
    return parents, revisions


def _validate_capture_policy_decisions(inputs, parents) -> str:
    decisions = {}
    for captured_content_id, decision in inputs.capture_policy_decisions:
        if captured_content_id in decisions:
            raise ReleaseContractError("CAPTURE_POLICY_DECISION_DUPLICATE")
        decisions[captured_content_id] = decision
    parent_ids = set(parents)
    decision_ids = set(decisions)
    if parent_ids - decision_ids:
        raise ReleaseContractError("CAPTURE_POLICY_DECISION_MISSING")
    if decision_ids - parent_ids:
        raise ReleaseContractError("CAPTURE_POLICY_DECISION_EXTRA")
    versions = set()
    for decision in decisions.values():
        if decision.mode is not CaptureMode.FULL_TEXT:
            raise ReleaseContractError("CAPTURE_POLICY_MODE_NOT_FULL_TEXT")
        versions.add(decision.policy_version)
    if len(versions) != 1:
        raise ReleaseContractError("CAPTURE_POLICY_VERSION_MIXED")
    return next(iter(versions))


def _derive_parser_version(revisions) -> str:
    versions = {revision.parser_version for revision in revisions.values()}
    if len(versions) != 1:
        raise ReleaseContractError("PARSER_VERSION_MIXED")
    return next(iter(versions))


def _validate_stale_proofs(inputs, parents, revisions):
    proofs = {}
    for captured_content_id, input_value, result in inputs.stale_proofs:
        if captured_content_id in proofs:
            raise ReleaseContractError("STALE_PROOF_DUPLICATE")
        proofs[captured_content_id] = (input_value, result)
    stale_ids = {
        captured_content_id
        for captured_content_id, parent in parents.items()
        if parent.capture_status is CaptureStatus.STALE
    }
    proof_ids = set(proofs)
    if stale_ids - proof_ids:
        raise ReleaseContractError("STALE_PROOF_MISSING")
    if proof_ids - stale_ids:
        raise ReleaseContractError("STALE_PROOF_EXTRA")

    freshness_versions = {}
    for captured_content_id in stale_ids:
        parent = parents[captured_content_id]
        input_value, result = proofs[captured_content_id]
        try:
            candidate = compose_stale_lkg(input_value, result)
        except ContentHashingError:
            raise ReleaseContractError("STALE_PROOF_INVALID") from None
        if (
            candidate.revision_ref != revisions[captured_content_id]
            or candidate.capture_status is not parent.capture_status
            or candidate.captured_at != parent.captured_at
            or candidate.last_successful_capture_at
            != parent.last_successful_capture_at
            or candidate.last_capture_attempt_at
            != parent.last_capture_attempt_at
            or candidate.searchable is not parent.searchable
            or input_value.current_canonical_url != parent.canonical_url
        ):
            raise ReleaseContractError("STALE_PROOF_BINDING_MISMATCH")
        freshness_versions[captured_content_id] = (
            candidate.freshness_policy_version
        )
    return freshness_versions


def _validate_and_group_chunks(inputs, parents, revisions):
    chunks_by_parent = {captured_content_id: [] for captured_content_id in parents}
    seen_chunk_ids = set()
    for chunk in inputs.captured_chunks:
        metadata = chunk.metadata
        chunk_id = metadata.chunk_id
        if chunk_id in seen_chunk_ids:
            raise ReleaseContractError("CAPTURED_CHUNK_ID_DUPLICATE")
        seen_chunk_ids.add(chunk_id)
        captured_content_id = metadata.captured_content_id
        parent = parents.get(captured_content_id)
        if parent is None:
            raise ReleaseContractError("CAPTURED_CHUNK_ORPHAN")
        if metadata.revision_ref != revisions[captured_content_id]:
            raise ReleaseContractError("CAPTURED_CHUNK_REVISION_MISMATCH")
        if metadata.authority_role is not parent.authority_role:
            raise ReleaseContractError("CAPTURED_CHUNK_AUTHORITY_MISMATCH")
        if (
            metadata.asset_key != parent.asset_key
            or metadata.metric_id != parent.metric_id
            or metadata.evidence_relationship_id
            != parent.evidence_relationship_id
        ):
            raise ReleaseContractError("CAPTURED_CHUNK_PARENT_MISMATCH")
        if (
            metadata.sync_batch_id != inputs.metadata_sync_batch_id
            or metadata.sync_batch_id != parent.sync_batch_id
        ):
            raise ReleaseContractError("CAPTURED_CHUNK_BATCH_MISMATCH")
        if (
            metadata.capture_status is not parent.capture_status
            or metadata.captured_at != parent.captured_at
            or metadata.last_successful_capture_at
            != parent.last_successful_capture_at
            or metadata.last_capture_attempt_at
            != parent.last_capture_attempt_at
            or metadata.searchable is not parent.searchable
        ):
            raise ReleaseContractError("CAPTURED_CHUNK_PARENT_STATE_MISMATCH")
        chunks_by_parent[captured_content_id].append(chunk)
    if any(not chunks for chunks in chunks_by_parent.values()):
        raise ReleaseContractError("CAPTURED_REVISION_CHUNKS_MISSING")
    return chunks_by_parent


def _compute_chunk_set_hash(chunks) -> ChunkSetHash:
    chunk_ids = sorted(str(chunk.metadata.chunk_id) for chunk in chunks)
    payload = _CHUNK_SET_HASH_DOMAIN + _uint64(len(chunk_ids))
    for chunk_id in chunk_ids:
        encoded = _encode_utf8(chunk_id, "CHUNK_SET_ID_UTF8_INVALID")
        payload += _uint64(len(encoded)) + encoded
    digest = hashlib.sha256(payload).hexdigest()
    return ChunkSetHash(f"chunkset:v1:sha256:{digest}")


def _validate_and_sort_artifacts(artifacts):
    by_role = {}
    for artifact in artifacts:
        if artifact.role in by_role:
            raise ReleaseContractError("ARTIFACT_ROLE_DUPLICATE")
        by_role[artifact.role] = artifact
    if {role.value for role in by_role} != _REQUIRED_ARTIFACT_ROLES:
        raise ReleaseContractError("ARTIFACT_SET_INCOMPLETE")
    return tuple(sorted(artifacts, key=lambda artifact: artifact.role.value))


def _authority_parent_key(parent):
    if parent.authority_role is AuthorityRole.PRIMARY_CONTENT:
        if (
            type(parent.asset_key) is not ContentAssetKey
            or parent.metric_id is not None
            or parent.evidence_relationship_id is not None
        ):
            raise ReleaseContractError("CAPTURE_PRIMARY_PARENT_INVALID")
        return ("primary", parent.asset_key)
    if parent.authority_role is AuthorityRole.EVIDENCE:
        if (
            parent.asset_key is not None
            or type(parent.metric_id) is not MetricId
            or type(parent.evidence_relationship_id) is not EvidenceRelationshipId
        ):
            raise ReleaseContractError("CAPTURE_EVIDENCE_PARENT_INVALID")
        return (
            "evidence",
            parent.metric_id,
            parent.evidence_relationship_id,
        )
    raise ReleaseContractError("CAPTURE_AUTHORITY_INVALID")


def _manifest_payload(manifest):
    return {
        "release_id": str(manifest.release_id),
        "schema_version": manifest.schema_version,
        "metadata_sync_batch_id": manifest.metadata_sync_batch_id,
        "source_fingerprint": manifest.source_fingerprint,
        "source_row_counts": [list(item) for item in manifest.source_row_counts],
        "entity_counts": [list(item) for item in manifest.entity_counts],
        "excluded_counts": [list(item) for item in manifest.excluded_counts],
        "capture_policy_version": manifest.capture_policy_version,
        "parser_version": manifest.parser_version,
        "captured_revisions": [
            _revision_entry_payload(entry)
            for entry in manifest.captured_revisions
        ],
        "artifacts": [
            {
                "role": artifact.role.value,
                "relative_path": artifact.relative_path,
                "checksum": artifact.checksum,
            }
            for artifact in manifest.artifacts
        ],
        "validator_versions": [list(item) for item in manifest.validator_versions],
        "previous_release": (
            str(manifest.previous_release)
            if manifest.previous_release is not None
            else None
        ),
        "created_at": _datetime_wire(manifest.created_at),
        "publish_state": manifest.publish_state.value,
    }


def _revision_entry_payload(entry):
    return {
        "revision_ref": {
            "captured_content_id": str(entry.revision_ref.captured_content_id),
            "content_hash": str(entry.revision_ref.content_hash),
            "parser_version": entry.revision_ref.parser_version,
        },
        "capture_status": entry.capture_status.value,
        "authority_role": entry.authority_role.value,
        "asset_key": str(entry.asset_key) if entry.asset_key is not None else None,
        "metric_id": str(entry.metric_id) if entry.metric_id is not None else None,
        "evidence_relationship_id": (
            str(entry.evidence_relationship_id)
            if entry.evidence_relationship_id is not None
            else None
        ),
        "sync_batch_id": entry.sync_batch_id,
        "captured_at": _datetime_wire(entry.captured_at),
        "last_successful_capture_at": _datetime_wire(
            entry.last_successful_capture_at
        ),
        "last_capture_attempt_at": _datetime_wire(
            entry.last_capture_attempt_at
        ),
        "freshness_policy_version": entry.freshness_policy_version,
        "chunk_count": entry.chunk_count,
        "chunk_set_hash": str(entry.chunk_set_hash),
    }


def _validate_policy_associations(value) -> None:
    _validate_exact_tuple(value, "CAPTURE_POLICY_DECISIONS")
    for entry in value:
        if type(entry) is not tuple or len(entry) != 2:
            raise ReleaseContractError("CAPTURE_POLICY_ASSOCIATION_INVALID")
        if type(entry[0]) is not CapturedContentId:
            raise ReleaseContractError("CAPTURE_POLICY_KEY_INVALID")
        if type(entry[1]) is not CapturePolicyDecision:
            raise ReleaseContractError("CAPTURE_POLICY_DECISION_INVALID")


def _validate_stale_proof_associations(value) -> None:
    _validate_exact_tuple(value, "STALE_PROOFS")
    for entry in value:
        if type(entry) is not tuple or len(entry) != 3:
            raise ReleaseContractError("STALE_PROOF_ASSOCIATION_INVALID")
        if type(entry[0]) is not CapturedContentId:
            raise ReleaseContractError("STALE_PROOF_KEY_INVALID")
        if type(entry[1]) is not LkgEligibilityInput:
            raise ReleaseContractError("STALE_PROOF_INPUT_INVALID")
        if type(entry[2]) is not LkgEligibilityResult:
            raise ReleaseContractError("STALE_PROOF_RESULT_INVALID")


def _validate_source_row_counts(value) -> None:
    _validate_exact_tuple(value, "SOURCE_ROW_COUNTS")
    seen = set()
    for entry in value:
        if type(entry) is not tuple or len(entry) != 2:
            raise ReleaseContractError("SOURCE_ROW_COUNT_ENTRY_INVALID")
        sheet_id, count = entry
        if type(sheet_id) is not int or sheet_id < 0:
            raise ReleaseContractError("SOURCE_ROW_COUNT_SHEET_ID_INVALID")
        if type(count) is not int or count < 0:
            raise ReleaseContractError("SOURCE_ROW_COUNT_VALUE_INVALID")
        if sheet_id in seen:
            raise ReleaseContractError("SOURCE_ROW_COUNT_SHEET_DUPLICATE")
        seen.add(sheet_id)


def _validate_named_counts(value, prefix: str) -> None:
    _validate_exact_tuple(value, prefix)
    seen = set()
    for entry in value:
        if type(entry) is not tuple or len(entry) != 2:
            raise ReleaseContractError(f"{prefix}_ENTRY_INVALID")
        name, count = entry
        if type(name) is not str or _COUNT_NAME_PATTERN.fullmatch(name) is None:
            raise ReleaseContractError(f"{prefix}_NAME_INVALID")
        if type(count) is not int or count < 0:
            raise ReleaseContractError(f"{prefix}_VALUE_INVALID")
        if name in seen:
            raise ReleaseContractError(f"{prefix}_NAME_DUPLICATE")
        seen.add(name)


def _validate_validator_versions(value, *, allow_release=False) -> None:
    _validate_exact_tuple(value, "VALIDATOR_VERSIONS")
    seen = set()
    for entry in value:
        if type(entry) is not tuple or len(entry) != 2:
            raise ReleaseContractError("VALIDATOR_VERSION_ENTRY_INVALID")
        name, version = entry
        _validate_strict_text(name, "VALIDATOR_NAME_INVALID")
        _validate_strict_text(version, "VALIDATOR_VERSION_INVALID")
        if name in seen:
            raise ReleaseContractError("VALIDATOR_NAME_DUPLICATE")
        if name == "release_contract" and not allow_release:
            raise ReleaseContractError("RELEASE_VALIDATOR_OVERRIDE_NOT_ALLOWED")
        seen.add(name)
    if allow_release and dict(value).get("release_contract") != _RELEASE_VALIDATOR_VERSION:
        raise ReleaseContractError("RELEASE_VALIDATOR_VERSION_INVALID")


def _validate_entry_authority(entry) -> None:
    if entry.authority_role is AuthorityRole.PRIMARY_CONTENT:
        if (
            type(entry.asset_key) is not ContentAssetKey
            or entry.metric_id is not None
            or entry.evidence_relationship_id is not None
        ):
            raise ReleaseContractError("RELEASE_ENTRY_PRIMARY_PARENT_INVALID")
        return
    if entry.authority_role is AuthorityRole.EVIDENCE:
        if (
            entry.asset_key is not None
            or type(entry.metric_id) is not MetricId
            or type(entry.evidence_relationship_id) is not EvidenceRelationshipId
        ):
            raise ReleaseContractError("RELEASE_ENTRY_EVIDENCE_PARENT_INVALID")
        return
    raise ReleaseContractError("RELEASE_ENTRY_AUTHORITY_INVALID")


def _validate_relative_posix_path(value) -> None:
    if (
        type(value) is not str
        or not value
        or not value.strip()
        or value != value.strip()
        or value.startswith("/")
        or "\\" in value
        or _contains_ascii_control(value)
        or any(segment in (".", "..") for segment in value.split("/"))
    ):
        raise ReleaseContractError("ARTIFACT_RELATIVE_PATH_INVALID")


def _validate_exact_tuple(value, prefix: str) -> None:
    if type(value) is not tuple:
        raise ReleaseContractError(f"{prefix}_TUPLE_REQUIRED")


def _validate_strict_text(value, code: str) -> None:
    if (
        type(value) is not str
        or not value
        or not value.strip()
        or value != value.strip()
        or _contains_ascii_control(value)
    ):
        raise ReleaseContractError(code)


def _validate_aware_datetime(value, code: str) -> None:
    if type(value) is not datetime:
        raise ReleaseContractError(code)
    try:
        offset = value.utcoffset()
    except Exception:
        raise ReleaseContractError(code) from None
    if offset is None:
        raise ReleaseContractError(code)


def _snapshot_utc_datetime(value, error_code: str) -> datetime:
    if type(value) is not datetime:
        raise ReleaseContractError(error_code)
    try:
        if value.utcoffset() is None:
            raise ValueError
        converted = value.astimezone(timezone.utc)
        return datetime(
            converted.year,
            converted.month,
            converted.day,
            converted.hour,
            converted.minute,
            converted.second,
            converted.microsecond,
            tzinfo=timezone.utc,
            fold=converted.fold,
        )
    except Exception:
        raise ReleaseContractError(error_code) from None


def _datetime_wire(value: datetime) -> str:
    canonical = _snapshot_utc_datetime(
        value,
        _DATETIME_CANONICALIZATION_ERROR,
    )
    return canonical.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _contains_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _encode_utf8(value: str, code: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ReleaseContractError(code) from None


def _uint64(value: int) -> bytes:
    try:
        return value.to_bytes(8, "big", signed=False)
    except (AttributeError, OverflowError):
        raise ReleaseContractError("RELEASE_FRAME_LENGTH_INVALID") from None


__all__ = [
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
