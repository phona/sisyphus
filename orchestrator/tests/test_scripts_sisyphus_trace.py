"""Unit tests for scripts/sisyphus-trace.py.

REQ-feat-req-trace-view-381-v2-1777866643

Covers spec scenarios:
  TRACE-S1  ASCII render shape
  TRACE-S2  --json mode emits NDJSON
  TRACE-S3  missing REQ-id positional triggers usage error
  TRACE-S4  default kubectl knobs match sisyphus-admin
  QQ-S1     four UNION ALL branches in Q24 SQL
  QQ-S2     ts/kind/detail columns + ORDER BY ts
  QQ-S3     only the four kinds appear as quoted literals
  QQ-S4     parameter placeholder is {{req_id}}
  DOC-S1    dashboard contains Q24 heading
  DOC-S2    CLAUDE.md mentions sisyphus-trace under a debug heading
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Load scripts/sisyphus-trace.py without running main(). The script lives at
# repo-root/scripts/, so walk three parents (orchestrator/tests → orchestrator →
# repo-root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "sisyphus-trace.py"
_Q24_PATH = _REPO_ROOT / "observability" / "queries" / "sisyphus" / "24-req-trace.sql"
_DASHBOARD_PATH = _REPO_ROOT / "observability" / "sisyphus-dashboard.md"
_CLAUDE_PATH = _REPO_ROOT / "CLAUDE.md"


def _load_module():
    spec = importlib.util.spec_from_file_location("sisyphus_trace", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ─── TRACE-S1 ────────────────────────────────────────────────────────────


def test_trace_s1_ascii_render_shape():
    mod = _load_module()
    t1 = datetime(2026, 5, 4, 2, 25, 46, tzinfo=UTC)
    t2 = datetime(2026, 5, 4, 2, 25, 50, tzinfo=UTC)
    t3 = datetime(2026, 5, 4, 2, 33, 30, tzinfo=UTC)
    rows = [
        (t1, "trans", "INIT → ANALYZING"),
        (t2, "stage", "analyze start"),
        (t3, "check", "spec_lint passed"),
    ]
    out = mod.render_ascii("REQ-X", rows)
    lines = out.splitlines()
    assert lines[0] == "sisyphus-trace REQ-X"
    assert lines[1].startswith("─")
    assert len(lines) == 5
    pat = re.compile(r"^\d{2}:\d{2}:\d{2} \[(trans|stage|verify|check)\] ")
    for ev_line in lines[2:]:
        assert pat.match(ev_line), ev_line


# ─── TRACE-S2 ────────────────────────────────────────────────────────────


def test_trace_s2_json_mode_ndjson():
    mod = _load_module()
    t1 = datetime(2026, 5, 4, 2, 25, 46, tzinfo=UTC)
    t2 = datetime(2026, 5, 4, 2, 25, 50, tzinfo=UTC)
    t3 = datetime(2026, 5, 4, 2, 33, 30, tzinfo=UTC)
    rows = [
        (t1, "trans", "INIT → ANALYZING"),
        (t2, "stage", "analyze start"),
        (t3, "check", "spec_lint passed"),
    ]
    out = mod.render_ndjson(rows)
    lines = [ln for ln in out.splitlines() if ln]
    assert len(lines) == 3
    assert "sisyphus-trace" not in out
    assert "─" not in out
    for ln in lines:
        obj = json.loads(ln)
        assert set(obj.keys()) == {"ts", "kind", "detail"}


def test_trace_s2_json_mode_empty_rows_emits_empty_string():
    mod = _load_module()
    assert mod.render_ndjson([]) == ""


# ─── TRACE-S3 ────────────────────────────────────────────────────────────


def test_trace_s3_missing_req_id_argparse_error():
    mod = _load_module()
    parser = mod.build_parser()
    with pytest.raises(SystemExit) as exc_info:
        # capture stderr to keep test output clean
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args([])
    assert exc_info.value.code == 2


def test_trace_s3_error_message_mentions_req_id(capsys):
    mod = _load_module()
    parser = mod.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    err = capsys.readouterr().err
    assert "req_id" in err


# ─── TRACE-S4 ────────────────────────────────────────────────────────────


def test_trace_s4_default_namespace_and_pgpod():
    mod = _load_module()
    parser = mod.build_parser()
    ns = parser.parse_args(["REQ-X"])
    assert ns.namespace == "sisyphus"
    assert ns.pg_pod == "sisyphus-postgresql-0"


# ─── helpers: SQL loading & binding ──────────────────────────────────────


def test_bind_req_id_replaces_placeholder():
    mod = _load_module()
    sql = "WHERE req_id = {{req_id}}"
    out = mod._bind_req_id(sql, "REQ-abc")
    assert out == "WHERE req_id = 'REQ-abc'"


def test_bind_req_id_escapes_single_quote():
    mod = _load_module()
    sql = "WHERE req_id = {{req_id}}"
    # injection attempt: a stray apostrophe must be doubled, not break the literal
    out = mod._bind_req_id(sql, "REQ' OR 1=1--")
    assert out == "WHERE req_id = 'REQ'' OR 1=1--'"


def test_parse_psql_rows_handles_pipe_in_detail():
    mod = _load_module()
    raw = "2026-05-04 02:25:46+00|check|spec_lint passed cmd=foo|bar|baz"
    rows = mod._parse_psql_rows(raw)
    assert len(rows) == 1
    _, kind, detail = rows[0]
    assert kind == "check"
    # detail must keep the trailing pipes (split with maxsplit=2)
    assert detail == "spec_lint passed cmd=foo|bar|baz"


def test_parse_psql_rows_skips_blank_and_malformed():
    mod = _load_module()
    raw = "\n2026-05-04 02:25:46+00|trans|x\nbadline\n\n2026-05-04 02:25:50+00|stage|y\n"
    rows = mod._parse_psql_rows(raw)
    assert [r[1] for r in rows] == ["trans", "stage"]


# ─── QQ-S1 / QQ-S2 / QQ-S3 / QQ-S4: SQL contract ───────────────────────


def test_qqs1_four_named_ctes():
    sql = _Q24_PATH.read_text(encoding="utf-8")
    # WITH trans AS ( ... ), stages AS ( ... ), verifies AS ( ... ), checks AS ( ... )
    cte_names = re.findall(r"\b(\w+)\s+AS\s+\(", sql)
    assert {"trans", "stages", "verifies", "checks"}.issubset(set(cte_names))
    # outer UNION ALL union of the four CTEs
    for name in ("trans", "stages", "verifies", "checks"):
        assert re.search(rf"FROM\s+{name}\b", sql), f"missing FROM {name}"


def test_qqs2_columns_and_order_by():
    sql = _Q24_PATH.read_text(encoding="utf-8")
    assert re.search(r"ORDER BY\s+ts\s+ASC", sql), "ORDER BY ts ASC missing"
    # final outer SELECT must project ts/kind/detail
    assert re.search(r"SELECT\s+ts,\s*kind,\s*detail\s+FROM\s+trans", sql)


def test_qqs3_only_four_kinds_as_literals():
    sql = _Q24_PATH.read_text(encoding="utf-8")
    # find every `'<word>'::text` literal that is the kind column
    kinds = set(re.findall(r"'(trans|stage|verify|check)'::text", sql))
    assert kinds == {"trans", "stage", "verify", "check"}
    # and no other `'<word>'::text` appears as the kind expression
    other = re.findall(r"'([a-z_]+)'::text", sql)
    assert set(other) == {"trans", "stage", "verify", "check"}


def test_qqs4_parameter_placeholder_metabase():
    sql = _Q24_PATH.read_text(encoding="utf-8")
    # at least one {{req_id}} per source-table reference (4 tables)
    # — strip line comments before counting so the docstring header doesn't inflate
    code_lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    code = "\n".join(code_lines)
    assert code.count("{{req_id}}") >= 4
    for table in ("req_state", "stage_runs", "verifier_decisions", "artifact_checks"):
        # the table reference and a {{req_id}} filter must both appear
        assert table in code
    # no hardcoded REQ-id literal in code (comments are fine)
    assert not re.search(r"=\s*'REQ-", code)


# ─── DOC-S1 / DOC-S2: documentation surfaces ───────────────────────────


def test_docs1_dashboard_contains_q24_heading():
    text = _DASHBOARD_PATH.read_text(encoding="utf-8")
    assert re.search(r"^### Q24\.", text, flags=re.MULTILINE)
    assert "queries/sisyphus/24-req-trace.sql" in text


def test_docs2_claude_mentions_sisyphus_trace_under_debug_heading():
    text = _CLAUDE_PATH.read_text(encoding="utf-8")
    assert "sisyphus-trace" in text
    # find a heading line containing "debug" (case-insensitive) at-or-above
    # the first sisyphus-trace mention
    lines = text.splitlines()
    first_idx = next(i for i, ln in enumerate(lines) if "sisyphus-trace" in ln)
    # search backwards for a heading
    found_debug_heading = False
    for ln in reversed(lines[: first_idx + 1]):
        if ln.startswith("#"):
            if re.search(r"debug", ln, flags=re.IGNORECASE):
                found_debug_heading = True
            break
    assert found_debug_heading, "sisyphus-trace must appear under a debug-flavoured heading"
