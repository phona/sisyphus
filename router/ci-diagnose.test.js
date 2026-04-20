import test from 'node:test';
import assert from 'node:assert/strict';
import { diagnoseCiFailure, parseCiResultBlock } from './ci-diagnose.js';

test('diagnose: compile error in tests/ → test-bug', () => {
  const stderr = `tests/contract/order_test.go:42:2: undefined: NewOrderRepo\nFAIL\ttests/contract [build failed]`;
  const d = diagnoseCiFailure({ target: 'integration', stderrTail: stderr });
  assert.equal(d.diagnosis, 'test-bug');
});

test('diagnose: compile error in main/ → code-bug', () => {
  const stderr = `main/service/order_service.go:100:5: syntax error: unexpected }\nFAIL\tmain [build failed]`;
  const d = diagnoseCiFailure({ target: 'unit', stderrTail: stderr });
  assert.equal(d.diagnosis, 'code-bug');
});

test('diagnose: runtime FAIL, both compile OK → code-bug (default)', () => {
  const stderr = `--- FAIL: TestOrderCreate (0.04s)\n  order_test.go:30: expected total=99 got=88\nFAIL`;
  const d = diagnoseCiFailure({ target: 'unit', stderrTail: stderr, failedTests: ['TestOrderCreate'] });
  assert.equal(d.diagnosis, 'code-bug');
});

test('diagnose: same test red 3 rounds → spec-bug', () => {
  const stderr = `--- FAIL: TestOrderCreate (0.04s)\nFAIL`;
  const history = [
    { round: 1, failedTests: ['TestOrderCreate'] },
    { round: 2, failedTests: ['TestOrderCreate'] },
  ];
  const d = diagnoseCiFailure({ target: 'unit', stderrTail: stderr, failedTests: ['TestOrderCreate'], history });
  assert.equal(d.diagnosis, 'spec-bug');
});

test('diagnose: unrecognizable output → unknown', () => {
  const d = diagnoseCiFailure({ target: 'unit', stderrTail: 'docker: cannot connect to daemon' });
  assert.equal(d.diagnosis, 'unknown');
});

test('parseCiResultBlock: happy path', () => {
  const desc = [
    'Some context',
    '## CI Result',
    'target: unit',
    'branch: stage/REQ-10-dev',
    'commit: abc1234',
    'exit_code: 1',
    'duration_ms: 45231',
    'coverage: 73.2%',
    'failed_tests:',
    '  - TestOrderCreate',
    '  - TestOrderCancel',
    'stderr_tail: |',
    '  --- FAIL: TestOrderCreate (0.04s)',
    '      order_test.go:30: expected 99',
    '',
  ].join('\n');
  const b = parseCiResultBlock(desc);
  assert.equal(b.target, 'unit');
  assert.equal(b.exitCode, 1);
  assert.equal(b.durationMs, 45231);
  assert.equal(b.coverage, '73.2%');
  assert.deepEqual(b.failedTests, ['TestOrderCreate', 'TestOrderCancel']);
  assert.match(b.stderrTail, /FAIL: TestOrderCreate/);
});

test('parseCiResultBlock: missing block → null', () => {
  assert.equal(parseCiResultBlock('no block here'), null);
});
