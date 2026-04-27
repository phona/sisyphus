# Design — BKD intent body status block

## Constraints (from probing the running BKD REST)

- BKD REST issue object only PATCHes `title` / `tags` / `statusId`. `description`
  and `prompt` PATCH bodies are silently dropped (verified against
  `localhost:3000/api/projects/.../issues/{id}` — server returns `success:true`
  but the field stays unchanged).
- No `/notes` / `/comments` / `/messages` write endpoints (all 404).
- `follow_up_issue` POSTs a new prompt to the agent and re-triggers the session,
  so it cannot be used to "post a passive status pane" — it would re-fire the
  agent every transition.
- The only durable, agent-visible text we control is the prompt body that
  `start_intake` / `start_analyze` / `start_analyze_with_finalized_intent` follow-up
  once at dispatch.

→ The status block must live inside that prompt body. We add it once, at
dispatch time, with whatever facts are in hand. We do *not* try to keep it
"live"; the orchestrator does not re-PATCH the prompt later (BKD doesn't allow
it anyway).

## Where the block lives in the template

```jinja2
{% if status_block -%}
{% include "_shared/status_block.md.j2" %}

─────────

{% endif -%}
{% include "_shared/tools_whitelist.md.j2" %}
... (rest unchanged)
```

The `{% if status_block %}` guard means callers that don't pass the kwarg
(verifier prompts, bugfix prompt, challenger prompt — out of scope this REQ) get
a no-op include and zero diff in their rendered output. Only `intake.md.j2` and
`analyze.md.j2` get a populated block this REQ. Every other template in
`prompts/` already has a `{% include %}` chain at the top, so this slot is a
natural extension.

The separator line `─────────` and the trailing horizontal rule `---` inside the
partial are deliberate: they visually delimit the block from the constraint
walls below it, both in the BKD UI rendering and in the agent's reading order.

## Schema of the partial's context

Single dict variable `status_block` with these keys:

| Key | Type | Required | Renders as row |
|---|---|---|---|
| `req_id` | `str` | yes | `REQ` |
| `stage` | `str` | yes | `Stage` |
| `bkd_intent_issue_url` | `str \| None` | no | `BKD intent issue` |
| `cloned_repos` | `list[str] \| None` | no | `Pre-cloned repos` |
| `pr_links_inline` | `str \| None` | no | `Linked PRs` |

`Branch` and `Runner Pod` rows are computed inline from `req_id` (they're 1:1
deterministic — sisyphus convention is `feat/<req_id>` and `runner-<req_id|lower>`).

`pr_links_inline` is **pre-rendered by Python** via the existing
`links.format_pr_links_inline(pr_urls)` helper (returns a comma-separated string
of `[repo#NN](url)` markdown). Pre-rendering keeps the Jinja2 template simple and
avoids duplicating the `_PR_NUMBER_RE` regex logic.

## The helper

```python
# orchestrator/src/orchestrator/prompts/status_block.py
from __future__ import annotations

from .. import links


def build_status_block_ctx(
    *,
    req_id: str,
    stage: str,
    bkd_intent_issue_url: str | None = None,
    cloned_repos: list[str] | None = None,
    pr_urls: dict[str, str] | None = None,
) -> dict:
    """Assemble the dict consumed by ``_shared/status_block.md.j2``.

    Falsy/empty values become None so the template's ``{% if %}`` guards
    suppress the corresponding row cleanly.
    """
    return {
        "req_id": req_id,
        "stage": stage,
        "bkd_intent_issue_url": (bkd_intent_issue_url or None),
        "cloned_repos": (list(cloned_repos) if cloned_repos else None),
        "pr_links_inline": (links.format_pr_links_inline(pr_urls) or None),
    }
```

`format_pr_links_inline` already returns `""` on empty/None input (from
`links.py`), and `or None` collapses that to `None` so the template skips the
row.

## Wiring into start_*.py

Three callsites change:

1. `start_intake.py` — adds `bkd_intent_issue_url` (currently not passed) +
   `status_block=build_status_block_ctx(...)`. No `cloned_repos`, no `pr_urls`
   at intake.
2. `start_analyze.py` — adds `status_block=build_status_block_ctx(...)`,
   reusing the already-passed `bkd_intent_issue_url`, `cloned_repos`, plus
   `pr_urls=ctx.get("pr_urls")` if present (re-entry from later stages may have
   discovered them).
3. `start_analyze_with_finalized_intent.py` — same as `start_analyze`. It
   currently does NOT pass `bkd_intent_issue_url`; this REQ adds it (it's a
   one-line `links.bkd_issue_url(proj, issue.id)` call, parity with the direct
   path).

Existing `cloned_repos` / `bkd_intent_issue_url` / footer-block kwargs on
`analyze.md.j2` are left untouched — no behavioural change for code paths that
don't consume the new block.

## Why not a Python pre-rendered string?

We considered: `prompt = render(...) + "\n\n" + render_status_block(...)`. Two
reasons against:

1. The block belongs at the *top* of the prompt, not concatenated at the end.
   Inserting a string into a pre-rendered template is fragile; using a Jinja2
   `{% include %}` slot is the project's idiomatic pattern (already used for
   `tools_whitelist.md.j2` etc.).
2. Per-template control over the block's position survives future template
   edits. If we later add a status block to `staging_test.md.j2` we just add the
   `{% include %}` at the desired line, no Python concatenation glue.

## Tests

New file `orchestrator/tests/test_prompts_status_block.py`:

- BISB-S1: render `_shared/status_block.md.j2` directly with all-fields-present
  context → table contains 7 rows in the documented column order.
- BISB-S2: render with only `req_id` + `stage` → table contains exactly the 4
  always-on rows; no empty cells; no row for omitted optional fields.
- BISB-S3: render `analyze.md.j2` with `status_block=...` and
  `cloned_repos=[...]` → first non-blank section is `## REQ Status`, which
  appears *before* `## 工具白名单`.
- BISB-S4: render `intake.md.j2` with `status_block=...` (no cloned_repos) →
  same as S3 but Pre-cloned repos row absent.
- BISB-S5: `pr_links_inline` row formats `pr_urls` dict via
  `links.format_pr_links_inline` (clickable per-repo markdown links, comma
  separated).
- BISB-S6: `build_status_block_ctx` collapses empty/None inputs to None so the
  template's `{% if %}` row guards drop the row.
- BISB-S7: rendering with `status_block=None` (omitted kwarg) is a no-op — the
  rest of the template is byte-identical to pre-REQ behaviour. Covers
  backwards-compat for any future caller that hasn't been wired yet.

Existing tests stay unchanged. `test_prompts_sisyphus_label.py::_render_analyze`
calls `render("analyze.md.j2", ...)` without `status_block` — it should keep
working (no new required kwargs).

## Out of scope

- Re-PATCH-ing the status block when REQ state changes mid-flight. BKD doesn't
  let us; even if it did, no callsite re-renders the intent issue prompt. The
  block is dispatch-time only.
- Posting to the BKD UI as a separate panel. No such surface exists.
- Extending the block to staging-test / pr-ci-watch / accept / verifier /
  challenger / fixer / done_archive prompts. Their issue is sisyphus-created
  (not "intent"), so it's a different REQ if we want it.
