"""从失败样本里分层抽样，导出人工标注用的 CSV。

## 这个脚本要回答的问题

Leaderboard 上的每个数字都建立在一个假设上：judged-fail 等于 actually-wrong。
这个假设从来没被验证过。正则可能写窄了、词表可能缺词、参考答案本身可能就是错的——
这些情况下模型答对了却被判失败，分数偏低，而且偏多少无从得知。

人工标注 60 条失败样本，就是为了把「评测本身的误判率」这个数字量化出来。
它会以「M7」的名字写进 README 的已知限制一节。

## 为什么是分层随机，不是挑可疑的

挑可疑样本能更快找到 bug，但算出来的误判率会系统性偏高，不能写进 README。
所以这里按「标签组合」分层，层内随机（固定 seed=42），
各层配额按其在失败样本中的占比分配。这样 60 条的估计是无偏的。

E8_EMPTY（正文为空）单独剔除：那是 max_tokens 预算不足导致的，
属于评测设计问题，已在 docs/notes.md D18 处理，不该混进判分准确性的估计里。

## 标注怎么填

CSV 里 `verdict` 一列留空，人工填三选一：

    模型真错     命令确实不对，判失败正确
    评分器误判   模型答对了，是正则/词表/归一化的问题
    题目有问题   reference 或 checkpoint 本身写错了

后两类之和 ÷ 60 就是 M7。

CSV 用 utf-8-sig 编码——Excel 打开 utf-8 无 BOM 的文件，中文全是乱码。
见 docs/notes.md D12。

用法：
    python scripts/sample_for_annotation.py            # 默认 60 条
    python scripts/sample_for_annotation.py --n 100
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telecom_cli_bench.normalize import extract_commands  # noqa: E402
from telecom_cli_bench.schema import load_tasks  # noqa: E402

SEED = 42
EXCLUDE_TAG = "E8_EMPTY"


def load_raw_outputs(raw_dir: Path) -> dict[tuple[str, str, str], str]:
    out: dict[tuple[str, str, str], str] = {}
    for path in raw_dir.glob("raw__*.jsonl"):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    out[(r["model"], r["prompt"], r["task_id"])] = r["output"]
    return out


def stratified_sample(rows: list[dict], n: int) -> list[dict]:
    """按 tags 组合分层，层内随机，配额按占比分配。

    小层至少给 1 个名额，否则占比低但可能富含 bug 的类别永远抽不到。
    """
    rng = random.Random(SEED)
    strata: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        strata[r["tags"] or "(无标签)"].append(r)

    total = len(rows)
    picked: list[dict] = []
    for key in sorted(strata, key=lambda k: -len(strata[k])):
        group = strata[key]
        quota = max(1, round(n * len(group) / total))
        quota = min(quota, len(group))
        picked += rng.sample(group, quota)

    rng.shuffle(picked)
    return picked[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--scores", type=Path, default=Path("results/scored/scores.jsonl"))
    ap.add_argument("--raw", type=Path, default=Path("results/raw"))
    ap.add_argument("--out", type=Path, default=Path("results/annotation_sample.csv"))
    args = ap.parse_args()

    if not args.scores.exists():
        sys.exit(f"[FAIL] 找不到 {args.scores}，先跑 tcb score")

    rows = [json.loads(x) for x in args.scores.read_text(encoding="utf-8").splitlines() if x.strip()]
    fails = [r for r in rows if not r["passed"] and EXCLUDE_TAG not in (r["tags"] or "")]
    print(f"总样本 {len(rows)}  失败 {sum(1 for r in rows if not r['passed'])}  "
          f"剔除 {EXCLUDE_TAG} 后 {len(fails)}")

    picked = stratified_sample(fails, args.n)
    raw = load_raw_outputs(args.raw)
    tasks = {t.id: t for t in load_tasks(Path("data/tasks"))}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "序号", "task_id", "model", "prompt", "vendor", "domain", "level",
            "得分", "未命中", "标签", "题目", "参考答案", "模型原始输出", "归一化后",
            "未命中的检查点正则", "verdict", "备注",
        ])
        for i, r in enumerate(picked, 1):
            t = tasks.get(r["task_id"])
            out = raw.get((r["model"], r["prompt"], r["task_id"]), "")
            cmds, _ = extract_commands(out)
            miss_ids = set((r["miss"] or "").split("|")) - {""}
            miss_pat = " ⏎ ".join(
                f"{c.id}: {c.pattern}" for c in (t.checkpoints if t else []) if c.id in miss_ids
            )
            w.writerow([
                i, r["task_id"], r["model"], r["prompt"], r["vendor"], r["domain"], r["level"],
                r["checkpoint_score"], r["miss"], r["tags"],
                t.instruction if t else "", " ⏎ ".join(t.reference) if t else "",
                out.strip(), " ⏎ ".join(cmds), miss_pat, "", "",
            ])

    dist = collections.Counter(r["tags"] or "(无标签)" for r in picked)
    print(f"\n已导出 {len(picked)} 条 -> {args.out}")
    print("各层条数:")
    for k, v in dist.most_common():
        print(f"  {v:>3}  {k}")
    print("\n填 verdict 一列，三选一：模型真错 / 评分器误判 / 题目有问题")
    print("后两类之和 ÷ 总数 = M7，写进 README 的已知限制。")


if __name__ == "__main__":
    main()
