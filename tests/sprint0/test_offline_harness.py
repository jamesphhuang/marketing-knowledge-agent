from __future__ import annotations

import io
import socket
import sqlite3
import urllib.request
from pathlib import Path

import pytest

from sprint0_fixtures import (
    ExternalNetworkBlocked,
    ProductionPersistenceBlocked,
    assert_isolated_test_path,
    assert_synthetic_fixture_tree,
    canonical_json_bytes,
    load_synthetic_html,
    load_synthetic_json,
    synthetic_cell_data_like,
    synthetic_governance_case,
    synthetic_ids,
)


@pytest.mark.parametrize("url", ["http://example.test/", "https://example.test/"])
def test_http_and_https_cannot_leave_process_without_explicit_guard_import(url):
    with pytest.raises(ExternalNetworkBlocked, match="external network disabled"):
        urllib.request.urlopen(url, timeout=0.01)


def test_direct_external_socket_connection_is_blocked():
    with pytest.raises(ExternalNetworkBlocked, match="external network disabled"):
        socket.create_connection(("203.0.113.10", 443), timeout=0.01)


def test_external_datagram_is_blocked():
    datagram = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(ExternalNetworkBlocked, match="external network disabled"):
            datagram.sendto(b"synthetic", ("203.0.113.10", 53))
    finally:
        datagram.close()


def test_local_and_in_memory_behavior_is_allowed():
    addresses = socket.getaddrinfo("localhost", 80)

    assert addresses
    assert io.BytesIO(b"synthetic").read() == b"synthetic"
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.parametrize(
    "relative_path",
    [
        "data/wp0-probe.txt",
        "reports/wp0-probe.txt",
        "obsidian_vault/wp0-probe.txt",
        ".mka/wp0-probe.txt",
    ],
)
def test_known_production_runtime_path_write_is_blocked(relative_path):
    with pytest.raises(
        ProductionPersistenceBlocked,
        match="production persistence disabled",
    ):
        Path(relative_path).write_text("synthetic", encoding="utf-8")


def test_known_production_sqlite_write_is_blocked():
    with pytest.raises(
        ProductionPersistenceBlocked,
        match="production persistence disabled",
    ):
        sqlite3.connect(".mka/wp0-probe.sqlite")


def test_tmp_path_write_is_allowed(tmp_path):
    output = assert_isolated_test_path(tmp_path / "output.json", tmp_path)

    output.write_text('{"kind":"synthetic"}\n', encoding="utf-8")

    assert output.read_text(encoding="utf-8") == '{"kind":"synthetic"}\n'


def test_synthetic_fixture_bundle_is_reusable_and_deterministic(
    synthetic_cell_data_like,
    synthetic_ids,
    synthetic_governance_case,
):
    fixture = load_synthetic_json("synthetic_fixture_bundle.json")
    reversed_fixture = dict(reversed(list(fixture.items())))

    assert canonical_json_bytes(fixture) == canonical_json_bytes(reversed_fixture)
    assert fixture["cell_data_like"] == synthetic_cell_data_like
    assert fixture["ids"] == synthetic_ids
    assert fixture["governance_case"] == synthetic_governance_case
    assert "Example Brand Alpha" in load_synthetic_html("article_synthetic.html")


def test_synthetic_fixture_tree_contains_required_safe_inputs():
    discovered_files = {path.name for path in assert_synthetic_fixture_tree()}
    required_files = {
        "article_synthetic.html",
        "synthetic_fixture_bundle.json",
    }

    assert required_files <= discovered_files


def test_synthetic_fixture_loader_rejects_paths_outside_fixture_root():
    with pytest.raises(ValueError, match="outside synthetic fixture root"):
        load_synthetic_json("../historical_inputs_manifest.json")
