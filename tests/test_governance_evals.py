from datetime import date

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.governance import GovernanceIndex, RestrictedCustomerRecord
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata, SearchFilters
from marketing_knowledge_agent.pipeline import ask_index, search_index
from marketing_knowledge_agent.query_gating import RESTRICTED_QUERY_REFUSAL


RESTRICTED_BRAND = "Restricted Eval Brand"


def test_ev_g1_restricted_brand_scrubbed(tmp_path):
    db_path, governance_index = _governance_fixture(tmp_path)

    answer = ask_index("campaign mention", db_path, governance_index=governance_index, limit=10)

    assert RESTRICTED_BRAND not in answer.answer
    assert all(RESTRICTED_BRAND not in citation.title for citation in answer.citations)


def test_ev_g2_external_intent_excludes_unquotable(tmp_path):
    db_path, _ = _governance_fixture(tmp_path)

    results = search_index("campaign", db_path, filters=SearchFilters(intent="external"), limit=20)

    assert all(result.chunk.metadata.record_type != "pending_metric" for result in results)
    assert all(result.chunk.metadata.can_quote_externally for result in results)


def test_ev_g3_channel_filter_returns_only_matching(tmp_path):
    db_path, _ = _governance_fixture(tmp_path)

    results = search_index(
        "campaign",
        db_path,
        filters=SearchFilters(record_type=["public_metric"], exposure_channel=["press_release"]),
        limit=20,
    )

    assert results
    assert all("press_release" in result.chunk.metadata.allowed_exposure_channels for result in results)


def test_ev_g4_denylist_query_refused(tmp_path):
    db_path, governance_index = _governance_fixture(tmp_path)
    audit_path = tmp_path / "audit.csv"

    answer = ask_index(
        RESTRICTED_BRAND,
        db_path,
        governance_index=governance_index,
        audit_log_path=audit_path,
    )

    assert answer.answer == RESTRICTED_QUERY_REFUSAL
    assert answer.citations == []
    assert "denylist_query_hit" in audit_path.read_text(encoding="utf-8")


def test_ev_g5_no_result_no_fabrication(tmp_path):
    db_path, _ = _governance_fixture(tmp_path)

    answer = ask_index(
        "campaign",
        db_path,
        filters=SearchFilters(topic=["missing-topic"]),
    )

    assert answer.citations == []
    assert "%" not in answer.answer


def test_ev_g6_merchant_risk_warns(tmp_path):
    db_path, _ = _governance_fixture(tmp_path)

    answer = ask_index(
        "closed merchant campaign",
        db_path,
        filters=SearchFilters(record_type=["merchant_case"]),
        limit=10,
    )

    assert answer.warnings


def _governance_fixture(tmp_path):
    records = [
        _metadata(
            title="Restricted mention source",
            record_type="content_asset",
            content=f"campaign mention involving {RESTRICTED_BRAND}",
        ),
        _metadata(
            title="Pending metric",
            record_type="pending_metric",
            content="campaign pending metric",
            data_classification="internal",
            can_quote_externally=False,
        ),
        _metadata(
            title="Public metric missing channels",
            record_type="public_metric",
            content="campaign metric missing channel",
            allowed_exposure_channels=[],
        ),
        _metadata(
            title="Approved press metric",
            record_type="public_metric",
            content="campaign approved metric",
            allowed_exposure_channels=["press_release"],
        ),
        _metadata(
            title="Closed merchant case",
            record_type="merchant_case",
            content="closed merchant campaign",
            merchant_status="已關店",
            data_classification="internal",
            can_quote_externally=False,
        ),
    ]
    documents = [
        Document(id=f"doc-{index}", metadata=metadata, content=content)
        for index, (metadata, content) in enumerate(records, start=1)
    ]
    db_path = tmp_path / "governance.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=RESTRICTED_BRAND)])
    return db_path, governance_index


def _metadata(title, record_type, content, **overrides):
    payload = {
        "title": title,
        "source_type": "database",
        "record_type": record_type,
        "status": "published",
        "publish_date": date(2026, 7, 1),
        "source_path": f"synthetic:{title}",
        "source_sheet": "Synthetic",
        "source_row": 1,
        "data_classification": "public",
        "can_quote_externally": True,
        "allowed_exposure_channels": [],
    }
    payload.update(overrides)
    return DocumentMetadata(**payload), content
