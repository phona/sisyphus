"""Challenger contract tests for REQ-fix-test-isolation-364-1777867646.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-test-isolation-364-1777867646/specs/
    test-isolation-readyz-harness/spec.md

Capability: test-isolation-readyz-harness

Dev MUST NOT modify these tests to make them pass — fix the harness instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

The SUT is the `_readyz_harness` contextmanager defined in
`tests/test_contract_readyz_namespaced_challenger.py`. These contract tests
import and invoke it as an opaque black box and assert post-conditions on
`orchestrator.k8s_runner.get_controller` after harness exit.

Scenarios covered:
  TIRH-S1   no get_controller leak — set_controller(fake) round-trips after harness
  TIRH-S2   RZN-S3 black-box still passes inside harness body
  TIRH-S3   harness exit restores the source `get_controller` (raises
            "RunnerController 未初始化" not "not init")
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from orchestrator import k8s_runner
from test_contract_readyz_namespaced_challenger import _client, _readyz_harness


# Snapshot the source's real get_controller at module import time, BEFORE any
# harness has run in this session. Used by TIRH-S3 to assert identity restoration.
_ORIGINAL_GET_CONTROLLER = k8s_runner.get_controller


@pytest.fixture(autouse=True)
def _reset_controller_state():
    """Each contract test starts and ends with _controller=None to avoid
    cross-test bleed inside *this* contract file. Does not depend on harness
    behavior — uses the source `set_controller` directly."""
    k8s_runner.set_controller(None)
    yield
    k8s_runner.set_controller(None)


# ─── TIRH-S1 ──────────────────────────────────────────────────────────────────


def test_TIRH_S1_no_get_controller_leak_after_harness():
    """After the polluter path (`raise_runtime_on_get_controller=True`) exits,
    `set_controller(fake)` followed by `get_controller()` MUST return `fake`
    itself — not a leaked MagicMock that would shadow the source resolver.
    """
    with _readyz_harness(
        controller=None, raise_runtime_on_get_controller=True
    ):
        # Exercise the path that triggers the dual-patch case in question.
        resp = _client().get("/readyz")
        assert resp.status_code == 200

    fake = object()  # opaque sentinel; identity is the only thing under test
    k8s_runner.set_controller(fake)
    result = k8s_runner.get_controller()
    assert result is fake, (
        f"leak detected: set_controller({fake!r}) but get_controller() returned "
        f"{result!r} (type={type(result).__name__}). The harness must restore "
        f"`k8s_runner.get_controller` to the source function on exit."
    )


# ─── TIRH-S2 ──────────────────────────────────────────────────────────────────


def test_TIRH_S2_RZN_S3_blackbox_inside_harness_body():
    """Inside the harness body with `controller=None` and
    `raise_runtime_on_get_controller=True`, `_client().get("/readyz")` MUST
    return HTTP 200 with body `{"status": "ok"}` and the failed-checks list
    MUST NOT contain "k8s" — this is the existing RZN-S3 black-box behavior
    that the isolation fix must not regress.
    """
    with _readyz_harness(
        controller=None, raise_runtime_on_get_controller=True
    ):
        resp = _client().get("/readyz")

        assert resp.status_code == 200, (
            f"RZN-S3: /readyz must respond 200 inside harness body; got "
            f"{resp.status_code}"
        )

        body = resp.json()
        assert body.get("status") == "ok", (
            f"RZN-S3: /readyz body.status must be 'ok'; got {body!r}"
        )

        failed = body.get("failed", []) or []
        assert "k8s" not in failed, (
            f"RZN-S3: failed list must not contain 'k8s' when controller is "
            f"intentionally uninitialized; got failed={failed!r}"
        )


# ─── TIRH-S3 ──────────────────────────────────────────────────────────────────


def test_TIRH_S3_harness_exit_restores_original_get_controller():
    """After `_readyz_harness(..., raise_runtime_on_get_controller=True)` exits,
    `orchestrator.k8s_runner.get_controller` MUST be the source's original
    function — invoking it with `_controller is None` MUST raise `RuntimeError`
    whose message starts with "RunnerController 未初始化" (the source signature),
    NOT "not init" (the leaked-mock signature from the harness's runtime patch).
    """
    with _readyz_harness(
        controller=None, raise_runtime_on_get_controller=True
    ):
        pass  # body does not matter — only the post-exit state under test

    # Identity check: the attribute MUST be the same callable captured at
    # module import time.
    assert k8s_runner.get_controller is _ORIGINAL_GET_CONTROLLER, (
        "harness exit must restore k8s_runner.get_controller to the source "
        f"function (id={id(_ORIGINAL_GET_CONTROLLER)}); got a different object "
        f"(id={id(k8s_runner.get_controller)}, type="
        f"{type(k8s_runner.get_controller).__name__}). This indicates a leaked "
        "patch that will pollute downstream tests in the same session."
    )

    # Behavioral check: must NOT be any flavor of Mock.
    assert not isinstance(k8s_runner.get_controller, Mock), (
        "k8s_runner.get_controller is a Mock after harness exit — leaked patch."
    )

    # Behavioral check: with _controller=None, the source raises with its own
    # error message. The leaked harness mock's message is "not init" — that
    # MUST NOT appear here.
    k8s_runner.set_controller(None)
    with pytest.raises(RuntimeError) as excinfo:
        k8s_runner.get_controller()

    msg = str(excinfo.value)
    assert msg.startswith("RunnerController 未初始化"), (
        f"after harness exit, get_controller() must raise the source "
        f"RuntimeError starting with 'RunnerController 未初始化'. Got: {msg!r}. "
        f"A message of 'not init' would indicate the harness mock leaked."
    )
    assert "not init" not in msg, (
        f"leaked-mock signature 'not init' detected in error message: {msg!r}"
    )
