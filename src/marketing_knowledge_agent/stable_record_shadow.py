"""Read-only Stable Record V2 shadow identity resolution.

This consumer accepts only an explicitly selected, externally pinned authority package that is
still materialized-not-activated while ``row_v1`` remains retained. It exposes no mutation API.

``SHADOW_VALIDATED != ACTIVATION_AUTHORIZED``: validation here proves only that this read-side
consumer can safely observe additive metadata. It does not activate Stable Record V2, retire
``row_v1``, authorize production re-indexing, or change any mutation key.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Iterable, Mapping, Optional, Tuple

from .stable_record_authority import (
    ACTIVATION_STATUS_NOT_ACTIVATED,
    AUTHORITY_RECORD_STATUS_CONTINUATION,
    AUTHORITY_RECORD_STATUS_NEW,
    AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
    IDENTITY_ORIGIN_AUTHORITY_NEW,
    IDENTITY_ORIGIN_LEGACY_CONTINUATION,
    LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY,
    LEGACY_SOURCE_SCHEME_ROW_V1,
    MANIFEST_HASH_FIELD,
    PACKAGE_MATERIALIZED_FIELD,
    PRODUCTION_REINDEX_AUTHORIZED_FIELD,
    RECORD_IDENTITY_SCHEME,
    ROW_V1_RETIRED_FIELD,
    ROW_V1_STATUS_RETAINED,
    STABLE_RECORD_V2_ACTIVATED_FIELD,
    AuthorityEvidencePins,
    StableRecordAuthority,
    StableRecordAuthorityError,
    load_authority_package,
    qualify_legacy_record_id,
    validate_authority,
)
from .stable_record_crosswalk import STABLE_ID_RE


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StableRecordShadowError(ValueError):
    """Raised when a shadow authority or lineage cannot be trusted safely."""


class ShadowResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    NOT_APPLICABLE = "not_applicable"
    UNRESOLVED = "unresolved"
    AUTHORITY_ONLY_NO_LEGACY_BINDING = "authority_only_no_legacy_binding"


@dataclass(frozen=True)
class ShadowResolution:
    status: ShadowResolutionStatus
    stable_record_id: Optional[str] = None
    qualified_legacy_record_id: Optional[str] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class StableRecordShadow:
    """Immutable read-side resolver for one externally pinned authority and workbook lineage."""

    authority_manifest_hash: str
    authority_status: str
    row_v1_workbook_sha256: str
    _by_legacy_id: Mapping[str, str]
    _legacy_id_by_stable_id: Mapping[str, str]
    _authority_only_ids: Tuple[str, ...]

    @property
    def authority_only_ids(self) -> Tuple[str, ...]:
        return self._authority_only_ids

    def resolve(
        self,
        *,
        record_type: object,
        source_sheet: object,
        source_row: object,
    ) -> ShadowResolution:
        if str(record_type).strip().lower() != "merchant_case":
            return ShadowResolution(
                status=ShadowResolutionStatus.NOT_APPLICABLE,
                detail="Stable merchant-case identity applies only to merchant_case records.",
            )
        try:
            qualified = qualify_legacy_record_id(
                self.row_v1_workbook_sha256, str(source_sheet), source_row
            )
        except StableRecordAuthorityError as exc:
            return ShadowResolution(
                status=ShadowResolutionStatus.UNRESOLVED,
                detail=f"Qualified row_v1 lineage is unavailable: {exc}",
            )
        stable_record_id = self._by_legacy_id.get(qualified)
        if stable_record_id is None:
            return ShadowResolution(
                status=ShadowResolutionStatus.UNRESOLVED,
                qualified_legacy_record_id=qualified,
                detail="No authority continuation is bound to this qualified row_v1 identity.",
            )
        return ShadowResolution(
            status=ShadowResolutionStatus.RESOLVED,
            stable_record_id=stable_record_id,
            qualified_legacy_record_id=qualified,
        )

    def resolve_stable_record_id(self, stable_record_id: object) -> ShadowResolution:
        """Inspect whether a stable ID has a current legacy binding, without inventing one."""
        value = str(stable_record_id).strip()
        if not STABLE_ID_RE.match(value):
            return ShadowResolution(
                status=ShadowResolutionStatus.UNRESOLVED,
                detail="Stable record ID is missing or malformed.",
            )
        if value in self._authority_only_ids:
            return ShadowResolution(
                status=ShadowResolutionStatus.AUTHORITY_ONLY_NO_LEGACY_BINDING,
                stable_record_id=value,
                detail="Authority-only identity has no row_v1 predecessor.",
            )
        qualified = self._legacy_id_by_stable_id.get(value)
        if qualified is None:
            return ShadowResolution(
                status=ShadowResolutionStatus.UNRESOLVED,
                detail="Stable record ID is not present in this pinned authority.",
            )
        return ShadowResolution(
            status=ShadowResolutionStatus.RESOLVED,
            stable_record_id=value,
            qualified_legacy_record_id=qualified,
        )

    def coverage_summary(self, resolutions: Iterable[ShadowResolution]) -> Dict[str, object]:
        applicable = [
            resolution
            for resolution in resolutions
            if resolution.status is not ShadowResolutionStatus.NOT_APPLICABLE
        ]
        return {
            "authority_manifest_hash": self.authority_manifest_hash,
            "authority_status": self.authority_status,
            "shadow_mode": True,
            "merchant_records_seen": len(applicable),
            "resolved": sum(
                resolution.status is ShadowResolutionStatus.RESOLVED
                for resolution in applicable
            ),
            "unresolved": sum(
                resolution.status is ShadowResolutionStatus.UNRESOLVED
                for resolution in applicable
            ),
            "authority_only": len(self._authority_only_ids),
            "stable_record_v2_activated": False,
            "row_v1_retired": False,
        }


def load_stable_record_shadow(
    *,
    authority_dir: Path,
    expected_manifest_hash: str,
    row_v1_workbook_sha256: str,
) -> StableRecordShadow:
    """Load a shadow resolver from explicit inputs; never discovers a production package."""
    if not isinstance(expected_manifest_hash, str) or not _SHA256_RE.match(expected_manifest_hash):
        raise StableRecordShadowError(
            "expected manifest hash is required and must be a lowercase sha256 hexdigest"
        )
    try:
        # Reuse the canonical loader for the package's self-seals. This function deliberately does
        # not enumerate the directory: it reads only the manifest-declared canonical package files,
        # so the activation F3 exact-file-set gate is not being approximated or claimed here.
        manifest, loaded_rows = load_authority_package(Path(authority_dir))
        actual_manifest_hash = manifest.get(MANIFEST_HASH_FIELD)
        if not isinstance(actual_manifest_hash, str) or not hmac.compare_digest(
            actual_manifest_hash, expected_manifest_hash
        ):
            raise StableRecordShadowError(
                "authority external manifest hash pin does not match the loaded package "
                f"(expected {expected_manifest_hash}, actual {actual_manifest_hash})"
            )

        _validate_shadow_manifest(manifest)
        authority = _loaded_authority(manifest, loaded_rows)
        validate_authority(authority)
        by_legacy_id, legacy_id_by_stable_id, authority_only_ids = _shadow_bindings(
            authority.rows
        )
        if authority.identity_continuation_count != len(by_legacy_id):
            raise StableRecordShadowError(
                "manifest identity_continuation_count does not match qualified legacy bindings"
            )
        if authority.new_identity_count != len(authority_only_ids):
            raise StableRecordShadowError(
                "manifest new_identity_count does not match authority-only identities"
            )

        # Reuse the canonical qualifier as the workbook-lineage validator too. No second hash or
        # row-key policy is introduced by this consumer.
        qualify_legacy_record_id(row_v1_workbook_sha256, "shadow-lineage-check", 1)
    except StableRecordShadowError:
        raise
    except (KeyError, TypeError, ValueError, StableRecordAuthorityError) as exc:
        raise StableRecordShadowError(f"authority is not shadow-readable: {exc}") from exc

    return StableRecordShadow(
        authority_manifest_hash=actual_manifest_hash,
        authority_status=str(manifest["authority_status"]),
        row_v1_workbook_sha256=row_v1_workbook_sha256,
        _by_legacy_id=MappingProxyType(by_legacy_id),
        _legacy_id_by_stable_id=MappingProxyType(legacy_id_by_stable_id),
        _authority_only_ids=tuple(sorted(authority_only_ids)),
    )


def _validate_shadow_manifest(manifest: Mapping[str, object]) -> None:
    expected = {
        "record_identity_scheme": RECORD_IDENTITY_SCHEME,
        "authority_status": AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED,
        "activation_status": ACTIVATION_STATUS_NOT_ACTIVATED,
        PACKAGE_MATERIALIZED_FIELD: True,
        STABLE_RECORD_V2_ACTIVATED_FIELD: False,
        ROW_V1_RETIRED_FIELD: False,
        PRODUCTION_REINDEX_AUTHORIZED_FIELD: False,
    }
    for field_name, expected_value in expected.items():
        if manifest.get(field_name) != expected_value:
            raise StableRecordShadowError(
                f"shadow requires {field_name}={expected_value!r}; "
                f"package declares {manifest.get(field_name)!r}"
            )


def _loaded_authority(
    manifest: Mapping[str, object], rows: Iterable[Mapping[str, str]]
) -> StableRecordAuthority:
    source_proposal = _mapping(manifest.get("source_proposal"), "source_proposal")
    source_decision = _mapping(
        manifest.get("source_decision_artifact"), "source_decision_artifact"
    )
    pins = AuthorityEvidencePins(
        proposal_registry_sha256=str(source_proposal.get("registry_sha256", "")),
        proposal_crosswalk_sha256=str(source_proposal.get("crosswalk_sha256", "")),
        proposal_content_digest=str(source_proposal.get("content_digest", "")),
        proposal_manifest_hash=str(source_proposal.get("manifest_hash", "")),
        decision_artifact_sha256=str(source_decision.get("decisions_sha256", "")),
        reviewer=str(manifest.get("reviewer", "")),
        reviewed_at=str(manifest.get("review_date", "")),
    )
    row_tuple = tuple(dict(row) for row in rows)
    return StableRecordAuthority(
        rows=row_tuple,
        pins=pins,
        record_count=_integer(manifest.get("record_count"), "record_count"),
        identity_continuation_count=_integer(
            manifest.get("identity_continuation_count"), "identity_continuation_count"
        ),
        new_identity_count=_integer(manifest.get("new_identity_count"), "new_identity_count"),
        confidence_counts={
            str(key): _integer(value, f"confidence_counts.{key}")
            for key, value in _mapping(
                manifest.get("confidence_counts"), "confidence_counts"
            ).items()
        },
        asset_review_required_ids=_string_tuple(
            manifest.get("asset_review_required_records"),
            "asset_review_required_records",
        ),
        alias_decision_required_ids=_string_tuple(
            manifest.get("alias_decision_required_records"),
            "alias_decision_required_records",
        ),
        payload_change_ids=_string_tuple(
            manifest.get("payload_change_records"), "payload_change_records"
        ),
        stable_id_set_digest=str(manifest.get("stable_id_set_digest", "")),
        source_proposal_manifest=dict(source_proposal),
    )


def _shadow_bindings(
    rows: Iterable[Mapping[str, str]],
) -> Tuple[Dict[str, str], Dict[str, str], Tuple[str, ...]]:
    by_legacy_id: Dict[str, str] = {}
    legacy_id_by_stable_id: Dict[str, str] = {}
    authority_only_ids = []
    continuation_count = 0
    new_count = 0

    for row in rows:
        stable_record_id = row["stable_record_id"]
        if row["record_type"] != "merchant_case":
            raise StableRecordShadowError(
                f"{stable_record_id}: shadow authority row must be record_type='merchant_case'"
            )
        if row["authority_status"] != AUTHORITY_STATUS_MATERIALIZED_NOT_ACTIVATED:
            raise StableRecordShadowError(
                f"{stable_record_id}: authority_status must remain materialized_not_activated"
            )
        if row["activation_status"] != ACTIVATION_STATUS_NOT_ACTIVATED:
            raise StableRecordShadowError(
                f"{stable_record_id}: activation_status must remain not_activated"
            )
        if row["row_v1_status"] != ROW_V1_STATUS_RETAINED:
            raise StableRecordShadowError(
                f"{stable_record_id}: row_v1_status must remain retained_not_retired"
            )
        if row["legacy_source_row_role"] != LEGACY_SOURCE_ROW_ROLE_AUDIT_ONLY:
            raise StableRecordShadowError(
                f"{stable_record_id}: bare legacy row must remain audit metadata only"
            )

        if row["authority_record_status"] == AUTHORITY_RECORD_STATUS_CONTINUATION:
            continuation_count += 1
            if row["identity_origin"] != IDENTITY_ORIGIN_LEGACY_CONTINUATION:
                raise StableRecordShadowError(
                    f"{stable_record_id}: continuation has invalid identity_origin"
                )
            if row["legacy_source_scheme"] != LEGACY_SOURCE_SCHEME_ROW_V1:
                raise StableRecordShadowError(
                    f"{stable_record_id}: continuation is not bound to row_v1"
                )
            qualified = qualify_legacy_record_id(
                row["legacy_workbook_sha256"],
                row["legacy_source_sheet"],
                row["legacy_source_row"],
            )
            if row["legacy_source_record_id"] != qualified:
                raise StableRecordShadowError(
                    f"{stable_record_id}: legacy_source_record_id does not equal canonical "
                    "qualified row_v1 identity"
                )
            if qualified in by_legacy_id:
                raise StableRecordShadowError(
                    "duplicate qualified legacy identity binds more than one stable_record_id: "
                    f"{qualified}"
                )
            by_legacy_id[qualified] = stable_record_id
            legacy_id_by_stable_id[stable_record_id] = qualified
        elif row["authority_record_status"] == AUTHORITY_RECORD_STATUS_NEW:
            new_count += 1
            if row["identity_origin"] != IDENTITY_ORIGIN_AUTHORITY_NEW:
                raise StableRecordShadowError(
                    f"{stable_record_id}: authority-only row has invalid identity_origin"
                )
            legacy_values = (
                row["legacy_source_record_id"],
                row["legacy_source_scheme"],
                row["legacy_source_sheet"],
                row["legacy_source_row"],
                row["legacy_workbook_sha256"],
            )
            if any(legacy_values):
                raise StableRecordShadowError(
                    f"{stable_record_id}: authority-only row must not fabricate legacy lineage"
                )
            authority_only_ids.append(stable_record_id)
        else:
            raise StableRecordShadowError(
                f"{stable_record_id}: unsupported authority_record_status"
            )

    if continuation_count != len(by_legacy_id):
        raise StableRecordShadowError("not every continuation has one unique qualified legacy identity")
    if new_count != len(authority_only_ids):
        raise StableRecordShadowError("authority-only identity accounting is inconsistent")
    return by_legacy_id, legacy_id_by_stable_id, tuple(authority_only_ids)


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StableRecordShadowError(f"manifest {field_name} must be an object")
    return value


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StableRecordShadowError(f"manifest {field_name} must be a non-negative integer")
    return value


def _string_tuple(value: object, field_name: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StableRecordShadowError(f"manifest {field_name} must be a list of strings")
    return tuple(value)
