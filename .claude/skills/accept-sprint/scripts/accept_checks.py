#!/usr/bin/env python3
"""Mechanical half of the sprint acceptance ritual.

Runs the checks that a fresh-context reviewer must NOT trust the sprint report
for: re-run the full test suite, detect weakened existing-test assertions, and
verify human-authored files weren't silently modified. Prints a verdict plus a
checklist of the human-judgment items (spec DoD, risk register) for the
reviewer to complete.

Usage:
    python accept_checks.py [--base main] [--snapshot]

    --base <ref>   git ref to diff the current branch against (default: main)
    --snapshot     record current human-file checksums as the new known-good
                   baseline (run this AFTER a legitimate human edit, e.g. after
                   filling review_decisions), then commit the snapshot file.

Exit codes:
    0  mechanical checks clean
    1  a mechanical check flagged something (STOP, review before merge)
    2  could not run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SNAPSHOT = Path(__file__).resolve().parent.parent / "references" / "human_file_checksums.json"

# Human-authored files that a code sprint must NEVER modify. gitignored, so git
# can't track them — we checksum them ourselves.
HUMAN_FILES = [
    "reports/excel_preview/review_decisions_template.csv",
]


def sh(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def md5(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()


def snapshot_human_files() -> int:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    data = {f: md5(REPO / f) for f in HUMAN_FILES}
    SNAPSHOT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"已記錄人工檔 checksum 快照: {SNAPSHOT}")
    for f, h in data.items():
        print(f"  {f}: {h or '(不存在)'}")
    print("\n記得把這個快照檔 commit 起來。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="main")
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()

    if not (REPO / ".git").exists():
        print(f"[stop] {REPO} 不是 git repo,accept-sprint 需要 git", file=sys.stderr)
        return 2
    if args.snapshot:
        return snapshot_human_files()

    problems: list[str] = []
    notes: list[str] = []

    # --- 1. Full test suite (do NOT trust the sprint report) ---
    print("## 1. 重跑完整測試(不採信報告)")
    code, out = sh([".venv/bin/pytest", "-p", "no:cacheprovider", "-q"])
    tail = [l for l in out.splitlines() if l.strip()][-1:] or ["(無輸出)"]
    print(f"  {tail[0]}")
    if code != 0:
        problems.append(f"pytest 未全綠(exit {code})")

    # --- 2. Weakened existing-test assertions ---
    print(f"\n## 2. 測試斷言完整性(diff vs {args.base})")
    code_d, changed = sh(["git", "diff", "--name-status", f"{args.base}...HEAD", "--", "tests/"])
    modified = [l.split("\t", 1)[1] for l in changed.splitlines() if l.startswith("M")]
    added = [l.split("\t", 1)[1] for l in changed.splitlines() if l.startswith("A")]
    print(f"  新增測試檔: {len(added)}  (正常)")
    print(f"  修改既有測試檔: {len(modified)}" + ("  ← 需檢查" if modified else "  ✓"))
    for f in modified:
        _, diff = sh(["git", "diff", f"{args.base}...HEAD", "--", f])
        removed_asserts = [l for l in diff.splitlines() if l.startswith("-") and "assert" in l and not l.startswith("---")]
        if removed_asserts:
            problems.append(f"{f} 有被移除/改動的 assert 行({len(removed_asserts)} 行)——可能是放水,必須逐行確認")
            print(f"    ⚠ {f}: {len(removed_asserts)} 個 assert 被動過:")
            for l in removed_asserts[:8]:
                print(f"        {l.strip()}")
        else:
            notes.append(f"{f} 有改動但未動 assert(可能是 fixture 更新,較安全,仍請掃一眼)")

    # --- 3. Source change summary ---
    print(f"\n## 3. 原始碼改動摘要")
    _, stat = sh(["git", "diff", "--stat", f"{args.base}...HEAD", "--", "src/"])
    for l in stat.splitlines()[-12:]:
        print(f"  {l}")

    # --- 4. Human-file integrity ---
    print(f"\n## 4. 人工檔完整性(sprint 不得修改)")
    if not SNAPSHOT.exists():
        notes.append("尚無 checksum 快照。人工檔第一次填好後,跑 `--snapshot` 記錄基準。")
        print("  (無快照;第一次用請跑 --snapshot)")
    else:
        recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        for f in HUMAN_FILES:
            cur = md5(REPO / f)
            old = recorded.get(f)
            if old is None:
                print(f"  {f}: 快照未含,略過")
            elif cur == old:
                print(f"  {f}: 未變動  ✓")
            else:
                problems.append(f"{f} 的 checksum 與快照不符——可能被 sprint 動過。若是人工合法編輯,請跑 --snapshot 更新")
                print(f"  {f}: 變動了  ✗")

    # --- Verdict + human-judgment checklist ---
    ok = not problems
    print("\n" + "=" * 60)
    print("機械檢查結果: " + ("乾淨" if ok else "有旗標,先別 merge"))
    print("=" * 60)
    if problems:
        print("\n必須先處理:")
        for p in problems:
            print(f"  ✗ {p}")
    if notes:
        print("\n提醒:")
        for n in notes:
            print(f"  - {n}")

    print("\n## 還需要人(或 fresh-context)判斷的部分:")
    print("  [ ] 對照該 sprint 的 spec DoD,逐條確認(docs/specs/ 或對應文件)")
    print("  [ ] 抽驗新規則不是空殼:自造一筆該被抓的資料,確認真的被抓")
    print("  [ ] 更新 risk register(docs/governance/I_GOVERNANCE_RISK_REVIEW.md)相關條目狀態")
    print("  [ ] governance/restricted/apply 相關變更:不得只靠原作者自驗")
    print("\n以上機械檢查乾淨 + 人工判斷通過,才可 merge。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
