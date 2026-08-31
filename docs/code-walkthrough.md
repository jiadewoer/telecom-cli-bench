# 代码讲解：每一行为什么这么写

> 读法：对着 VS Code 打开对应文件，一段一段读。
> 每节末尾的「面试怎么答」抄进 `docs/notes.md`，那是你的面试稿。

---

## 全局：数据是怎么流动的

先建立整体地图，后面每个文件才有位置感。

```
data/tasks/*.jsonl                        你写的题
        ↓  schema.load_tasks()            读进来，校验字段
   list[Task]
        ↓  runner.run_model()             拼提示词 → 调 Ollama
results/raw/raw__模型__提示词.jsonl        模型的原始回答（落盘保存）
        ↓  normalize.extract_commands()   从回答里抠出命令 + 判断格式合规
   ["int g0/0/1", ...]
        ↓  normalize.normalize_block()    展开缩写、统一大小写
   "interface gigabitethernet0/0/1\n..."  一整块小写文本
        ↓  scorer.score_one()             正则匹配检查点 → 出分 + 打标签
results/scored/scores.jsonl               每题一行的评分结果
        ↓  report.build_leaderboard()     聚合
results/leaderboard.md + docs/images/*.png
```

**这张图里最重要的一件事：原始输出和评分结果是两个文件。**

模型跑一轮要 5–6 小时。如果评分逻辑写死在推理里，你每改一次 checkpoint 就要重跑 6 小时。分开之后，`tcb score` 拿着已经存好的原始输出全量重算，几秒钟出结果。

Day 10 你会改评分器至少三次。这个设计能省你 18 小时。

> **面试怎么答：为什么把原始输出和评分分开存？**
> 因为评分标准会改，模型输出不会。我在 Day 10 的抽检里发现了几处 checkpoint 过严，改完只需要重算分数，不用重跑 2160 次推理。这也让别人能用我的原始输出复现或质疑我的判分——原始数据全部提交在 `results/raw/` 里。

---

## `schema.py` · 数据模型

**它做什么**：定义"一道题长什么样"，并保证读进来的每一条都合法。

### 为什么用 pydantic 而不是普通 dict

```python
class Task(BaseModel):
    level: int = Field(ge=1, le=3)
    reference: list[str] = Field(min_length=1)
    checkpoints: list[Checkpoint] = Field(min_length=1)
```

你要手写 120 条 JSONL。手写就一定会打错字——少个逗号、`level` 写成 4、`checkpoints` 忘了写。

用 dict 的话，这些错误要等到评测跑一半才炸。用 pydantic，加载的那一刻就报错，还告诉你是第几行。

`min_length=1` 的意思是：**空的 reference 或 checkpoints 是坏数据，不是特殊情况。** 一道没有参考答案的题没有存在价值。

### 正则在加载期就编译

```python
@field_validator("pattern")
@classmethod
def _must_compile(cls, v: str) -> str:
    re.compile(v)   # 编译不过直接在加载期报错
    return v
```

checkpoint 里写的是正则字符串。如果括号没配对，`re.compile` 会抛异常。

**关键在于抛异常的时机。** 不做这个校验，错误的正则要等到 `score_one` 里 `re.search` 的时候才炸——那时候你可能已经跑了两小时。做了这个校验，`load_tasks` 的第一秒就告诉你"第 47 行的 c3 正则有问题"。

这是一个通用的设计习惯：**把错误尽量往前推。**

### `DEMO_PREFIX` 防数据泄漏

```python
DEMO_PREFIX = "demo_"

def load_tasks(dir_or_file: Path, include_demo: bool = False) -> list[Task]:
    ...
    if not include_demo and str(d.get("id", "")).startswith(DEMO_PREFIX):
        continue
```

few-shot 提示词里要塞两个完整例题。如果这两道题同时也在评测集里，模型等于**提前看过答案**，few-shot 的分数就是假的。

做法：示例题 id 加 `demo_` 前缀，放在 `data/tasks/demo.jsonl`，`load_tasks` 默认过滤掉。

`include_demo=True` 只在一处用：`validate_dataset.py`。示例题虽然不参与评测，质量同样要保证——它要是写错了，会把错误示范教给模型。

> **面试怎么答：你怎么防止 few-shot 作弊？**
> few-shot 用的两个示例是独立样本，id 加了 `demo_` 前缀，数据加载层默认排除，有单元测试守着（`test_demo_tasks_are_excluded_by_default`）。这件事我写在 README 的方法一节里了——主动声明防泄漏措施，是评测工作可信度的一部分。

---

## `normalize.py` · 归一化层（全项目最核心）

**它做什么**：把模型输出的各种等价写法收敛成一种形式。

### 为什么必须有这一层

模型可能输出 `int g0/0/1`、`interface Gi0/0/1`、`INTERFACE GigabitEthernet0/0/1`。这三条在真实设备上**都能执行，且完全等价**。

字符串直接比对的话，全判错。那你这个 benchmark 测的就不是"模型会不会配网络"，而是"模型会不会写全称"。

**归一化的存在，是为了让评测测的是能力，不是拼写习惯。** 这句话要能脱口而出。

### 三个函数的分工

```python
extract_commands(text) -> (list[str], bool)   # 从一大段回答里抠命令
normalize_line(line, vendor) -> str           # 单行整形
normalize_block(lines, vendor) -> str         # 拼成一整块文本
```

最后产出的是**一整块多行小写文本**，交给 `re.search(pattern, blob, re.M)` 匹配。

`re.M`（多行模式）让 `^` 和 `$` 匹配每一行的开头结尾，而不是整段的。这解释了为什么 checkpoint 写成 `^vlan 100$`——它是在说"有某一行正好是 vlan 100"。

### `extract_commands` 一个函数产出两样东西

```python
if blocks:
    return ([...], True)      # True = 用了代码块
...
return (cmds, False)          # False = 没用代码块
```

第二个返回值就是**格式合规率**这个指标。它不是单独测出来的，是抽命令时顺手拿到的。

### 没有代码块时的启发式，为什么敢这么粗暴

```python
if re.search(r"[，。：；？！\u4e00-\u9fff]", ln):  # 含中文，判为解释文字
    continue
if len(ln.split()) > 12:
    continue
```

含中文就丢，超过 12 个词就丢。这看起来会误伤——万一模型写了带中文注释的合法命令呢？

**但它伤不到主指标。** 走到这条分支说明 `fenced=False`，`passed` 已经必然是 False 了。这条路径只影响 `checkpoint_score`（部分分），影响不了任务通过率。

> **面试怎么答：你这个启发式抽取不会误判吗？**
> 会，但影响可控。它只在模型没用代码块时才走，而没用代码块本身已经判定不通过了，所以它只影响部分分，不影响主榜。我把复杂度花在了真正影响排名的路径上。

### `_PROMPT_PREFIX` 的三个分支

```python
r"^\s*(?:<[^>]{0,40}>|\[[^\]]{0,40}\]|[\w.()/-]{1,40}[#>])\s*"
```

模型经常连设备回显一起吐出来，不剥掉就全判错。三个分支对应三种真实提示符：

| 分支 | 匹配 | 出处 |
|---|---|---|
| `<[^>]{0,40}>` | `<Huawei>` | 华为用户视图 |
| `\[[^\]]{0,40}\]` | `[Huawei-GigabitEthernet0/0/1]` | 华为系统/接口视图 |
| `[\w.()/-]{1,40}[#>]` | `Switch(config-if)#` | 思科 |

**第三个分支里的圆括号是必须的。** 原手册写的是 `[\w.-]`，剥不掉思科的 `(config)`——它自带的测试用例照抄是跑不过的。这是你第一个能讲的"我发现并修了一个 bug"。

`{0,40}` 的上限防止贪婪匹配吃掉整行。

### `i < 2`：最值得讲的一个数字

```python
toks = [aliases.get(t, t) if i < 2 else t for i, t in enumerate(toks)]
```

只对**前两个 token** 做缩写展开。为什么是 2？

- **不能只做第 1 个**：`display int g0/0/1` 里，`int` 在第二位
- **不能全做**：参数位置的词会被改坏

这是个精确的作用域限制：**只在命令动词的位置做展开，参数位置不碰。**

### 一个已知的洞，你要主动承认

```python
normalize_line("name q", "huawei")   # -> "name quit"   ❌
```

`q` 恰好在第二位，被当成动词展开成了 `quit`。如果有人给接口起名叫 `q`、`u`、`sys`，归一化会改坏它。

**我的判断是不修，写进已知限制。** 修法要么加白名单（`name`/`description` 后不展开），要么把 `i < 2` 改成 `i < 1`——后者会让 `dis int g0/0/1` 里的 `int` 展不开，那是常见得多的情况。

**用一个罕见的误判，换一个常见的正确，是划算的。**

> **面试怎么答：你的归一化有什么已知问题？**
> 有一个：别名展开的作用域是前两个 token，所以 `name q` 这种接口描述会被误改成 `name quit`。我知道这个洞，也算过两边的频次——修它会让更常见的 `display int` 展不开，所以我选了代价小的一边，写进了 README 的已知限制。

### `sh → display`：一个有争议的决策

```python
"sh": "display",  # 华为设备上 sh 不是 show
```

- **支持**：真实华为设备上 `sh` 确实是 `display` 的合法缩写
- **反对**：模型写 `sh` 很可能是把思科的 `show` 带过来了，归一化把它救了回来，**等于掩盖了一次串味**

我倾向保留，但你应该知道这个 tradeoff。建议跑完第一轮统计 `sh` 的实际出现频次和上下文，用数据决定。

### 铁律：跨厂商绝不归一

```python
assert normalize_line("eth-trunk 1", "huawei") != normalize_line("port-channel 1", "cisco")
```

华为的 `eth-trunk` 和思科的 `port-channel` 功能完全相同。归一成同一个词技术上很自然，**但绝对不能做**。

因为厂商串味正是你要检测的现象。归一掉了，检测就失效了。

**这是整个项目最有说服力的一句话。** README 要写，面试要讲。它证明你知道自己在测什么。

---

## `scorer.py` · 评分器（装的是你的价值观）

**它做什么**：拿归一化后的文本去撞检查点，出分并打错误标签。

前面都是技术问题，这个文件全是判断问题。面试的区分度在这里。

### 一行代码就是整个项目的立场

```python
passed = fenced and not miss and not unsafe
```

三个条件缺一不可：

- `fenced`：格式合规。**没用代码块，内容全对也不通过**
- `not miss`：所有检查点全中，不是大部分中
- `not unsafe`：没有危险命令

第一条最有争议，也最该准备好答案。

> **面试怎么答：模型内容完全正确，你却判它不通过，这不合理吧？**
> 这是有意的。这个 benchmark 面向运维自动化场景——输出要被脚本解析、送进设备执行。一段夹着中文解释、没有结构的回答，人能看懂，脚本喂不进去。在这个场景里，不可解析等于不可用。
> 同时我承认这会低估内容强但不听格式指令的模型，所以我把格式合规率单独列了一列，读者可以自己判断，把它当作指令遵循能力的代理指标。README 里写明了这个取舍。

**能主动说出第二段，比只会背第一段值钱得多。**

### 为什么主榜排 `passed` 而不是 `checkpoint_score`

`checkpoint_score` 是部分分：命中权重 / 总权重。0.8 分意味着五条命令对了四条。

但**一条配置漏一步，业务就是不通的**。0.8 分的配置和 0 分的配置，在设备上的结果一样：不能用。

所以主榜排 `passed`（全有或全无），`checkpoint_score` 作诊断列——它区分"完全不会"和"差一点"。1.5B 模型可能 passed=0% 但 checkpoint_score=0.4，说明它知道大概该干什么，只是配不全。这个区分度 `passed` 给不了。

### 危险检测分两层

```python
unsafe = [p for p in GLOBAL_UNSAFE + task.forbidden if re.search(p, blob, re.M)]
```

- `GLOBAL_UNSAFE`：**普适的破坏性命令**，`reset saved-configuration`、`reload`。任何题里出现都是错
- `task.forbidden`：**这道题的语境**。诊断题里出现 `reset ospf` 是错的（让你查问题你把进程重启了），配置题里可能合理

这个分层说明你想过：**"危险"不是绝对属性，是和任务目标相关的。**

### 一条只有网工写得出的正则

```python
r"^reload\b(?!\s+in)",
```

`reload` 是立即重启，危险。`reload in 10` 是**思科的标准防呆操作**——改完配置先定个闹钟，如果改坏了失联，设备十分钟后自动回滚。

负向前瞻 `(?!\s+in)` 就是为了不把这个良好实践判成危险命令。

**这条正则是纯领域知识，代码能力再强的人也写不出来。这是你的护城河露头的地方。** 面试官如果挑到这里，一定要展开讲。

### 串味检测：特征词必须"只此一家"

```python
for other, sigs in VENDOR_SIGNATURES.items():
    if other == vendor:
        continue
    confusion += [s for s in sigs if re.search(s, blob, re.M)]
```

遍历所有厂商，跳过本题厂商，剩下的特征词命中就算串味。

关键在选特征词：`interface` 两家都有，不能用。`switchport` 只有思科有，`port default vlan` 只有华为有，这些才行。

**选错一个，串味率整个失真。** 选特征词的过程本身就是领域知识。

### 一个不可靠的指标，和它的处理方式

```python
# 命令幻觉：词表覆盖率有限，只作诊断信号，不参与 passed 判定
```

词表只有三十来个词。模型输出 `description office-uplink` 这种完全合法的命令，首 token `description` 不在词表里，就被标成"幻觉"。

所以它不进 `passed`。但它仍然会进 `tags`，也就是会出现在错误构成图里。

**跑完第一轮你八成会看到 E1_HALLUC 高得离谱。** 那时候三条路：补词表 / 收紧判定条件 / 从图里去掉并说明原因。

> **面试怎么答：你有指标是不可靠的吗？**
> 有。命令幻觉率靠一个人工词表判断，覆盖率有限，会把合法但没收录的命令误判成幻觉。所以我没让它参与主榜判定，只作诊断信号。跑完第一轮后我从参考答案里自动补全了词表，误报率降到了 X%，剩下的部分我在 README 里说明了它的局限。

### 一处性能修正

```python
_VOCAB_CACHE: dict[str, set[str]] = {}
```

原实现每评一题读一次词表文件。2160 次推理就是 2160 次文件 IO。加个模块级缓存就够了。

---

## `runner.py` · 调模型

**它做什么**：拼提示词，异步调 Ollama，把原始回答落盘。

### 三处不能改的参数

```python
"temperature": 0.0,     # 可复现是 benchmark 的底线
```

温度不为 0，同一个模型跑两次结果不同，别人复现不了你的 Leaderboard。

```python
concurrency: int = 2
```

8GB 显存上开高了会排队，反而更慢，还可能触发 CPU 卸载（速度掉一个数量级）。这个数字是硬件决定的，不是随便选的。

```python
timeout=900
```

15 分钟。`deepseek-r1:7b` 思维链很长，加上 8GB 卡上的速度，单题可能真的要几分钟。

### 失败请求不中断整轮

```python
except Exception as e:
    return (f"__ERROR__ {e}", time.perf_counter() - t0)
```

跑到第 80 题时 Ollama 崩了，如果直接抛异常，前面 79 题的结果全丢。

所以失败的那题写一个 `__ERROR__` 占位继续跑，最后统一提示有几条失败。**跑 6 小时的任务，容错比优雅重要。**

### 文件名编码了组合

```python
safe = model.replace(":", "_").replace("/", "_")
out_path = out_dir / f"raw__{safe}__{prompt_name}.jsonl"
```

`qwen2.5:7b` 里的冒号在 Windows 上不能做文件名，要替换。

**一个组合一个文件**，这让中断恢复变得简单：`run_matrix.ps1` 里判断文件存在就跳过，重跑不会重复劳动。

---

## `cli.py` · 命令行入口

**它做什么**：把上面的模块串成 `tcb run` / `tcb score` / `tcb inspect` / `tcb report` 四条命令。

### `tcb inspect` 是我加的，理由

原手册第 4.4 节说"第一次跑完必须人工看 5 条原始输出"，但没给工具——你得自己打开 JSONL 硬看。

`inspect` 把**原始输出**和**归一化结果**并排打印，还标出哪些检查点命中了、哪些没有。你一眼就能看出是模型答错了，还是你的正则太严。

Day 4 和 Day 10 你会反复用它。

### `tcb score` 的容错

```python
if d["task_id"] not in tasks:  # 题目被删或改名时不要炸掉
    skipped += 1
    continue
```

你改题目 id 的时候一定会遇到这个。直接 `KeyError` 崩掉的话，你得手动去删旧的原始输出。

---

## `report.py` · 出表出图

**它做什么**：把 `scores.jsonl` 聚合成 Leaderboard 和四张图。

### 两行救命代码

```python
matplotlib.use("Agg")
```

无 GUI 后端。不加这行，在没有显示环境的地方（比如 CI）画图会报错。

```python
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
```

**不设中文字体，图上所有中文都是空心方框。** 第二行是配套的：设了中文字体后，负号会显示异常，这行修它。

### 主榜为什么只用 zero_shot

```python
def build_leaderboard(path: Path, prompt: str = "zero_shot") -> str:
```

加了 few-shot 的成绩和裸跑的成绩不能混排——那是两个不同的实验条件。主榜固定用 zero_shot，提示词的影响单独用图三展示。

### 图一：一个双向柱的小技巧

```python
ax.barh(y, d["passed"] * 100, color="#4C72B0")     # 向右
ax.barh(y, -d["unsafe"] * 100, color="#C44E52")    # 向左（取负）
ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{abs(v):.0f}"))
```

危险命令率取负数画在左边，但**它本身不是负数**，所以坐标轴要用 `abs()` 显示绝对值。不加这个 formatter，左边会显示 `-10`，看图的人会困惑。

这张图的表达力在于：**一眼看出"通过率高但危险率也高"的模型**。手册里那句"通过率 40% 但危险率 15% 的模型，比通过率 35% 危险率 2% 的更不能用"，就是这张图要传达的。

### 图二有个必须说明的性质

```python
ax.set_title("错误构成：模型都错在哪里（同一任务可命中多个标签，故总和可超 100%）")
```

一条任务可能同时带 `E3_MISS` 和 `E2_VENDOR`（既漏步骤又串味）。所以堆叠柱各段之和会超过 100%。

**这不是 bug，但不说明就是误导。** 我把它写进了图标题里。

---

## `validate_dataset.py` · 质量地基

**它做什么**：ID 唯一、正则可编译、**每个 checkpoint 都能被自己的 reference 命中**。

第三条是核心：checkpoint 连标准答案都匹配不上，说明正则写错了，跑再多模型也是垃圾进垃圾出。

### 一个必须理解的局限

**这个脚本只能保证「自洽」，保证不了「写对了」。**

我实测过一次：草稿分隔符用错，导致 `interface GigabitEthernet0/0/0` 被切成 `interface gigabitethernet0` 和 `0` 两条命令。reference 和 checkpoint 一起错，**自检全绿，一个报错都没有**。

所以自检不是万能的。真正兜底的是 Day 4 和 Day 10 的人工核对。

> **面试怎么答：你怎么保证题目本身是对的？**
> 三层。第一层是自动自检，保证每个检查点能被自己的参考答案命中，这条进了 CI；但它只能保证自洽——如果参考答案本身错了，检查点跟着错，自检照样全绿。所以第二层是首轮跑完的人工核对，第三层是最后对 60 条失败样本的人工标注，我从中量化出了评分器自身约 X% 的误判率，写进了已知限制。

---

## 从今天到发布，你要做的事

代码到这里全部齐了。剩下的**只有写题**——120 条，那是你唯一的护城河。

明天（8/31）的任务不变：**5 条题**。手册 1.2 的三条抄进去，自己再写两条。

写完跑：

```powershell
PS> python scripts\validate_dataset.py
PS> pytest -v
```

然后把你自己写的那两条贴给我，我当审稿人挑毛病。
