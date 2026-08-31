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
