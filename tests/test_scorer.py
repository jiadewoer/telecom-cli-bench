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
