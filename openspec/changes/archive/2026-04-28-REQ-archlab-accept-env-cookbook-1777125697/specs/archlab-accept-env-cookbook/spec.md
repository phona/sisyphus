# archlab-accept-env-cookbook Specification

## ADDED Requirements

### Requirement: 仓 MUST 提供 docs/cookbook/ttpos-arch-lab-accept-env.md cookbook 文件

The sisyphus repo SHALL ship a cookbook document at the canonical path
`docs/cookbook/ttpos-arch-lab-accept-env.md`. The file MUST exist on the
`feat/REQ-archlab-accept-env-cookbook-1777125697` branch and serve as the
copy-pasteable implementation recipe for any integration repo whose accept
env combines a backend `docker compose` stack, a headless Android emulator
container, an APK build / install step, and a multi-key endpoint JSON line.
This cookbook MUST live alongside the canonical contract document
`docs/integration-contracts.md` (not inline) so that the contract stays short
and the recipe can grow without bloating the contract.

#### Scenario: ARCHLAB-S1 cookbook file exists at docs/cookbook/ttpos-arch-lab-accept-env.md

- **GIVEN** the working tree at the head of `feat/REQ-archlab-accept-env-cookbook-1777125697`
- **WHEN** `test -f docs/cookbook/ttpos-arch-lab-accept-env.md` runs
- **THEN** the command exits 0 (file is present) and the file's first non-empty
  line is a level-1 heading containing the words `Cookbook` and `ttpos-arch-lab`

### Requirement: cookbook MUST 覆盖 5 步 recipe + 9 节结构

The cookbook `docs/cookbook/ttpos-arch-lab-accept-env.md` SHALL document a
five-step `accept-env-up` recipe (1. backend compose up; 2. Android emulator
container up; 3. APK build; 4. APK install via adb; 5. emit endpoint JSON on
the last stdout line) and MUST organize the content into nine sections so that
readers can navigate by topic: §0 TL;DR, §1 repo layout, §2 backend compose,
§3 Android emulator container, §4 APK build, §5 endpoint JSON contract, §6
complete Makefile sample, §7 accept-agent prompt integration, §8 troubleshooting
checklist, §9 relationship to existing helm / compose templates. Each section
MUST be discoverable as a level-2 markdown heading.

#### Scenario: ARCHLAB-S2 nine top-level sections present in cookbook

- **GIVEN** the cookbook file `docs/cookbook/ttpos-arch-lab-accept-env.md`
- **WHEN** `grep -E '^## ' docs/cookbook/ttpos-arch-lab-accept-env.md` runs
- **THEN** the output contains at least nine `## ` level-2 headings whose
  numbering covers `## 0`, `## 1`, ..., `## 9` (or equivalent numeric prefixes
  that map to the nine sections enumerated in this requirement)

#### Scenario: ARCHLAB-S3 TL;DR section enumerates the five recipe steps

- **GIVEN** the cookbook §0 TL;DR section
- **WHEN** the section body is read
- **THEN** the prose lists the five env-up steps in order: backend compose up,
  emulator container up, APK build, APK install via adb, emit endpoint JSON;
  AND the section explicitly names that the JSON is on the **last** stdout line

### Requirement: cookbook §3 MUST 提供 KVM-fallback 软件模拟方案

The cookbook §3 (Android emulator container) section MUST provide a
software-rendering fallback for environments where `/dev/kvm` is not available
(the default state of the K3s `sisyphus-runners` namespace on vm-node04).
The fallback SHALL take the form of an `EMULATOR_ARGS` declaration that
includes the literal flags `-no-accel` and `-gpu swiftshader_indirect`, and
the section MUST explicitly call out that the emulator container's healthcheck
`start_period` SHALL be increased (roughly 180 s order-of-magnitude) when
running without KVM, so that boot can finish before compose `--wait` declares
failure. Without this fallback, every new mobile-lab integration repo would
need to negotiate KVM passthrough with infra before any accept-env recipe can
run, which contradicts the cookbook's self-contained design goal.

#### Scenario: ARCHLAB-S4 §3 names -no-accel and swiftshader_indirect as fallback

- **GIVEN** the cookbook §3 emulator container section
- **WHEN** the section body is scanned
- **THEN** the body contains both the literal token `-no-accel` and the literal
  token `swiftshader_indirect`, AND the body explicitly mentions adjusting
  `start_period` (or healthcheck timing) for the software-rendering path

### Requirement: cookbook §5 MUST 描述 endpoint JSON 多键扩展（endpoint 必需 + adb / apk_package 扩展）

The cookbook §5 (endpoint JSON contract) section MUST document a multi-key
endpoint JSON shape where the field `endpoint` remains the single required
key (preserving the `docs/integration-contracts.md` §3 contract that sisyphus
orchestrator parses) and where `adb` and `apk_package` are documented as
optional extension keys consumed by the accept-agent prompt context (not by
the orchestrator). The section SHALL state that absence of an extension key
MUST cause graceful degradation of the relevant scenario class (e.g. UI
scenarios skipped when `adb` is missing) but MUST NOT cause the orchestrator
to fail accept env-up, so that this cookbook remains backward-compatible with
existing integration repos that emit only `endpoint`.

#### Scenario: ARCHLAB-S5 §5 declares endpoint required and adb / apk_package optional

- **GIVEN** the cookbook §5 endpoint JSON contract section
- **WHEN** the section body is read
- **THEN** the body contains all three literal keys: `"endpoint"`, `"adb"`,
  `"apk_package"`, AND the body labels `endpoint` as required (with a marker
  like ✅ or the word "必需" / "required") and `adb` / `apk_package` as
  optional extensions (with a marker like "扩展" / "optional" / "extension")

### Requirement: cookbook §6 Makefile 范本 MUST 严格分流 stderr / stdout

The cookbook §6 (complete Makefile sample) section MUST provide a
copy-pasteable Makefile block that contains both `accept-env-up` and
`accept-env-down` recipes, AND the `accept-env-up` recipe SHALL route
every progress / log line to stderr (via `>&2` redirection or `@echo ... >&2`)
and reserve stdout for the final `printf` of the endpoint JSON only. The
recipe's terminating `printf` MUST emit a JSON object whose keys cover at
minimum `endpoint`, `adb`, `apk_package`, `namespace`, with a trailing `\n`
so that `result.stdout.splitlines()` reverse-iteration in
`actions/create_accept.py` resolves the JSON line. The `accept-env-down`
recipe MUST be best-effort idempotent: every teardown command SHALL be
prefixed with the make `-` ignore-error sigil OR appended with `|| true`, so
re-running after a partial failure exits 0.

#### Scenario: ARCHLAB-S6 §6 Makefile up emits endpoint JSON on stdout last line

- **GIVEN** the cookbook §6 Makefile sample
- **WHEN** the `accept-env-up` recipe body is parsed
- **THEN** the recipe's last command is a `printf` (or `@printf`) call whose
  format string contains the literals `"endpoint"`, `"adb"`, `"apk_package"`,
  `"namespace"`, AND the format string ends with `\n`

#### Scenario: ARCHLAB-S7 §6 Makefile down is best-effort idempotent

- **GIVEN** the cookbook §6 Makefile sample
- **WHEN** the `accept-env-down` recipe body is parsed
- **THEN** every primary teardown command (compose down for backend, compose
  down for emulator, adb kill-server, port-file rm) is either prefixed with
  `-` (make ignore-error) or appended with `|| true`, so a missing prior stack
  does not propagate non-zero exit

### Requirement: cookbook MUST 被 integration-contracts.md / README.md / CLAUDE.md 索引交叉链接

`docs/integration-contracts.md` SHALL link to the cookbook from §4.2.2
(docker-compose template) so that integrators reading the contract document
discover the mobile-lab recipe at the relevant decision point.
`README.md` and `CLAUDE.md` SHALL each add an entry to their respective
"文档索引" tables pointing at `docs/cookbook/` (the directory entry) so that
both human onboarding and AI session bootstrap surfaces expose the cookbook
without requiring readers to grep. The system MUST NOT publish the cookbook
file in isolation: an unlinked cookbook is invisible to the population that
needs it most (lab teams onboarding for the first time).

#### Scenario: ARCHLAB-S8 integration-contracts.md §4.2.2 links to cookbook

- **GIVEN** `docs/integration-contracts.md`
- **WHEN** the §4.2.2 section's body is scanned
- **THEN** the section contains a markdown link whose target resolves to
  `cookbook/ttpos-arch-lab-accept-env.md` (relative path from `docs/`) or
  `docs/cookbook/ttpos-arch-lab-accept-env.md` (absolute from repo root)

#### Scenario: ARCHLAB-S9 README.md 文档索引 lists docs/cookbook/

- **GIVEN** `README.md`
- **WHEN** the "文档索引" table is parsed
- **THEN** at least one row's first column links to `docs/cookbook/` (the
  directory) AND the row's second column mentions `accept-env-up` or
  `accept-env` to disambiguate it from unrelated cookbook content
