## MODIFIED Requirements

### Requirement: fixer round cap default MUST be 2

The orchestrator's `Settings` class MUST set `fixer_round_cap` default value to 2.
This controls the maximum number of fixer rounds before sisyphus automatically
escalates a REQ with reason `fixer-round-cap`. A lower cap reduces token waste
from low-quality fix loops while preserving enough headroom for genuine
iterative fixes.

#### Scenario: FRC-S1 default cap is 2

- **GIVEN** a fresh `Settings()` instance with no environment overrides
- **WHEN** `settings.fixer_round_cap` is read
- **THEN** the value MUST equal 2

#### Scenario: FRC-S2 cap escalation triggers at round 3 with default

- **GIVEN** a REQ with `ctx.fixer_round = 2` (already completed 2 fixer rounds)
- **WHEN** `start_fixer` is called for the 3rd time with default cap
- **THEN** no fixer issue is created
- **AND** the action MUST escalate with `escalated_reason = "fixer-round-cap"`
- **AND** `ctx.fixer_round_cap_hit` MUST equal 2

#### Scenario: FRC-S3 round-counter test is isolated from default changes

- **GIVEN** the existing round-counter increment test in `test_verifier.py`
- **WHEN** the default cap changes from 5 to 2
- **THEN** the test MUST still pass via explicit monkeypatch to cap=5
- **AND** the test's behavioral assertion (increment counter and create fixer)
  MUST NOT be affected by the new default

#### Scenario: FRC-S4 default cap test reflects new value

- **GIVEN** the default cap test `test_start_fixer_caps_at_default_2`
- **WHEN** `start_fixer` is called with `ctx.fixer_round = 2`
- **THEN** no fixer issue is created
- **AND** the action MUST escalate with `escalated_reason = "fixer-round-cap"`
- **AND** `ctx.fixer_round_cap_hit` MUST equal 2

## ADDED Requirements

### Requirement: observability MUST track fixer round distribution and cap impact

The observability queries directory MUST expose two new SQL queries (Q19 and Q20)
that enable data-driven evaluation of the cap change. Q19 MUST aggregate fixer
round counts per REQ over the last 30 days, showing distribution percentages and
escalation rates per round count. Q20 MUST aggregate verifier decision actions
(pass/fix/escalate) over the last 30 days, showing total counts and percentages
for each decision type.

#### Scenario: FRC-S5 Q19 returns fixer round distribution with escalate rates

- **GIVEN** the `req_state` table contains REQs with varying `fixer_round` values
  in the last 30 days
- **WHEN** Q19 SQL is executed
- **THEN** each row MUST contain `fixer_round`, `n_reqs`, `pct`, `n_escalated`,
  and `escalate_rate`
- **AND** rows MUST be ordered by `fixer_round` ascending

### Requirement: documentation MUST reflect the new cap value

The `docs/IMPACT-REPORT.md` and `docs/user-feedback-loop.md` documents MUST
update all hard-coded references to the fixer round cap from 5 to 2, ensuring
readers see the current default value without confusion.

#### Scenario: FRC-S6 documentation references match the default cap

- **GIVEN** `docs/IMPACT-REPORT.md` and `docs/user-feedback-loop.md` are read
- **WHEN** searching for the hard-coded cap description (e.g. "硬上限 N 轮")
- **THEN** the text MUST describe a cap of 2 rounds, not 5
- **AND** no stale reference to 5 rounds as the default cap MUST remain
