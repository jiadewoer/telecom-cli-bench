"""CLI entry point: tcb validate / run / check-raw / score / inspect / report."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from .paths import IMAGE_DIR, PROJECT_ROOT, PROMPT_DIR, RAW_DIR, SCORE_DIR, SCRIPT_DIR, TASK_DIR
from .runner import (
    EndpointPreflightError,
    InferenceRunError,
    get_base_url,
    inspect_raw_file,
    preflight_endpoint,
    run_model,
)
from .schema import load_tasks
from .scorer import score_one

app = typer.Typer(add_completion=False)
console = Console()


def _infra_row(row: dict) -> bool:
    output = row.get("output", "")
    return row.get("status", "ok") != "ok" or (
        isinstance(output, str) and output.startswith("__ERROR__")
    )


@app.command()
def validate() -> None:
    """Run dataset self-check from any current working directory."""
    script = SCRIPT_DIR / "validate_dataset.py"
    raise typer.Exit(subprocess.call([sys.executable, str(script)], cwd=PROJECT_ROOT))


@app.command()
def run(
    model: str,
    prompt: str = "zero_shot",
    concurrency: int = 1,
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="OpenAI-compatible Ollama base URL; defaults to TCB_BASE_URL or localhost:11434.",
    ),
    trust_env_proxy: bool = typer.Option(
        False,
        "--trust-env-proxy",
        help="Allow httpx to use HTTP_PROXY/HTTPS_PROXY from the environment. Disabled by default.",
    ),
) -> None:
    """Run one model × prompt. Infrastructure errors make the command fail."""
    if concurrency < 1:
        console.print("[red]--concurrency 必须 >= 1[/red]")
        raise typer.Exit(2)
    prompt_path = PROMPT_DIR / f"{prompt}.txt"
    if not prompt_path.exists():
        console.print(f"[red]提示词不存在：{prompt_path}[/red]")
        raise typer.Exit(2)

    tasks = load_tasks(TASK_DIR)
    endpoint = get_base_url(base_url)
    proxy_mode = "读取系统代理" if trust_env_proxy else "直连（忽略 HTTP_PROXY/HTTPS_PROXY）"
    console.print(f"加载 {len(tasks)} 条评测任务（demo_ 前缀已排除）")
    console.print(f"Ollama endpoint: {endpoint}；网络模式：{proxy_mode}")
    tmpl = prompt_path.read_text(encoding="utf-8")
    try:
        path = asyncio.run(
            run_model(
                model,
                prompt,
                tmpl,
                tasks,
                RAW_DIR,
                concurrency,
                base_url=base_url,
                trust_env_proxy=trust_env_proxy,
            )
        )
    except EndpointPreflightError as exc:
        console.print(
            f"[red]Ollama 预检失败：{exc.error_type}[/red]\n"
            f"[red]{exc.message}[/red]"
        )
        if exc.error_type == "http_502":
            console.print(
                "[yellow]502 通常来自 HTTP 代理/反向代理，而不是 benchmark scorer。"
                " 本工具默认已绕过系统 HTTP_PROXY/HTTPS_PROXY；"
                "若仍是 502，请直接检查 Ollama 服务端或其前置代理。[/yellow]"
            )
        console.print("[yellow]未开始 122 条正式推理，也不会覆盖现有 raw。[/yellow]")
        raise typer.Exit(1) from exc
    except InferenceRunError as exc:
        console.print(
            f"[red]推理基础设施失败：{exc.failed}/{exc.total} 条。[/red] "
            f"诊断数据已写入 {exc.out_path}；该文件不会被 scorer 当作模型错误。"
        )
        console.print(f"[red]错误类型：{exc.error_types}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]原始输出已写入[/green] {path}")


@app.command("check-raw")
def check_raw(
    path: Path,
    quiet: bool = False,
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="When set, also require every raw row to have been produced by this endpoint.",
    ),
) -> None:
    """Check exact task coverage, infrastructure status, and optional endpoint provenance."""
    expected = {t.id for t in load_tasks(TASK_DIR)}
    health = inspect_raw_file(path, expected, expected_base_url=base_url)
    if not quiet or not health.complete:
        console.print(
            f"{path}: rows={health.rows}, ok={health.ok_rows}, infra={health.infra_errors}, "
            f"malformed={health.malformed_rows}, duplicate={len(health.duplicate_ids)}, "
            f"missing={len(health.missing_ids)}, unexpected={len(health.unexpected_ids)}"
        )
        if health.error_types:
            console.print(f"  error_types={health.error_types}")
    if not health.complete:
        raise typer.Exit(1)



@app.command()
def doctor(
    model: str = typer.Option(..., "--model", help="Model that must exist on the Ollama server."),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="OpenAI-compatible Ollama base URL; defaults to TCB_BASE_URL or localhost:11434.",
    ),
    trust_env_proxy: bool = typer.Option(
        False,
        "--trust-env-proxy",
        help="Allow httpx to use HTTP_PROXY/HTTPS_PROXY from the environment.",
    ),
) -> None:
    """Probe Ollama once without starting the benchmark."""
    import httpx

    endpoint = get_base_url(base_url)

    async def _probe() -> tuple[str, ...]:
        async with httpx.AsyncClient(trust_env=trust_env_proxy) as client:
            return await preflight_endpoint(client, model, endpoint)

    try:
        models = asyncio.run(_probe())
    except EndpointPreflightError as exc:
        console.print(f"[red]FAIL {exc.error_type}[/red] {exc.message}", markup=False)
        raise typer.Exit(1) from exc

    console.print(
        f"[green]OK[/green] {endpoint} 可访问，模型 [bold]{model}[/bold] 已找到；"
        f"服务器共暴露 {len(models)} 个模型。"
    )


@app.command()
def score() -> None:
    """Score all healthy raw outputs; never convert infra failures into model errors."""
    tasks = {t.id: t for t in load_tasks(TASK_DIR)}
    SCORE_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    seen: dict[str, set[str]] = {}
    integrity_errors: list[str] = []

    raw_files = sorted(RAW_DIR.glob("raw__*.jsonl"))
    if not raw_files:
        console.print(f"[red]没有找到原始输出：{RAW_DIR}[/red]")
        raise typer.Exit(1)

    for path in raw_files:
        combo = path.stem.replace("raw__", "")
        health = inspect_raw_file(path, set(tasks))
        if not health.complete:
            integrity_errors.append(
                f"{path.name}: infra={health.infra_errors}, malformed={health.malformed_rows}, "
                f"duplicate={len(health.duplicate_ids)}, missing={len(health.missing_ids)}, "
                f"unexpected={len(health.unexpected_ids)}"
            )
        seen[combo] = set()
        with path.open(encoding="utf-8-sig") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _infra_row(data):
                    continue
                task_id = data.get("task_id")
                if task_id not in tasks:
                    continue
                if task_id in seen[combo]:
                    continue
                seen[combo].add(task_id)
                try:
                    score = score_one(
                        tasks[task_id],
                        data["model"],
                        data["prompt"],
                        data["output"],
                        data.get("latency_s", 0.0),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    integrity_errors.append(f"{path.name}:{lineno}: malformed score row ({exc})")
                    continue
                rows.append(score.to_row())

    out = SCORE_DIR / "scores.jsonl"
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(out)

    console.print(f"[green]已评分 {len(rows)} 条健康输出[/green] -> {out}")
    if integrity_errors:
        console.print("[red]检测到原始结果完整性问题，评分结果仅包含健康记录：[/red]")
        for msg in integrity_errors[:20]:
            console.print(f"  [red]- {msg}[/red]")
        if len(integrity_errors) > 20:
            console.print(f"  [red]... 另有 {len(integrity_errors) - 20} 条[/red]")
        console.print("[red]请重跑上述组合；本次 tcb score 返回非 0，避免生成误导性榜单。[/red]")
        raise typer.Exit(1)


@app.command()
def inspect(model: str, prompt: str = "zero_shot", n: int = 5) -> None:
    """Print raw and normalized outputs for manual verification."""
    safe = model.replace(":", "_").replace("/", "_")
    path = RAW_DIR / f"raw__{safe}__{prompt}.jsonl"
    tasks = {t.id: t for t in load_tasks(TASK_DIR)}
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
    for data in lines[:n]:
        if _infra_row(data):
            console.rule(f"{data.get('task_id', '?')}  INFRA_ERROR")
            console.print(
                f"{data.get('error_type', 'legacy_error')}: "
                f"{data.get('error_message') or data.get('output', '')}",
                markup=False,
                highlight=False,
            )
            continue
        score = score_one(tasks[data["task_id"]], data["model"], data["prompt"], data["output"])
        console.rule(
            f"{data['task_id']}  passed={score.passed}  score={score.checkpoint_score:.2f}"
        )
        console.print("[dim]--- 原始输出 ---[/dim]")
        console.print(data["output"][:600], markup=False, highlight=False)
        console.print("[dim]--- 归一化后 ---[/dim]")
        console.print(score.normalized or "(空)", markup=False, highlight=False)
        console.print(
            f"[dim]命中[/dim] {score.hit}  [dim]未命中[/dim] {score.miss}  "
            f"[dim]标签[/dim] {score.tags}"
        )


@app.command()
def report() -> None:
    """Build leaderboard and charts from healthy scores."""
    from .report import build_leaderboard, plot_all

    score_path = SCORE_DIR / "scores.jsonl"
    md = build_leaderboard(score_path)
    leaderboard = PROJECT_ROOT / "results" / "leaderboard.md"
    leaderboard.write_text(md, encoding="utf-8")
    plot_all(score_path, IMAGE_DIR)
    console.print(md)


if __name__ == "__main__":
    app()
