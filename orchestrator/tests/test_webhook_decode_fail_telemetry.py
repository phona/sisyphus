"""Router decode-fail telemetry — 3 emit signals at terminal verifier escalate
(closes phona/sisyphus#372).

Covers spec scenarios RDFT-S1..S3 in
``openspec/changes/REQ-feat-router-telemetry-v3-1777866642/specs/router-decode-fail-telemetry/spec.md``.
"""
from __future__ import annotations

from typing import ClassVar

import pytest
import structlog

from orchestrator import webhook
from orchestrator.bkd import Issue


class _FakeBKD:
    """In-memory stub of BKDClient — capture update_issue / get_issue calls."""

    captured_update: ClassVar[list[dict]] = []
    captured_get: ClassVar[list[tuple[str, str]]] = []
    raise_on_update: ClassVar[bool] = False
    raise_on_get: ClassVar[bool] = False
    issue_tags: ClassVar[list[str]] = []
    issue_description: ClassVar[str | None] = None

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get_issue(self, project_id, issue_id):
        self.captured_get.append((project_id, issue_id))
        if self.raise_on_get:
            raise RuntimeError("BKD get_issue down")
        return Issue(
            id=issue_id, project_id=project_id, issue_number=1,
            title="t", status_id="working",
            tags=list(self.issue_tags),
            session_status="completed",
            description=self.issue_description,
        )

    async def update_issue(self, *, project_id, issue_id, **kw):
        if self.raise_on_update:
            raise RuntimeError("BKD update_issue down")
        self.captured_update.append({
            "project_id": project_id, "issue_id": issue_id, **kw,
        })


@pytest.fixture(autouse=True)
def _reset_fake_bkd():
    _FakeBKD.captured_update = []
    _FakeBKD.captured_get = []
    _FakeBKD.raise_on_update = False
    _FakeBKD.raise_on_get = False
    _FakeBKD.issue_tags = ["verifier", "verify:staging_test", "REQ-x"]
    _FakeBKD.issue_description = None


@pytest.fixture
def fake_bkd(monkeypatch):
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKD)
    return _FakeBKD


@pytest.fixture
def captured_stage_runs(monkeypatch):
    """Replace stage_runs.insert_decode_fail with a list-capturing stub."""
    captured: list[dict] = []

    async def fake_insert(pool, **kw):
        captured.append(kw)
        return 12345

    monkeypatch.setattr(
        "orchestrator.webhook.stage_runs.insert_decode_fail", fake_insert,
    )
    return captured


@pytest.fixture
def captured_obs(monkeypatch):
    captured: list[dict] = []

    async def fake_record(kind, *, req_id=None, issue_id=None, extras=None, **_kw):
        captured.append({
            "kind": kind, "req_id": req_id, "issue_id": issue_id,
            "extras": extras,
        })

    monkeypatch.setattr("orchestrator.webhook.obs.record_event", fake_record)
    return captured


@pytest.fixture
def captured_logs():
    """structlog testing helper — returns the in-memory log entries."""
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    yield cap
    structlog.reset_defaults()


@pytest.mark.asyncio
async def test_stage_runs_row_inserted_on_decode_fail(
    fake_bkd, captured_stage_runs, captured_obs,
):
    """RDFT-S1: stage_runs row inserted with stage='router_decode_fail',
    outcome='silent_drop', and full context."""
    await webhook._emit_decode_fail_telemetry(
        pool=object(),  # stub — fake helper ignores it
        project_id="proj-1",
        issue_id="vfy-1",
        req_id="REQ-x",
        verifier_stage="staging_test",
        reason="no decision JSON found in tag or description",
        raw_tags=["verifier", "verify:staging_test", "REQ-x"],
    )

    assert len(captured_stage_runs) == 1
    row = captured_stage_runs[0]
    assert row["req_id"] == "REQ-x"
    assert row["issue_id"] == "vfy-1"
    assert row["verifier_stage"] == "staging_test"
    assert row["reason"].startswith("no decision JSON")
    assert row["raw_tags"] == ["verifier", "verify:staging_test", "REQ-x"]


@pytest.mark.asyncio
async def test_bkd_issue_tag_and_description_patched(
    fake_bkd, captured_stage_runs, captured_obs,
):
    """RDFT-S2: BKD `update_issue` call carries tags + description with the
    decode-fail signal, both reason and raw_tags surfaced verbatim."""
    _FakeBKD.issue_tags = ["verifier", "verify:staging_test", "REQ-x"]
    _FakeBKD.issue_description = "original prompt body"

    await webhook._emit_decode_fail_telemetry(
        pool=object(),
        project_id="proj-1",
        issue_id="vfy-1",
        req_id="REQ-x",
        verifier_stage="staging_test",
        reason="invalid decision: invalid action: 'pass'",
        raw_tags=["verifier", "verify:staging_test", "REQ-x", "decision:pass"],
    )

    assert len(_FakeBKD.captured_update) == 1
    payload = _FakeBKD.captured_update[0]
    assert payload["project_id"] == "proj-1"
    assert payload["issue_id"] == "vfy-1"

    assert "router-decode-fail" in payload["tags"]
    # Existing tags preserved, no duplicates of router-decode-fail
    assert payload["tags"].count("router-decode-fail") == 1
    for original in _FakeBKD.issue_tags:
        assert original in payload["tags"]

    assert "router decode 失败" in payload["description"]
    assert "invalid action: 'pass'" in payload["description"]
    # Original body preserved before warning block
    assert payload["description"].startswith("original prompt body")


@pytest.mark.asyncio
async def test_warning_log_emitted_first(
    captured_logs, fake_bkd, captured_stage_runs, captured_obs,
):
    """RDFT-S3: a single WARNING-level `router.decode_fail` log entry is emitted
    with structured `issue_id` / `req_id` / `stage` / `reason` / `raw_tags`."""
    await webhook._emit_decode_fail_telemetry(
        pool=object(),
        project_id="proj-1",
        issue_id="vfy-1",
        req_id="REQ-x",
        verifier_stage="staging_test",
        reason="no decision JSON found in tag or description",
        raw_tags=["verifier", "REQ-x"],
    )

    matching = [
        e for e in captured_logs.entries
        if e.get("event") == "router.decode_fail"
        and e.get("log_level") == "warning"
    ]
    assert len(matching) == 1, captured_logs.entries
    e = matching[0]
    assert e["issue_id"] == "vfy-1"
    assert e["req_id"] == "REQ-x"
    assert e["stage"] == "staging_test"
    assert e["reason"] == "no decision JSON found in tag or description"
    assert e["raw_tags"] == ["verifier", "REQ-x"]


@pytest.mark.asyncio
async def test_bkd_failure_does_not_block_other_signals(
    captured_logs, fake_bkd, captured_stage_runs, captured_obs,
):
    """RDFT-S2 isolation: when BKD update_issue raises, stage_runs row + obs
    record + WARNING log still fire."""
    _FakeBKD.raise_on_update = True

    await webhook._emit_decode_fail_telemetry(
        pool=object(),
        project_id="proj-1",
        issue_id="vfy-1",
        req_id="REQ-x",
        verifier_stage="staging_test",
        reason="some reason",
        raw_tags=["verifier"],
    )

    # stage_runs still inserted
    assert len(captured_stage_runs) == 1
    # obs.record_event still called
    assert any(c["kind"] == "router.decode_fail" for c in captured_obs)
    # primary warning still logged
    assert any(
        e.get("event") == "router.decode_fail" and e.get("log_level") == "warning"
        for e in captured_logs.entries
    )
    # BKD-failure-specific WARNING also logged
    assert any(
        e.get("event") == "router.decode_fail.bkd_patch_failed"
        for e in captured_logs.entries
    )


@pytest.mark.asyncio
async def test_no_req_id_skips_stage_runs_but_still_logs(
    captured_logs, fake_bkd, captured_stage_runs, captured_obs,
):
    """When req_id is None (extract_req_id returned nothing), stage_runs insert
    is skipped (FK / NOT NULL safety) but the BKD signal + warning log still fire."""
    await webhook._emit_decode_fail_telemetry(
        pool=object(),
        project_id="proj-1",
        issue_id="vfy-1",
        req_id=None,
        verifier_stage="unknown",
        reason="no decision JSON found in tag or description",
        raw_tags=["verifier"],
    )

    assert captured_stage_runs == []
    # BKD PATCH still attempted
    assert len(_FakeBKD.captured_update) == 1
    # Warning still logged
    assert any(
        e.get("event") == "router.decode_fail" and e.get("log_level") == "warning"
        for e in captured_logs.entries
    )
