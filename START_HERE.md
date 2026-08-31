# 从这里开始

这个骨架是「竖切」版本：**4 条题 + 完整管线**，不是手册里的「30 条题再建管线」。
目的是让你在动手写第 5 条题之前，就已经亲眼看过一条题从写下来到出分的全过程。

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

# 6. 跑通第一个组合（只有 4 条题，一分钟内出结果）
PS> tcb run qwen2.5:7b --prompt zero_shot
PS> tcb score

# 7. ⭐ 最重要的一步：人工核对
PS> tcb inspect qwen2.5:7b
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

### 草稿格式（这是你唯一需要打的字）

在 `data/tasks/_drafts.txt` 里这样写：

```
# hw_vlan_004 | vlan | L2
context: 华为 S5700，当前处于用户视图。
task: 创建 VLAN 200 命名为 guest，并把 GE0/0/5 配置为 access 口划入。
ref:
  system-view
  vlan 200
  name guest
  quit
  interface GigabitEthernet0/0/5
  port link-type access
  port default vlan 200
```

命令少的时候可以写成一行：`ref: system-view ; vlan 200 ; name guest`
**分隔符是分号，不能用斜杠**——`GigabitEthernet0/0/0` 会被切碎。

任务头里的 id 可以省略（自动按 `hw_vlan_007` 这样编号），
但厂商靠 `cs_` 前缀识别，写思科题时要么带前缀，要么加 `--vendor cisco`。

### 转成 JSONL

```powershell
# 先干跑，看生成的正则对不对
PS> python scripts\draft_to_jsonl.py data\tasks\_drafts.txt --dry-run

# 确认无误再落盘
PS> python scripts\draft_to_jsonl.py data\tasks\_drafts.txt --out data\tasks\huawei.jsonl --append
PS> python scripts\validate_dataset.py
```

脚本自动做四件事：归一化 reference 并逐条转成锚定正则、掩码放宽成
`(255\.255\.255\.0|24)`、跳过 `quit`/`exit` 这类纯导航命令不生成检查点、
按厂商补一组跨厂商 forbidden。

**生成之后你必须做两件事**（脚本做不了）：

1. **给关键步骤加 weight。** 全部生成为 1.0，"配 IP"这种核心步骤应该调到 1.5 或 2.0。
2. **判断正则是否过严。** 自动生成的是全等匹配，合法变体会被误杀。
   比如 `^ospf 1 router-id 1\.1\.1\.1$` 就挡掉了"先 `ospf 1` 再 `router-id 1.1.1.1`"
   这种同样正确的两行写法。

### ⚠️ 一个必须理解的局限

`validate_dataset.py` 只能保证**自洽**，保证不了**写对了**。

因为 checkpoint 是从 reference 生成的，如果 reference 本身错了，
两边一起错，自检照样全绿。我实测过一次：用斜杠当分隔符导致
`interface GigabitEthernet0/0/0` 被切成 `interface gigabitethernet0` 和 `0`
两条命令，自检没有任何报错。

所以 `draft_to_jsonl.py` 额外加了启发式告警（残缺检查点、命令里混入分隔符、
超过 10 个 token 的命令）。**看到 `[WARN]` 一定要停下来核对，
它抓的正是自检抓不到的那一类错误。**

写题时守住六条原则（详见手册 1.2）：

1. checkpoint 必须能被 reference 命中 —— `validate_dataset.py` 会强制这一点
2. checkpoint 要容忍合法变体 —— 掩码 `255.255.255.0` 和 `24` 都对
3. weight 给关键步骤加权
4. forbidden 放两类：破坏性命令、跨厂商语法
5. **判据：你能不能用三条以内的正则说清楚「对」的标准**，说不清就换题
6. L3 题的 instruction 里要写明输出数量

**每写 10 条跑一次 `python scripts\validate_dataset.py`。** 别攒到最后。

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

## 还没写的部分（按手册进度补）

- `src/telecom_cli_bench/report.py` —— Day 8 出图与 Leaderboard，`tcb report` 已经预留了调用
- `scripts/run_matrix.ps1` —— Day 6 批量跑
- `configs/models.json` —— Day 6
- `.github/workflows/ci.yml` —— Day 9
- `README.md` —— Day 9

不用提前写。**现在唯一该做的事是写题。**
