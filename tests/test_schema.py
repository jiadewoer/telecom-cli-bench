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


def test_reference_answers_never_trigger_hallucination():
    """标准答案里的每个命令动词都必须在词表里。

    否则模型答对了反而被扣一个「命令幻觉」标签。首轮实测踩到四处：
    import-route（hw_route_021/040）、silent-interface（hw_route_039）、
    neighbor（cs_route_029）。cs_route_029 里模型写的
    `neighbor 10.0.0.2 remote-as 65002` 完全正确，却挂着 E1_HALLUC。

    这是词表维护的固有风险，靠人眼发现不了，只能靠这条把它挡在 CI 里。
    """
    from pathlib import Path

    from telecom_cli_bench.normalize import normalize_block
    from telecom_cli_bench.scorer import load_vocab

    bad = []
    for t in load_tasks(Path("data/tasks")):
        vendor = t.vendor.value
        vocab = load_vocab(vendor, Path("data/vocab"))
        for line in normalize_block(t.reference, vendor).splitlines():
            verb = line.split(" ")[0]
            if verb and verb not in vocab:
                bad.append(f"{t.id}: 动词 {verb!r} 不在 {vendor} 词表里")
    assert not bad, "参考答案会被自己的词表判为幻觉：\n" + "\n".join(bad)
