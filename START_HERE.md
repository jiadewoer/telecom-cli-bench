# 从这里开始

当前仓库已经从最初的「4 条题竖切骨架」演进为 **122 条正式任务 + 4 条 few-shot demo + 完整管线**。
这份文档保留“先跑通一条完整链路再扩题”的使用顺序，但命令和完整性规则以当前实现为准。

---

## 第一小时：让管线跑起来

```powershell
# 1. 解压到项目目录
PS> cd D:\projects\telecom-cli-bench

# 2. 建虚拟环境装依赖
PS> uv venv
PS> .\.venv\Scripts\Activate.ps1
PS> uv pip install -e ".[dev]"

# 3. 数据集自检（不需要 Ollama，应该立刻全绿）
PS> python scripts\validate_dataset.py

# 4. 单元测试（不需要 Ollama）
PS> pytest -v

# 5. 确认 Ollama 在跑，且模型已拉
PS> ollama list
PS> ollama pull qwen2.5:7b     # 如果还没有

# 6. 跑通第一个组合（当前为 122 条正式任务）
PS> tcb run qwen2.5:7b --prompt zero_shot
PS> tcb score

# 7. ⭐ 最重要的一步：人工核对
PS> tcb inspect qwen2.5:7b --prompt zero_shot --n 5
```

第 7 步会把原始输出和归一化结果并排打印出来。**逐条看这三件事：**

1. 模型有没有用代码块 → 没有就调提示词，**不要调评分器**
2. 归一化后的命令对不对 → 有漏网的缩写就补进 `normalize.py` 的 `_TOKEN_ALIASES`
3. **有没有把对的答案判成错的** → 这是最危险的情况，当场改 checkpoint

走完这七步，你就有了对整条链路的手感。这时候再去写第 5 条题，你会知道 checkpoint 该写多松。

---

## 第一周：只做一件事——写题

**在管线跑通之后、开始批量写题之前，先接受这个分工：**

- 想题目、定难度、判断哪条命令真的会用到 —— 只有你能做
- 转 JSON、写正则、调格式 —— 机械劳动，可以让模型起草，你逐条核对

所以别一上来就写 JSON。开一个纯文本文件 `data/tasks/_drafts.txt`，只写两栏：

```
instruction: 把 GigabitEthernet0/0/1 的 IP 配置为 192.168.10.1/24
reference:   system-view / interface GigabitEthernet0/0/1 / ip address 192.168.10.1 255.255.255.0
---
instruction: ...
reference:   ...
```

**按领域批量写**，一次专注写 15 道 VLAN 题，比东一条西一条快一倍。

攒够 40~50 条再统一转成 JSONL 格式。转格式和写正则是机械劳动，
想题目、判断哪条命令真的会用到，才是只有你能做的部分。

### ⚠️ 一个必须理解的局限

`validate_dataset.py` 只能保证**自洽**，保证不了**写对了**。

因为 checkpoint 是从 reference 生成的，如果 reference 本身错了，
两边一起错，自检照样全绿。实测踩过两次：一次是分隔符用错把接口名切碎，
一次是批量生成时检查点编号错位一位。**两次自检都是全绿的。**

所以真正兜底的是首轮跑完的人工核对（`tcb inspect`）和最后的人工标注。
详见 `docs/notes.md` 的 D10。

---

## 已经替你处理掉的三个坑

| 坑 | 手册里的样子 | 这里的处理 |
|---|---|---|
| 提示符正则剥不掉思科的 `Switch(config)#` | `[\w.-]{1,30}[#>]` 不含圆括号，手册自带的测试用例会红 | 改成 `[\w.()/-]{1,40}[#>]`，并加了测试 |
| `validate_dataset.py` 把 `import re` 写在 for 循环里 | 靠循环先执行过一次才能用，改动时容易 `NameError` | 提到文件顶层 |
| few-shot 防泄漏要 Day 5 才补 | 事后再隔离容易漏 | `load_tasks` 一开始就过滤 `demo_` 前缀，示例题已单独放 `demo.jsonl` |

另外两处小改动：

- `load_vocab` 加了缓存。原实现每评一题读一次盘，2160 次推理就是 2160 次文件 IO。
- `tcb score` 遇到原始输出里已不存在的 `task_id` 会跳过并提示，而不是直接 `KeyError` 崩掉——你改题目 id 的时候一定会遇到。

---

## 一个需要你自己拿主意的设计问题

`unknown_verbs`（命令幻觉率）这个指标，词表只有二三十个词。模型输出
`description office-uplink` 这种完全合法的命令时，首 token `description`
不在词表里，就会被标成「命令幻觉」。

所以这里把它定位成**纯诊断信号，不参与 `passed` 判定**。
等你跑完第一轮，用下面这条命令从 reference 里把词表补全：

```powershell
PS> python -c "from telecom_cli_bench.schema import load_tasks; from pathlib import Path; import collections; ts=load_tasks(Path('data/tasks'), include_demo=True); c=collections.Counter((t.vendor.value, r.split()[0].lower()) for t in ts for r in t.reference); [print(k) for k in sorted(c)]"
```

输出里凡是词表没有的，人工判断后加进 `data/vocab/*_verbs.txt`。

---

## 当前已经完成

`report.py`、全矩阵脚本、模型配置、CI 与 README 都已经落地。当前最重要的工程护栏是：raw 必须精确覆盖当前任务集且不能含基础设施错误；`run_matrix.ps1` 会通过 `tcb check-raw` 验证已有结果，失败就重跑，`tcb score` 也会再次兜底。
