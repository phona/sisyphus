"""Typed result dataclasses returned by `run_scenario` / `run_all`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Evidence:
    dom: str | None = None
    network: list[dict[str, Any]] | None = None
    screenshot: str | None = None  # base64 png or runner-PVC URL


@dataclass
class StepResult:
    step: str
    ok: bool
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class KbUpdate:
    path: str  # relative to source repo root, e.g. ".thanatos/anchors.md"
    action: Literal["patch", "append"]
    content: str


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    steps: list[StepResult] = field(default_factory=list)
    kb_updates: list[KbUpdate] = field(default_factory=list)
    failure_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "pass": self.passed,
            "steps": [
                {
                    "step": s.step,
                    "ok": s.ok,
                    "evidence": {
                        k: v
                        for k, v in {
                            "dom": s.evidence.dom,
                            "network": s.evidence.network,
                            "screenshot": s.evidence.screenshot,
                        }.items()
                        if v is not None
                    },
                }
                for s in self.steps
            ],
            "kb_updates": [
                {"path": u.path, "action": u.action, "content": u.content}
                for u in self.kb_updates
            ],
            "failure_hint": self.failure_hint,
        }
