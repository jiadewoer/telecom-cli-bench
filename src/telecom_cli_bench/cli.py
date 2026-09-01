"""命令行入口：tcb validate / tcb run / tcb score / tcb report"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console

from .runner import run_model
from .schema import load_tasks
from .scorer import score_one

app = typer.Typer(add_completion=False)
console = Console()

TASK_DIR = Path("data/tasks")
PROMPT_DIR = Path("configs/prompts")
RAW_DIR = Path("results/raw")
SCORE_DIR = Path("results/scored")


@app.command()
def validate() -> None:
    """数据集自检（等价于 python scripts/validate_dataset.py）。"""
    import subprocess
    import sys

    raise typer.Exit(subprocess.call([sys.executable, "scripts/validate_dataset.py"]))


@app.command()
def run(model: str, prompt: str = "zero_shot", concurrency: int = 1) -> None:
    # 这里的默认值会覆盖 run_model 的默认值，两处必须一致。
    # 曾经 cli 是 2、runner 是 1，外层静默生效，导致我以为自己在测并发 1 的表现。
    """跑一个模型 × 一套提示词。"""
    tasks = load_tasks(TASK_DIR)
    console.print(f"加载 {len(tasks)} 条评测任务（demo_ 前缀已排除）")
    tmpl = (PROMPT_DIR / f"{prompt}.txt").read_text(encoding="utf-8")
    p = asyncio.run(run_model(model, prompt, tmpl, tasks, RAW_DIR, concurrency))
    console.print(f"[green]原始输出已写入[/green] {p}")


@app.command()
def score() -> None:
    """对 results/raw 下所有原始输出评分。"""
    tasks = {t.id: t for t in load_tasks(TASK_DIR)}
    SCORE_DIR.mkdir(parents=True, exist_ok=True)
    rows, skipped = [], 0
    seen: dict[str, set[str]] = {}  # 组合 -> 该组合覆盖到的 task_id
    for p in sorted(RAW_DIR.glob("raw__*.jsonl")):
        seen[p.stem.replace("raw__", "")] = set()
        with p.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d["task_id"] not in tasks:  # 题目被删或改名时不要炸掉
                    skipped += 1
                    continue
                seen[p.stem.replace("raw__", "")].add(d["task_id"])
                s = score_one(
                    tasks[d["task_id"]], d["model"], d["prompt"],
                    d["output"], d.get("latency_s", 0.0),
                )
                rows.append(s.to_row())
    out = SCORE_DIR / "scores.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    console.print(f"[green]已评分 {len(rows)} 条[/green] -> {out}")
    if skipped:
        console.print(f"[yellow]跳过 {skipped} 条：原始输出里的 task_id 已不在数据集中[/yellow]")

    # 反向检查：数据集里有、但某个组合没跑到的题。
    #
    # 上面那个 skipped 管的是「raw 有、数据集没有」，加题时会遇到的是反方向：
    # 新题没有任何 raw，评分时静默缺席，报表照样出，没有任何提示。
    # run_matrix.ps1 的 Test-Path 跳过逻辑会加重这件事——文件已存在就跳过，
    # 不管它是不是旧数据集跑出来的。
    #
    # 这和 D12 记的那个「静默成功的脚本比崩掉的脚本危险」是同一类问题：
    # 你会以为矩阵是完整的，直到发现某道题在所有模型上都没有数据。
    if seen:
        expected = set(tasks)
        incomplete = {k: expected - v for k, v in seen.items() if expected - v}
        if incomplete:
            missing_all = set.intersection(*incomplete.values()) if len(incomplete) == len(seen) else set()
            console.print(
                f"[yellow]警告：{len(incomplete)}/{len(seen)} 个组合没有覆盖全部 "
                f"{len(expected)} 道题[/yellow]"
            )
            if missing_all:
                console.print(
                    f"[yellow]  以下 {len(missing_all)} 道题在所有组合里都没有数据，"
                    f"排行榜不包含它们：[/yellow]\n  {', '.join(sorted(missing_all))}"
                )
                console.print(
                    "[yellow]  数据集加过题？删掉 results/raw/ 下对应文件后重跑 "
                    "scripts/run_matrix.ps1。[/yellow]"
                )


@app.command()
def inspect(model: str, prompt: str = "zero_shot", n: int = 5) -> None:
    """人工核对：打印前 n 条的原始输出与归一化结果。第一次跑完必须做这件事。"""
    safe = model.replace(":", "_").replace("/", "_")
    p = RAW_DIR / f"raw__{safe}__{prompt}.jsonl"
    tasks = {t.id: t for t in load_tasks(TASK_DIR)}
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    for d in lines[:n]:
        s = score_one(tasks[d["task_id"]], d["model"], d["prompt"], d["output"])
        console.rule(f"{d['task_id']}  passed={s.passed}  score={s.checkpoint_score:.2f}")
        console.print("[dim]--- 原始输出 ---[/dim]")
        # markup=False 必须加：模型输出里的 [Huawei] 会被 rich 当成样式标签吞掉，
        # 屏幕上显示为空行，让你误以为模型没输出东西。
        console.print(d["output"][:600], markup=False, highlight=False)
        console.print("[dim]--- 归一化后 ---[/dim]")
        console.print(s.normalized or "(空)", markup=False, highlight=False)
        console.print(f"[dim]命中[/dim] {s.hit}  [dim]未命中[/dim] {s.miss}  [dim]标签[/dim] {s.tags}")


@app.command()
def report() -> None:
    """出 Leaderboard 与图表（Day 8 实现 report.py 后可用）。"""
    from .report import build_leaderboard, plot_all

    md = build_leaderboard(SCORE_DIR / "scores.jsonl")
    Path("results/leaderboard.md").write_text(md, encoding="utf-8")
    plot_all(SCORE_DIR / "scores.jsonl", Path("docs/images"))
    console.print(md)


if __name__ == "__main__":
    app()
