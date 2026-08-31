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
    s = score_one(TASK, "m", "zero", _out(
        "vlan 100\ninterface gigabitethernet0/0/2\nswitchport access vlan 100"))
    assert "E2_VENDOR" in s.tags
    assert "E7_UNSAFE" in s.tags     # switchport 同时在本题 forbidden 里


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
