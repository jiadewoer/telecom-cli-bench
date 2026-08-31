# 全矩阵：6 模型 x 3 提示词 = 18 个组合
# 中断了不要紧：原始输出按组合分文件存，已完成的文件还在，
# 重跑时下面的 if 会自动跳过已有结果。

$models = (Get-Content configs\models.json -Raw -Encoding UTF8 | ConvertFrom-Json).model
$prompts = @("zero_shot", "few_shot", "syntax_hint")
$t0 = Get-Date

foreach ($m in $models) {
    foreach ($p in $prompts) {
        $safe = $m -replace ':', '_' -replace '/', '_'
        $out  = "results\raw\raw__${safe}__${p}.jsonl"
        if (Test-Path $out) {
            Write-Host "--- 跳过（已存在）$m / $p" -ForegroundColor DarkGray
            continue
        }
        Write-Host "=== $m / $p ===" -ForegroundColor Cyan
        tcb run $m --prompt $p --concurrency 2
    }
    # 换模型后让 Ollama 把上一个卸载干净，避免显存打架
    Start-Sleep -Seconds 10
}

tcb score
Write-Host ("全矩阵完成，耗时 {0:hh\:mm\:ss}" -f ((Get-Date) - $t0)) -ForegroundColor Green
