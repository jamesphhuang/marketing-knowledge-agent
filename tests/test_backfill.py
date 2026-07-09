from marketing_knowledge_agent.backfill import generate_backfill_report


def test_backfill_report_generates_candidates_without_mutating_source(tmp_path):
    vault = tmp_path / "vault"
    (vault / "showcase").mkdir(parents=True)
    source = vault / "showcase" / "stancave.md"
    source.write_text(
        "# STANCAVE 透過 SHOPLINE POS 快閃店完成活動銷售任務\n\n"
        "SHOPLINE 團隊採訪運動電商品牌，內容提到 POS、快閃店、電商官網與運動 IP 合作。",
        encoding="utf-8",
    )
    output = tmp_path / "reports" / "metadata_backfill_candidates.md"

    report = generate_backfill_report(vault, output_path=output)

    assert report["candidate_count"] == 1
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "source_type: \"showcase\"" in text
    assert "content_category: \"showcase\"" in text
    assert "parent_source_type: \"blog\"" in text
    assert "status: \"draft\"" in text
    assert "publish_date: \"TODO\"" in text
    assert source.read_text(encoding="utf-8").startswith("# STANCAVE")


def test_backfill_report_ignores_valid_documents(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "valid.md").write_text(
        """---
title: "Valid Website Page"
source_type: website
product: [shopline]
industry: [ecommerce]
topic: [pricing]
funnel_stage: [consideration]
status: published
publish_date: 2026-06-24
---

Body
""",
        encoding="utf-8",
    )

    report = generate_backfill_report(vault)

    assert report["candidate_count"] == 0
    assert "No missing-frontmatter Markdown files found." in report["report"]
