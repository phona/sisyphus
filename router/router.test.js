import test from 'node:test';
import assert from 'node:assert/strict';
import { routeEvent, parseTags } from './router.js';

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
  const ctx = parseTags(['contract-test', 'REQ-3']);
  assert.equal(ctx.routeKey, 'spec');
  assert.equal(ctx.specStage, 'contract-test');
});

test('parseTags: analyze with repos (multi-repo hint)', () => {
  const ctx = parseTags(['analyze', 'REQ-1', 'repo:ubox-crosser', 'repo:fe-app']);
  assert.equal(ctx.routeKey, 'analyze');
  assert.deepEqual(ctx.repos.sort(), ['fe-app', 'ubox-crosser']);
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

test('routeEvent: ci(integration) pass → create_accept', () => {
  const body = { tags: ['ci', 'REQ-10', 'ci:pass', 'target:integration'], issueId: 'ci-int', projectId: '77k9z58j' };
  const r = routeEvent(body);
  assert.equal(r.action, 'create_accept');
  assert.equal(r.params.reqId, 'REQ-10');
  assert.equal(r.params.branch, 'feat/REQ-10');
  assert.equal(r.params.repoUrl, 'https://github.com/phona/ubox-crosser.git');
});

test('routeEvent: ci(integration) fail ALWAYS → open_github_issue (even code-bug)', () => {
  const body = {
    tags: ['ci', 'REQ-10', 'ci:fail', 'target:integration'],
    issueId: 'ci-x',
    projectId: '77k9z58j',
    _ciResult: { stderrTail: '--- FAIL: TestHealthz\nexpected 200 got 500', failedTests: ['TestHealthz'] },
  };
  const r = routeEvent(body);
  assert.equal(r.action, 'open_github_issue');
  assert.equal(r.params.kind, 'ci-integration-fail');
  assert.equal(r.params.diagnosis, 'code-bug');  // hint, not routing decision
  assert.equal(r.params.incidentKey, 'REQ-10:ci-integration-fail');
});

test('routeEvent: ci(integration) fail test-bug → open_github_issue', () => {
  const body = {
    tags: ['ci', 'REQ-10', 'ci:fail', 'target:integration'],
    issueId: 'ci-y',
    _ciResult: { stderrTail: 'tests/contract/ping_test.go:12: undefined: SomeHelper', failedTests: [] },
  };
  const r = routeEvent(body);
  assert.equal(r.action, 'open_github_issue');
  assert.equal(r.params.diagnosis, 'test-bug');
});

test('routeEvent: ci(integration) fail unknown → open_github_issue', () => {
  const body = {
    tags: ['ci', 'REQ-10', 'ci:fail', 'target:integration'],
    issueId: 'ci-z',
    _ciResult: { stderrTail: '', failedTests: [] },
  };
  const r = routeEvent(body);
  assert.equal(r.action, 'open_github_issue');
  assert.equal(r.params.diagnosis, 'unknown');
});

test('routeEvent: ci(lint) pass on spec → mark_spec_reviewed', () => {
  const body = { tags: ['ci', 'REQ-10', 'ci:pass', 'target:lint'], metadata: { parentStage: 'contract-test', parentIssueId: 's-1' } };
  const r = routeEvent(body);
  assert.equal(r.action, 'mark_spec_reviewed');
  assert.equal(r.params.specStage, 'contract-test');
});

test('routeEvent: spec done → create_ci_runner(target=lint) on spec branch', () => {
  const r = routeEvent({ tags: ['accept-test', 'REQ-20'], issueId: 'spec-1', projectId: '77k9z58j' });
  assert.equal(r.action, 'create_ci_runner');
  assert.equal(r.params.target, 'lint');
  assert.equal(r.params.parentStage, 'accept-test');
  assert.equal(r.params.parentIssueId, 'spec-1');
  assert.equal(r.params.branch, 'stage/REQ-20-accept-test');
});

test('routeEvent: analyze done → fanout 2 fixed specs (contract-test + accept-test)', () => {
  const body = { tags: ['analyze', 'REQ-5'] };
  const r = routeEvent(body);
  assert.equal(r.action, 'fanout_specs');
  assert.deepEqual(r.params.specs.sort(), ['accept-test', 'contract-test']);
});

test('routeEvent: analyze unsupported → escalate', () => {
  const r = routeEvent({ tags: ['analyze', 'REQ-1', 'decision:unsupported'] });
  assert.equal(r.action, 'escalate');
  assert.ok(r.reason.includes('unsupported'));
});

test('routeEvent: analyze with reqId but no extra tags → fanout (layers deprecated)', () => {
  const r = routeEvent({ tags: ['analyze', 'REQ-1'] });
  assert.equal(r.action, 'fanout_specs');
  assert.equal(r.params.reqId, 'REQ-1');
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

test('routeEvent: accept pass → done_archive', () => {
  const r = routeEvent({ tags: ['accept', 'REQ-9', 'result:pass'], issueId: 'acc-1', projectId: '77k9z58j' });
  assert.equal(r.action, 'done_archive');
  assert.equal(r.params.reqId, 'REQ-9');
  assert.equal(r.params.branch, 'feat/REQ-9');
  assert.equal(r.params.acceptIssueId, 'acc-1');
});

test('routeEvent: accept fail → open_github_issue (human decides spec vs code)', () => {
  const r = routeEvent({ tags: ['accept', 'REQ-9', 'result:fail'], issueId: 'acc-f', projectId: '77k9z58j' });
  assert.equal(r.action, 'open_github_issue');
  assert.equal(r.params.kind, 'accept-fail');
  assert.equal(r.params.branch, 'feat/REQ-9');
  assert.equal(r.params.repoUrl, 'https://github.com/phona/ubox-crosser.git');
});

test('routeEvent: title lies — accept + result:fail still routes to github issue', () => {
  const r = routeEvent({ title: 'PASS [REQ-9] 验收', tags: ['accept', 'REQ-9', 'result:fail'] });
  assert.equal(r.action, 'open_github_issue');
});

test('routeEvent: truly unknown (no reqId, no issueNumber) → escalate', () => {
  const r = routeEvent({ tags: ['mystery-tag'] });
  assert.equal(r.action, 'escalate');
});

test('routeEvent: done-archive terminal → skip', () => {
  const r = routeEvent({ event: 'session.completed', issueId: 'done-1', tags: ['done-archive', 'REQ-9', 'result:pass', 'pr:foo/bar#1'] });
  assert.equal(r.action, 'skip');
  assert.match(r.reason, /terminal/);
});

test('routeEvent: github-incident terminal → skip', () => {
  const r = routeEvent({ event: 'session.completed', issueId: 'gh-1', tags: ['github-incident', 'REQ-9', 'kind:accept-fail'] });
  assert.equal(r.action, 'skip');
});

test('routeEvent: agent dropped analyze tag but keeps repo:* → fallback fanout', () => {
  const r = routeEvent({ event: 'session.completed', issueId: 'anz-1', issueNumber: 685, tags: ['repo:ubox-crosser'] });
  assert.equal(r.action, 'fanout_specs');
  assert.equal(r.params.reqId, 'REQ-685');
  assert.deepEqual(r.params.specs.sort(), ['accept-test', 'contract-test']);
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
