# Tasks — REQ-docs-drift-audit-1777220568

## Stage: contract / spec
- [x] `specs/docs/spec.md` — declare requirement that doc claims about counts,
      file paths, and stage names must match current `state.py` / `actions/` /
      `prompts/` / `migrations/` / `observability/queries/sisyphus/` reality

## Stage: implementation (docs only)
- [x] README.md — fix Metabase count, action references, migration list, add
      challenger to architecture diagram, drop fanout_specs / create_dev refs
- [x] CLAUDE.md — fix counts (actions, verifier templates, migrations,
      state/event counts), fix migration filenames, add challenger to happy
      path flow, drop "M3 admission / M11" checker label
- [x] docs/architecture.md — drop spec-agent / dev-agent role rows, drop
      spec.md.j2 / dev.md.j2 refs, add CHALLENGER to mermaid + §6 stage table,
      header version bump, verifier count fix
- [x] docs/state-machine.md — add CHALLENGER state + events, fix counts, update
      mermaid, drop stale dev.done event
- [x] docs/prompts.md — drop spec.md.j2 / dev.md.j2 / fanout_specs / create_dev
      references, list all 14 verifier stage_trigger pairs, add challenger.md.j2
      to stage agent table
- [x] docs/api-tag-management-spec.md — replace Apifox content with actual BKD
      issue tag spec (derived from router.py)
- [x] observability/sisyphus-dashboard.md — fix "13 question" → "18", document
      Q17, mention migrations 0006/0007
- [x] docs/observability.md — bump migration range 0001~0005 → 0001~0007

## Stage: PR
- [x] git push feat/REQ-docs-drift-audit-1777220568
- [x] gh pr create
