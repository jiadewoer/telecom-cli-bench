"""数据集自检：ID 唯一、正则可编译、每个 checkpoint 都能被自己的 reference 命中。

最后一条是这个项目质量的地基——checkpoint 连标准答案都匹配不上，
说明正则写错了，跑再多模型也是垃圾进垃圾出。
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from telecom_cli_bench.normalize import normalize_block
from telecom_cli_bench.schema import load_tasks

TASK_DIR = Path("data/tasks")


def main() -> int:
    # include_demo=True：few-shot 示例虽不参与评测，质量同样要保证
    tasks = load_tasks(TASK_DIR, include_demo=True)
    errs: list[str] = []

    dup = [i for i, n in Counter(t.id for t in tasks).items() if n > 1]
    if dup:
        errs.append(f"重复 ID: {dup}")

    for t in tasks:
        blob = normalize_block(t.reference, t.vendor.value)
        if not blob.strip():
            errs.append(f"{t.id} 参考答案归一化后为空")
            continue
        for cp in t.checkpoints:
            if not re.search(cp.pattern, blob, re.M):
                errs.append(
                    f"{t.id}/{cp.id} 参考答案未命中自身检查点: {cp.pattern}\n"
                    f"      归一化后 -> {blob!r}"
                )
        for fb in t.forbidden:
            if re.search(fb, blob, re.M):
                errs.append(f"{t.id} 参考答案命中了自己的违禁项: {fb}")

    graded = [t for t in tasks if not t.id.startswith("demo_")]
    print(f"任务总数: {len(graded)}（另有 {len(tasks) - len(graded)} 条 demo 示例不参与评测）")
    print(f"厂商分布: {dict(Counter(t.vendor.value for t in graded))}")
    print(f"难度分布: {dict(sorted(Counter(t.level for t in graded).items()))}")
    print(f"领域分布: {dict(Counter(t.domain.value for t in graded))}")

    if graded:
        lv = Counter(t.level for t in graded)
        pct = {k: round(lv[k] / len(graded) * 100) for k in (1, 2, 3)}
        print(f"难度占比: L1 {pct[1]}% / L2 {pct[2]}% / L3 {pct[3]}%  (目标 30/50/20)")

    if errs:
        print("\n[FAIL] 发现问题:")
        for e in errs:
            print("  -", e)
        return 1
    print("\n[ OK ] 数据集自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
