## ADDED Requirements

### Requirement: Sisyphus repo MUST publish a Flutter mobile self-hosted accept-env cookbook

The sisyphus repository SHALL ship a dedicated cookbook at
`docs/cookbook/ttpos-flutter-mobile-accept-env.md` that documents how a Flutter
source repository (such as `ZonEaseTech/ttpos-flutter`) can OPTIONALLY act as
its own integration repo for mobile accept testing. The cookbook MUST cover the
self-hosted scenario where no Android emulator is started and acceptance is
limited to HTTP-level validation against a mock backend stack. The cookbook
MUST be discoverable from `docs/cookbook/ttpos-flutter-makefile.md` §4.3 and
from `docs/integration-contracts.md` §4.2.2 via explicit hyperlinks.

#### Scenario: FMAC-S1 cookbook file exists at the published path

- **GIVEN** the sisyphus main branch after this REQ merges
- **WHEN** an engineer browses `docs/cookbook/`
- **THEN** the file `ttpos-flutter-mobile-accept-env.md` exists alongside `ttpos-flutter-makefile.md` and `ttpos-arch-lab-accept-env.md`

#### Scenario: FMAC-S2 cookbook is reachable from existing Flutter source-repo cookbook

- **GIVEN** an engineer reading `docs/cookbook/ttpos-flutter-makefile.md` §4.3
- **WHEN** they look for the full self-hosted integration recipe
- **THEN** §4.3 contains an inline link to `ttpos-flutter-mobile-accept-env.md` and §9 (relationship table) lists the new cookbook as a third column

#### Scenario: FMAC-S3 cookbook is reachable from integration-contracts §4.2.2

- **GIVEN** an engineer reading `docs/integration-contracts.md` §4.2.2 (docker-compose integration repo template)
- **WHEN** they look for the Flutter-specific self-hosted variant
- **THEN** the section's reference block includes an explicit link to `cookbook/ttpos-flutter-mobile-accept-env.md`

### Requirement: Cookbook MUST cover the decision tree, mock backend, Makefile recipe, and concurrency isolation

The cookbook `docs/cookbook/ttpos-flutter-mobile-accept-env.md` SHALL contain at
least the following nine top-level sections in order: §0 TL;DR, §1 decision
tree, §2 repo layout, §3 mock backend stack (`tests/docker-compose.accept.yml`),
§4 Makefile (`accept-env-up` / `accept-env-down`), §5 accept-agent endpoint
usage and scenario limits, §6 `SISYPHUS_NAMESPACE` concurrency isolation, §7
cross-cookbook relationship (with three-way comparison table), §8 troubleshooting
checklist, §9 anti-patterns. The cookbook MUST give a complete copyable Makefile
template for `accept-env-up` and `accept-env-down` that emits a final-line stdout
JSON containing `endpoint`, `namespace`, and the extension key
`stack: "flutter-self-hosted"`, consistent with `docs/integration-contracts.md`
§3 stdout JSON contract.

#### Scenario: FMAC-S4 cookbook decision tree distinguishes self-hosted from arch-lab

- **GIVEN** the cookbook §1 decision tree
- **WHEN** an engineer is evaluating whether to use the self-hosted recipe
- **THEN** §1 contains an explicit decision tree (or equivalent table) that recommends arch-lab when the team needs UI/emulator validation, and recommends the self-hosted recipe only when validation is limited to HTTP-level scenarios or no arch-lab is available

#### Scenario: FMAC-S5 Makefile template emits final-line endpoint JSON with stack key

- **GIVEN** the cookbook §4 Makefile template for `accept-env-up`
- **WHEN** copied verbatim and run after `SISYPHUS_NAMESPACE` is set
- **THEN** the final stdout line is a single JSON object containing `endpoint`, `namespace`, and `stack: "flutter-self-hosted"`; all progress logs (compose `--wait`, etc.) are written to stderr so they do not contaminate the final-line parser

#### Scenario: FMAC-S6 mock backend compose file uses dynamic host port and healthchecks

- **GIVEN** the cookbook §3 `tests/docker-compose.accept.yml` skeleton
- **WHEN** an engineer reads the backend service definition
- **THEN** the backend service exposes only `ports: ["8080"]` (container port, no fixed host port) and declares a `healthcheck`; the cookbook explains that this combination is required for `docker compose up --wait` to block until ready and for concurrent REQs to avoid host port collisions

#### Scenario: FMAC-S7 anti-patterns explicitly forbid embedding emulator in Flutter repo

- **GIVEN** the cookbook §9 anti-patterns section
- **WHEN** an engineer is tempted to add an Android emulator container to the Flutter repo
- **THEN** §9 explicitly states that emulator containers MUST NOT be added to the Flutter source repo and that UI / `flutter drive` validation belongs in the arch-lab integration repo, with a back-pointer to `ttpos-arch-lab-accept-env.md`
