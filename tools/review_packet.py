#!/usr/bin/env python3
"""Render deterministic, read-only reviewer evidence for a candidate."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from governance_policy import (
    GovernancePolicyError,
    commit_list,
    diff_stat,
    discover_repository,
    governance_boundary,
    inspect_candidate,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="baseline SHA or ref")
    parser.add_argument("--candidate", required=True, help="candidate SHA or ref")
    parser.add_argument("--repo", default=".", help="repository path (default: current directory)")
    parser.add_argument(
        "--test",
        action="append",
        default=[],
        help="operator-provided test evidence; repeat for multiple checks",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repository = discover_repository(Path(args.repo))
        evidence = inspect_candidate(repository, args.base, args.candidate)
        commits = commit_list(repository, evidence["base_sha"], evidence["candidate_sha"])
        stat = diff_stat(repository, evidence["base_sha"], evidence["candidate_sha"])
    except GovernancePolicyError as exc:
        print("# Fast Lane Reviewer Packet")
        print()
        print(f"GOVERNANCE_GATE=FAIL")
        print(f"FAILED_CHECK={exc}")
        return 2

    print(_render_packet(evidence, commits, stat, args.test))
    return 0 if evidence["gate_pass"] else 2


def _render_packet(evidence, commits, stat: str, tests: List[str]) -> str:
    gate = "PASS" if evidence["gate_pass"] else "FAIL"
    lines = [
        "# Fast Lane Reviewer Packet",
        "",
        "## Candidate identity",
        "",
        f"- Baseline SHA: `{evidence['base_sha']}`",
        f"- Candidate SHA: `{evidence['candidate_sha']}`",
        f"- Merge base: `{evidence['merge_base'] or 'NONE'}`",
        f"- Governance gate: `{gate}`",
        f"- Risk tier: `{evidence['risk_tier']}`",
        f"- Fast Lane allowed: `{'YES' if evidence['fast_lane_allowed'] else 'NO'}`",
        "",
        "## Changed files",
        "",
    ]
    lines.extend(f"- `{_escape(path)}`" for path in evidence["changed_files"])
    if not evidence["changed_files"]:
        lines.append("- None")

    lines.extend(["", "## Commit list", ""])
    lines.extend(
        f"- `{commit['sha']}` — {_escape(commit['subject'])}" for commit in commits
    )
    if not commits:
        lines.append("- None")

    lines.extend(["", "## Diff stat", "", "```text", stat or "(empty)", "```"])
    lines.extend(["", "## Risk and governance boundary", ""])
    lines.append(f"- {governance_boundary(evidence['risk_tier'])}")
    lines.append(
        "- High-risk rules: "
        + (", ".join(evidence["high_risk_rules"]) or "none")
    )
    lines.append(
        "- Unclassified paths: "
        + (", ".join(evidence["unclassified_paths"]) or "none")
    )
    lines.append(
        "- Failed checks: " + (", ".join(evidence["failures"]) or "none")
    )

    lines.extend(["", "## Tests", ""])
    if tests:
        lines.append("Operator-provided (reviewer must verify independently):")
        lines.extend(f"- {_escape(test)}" for test in tests)
    else:
        lines.append("- Not provided")

    lines.extend(
        [
            "",
            "## Reviewer instructions",
            "",
            "- Read only. Do not modify the candidate or its worktree.",
            "- Do not commit, push, merge, rebase, amend, or change refs.",
            "- Record the candidate SHA before review and resolve it again after review.",
            "- Verify the worktree and candidate tree are unchanged after review.",
            "- Classify findings as `BLOCKING`, `NONBLOCKING`, or `INFORMATIONAL`.",
            "- Report `REVIEWER_MODIFIED_CANDIDATE=NO` only after the before/after checks match.",
            "- A HIGH-risk candidate cannot be approved for Fast Lane promotion.",
            "",
            "REVIEWER_MODIFIED_CANDIDATE=NO (required; reviewer must verify)",
            "",
        ]
    )
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("`", "\\`").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
