# telecom-cli-bench

> **大模型写得对网络设备配置命令吗？** 面向华为 VRP / 思科 IOS 的中文 CLI 评测基准。

⚠️ 施工中。当前为竖切骨架版本，数据集仅 4 条种子任务。
完整发布计划见 `START_HERE.md`。

## 快速开始

```
uv venv && .\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
python scripts\validate_dataset.py
pytest -v
tcb run qwen2.5:7b --prompt zero_shot
tcb score
tcb inspect qwen2.5:7b
```

## License

数据集 CC-BY-4.0，代码 MIT
