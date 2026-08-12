from pathlib import Path
import socket
from urllib.request import urlopen

import pytest

from sprint0_fixtures import (
    ExternalNetworkBlocked,
    ProductionPersistenceBlocked,
    WORKSPACE_ROOT,
)


def test_sprint1_harness_blocks_external_network():
    with pytest.raises(ExternalNetworkBlocked, match="external network disabled"):
        socket.getaddrinfo("example.com", 443)

    with pytest.raises(ExternalNetworkBlocked, match="external network disabled"):
        urlopen("https://example.com/synthetic", timeout=0.1)


def test_sprint1_harness_blocks_production_runtime_persistence():
    prohibited = WORKSPACE_ROOT / "data" / "sprint1-wp0-guard-sentinel"

    with pytest.raises(
        ProductionPersistenceBlocked,
        match="production persistence disabled",
    ):
        prohibited.write_text("synthetic", encoding="utf-8")


def test_sprint1_harness_allows_isolated_temporary_persistence(tmp_path: Path):
    target = tmp_path / "synthetic.txt"
    target.write_text("synthetic", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "synthetic"
