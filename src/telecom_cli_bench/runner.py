"""调 Ollama 跑评测。复用项目① 的 httpx 异步写法。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm_asyncio

from .schema import Task

BASE_URL = "http://localhost:11434"
VENDOR_CN = {"huawei": "华为 VRP", "cisco": "思科 IOS"}


def build_prompt(template: str, task: Task) -> str:
    return template.format(
        vendor_cn=VENDOR_CN[task.vendor.value],
        context=task.context or "（无特殊说明）",
        instruction=task.instruction,
    )


async def _ask(
    client: httpx.AsyncClient, model: str, prompt: str, sem: asyncio.Semaphore
) -> tuple[str, float]:
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await client.post(
                f"{BASE_URL}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 768,
                },
                timeout=900,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            return (f"__ERROR__ {e}", time.perf_counter() - t0)
        return (text, time.perf_counter() - t0)


async def run_model(
    model: str,
    prompt_name: str,
    template: str,
    tasks: list[Task],
    out_dir: Path,
    concurrency: int = 2,
) -> Path:
    """跑一个 (模型 × 提示词) 组合，原始输出落盘。

    并发只开 2：8GB 卡上开高了会排队，反而更慢，还可能触发卸载。
    temperature=0 保证可复现——这是 benchmark 的底线。
    """
    sem = asyncio.Semaphore(concurrency)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = model.replace(":", "_").replace("/", "_")
    out_path = out_dir / f"raw__{safe}__{prompt_name}.jsonl"

    async with httpx.AsyncClient() as client:
        results = await tqdm_asyncio.gather(
            *[_ask(client, model, build_prompt(template, t), sem) for t in tasks],
            desc=f"{model} / {prompt_name}",
        )

    n_err = sum(1 for text, _ in results if text.startswith("__ERROR__"))
    with out_path.open("w", encoding="utf-8") as f:
        for task, (text, lat) in zip(tasks, results, strict=True):
            f.write(
                json.dumps(
                    {
                        "task_id": task.id,
                        "model": model,
                        "prompt": prompt_name,
                        "output": text,
                        "latency_s": lat,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    if n_err:
        print(f"[warn] {n_err}/{len(results)} 次请求失败，已写入 __ERROR__ 占位，建议排查后重跑")
    return out_path
