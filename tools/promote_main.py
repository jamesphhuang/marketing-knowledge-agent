#!/usr/bin/env python3
"""Safely preflight or execute an explicitly authorized fast-forward main update."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from governance_policy import (
    GovernancePolicyError,
    RISK_HIGH,
    discover_repository,
    evidence_requirements,
    inspect_candidate,
    is_ancestor,
    json_value,
    resolve_commit,
    validate_evidence_file,
    validate_expected_sha,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-main", required=True, help="exact full SHA expected on origin/main")
    parser.add_argument("--candidate", required=True, help="candidate SHA or ref")
    parser.add_argument("--repo", default=".", help="repository path (default: current directory)")
    parser.add_argument("--review-evidence", help="STANDARD-tier independent review JSON")
    parser.add_argument("--acceptance-evidence", help="review acceptance JSON")
    parser.add_argument("--execute", action="store_true", help="request a real main update")
    parser.add_argument(
        "--confirm-main-update-authorized",
        action="store_true",
        help="declare that external human authorization has already been obtained",
    )
    parser.add_argument("--format", choices=("env", "json"), default="env")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = promote(args)
    _emit(result, args.format)
    return 0 if result.get("STATUS") == "PASS" else 2


def promote(args: argparse.Namespace) -> Dict[str, object]:
    mode = "EXECUTED" if args.execute else "DRY_RUN"
    if args.confirm_main_update_authorized and not args.execute:
        return _failure(mode, "EXECUTE_REQUIRED_FOR_AUTHORIZATION_CLAIM")
    if args.execute and not args.confirm_main_update_authorized:
        return _failure(mode, "MAIN_UPDATE_AUTHORIZATION_CONFIRMATION_REQUIRED")

    try:
        repository = discover_repository(Path(args.repo))
        expected_main = validate_expected_sha(args.expected_main)
        # Resolve before fetch so a moving remote-tracking ref cannot silently change candidate.
        candidate_sha = resolve_commit(repository, args.candidate)

        fetch = _run_write_capable_git(
            repository,
            [
                "fetch",
                "--no-tags",
                "origin",
                "refs/heads/main",
            ],
        )
        if fetch.returncode != 0:
            return _failure(mode, "FETCH_ORIGIN_MAIN_FAILED")
        remote_main = resolve_commit(repository, "FETCH_HEAD")
        if remote_main != expected_main:
            return _failure(
                mode,
                "REMOTE_MAIN_DRIFT",
                OLD_MAIN=remote_main,
                EXPECTED_MAIN=expected_main,
                CANDIDATE_SHA=candidate_sha,
            )
        if not is_ancestor(repository, remote_main, candidate_sha):
            return _failure(
                mode,
                "NON_FAST_FORWARD",
                OLD_MAIN=remote_main,
                CANDIDATE_SHA=candidate_sha,
            )

        evidence = inspect_candidate(repository, remote_main, candidate_sha)
        if not evidence["gate_pass"]:
            return _failure(
                mode,
                "GOVERNANCE_GATE_FAILED",
                FAILED_CHECKS=evidence["failures"],
                RISK_TIER=evidence["risk_tier"],
            )
        if evidence["risk_tier"] == RISK_HIGH or not evidence["fast_lane_allowed"]:
            return _failure(
                mode,
                "HIGH_RISK_FAST_LANE_DENIED",
                RISK_TIER=evidence["risk_tier"],
                HIGH_RISK_RULES=evidence["high_risk_rules"],
            )

        requirement_error = _validate_required_evidence(
            args,
            risk_tier=evidence["risk_tier"],
            base_sha=remote_main,
            candidate_sha=candidate_sha,
        )
        if requirement_error:
            return _failure(
                mode,
                requirement_error,
                RISK_TIER=evidence["risk_tier"],
                OLD_MAIN=remote_main,
                CANDIDATE_SHA=candidate_sha,
            )

        refspec = f"{candidate_sha}:refs/heads/main"
        common = {
            "OLD_MAIN": remote_main,
            "NEW_MAIN": candidate_sha,
            "RISK_TIER": evidence["risk_tier"],
            "FAST_FORWARD": "YES",
            "FORCE_USED": "NO",
            "PUSH_REFSPEC": refspec,
        }
        if not args.execute:
            return {
                "STATUS": "PASS",
                "PROMOTION_PREFLIGHT": "PASS",
                "SAFE_TO_PROMOTE": "YES",
                "MODE": mode,
                **common,
            }

        push = _run_write_capable_git(repository, ["push", "origin", refspec])
        if push.returncode != 0:
            return _failure(mode, "PUSH_REJECTED", **common)
        post_fetch = _run_write_capable_git(
            repository,
            [
                "fetch",
                "--no-tags",
                "origin",
                "refs/heads/main",
            ],
        )
        if post_fetch.returncode != 0:
            return _failure(mode, "POST_PUSH_FETCH_FAILED", **common)
        observed = resolve_commit(repository, "FETCH_HEAD")
        if observed != candidate_sha:
            return _failure(
                mode,
                "POST_PUSH_VERIFICATION_FAILED",
                OBSERVED_MAIN=observed,
                **common,
            )
        return {"STATUS": "PASS", "PROMOTION": "PASS", "MODE": mode, **common}
    except GovernancePolicyError as exc:
        return _failure(mode, str(exc))


def _validate_required_evidence(
    args: argparse.Namespace,
    *,
    risk_tier: str,
    base_sha: str,
    candidate_sha: str,
) -> Optional[str]:
    requirements = evidence_requirements(risk_tier)
    paths = {
        "review": args.review_evidence,
        "acceptance": args.acceptance_evidence,
    }
    for kind in requirements:
        value = paths[kind]
        if not value:
            return f"{kind.upper()}_EVIDENCE_REQUIRED"
        try:
            validate_evidence_file(
                Path(value),
                kind=kind,
                base_sha=base_sha,
                candidate_sha=candidate_sha,
            )
        except GovernancePolicyError as exc:
            return str(exc)
    return None


def _run_write_capable_git(repository: Path, args: Sequence[str]) -> subprocess.CompletedProcess:
    """The only fetch/push runner. Argument arrays make shell injection impossible."""
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )


def _failure(mode: str, reason: str, **fields: object) -> Dict[str, object]:
    return {
        "STATUS": "FAIL",
        "PROMOTION": "FAIL",
        "MODE": mode,
        "REASON": reason,
        "FORCE_USED": "NO",
        **fields,
    }


def _emit(result: Dict[str, object], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            value = json_value(value)
        print(f"{key}={value}")


if __name__ == "__main__":
    raise SystemExit(main())
