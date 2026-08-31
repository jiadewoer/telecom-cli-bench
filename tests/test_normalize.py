from telecom_cli_bench.normalize import extract_commands, normalize_line


def test_huawei_abbrev_expansion():
    assert normalize_line("sys", "huawei") == "system-view"
    assert normalize_line("int g0/0/1", "huawei") == "interface gigabitethernet0/0/1"
    assert normalize_line("dis ospf peer", "huawei") == "display ospf peer"


def test_interface_forms_converge():
    forms = ["interface GigabitEthernet0/0/1", "int Gi0/0/1", "INT GE 0/0/1", "int g0/0/1"]
    normed = {normalize_line(f, "huawei") for f in forms}
    assert normed == {"interface gigabitethernet0/0/1"}


def test_device_prompt_is_stripped():
    assert normalize_line("[Huawei-GigabitEthernet0/0/1] undo shutdown", "huawei") == "undo shutdown"
    assert normalize_line("<Huawei> save", "huawei") == "save"
    assert normalize_line("Switch(config)# no shutdown", "cisco") == "no shutdown"
    assert normalize_line("Switch(config-if)#switchport mode access", "cisco") == (
        "switchport mode access"
    )


def test_cross_vendor_terms_are_not_merged():
    """铁律：跨厂商等价概念不归一，否则串味检测失效。"""
    assert normalize_line("eth-trunk 1", "huawei") != normalize_line("port-channel 1", "cisco")


def test_ordinary_commands_are_not_mangled():
    """回归：接口缩写规则不能误伤不含接口的普通命令。

    这些规则里的单字母分支（g / e / lo）很容易在别处误命中，
    一旦误伤，检查点会莫名匹配不上，且极难排查。
    """
    unchanged = [
        "vlan 100",
        "name office",
        "port default vlan 100",
        "ospf 1 router-id 1.1.1.1",
        "area 0",
        "bgp 65001",
        "ip address 192.168.10.1 255.255.255.0",
        "rule 5 permit source 10.0.0.0 0.0.0.255",
        "acl number 3000",
        "stp mode rstp",
        "quit",
        "undo shutdown",
        "switchport access vlan 20",
        "network 10.0.0.0 0.0.0.255 area 0",
    ]
    for cmd in unchanged:
        assert normalize_line(cmd, "huawei") == cmd, f"被误改: {cmd}"


def test_extract_from_fence():
    text = "配置如下：\n```\nsystem-view\nvlan 100\n```\n这样就好了。"
    cmds, fenced = extract_commands(text)
    assert fenced is True
    assert cmds == ["system-view", "vlan 100"]


def test_extract_without_fence_drops_prose():
    text = "首先进入系统视图。\nsystem-view\n然后创建 VLAN。\nvlan 100"
    cmds, fenced = extract_commands(text)
    assert fenced is False
    assert cmds == ["system-view", "vlan 100"]


def test_think_tag_is_stripped():
    text = "<think>用户想配 VLAN，我先想想</think>\n```\nvlan 100\n```"
    cmds, _ = extract_commands(text)
    assert cmds == ["vlan 100"]


def test_unclosed_think_tag():
    text = "<think>思考中，忘了闭合\n```\nvlan 100\n```"
    cmds, _ = extract_commands(text)
    assert cmds == ["vlan 100"]


def test_cisco_conf_t_variants():
    """conf t 是思科工程师最常用的敲法，不归一会把大量正确答案判错。

    首轮实测中 qwen2.5:7b 就输出了 `conf t`，被误判为未进入全局配置模式。
    """
    for form in ["conf t", "config t", "configure t", "conf term",
                 "conf terminal", "configure terminal", "CONF T"]:
        assert normalize_line(form, "cisco") == "configure terminal", f"未归一: {form}"


def test_conf_t_rule_does_not_leak_to_huawei():
    """华为设备上没有 configure terminal，这条规则不能作用于华为。"""
    assert normalize_line("conf t", "huawei") != "configure terminal"


def test_whole_line_brackets_are_unwrapped_not_dropped():
    """模型可能把命令逐行包在方括号里，不能当成设备提示符整行丢掉。

    首轮实测中 qwen2.5:7b 对两道 VLAN 题输出的就是 [vlan 100] 这种形式，
    原实现把整行抹成空字符串，得分从应有的 0.83 掉到 0.00。
    """
    assert normalize_line("[vlan 100]", "huawei") == "vlan 100"
    assert normalize_line("[quit]", "huawei") == "quit"
    assert normalize_line("[port default vlan 100]", "huawei") == "port default vlan 100"
    # 带命令的提示符仍按提示符处理
    assert normalize_line("[Huawei-GigabitEthernet0/0/1] undo shutdown", "huawei") == "undo shutdown"
