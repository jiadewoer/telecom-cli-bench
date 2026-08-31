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

# 一级 token 别名：同厂商内的通用缩写
_TOKEN_ALIASES: dict[str, dict[str, str]] = {
    "huawei": {
        "sys": "system-view",
        "system": "system-view",
        "int": "interface",
        "inter": "interface",
        "dis": "display",
        "disp": "display",
        "q": "quit",
        "u": "undo",
        "sh": "display",  # 华为设备上 sh 不是 show，模型常写错，这里不纵容
    },
    "cisco": {
        "conf": "configure",
        "config": "configure",
        "int": "interface",
        "sh": "show",
        "sho": "show",
        "ip add": "ip address",
        "no shut": "no shutdown",
        "wr": "write",
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


def strip_think(text: str) -> str:
    """剥掉推理模型的 <think> 段。未闭合的情况也要处理。"""
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
    s = _PROMPT_PREFIX.sub("", line.strip())
    s = _LIST_MARKER.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    if not s or _COMMENT.match(s):
        return ""

    aliases = _TOKEN_ALIASES.get(vendor, {})
    # 先做多词别名（如 cisco 的 "ip add"），再做单 token
    for k, v in aliases.items():
        if " " in k:
            s = re.sub(rf"^{re.escape(k)}\b", v, s)
    toks = s.split(" ")
    toks = [aliases.get(t, t) if i < 2 else t for i, t in enumerate(toks)]
    s = " ".join(toks)

    for pat, repl in _IFACE_RULES:
        s = pat.sub(repl, s)
    s = _IFACE_SPACE.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_block(lines: list[str], vendor: str) -> str:
    """把一组命令归一化成一整块小写文本，供 re.search(..., re.M) 匹配。"""
    out = [normalize_line(ln, vendor) for ln in lines]
    return "\n".join(x for x in out if x)
