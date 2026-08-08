import pytest

from sprint0_fixtures import install_offline_test_guards


@pytest.fixture(autouse=True)
def sprint0_offline_harness(monkeypatch):
    """Apply WP0 guards to every test below tests/sprint0/."""
    install_offline_test_guards(monkeypatch)
