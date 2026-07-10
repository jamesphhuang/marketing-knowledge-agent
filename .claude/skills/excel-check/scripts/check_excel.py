#!/usr/bin/env python3
"""Read-only structural check for a marketing-database Excel workbook.

Runs the project's own excel-preview into a TEMP dir (never reports/), then
compares structure and counts against the verified baseline. Writing to a temp
dir is deliberate: running excel-preview into reports/ would overwrite a
human-filled review_decisions CSV (see docs/governance/LESSONS.md 2026-07-10).

Usage:
    python check_excel.py <path-to.xlsx> [--baseline <baseline.json>]

Exit codes:
    0  structural checks passed (safe to proceed to review-template)
    1  structural problem found (STOP; do not run review-template)
    2  could not run (bad path, import error, etc.)
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


def _load_project(repo_root: Path):
    sys.path.insert(0, str(repo_root / "src"))
    from marketing_knowledge_agent.excel_preview import (  # noqa: E402
        ExcelPreviewError,
        generate_excel_preview,
    )
    return generate_excel_preview, ExcelPreviewError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--baseline", type=Path, default=Path(__file__).resolve().parent.parent / "references" / "baseline.json")
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo_root = args.repo_root or Path(__file__).resolve().parents[4]
    if not (repo_root / "src" / "marketing_knowledge_agent").exists():
        print(f"[stop] 找不到專案 src/,repo_root 推斷錯誤: {repo_root}", file=sys.stderr)
        return 2
    if not args.workbook.exists():
        print(f"[stop] 找不到 workbook: {args.workbook}", file=sys.stderr)
        return 2

    try:
        generate_excel_preview, ExcelPreviewError = _load_project(repo_root)
    except Exception as exc:  # noqa: BLE001
        print(f"[stop] 無法載入專案模組: {exc}", file=sys.stderr)
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))

    problems: list[str] = []
    notes: list[str] = []

    with tempfile.TemporaryDirectory(prefix="excel_check_") as tmp:
        out = Path(tmp)
        try:
            summary = generate_excel_preview(workbook_path=args.workbook, output_dir=out)
        except ExcelPreviewError as exc:
            # This is the header-preflight / missing-sheet failure. Hard stop.
            print("=" * 60)
            print("結果: 停止 — 結構不符,不要進 review-template")
            print("=" * 60)
            print(f"\n[preflight 失敗] {exc}")
            print("\n這通常代表 sheet 名稱、header 位置或欄名跟基準不同。")
            print("這是人工決策:確認是 workbook 改版(要更新 code/baseline)還是拿錯檔。")
            return 1

        # --- Structural (hard) checks ---
        if summary["validation_error_count"] != 0:
            problems.append(f"validation_error_count = {summary['validation_error_count']}(應為 0);見 {out}/validation_errors.md")

        # phantom-row check for pending metrics (see LESSONS: merge expansion)
        pend = json.loads((out / "pending_metrics.json").read_text(encoding="utf-8"))
        phantom = [r.get("source_row") for r in pend if not r.get("metric_name") and not r.get("claim_statement")]
        if phantom:
            problems.append(f"待確認數據有幻影列(無指標且無論述): rows {phantom}")

        unknown = summary.get("unknown_exposure_channel_columns") or []
        if unknown:
            notes.append(f"公開數據出現未知 channel 欄:{unknown} — 可能是新增渠道,需人工確認是否要擴 enum")

        # --- Count deltas vs baseline (informational, NOT pass/fail) ---
        base_counts = baseline["sheet_counts"]
        actual_counts = summary["sheet_counts"]
        deltas = []
        for sheet, base_n in base_counts.items():
            act_n = actual_counts.get(sheet)
            if act_n is None:
                problems.append(f"缺少預期 sheet: {sheet}")
                continue
            d = act_n - base_n
            mark = "" if d == 0 else f"  ({'+' if d > 0 else ''}{d} vs 基準)"
            deltas.append((sheet, act_n, base_n, d, mark))

        gr = summary["merchant_case_governance_risk_count"]
        gr_base = baseline.get("merchant_case_governance_risk_count")

    # --- Report ---
    ok = not problems
    print("=" * 60)
    print("結果: " + ("通過 — 可以進 review-template" if ok else "停止 — 有結構性問題"))
    print("=" * 60)

    print("\n## 結構性硬檢查")
    print(f"  - preflight / 讀檔: 通過")
    print(f"  - validation errors: {summary['validation_error_count']}" + ("  ✓" if summary["validation_error_count"] == 0 else "  ✗"))
    print(f"  - 待確認數據幻影列: {'無  ✓' if not phantom else str(phantom) + '  ✗'}")

    print("\n## 計數 vs 基準(參考,非通過標準)")
    print(f"  基準來源: {baseline.get('baseline_source')}")
    for sheet, act_n, base_n, d, mark in deltas:
        print(f"  - {sheet}: {act_n}{mark}")
    print(f"  - governance_risk: {gr}" + (f"  ({'+' if gr - gr_base > 0 else ''}{gr - gr_base} vs 基準)" if gr_base is not None and gr != gr_base else ""))

    if notes:
        print("\n## 提醒(需人工留意)")
        for n in notes:
            print(f"  - {n}")

    if problems:
        print("\n## 問題(必須先解決)")
        for p in problems:
            print(f"  - {p}")
        print("\n下一步: 不要跑 review-template。先判斷是 workbook 問題還是 code/baseline 要更新。")
    else:
        big_delta = [s for s, a, b, d, m in deltas if abs(d) >= max(5, int(0.1 * b))]
        if big_delta:
            print(f"\n注意: {', '.join(big_delta)} 與基準差異較大,若非預期請先確認是不是拿錯檔。")
        print("\n下一步: 結構無誤,可以對「reports/excel_preview/」跑正式 review-template。")
        print("  ⚠ 跑 review-template 前確認 reports/excel_preview/review_decisions_template.csv")
        print("    的 reviewer 欄是空的(=無人工內容);若非空代表有人工決策,覆蓋前先備份。")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
