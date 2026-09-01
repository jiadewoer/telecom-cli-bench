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
    assert (
        normalize_line("[Huawei-GigabitEthernet0/0/1] undo shutdown", "huawei")
        == "undo shutdown"
    )
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
    assert (
        normalize_line("[Huawei-GigabitEthernet0/0/1] undo shutdown", "huawei")
        == "undo shutdown"
    )


def test_huawei_sh_is_not_expanded_to_display():
    """华为侧不再把 sh 展开成 display。

    这条规则曾经存在，注释写着「华为设备上 sh 不是 show，模型常写错，
    这里不纵容」。首轮全矩阵 2160 次推理把两个判断都推翻了：

    一、模型不常写错——华为题里写 sh 的次数是 0。
    二、方向反了——VRP 上 sh 更可能是 shutdown 的缩写，
        转成 display 会把「关闭接口」变成「查看接口」，语义完全颠倒。

    零收益、非零风险，所以删掉。这条测试防止它被好心加回来。
    """
    assert normalize_line("sh int g0/0/1", "huawei").startswith("sh ")


def test_cisco_sh_still_expands_to_show():
    """思科侧 sh -> show 没有歧义，保留。删上一条规则时不能把这条一起删掉。"""
    assert normalize_line("sh ip route", "cisco") == "show ip route"


def test_view_commands_expand_mid_position_int():
    """show ip int brief 是思科上最常见的敲法之一，必须归一化成全称。

    D6 把别名展开限制在前两个 token，int 排在第三位就漏掉了。
    人工标注 60 条时踩到（cs_diag_033 / llama3.1），全矩阵影响 10 处。
    只在 show / display 开头的行上放宽：查看命令后面全是设备关键字，
    不会出现用户自定义字符串，扩大作用域是安全的。
    """
    assert normalize_line("show ip int brief", "cisco") == "show ip interface brief"
    assert normalize_line("display ip int brief", "huawei") == "display ip interface brief"


def test_view_commands_unify_interface_plural():
    """show interfaces trunk 与 show interface trunk 在 IOS 上等价，统一到单数。

    没有任何命令靠 interface 的单复数区分语义。首版检查点写死了复数，
    而全矩阵里模型用单数写了 46 处——这是三条归一化缺口里影响面最大的一条。
    """
    assert normalize_line("show interfaces trunk", "cisco") == "show interface trunk"
    assert normalize_line("show interface trunk", "cisco") == "show interface trunk"


def test_config_commands_keep_mid_tokens_intact():
    """放宽只对查看命令生效，配置命令的中段一个字都不能动。

    这条是上面两条的护栏：如果哪天有人把 _VIEW_CMD 的限制去掉，
    description 之类的用户字符串就会被当成缩写展开。
    """
    assert normalize_line("description int-uplink", "cisco") == "description int-uplink"
    assert normalize_line("description interfaces", "huawei") == "description interfaces"


def test_quotes_are_stripped():
    """模型受 Markdown 习惯影响会给描述加引号，设备上引号是字面量的一部分。

    严格说两者不等价，但把 description "to-branch" 判成错答案对读者没有信息量。
    人工标注时踩到一次（hw_route_059 / llama3.1），全矩阵 23 处命令带引号。
    """
    assert normalize_line('description "to-branch"', "huawei") == "description to-branch"
    assert normalize_line("description 'To-Server'", "cisco") == "description to-server"
