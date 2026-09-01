# 全矩阵：6 模型 x 3 提示词 = 18 个组合
#
# 用法：
#   .\scripts\run_matrix.ps1 -BaseUrl http://192.168.1.10:11434
#   $env:TCB_BASE_URL = "http://192.168.1.10:11434"; .\scripts\run_matrix.ps1
#
# 默认不使用 HTTP_PROXY/HTTPS_PROXY，避免局域网 Ollama 被系统代理劫持。
# 只有明确需要环境代理时才加 -TrustEnvProxy。
#
# 已有 raw 只有在 task_id 精确覆盖、无 infra/malformed/重复，
# 且（指定 BaseUrl 时）endpoint provenance 一致才允许跳过。
#
# 本文件保存为 UTF-8 with BOM，以兼容 Windows PowerShell 5.1。

param(
    [string]$BaseUrl = $env:TCB_BASE_URL,
    [int]$Concurrency = 1,
    [switch]$TrustEnvProxy
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

try {
    if ($Concurrency -lt 1) {
        throw "Concurrency 必须 >= 1"
    }

    $json = Get-Content configs\models.json -Raw -Encoding UTF8 | ConvertFrom-Json
    $models = @($json | ForEach-Object { $_.model })
    if ($models.Count -eq 0) {
        throw "models.json 解析出 0 个模型。检查文件编码与格式。"
    }

    $endpointText = if ($BaseUrl) { $BaseUrl } else { "http://localhost:11434 (默认)" }
    $proxyText = if ($TrustEnvProxy) { "读取环境代理" } else { "直连，忽略 HTTP_PROXY/HTTPS_PROXY" }
    Write-Host "Ollama endpoint: $endpointText；$proxyText" -ForegroundColor Yellow
    Write-Host "待跑模型 $($models.Count) 个: $($models -join ', ')" -ForegroundColor Yellow

    # 先探测所有模型，任何一个不可用都在正式 benchmark 前失败。
    foreach ($m in $models) {
        $doctorArgs = @("doctor", "--model", $m)
        if ($BaseUrl) { $doctorArgs += @("--base-url", $BaseUrl) }
        if ($TrustEnvProxy) { $doctorArgs += "--trust-env-proxy" }
        & tcb @doctorArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Ollama 预检失败: $m。未启动完整矩阵。"
        }
    }

    $prompts = @("zero_shot", "few_shot", "syntax_hint")
    $t0 = Get-Date
    $ran = 0
    $skipped = 0
    $stale = 0

    foreach ($m in $models) {
        $didRun = $false
        foreach ($p in $prompts) {
            $safe = $m -replace ':', '_' -replace '/', '_'
            $out  = "results\raw\raw__${safe}__${p}.jsonl"
            if (Test-Path $out) {
                $checkArgs = @("check-raw", $out, "--quiet")
                if ($BaseUrl) { $checkArgs += @("--base-url", $BaseUrl) }
                & tcb @checkArgs
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "--- 跳过（完整、健康且 endpoint 一致）$m / $p" -ForegroundColor DarkGray
                    $skipped++
                    continue
                }
                Write-Host "--- 重跑（文件不完整、含错误、endpoint 不一致或数据集已变化）$m / $p" -ForegroundColor Yellow
                $stale++
            }

            Write-Host "=== $m / $p ===" -ForegroundColor Cyan
            $runArgs = @("run", $m, "--prompt", $p, "--concurrency", "$Concurrency")
            if ($BaseUrl) { $runArgs += @("--base-url", $BaseUrl) }
            if ($TrustEnvProxy) { $runArgs += "--trust-env-proxy" }
            & tcb @runArgs
            if ($LASTEXITCODE -ne 0) {
                throw "tcb run 失败: $m / $p。检查上方 Ollama 预检/HTTP 错误。"
            }

            $checkArgs = @("check-raw", $out, "--quiet")
            if ($BaseUrl) { $checkArgs += @("--base-url", $BaseUrl) }
            & tcb @checkArgs
            if ($LASTEXITCODE -ne 0) {
                throw "tcb run 返回成功，但 raw 完整性/endpoint 检查失败: $m / $p"
            }
            $ran++
            $didRun = $true
        }
        if ($didRun) { Start-Sleep -Seconds 10 }
    }

    if ($ran -eq 0) {
        Write-Host "没有新组合需要跑（$skipped 个已有结果均完整健康且 endpoint 一致）。" -ForegroundColor Yellow
    }

    tcb score
    if ($LASTEXITCODE -ne 0) { throw "tcb score 检测到 raw 完整性问题，已阻止静默出榜。" }

    $staleMsg = if ($stale -gt 0) { "，其中 $stale 个因检查失败触发重跑" } else { "" }
    Write-Host ("全矩阵完成：新跑 $ran 个组合，跳过 $skipped 个$staleMsg，耗时 {0:hh\:mm\:ss}" -f ((Get-Date) - $t0)) -ForegroundColor Green
}
finally {
    Pop-Location
}
