"""命令归一化：把等价写法收敛到同一形式，再交给检查点正则匹配。

设计铁律：只做「同一厂商内部的写法差异」的归一化。
跨厂商的等价概念（eth-trunk vs port-channel）绝不归一，
否则厂商串味检测就失效了。
"""

from __future__ import annotations

import re

# 推理模型（deepseek-r1 等）的思维链，评分前必须剥掉
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)```", re.S)

_COMMENT = re.compile(r"^\s*[#!]|^\s*//")
# 设备提示符：<Huawei>  [Huawei-GigabitEthernet0/0/1]  Switch(config-if)#  R1>
# 注意 Cisco 提示符里有圆括号和斜杠，字符类里必须带上，否则剥不掉
_PROMPT_PREFIX = re.compile(r"^\s*(?:<[^>]{0,40}>|\[[^\]]{0,40}\]|[\w.()/-]{1,40}[#>])\s*")
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+")

# 整行被方括号包住的情况。设备提示符总是出现在命令「前面」并跟着命令，
# 所以整行只有一个方括号组时，更可能是模型把命令本身包了起来。
# 取舍：拆括号保留内容，而不是当提示符丢掉——
# 漏掉一条正确命令的代价，大于多出一条无效命令的代价（后者最多被标成幻觉，
# 不影响 passed 判定）。首轮实测 qwen2.5:7b 就把整份配置逐行包在了方括号里。
_BRACKET_ONLY = re.compile(r"^\[([^\]]{1,80})\]$")

# 一级 token 别名：同厂商内的通用缩写。
#
# 首轮全矩阵（2160 次推理）实测各条规则的触发次数，注在每行末尾。
# 整张表只触发了 20 次，其中 8 次是 cisco 的 wr。九条规则一次都没用上。
# 记下这些数字是为了下次有人想加规则时，先问一句「模型真的会这么写吗」。
_TOKEN_ALIASES: dict[str, dict[str, str]] = {
    "huawei": {
        "sys": "system-view",   # 实测 3 次
        "system": "system-view",  # 0
        "int": "interface",     # 0
        "inter": "interface",   # 0
        "dis": "display",       # 1
        "disp": "display",      # 0
        "q": "quit",            # 0
        "u": "undo",            # 0
        # 这里曾经有一条 "sh": "display"，注释写的是「华为设备上 sh 不是 show，
        # 模型常写错，这里不纵容」。两个判断都被数据推翻了：
        #
        # 一、模型不常写错。2160 次推理里华为题写 sh 的次数是 0。
        # 二、方向反了。VRP 上 sh 更可能是 shutdown 的缩写，
        #    转成 display 会把「关闭接口」变成「查看」，语义完全颠倒——
        #    这比不做归一化更糟。
        #
        # 零收益、非零风险的规则，删掉。cisco 的 sh -> show 没有这个歧义，保留。
        # 见 docs/notes.md D19。
    },
    "cisco": {
        "conf": "configure",    # 实测 3 次
        "config": "configure",  # 1
        "int": "interface",     # 4
        "sh": "show",           # 0，但 cisco 上 sh 无歧义，保留
        "sho": "show",          # 0
        "ip add": "ip address",  # 0
        "no shut": "no shutdown",  # 0
        "wr": "write",          # 8，全表最高。模型会主动写保存配置，题目并没要求
    },
}

# 接口名归一化：缩写 → 全称（小写）。顺序重要，长的在前。
_IFACE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:xgigabitethernet|xge|xg)(?=\s*\d)", re.I), "xgigabitethernet"),
    (re.compile(r"\b(?:gigabitethernet|gigabit|gig|gi|ge|g)(?=\s*\d)", re.I), "gigabitethernet"),
    (re.compile(r"\b(?:ethernet|eth|et|e)(?=\s*\d)", re.I), "ethernet"),
    (re.compile(r"\b(?:loopback|loop|lo)(?=\s*\d)", re.I), "loopback"),
    (re.compile(r"\b(?:vlanif|vlan-interface|vlanint)(?=\s*\d)", re.I), "vlanif"),
]
_IFACE_SPACE = re.compile(
    r"\b(xgigabitethernet|gigabitethernet|ethernet|loopback|vlanif)\s+(?=\d)", re.I
)

# 查看命令的识别与放宽。只在这两个动词开头的行上生效，见 normalize_line 里的说明。
_VIEW_CMD = re.compile(r"^(show|display)\b")
_MID_INT = re.compile(r"\bint(?:er)?\b(?!erface)")
_IFACES_PLURAL = re.compile(r"\binterfaces\b")

# 思科进全局配置模式的各种缩写：conf t / config t / configure t / conf term / ...
# 单 token 别名表处理不了这种「两个词各自都缩写」的情况，单独出一条规则。
# 这是工程师现实中最常用的敲法，不处理会把大量正确答案判错。
_CISCO_CONF_T = re.compile(r"^conf(?:ig(?:ure)?)?\s+t(?:erm(?:inal)?)?$")


def strip_think(text: str) -> str:
    """剥掉推理模型的 <think> 段。未闭合的情况也要处理。

    注意：这条逻辑在首轮全矩阵的真实数据上**一次都没被触发过**。
    deepseek-r1:8b 跑了 360 次，输出里没有出现过一个 <think> 标签——
    Ollama 的 OpenAI 兼容接口把推理过程放在单独字段里，content 只有最终答案。

    保留它是因为换个推理后端（vLLM、llama.cpp 直连、或 Ollama 的原生 /api/chat）
    很可能就需要它了。但它属于「防的是没发生过的情况」，
    真出问题时别指望这两条测试已经验证过它。见 docs/notes.md D18。
    """
    text = THINK_RE.sub("", text)
    if "<think>" in text.lower():
        text = re.split(r"</?think>", text, flags=re.I)[-1]
    return text


def extract_commands(text: str) -> tuple[list[str], bool]:
    """从模型输出里抽命令。

    返回 (命令列表, 是否来自代码块)。
    第二个值就是「格式合规」指标——我们在提示词里要求用代码块包裹，
    没照做的模型要扣分。
    """
    text = strip_think(text)
    blocks = FENCE_RE.findall(text)
    if blocks:
        body = "\n".join(blocks)
        return ([ln for ln in (x.strip() for x in body.splitlines()) if ln], True)

    # 没有代码块：退化成启发式抽取，逐行判断像不像命令
    cmds = []
    for raw in text.splitlines():
        ln = _LIST_MARKER.sub("", raw).strip()
        if not ln or _COMMENT.match(ln):
            continue
        if re.search(r"[，。：；？！\u4e00-\u9fff]", ln):  # 含中文，判为解释文字
            continue
        if len(ln.split()) > 12:
            continue
        cmds.append(ln)
    return (cmds, False)


def normalize_line(line: str, vendor: str) -> str:
    """单行归一化：剥提示符、展开缩写、统一接口名、压空白、转小写。"""
    s = line.strip()
    m = _BRACKET_ONLY.match(s)
    if m:
        s = m.group(1).strip()
    s = _PROMPT_PREFIX.sub("", s)
    s = _LIST_MARKER.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    # 剥引号。模型受 Markdown 习惯影响会写 description "to-branch"，
    # 设备上引号是字面量的一部分，两者不等价——但把它判成错答案对读者毫无信息量。
    # 人工标注 60 条时踩到一次（hw_route_059 / llama3.1），全矩阵共 23 处命令带引号。
    s = s.replace('"', "").replace("'", "")
    if not s or _COMMENT.match(s):
        return ""

    aliases = _TOKEN_ALIASES.get(vendor, {})
    if vendor == "cisco":
        s = _CISCO_CONF_T.sub("configure terminal", s)
    # 先做多词别名（如 cisco 的 "ip add"），再做单 token
    for k, v in aliases.items():
        if " " in k:
            s = re.sub(rf"^{re.escape(k)}\b", v, s)
    toks = s.split(" ")
    toks = [aliases.get(t, t) if i < 2 else t for i, t in enumerate(toks)]
    s = " ".join(toks)

    # 查看命令的两处放宽。D6 把别名展开限制在前两个 token，是为了防止
    # 过度归一化抹平跨厂商概念。但查看命令（show / display 开头）是安全区：
    # 后面跟的全是设备自己的关键字，不会出现用户自定义字符串。
    # 人工标注 60 条时，这两条各踩到一次，全矩阵影响面 10 处和 46 处。
    if _VIEW_CMD.match(s):
        # 一、int / inter 在第三个 token 之后也要展开。
        #     show ip int brief 是思科上最常见的敲法之一，D6 的两 token 限制放不过它。
        s = _MID_INT.sub("interface", s)
        # 二、统一单复数。show interfaces trunk 与 show interface trunk 在 IOS 上等价，
        #     没有任何命令靠单复数区分语义，统一到单数最省事。
        #     注意：数据集里的 checkpoint 正则必须同步写成单数，否则永远匹配不上。
        s = _IFACES_PLURAL.sub("interface", s)

    for pat, repl in _IFACE_RULES:
        s = pat.sub(repl, s)
    s = _IFACE_SPACE.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_block(lines: list[str], vendor: str) -> str:
    """把一组命令归一化成一整块小写文本，供 re.search(..., re.M) 匹配。"""
    out = [normalize_line(ln, vendor) for ln in lines]
    return "\n".join(x for x in out if x)
