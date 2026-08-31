"""Leaderboard 与图表。

三张图对应三条叙事：
  图一 主榜        —— 谁能用（通过率 vs 危险命令率）
  图二 错误构成    —— 都错在哪里
  图三 提示词消融  —— 领域知识能不能靠提示词补上（本项目的差异化）
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")  # 无 GUI 后端，脚本里画图不弹窗、不依赖显示环境
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# 中文字体。不设这两行，图上所有中文都是空心方框。
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

TAG_ORDER = ["E0_FORMAT", "E1_HALLUC", "E2_VENDOR", "E3_MISS", "E7_UNSAFE", "E8_EMPTY"]
TAG_CN = {
    "E0_FORMAT": "格式不合规",
    "E1_HALLUC": "命令幻觉",
    "E2_VENDOR": "厂商串味",
    "E3_MISS": "遗漏步骤",
    "E7_UNSAFE": "危险命令",
    "E8_EMPTY": "无有效输出",
}


def load_scores(path: Path) -> pd.DataFrame:
    lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines:
        raise ValueError(f"{path} 是空的，先跑 tcb score")
    return pd.DataFrame([json.loads(x) for x in lines])


def build_leaderboard(path: Path, prompt: str = "zero_shot") -> str:
    """主榜只用 zero_shot 一档：加了提示词的成绩不能和裸跑的混排。"""
    df = load_scores(path)
    d = df[df.prompt == prompt]
    if d.empty:
        raise ValueError(f"没有 prompt={prompt} 的数据，实际有: {sorted(df.prompt.unique())}")

    g = (
        d.groupby("model")
        .agg(
            任务通过率=("passed", "mean"),
            检查点得分=("checkpoint_score", "mean"),
            格式合规率=("format_ok", "mean"),
            危险命令率=("unsafe", "mean"),
            厂商串味率=("confusion", "mean"),
            平均耗时s=("latency_s", "mean"),
        )
        .sort_values("任务通过率", ascending=False)
    )
    for c in g.columns:
        # 比率列转成百分数，耗时保持秒
        g[c] = (g[c] * 100).round(1) if c.endswith("率") or c == "检查点得分" else g[c].round(2)
    return g.reset_index().to_markdown(index=False)


def plot_leaderboard(df: pd.DataFrame, out: Path, prompt: str = "zero_shot") -> None:
    """图一：主榜。通过率向右，危险命令率向左，一眼看出「能用但危险」的模型。"""
    d = (
        df[df.prompt == prompt]
        .groupby("model")
        .agg(passed=("passed", "mean"), unsafe=("unsafe", "mean"))
        .sort_values("passed")
    )
    fig, ax = plt.subplots(figsize=(9, 5))
    y = range(len(d))
    ax.barh(y, d["passed"] * 100, color="#4C72B0", label="任务通过率 (%)")
    ax.barh(y, -d["unsafe"] * 100, color="#C44E52", label="危险命令率 (%)")
    ax.set_yticks(list(y))
    ax.set_yticklabels(d.index, fontsize=9)
    ax.axvline(0, color="black", lw=0.8)
    # 危险命令率是画在负半轴上的，但它本身不是负数，刻度要显示绝对值
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{abs(v):.0f}"))
    ax.set_xlabel("← 危险命令率      任务通过率 →")
    ax.set_title("网络设备 CLI 任务：通过率 vs 危险命令率", fontsize=13)
    ax.legend(loc="lower right")
    ax.grid(alpha=0.25, axis="x")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_error_composition(df: pd.DataFrame, out: Path, prompt: str = "zero_shot") -> None:
    """图二：每个模型的错误构成堆叠柱。

    注意：一条任务可能同时带多个标签（既漏步骤又串味），
    所以各段之和会超过 100%。这不是 bug，图注里要说明。
    """
    d = df[df.prompt == prompt]
    rows = {}
    for model, sub in d.groupby("model"):
        n = len(sub)
        rows[model] = {t: sub["tags"].str.contains(t, na=False).sum() / n * 100 for t in TAG_ORDER}
    m = pd.DataFrame(rows).T[TAG_ORDER]
    m.columns = [TAG_CN[c] for c in m.columns]

    ax = m.plot(kind="bar", stacked=True, figsize=(10, 5.5), colormap="tab20")
    ax.set_ylabel("占全部任务的比例 (%)")
    ax.set_title("错误构成：模型都错在哪里（同一任务可命中多个标签，故总和可超 100%）", fontsize=12)
    ax.set_xticklabels(m.index, rotation=18, ha="right", fontsize=9)
    ax.legend(fontsize=8, ncol=3)
    ax.grid(alpha=0.25, axis="y")
    ax.figure.tight_layout()
    ax.figure.savefig(out, dpi=150)
    plt.close(ax.figure)


def plot_prompt_ablation(df: pd.DataFrame, out: Path) -> None:
    """图三（差异化）：提示词能补多少领域知识。"""
    piv = df.pivot_table(index="model", columns="prompt", values="passed", aggfunc="mean") * 100
    order = [c for c in ["zero_shot", "few_shot", "syntax_hint"] if c in piv.columns]
    ax = piv[order].plot(kind="bar", figsize=(10, 5), width=0.75)
    ax.set_ylabel("任务通过率 (%)")
    ax.set_title("提示词消融：领域知识能靠提示词补上吗？", fontsize=13)
    ax.set_xticklabels(piv.index, rotation=18, ha="right", fontsize=9)
    ax.grid(alpha=0.25, axis="y")
    ax.legend(title="提示词")
    ax.figure.tight_layout()
    ax.figure.savefig(out, dpi=150)
    plt.close(ax.figure)


def plot_vendor_gap(df: pd.DataFrame, out: Path, prompt: str = "zero_shot") -> None:
    """图四（可选）：华为 vs 思科的通过率差，反映训练语料的分布偏差。"""
    d = df[df.prompt == prompt]
    piv = d.pivot_table(index="model", columns="vendor", values="passed", aggfunc="mean") * 100
    ax = piv.plot(kind="bar", figsize=(9, 4.5), width=0.7, color=["#DD8452", "#55A868"])
    ax.set_ylabel("任务通过率 (%)")
    ax.set_title("厂商差异：同一模型在华为题与思科题上的表现", fontsize=13)
    ax.set_xticklabels(piv.index, rotation=18, ha="right", fontsize=9)
    ax.grid(alpha=0.25, axis="y")
    ax.legend(title="厂商")
    ax.figure.tight_layout()
    ax.figure.savefig(out, dpi=150)
    plt.close(ax.figure)


def plot_all(scores: Path, img_dir: Path) -> None:
    df = load_scores(scores)
    img_dir.mkdir(parents=True, exist_ok=True)
    plot_leaderboard(df, img_dir / "leaderboard.png")
    plot_error_composition(df, img_dir / "error_composition.png")
    if df.prompt.nunique() > 1:  # 只跑了一套提示词时，消融图没有意义
        plot_prompt_ablation(df, img_dir / "prompt_ablation.png")
    if df.vendor.nunique() > 1:
        plot_vendor_gap(df, img_dir / "vendor_gap.png")
