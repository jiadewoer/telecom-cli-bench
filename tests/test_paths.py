from telecom_cli_bench.paths import PROJECT_ROOT, PROMPT_DIR, TASK_DIR, VOCAB_DIR


def test_runtime_paths_are_absolute_and_point_to_project_assets():
    assert PROJECT_ROOT.is_absolute()
    assert TASK_DIR.is_absolute() and TASK_DIR.is_dir()
    assert PROMPT_DIR.is_absolute() and PROMPT_DIR.is_dir()
    assert VOCAB_DIR.is_absolute() and VOCAB_DIR.is_dir()
