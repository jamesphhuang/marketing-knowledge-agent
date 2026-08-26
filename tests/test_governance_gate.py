from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/governance_gate.py"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch", "main")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "--quiet", "-m", "baseline")
    return root, _git(root, "rev-parse", "HEAD")


def _commit(root: Path, relative: str, content: str = "candidate\n") -> str:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(root, "add", "--", relative)
    _git(root, "commit", "--quiet", "-m", f"change {relative}")
    return _git(root, "rev-parse", "HEAD")


def _gate(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *args, "--repo", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def _env(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def test_clean_valid_repo_passes_and_reports_candidate_identity(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "docs/guide.md")

    result = _gate(root, "verify", "--base", base, "--candidate", candidate)
    values = _env(result.stdout)

    assert result.returncode == 0
    assert values["GOVERNANCE_GATE"] == "PASS"
    assert values["BASE_SHA"] == base
    assert values["CANDIDATE_SHA"] == candidate
    assert values["WORKTREE_CLEAN"] == "PASS"
    assert values["DIFF_CHECK"] == "PASS"


def test_diff_check_failure_is_blocking(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "docs/guide.md", "trailing whitespace  \n")

    result = _gate(root, "verify", "--base", base, "--candidate", candidate)
    values = _env(result.stdout)

    assert result.returncode != 0
    assert values["GOVERNANCE_GATE"] == "FAIL"
    assert "DIFF_CHECK_FAILED" in values["FAILED_CHECK"]


def test_invalid_candidate_fails_closed(tmp_path):
    root, base = _repository(tmp_path)

    result = _gate(root, "verify", "--base", base, "--candidate", "missing")

    assert result.returncode != 0
    assert _env(result.stdout)["FAILED_CHECK"].startswith("COMMIT_NOT_FOUND")


def test_non_ancestor_candidate_fails(tmp_path):
    root, base = _repository(tmp_path)
    _git(root, "switch", "--orphan", "unrelated")
    (root / "README.md").unlink(missing_ok=True)
    candidate = _commit(root, "docs/unrelated.md")

    result = _gate(root, "verify", "--base", base, "--candidate", candidate)

    assert result.returncode != 0
    assert "BASE_NOT_ANCESTOR" in _env(result.stdout)["FAILED_CHECK"]


def test_docs_only_candidate_is_fast(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "docs/guide.md")

    values = _env(
        _gate(root, "verify", "--base", base, "--candidate", candidate).stdout
    )

    assert values["RISK_TIER"] == "FAST"
    assert values["FAST_LANE_ALLOWED"] == "YES"


def test_application_code_candidate_is_standard(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "src/example.py")

    values = _env(
        _gate(root, "verify", "--base", base, "--candidate", candidate).stdout
    )

    assert values["RISK_TIER"] == "STANDARD"
    assert values["FAST_LANE_ALLOWED"] == "YES"


def test_high_risk_path_is_high_and_fast_lane_denied(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "data/identity/authority/registry.csv")

    values = _env(
        _gate(root, "verify", "--base", base, "--candidate", candidate).stdout
    )

    assert values["RISK_TIER"] == "HIGH"
    assert values["FAST_LANE_ALLOWED"] == "NO"


def test_unknown_path_never_defaults_to_fast(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "unknown.payload")

    values = _env(
        _gate(root, "verify", "--base", base, "--candidate", candidate).stdout
    )

    assert values["RISK_TIER"] == "STANDARD"
    assert values["NON_GOVERNANCE_DRIFT"] == "PRESENT"


def test_promote_preflight_rejects_high_risk_even_when_git_checks_pass(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "obsidian_vault/MKA/record.md")

    result = _gate(
        root,
        "promote-preflight",
        "--expected-main",
        base,
        "--candidate",
        candidate,
    )

    assert result.returncode != 0
    assert "HIGH_RISK_FAST_LANE_DENIED" in _env(result.stdout)["FAILED_CHECK"]


def test_renaming_high_risk_source_to_docs_remains_high(tmp_path):
    root, _initial = _repository(tmp_path)
    base = _commit(root, "data/identity/authority/registry.csv")
    (root / "docs").mkdir(exist_ok=True)
    _git(
        root,
        "mv",
        "data/identity/authority/registry.csv",
        "docs/registry.md",
    )
    _git(root, "commit", "--quiet", "-m", "move authority source")
    candidate = _git(root, "rev-parse", "HEAD")

    values = _env(
        _gate(root, "verify", "--base", base, "--candidate", candidate).stdout
    )

    assert values["RISK_TIER"] == "HIGH"
    assert values["FAST_LANE_ALLOWED"] == "NO"


def test_authorization_transition_in_formal_record_is_high(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(
        root,
        "docs/collaboration/DECISIONS.md",
        "STABLE_RECORD_V2_ACTIVATED=YES\n",
    )

    values = _env(
        _gate(root, "verify", "--base", base, "--candidate", candidate).stdout
    )

    assert values["RISK_TIER"] == "HIGH"
    assert values["FAST_LANE_ALLOWED"] == "NO"


def test_credential_path_is_high(tmp_path):
    root, base = _repository(tmp_path)
    candidate = _commit(root, "config/service-api-key.txt")

    values = _env(
        _gate(root, "verify", "--base", base, "--candidate", candidate).stdout
    )

    assert values["RISK_TIER"] == "HIGH"
    assert values["FAST_LANE_ALLOWED"] == "NO"


def test_ref_input_is_never_interpreted_by_a_shell(tmp_path):
    root, base = _repository(tmp_path)
    marker = tmp_path / "command-injection"

    result = _gate(
        root,
        "verify",
        "--base",
        base,
        "--candidate",
        f"$(touch {marker})",
    )

    assert result.returncode != 0
    assert not marker.exists()
