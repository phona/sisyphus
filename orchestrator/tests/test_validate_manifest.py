"""validate-manifest.py 单测。

schema 严：字段类型 / 枚举 / 格式错都要被拒。
"""
from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

# 直接从 scripts 目录加载（不是 package）
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-manifest.py"
_spec = importlib.util.spec_from_file_location("validate_manifest", str(_SCRIPT))
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate_path = _mod.validate_path
_collect_errors = _mod._collect_errors


# ── helpers ─────────────────────────────────────────────────────────


def _good_manifest() -> dict:
    return {
        "schema_version": 1,
        "req_id": "REQ-997",
        "sources": [
            {
                "repo": "phona/ttpos-server-go",
                "path": "source/ttpos-server-go",
                "role": "leader",
                "branch": "stage/REQ-997-dev",
                "depends_on": ["phona/ubox-proto"],
            },
            {
                "repo": "phona/ubox-proto",
                "path": "source/ubox-proto",
                "role": "source",
                "branch": "stage/REQ-997-dev",
            },
        ],
        "integration": {
            "repo": "phona/ttpos-arch-lab",
            "path": "integration/ttpos-arch-lab",
        },
    }


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "manifest.yaml"
    p.write_text(textwrap.dedent(body))
    return p


# ── happy path ──────────────────────────────────────────────────────


def test_good_manifest_no_errors():
    assert _collect_errors(_good_manifest()) == []


def test_no_integration_is_ok():
    m = _good_manifest()
    del m["integration"]
    assert _collect_errors(m) == []


def test_file_load_happy(tmp_path):
    p = _write(tmp_path, """\
    schema_version: 1
    req_id: REQ-1
    sources:
      - repo: a/b
        path: source/b
        role: leader
        branch: stage/REQ-1-dev
    """)
    assert validate_path(p) == []


# ── bad schema_version ──────────────────────────────────────────────


def test_wrong_schema_version():
    m = _good_manifest()
    m["schema_version"] = 2
    errs = _collect_errors(m)
    assert any("schema_version" in e for e in errs)


# ── bad req_id ──────────────────────────────────────────────────────


def test_invalid_req_id():
    m = _good_manifest()
    m["req_id"] = "not-a-req"
    errs = _collect_errors(m)
    assert any("req_id" in e for e in errs)


# ── sources invariants ──────────────────────────────────────────────


def test_empty_sources_rejected():
    m = _good_manifest()
    m["sources"] = []
    errs = _collect_errors(m)
    assert any("sources" in e for e in errs)


def test_must_have_exactly_one_leader():
    m = _good_manifest()
    m["sources"] = [
        {"repo": "a/b", "path": "source/b", "role": "source", "branch": "stage/REQ-1-dev"},
        {"repo": "c/d", "path": "source/d", "role": "source", "branch": "stage/REQ-1-dev"},
    ]
    errs = _collect_errors(m)
    assert any("leader" in e for e in errs)


def test_duplicate_source_repo_rejected():
    m = _good_manifest()
    m["sources"] = [
        {"repo": "a/b", "path": "source/b", "role": "leader", "branch": "stage/REQ-1-dev"},
        {"repo": "a/b", "path": "source/b2", "role": "source", "branch": "stage/REQ-1-dev"},
    ]
    errs = _collect_errors(m)
    assert any("重复" in e for e in errs)


def test_bad_repo_format():
    m = _good_manifest()
    m["sources"][0]["repo"] = "no-slash"
    errs = _collect_errors(m)
    assert any("repo" in e for e in errs)


def test_bad_path_prefix():
    m = _good_manifest()
    m["sources"][0]["path"] = "srcs/foo"
    errs = _collect_errors(m)
    assert any("path" in e and "source/" in e for e in errs)


def test_bad_role_value():
    m = _good_manifest()
    m["sources"][0]["role"] = "random"
    errs = _collect_errors(m)
    assert any("role" in e for e in errs)


def test_bad_branch_prefix():
    m = _good_manifest()
    m["sources"][0]["branch"] = "main"
    errs = _collect_errors(m)
    assert any("branch" in e for e in errs)


def test_bad_depends_on_entry():
    m = _good_manifest()
    m["sources"][0]["depends_on"] = ["not-a-repo"]
    errs = _collect_errors(m)
    assert any("depends_on" in e for e in errs)


# ── integration invariants ──────────────────────────────────────────


def test_integration_bad_repo():
    m = _good_manifest()
    m["integration"]["repo"] = "invalid"
    errs = _collect_errors(m)
    assert any("integration.repo" in e for e in errs)


def test_integration_bad_path():
    m = _good_manifest()
    m["integration"]["path"] = "lab/"
    errs = _collect_errors(m)
    assert any("integration.path" in e for e in errs)


# ── later-filled fields type check ──────────────────────────────────


def test_image_tags_must_be_dict():
    m = _good_manifest()
    m["image_tags"] = []  # type error
    errs = _collect_errors(m)
    assert any("image_tags" in e for e in errs)


def test_merge_order_must_be_list():
    m = _good_manifest()
    m["merge_order"] = {}  # type error
    errs = _collect_errors(m)
    assert any("merge_order" in e for e in errs)


# ── file IO errors ──────────────────────────────────────────────────


def test_missing_file(tmp_path):
    errs = validate_path(tmp_path / "nonexistent.yaml")
    assert any("不存在" in e for e in errs)


def test_invalid_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("::::not-valid-yaml:::\n  - [\n")
    errs = validate_path(p)
    assert any("YAML" in e or "解析" in e for e in errs)


def test_yaml_root_not_dict(tmp_path):
    p = tmp_path / "scalar.yaml"
    p.write_text("just-a-string\n")
    errs = validate_path(p)
    assert any("object" in e or "dict" in e for e in errs)
