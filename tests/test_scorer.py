from telecom_cli_bench.schema import Checkpoint, Task
from telecom_cli_bench.scorer import score_one

TASK = Task(
    id="t1",
    vendor="huawei",
    domain="vlan",
    level=2,
    instruction="创建 VLAN 100 并把 GE0/0/2 划入",
    reference=[
        "system-view", "vlan 100", "interface GigabitEthernet0/0/2",
        "port link-type access", "port default vlan 100",
    ],
    checkpoints=[
        Checkpoint(id="c1", pattern=r"^vlan 100$"),
        Checkpoint(id="c2", pattern=r"^interface gigabitethernet0/0/2$"),
        Checkpoint(id="c3", pattern=r"^port default vlan 100$", weight=2.0),
    ],
    forbidden=[r"switchport"],
)


def _out(body: str) -> str:
    return f"配置如下：\n```\n{body}\n```"


def test_perfect_answer_passes():
    s = score_one(TASK, "m", "zero", _out(
        "sys\nvlan 100\nint g0/0/2\nport link-type access\nport default vlan 100"))
    assert s.passed and s.checkpoint_score == 1.0 and not s.unsafe


def test_partial_credit():
    s = score_one(TASK, "m", "zero", _out("sys\nvlan 100"))
    assert not s.passed
    assert 0.2 < s.checkpoint_score < 0.3   # 命中 1.0 / 总权重 4.0
    assert "E3_MISS" in s.tags


def test_vendor_confusion_detected():
    """串味要被识别为串味，且**只**被识别为串味。

    这条断言原来写的是 `assert "E7_UNSAFE" in s.tags`，注释是
    「switchport 同时在本题 forbidden 里」。它是绿的，所以三个月没人怀疑过
    unsafe 的定义——**一条测试把一个错误的行为固化成了「预期行为」**。

    真正的问题在 scorer 里：forbidden 装的是跨厂商特征词，被并进了 unsafe，
    于是华为题写 switchport 会被报成「输出了破坏性命令」。见 docs/notes.md D17。

    教训：测试断言的是「代码现在这么做」还是「代码应该这么做」，
    写的时候看不出区别，只有在改设计时才会暴露。
    """
    s = score_one(TASK, "m", "zero", _out(
        "vlan 100\ninterface gigabitethernet0/0/2\nswitchport access vlan 100"))
    assert "E2_VENDOR" in s.tags
    assert "E7_UNSAFE" not in s.tags, "串味不是危险命令"
    assert not s.passed


def test_no_fence_fails_format():
    s = score_one(TASK, "m", "zero",
                  "vlan 100\ninterface gigabitethernet0/0/2\nport default vlan 100")
    assert not s.format_ok and "E0_FORMAT" in s.tags
    assert not s.passed              # 内容全对，但格式不合规也不算通过


def test_global_unsafe_command():
    s = score_one(TASK, "m", "zero", _out("reset saved-configuration\nvlan 100"))
    assert "E7_UNSAFE" in s.tags and not s.passed


def test_reasoning_model_output_is_scored():
    s = score_one(TASK, "m", "zero", "<think>先想想</think>\n```\n" +
                  "vlan 100\ninterface gigabitethernet0/0/2\nport default vlan 100\n```")
    assert s.passed


def test_truly_empty_output_is_tagged():
    """归一化后什么都不剩才算 E8_EMPTY。

    判定依据是归一化后的 blob，不是原始 commands——
    模型可能输出注释行，commands 非空但归一化后为空。
    """
    s = score_one(TASK, "m", "zero", _out("! 这里本该是配置\n! 但我没想好"))
    assert "E8_EMPTY" in s.tags
    assert not s.passed
    assert s.checkpoint_score == 0.0


def test_bare_device_prompt_becomes_unknown_verb():
    """裸设备提示符不再被静默丢弃，而是留下来暴露成命令幻觉。

    这是一个有意的取舍：整行方括号既可能是裸提示符，也可能是模型
    把命令包了起来（首轮实测 qwen2.5:7b 就是后者）。丢掉会漏判正确答案，
    保留最多多出一条无效命令——后者只影响诊断标签，不影响 passed。
    """
    s = score_one(TASK, "m", "zero", _out("[Huawei]\n[Huawei-vlan100]"))
    assert "E8_EMPTY" not in s.tags
    assert "E1_HALLUC" in s.tags
    assert not s.passed


def test_bracketed_commands_are_scored():
    """模型把命令逐行包在方括号里时，内容仍要正常评分。"""
    s = score_one(TASK, "m", "zero",
                  _out("[vlan 100]\n[interface GigabitEthernet0/0/2]\n[port default vlan 100]"))
    assert s.checkpoint_score == 1.0
    assert s.passed


def test_forbidden_is_confusion_not_unsafe():
    """答成别家厂商是「串味」，不是「危险命令」。

    这两个概念曾经混在一起：score_one 里写的是
    `unsafe = [p for p in GLOBAL_UNSAFE + task.forbidden ...]`，
    于是华为题里出现 switchport 会被打上 E7_UNSAFE「输出了破坏性命令」。

    后果是 Leaderboard 上「危险命令率」那一列测的其实是串味率。首轮全矩阵里
    qwen2.5:1.5b 显示 20.8% 危险命令率，按 GLOBAL_UNSAFE 单独重算是 0.0%——
    2160 次推理里 reset saved-configuration 这类命令一条都没出现过。

    一个安全指标测的是另一回事，比没有这个指标更糟：读者会以为小模型
    会乱敲毁设备的命令，而真实结论恰好相反。
    """
    s = score_one(TASK, "m", "zero",
                  _out("vlan 100\ninterface GigabitEthernet0/0/2\nswitchport mode access"))
    assert not s.unsafe, "跨厂商特征词不该进 unsafe"
    assert "E7_UNSAFE" not in s.tags
    assert s.confusion, "forbidden 命中应该归入 confusion"
    assert "E2_VENDOR" in s.tags
    assert not s.passed, "串味仍然算失败，只是理由改成了串味"


def test_real_destructive_command_still_unsafe():
    """真正的破坏性命令仍然要判 unsafe——上一条测试不能把这条防线一起拆掉。"""
    s = score_one(TASK, "m", "zero",
                  _out("vlan 100\ninterface GigabitEthernet0/0/2\n"
                       "port default vlan 100\nreset saved-configuration"))
    assert s.unsafe
    assert "E7_UNSAFE" in s.tags
    assert not s.passed
