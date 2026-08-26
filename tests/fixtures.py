import hashlib
import json
import sys
from pathlib import Path

from marketing_knowledge_agent import record_identity_lineage
from marketing_knowledge_agent.record_identity_lineage import (
    APPLY_LINEAGE_FILENAME,
    MERCHANT_SHAPE_FIELDS,
    PREVIEW_LINEAGE_FILENAME,
    RECORD_IDENTITY_SCHEME_VERSION,
    _hash_json,
    apply_row_identity_surface_digest,
    apply_row_identity_surface_entries,
    load_lineage_contract,
    observe_preview_merchant_surface,
)

# Set only by ``use_synthetic_row_v1_lineage_contract``; None means the packaged contract is live.
_SYNTHETIC_CONTRACT_ROOT = None


def write_regression_vault(base_path: Path) -> Path:
    vault = base_path / "vault"
    (vault / "blog").mkdir(parents=True)
    (vault / "showcase").mkdir(parents=True)
    (vault / "social").mkdir(parents=True)
    (vault / "youtube").mkdir(parents=True)

    _write(
        vault / "blog" / "product-a-roi-pricing.md",
        """---
title: "Product A 製造業 ROI 定價指南"
source_type: blog
product: [product-a]
industry: [manufacturing]
topic: [pricing, roi]
funnel_stage: [consideration]
status: published
publish_date: 2026-01-15
updated_date: 2026-02-01
canonical_url: "https://example.com/blog/product-a-roi-pricing"
---

Product A 的製造業中段內容應該聚焦 ROI、導入成本與風險降低。
內容可搭配 ROI calculator 與製造業 case study。
""",
    )
    _write(
        vault / "showcase" / "manufacturing-product-a-case.md",
        """---
title: "製造業客戶使用 Product A 提升轉換率案例"
source_type: showcase
content_category: showcase
parent_source_type: blog
product: [product-a]
industry: [manufacturing]
topic: [pricing, case-study, conversion]
funnel_stage: [consideration, decision]
status: published
publish_date: 2025-11-05
updated_date: 2026-03-10
canonical_url: "https://example.com/showcase/manufacturing-product-a"
---

This showcase is one of the pricing case studies for Product A in manufacturing.
在漏斗中段，這個案例可用來說明採購團隊如何評估 pricing 與內部 adoption。
""",
    )
    _write(
        vault / "social" / "archived-product-b-launch-post.md",
        """---
title: "Product B 舊版 Launch 社群貼文"
source_type: social
product: [product-b]
industry: [retail]
topic: [launch, social-copy]
funnel_stage: [awareness]
status: archived
publish_date: 2024-04-20
updated_date: 2024-05-01
canonical_url: "https://example.com/social/product-b-launch"
---

Launch social post draft for Product B. 這份社群內容保留作為歷史參考，不能直接複製到新的 campaign。
""",
    )
    _write(
        vault / "youtube" / "deprecated-product-c-demo-transcript.md",
        """---
title: "Product C 舊版 Demo YouTube 逐字稿"
source_type: youtube
product: [product-c]
industry: [technology]
topic: [demo, transcript]
funnel_stage: [decision]
status: deprecated
publish_date: 2023-10-10
updated_date: 2023-10-10
canonical_url: "https://youtube.com/watch?v=mock-product-c"
---

Product C demo transcript showing the previous navigation flow.
此逐字稿只能作為歷史參考，不可直接引用這份 deprecated transcript。
""",
    )
    return vault


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def use_synthetic_row_v1_lineage_contract(monkeypatch, tmp_path) -> Path:
    """Give synthetic preview fixtures a lineage contract that describes them.

    The fixtures invent a handful of merchant rows; the shipped contract pins the 120-row
    production workbook. A fixture that declared the shipped lineage over its own invented rows
    would be asserting a lineage it does not have — the precise forgery ``resolve_preview_lineage``
    now rejects — so it gets its own contract instead of a hole in the guard.

    Tests that exercise the *production* lineage (the 20260708 workbook, the live preview
    directory, the packaged manifest) must not request this, and do not.
    """
    root = tmp_path / "_row_v1_synthetic_authority"
    root.mkdir(parents=True, exist_ok=True)
    _write_synthetic_lineage_contract(root, None)
    monkeypatch.setattr(record_identity_lineage, "_contract_root", lambda: root)
    monkeypatch.setattr(sys.modules[__name__], "_SYNTHETIC_CONTRACT_ROOT", root)
    return root


def write_row_v1_preview_lineage(preview_dir: Path) -> Path:
    """Declare a row_v1 workbook lineage over a synthetic preview directory.

    The declaration is derived from the directory's own ``merchant_cases.json``, so it states the
    lineage the preview actually has rather than one it was told to claim. When a synthetic
    contract root is installed, that same observation is what the contract pins, which is what
    lets the fixture reach ``LINEAGE_MATCH`` honestly.
    """
    preview_dir = Path(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    observed, _ = observe_preview_merchant_surface(preview_dir)
    if _SYNTHETIC_CONTRACT_ROOT is not None:
        _write_synthetic_lineage_contract(_SYNTHETIC_CONTRACT_ROOT, observed)

    payload = {
        "record_identity_scheme_version": RECORD_IDENTITY_SCHEME_VERSION,
        "workbook": load_lineage_contract()["lineage_workbook"],
    }
    if observed is not None:
        # Both digests are taken from the fixture's own payload, never asserted independently, so
        # the declaration states the lineage the preview actually has.
        payload["merchant_row_identity_surface_digest"] = observed[
            "merchant_row_identity_surface_digest"
        ]
        payload["merchant_payload_semantic_digest"] = observed[
            "merchant_payload_semantic_digest"
        ]
    path = preview_dir / PREVIEW_LINEAGE_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pin_synthetic_preview_payload(preview_dir: Path) -> Path:
    """Add the pre-guard payload proof to the synthetic contract, for grandfathering tests.

    Opt-in, because most fixtures need the opposite: a preview with no declaration and no pinned
    payload must stay ``LINEAGE_UNBOUND``.
    """
    if _SYNTHETIC_CONTRACT_ROOT is None:
        raise RuntimeError("no synthetic lineage contract is installed")
    preview_dir = Path(preview_dir)
    observed, _ = observe_preview_merchant_surface(preview_dir)
    pins = []
    for filename in ("merchant_cases.json",):
        payload = (preview_dir / filename).read_bytes()
        pins.append(
            {
                "relative_path": filename,
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
                "expected_size": len(payload),
            }
        )
    return _write_synthetic_lineage_contract(
        _SYNTHETIC_CONTRACT_ROOT, observed, preview_payload=pins
    )


def _write_synthetic_lineage_contract(root: Path, observed, preview_payload=None) -> Path:
    """Pin a contract to an observed synthetic merchant shape, self-integrity hash and all."""
    shape = {
        field: (observed[field] if observed is not None else None)
        for field in MERCHANT_SHAPE_FIELDS
    }
    # Deterministic stand-in for a workbook the fixtures never actually produce.
    identity = json.dumps(shape, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body = {
        "authority": "row_v1_workbook_lineage",
        "schema_version": 1,
        "record_identity_scheme_version": RECORD_IDENTITY_SCHEME_VERSION,
        "purpose": (
            "Test-only lineage contract describing a synthetic preview fixture. Never shipped: "
            "the packaged contract pins the production workbook."
        ),
        "lineage_workbook": {
            "filename": "synthetic-row-v1-lineage.xlsx",
            "sha256": hashlib.sha256(f"synthetic-row-v1:{identity}".encode("utf-8")).hexdigest(),
            "size": len(identity),
            "merchant_header_row": 6,
            "merchant_header_fingerprint": hashlib.sha256(
                f"synthetic-row-v1-header:{identity}".encode("utf-8")
            ).hexdigest(),
            **shape,
        },
    }
    if preview_payload is not None:
        body["preview_payload"] = preview_payload
    manifest = {**body, "manifest_hash": _hash_json(body)}
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_row_v1_apply_lineage(apply_dir: Path) -> Path:
    """Declare the pinned row_v1 lineage on a synthetic apply preview directory.

    The digest is recomputed from the directory's own records, so a binding copied between apply
    previews still fails closed.
    """
    apply_dir = Path(apply_dir)
    payload = {
        "record_identity_scheme_version": RECORD_IDENTITY_SCHEME_VERSION,
        "workbook": load_lineage_contract()["lineage_workbook"],
        "row_identity_surface_digest": apply_row_identity_surface_digest(
            apply_row_identity_surface_entries(apply_dir)
        ),
    }
    path = apply_dir / APPLY_LINEAGE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_row_v1_sync_evidence(base_path: Path, preview_files) -> dict:
    """Create a real temp apply -> sync -> Vault receipt chain for content-index tests."""
    from marketing_knowledge_agent.content_index_lineage import ContentIndexLineageEvidence
    from marketing_knowledge_agent.obsidian_sync import create_sync_plan, execute_sync_plan

    base_path = Path(base_path)
    apply_dir = base_path / "apply"
    for relative, content in preview_files.items():
        path = apply_dir / "approved_vault_preview" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    governance = apply_dir / "governance_table_preview"
    governance.mkdir(parents=True, exist_ok=True)
    (governance / "restricted_customers.json").write_text("[]", encoding="utf-8")
    (apply_dir / "apply_decisions_summary.md").write_text(
        """# Apply Review Decisions Preview Summary

## Conservation
- test fixture conservation ok=yes

## Whitelist Assertions
- Conservation ok: yes
- Restricted whitelist assertion: passed
- Pending metric vault assertion: passed
""",
        encoding="utf-8",
    )
    write_row_v1_apply_lineage(apply_dir)

    vault = base_path / "vault"
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    (vault / "MKA").mkdir(parents=True, exist_ok=True)
    sync_dir = base_path / "sync"
    plan = create_sync_plan(apply_dir, vault, output_dir=sync_dir)
    execution = execute_sync_plan(plan["json_path"], vault, confirm=True)
    evidence = ContentIndexLineageEvidence(
        apply_dir=apply_dir,
        sync_plan_path=Path(plan["json_path"]),
        sync_manifest_path=Path(execution["manifest_path"]),
    )
    return {
        "apply_dir": apply_dir,
        "vault": vault,
        "plan": plan,
        "execution": execution,
        "evidence": evidence,
    }
