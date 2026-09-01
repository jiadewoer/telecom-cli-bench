"""Call an OpenAI-compatible Ollama endpoint and persist raw benchmark outputs."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm_asyncio

from .paths import DEMO_FILE
from .schema import Task, load_tasks

DEFAULT_BASE_URL = "http://localhost:11434"
VENDOR_CN = {"huawei": "华为 VRP", "cisco": "思科 IOS"}


@dataclass(frozen=True)
class AskResult:
    output: str
    latency_s: float
    status: str = "ok"
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RawFileHealth:
    path: Path
    rows: int
    ok_rows: int
    infra_errors: int
    malformed_rows: int
    duplicate_ids: tuple[str, ...] = ()
    missing_ids: tuple[str, ...] = ()
    unexpected_ids: tuple[str, ...] = ()
    error_types: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not (
            self.infra_errors
            or self.malformed_rows
            or self.duplicate_ids
            or self.missing_ids
            or self.unexpected_ids
        )


class EndpointPreflightError(RuntimeError):
    """Raised before a benchmark run when the configured Ollama endpoint is unhealthy."""

    def __init__(self, base_url: str, error_type: str, message: str):
        self.base_url = base_url
        self.error_type = error_type
        self.message = message
        super().__init__(f"{base_url}: {error_type}: {message}")


class InferenceRunError(RuntimeError):
    """Raised after a raw file is written when one or more inference calls failed."""

    def __init__(self, out_path: Path, failed: int, total: int, error_types: dict[str, int]):
        self.out_path = out_path
        self.failed = failed
        self.total = total
        self.error_types = error_types
        detail = ", ".join(f"{k}={v}" for k, v in sorted(error_types.items())) or "unknown"
        super().__init__(
            f"{failed}/{total} inference requests failed ({detail}); raw file kept at {out_path}"
        )


def get_base_url(base_url: str | None = None) -> str:
    """Resolve endpoint from CLI argument, environment, then local Ollama default."""
    return (base_url or os.getenv("TCB_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def build_examples(vendor: str) -> str:
    """Select same-vendor few-shot examples to avoid teaching vendor confusion."""
    if not DEMO_FILE.exists():
        return ""
    demos = [t for t in load_tasks(DEMO_FILE, include_demo=True) if t.vendor.value == vendor]
    demos.sort(key=lambda t: t.level)
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
    return template.format(
        vendor_cn=VENDOR_CN[vendor],
        context=task.context or "（无特殊说明）",
        instruction=task.instruction,
        examples=build_examples(vendor),
    )


def _classify_exception(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "connection_error"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError)):
        return "invalid_response"
    return "unexpected_error"


def _format_exception(exc: Exception) -> str:
    """Keep enough HTTP diagnostics to identify proxies/gateways without dumping huge bodies."""
    message = str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        body = response.text.strip().replace("\r", " ").replace("\n", " ")[:500]
        server = response.headers.get("server")
        via = response.headers.get("via")
        details = []
        if server:
            details.append(f"server={server}")
        if via:
            details.append(f"via={via}")
        if body:
            details.append(f"body={body}")
        if details:
            message = f"{message}; " + "; ".join(details)
    return message


async def preflight_endpoint(
    client: httpx.AsyncClient,
    model: str,
    base_url: str,
) -> tuple[str, ...]:
    """Fail fast before 122 requests and verify the requested model is visible."""
    try:
        response = await client.get(f"{base_url}/v1/models", timeout=15)
        response.raise_for_status()
        payload = response.json()
        data = payload["data"]
        if not isinstance(data, list):
            raise TypeError("v1/models data is not a list")
        model_ids = tuple(
            item["id"]
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
        if model not in model_ids:
            shown = ", ".join(model_ids[:20]) or "(none)"
            raise EndpointPreflightError(
                base_url,
                "model_not_found",
                f"model {model!r} is not exposed by /v1/models; available: {shown}",
            )
        return model_ids
    except EndpointPreflightError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EndpointPreflightError(
            base_url,
            _classify_exception(exc),
            _format_exception(exc),
        ) from exc


async def _ask(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    sem: asyncio.Semaphore,
    base_url: str,
) -> AskResult:
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "seed": 42,
                    "top_p": 1.0,
                    "max_tokens": 2048,
                },
                timeout=900,
            )
            r.raise_for_status()
            payload = r.json()
            text = payload["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError("choices[0].message.content is not a string")
            return AskResult(output=text, latency_s=time.perf_counter() - t0)
        except Exception as exc:  # noqa: BLE001
            return AskResult(
                output="",
                latency_s=time.perf_counter() - t0,
                status="infra_error",
                error_type=_classify_exception(exc),
                error_message=_format_exception(exc),
            )


def inspect_raw_file(
    path: Path,
    expected_ids: set[str],
    expected_base_url: str | None = None,
) -> RawFileHealth:
    """Validate exact task coverage and reject infrastructure failures.

    Legacy ``__ERROR__ ...`` outputs are treated as infrastructure failures too,
    so old contaminated raw files cannot silently pass the matrix skip check.
    """
    if not path.exists():
        return RawFileHealth(
            path=path,
            rows=0,
            ok_rows=0,
            infra_errors=0,
            malformed_rows=1,
            missing_ids=tuple(sorted(expected_ids)),
        )

    task_ids: list[str] = []
    infra_errors = 0
    malformed = 0
    row_count = 0
    error_types: Counter[str] = Counter()
    ok_rows = 0
    endpoint_mismatches = 0
    normalized_expected_base_url = get_base_url(expected_base_url) if expected_base_url else None

    with path.open(encoding="utf-8-sig") as f:
        for line in f:
            if not line.strip():
                continue
            row_count += 1
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                malformed += 1
                continue
            task_id = row.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                malformed += 1
                continue
            task_ids.append(task_id)
            output = row.get("output", "")
            status = row.get("status", "ok")
            if not isinstance(row.get("model"), str) or not isinstance(row.get("prompt"), str):
                malformed += 1
                continue
            if not isinstance(output, str):
                malformed += 1
                continue
            if normalized_expected_base_url is not None:
                row_base_url = row.get("base_url")
                if (
                    not isinstance(row_base_url, str)
                    or row_base_url.rstrip("/") != normalized_expected_base_url
                ):
                    endpoint_mismatches += 1
            is_legacy_error = isinstance(output, str) and output.startswith("__ERROR__")
            if status != "ok" or is_legacy_error:
                infra_errors += 1
                error_types[str(row.get("error_type") or "legacy_error")] += 1
            else:
                ok_rows += 1

    counts = Counter(task_ids)
    duplicates = tuple(sorted(task_id for task_id, n in counts.items() if n > 1))
    got = set(task_ids)
    if endpoint_mismatches:
        error_types["endpoint_mismatch"] += endpoint_mismatches

    return RawFileHealth(
        path=path,
        rows=row_count,
        ok_rows=ok_rows,
        infra_errors=infra_errors + endpoint_mismatches,
        malformed_rows=malformed,
        duplicate_ids=duplicates,
        missing_ids=tuple(sorted(expected_ids - got)),
        unexpected_ids=tuple(sorted(got - expected_ids)),
        error_types=dict(error_types),
    )


async def run_model(
    model: str,
    prompt_name: str,
    template: str,
    tasks: list[Task],
    out_dir: Path,
    concurrency: int = 1,
    base_url: str | None = None,
    trust_env_proxy: bool = False,
) -> Path:
    """Run one model/prompt pair and atomically persist structured raw results.

    Failed inference calls are written with ``status=infra_error`` for diagnosis,
    then ``InferenceRunError`` is raised so callers receive a non-zero exit code.
    This prevents transport/model-server failures from being scored as model errors.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")

    sem = asyncio.Semaphore(concurrency)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = model.replace(":", "_").replace("/", "_")
    out_path = out_dir / f"raw__{safe}__{prompt_name}.jsonl"
    endpoint = get_base_url(base_url)

    # Direct Ollama/LAN access should not accidentally inherit HTTP_PROXY/HTTPS_PROXY.
    # Users who intentionally need those variables can opt in with --trust-env-proxy.
    async with httpx.AsyncClient(trust_env=trust_env_proxy) as client:
        await preflight_endpoint(client, model, endpoint)
        results = await tqdm_asyncio.gather(
            *[_ask(client, model, build_prompt(template, t), sem, endpoint) for t in tasks],
            desc=f"{model} / {prompt_name}",
        )

    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
        for task, result in zip(tasks, results, strict=True):
            row = {
                "task_id": task.id,
                "model": model,
                "prompt": prompt_name,
                "output": result.output,
                "latency_s": result.latency_s,
                "status": result.status,
                "base_url": endpoint,
                "trust_env_proxy": trust_env_proxy,
            }
            if result.status != "ok":
                row["error_type"] = result.error_type
                row["error_message"] = result.error_message
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(out_path)

    errors = [r for r in results if r.status != "ok"]
    if errors:
        error_types = Counter(r.error_type or "unknown" for r in errors)
        raise InferenceRunError(out_path, len(errors), len(results), dict(error_types))
    return out_path
