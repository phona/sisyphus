"""Contract: SISYPHUS_PROMPTS_DIR env var 优先级 + 缺省回退 package dir。

验收条件（issue #330 §A）：
  1. 设了 SISYPHUS_PROMPTS_DIR 且目录含 *.j2 → 返回该目录
  2. 设了 SISYPHUS_PROMPTS_DIR 但目录为空  → 回退 package dir
  3. 未设 SISYPHUS_PROMPTS_DIR             → 回退 package dir
"""
from orchestrator.prompts import _PACKAGE_DIR, _get_prompt_dir


def test_env_dir_with_j2_files_is_preferred(monkeypatch, tmp_path):
    (tmp_path / "example.md.j2").write_text("{{ var }}")
    monkeypatch.setenv("SISYPHUS_PROMPTS_DIR", str(tmp_path))
    assert _get_prompt_dir() == tmp_path


def test_env_dir_empty_falls_back_to_package_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SISYPHUS_PROMPTS_DIR", str(tmp_path))
    assert _get_prompt_dir() == _PACKAGE_DIR


def test_no_env_uses_package_dir(monkeypatch):
    monkeypatch.delenv("SISYPHUS_PROMPTS_DIR", raising=False)
    assert _get_prompt_dir() == _PACKAGE_DIR


def test_package_dir_contains_j2_files():
    """回归：package dir 自身必须含 *.j2，否则 prod 路径全挂。"""
    assert any(_PACKAGE_DIR.glob("*.j2")), f"no *.j2 in {_PACKAGE_DIR}"


def test_env_dir_nonexistent_path_falls_back(monkeypatch):
    monkeypatch.setenv("SISYPHUS_PROMPTS_DIR", "/nonexistent/path/prompts")
    assert _get_prompt_dir() == _PACKAGE_DIR
