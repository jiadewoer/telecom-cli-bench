"""调 Ollama 跑评测。复用项目① 的 httpx 异步写法。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm_asyncio

from .schema import Task, load_tasks

BASE_URL = "http://localhost:11434"
VENDOR_CN = {"huawei": "华为 VRP", "cisco": "思科 IOS"}

DEMO_FILE = Path("data/tasks/demo.jsonl")


def build_examples(vendor: str) -> str:
    """按厂商取 few-shot 示例。

    ⚠️ 这是本项目最容易犯的一个错：如果 few-shot 模板里写死华为示例，
    跑思科题时就等于在提示词里主动教模型串味，
    厂商串味率这个核心指标会直接失真。
    示例必须和题目同厂商。
    """
    if not DEMO_FILE.exists():
        return ""
    demos = [t for t in load_tasks(DEMO_FILE, include_demo=True) if t.vendor.value == vendor]
    demos.sort(key=lambda t: t.level)  # 先 L1 后 L2，由易到难
    blocks = []
    for i, t in enumerate(demos[:2], 1):
        cmds = "\n".join(t.reference)
        blocks.append(
            f"示例{'一' if i == 1 else '二'}\n"
            f"设备现状：{t.context}\n"
            f"任务：{t.instruction}\n"
            f"```\n{cmds}\n```\n"
        )
    return "\n".join(blocks)


def build_prompt(template: str, task: Task) -> str:
    vendor = task.vendor.value
    # zero_shot / syntax_hint 模板里没有 {examples}，多传的参数会被 format 忽略
    return template.format(
        vendor_cn=VENDOR_CN[vendor],
        context=task.context or "（无特殊说明）",
        instruction=task.instruction,
        examples=build_examples(vendor),
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
                    # seed 必须显式给。只设 temperature=0 不够：Ollama 在并发时会把
                    # 多个请求拼进同一个 batch，batch 组成不同会让浮点归约顺序变化，
                    # 贪心解码也可能走出不同路径。实测同模型同提示词两次跑，
                    # 80 条里有 11 条（22%）原始输出不一致，其中 hw_diag_003
                    # 从 1.00 掉到 0.67，cs_acl_017 从 0.00 升到 0.75。
                    "seed": 42,
                    "top_p": 1.0,
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
    concurrency: int = 1,
) -> Path:
    """跑一个 (模型 × 提示词) 组合，原始输出落盘。

    并发只开 2：8GB 卡上开高了会排队，反而更慢，还可能触发卸载。
    temperature=0 + seed 固定 + 单并发，共同保证可复现——这是 benchmark 的底线。
    并发>1 时 Ollama 的 batch 组成会引入非确定性，见 _ask 里的注释与 docs/notes.md D13。
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
