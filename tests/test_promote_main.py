from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
PROMOTE = ROOT / "tools/promote_main.py"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _remote_candidate(
    tmp_path: Path,
    relative: str = "docs/guide.md",
) -> tuple[Path, Path, str, str]:
    remote = tmp_path / "origin.git"
    repository = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", "--quiet", "--initial-branch", "main", str(remote))
    repository.mkdir()
    _git(repository, "init", "--quiet", "--initial-branch", "main")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Tests")
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--quiet", "-m", "baseline")
    base = _git(repository, "rev-parse", "HEAD")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "--quiet", "-u", "origin", "main")

    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("candidate\n", encoding="utf-8")
    _git(repository, "add", "--", relative)
    _git(repository, "commit", "--quiet", "-m", f"change {relative}")
    candidate = _git(repository, "rev-parse", "HEAD")
    return repository, remote, base, candidate


def _evidence(
    tmp_path: Path,
    base: str,
    candidate: str,
    *,
    standard: bool = False,
) -> list[str]:
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(
        json.dumps(
            {
                "baseline_sha": base,
                "candidate_sha": candidate,
                "accepted": True,
                "reviewer_modified_candidate": False,
            }
        ),
        encoding="utf-8",
    )
    args = ["--acceptance-evidence", str(acceptance)]
    if standard:
        review = tmp_path / "review.json"
        review.write_text(
            json.dumps(
                {
                    "baseline_sha": base,
                    "candidate_sha": candidate,
                    "verdict": "PASS",
                    "reviewer_modified_candidate": False,
                }
            ),
            encoding="utf-8",
        )
        args.extend(["--review-evidence", str(review)])
    return args


def _promote(
    repository: Path,
    base: str,
    candidate: str,
    *extra: str,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(PROMOTE),
            "--repo",
            str(repository),
            "--expected-main",
            base,
            "--candidate",
            candidate,
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _env(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _remote_main(remote: Path) -> str:
    return _git(remote, "rev-parse", "refs/heads/main")


def test_dry_run_remote_main_exact_passes_and_never_pushes(tmp_path):
    repository, remote, base, candidate = _remote_candidate(tmp_path)
    evidence = _evidence(tmp_path, base, candidate)

    result = _promote(repository, base, candidate, *evidence)
    values = _env(result.stdout)

    assert result.returncode == 0
    assert values["PROMOTION_PREFLIGHT"] == "PASS"
    assert values["MODE"] == "DRY_RUN"
    assert values["SAFE_TO_PROMOTE"] == "YES"
    assert _remote_main(remote) == base


def test_remote_drift_fails_without_push(tmp_path):
    repository, remote, base, candidate = _remote_candidate(tmp_path)

    result = _promote(repository, "0" * 40, candidate)

    assert result.returncode != 0
    assert _env(result.stdout)["REASON"] == "REMOTE_MAIN_DRIFT"
    assert _remote_main(remote) == base


def test_non_fast_forward_fails_without_push(tmp_path):
    repository, remote, base, _candidate = _remote_candidate(tmp_path)
    _git(repository, "switch", "--orphan", "unrelated")
    (repository / "README.md").unlink(missing_ok=True)
    (repository / "docs/guide.md").unlink(missing_ok=True)
    (repository / "docs").mkdir(exist_ok=True)
    (repository / "docs/unrelated.md").write_text("unrelated\n", encoding="utf-8")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "unrelated")
    candidate = _git(repository, "rev-parse", "HEAD")

    result = _promote(repository, base, candidate)

    assert result.returncode != 0
    assert _env(result.stdout)["REASON"] == "NON_FAST_FORWARD"
    assert _remote_main(remote) == base


def test_high_risk_candidate_is_denied(tmp_path):
    repository, remote, base, candidate = _remote_candidate(
        tmp_path, "data/identity/authority/registry.csv"
    )

    result = _promote(repository, base, candidate)

    assert result.returncode != 0
    assert _env(result.stdout)["REASON"] == "HIGH_RISK_FAST_LANE_DENIED"
    assert _remote_main(remote) == base


def test_authorization_claim_without_execute_fails_and_does_not_write(tmp_path):
    repository, remote, base, candidate = _remote_candidate(tmp_path)
    evidence = _evidence(tmp_path, base, candidate)

    result = _promote(
        repository,
        base,
        candidate,
        *evidence,
        "--confirm-main-update-authorized",
    )

    assert result.returncode != 0
    assert _env(result.stdout)["REASON"] == "EXECUTE_REQUIRED_FOR_AUTHORIZATION_CLAIM"
    assert _remote_main(remote) == base


def test_execute_without_authorization_confirmation_fails_and_does_not_write(tmp_path):
    repository, remote, base, candidate = _remote_candidate(tmp_path)

    result = _promote(repository, base, candidate, "--execute")

    assert result.returncode != 0
    assert _env(result.stdout)["REASON"] == "MAIN_UPDATE_AUTHORIZATION_CONFIRMATION_REQUIRED"
    assert _remote_main(remote) == base


def test_standard_candidate_requires_review_and_acceptance_evidence(tmp_path):
    repository, remote, base, candidate = _remote_candidate(tmp_path, "tools/helper.py")
    acceptance_only = _evidence(tmp_path, base, candidate)

    result = _promote(repository, base, candidate, *acceptance_only)

    assert result.returncode != 0
    assert _env(result.stdout)["REASON"] == "REVIEW_EVIDENCE_REQUIRED"
    assert _remote_main(remote) == base


def test_execute_uses_explicit_non_force_refspec_and_verifies_remote(tmp_path):
    repository, remote, base, candidate = _remote_candidate(tmp_path)
    evidence = _evidence(tmp_path, base, candidate)
    trace = tmp_path / "git-trace.jsonl"
    environment = os.environ.copy()
    environment["GIT_TRACE2_EVENT"] = str(trace)

    result = _promote(
        repository,
        base,
        candidate,
        *evidence,
        "--execute",
        "--confirm-main-update-authorized",
        env=environment,
    )
    values = _env(result.stdout)

    assert result.returncode == 0
    assert values["PROMOTION"] == "PASS"
    assert values["MODE"] == "EXECUTED"
    assert values["PUSH_REFSPEC"] == f"{candidate}:refs/heads/main"
    assert values["FORCE_USED"] == "NO"
    assert _remote_main(remote) == candidate

    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    commands = [event.get("argv", []) for event in events if event.get("event") == "start"]
    push_commands = [args for args in commands if len(args) > 1 and args[1] == "push"]
    assert any(
        Path(args[0]).name == "git"
        and args[1:] == ["push", "origin", f"{candidate}:refs/heads/main"]
        for args in push_commands
    )
    assert all(not arg.startswith("--force") for args in commands for arg in args)


def test_post_push_verification_failure_is_reported(tmp_path):
    repository, remote, base, candidate = _remote_candidate(tmp_path)
    evidence = _evidence(tmp_path, base, candidate)
    hook = remote / "hooks/post-receive"
    hook.write_text(
        "#!/bin/sh\ngit update-ref refs/heads/main " + base + "\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    result = _promote(
        repository,
        base,
        candidate,
        *evidence,
        "--execute",
        "--confirm-main-update-authorized",
    )

    assert result.returncode != 0
    assert _env(result.stdout)["REASON"] == "POST_PUSH_VERIFICATION_FAILED"
    assert _remote_main(remote) == base
