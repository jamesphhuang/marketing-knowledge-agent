from pathlib import Path


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
