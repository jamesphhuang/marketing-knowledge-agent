#!/usr/bin/env python3
"""從一份 sprint spec 抽出組派工訊息需要的結構化資訊。

這個腳本做「機械可靠」的部分:找出 spec 的標題、Non-goals、驗收 DoD、
使用者裁決狀態、可能的 smoke 錨點(數字)。它「不」自動生成派工訊息——
「最容易做錯的點」與 smoke 錨點的取捨需要讀懂 spec,那是主模型的判斷(見 SKILL.md)。

用法:
    python extract_spec.py docs/specs/Q_LLM_INTEGRATION_SPEC.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def sections(text: str) -> list[tuple[str, str]]:
    """回傳 [(heading, body), ...],以 '## ' 切分。"""
    parts = re.split(r"^## ", text, flags=re.M)
    out = []
    for p in parts[1:]:
        line, _, body = p.partition("\n")
        # 去掉頁尾(水平線後的 *規格作者* 註記等),避免污染最後一節的 body
        body = re.split(r"\n---\s*\n", body)[0]
        out.append((line.strip(), body.strip()))
    return out


def find_section(secs, *keywords) -> tuple[str, str] | None:
    for head, body in secs:
        if any(k in head for k in keywords):
            return head, body
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: extract_spec.py <spec.md>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"找不到 spec: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    secs = sections(text)

    title = text.splitlines()[0].lstrip("# ").strip()
    letter = path.name.split("_")[0]

    print("=" * 64)
    print(f"SPEC: {path}")
    print(f"標題: {title}")
    print(f"Sprint 代號建議: {letter.lower()}  分支建議: feat/{letter.lower()}-<slug>")
    print("=" * 64)

    # 執行等級 / 待決
    lvl = re.search(r"執行等級[:：]\s*([^\n。]+)", text)
    print(f"\n[執行等級] {lvl.group(1).strip() if lvl else '未標明——自行判斷'}")

    ud = find_section(secs, "使用者裁決", "待決", "待使用者")
    if ud:
        undecided = "＿＿＿" in ud[1] or "未" in ud[1][:40] and "已" not in ud[1][:20]
        print(f"[使用者裁決] 有此節『{ud[0]}』——" + ("⚠ 可能仍有未決項,派工前先確認" if "＿" in ud[1] else "看來已回填,確認一下"))
    else:
        print("[使用者裁決] 無此節(spec 宣稱無待決事項)")

    # Non-goals
    ng = find_section(secs, "明確不做", "Non-goal", "不做")
    print("\n[Non-goals 原文]")
    print(ng[1] if ng else "  ⚠ 找不到 Non-goals 節——派工必須有,回頭確認 spec 或自行補")

    # DoD / 驗收
    dod = find_section(secs, "DoD", "驗收條件", "測試 DoD", "驗收")
    print("\n[驗收 / DoD 原文]")
    print(dod[1] if dod else "  ⚠ 找不到 DoD 節")

    # 安全斷言 / 防線(高風險 sprint 才有)
    safety = find_section(secs, "安全斷言", "防線", "安全", "白名單")
    if safety:
        print(f"\n[安全防線節『{safety[0]}』——派工的 Required checks 必須要求測試覆蓋]")

    # smoke 錨點:掃全文的「數字 + 期望/預期」句,供主模型挑錨點
    print("\n[可能的 smoke 錨點(含數字或『預期/期望』的句子,自行判斷哪些是驗收錨點)]")
    for m in re.finditer(r"[^\n]*(?:預期|期望|smoke|Smoke)[^\n]*", text):
        line = m.group(0).strip()
        if line and len(line) < 160:
            print(f"  · {line}")

    print("\n" + "=" * 64)
    print("下一步:讀 SKILL.md 的模板,把上面資訊 + 你讀 spec 判斷出的『最易錯點』組成派工訊息。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
