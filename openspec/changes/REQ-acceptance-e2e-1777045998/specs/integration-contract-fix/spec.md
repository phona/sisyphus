## CHANGED Requirements

### Requirement: integration-contracts.md uses ci-accept-env-up and ci-accept-env-down consistently

The document `docs/integration-contracts.md` SHALL use `ci-accept-env-up` and `ci-accept-env-down` as the canonical target names for the integration repo acceptance contract, matching what `create_accept.py` and `teardown_accept_env.py` actually call. The document MUST NOT contain the obsolete target names `accept-up` or `accept-down` in any normative context.

The document SHALL note that sisyphus runner pods provide Docker DinD but NOT kubectl or helm, and the §4.2 integration repo template MUST use Docker Compose rather than Helm.

#### Scenario: SISP-S1 integration repo author reads correct target name from docs

- **GIVEN** a developer implementing the integration repo contract reads §2.3 of integration-contracts.md
- **WHEN** they implement the acceptance targets in their Makefile
- **THEN** they implement `ci-accept-env-up` and `ci-accept-env-down` (matching what sisyphus calls)
- **AND** they do not implement `accept-up` or `accept-down` (which would never be called)

#### Scenario: SISP-S2 §4.2 template uses Docker Compose

- **GIVEN** a developer reads the §4.2 Minimal Makefile Template for integration repos
- **WHEN** they follow the template
- **THEN** the resulting Makefile uses Docker Compose (not kubectl/helm)
- **AND** the resulting Makefile works in a sisyphus runner pod environment
