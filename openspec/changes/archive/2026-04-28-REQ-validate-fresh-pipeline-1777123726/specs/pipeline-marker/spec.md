## ADDED Requirements

### Requirement: 仓内 MUST 提供 pipeline validation marker 模块

The repository SHALL provide a module at the path
`orchestrator/src/orchestrator/_pipeline_marker.py` whose sole responsibility is
to expose a module-level string constant identifying the REQ that is exercising
the sisyphus pipeline as a smoke / no-op validation. The module MUST NOT be
imported by any production code path (engine / router / actions / checkers /
prompts / store) and MUST NOT register any side-effect at import time (no IO,
no network, no global mutation, no logging emission). The module's value to the
project is purely as a self-dogfood scaffolding artifact: it gives every
"validate-fresh-pipeline" smoke REQ a tiny, deterministic, no-op delta to ship,
so the pipeline can be re-validated end-to-end without entangling the result
with real business changes.

#### Scenario: PVR-S1 module imports cleanly with zero side effects

- **GIVEN** a fresh Python interpreter with `orchestrator/src` on `sys.path`
- **WHEN** the test calls `importlib.import_module("orchestrator._pipeline_marker")`
- **THEN** the import returns a module object without raising, and no log line,
  network call, or filesystem write is observable as a side effect of the import

### Requirement: marker 常量 MUST 是字符串且匹配 REQ-validate-fresh-pipeline-<digits> 命名

The module `orchestrator._pipeline_marker` SHALL define a module-level attribute
named `PIPELINE_VALIDATION_REQ`. The attribute MUST be a `str` instance, and
its value MUST match the regular expression
`^REQ-validate-fresh-pipeline-\d+$`. This pattern, rather than a hard-coded
literal, is the contract: future pipeline-validation smoke REQs are expected
to bump only the trailing timestamp / counter while keeping the prefix stable,
so contract tests written today continue to pass against future smoke REQs
without per-REQ test edits.

#### Scenario: PVR-S2 constant is str and matches REQ pattern

- **GIVEN** the module `orchestrator._pipeline_marker` has been imported
- **WHEN** the test reads attribute `PIPELINE_VALIDATION_REQ` from the module
- **THEN** the attribute is an instance of `str` and its value matches the
  regex `^REQ-validate-fresh-pipeline-\d+$`

#### Scenario: PVR-S3 re-import returns the same constant value

- **GIVEN** the module has been imported once and `PIPELINE_VALIDATION_REQ`
  was captured into a local variable `first`
- **WHEN** the test invokes `importlib.reload` on the module and re-reads
  `PIPELINE_VALIDATION_REQ` into `second`
- **THEN** `first == second` (the module is referentially stable across
  reloads, confirming there is no import-time randomness or env-driven branch)

### Requirement: marker 模块 MUST NOT 被 re-export 到 orchestrator 包命名空间

The constant `PIPELINE_VALIDATION_REQ` MUST be accessible only via the explicit
submodule path `orchestrator._pipeline_marker.PIPELINE_VALIDATION_REQ`. It MUST
NOT be re-exported from `orchestrator/__init__.py` (which today does not exist
and SHALL remain absent for this REQ), nor surfaced through any other
production module's `__all__` or attribute. This keeps the marker invisible
to anyone reading the package's public surface and prevents accidental
production reliance on a smoke fixture.

#### Scenario: PVR-S4 marker constant is not present on orchestrator package object

- **GIVEN** `orchestrator` has been imported as a package
- **WHEN** the test inspects `dir(orchestrator)` and `getattr(orchestrator, "PIPELINE_VALIDATION_REQ", None)`
- **THEN** `PIPELINE_VALIDATION_REQ` is NOT in `dir(orchestrator)` and the
  `getattr` call returns `None` (the constant is reachable only via the
  fully-qualified submodule path)
