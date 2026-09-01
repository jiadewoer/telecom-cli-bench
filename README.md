# telecom-cli-bench

面向电信网络设备命令生成能力的本地大模型评测工具。项目通过统一的数据集、提示词策略、输出规范化和评分规则，对 Ollama 中的模型进行可复现的 Cisco/Huawei CLI 基准测试。

## 项目状态

当前版本已完成核心功能与完整评测，可作为阶段性正式版本交付。

- 122 条正式评测任务，另有 4 条 `demo_` 示例不参与评分
- 6 个模型 × 3 种提示词策略，共 18 个评测组合
- 2196 条健康原始输出已完成评分
- 43 项自动化测试全部通过
- 数据集自检、`ruff`、`mypy` 和报告生成均已通过

精确榜单和图表以 `results/leaderboard.csv` 及 `results/charts/` 中当前生成的文件为准，避免 README 与重新运行后的结果不一致。

## 评测模型

默认矩阵由 `configs/models.json` 定义，当前包含：

- `qwen2.5:1.5b`
- `qwen2.5:3b`
- `qwen2.5:7b`
- `qwen2.5-coder:7b`
- `llama3.1:8b`
- `deepseek-r1:8b`

Ollama 中安装的其他模型不会自动加入评测矩阵；如需新增模型，请先修改配置文件。

## 数据集概况

| 维度 | 分布 |
| --- | --- |
| 厂商 | Cisco 50，Huawei 72 |
| 难度 | L1 36，L2 59，L3 27 |
| 领域 | VLAN 25，接口 26，路由 24，ACL 13，诊断 24，MPLS 10 |

正式评测会自动排除 ID 以 `demo_` 开头的示例任务。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/)
- Windows PowerShell（运行完整矩阵脚本时推荐）

## 安装

在项目根目录创建并激活虚拟环境：

```powershell
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

该项目推荐直接使用 `uv pip`。由 `uv` 创建的虚拟环境不一定包含传统 `pip`，这不影响项目安装。

如果出现 hardlink 警告，通常只是缓存与目标目录位于不同文件系统导致的性能提示，不影响安装。需要隐藏该提示时可使用：

```powershell
uv pip install --link-mode=copy -e ".[dev]"
```

安装后验证命令行入口：

```powershell
tcb --help
```

## 准备 Ollama 模型

查看本地模型：

```powershell
ollama list
```

缺少模型时，例如：

```powershell
ollama pull qwen2.5:7b
```

运行评测前先检查 Ollama 服务和目标模型：

```powershell
tcb doctor --model qwen2.5:7b
```

默认 endpoint 为 `http://localhost:11434`。运行器默认直连 Ollama，并忽略 `HTTP_PROXY` 和 `HTTPS_PROXY`，避免本地请求被代理转发。

## 快速开始

先运行一个模型与提示词组合，确认完整链路：

```powershell
tcb run qwen2.5:7b --prompt zero_shot
tcb score
tcb inspect qwen2.5:7b --prompt zero_shot --n 5
```

注意：`inspect` 的数量参数是长选项 `--n`，不是 `-n`。省略 `--n` 时默认显示 5 条。

确认单组合正常后运行完整矩阵：

```powershell
.\scripts\run_matrix.ps1
```

矩阵结束后检查、评分并生成报告：

```powershell
tcb check-raw
tcb score
tcb report
```

`tcb score` 会重新评分 `results/raw/` 中所有健康输出，而不只是最近一次运行产生的文件。基础设施错误不会被伪装成模型错误参与评分。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `tcb validate` | 从任意当前目录运行数据集自检 |
| `tcb doctor --model MODEL` | 检查 Ollama endpoint 和模型可用性 |
| `tcb run MODEL --prompt PROMPT` | 运行一个模型与提示词组合 |
| `tcb check-raw` | 检查任务覆盖、基础设施状态和原始结果完整性 |
| `tcb score` | 评分全部健康原始输出 |
| `tcb inspect MODEL --prompt PROMPT --n 5` | 查看原始输出及规范化结果 |
| `tcb report` | 生成榜单与图表 |

## 输出目录

```text
results/
├── raw/                 # 各模型/提示词组合的原始 JSONL
├── scored/              # 评分结果
├── charts/              # 报告图表
└── leaderboard.csv      # 汇总榜单
```

原始结果文件名会对模型名称做安全转换。例如：

```text
raw__qwen2.5_7b__zero_shot.jsonl
```

## 验证项目

运行完整工程检查：

```powershell
pytest -v
python scripts\validate_dataset.py
ruff check .
mypy .
```

当前验收基线：

```text
43 passed
任务总数: 122（另有 4 条 demo 示例不参与评测）
[ OK ] 数据集自检通过
```

## 复现性说明

项目固定了评测数据、配置和推理参数，以降低运行间差异；但本地推理仍可能因 Ollama 版本、模型量化版本、硬件后端或推理实现而产生少量变化。因此，不应把固定 seed 理解为跨环境逐字一致。比较结果时请同时保留模型标签、Ollama 版本、endpoint 来源和原始输出。

## 提交最终 README

将本文件覆盖项目根目录的 `README.md` 后执行：

```powershell
cd D:\projects\telecom-cli-bench
git status
git add README.md
git commit -m "docs: 完善 README 并更新最终评测说明"
git push origin main
```

如果 `git status` 仍显示 `nothing to commit`，请确认下载的文件确实覆盖了 `D:\projects\telecom-cli-bench\README.md`。

## 许可证

许可证信息以仓库中的许可证文件为准。
