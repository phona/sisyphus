// Verify the build-workflow.js bundle is self-contained and runs correctly
// in a "Code node-like" environment (no imports, no exports, functions in
// a single script scope).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const workflow = JSON.parse(readFileSync(resolve(here, '../charts/n8n-workflows/v3.1/v3-events.json'), 'utf8'));
const routerNode = workflow.nodes.find(n => n.name === 'Router');
const bundle = routerNode.parameters.jsCode;

function runBundle(body) {
  // Simulate n8n Code-node environment:
  //   - $input.first().json returns input
  //   - script returns [{ json: ... }]
  const $input = { first: () => ({ json: body }) };
  const fn = new Function('$input', `
    ${bundle.replace(/^\s*return\s+\[/m, 'return [')}
  `);
  const out = fn($input);
  return out[0].json;
}

test('bundle: dev.completed → create_ci_runner(unit)', () => {
  const r = runBundle({ tags: ['dev', 'REQ-10'], issueId: 'd1' });
  assert.equal(r.action, 'create_ci_runner');
  assert.equal(r.params.target, 'unit');
});

test('bundle: ci unit fail → comment_back', () => {
  const r = runBundle({ tags: ['ci', 'REQ-10', 'ci:fail', 'target:unit'], metadata: { parentStage: 'dev', parentIssueId: 'd1' }, issueId: 'c1' });
  assert.equal(r.action, 'comment_back');
  assert.equal(r.params.targetIssueId, 'd1');
});

test('bundle: ci integration fail → create_bugfix round 1', () => {
  const r = runBundle({ tags: ['ci', 'REQ-10', 'ci:fail', 'target:integration'] });
  assert.equal(r.action, 'create_bugfix');
  assert.equal(r.params.round, 1);
});

test('bundle: accept fail round 3 → circuit breaker', () => {
  const r = runBundle({ tags: ['accept', 'REQ-10', 'result:fail', 'round-3'] });
  assert.equal(r.action, 'escalate');
  assert.match(r.reason, /circuit-breaker/);
});

test('bundle: already done → skip', () => {
  const r = runBundle({ priorStatusId: 'done', tags: ['verify', 'REQ-10', 'result:pass'] });
  assert.equal(r.action, 'skip');
});

test('bundle: session.failed → escalate', () => {
  const r = runBundle({ event: 'session.failed', tags: ['dev', 'REQ-10'] });
  assert.equal(r.action, 'escalate');
  assert.equal(r.reason, 'session.failed');
});

test('bundle: analyze with layers → fanout_specs', () => {
  const r = runBundle({ tags: ['analyze', 'REQ-5', 'layer:backend', 'layer:frontend'] });
  assert.equal(r.action, 'fanout_specs');
  assert.deepEqual(r.params.specs.sort(), ['accept-spec', 'contract-spec', 'dev-spec', 'ui-spec']);
});

test('bundle: exports attached _input + _routedAt for observability', () => {
  const r = runBundle({ tags: ['dev', 'REQ-10'] });
  assert.ok(r._routedAt, 'bundle should stamp _routedAt');
  assert.ok(r._input, 'bundle should echo _input');
});

test('bundle: unexpected exception yields escalate-with-error (no crash)', () => {
  // Feed a tags value that will make parseTags blow up if unguarded
  const r = runBundle({ tags: null });
  // With tags=null, Array.isArray check falls through to []; should return escalate (unknown route)
  assert.equal(r.action, 'escalate');
});
