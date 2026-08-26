"""Shared fail-closed policy and Git inspection for Development Governance Fast Lane v1."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Sequence, Tuple


RISK_FAST = "FAST"
RISK_STANDARD = "STANDARD"
RISK_HIGH = "HIGH"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_CONFLICT_MARKER = re.compile(r"^(?:<{7}|={7}|>{7})(?: |$)")
_SENSITIVE_NAME = re.compile(
    r"(?:^|[._-])(?:api[._-]?key|credentials?|secrets?|token)(?:[._-]|$)",
    re.IGNORECASE,
)

# Tests remain STANDARD even when they exercise a HIGH-risk production module. They do not mutate
# that boundary. Candidate-content checks below can still raise them to HIGH if they record an
# authorization transition in a formal collaboration record.
_STANDARD_PREFIXES = ("src/", "tests/", "tools/")
_PROTECTED_DOC_PREFIXES = ("docs/collaboration/", "docs/governance/", "docs/reviews/")
_HIGH_PREFIXES = (
    ".github/workflows/",
    ".mka/",
    "data/",
    "deploy/",
    "deployment/",
    "infra/",
    "migrations/",
    "obsidian_vault/",
    "production/",
)
_HIGH_EXACT_PATHS = frozenset(
    {
        "src/marketing_knowledge_agent/content_index.py",
        "src/marketing_knowledge_agent/governance.py",
        "src/marketing_knowledge_agent/indexing.py",
        "src/marketing_knowledge_agent/query_gating.py",
        "src/marketing_knowledge_agent/stable_record_authority.py",
        "src/marketing_knowledge_agent/stable_record_crosswalk.py",
        "tools/build_approved_asset_url_authority.py",
    }
)
_SENSITIVE_BASENAMES = frozenset(
    {
        ".env",
        ".env.production",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)
_AUTHORIZATION_RECORDS = frozenset(
    {
        "docs/collaboration/CURRENT_WORK.md",
        "docs/collaboration/DECISIONS.md",
    }
)
_HIGH_AUTHORIZATION_MARKERS = (
    "AUTHORITY_MUTATION_AUTHORIZED=YES",
    "MAIN_UPDATE_AUTHORIZED=YES",
    "PRODUCTION_REINDEX_AUTHORIZED=YES",
    "ROW_V1_RETIRED=YES",
    "STABLE_RECORD_V2_ACTIVATED=YES",
)


class GovernancePolicyError(RuntimeError):
    """Raised when deterministic governance evidence cannot be established."""


@dataclass(frozen=True)
class RiskClassification:
    tier: str
    fast_lane_allowed: bool
    high_risk_rules: Tuple[str, ...]
    unclassified_paths: Tuple[str, ...]
    path_reasons: Mapping[str, str]


def run_git(
    repository: Path,
    args: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run Git without a shell; callers must pass resolved SHAs to ancestry/diff operations."""
    result = subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "git command failed"
        raise GovernancePolicyError(detail)
    return result


def discover_repository(start: Path) -> Path:
    result = run_git(Path(start), ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        raise GovernancePolicyError("REPOSITORY_NOT_RESOLVABLE")
    root = Path(result.stdout.strip()).resolve()
    if not root.is_dir():
        raise GovernancePolicyError("REPOSITORY_NOT_RESOLVABLE")
    return root


def resolve_commit(repository: Path, ref: str) -> str:
    _validate_ref_input(ref)
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        check=False,
    )
    sha = result.stdout.strip().lower()
    if result.returncode != 0 or not _FULL_SHA.fullmatch(sha):
        raise GovernancePolicyError(f"COMMIT_NOT_FOUND:{ref}")
    return sha


def validate_expected_sha(value: str) -> str:
    value = value.strip().lower()
    if not _FULL_SHA.fullmatch(value):
        raise GovernancePolicyError("EXPECTED_MAIN_MUST_BE_FULL_SHA")
    return value


def is_ancestor(repository: Path, base_sha: str, candidate_sha: str) -> bool:
    result = run_git(
        repository,
        ["merge-base", "--is-ancestor", base_sha, candidate_sha],
        check=False,
    )
    if result.returncode not in (0, 1):
        raise GovernancePolicyError("ANCESTRY_CHECK_FAILED")
    return result.returncode == 0


def merge_base(repository: Path, base_sha: str, candidate_sha: str) -> str:
    result = run_git(repository, ["merge-base", base_sha, candidate_sha], check=False)
    value = result.stdout.strip().lower()
    if result.returncode != 0 or not _FULL_SHA.fullmatch(value):
        raise GovernancePolicyError("MERGE_BASE_NOT_FOUND")
    return value


def changed_files(repository: Path, base_sha: str, candidate_sha: str) -> List[str]:
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=ACDMRTUXB",
            base_sha,
            candidate_sha,
            "--",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise GovernancePolicyError("CHANGED_FILES_UNAVAILABLE")
    return sorted(
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    )


def inspect_candidate(repository: Path, base_ref: str, candidate_ref: str) -> Dict[str, object]:
    """Return deterministic candidate evidence; safety failures are collected, not warned away."""
    base_sha = resolve_commit(repository, base_ref)
    candidate_sha = resolve_commit(repository, candidate_ref)
    failures: List[str] = []

    ancestor = is_ancestor(repository, base_sha, candidate_sha)
    if not ancestor:
        failures.append("BASE_NOT_ANCESTOR")
    try:
        common = merge_base(repository, base_sha, candidate_sha)
    except GovernancePolicyError:
        common = ""

    status = run_git(
        repository,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ).stdout
    worktree_clean = status == ""
    if not worktree_clean:
        failures.append("WORKTREE_NOT_CLEAN")

    diff_check = run_git(
        repository,
        ["diff", "--check", base_sha, candidate_sha, "--"],
        check=False,
    )
    diff_check_pass = diff_check.returncode == 0
    if not diff_check_pass:
        failures.append("DIFF_CHECK_FAILED")

    paths = changed_files(repository, base_sha, candidate_sha)
    if not paths:
        failures.append("NO_CHANGED_FILES")

    candidate_diff = run_git(
        repository,
        ["diff", "--no-ext-diff", "--no-color", "--unified=0", base_sha, candidate_sha, "--"],
    ).stdout
    conflict_markers = _added_conflict_markers(candidate_diff)
    if conflict_markers:
        failures.append("CONFLICT_MARKERS_FOUND")

    classification = classify_candidate(repository, candidate_sha, paths)
    identity = candidate_identity(repository, candidate_sha)

    return {
        "repository": str(repository),
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "merge_base": common,
        "base_is_ancestor": ancestor,
        "worktree_clean": worktree_clean,
        "worktree_status": status.split("\0") if status else [],
        "diff_check_pass": diff_check_pass,
        "diff_check_output": (diff_check.stdout + diff_check.stderr).strip(),
        "changed_files": paths,
        "conflict_markers": conflict_markers,
        "risk_tier": classification.tier,
        "fast_lane_allowed": classification.fast_lane_allowed,
        "high_risk_rules": list(classification.high_risk_rules),
        "unclassified_paths": list(classification.unclassified_paths),
        "path_reasons": dict(classification.path_reasons),
        "candidate_identity": identity,
        "failures": failures,
        "gate_pass": not failures,
    }


def classify_candidate(
    repository: Path,
    candidate_sha: str,
    paths: Sequence[str],
) -> RiskClassification:
    high_rules: List[str] = []
    unclassified: List[str] = []
    reasons: Dict[str, str] = {}
    saw_standard = False

    for path in paths:
        normalized = _normalized_git_path(path)
        path_risk, reason = _classify_path(normalized)
        reasons[normalized] = reason
        if path_risk == RISK_HIGH:
            high_rules.append(f"{reason}:{normalized}")
        elif path_risk == RISK_STANDARD:
            saw_standard = True
            if reason == "UNCLASSIFIED_PATH_FAIL_CLOSED":
                unclassified.append(normalized)

        if normalized in _AUTHORIZATION_RECORDS:
            content = _blob_text(repository, candidate_sha, normalized)
            for marker in _HIGH_AUTHORIZATION_MARKERS:
                if marker in content:
                    high_rules.append(f"AUTHORIZATION_TRANSITION:{marker}:{normalized}")

    if high_rules:
        tier = RISK_HIGH
    elif saw_standard or not paths:
        tier = RISK_STANDARD
    else:
        tier = RISK_FAST
    return RiskClassification(
        tier=tier,
        fast_lane_allowed=tier != RISK_HIGH,
        high_risk_rules=tuple(sorted(set(high_rules))),
        unclassified_paths=tuple(sorted(set(unclassified))),
        path_reasons=reasons,
    )


def candidate_identity(repository: Path, candidate_sha: str) -> Dict[str, object]:
    result = run_git(
        repository,
        [
            "show",
            "--no-patch",
            "--format=%H%x00%T%x00%P%x00%an%x00%ae%x00%aI%x00%s",
            candidate_sha,
        ],
    )
    parts = result.stdout.rstrip("\n").split("\0")
    if len(parts) != 7:
        raise GovernancePolicyError("CANDIDATE_IDENTITY_UNAVAILABLE")
    return {
        "commit": parts[0],
        "tree": parts[1],
        "parents": parts[2].split() if parts[2] else [],
        "author_name": parts[3],
        "author_email": parts[4],
        "authored_at": parts[5],
        "subject": parts[6],
    }


def commit_list(repository: Path, base_sha: str, candidate_sha: str) -> List[Dict[str, str]]:
    result = run_git(
        repository,
        ["log", "-z", "--reverse", "--format=%H%x00%s", f"{base_sha}..{candidate_sha}"],
    )
    fields = result.stdout.rstrip("\n\0").split("\0") if result.stdout else []
    commits: List[Dict[str, str]] = []
    for index in range(0, len(fields), 2):
        if index + 1 < len(fields) and fields[index]:
            commits.append({"sha": fields[index], "subject": fields[index + 1]})
    return commits


def diff_stat(repository: Path, base_sha: str, candidate_sha: str) -> str:
    return run_git(
        repository,
        ["diff", "--stat", "--no-color", base_sha, candidate_sha, "--"],
    ).stdout.rstrip()


def evidence_requirements(risk_tier: str) -> Tuple[str, ...]:
    if risk_tier == RISK_FAST:
        return ("acceptance",)
    if risk_tier == RISK_STANDARD:
        return ("review", "acceptance")
    return ()


def validate_evidence_file(
    path: Path,
    *,
    kind: str,
    base_sha: str,
    candidate_sha: str,
) -> Dict[str, object]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernancePolicyError(f"{kind.upper()}_EVIDENCE_INVALID") from exc
    if not isinstance(payload, dict):
        raise GovernancePolicyError(f"{kind.upper()}_EVIDENCE_INVALID")
    if payload.get("baseline_sha") != base_sha or payload.get("candidate_sha") != candidate_sha:
        raise GovernancePolicyError(f"{kind.upper()}_EVIDENCE_IDENTITY_MISMATCH")
    if payload.get("reviewer_modified_candidate") is not False:
        raise GovernancePolicyError("REVIEWER_MODIFIED_CANDIDATE_NOT_VERIFIED")
    if kind == "review":
        if payload.get("verdict") not in {"PASS", "PASS_WITH_NONBLOCKING_FINDINGS"}:
            raise GovernancePolicyError("REVIEW_EVIDENCE_NOT_ACCEPTABLE")
    elif kind == "acceptance":
        if payload.get("accepted") is not True:
            raise GovernancePolicyError("ACCEPTANCE_EVIDENCE_NOT_ACCEPTED")
    else:
        raise GovernancePolicyError("UNKNOWN_EVIDENCE_KIND")
    return payload


def governance_boundary(risk_tier: str) -> str:
    if risk_tier == RISK_HIGH:
        return "HIGH risk: Fast Lane promotion is prohibited; separate governed workflow required."
    if risk_tier == RISK_STANDARD:
        return "STANDARD risk: independent review and explicit acceptance evidence are required."
    return "FAST risk: explicit acceptance evidence remains required before main promotion."


def _classify_path(path: str) -> Tuple[str, str]:
    if path.startswith("tests/"):
        return RISK_STANDARD, "TEST_CHANGE"
    if path in _HIGH_EXACT_PATHS:
        return RISK_HIGH, "HIGH_RISK_CONTRACT_PATH"
    if path.startswith(_HIGH_PREFIXES):
        return RISK_HIGH, "HIGH_RISK_STATE_OR_DEPLOYMENT_PATH"
    parts = PurePosixPath(path).parts
    basename = parts[-1].lower()
    if "authority" in {part.lower() for part in parts}:
        return RISK_HIGH, "AUTHORITY_PATH"
    if (
        path == ".gitmodules"
        or basename.startswith(".env")
        or basename in _SENSITIVE_BASENAMES
        or _SENSITIVE_NAME.search(basename)
    ):
        return RISK_HIGH, "CREDENTIAL_OR_EXTERNAL_CODE_PATH"
    if (
        "migration" in basename
        or "content_index" in basename
        or "reindex" in basename
        or "stable_record" in basename
        or "production" in {part.lower() for part in parts}
        or basename.startswith("production.")
    ):
        return RISK_HIGH, "HIGH_RISK_OPERATION_PATH"
    if basename.endswith((".pem", ".key", ".p12", ".pfx")):
        return RISK_HIGH, "CREDENTIAL_PATH"
    if path.startswith(_PROTECTED_DOC_PREFIXES) or path in {"AGENTS.md", "CLAUDE.md"}:
        return RISK_STANDARD, "PROTECTED_GOVERNANCE_DOCUMENT"
    if path == "README.md" or (path.startswith("docs/") and path.endswith(".md")):
        return RISK_FAST, "EXPLICIT_DOCS_ONLY_ALLOWLIST"
    if path.startswith(_STANDARD_PREFIXES) or path in {
        ".gitignore",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
    }:
        return RISK_STANDARD, "STANDARD_ENGINEERING_CHANGE"
    return RISK_STANDARD, "UNCLASSIFIED_PATH_FAIL_CLOSED"


def _normalized_git_path(path: str) -> str:
    if not path or "\x00" in path or "\n" in path or "\r" in path or "\\" in path:
        raise GovernancePolicyError("UNSAFE_GIT_PATH")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise GovernancePolicyError("UNSAFE_GIT_PATH")
    return pure.as_posix()


def _validate_ref_input(ref: str) -> None:
    if not isinstance(ref, str) or not ref or len(ref) > 1024:
        raise GovernancePolicyError("INVALID_REF_INPUT")
    if any(character in ref for character in ("\x00", "\n", "\r")):
        raise GovernancePolicyError("INVALID_REF_INPUT")


def _added_conflict_markers(candidate_diff: str) -> List[str]:
    markers: List[str] = []
    for line in candidate_diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added = line[1:]
            if _CONFLICT_MARKER.match(added):
                markers.append(added)
    return markers


def _blob_text(repository: Path, candidate_sha: str, path: str) -> str:
    result = run_git(repository, ["show", f"{candidate_sha}:{path}"], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
