# 全矩阵：6 模型 x 3 提示词 = 18 个组合
#
# 中断了不要紧：原始输出按组合分文件存，已完成的文件还在，
# 重跑时下面的 if 会自动跳过已有结果。
#
# 本文件必须以 UTF-8 with BOM 保存。PowerShell 5.1 读 .ps1 时按系统 ANSI
# （中文机器上是 GBK）解码，没有 BOM 的话中文注释和 Write-Host 全是乱码。

$ErrorActionPreference = "Stop"

# PowerShell 5.1 的 Get-Content 默认按系统 ANSI 读文件，读 UTF-8 的 models.json
# 会乱码并导致 ConvertFrom-Json 解析失败。必须显式指定编码。
$json = Get-Content configs\models.json -Raw -Encoding UTF8 | ConvertFrom-Json
$models = @($json | ForEach-Object { $_.model })

# 护栏。上一版没有这几行，$models 取空时 foreach 直接不进循环，
# 脚本一个组合都没跑，最后还打印了一句绿色的「全矩阵完成，耗时 00:00:00」。
# 一个什么都没干却报告成功的脚本，比一个崩掉的脚本危险得多。
if ($models.Count -eq 0) {
    throw "models.json 解析出 0 个模型。检查文件编码与格式：Get-Content configs\models.json -Raw -Encoding UTF8 | ConvertFrom-Json"
}
Write-Host "待跑模型 $($models.Count) 个: $($models -join ', ')" -ForegroundColor Yellow

$prompts = @("zero_shot", "few_shot", "syntax_hint")

# 当前数据集的题数。用来判断已有的 raw 文件是不是旧数据集跑出来的。
#
# 起因：下面的 Test-Path 跳过逻辑只看文件在不在，不看它跑的是哪一版数据集。
# 数据集加了两道 MPLS 对照题之后，18 个组合全部命中「已存在」被跳过，
# 新题永远拿不到数据，而 tcb score 会静默地把它们漏掉、照常出报表。
# 这和 D12 记的「静默成功的脚本比崩掉的脚本危险」是同一个坑的第二次出现。
$taskCount = [int](python -c "import json,glob;print(sum(1 for p in glob.glob('data/tasks/*.jsonl') for l in open(p,encoding='utf-8') if l.strip() and not json.loads(l)['id'].startswith('demo_')))")
if ($LASTEXITCODE -ne 0) { throw "统计题数失败，检查 data/tasks/ 是否完整" }
Write-Host "当前数据集 $taskCount 道题（不含 demo）" -ForegroundColor Yellow
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
            # 只有题数对得上才跳过。对不上说明数据集变过，这份结果是旧的。
            $have = (Get-Content $out | Where-Object { $_.Trim() }).Count
            if ($have -eq $taskCount) {
                Write-Host "--- 跳过（已存在，$have 条）$m / $p" -ForegroundColor DarkGray
                $skipped++
                continue
            }
            Write-Host "--- 重跑（已有 $have 条，数据集现有 $taskCount 条）$m / $p" -ForegroundColor Yellow
            $stale++
        }
        Write-Host "=== $m / $p ===" -ForegroundColor Cyan
        tcb run $m --prompt $p
        if ($LASTEXITCODE -ne 0) { throw "tcb run 失败: $m / $p" }
        $ran++
        $didRun = $true
    }
    # 换模型后让 Ollama 把上一个卸载干净，避免显存打架。
    # 全部跳过的模型不用等。
    if ($didRun) { Start-Sleep -Seconds 10 }
}

if ($ran -eq 0) {
    Write-Host "没有新组合需要跑（$skipped 个已存在）。要重跑请先删掉 results\raw\ 下对应文件。" -ForegroundColor Yellow
}

tcb score
$staleMsg = if ($stale -gt 0) { "，其中 $stale 个是题数不匹配触发的重跑" } else { "" }
Write-Host ("全矩阵完成：新跑 $ran 个组合，跳过 $skipped 个$staleMsg，耗时 {0:hh\:mm\:ss}" -f ((Get-Date) - $t0)) -ForegroundColor Green
