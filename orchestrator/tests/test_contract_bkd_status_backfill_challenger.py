"""Challenger contract tests for REQ-bkd-cleanup-historical-review-1777222384.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-bkd-cleanup-historical-review-1777222384/specs/bkd-status-backfill/spec.md

Run the CLI as a subprocess against a local embedded HTTP server (no internal
imports, no monkeypatching) — testing only externally observable behaviour.

Scenarios:
  BBR-S1  verifier review+completed → selected, stdout action="skipped" with role=verifier reason
  BBR-S2  intent issue (no role tag) → rejected, NOT in stdout
  BBR-S3  running session + role tag → rejected, NOT in stdout
  BBR-S4  dry-run: 0 HTTP PATCHes, exactly 2 JSON lines action="skipped", exit 0
  BBR-S5  apply: 3 PATCHes with {"statusId":"done"} body only, 3 "patched" lines, exit 0
  BBR-S6  partial PATCH failure (503): loop continues, "failed" entry, ≥1 success → exit 0

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse


# ─── Embedded mock BKD HTTP server ───────────────────────────────────────────


class _BKDHandler(BaseHTTPRequestHandler):
    """Minimal mock: GET /…/issues returns list; PATCH /…/issues/<id> records call."""

    def log_message(self, *_: Any) -> None:
        pass  # suppress access-log noise on stderr

    def _send_json(self, code: int, body: Any) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if urlparse(self.path).path.endswith("/issues"):
            self._send_json(200, {"success": True, "data": self.server.list_issues})  # type: ignore[attr-defined]
        else:
            self._send_json(404, {"success": False, "error": "not found"})

    def do_PATCH(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        issue_id = urlparse(self.path).path.rsplit("/", 1)[-1]
        self.server.patch_calls.append((issue_id, body))  # type: ignore[attr-defined]
        sc = self.server.patch_outcomes.get(issue_id, 200)  # type: ignore[attr-defined]
        if sc >= 400:
            self._send_json(sc, {"success": False, "error": f"HTTP {sc}"})
        else:
            self._send_json(200, {"success": True, "data": {"id": issue_id, "statusId": "done"}})


@contextmanager
def _mock_bkd(
    list_issues: list[dict],
    patch_outcomes: dict[str, int] | None = None,
):
    """Spin up a mock BKD HTTP server; yield (server, base_url); shut down on exit."""
    server = HTTPServer(("127.0.0.1", 0), _BKDHandler)
    server.list_issues = list_issues  # type: ignore[attr-defined]
    server.patch_outcomes = patch_outcomes or {}  # type: ignore[attr-defined]
    server.patch_calls: list[tuple[str, dict]] = []  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address
    try:
        yield server, f"http://{host}:{port}"
    finally:
        server.shutdown()


def _run_cli(
    base_url: str,
    *,
    apply: bool = False,
) -> tuple[int, list[dict], str]:
    """Run CLI as subprocess; return (exit_code, parsed_stdout_json_lines, stderr)."""
    cmd = [
        sys.executable, "-m",
        "orchestrator.maintenance.backfill_bkd_review_stuck",
        "--project", "p",
        "--bkd-base-url", base_url,
    ]
    if apply:
        cmd.append("--apply")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    lines: list[dict] = []
    for raw in result.stdout.strip().splitlines():
        raw = raw.strip()
        if raw:
            lines.append(json.loads(raw))
    return result.returncode, lines, result.stderr


def _issue(
    *,
    id: str = "i1",
    status: str = "review",
    tags: list[str] | None = None,
    session: str = "completed",
) -> dict:
    return {
        "id": id,
        "statusId": status,
        "tags": tags if tags is not None else [],
        "sessionStatus": session,
    }


# ─── BBR-S1 ──────────────────────────────────────────────────────────────────


def test_bbr_s1_verifier_review_completed_selected() -> None:
    """BBR-S1: verifier issue at review with completed session MUST be selected.

    In dry-run, the issue appears in stdout with action='skipped' and
    reason starting with 'role=verifier;session=completed'.
    """
    issue = _issue(
        id="v1",
        tags=["verifier", "REQ-foo-1234", "verify:staging_test", "decision:escalate"],
        session="completed",
    )
    with _mock_bkd([issue]) as (_, url):
        rc, lines, _ = _run_cli(url, apply=False)

    assert rc == 0
    assert len(lines) == 1, f"BBR-S1: expected 1 JSON line; got {len(lines)}"
    entry = lines[0]
    assert entry.get("action") == "skipped", (
        f"BBR-S1: dry-run candidate MUST have action='skipped'; got {entry!r}"
    )
    reason = entry.get("reason", "")
    assert reason.startswith("role=verifier;session=completed"), (
        f"BBR-S1: reason MUST start with 'role=verifier;session=completed'; got {reason!r}"
    )


# ─── BBR-S2 ──────────────────────────────────────────────────────────────────


def test_bbr_s2_intent_issue_without_role_tag_rejected() -> None:
    """BBR-S2: issue with no role tag MUST NOT appear in stdout output.

    The CLI protects user-created intent issues from being silently archived.
    """
    intent = _issue(id="intent1", tags=["REQ-foo-1234"], session="completed")
    with _mock_bkd([intent]) as (server, url):
        rc, lines, _ = _run_cli(url, apply=False)
        patch_calls = list(server.patch_calls)

    assert rc == 0
    assert patch_calls == [], "BBR-S2: no PATCHes for rejected issue"
    issue_ids_in_output = [ln["issue_id"] for ln in lines if "issue_id" in ln]
    assert "intent1" not in issue_ids_in_output, (
        "BBR-S2: intent issue without role tag MUST NOT appear in stdout"
    )


# ─── BBR-S3 ──────────────────────────────────────────────────────────────────


def test_bbr_s3_running_session_rejected_even_with_role_tag() -> None:
    """BBR-S3: sessionStatus='running' MUST prevent selection even when role tag present.

    Live sessions MUST NOT be patched to 'done'.
    """
    live = _issue(id="live1", tags=["fixer", "REQ-foo-1234"], session="running")
    with _mock_bkd([live]) as (server, url):
        rc, lines, _ = _run_cli(url, apply=False)
        patch_calls = list(server.patch_calls)

    assert rc == 0
    assert patch_calls == [], "BBR-S3: no PATCHes for running session"
    issue_ids_in_output = [ln["issue_id"] for ln in lines if "issue_id" in ln]
    assert "live1" not in issue_ids_in_output, (
        "BBR-S3: running session issue MUST NOT appear in stdout"
    )


# ─── BBR-S4 ──────────────────────────────────────────────────────────────────


def test_bbr_s4_dry_run_zero_patches_two_skipped_lines() -> None:
    """BBR-S4: without --apply, CLI MUST make zero HTTP PATCHes.

    stdout MUST contain exactly 2 JSON lines, each with action='skipped' and
    a non-empty reason. Exit code MUST be 0.
    """
    issues = [
        _issue(id="a", tags=["verifier", "REQ-x-1"], session="completed"),
        _issue(id="b", tags=["fixer", "REQ-y-2"], session="failed"),
    ]
    with _mock_bkd(issues) as (server, url):
        rc, lines, _ = _run_cli(url, apply=False)
        patch_calls = list(server.patch_calls)

    assert rc == 0
    assert patch_calls == [], (
        f"BBR-S4: dry-run MUST make zero HTTP PATCH calls; got {patch_calls!r}"
    )
    assert len(lines) == 2, (
        f"BBR-S4: dry-run MUST emit exactly 2 JSON lines for 2 candidates; got {len(lines)}"
    )
    for line in lines:
        assert line.get("action") == "skipped", (
            f"BBR-S4: every dry-run line MUST have action='skipped'; got {line!r}"
        )
        assert line.get("reason"), (
            f"BBR-S4: reason MUST be non-empty; got {line!r}"
        )


# ─── BBR-S5 ──────────────────────────────────────────────────────────────────


def test_bbr_s5_apply_patches_each_candidate_with_statusid_only_body() -> None:
    """BBR-S5: --apply MUST PATCH each candidate exactly once with {"statusId":"done"}.

    Contract:
    - exactly 3 HTTP PATCH calls, one per candidate
    - each call body is {"statusId": "done"} — tags key MUST be absent
    - stdout has 3 lines with action="patched"
    - exit 0
    """
    issues = [
        _issue(id="a", tags=["verifier", "REQ-x-1"], session="completed"),
        _issue(id="b", tags=["fixer", "REQ-y-2"], session="failed"),
        _issue(id="c", tags=["analyze", "REQ-z-3"], session="failed"),
    ]
    with _mock_bkd(issues) as (server, url):
        rc, lines, _ = _run_cli(url, apply=True)
        patch_calls = list(server.patch_calls)

    assert rc == 0
    assert len(patch_calls) == 3, (
        f"BBR-S5: MUST make exactly 3 PATCH calls; got {len(patch_calls)}"
    )
    patched_ids = {iid for iid, _ in patch_calls}
    assert patched_ids == {"a", "b", "c"}, (
        f"BBR-S5: MUST patch all 3 candidate IDs; got {patched_ids!r}"
    )
    for iid, body in patch_calls:
        assert body == {"statusId": "done"}, (
            f"BBR-S5: PATCH body for {iid!r} MUST be exactly "
            f'{{"statusId": "done"}}; got {body!r}'
        )
        assert "tags" not in body, (
            f"BBR-S5: PATCH body MUST NOT include 'tags' key "
            f"(full-replace semantics would wipe audit tags); got {body!r}"
        )
    assert len(lines) == 3, (
        f"BBR-S5: stdout MUST have 3 JSON lines; got {len(lines)}"
    )
    for line in lines:
        assert line.get("action") == "patched", (
            f"BBR-S5: apply entries MUST have action='patched'; got {line!r}"
        )


# ─── BBR-S6 ──────────────────────────────────────────────────────────────────


def test_bbr_s6_partial_patch_failure_continues_and_exits_zero() -> None:
    """BBR-S6: a single PATCH returning 503 MUST NOT abort the loop; exit 0 if ≥1 success.

    Contract:
    - 3 PATCH attempts regardless of mid-loop failure
    - first + third stdout entries have action="patched"
    - second entry has action="failed" with non-empty reason
    - exit code 0 (at least one succeeded)
    """
    issues = [
        _issue(id="a", tags=["verifier", "REQ-x-1"], session="completed"),
        _issue(id="b", tags=["fixer", "REQ-y-2"], session="failed"),   # will 503
        _issue(id="c", tags=["analyze", "REQ-z-3"], session="failed"),
    ]
    with _mock_bkd(issues, patch_outcomes={"b": 503}) as (server, url):
        rc, lines, _ = _run_cli(url, apply=True)
        patch_calls = list(server.patch_calls)

    assert rc == 0, (
        f"BBR-S6: exit code MUST be 0 when ≥1 PATCH succeeded; got {rc}"
    )
    assert len(patch_calls) == 3, (
        f"BBR-S6: MUST attempt all 3 PATCHes even after mid-loop 503; got {len(patch_calls)}"
    )
    by_id = {line["issue_id"]: line for line in lines}
    assert by_id.get("a", {}).get("action") == "patched", (
        f"BBR-S6: first issue MUST be patched; got {by_id.get('a')!r}"
    )
    assert by_id.get("c", {}).get("action") == "patched", (
        f"BBR-S6: third issue MUST be patched; got {by_id.get('c')!r}"
    )
    b_entry = by_id.get("b", {})
    assert b_entry.get("action") == "failed", (
        f"BBR-S6: second issue (503) MUST report action='failed'; got {b_entry!r}"
    )
    assert b_entry.get("reason"), (
        f"BBR-S6: failed entry MUST have non-empty reason (mention HTTP error); got {b_entry!r}"
    )
