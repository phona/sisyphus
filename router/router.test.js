import test from 'node:test';
import assert from 'node:assert/strict';
import { routeEvent, parseTags, expectedSpecsFor } from './router.js';

// ─── parseTags unit tests ──────────────────────────────────────────────────

test('parseTags: plain verify fail', () => {
  const ctx = parseTags(['verify', 'REQ-999', 'result:fail']);
  assert.equal(ctx.routeKey, 'verify');
  assert.equal(ctx.resultKey, 'fail');
  assert.equal(ctx.reqId, 'REQ-999');
  assert.equal(ctx.round, 0);
});

test('parseTags: ci-runner pass with target', () => {
  const ctx = parseTags(['ci', 'REQ-10', 'ci:pass', 'target:unit']);
  assert.equal(ctx.routeKey, 'ci');
  assert.equal(ctx.resultKey, 'ci-pass');
  assert.equal(ctx.target, 'unit');
});

test('parseTags: bugfix with round', () => {
  const ctx = parseTags(['bugfix', 'REQ-7', 'round-2', 'diagnosis:spec-bug']);
  assert.equal(ctx.routeKey, 'bugfix');
  assert.equal(ctx.resultKey, 'spec-bug');
  assert.equal(ctx.round, 2);
});

test('parseTags: spec stage inferred', () => {
  const ctx = parseTags(['contract-spec', 'REQ-3']);
  assert.equal(ctx.routeKey, 'spec');
  assert.equal(ctx.specStage, 'contract-spec');
});

test('parseTags: analyze with layers', () => {
  const ctx = parseTags(['analyze', 'REQ-1', 'layer:backend', 'layer:data']);
  assert.equal(ctx.routeKey, 'analyze');
  assert.deepEqual(ctx.layers.sort(), ['backend', 'data']);
});

test('parseTags: stage priority — test-fix outranks bugfix sibling tag', () => {
  const ctx = parseTags(['test-fix', 'bugfix', 'REQ-1']);
  assert.equal(ctx.routeKey, 'test-fix');
});

// ─── routeEvent end-to-end ─────────────────────────────────────────────────

test('routeEvent: already-done gate', () => {
  const r = routeEvent({ issueId: 'i1', priorStatusId: 'done', tags: ['verify', 'REQ-1', 'result:pass'] });
  assert.equal(r.action, 'skip');
});

test('routeEvent: session.failed → escalate', () => {
  const r = routeEvent({ event: 'session.failed', issueId: 'i1', tags: ['dev', 'REQ-1'] });
  assert.equal(r.action, 'escalate');
  assert.equal(r.reason, 'session.failed');
});

test('routeEvent: dev completed → create ci-runner(unit)', () => {
  const r = routeEvent({ event: 'session.completed', issueId: 'dev-1', tags: ['dev', 'REQ-10'], projectId: '77k9z58j' });
  assert.equal(r.action, 'create_ci_runner');
  assert.equal(r.params.target, 'unit');
  assert.equal(r.params.branch, 'stage/REQ-10-dev');
  assert.equal(r.params.parentStage, 'dev');
  assert.equal(r.params.workdir, '/var/sisyphus-ci/stage-REQ-10-dev');
  assert.equal(r.params.repoUrl, 'https://github.com/phona/ubox-crosser.git');
});

test('routeEvent: dev completed with unknown project → repoUrl null', () => {
  const r = routeEvent({ event: 'session.completed', tags: ['dev', 'REQ-10'], projectId: 'unknown-proj' });
  assert.equal(r.params.workdir, '/var/sisyphus-ci/stage-REQ-10-dev');
  assert.equal(r.params.repoUrl, null);
});

test('routeEvent: ci(unit) pass → create_ci_runner(integration)', () => {
  const body = { event: 'session.completed', issueId: 'ci-1', tags: ['ci', 'REQ-10', 'ci:pass', 'target:unit'], projectId: '77k9z58j', metadata: { parentStage: 'dev', parentIssueId: 'dev-1' } };
  const r = routeEvent(body);
  assert.equal(r.action, 'create_ci_runner');
  assert.equal(r.params.target, 'integration');
  assert.equal(r.params.branch, 'feat/REQ-10');
  assert.equal(r.params.parentStage, 'verify');
});

test('routeEvent: ci(unit) fail → comment back to dev (no round)', () => {
  const body = { tags: ['ci', 'REQ-10', 'ci:fail', 'target:unit'], metadata: { parentStage: 'dev', parentIssueId: 'dev-1' }, issueId: 'ci-1' };
  const r = routeEvent(body);
  assert.equal(r.action, 'comment_back');
  assert.equal(r.params.targetIssueId, 'dev-1');
  assert.equal(r.params.ciIssueId, 'ci-1');
});

test('routeEvent: ci(integration) pass → escalate (AI-QA pending)', () => {
  const body = { tags: ['ci', 'REQ-10', 'ci:pass', 'target:integration'] };
  const r = routeEvent(body);
  assert.equal(r.action, 'escalate');
  assert.match(r.reason, /AI-QA/);
});

test('routeEvent: ci(integration) fail → create_bugfix round 1', () => {
  const body = { tags: ['ci', 'REQ-10', 'ci:fail', 'target:integration'], issueId: 'ci-x' };
  const r = routeEvent(body);
  assert.equal(r.action, 'create_bugfix');
  assert.equal(r.params.round, 1);
});

test('routeEvent: ci(lint) pass on spec → mark_spec_reviewed', () => {
  const body = { tags: ['ci', 'REQ-10', 'ci:pass', 'target:lint'], metadata: { parentStage: 'contract-spec', parentIssueId: 's-1' } };
  const r = routeEvent(body);
  assert.equal(r.action, 'mark_spec_reviewed');
  assert.equal(r.params.specStage, 'contract-spec');
});

test('routeEvent: spec done → mark_spec_reviewed (TEMP bypass ci-lint)', () => {
  // Temporary: spec completion shortcuts straight to SPG gate (ci-lint disabled during integration testing)
  const r = routeEvent({ tags: ['dev-spec', 'REQ-20'], issueId: 'spec-1', projectId: '77k9z58j' });
  assert.equal(r.action, 'mark_spec_reviewed');
  assert.equal(r.params.specStage, 'dev-spec');
  assert.equal(r.params.parentIssueId, 'spec-1');
});

test('routeEvent: analyze done with layers → fanout expected specs', () => {
  const body = { tags: ['analyze', 'REQ-5', 'layer:backend', 'layer:frontend'] };
  const r = routeEvent(body);
  assert.equal(r.action, 'fanout_specs');
  assert.deepEqual(r.params.specs.sort(), ['accept-spec', 'contract-spec', 'dev-spec', 'ui-spec']);
});

test('routeEvent: analyze unsupported → escalate', () => {
  const r = routeEvent({ tags: ['analyze', 'REQ-1', 'decision:unsupported'] });
  assert.equal(r.action, 'escalate');
  assert.ok(r.reason.includes('unsupported'));
});

test('routeEvent: analyze missing layers → escalate', () => {
  const r = routeEvent({ tags: ['analyze', 'REQ-1'] });
  assert.equal(r.action, 'escalate');
  assert.match(r.reason, /missing layers/);
});

test('routeEvent: bugfix diagnosis:spec-bug → escalate', () => {
  const r = routeEvent({ tags: ['bugfix', 'REQ-9', 'round-2', 'diagnosis:spec-bug'] });
  assert.equal(r.action, 'escalate');
});

test('routeEvent: bugfix no diagnosis → create_test_fix', () => {
  const r = routeEvent({ tags: ['bugfix', 'REQ-9', 'round-1'] });
  assert.equal(r.action, 'create_test_fix');
});

test('routeEvent: test-fix completed → create_reviewer', () => {
  const r = routeEvent({ tags: ['test-fix', 'REQ-9', 'round-1'] });
  assert.equal(r.action, 'create_reviewer');
});

test('routeEvent: reviewer pass → create_ci_runner(integration) on feat branch', () => {
  const r = routeEvent({ tags: ['reviewer', 'REQ-9', 'round-1', 'result:pass'], projectId: '77k9z58j' });
  assert.equal(r.action, 'create_ci_runner');
  assert.equal(r.params.target, 'integration');
  assert.equal(r.params.branch, 'feat/REQ-9');
});

test('routeEvent: reviewer fail → escalate', () => {
  const r = routeEvent({ tags: ['reviewer', 'REQ-9', 'round-1', 'result:fail'] });
  assert.equal(r.action, 'escalate');
});

test('routeEvent: accept pass → escalate (done_archive pending)', () => {
  const r = routeEvent({ tags: ['accept', 'REQ-9', 'result:pass'] });
  assert.equal(r.action, 'escalate');
  assert.match(r.reason, /done_archive/);
});

test('routeEvent: accept fail + round<3 → new bugfix with round+1', () => {
  const r = routeEvent({ tags: ['accept', 'REQ-9', 'result:fail', 'round-1'] });
  assert.equal(r.action, 'create_bugfix');
  assert.equal(r.params.round, 2);
});

test('routeEvent: accept fail + round>=3 → escalate (circuit breaker)', () => {
  const r = routeEvent({ tags: ['accept', 'REQ-9', 'result:fail', 'round-3'] });
  assert.equal(r.action, 'escalate');
  assert.match(r.reason, /circuit-breaker/);
});

test('routeEvent: title lies — accept + result:fail still creates bugfix regardless of title', () => {
  const r = routeEvent({ title: 'PASS [REQ-9] 验收', tags: ['accept', 'REQ-9', 'result:fail'] });
  assert.equal(r.action, 'create_bugfix');
});

test('routeEvent: unknown routing → escalate', () => {
  const r = routeEvent({ tags: ['REQ-9'] });
  assert.equal(r.action, 'escalate');
});

test('expectedSpecsFor: all three layers → 5 specs', () => {
  const specs = expectedSpecsFor(['backend', 'frontend', 'data']);
  assert.deepEqual(specs.sort(), ['accept-spec', 'contract-spec', 'dev-spec', 'migration-spec', 'ui-spec']);
});

test('expectedSpecsFor: backend only → 3 specs', () => {
  const specs = expectedSpecsFor(['backend']);
  assert.deepEqual(specs.sort(), ['accept-spec', 'contract-spec', 'dev-spec']);
});

test('routeEvent: intent:analyze entry → start_analyze', () => {
  const r = routeEvent({
    event: 'issue.updated',
    issueId: 'aaa',
    projectId: '77k9z58j',
    issueNumber: 700,
    title: '加个 /healthz',
    tags: ['intent:analyze'],
  });
  assert.equal(r.action, 'start_analyze');
  assert.equal(r.params.reqId, 'REQ-700');
  assert.equal(r.params.issueId, 'aaa');
  assert.equal(r.params.originalTitle, '加个 /healthz');
  assert.equal(r.params.repoUrl, 'https://github.com/phona/ubox-crosser.git');
});

test('routeEvent: intent:analyze + already has analyze tag → NOT trigger again', () => {
  const r = routeEvent({
    event: 'issue.updated',
    issueId: 'aaa',
    tags: ['intent:analyze', 'analyze', 'REQ-700'],
    issueNumber: 700,
  });
  // Should fall through, not start_analyze
  assert.notEqual(r.action, 'start_analyze');
});

test('routeEvent: intent:analyze without issueNumber → escalate', () => {
  const r = routeEvent({
    event: 'issue.updated',
    issueId: 'aaa',
    tags: ['intent:analyze'],
  });
  assert.equal(r.action, 'escalate');
  assert.match(r.reason, /without issueNumber/);
});
