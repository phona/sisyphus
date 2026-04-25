## ADDED Requirements

### Requirement: Flutter source repo MUST expose ttpos-ci standard Makefile targets

A Flutter source repo (such as `ZonEaseTech/ttpos-flutter`) that uses melos or
dart scripts internally MUST expose a root `Makefile` providing all six ttpos-ci
standard targets: `ci-env`, `ci-setup`, `ci-lint`, `ci-unit-test`,
`ci-integration-test`, and `ci-build`. The Makefile SHALL wrap existing melos /
dart CLI commands without duplicating internal build logic. Each target MUST exit
with code 0 on success and non-zero on failure, consistent with
`docs/integration-contracts.md §2.1`.

#### Scenario: FMC-S1 ci-lint runs flutter analyze regardless of BASE_REV value

- **GIVEN** the Flutter source repo Makefile is present at root
- **WHEN** sisyphus dev_cross_check calls `BASE_REV=<sha> make ci-lint`
- **THEN** `flutter analyze --no-pub` runs on the full project and exits 0 when no issues found; the BASE_REV value is accepted but a full scan is performed (flutter analyze has no --new-from-rev equivalent)

#### Scenario: FMC-S2 ci-unit-test succeeds without device or emulator

- **GIVEN** the Flutter source repo has unit/widget tests under `test/`
- **WHEN** sisyphus staging-test calls `make ci-unit-test` inside runner pod (no emulator)
- **THEN** `melos run test:unit` (or equivalent `flutter test test/ --no-pub`) completes and exits 0; no Android emulator or connected device is required

#### Scenario: FMC-S3 ci-integration-test exits 0 when not configured

- **GIVEN** the Flutter source repo uses the default empty implementation
- **WHEN** sisyphus staging-test calls `make ci-integration-test`
- **THEN** the target exits 0 (sisyphus treats exit 0 as pass; the cookbook documents this as the recommended default for Flutter repos without docker compose backend tests)

#### Scenario: FMC-S4 Flutter source repo is cloned to convention path for accept stage

- **GIVEN** `ZonEaseTech/ttpos-flutter` is listed in `involved_repos` of the BKD intent issue
- **WHEN** sisyphus start_analyze dispatches the analyze-agent
- **THEN** the repo is cloned to `/workspace/source/ttpos-flutter/` in the runner pod; the arch-lab integration repo's `apk/build.sh` can reference `TTPOS_FLUTTER_REPO=/workspace/source/ttpos-flutter` to build the APK during accept stage without the Flutter source repo providing `accept-env-up`

### Requirement: Cookbook MUST document the relationship between Flutter source repo and arch-lab integration repo

The sisyphus cookbook for Flutter source repos MUST clearly explain the division
of responsibilities between the Flutter source repo (source role: provides ci-*
targets) and the arch-lab integration repo (integration role: provides
accept-env-up/down with emulator). The cookbook SHALL state that Flutter source
repos MUST NOT implement `accept-env-up` / `accept-env-down` unless they explicitly
act as integration repos, and SHALL reference `docs/cookbook/ttpos-arch-lab-accept-env.md`
for the full mobile e2e lab setup.

#### Scenario: FMC-S5 cookbook cross-references arch-lab cookbook for accept stage

- **GIVEN** an engineer reading docs/cookbook/ttpos-flutter-makefile.md
- **WHEN** they reach section 4 (accept-env 契約参与方式)
- **THEN** the cookbook explicitly states that Flutter source repos do not implement accept-env-up/down and references ttpos-arch-lab-accept-env.md for the full emulator+APK lab setup
