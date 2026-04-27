## ADDED Requirements

### Requirement: thanatos exposes a shell CLI mirroring the MCP tool surface

The `thanatos` package SHALL expose three argparse subcommands `run-scenario`,
`run-all`, and `recall` reachable as `python -m thanatos <subcmd> --...`. Each
subcommand MUST dispatch to the same `thanatos.runner` entrypoint that the MCP
stdio server already uses (`run_scenario` / `run_all` / `recall`), so the MCP
and CLI surfaces stay logic-identical. The CLI MUST print exactly one JSON
document on stdout (object for `run-scenario`, array for the other two), write
human-readable diagnostics on stderr, and use exit code `0` on a successful
dispatch (regardless of `pass` boolean), `2` on argparse / validation errors,
and `3` when the runner raises. `python -m thanatos` (no args) MUST keep the
M0 behaviour of booting the MCP stdio server so the helm chart's
`command: ["python", "-m", "thanatos.server"]` invocation continues to work.

#### Scenario: TM1-S1 run-scenario subcommand prints JSON and exits 0

- **GIVEN** a valid `.thanatos/skill.yaml` (driver=playwright) and a spec.md
  containing `#### Scenario: S1` with one GIVEN/WHEN/THEN bullet block
- **WHEN** the user runs `python -m thanatos run-scenario --skill <path>
  --spec <path> --scenario-id S1 --endpoint http://lab.local:8080`
- **THEN** stdout contains exactly one JSON object whose keys include
  `scenario_id`, `pass`, `kb_updates`, `failure_hint`
- **AND** the process exits with code `0`
- **AND** because M0 drivers raise `NotImplementedError`, `pass` is `false`
  and `failure_hint == "M0: thanatos scaffold only, drivers not implemented"`

#### Scenario: TM1-S2 missing required argument exits 2 and prints argparse usage

- **GIVEN** the thanatos package is installed
- **WHEN** the user runs `python -m thanatos run-scenario --skill x --spec y`
  (omitting `--scenario-id` and `--endpoint`)
- **THEN** the process exits with code `2`
- **AND** stderr contains the string `the following arguments are required`

### Requirement: accept stage prompt routes scenarios through thanatos when opted-in

The accept stage prompt template `accept.md.j2` SHALL teach the accept-agent a two-branch flow keyed on whether any source repo under `/workspace/source/*/.thanatos/skill.yaml` exists AND whether `accept_env.thanatos_pod` is present in the endpoint JSON.

The thanatos branch MUST invoke the thanatos CLI per scenario via
`kubectl -n <namespace> exec <thanatos_pod> -- python -m thanatos
run-scenario --skill ... --spec ... --scenario-id ... --endpoint ...`,
aggregate `kb_updates[]` across scenarios, apply patch/append updates to
its own working-tree source repo, and `git push origin feat/<REQ>`. The
curl fallback branch MUST remain documented in the same template so
sisyphus self-dogfood (no UI, no `.thanatos/skill.yaml`) keeps working.
The thanatos branch MUST treat "0 scenarios collected" as `result:fail`
to avoid silent vacuous-pass when spec.md ships without scenario blocks.

#### Scenario: TM1-S3 thanatos pod field present produces thanatos invocation block

- **GIVEN** `accept_env = {"endpoint":"http://e","namespace":"accept-req-417",
  "thanatos_pod":"thanatos-abc"}` and a `.thanatos/skill.yaml` is present in
  the source repo
- **WHEN** `accept.md.j2` is rendered with this context
- **THEN** the rendered prompt contains the literal substring
  `python -m thanatos run-scenario`
- **AND** it contains the substring `kb_updates`
- **AND** it contains the substring `git push origin feat/`

#### Scenario: TM1-S4 curl fallback block always rendered

- **GIVEN** the rendered `accept.md.j2` with any context (with or without
  `accept_env.thanatos_pod`)
- **WHEN** searched for the curl fallback heading
- **THEN** the rendered prompt contains the substring
  `curl -sf` (the legacy curl example) and a section heading mentioning
  fallback or "无 thanatos" so reviewers see both paths documented

### Requirement: integration-contracts documents .thanatos opt-in and endpoint JSON ext

[`docs/integration-contracts.md`](../../../docs/integration-contracts.md) SHALL
contain a new section that documents the `.thanatos/` directory contract:

- `<source-repo>/.thanatos/skill.yaml` is OPTIONAL; presence opts the repo
  into thanatos-driven acceptance.
- The skill.yaml schema MUST reference the canonical
  [`thanatos M0 contract.spec.yaml`](../../REQ-thanatos-m0-scaffold-v6-1777283112/specs/thanatos/contract.spec.yaml).
- The business repo's `make accept-env-up` MUST emit a `thanatos_pod` key in
  its stdout-tail endpoint JSON when thanatos is co-installed; absent ⇒
  curl fallback.
- `.thanatos/anchors.md` / `.thanatos/flows.md` / `.thanatos/pitfalls.md` are
  written by thanatos and committed to `feat/<REQ>` by the accept-agent.

#### Scenario: TM1-S5 integration-contracts mentions .thanatos opt-in section

- **GIVEN** the file `docs/integration-contracts.md`
- **WHEN** the file is searched for the substring `.thanatos`
- **THEN** at least one occurrence is found inside a section whose heading
  contains the word `Thanatos` or `thanatos`
- **AND** the same section mentions the substring `thanatos_pod`

### Requirement: arch-lab cookbook shows helm install thanatos in accept-env-up

The arch-lab cookbook at `docs/cookbook/ttpos-arch-lab-accept-env.md` SHALL contain a section that demonstrates installing the thanatos chart alongside the lab chart inside `make accept-env-up`.

The example MUST show `helm upgrade --install thanatos` running after the
lab chart install, and MUST show how the cookbook discovers the thanatos
pod name and folds it into the endpoint JSON's `thanatos_pod` field so
accept-agent can reach the harness without extra discovery round-trips.

#### Scenario: TM1-S6 cookbook demonstrates thanatos helm install + thanatos_pod emission

- **GIVEN** the file `docs/cookbook/ttpos-arch-lab-accept-env.md`
- **WHEN** the file is searched for the substring `helm` followed by any text
  followed by `thanatos`
- **THEN** at least one occurrence is found
- **AND** the same file contains the substring `thanatos_pod` showing how
  the endpoint JSON gains the field
