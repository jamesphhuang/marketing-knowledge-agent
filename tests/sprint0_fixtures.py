from __future__ import annotations

import builtins
import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Union
from urllib.parse import urlsplit

import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "google_sheets_sprint0"
PRODUCTION_RUNTIME_ROOTS = ("data", "reports", "obsidian_vault", ".mka")

_WRITE_MODES = frozenset("wax+")
_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_RDWR
    | os.O_APPEND
    | os.O_CREAT
    | os.O_TRUNC
    | os.O_EXCL
)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_MARKERS = (
    b"-----begin private key-----",
    b"api_key",
    b"client_email",
    b"client_secret",
    b"credentials",
    b"google_token",
    b"private_key",
    b"refresh_token",
    b"access_token",
    b"slack_bot_token",
    b"slack_app_token",
    b"service_account",
)


class ExternalNetworkBlocked(AssertionError):
    pass


class ProductionPersistenceBlocked(AssertionError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def load_synthetic_json(name: str) -> Dict[str, Any]:
    path = _synthetic_fixture_path(name, ".json")
    return json.loads(path.read_text(encoding="utf-8"))


def load_synthetic_html(name: str) -> str:
    path = _synthetic_fixture_path(name, ".html")
    return path.read_text(encoding="utf-8")


def assert_synthetic_fixture_tree() -> List[Path]:
    root = SYNTHETIC_FIXTURE_ROOT.resolve(strict=False)
    paths = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and not path.name.startswith("._")
    )
    if not paths:
        raise AssertionError("synthetic fixture tree is empty")

    for path in paths:
        if path.suffix not in {".json", ".html"}:
            raise AssertionError("unsupported synthetic fixture type")
        payload = path.read_bytes()
        lowered = payload.lower()
        if b"synthetic" not in lowered and b"example" not in lowered:
            raise AssertionError("fixture lacks an explicit synthetic sentinel")
        for marker in _SENSITIVE_MARKERS:
            if marker in lowered:
                raise AssertionError("sensitive marker found in synthetic fixture")
        _assert_fixture_urls_are_reserved(payload.decode("utf-8"))
    return paths


def assert_isolated_test_path(path: Path, temp_root: Path) -> Path:
    candidate = _resolved_path(path)
    isolated_root = _resolved_path(temp_root)
    assert_not_production_persistence_path(candidate)
    assert_not_production_persistence_path(isolated_root)
    if not _is_within(candidate, isolated_root):
        raise AssertionError("test output must remain below its isolated temp root")
    return candidate


def assert_not_production_persistence_path(
    path: Union[str, os.PathLike],
    workspace_root: Path = WORKSPACE_ROOT,
) -> Path:
    candidate = _resolved_path(path)
    for relative_root in PRODUCTION_RUNTIME_ROOTS:
        production_root = _resolved_path(workspace_root / relative_root)
        if _is_within(candidate, production_root):
            raise ProductionPersistenceBlocked(
                "production persistence disabled for Sprint 0 tests"
            )
    return candidate


def install_offline_test_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_network_guard(monkeypatch)
    _install_persistence_guard(monkeypatch)


@pytest.fixture
def synthetic_cell_data_like():
    fixture = load_synthetic_json("synthetic_fixture_bundle.json")
    return deepcopy(fixture["cell_data_like"])


@pytest.fixture
def synthetic_ids():
    fixture = load_synthetic_json("synthetic_fixture_bundle.json")
    return deepcopy(fixture["ids"])


@pytest.fixture
def synthetic_governance_case():
    fixture = load_synthetic_json("synthetic_fixture_bundle.json")
    return deepcopy(fixture["governance_case"])


def _synthetic_fixture_path(name: str, expected_suffix: str) -> Path:
    if Path(name).suffix != expected_suffix:
        raise ValueError("unexpected synthetic fixture type")
    root = SYNTHETIC_FIXTURE_ROOT.resolve(strict=False)
    path = (root / name).resolve(strict=False)
    if not _is_within(path, root):
        raise ValueError("fixture path is outside synthetic fixture root")
    return path


def _assert_fixture_urls_are_reserved(text: str) -> None:
    for raw_url in _URL_PATTERN.findall(text):
        hostname = (urlsplit(raw_url).hostname or "").lower()
        if not (
            hostname.endswith(".test")
            or hostname in {"example.com", "example.net", "example.org"}
        ):
            raise AssertionError("non-reserved URL found in synthetic fixture")


def _install_network_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto

    def guarded_getaddrinfo(host, *args, **kwargs):
        _assert_local_network_host(host)
        return original_getaddrinfo(host, *args, **kwargs)

    def guarded_gethostbyname(host):
        _assert_local_network_host(host)
        return original_gethostbyname(host)

    def guarded_gethostbyname_ex(host):
        _assert_local_network_host(host)
        return original_gethostbyname_ex(host)

    def guarded_create_connection(address, *args, **kwargs):
        _assert_local_network_host(address[0])
        return original_create_connection(address, *args, **kwargs)

    def guarded_connect(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            _assert_local_network_host(address[0])
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            _assert_local_network_host(address[0])
        return original_connect_ex(sock, address)

    def guarded_sendto(sock, data, *args):
        address = args[-1]
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            _assert_local_network_host(address[0])
        return original_sendto(sock, data, *args)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", guarded_gethostbyname)
    monkeypatch.setattr(socket, "gethostbyname_ex", guarded_gethostbyname_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", guarded_sendto)


def _assert_local_network_host(host: Any) -> None:
    if host is None:
        return
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="strict")
    normalized = str(host).strip().strip("[]").lower()
    if normalized == "localhost":
        return
    try:
        if ipaddress.ip_address(normalized).is_loopback:
            return
    except ValueError:
        pass
    raise ExternalNetworkBlocked("external network disabled for Sprint 0 tests")


def _install_persistence_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    original_builtin_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_mkdir = os.mkdir
    original_remove = os.remove
    original_unlink = os.unlink
    original_rmdir = os.rmdir
    original_rename = os.rename
    original_replace = os.replace
    original_link = os.link
    original_symlink = os.symlink
    original_sqlite_connect = sqlite3.connect

    def guarded_builtin_open(file, mode="r", *args, **kwargs):
        _guard_write_mode(file, mode)
        return original_builtin_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        _guard_write_mode(file, mode)
        return original_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & _WRITE_FLAGS:
            _guard_path_value(path)
        return original_os_open(path, flags, *args, **kwargs)

    def guarded_mkdir(path, *args, **kwargs):
        _guard_path_value(path)
        return original_mkdir(path, *args, **kwargs)

    def guarded_remove(path, *args, **kwargs):
        _guard_path_value(path)
        return original_remove(path, *args, **kwargs)

    def guarded_unlink(path, *args, **kwargs):
        _guard_path_value(path)
        return original_unlink(path, *args, **kwargs)

    def guarded_rmdir(path, *args, **kwargs):
        _guard_path_value(path)
        return original_rmdir(path, *args, **kwargs)

    def guarded_rename(source, destination, *args, **kwargs):
        _guard_path_value(source)
        _guard_path_value(destination)
        return original_rename(source, destination, *args, **kwargs)

    def guarded_replace(source, destination, *args, **kwargs):
        _guard_path_value(source)
        _guard_path_value(destination)
        return original_replace(source, destination, *args, **kwargs)

    def guarded_link(source, destination, *args, **kwargs):
        _guard_path_value(source)
        _guard_path_value(destination)
        return original_link(source, destination, *args, **kwargs)

    def guarded_symlink(source, destination, *args, **kwargs):
        _guard_path_value(destination)
        return original_symlink(source, destination, *args, **kwargs)

    def guarded_sqlite_connect(database, *args, **kwargs):
        database_text = os.fspath(database)
        if database_text not in {":memory:", "file::memory:"}:
            if database_text.startswith("file:"):
                database_text = database_text[5:].split("?", 1)[0]
            _guard_path_value(database_text)
        return original_sqlite_connect(database, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(os, "mkdir", guarded_mkdir)
    monkeypatch.setattr(os, "remove", guarded_remove)
    monkeypatch.setattr(os, "unlink", guarded_unlink)
    monkeypatch.setattr(os, "rmdir", guarded_rmdir)
    monkeypatch.setattr(os, "rename", guarded_rename)
    monkeypatch.setattr(os, "replace", guarded_replace)
    monkeypatch.setattr(os, "link", guarded_link)
    monkeypatch.setattr(os, "symlink", guarded_symlink)
    monkeypatch.setattr(sqlite3, "connect", guarded_sqlite_connect)


def _guard_write_mode(file: Any, mode: str) -> None:
    if any(character in mode for character in _WRITE_MODES):
        _guard_path_value(file)


def _guard_path_value(path: Any) -> None:
    if isinstance(path, int):
        return
    assert_not_production_persistence_path(os.fspath(path))


def _resolved_path(path: Union[str, os.PathLike]) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
