# sisyphus-testkit skill

**Bootstrap integration test infrastructure for a new Go service repo.**

## When to trigger

- User says "set up integration tests for this repo"
- User asks to add `accept-env-up` / `accept-env-down` targets
- User wants a Docker Compose lab for CI integration tests
- User needs coverage path fixing for SonarQube

## What this skill does

Renders the templates in `templates/` into the target repo, wires up Makefile targets,
and documents the test conventions from `references/practices.md`.

## Quick start

```bash
# 1. Install the skill (run once from sisyphus repo root)
make skill-install

# 2. In the target business repo, render templates
# Copy templates/docker-compose.yml.tmpl → tests/docker-compose.yml  (edit {{VARS}})
# Copy templates/Makefile.snippet       → append to repo Makefile
# Copy templates/tests-README.md.tmpl  → tests/README.md

# 3. Import testkit in your test fixtures
# go get github.com/phona/sisyphus/testkit@latest
```

## Template variables

| Variable | Description |
|---|---|
| `{{SERVICE_NAME}}` | Your service binary name (e.g. `myservice`) |
| `{{SERVICE_PORT}}` | HTTP port exposed by the service |
| `{{PROTO_PATH}}` | Path to proto files relative to repo root |
| `{{COVERAGE_BINARY}}` | Path to the coverage-instrumented binary |

## Red line

Never commit business names (service-specific table/schema names, UUIDs, etc.)
into testkit source. Run `make test-no-business-leak` from `testkit/` to verify.
