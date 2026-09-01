"""对比同一 (模型 × 提示词) 组合的两次运行，量化输出漂移。

一个 benchmark 声称可复现之前，得先证明它真的可复现。

首轮实测中，只设 temperature=0、不设 seed、并发为 2 时，
同一组合两次跑出的 80 条里有 11 条（22%）原始输出不一致，
其中两条直接改变了 passed 判定。这个脚本就是为了把那个数字量化出来，
并在加了 seed 之后验证它降到了多少。

用法：
    copy results\\raw\\raw__qwen2.5_7b__zero_shot.jsonl results\\raw\\_run1.jsonl
    tcb run qwen2.5:7b --prompt zero_shot
    python scripts/compare_runs.py \
        results/raw/_run1.jsonl results/raw/raw__qwen2.5_7b__zero_shot.jsonl

输出里最重要的是最后一行：分数发生变化的条数。
原始输出有细微差别但分数不变，对排名没有影响；
分数变了才说明 Leaderboard 上的数字会随运行次数波动。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from telecom_cli_bench.schema import load_tasks  # noqa: E402
from telecom_cli_bench.scorer import score_one  # noqa: E402


def load_raw(path: Path) -> dict[str, dict]:
    if not path.exists():
        sys.exit(f"[FAIL] 找不到 {path}")
    rows = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                d = json.loads(line)
                rows[d["task_id"]] = d
    return rows


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a_path, b_path = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = load_raw(a_path), load_raw(b_path)

    common = sorted(set(a) & set(b))
    if not common:
        sys.exit("[FAIL] 两个文件没有共同的 task_id，是不是对比了不同的模型或提示词？")
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    if only_a or only_b:
        print(f"[WARN] 仅出现在一侧的任务: A {len(only_a)} 条 / B {len(only_b)} 条，已跳过")

    tasks = {t.id: t for t in load_tasks(REPO_ROOT / "data" / "tasks")}

    text_diff, score_diff, pass_flip = [], [], []
    for tid in common:
        oa, ob = a[tid]["output"], b[tid]["output"]
        if oa == ob:
            continue
        text_diff.append(tid)
        task = tasks.get(tid)
        if task is None:  # 题目改过或已删除，无法重新评分
            continue
        sa = score_one(task, "cmp", "cmp", oa)
        sb = score_one(task, "cmp", "cmp", ob)
        if abs(sa.checkpoint_score - sb.checkpoint_score) > 1e-9 or sa.passed != sb.passed:
            score_diff.append((tid, sa.checkpoint_score, sb.checkpoint_score, sa.passed, sb.passed))
        if sa.passed != sb.passed:
            pass_flip.append(tid)

    n = len(common)
    print(f"\n对比 {a_path.name}  vs  {b_path.name}")
    print(f"共同任务 {n} 条")
    print(f"原始输出不一致 : {len(text_diff):>3} 条 ({len(text_diff) / n * 100:.1f}%)")
    print(f"检查点得分变化 : {len(score_diff):>3} 条 ({len(score_diff) / n * 100:.1f}%)")
    print(f"passed 判定翻转: {len(pass_flip):>3} 条 ({len(pass_flip) / n * 100:.1f}%)")

    if score_diff:
        print("\n分数发生变化的任务：")
        for tid, s1, s2, p1, p2 in score_diff:
            flag = "  <-- passed 翻转" if p1 != p2 else ""
            print(f"  {tid:<16} {s1:.2f} -> {s2:.2f}{flag}")

    if text_diff and not score_diff:
        print("\n有输出漂移但分数全部不变，对 Leaderboard 无影响。漂移的任务：")
        print("  " + ", ".join(text_diff))

    print(
        "\n把 passed 翻转的条数写进 README 的限制说明。"
        "\nGPU 上的浮点归约顺序无法从客户端彻底固定，声称完全可复现是不诚实的；"
        "\n给出一个实测数字，读者才知道该把排名差异读到什么精度。"
    )


if __name__ == "__main__":
    main()
