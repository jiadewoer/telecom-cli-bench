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
def run(model: str, prompt: str = "zero_shot", concurrency: int = 2) -> None:
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
    for p in sorted(RAW_DIR.glob("raw__*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d["task_id"] not in tasks:  # 题目被删或改名时不要炸掉
                    skipped += 1
                    continue
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
        console.print(d["output"][:600])
        console.print("[dim]--- 归一化后 ---[/dim]")
        console.print(s.normalized or "(空)")
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
