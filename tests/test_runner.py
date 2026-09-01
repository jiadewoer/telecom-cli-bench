import asyncio
import json

import httpx
import pytest

import telecom_cli_bench.runner as runner
from telecom_cli_bench.runner import (
    AskResult,
    EndpointPreflightError,
    InferenceRunError,
    _ask,
    inspect_raw_file,
    preflight_endpoint,
    run_model,
)
from telecom_cli_bench.schema import Checkpoint, Task

TASK = Task(
    id="t-runner",
    vendor="cisco",
    domain="diagnose",
    level=1,
    instruction="show status",
    reference=["show ip interface brief"],
    checkpoints=[Checkpoint(id="c1", pattern=r"^show ip interface brief$")],
)


def test_ask_http_error_is_structured_infra_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, json={"error": "offline"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _ask(client, "m", "p", asyncio.Semaphore(1), "http://test")

    result = asyncio.run(go())
    assert result.status == "infra_error"
    assert result.error_type == "http_503"
    assert result.output == ""


def test_run_model_fails_nonzero_semantics_but_keeps_diagnostic_raw(tmp_path, monkeypatch):
    async def fake_preflight(client, model, base_url):
        return (model,)

    async def fake_ask(client, model, prompt, sem, base_url):
        return AskResult(
            output="",
            latency_s=0.01,
            status="infra_error",
            error_type="connection_error",
            error_message="server down",
        )

    monkeypatch.setattr(runner, "preflight_endpoint", fake_preflight)
    monkeypatch.setattr(runner, "_ask", fake_ask)
    with pytest.raises(InferenceRunError) as exc:
        asyncio.run(run_model("m", "zero", "{instruction}", [TASK], tmp_path))

    path = exc.value.out_path
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["status"] == "infra_error"
    assert row["error_type"] == "connection_error"
    assert row["output"] == ""


def test_raw_health_rejects_legacy_error_duplicate_and_missing(tmp_path):
    path = tmp_path / "raw.jsonl"
    rows = [
        {"task_id": "a", "model": "m", "prompt": "p", "output": "__ERROR__ connection refused"},
        {"task_id": "a", "model": "m", "prompt": "p", "output": "```\nshow x\n```", "status": "ok"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    health = inspect_raw_file(path, {"a", "b"})
    assert not health.complete
    assert health.infra_errors == 1
    assert health.duplicate_ids == ("a",)
    assert health.missing_ids == ("b",)


def test_raw_health_accepts_exact_healthy_coverage(tmp_path):
    path = tmp_path / "raw.jsonl"
    rows = [
        {"task_id": "a", "model": "m", "prompt": "p", "output": "x", "status": "ok"},
        {"task_id": "b", "model": "m", "prompt": "p", "output": "y", "status": "ok"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    health = inspect_raw_file(path, {"a", "b"})
    assert health.complete
    assert health.ok_rows == 2



def test_preflight_502_fails_fast_and_keeps_gateway_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            request=request,
            text="Bad Gateway from corp proxy",
            headers={"server": "proxy-test"},
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await preflight_endpoint(client, "qwen2.5:7b", "http://192.168.1.10:11434")

    with pytest.raises(EndpointPreflightError) as exc:
        asyncio.run(go())
    assert exc.value.error_type == "http_502"
    assert "Bad Gateway from corp proxy" in exc.value.message
    assert "server=proxy-test" in exc.value.message


def test_preflight_rejects_missing_model_before_benchmark():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"object": "list", "data": [{"id": "llama3.1:8b", "object": "model"}]},
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await preflight_endpoint(client, "qwen2.5:7b", "http://test")

    with pytest.raises(EndpointPreflightError) as exc:
        asyncio.run(go())
    assert exc.value.error_type == "model_not_found"


def test_run_model_ignores_environment_proxy_by_default(tmp_path, monkeypatch):
    seen = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_preflight(client, model, base_url):
        return (model,)

    async def fake_ask(client, model, prompt, sem, base_url):
        return AskResult(output="ok", latency_s=0.01)

    monkeypatch.setattr(runner.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(runner, "preflight_endpoint", fake_preflight)
    monkeypatch.setattr(runner, "_ask", fake_ask)

    path = asyncio.run(run_model("m", "zero", "{instruction}", [TASK], tmp_path))
    row = json.loads(path.read_text(encoding="utf-8"))

    assert seen["trust_env"] is False
    assert row["base_url"] == "http://localhost:11434"
    assert row["trust_env_proxy"] is False


def test_raw_health_can_require_endpoint_provenance(tmp_path):
    path = tmp_path / "raw.jsonl"
    rows = [
        {
            "task_id": "a",
            "model": "m",
            "prompt": "p",
            "output": "x",
            "status": "ok",
            "base_url": "http://localhost:11434",
        }
    ]
    path.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")

    same = inspect_raw_file(path, {"a"}, expected_base_url="http://localhost:11434/")
    other = inspect_raw_file(path, {"a"}, expected_base_url="http://192.168.1.10:11434")

    assert same.complete
    assert not other.complete
    assert other.error_types["endpoint_mismatch"] == 1
