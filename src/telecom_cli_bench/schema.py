"""任务与结果的数据模型。所有 JSONL 读写都过这里，保证数据集不腐坏。"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# few-shot 示例任务的 id 前缀。这些任务不参与评测，防止数据泄漏。
DEMO_PREFIX = "demo_"


class Vendor(str, Enum):
    HUAWEI = "huawei"
    CISCO = "cisco"


class Domain(str, Enum):
    INTERFACE = "interface"
    VLAN = "vlan"
    ROUTING = "routing"
    ACL = "acl"
    MPLS = "mpls"
    DIAGNOSE = "diagnose"


class Checkpoint(BaseModel):
    """一个检查点 = 一条正则，匹配「归一化后的小写命令文本」。

    约定：pattern 一律写成小写全称形式。归一化层已经把
    `int g0/0/1` 展开成 `interface gigabitethernet0/0/1`，
    所以这里不需要考虑缩写。
    """

    id: str
    pattern: str
    weight: float = 1.0
    desc: str = ""

    @field_validator("pattern")
    @classmethod
    def _must_compile(cls, v: str) -> str:
        re.compile(v)  # 编译不过直接在加载期报错，不要拖到评测时
        return v


class Task(BaseModel):
    id: str
    vendor: Vendor
    domain: Domain
    level: int = Field(ge=1, le=3)
    instruction: str
    context: str = ""
    reference: list[str] = Field(min_length=1)
    checkpoints: list[Checkpoint] = Field(min_length=1)
    forbidden: list[str] = Field(default_factory=list)
    notes: str = ""

    @property
    def total_weight(self) -> float:
        return sum(c.weight for c in self.checkpoints)


def load_tasks(dir_or_file: Path, include_demo: bool = False) -> list[Task]:
    """加载任务。默认排除 demo_ 前缀的 few-shot 示例，防止数据泄漏。"""
    paths = sorted(dir_or_file.glob("*.jsonl")) if dir_or_file.is_dir() else [dir_or_file]
    tasks: list[Task] = []
    for p in paths:
        with p.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except Exception as e:  # noqa: BLE001
                    raise ValueError(f"{p.name}:{lineno} JSON 解析失败 -> {e}") from e
                if not include_demo and str(d.get("id", "")).startswith(DEMO_PREFIX):
                    continue
                try:
                    tasks.append(Task(**d))
                except Exception as e:  # noqa: BLE001
                    raise ValueError(f"{p.name}:{lineno} 校验失败 -> {e}") from e
    return tasks
