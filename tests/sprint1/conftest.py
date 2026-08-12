import pytest

from sprint0_fixtures import install_offline_test_guards


@pytest.fixture(autouse=True)
def sprint1_offline_harness(monkeypatch):
    """Apply the shared zero-network/persistence guard to Sprint 1 tests."""
    install_offline_test_guards(monkeypatch)
