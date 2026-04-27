## ADDED Requirements

### Requirement: thanatos exposes a stdio MCP server entrypoint

The thanatos package SHALL expose a stdio MCP server entrypoint reachable as
`python -m thanatos.server` (and `python -m thanatos`). The server MUST register
exactly three tools — `run_scenario`, `run_all`, and `recall` — when its
`list_tools` handler is invoked. Each tool's `inputSchema` MUST declare the
`required` parameters listed in
[`contract.spec.yaml`](./contract.spec.yaml).

The Dockerfile SHALL set `python -m thanatos.server` as its entrypoint so that
`docker run thanatos:dev` boots the server with no extra arguments.

#### Scenario: THAN-S1 server module starts and registers three tools

- **GIVEN** the thanatos package is installed and `python -m thanatos.server`
  is invoked
- **WHEN** an MCP client sends `initialize` followed by `tools/list`
- **THEN** the response lists exactly the three tools `run_scenario`,
  `run_all`, `recall`
- **AND** every tool's `inputSchema` declares the `required` parameters
  documented in `contract.spec.yaml`

### Requirement: scenario parser supports gherkin code-block and bullet formats

The `thanatos.scenario` module SHALL expose `parse_spec_text(text)` and
`parse_spec_file(path)` functions that scan markdown for `#### Scenario:`
headings and return a list of `ParsedScenario` records in source order. The
parser MUST accept two mutually-exclusive step formats inside a single block:

- a fenced code block whose info string is `gherkin`
- markdown bullets of the form `- **GIVEN** ...` (case-insensitive on the
  keyword)

Each `ParsedScenario` MUST contain `scenario_id`, `description`, `given`,
`when`, `then`, and `source_format`. `And`/`But` lines MUST extend whichever
bucket (`given` / `when` / `then`) was last filled. The parser MUST raise
`ScenarioFormatError` when a block mixes gherkin and bullet steps, or when two
blocks share an id. The parser MUST raise `EmptyScenarioError` when a block
contains no recognisable GIVEN/WHEN/THEN steps. `#### Scenario:` headings that
appear inside a fenced code block (any info string other than `gherkin`) MUST
be ignored.

#### Scenario: THAN-S2 gherkin code block parses into structured fields

- **GIVEN** a markdown document containing `#### Scenario: REQ-1004-S1` followed
  by a ` ```gherkin` fence with `Given foo`, `When bar`, `Then baz`
- **WHEN** `parse_spec_text` is invoked
- **THEN** the returned `ParsedScenario` has `scenario_id="REQ-1004-S1"`,
  `given=["foo"]`, `when=["bar"]`, `then=["baz"]`, and `source_format="gherkin"`

#### Scenario: THAN-S3 bullet-format scenario parses with multiple GIVEN entries

- **GIVEN** a markdown block with `#### Scenario: THAN-multi`
- **AND** four bullets: two `- **GIVEN** ...`, one `- **WHEN** ...`, one
  `- **THEN** ...`
- **WHEN** `parse_spec_text` is invoked
- **THEN** the returned `ParsedScenario.given` list has length 2 and
  `source_format == "bullet"`

#### Scenario: THAN-S4 mixed gherkin and bullet inside one block raises ScenarioFormatError

- **GIVEN** a `#### Scenario: MIX-1` block that contains both a `- **GIVEN** ...`
  bullet and a ` ```gherkin` fence with steps
- **WHEN** `parse_spec_text` is invoked
- **THEN** the parser raises `ScenarioFormatError` with a message containing
  `mixes gherkin`

### Requirement: Driver Protocol defines a five-method async contract

The `thanatos.drivers.base` module SHALL declare a `Driver` Protocol whose
methods are exactly `preflight(endpoint)`, `observe()`, `act(step)`,
`assert_(step)`, and `capture_evidence()`, all async. The Protocol MUST carry a
class-level `name: str` attribute. M0 SHALL ship three concrete driver classes
— `PlaywrightDriver`, `AdbDriver`, `HttpDriver` — each importable from
`thanatos.drivers`. Every method on every M0 driver class MUST raise
`NotImplementedError("M0: scaffold only")`. No method body MAY contain real
runtime logic in M0.

#### Scenario: THAN-S5 each driver class raises NotImplementedError on every method

- **GIVEN** an instance of `PlaywrightDriver`, `AdbDriver`, or `HttpDriver`
- **WHEN** any of `preflight`, `observe`, `act`, `assert_`, or
  `capture_evidence` is awaited
- **THEN** `NotImplementedError` is raised with the message
  `"M0: scaffold only"`

### Requirement: helm chart renders three driver-conditional pod topologies

The chart at `deploy/charts/thanatos/` SHALL render successfully (exit 0) under
`helm template . --set driver=<value>` for `<value> ∈ {playwright, adb, http}`.
The rendered Deployment MUST contain exactly two containers (`redroid` and
`thanatos`) when `driver=adb`, and exactly one container (`thanatos`) when
`driver=playwright` or `driver=http`. The `redroid` container MUST be marked
`securityContext.privileged: true` and expose container port `5555`. The
`thanatos` container MUST set `command: ["python", "-m", "thanatos.server"]` in
all three modes. Any `driver` value outside `{playwright, adb, http}` MUST cause
`helm template` to fail with a non-zero exit code and an error message naming
the allowed values.

#### Scenario: THAN-S6 driver=adb emits a two-container Pod with redroid + thanatos

- **GIVEN** the chart at `deploy/charts/thanatos/`
- **WHEN** `helm template . --set driver=adb` is invoked
- **THEN** the rendered manifest contains a Deployment whose pod spec lists
  exactly two containers named `redroid` and `thanatos`
- **AND** the `redroid` container has `securityContext.privileged: true`
- **AND** the `thanatos` container has `ADB_SERVER_ADDR=localhost:5555` in its
  env

#### Scenario: THAN-S7 driver=playwright emits a single-container thanatos Pod

- **GIVEN** the chart at `deploy/charts/thanatos/`
- **WHEN** `helm template . --set driver=playwright` is invoked
- **THEN** the rendered manifest contains a Deployment whose pod spec lists
  exactly one container named `thanatos`
- **AND** the `thanatos` container's `command` is
  `["python", "-m", "thanatos.server"]`
