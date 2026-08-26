from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "tools/review_packet.py"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _candidate(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch", "main")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "--quiet", "-m", "baseline")
    base = _git(root, "rev-parse", "HEAD")
    target = root / "docs/guide.md"
    target.parent.mkdir()
    target.write_text("review me\n", encoding="utf-8")
    _git(root, "add", "docs/guide.md")
    _git(root, "commit", "--quiet", "-m", "document fast lane")
    return root, base, _git(root, "rev-parse", "HEAD")


def _packet(root: Path, base: str, candidate: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(PACKET),
            "--repo",
            str(root),
            "--base",
            base,
            "--candidate",
            candidate,
            "--test",
            "pytest tests/test_example.py: PASS",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_packet_has_correct_identity_files_risk_and_instructions(tmp_path):
    root, base, candidate = _candidate(tmp_path)

    result = _packet(root, base, candidate)

    assert result.returncode == 0
    assert f"Baseline SHA: `{base}`" in result.stdout
    assert f"Candidate SHA: `{candidate}`" in result.stdout
    assert "`docs/guide.md`" in result.stdout
    assert "Risk tier: `FAST`" in result.stdout
    assert "pytest tests/test_example.py: PASS" in result.stdout
    assert "Read only. Do not modify the candidate" in result.stdout
    assert "findings as `BLOCKING`, `NONBLOCKING`, or `INFORMATIONAL`" in result.stdout
    assert "REVIEWER_MODIFIED_CANDIDATE=NO" in result.stdout


def test_packet_output_is_deterministic(tmp_path):
    root, base, candidate = _candidate(tmp_path)

    first = _packet(root, base, candidate)
    second = _packet(root, base, candidate)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout


def test_packet_reports_tests_not_provided(tmp_path):
    root, base, candidate = _candidate(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(PACKET),
            "--repo",
            str(root),
            "--base",
            base,
            "--candidate",
            candidate,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "## Tests\n\n- Not provided" in result.stdout
