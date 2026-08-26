#!/usr/bin/env python3
"""Deterministically verify a Git candidate against the shared Fast Lane policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

from governance_policy import (
    GovernancePolicyError,
    discover_repository,
    inspect_candidate,
    json_value,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="verify a base/candidate range")
    _common_arguments(verify)
    verify.add_argument("--base", required=True, help="baseline SHA or ref")

    promote = subparsers.add_parser(
        "promote-preflight",
        help="verify that a candidate is eligible for Fast Lane promotion",
    )
    _common_arguments(promote)
    promote.add_argument("--expected-main", required=True, help="expected main SHA or ref")
    return parser


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--candidate", required=True, help="candidate SHA or ref")
    parser.add_argument("--repo", default=".", help="repository path (default: current directory)")
    parser.add_argument("--format", choices=("env", "json"), default="env")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    base = args.base if args.command == "verify" else args.expected_main
    try:
        repository = discover_repository(Path(args.repo))
        evidence = inspect_candidate(repository, base, args.candidate)
        if args.command == "promote-preflight" and not evidence["fast_lane_allowed"]:
            evidence["failures"].append("HIGH_RISK_FAST_LANE_DENIED")
            evidence["gate_pass"] = False
        result = _render_result(evidence, args.command)
    except GovernancePolicyError as exc:
        result = {
            "GOVERNANCE_GATE": "FAIL",
            "MODE": args.command.upper().replace("-", "_"),
            "FAILED_CHECK": str(exc),
        }
    _emit(result, args.format)
    return 0 if result["GOVERNANCE_GATE"] == "PASS" else 2


def _render_result(evidence: Dict[str, object], command: str) -> Dict[str, object]:
    failures = evidence["failures"]
    result: Dict[str, object] = {
        "GOVERNANCE_GATE": "PASS" if evidence["gate_pass"] else "FAIL",
        "MODE": command.upper().replace("-", "_"),
        "FAILED_CHECK": ",".join(failures) if failures else "NONE",
        "REPOSITORY": evidence["repository"],
        "BASE_SHA": evidence["base_sha"],
        "CANDIDATE_SHA": evidence["candidate_sha"],
        "MERGE_BASE": evidence["merge_base"],
        "BASE_IS_ANCESTOR": "YES" if evidence["base_is_ancestor"] else "NO",
        "WORKTREE_CLEAN": "PASS" if evidence["worktree_clean"] else "FAIL",
        "DIFF_CHECK": "PASS" if evidence["diff_check_pass"] else "FAIL",
        "CONFLICT_MARKERS": "NONE" if not evidence["conflict_markers"] else "PRESENT",
        "RISK_TIER": evidence["risk_tier"],
        "FAST_LANE_ALLOWED": "YES" if evidence["fast_lane_allowed"] else "NO",
        "NON_GOVERNANCE_DRIFT": (
            "NONE" if not evidence["unclassified_paths"] else "PRESENT"
        ),
        "CHANGED_FILES_JSON": evidence["changed_files"],
        "UNCLASSIFIED_PATHS_JSON": evidence["unclassified_paths"],
        "HIGH_RISK_RULES_JSON": evidence["high_risk_rules"],
        "PATH_REASONS_JSON": evidence["path_reasons"],
        "CANDIDATE_IDENTITY_JSON": evidence["candidate_identity"],
    }
    return result


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
