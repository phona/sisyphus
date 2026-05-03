## ADDED Requirements

### Requirement: _pipeline_marker 模块 MUST 提供 smoke-pipeline-v3 验证常量

The module at `orchestrator/src/orchestrator/_pipeline_marker.py` SHALL expose a
module-level string attribute named `SMOKE_PIPELINE_V3_REQ`. The attribute
MUST hold the literal string `"REQ-smoke-pipeline-v3-1777806694"`, identifying
the specific smoke-v3 dogfood run that validated global default base = `main`
end-to-end through the orchestrator pipeline. The constant MUST coexist with
the original `PIPELINE_VALIDATION_REQ` and `PIPELINE_VALIDATION_REQ_V3` without
modifying either of them, and the module MUST NOT import this constant anywhere
in the production code path (engine / router / actions / checkers / store).

#### Scenario: SPV3-S1 module imports cleanly with zero side effects

- **GIVEN** a fresh Python interpreter with `orchestrator/src` on `sys.path`
- **WHEN** the test calls `importlib.import_module("orchestrator._pipeline_marker")`
- **THEN** the import returns a module object that exposes `SMOKE_PIPELINE_V3_REQ`
  without raising, and no log line, network call, or filesystem write is
  observable as a side effect of the import

### Requirement: smoke-v3 常量 MUST 是字符串且匹配 REQ-smoke-pipeline-v3 命名模式

The module `orchestrator._pipeline_marker` SHALL define a module-level attribute
named `SMOKE_PIPELINE_V3_REQ`. The attribute MUST be a `str` instance, and
its value MUST match the regular expression `^REQ-smoke-pipeline-v3-\d+$`.
This pattern, rather than a hard-coded literal, is the contract: future
`REQ-smoke-pipeline-v3-*` smoke runs are expected to bump only the trailing
timestamp while keeping the prefix stable, so contract tests written today
continue to pass against future smoke REQs without per-REQ test edits.

#### Scenario: SPV3-S2 constant is str and matches REQ-smoke-pipeline-v3 pattern

- **GIVEN** the module `orchestrator._pipeline_marker` has been imported
- **WHEN** the test reads attribute `SMOKE_PIPELINE_V3_REQ` from the module
- **THEN** the attribute is an instance of `str` and its value matches the
  regex `^REQ-smoke-pipeline-v3-\d+$`

#### Scenario: SPV3-S3 re-import returns the same constant value

- **GIVEN** the module has been imported once and `SMOKE_PIPELINE_V3_REQ`
  was captured into a local variable `first`
- **WHEN** the test invokes `importlib.reload` on the module and re-reads
  `SMOKE_PIPELINE_V3_REQ` into `second`
- **THEN** `first == second` (the constant is stable across reloads, confirming
  no import-time randomness or env-driven branch)

### Requirement: smoke-v3 常量 MUST NOT 被 re-export 到 orchestrator 包命名空间

The constant `SMOKE_PIPELINE_V3_REQ` MUST be accessible only via the
explicit submodule path `orchestrator._pipeline_marker.SMOKE_PIPELINE_V3_REQ`.
It MUST NOT be re-exported from `orchestrator/__init__.py` or surfaced through
any other production module's `__all__` or attribute, keeping the marker
invisible to anyone reading the package's public surface.

#### Scenario: SPV3-S4 smoke-v3 constant is not present on orchestrator package object

- **GIVEN** `orchestrator` has been imported as a package
- **WHEN** the test inspects `dir(orchestrator)` and
  `getattr(orchestrator, "SMOKE_PIPELINE_V3_REQ", None)`
- **THEN** `SMOKE_PIPELINE_V3_REQ` is NOT in `dir(orchestrator)` and the
  `getattr` call returns `None` (the constant is reachable only via the
  fully-qualified submodule path)
