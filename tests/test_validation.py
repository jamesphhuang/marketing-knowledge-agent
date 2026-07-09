from marketing_knowledge_agent.validation import SHOWCASE_PARENT_WARNING, validate_vault


def test_validate_reports_missing_frontmatter_and_skips_system_files(tmp_path):
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
    (vault / "missing.md").write_text("# Missing frontmatter\n\nBody", encoding="utf-8")
    (vault / "asset.docx").write_bytes(b"not a markdown file")
    (vault / "._hidden.md").write_bytes(b"\x00\x05\x16\x07")

    report = validate_vault(vault)

    assert report["summary"]["valid"] == 1
    assert report["summary"]["invalid"] == 1
    assert report["summary"]["skipped"] == 2

    missing = next(result for result in report["files"] if result["path"] == "missing.md")
    assert missing["errors"][0]["code"] == "missing_frontmatter"


def test_validate_warns_when_showcase_lacks_blog_relationship(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "showcase.md").write_text(
        """---
title: "Showcase Without Relationship"
source_type: showcase
product: [shopline]
industry: [retail]
topic: [case-study]
funnel_stage: [consideration]
status: published
publish_date: 2026-06-24
---

Body
""",
        encoding="utf-8",
    )

    report = validate_vault(vault)

    assert report["summary"]["valid"] == 1
    assert report["summary"]["warnings"] == 1
    result = report["files"][0]
    assert SHOWCASE_PARENT_WARNING in result["warnings"]


def test_validate_accepts_showcase_blog_relationship(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "showcase.md").write_text(
        """---
title: "Showcase With Relationship"
source_type: showcase
content_category: showcase
parent_source_type: blog
product: [shopline]
industry: [retail]
topic: [case-study]
funnel_stage: [consideration]
status: published
publish_date: 2026-06-24
---

Body
""",
        encoding="utf-8",
    )

    report = validate_vault(vault)

    assert report["summary"]["valid"] == 1
    assert report["summary"]["warnings"] == 0
    metadata = report["files"][0]["metadata"]
    assert metadata["content_category"] == "showcase"
    assert metadata["parent_source_type"] == "blog"
