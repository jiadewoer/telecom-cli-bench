from pathlib import Path

from telecom_cli_bench.schema import load_tasks

TASK_DIR = Path("data/tasks")


def test_demo_tasks_are_excluded_by_default():
    """防数据泄漏：demo_ 前缀的 few-shot 示例不能进评测集。"""
    graded = load_tasks(TASK_DIR)
    assert all(not t.id.startswith("demo_") for t in graded)
    assert len(load_tasks(TASK_DIR, include_demo=True)) > len(graded)


def test_task_ids_are_unique():
    tasks = load_tasks(TASK_DIR, include_demo=True)
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids))


def test_few_shot_demos_cover_both_vendors():
    """few-shot 示例必须每个厂商都有，否则跑另一厂商的题时会在提示词里教模型串味。"""
    demos = [t for t in load_tasks(TASK_DIR, include_demo=True) if t.id.startswith("demo_")]
    vendors = {t.vendor.value for t in demos}
    assert vendors == {"huawei", "cisco"}, f"缺少示例的厂商: {{'huawei','cisco'}} - {vendors}"
    for v in vendors:
        assert len([t for t in demos if t.vendor.value == v]) >= 2, f"{v} 的示例少于 2 条"
