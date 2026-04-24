# OpenSpec Agents Guide

## Repository: sisyphus

### Tech Stack
- Python orchestrator (asyncio, aiohttp)
- Kubernetes for runner pods
- BKD for issue/tag management

### Key Conventions
- Integration contract targets: `ci-accept-env-up` / `ci-accept-env-down` (NOT `accept-up` / `accept-down`)
- Runner pods have Docker DinD but NO kubectl/helm — integration repos must use Docker Compose
- Scenario IDs use namespace `SISP-S<N>` (e.g., `SISP-S1`)

### Spec Guidelines
- Specs describe orchestrator behavior and integration contracts
- `docs/integration-contracts.md` is the authoritative reference for business repo authors
