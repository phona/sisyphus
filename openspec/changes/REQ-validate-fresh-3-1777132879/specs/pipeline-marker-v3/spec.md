## ADDED Requirements

### Requirement: _pipeline_marker 模块 MUST 提供 v3 验证常量

The module at `orchestrator/src/orchestrator/_pipeline_marker.py` SHALL expose a
module-level string attribute named `PIPELINE_VALIDATION_REQ_V3`. The attribute
MUST hold the literal string `"REQ-validate-fresh-3-1777132879"`, identifying
the specific fresh-pipeline dogfood run that validated sha-ddf4ea4 (sonnet
default + all P3 fix). The constant MUST coexist with the original
`PIPELINE_VALIDATION_REQ` without modifying it, and the module MUST NOT import
this constant anywhere in the production code path (engine / router / actions /
checkers / store).

#### Scenario: PVR3-S1 module imports cleanly with zero side effects

- **GIVEN** a fresh Python interpreter with `orchestrator/src` on `sys.path`
- **WHEN** the test calls `importlib.import_module("orchestrator._pipeline_marker")`
- **THEN** the import returns a module object that exposes `PIPELINE_VALIDATION_REQ_V3`
  without raising, and no log line, network call, or filesystem write is
  observable as a side effect of the import

### Requirement: v3 常量 MUST 是字符串且匹配 REQ-validate-fresh-3 命名模式

The module `orchestrator._pipeline_marker` SHALL define a module-level attribute
named `PIPELINE_VALIDATION_REQ_V3`. The attribute MUST be a `str` instance, and
its value MUST match the regular expression `^REQ-validate-fresh-3-\d+$`.
This pattern, rather than a hard-coded literal, is the contract: future
`REQ-validate-fresh-3-*` smoke runs are expected to bump only the trailing
timestamp while keeping the prefix stable, so contract tests written today
continue to pass against future smoke REQs without per-REQ test edits.

#### Scenario: PVR3-S2 constant is str and matches REQ-validate-fresh-3 pattern

- **GIVEN** the module `orchestrator._pipeline_marker` has been imported
- **WHEN** the test reads attribute `PIPELINE_VALIDATION_REQ_V3` from the module
- **THEN** the attribute is an instance of `str` and its value matches the
  regex `^REQ-validate-fresh-3-\d+$`

#### Scenario: PVR3-S3 re-import returns the same constant value

- **GIVEN** the module has been imported once and `PIPELINE_VALIDATION_REQ_V3`
  was captured into a local variable `first`
- **WHEN** the test invokes `importlib.reload` on the module and re-reads
  `PIPELINE_VALIDATION_REQ_V3` into `second`
- **THEN** `first == second` (the constant is stable across reloads, confirming
  no import-time randomness or env-driven branch)

### Requirement: v3 常量 MUST NOT 被 re-export 到 orchestrator 包命名空间

The constant `PIPELINE_VALIDATION_REQ_V3` MUST be accessible only via the
explicit submodule path `orchestrator._pipeline_marker.PIPELINE_VALIDATION_REQ_V3`.
It MUST NOT be re-exported from `orchestrator/__init__.py` or surfaced through
any other production module's `__all__` or attribute, keeping the marker
invisible to anyone reading the package's public surface.

#### Scenario: PVR3-S4 v3 constant is not present on orchestrator package object

- **GIVEN** `orchestrator` has been imported as a package
- **WHEN** the test inspects `dir(orchestrator)` and
  `getattr(orchestrator, "PIPELINE_VALIDATION_REQ_V3", None)`
- **THEN** `PIPELINE_VALIDATION_REQ_V3` is NOT in `dir(orchestrator)` and the
  `getattr` call returns `None` (the constant is reachable only via the
  fully-qualified submodule path)
