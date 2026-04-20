// Mechanical CI failure classifier — reads stderr_tail emitted by ci-runner
// and maps to diagnosis categories so humans/agents don't self-report.

export function diagnoseCiFailure({ target, stderrTail = '', failedTests = [], history = [] } = {}) {
  const text = String(stderrTail);

  // compile errors bucketed by path
  const compileErrInTests  = /^(tests\/\S+?\.(?:go|ts|kt)):\d+/m.test(text) && /undefined:|syntax error|expected|cannot find/i.test(text);
  const compileErrInMain   = /^(main|internal|cmd)\/\S+?\.go:\d+/m.test(text) && /undefined:|syntax error|expected|cannot find/i.test(text);

  // runtime test failures
  const runtimeFail = /^(---\s+FAIL|FAIL\s+main|FAIL\s+tests\/)/m.test(text);

  // recurrence → spec-bug suspicion
  // history is [{round, failedTests: [...]}] from prior rounds
  const sameTestRedThreeTimes = countRecurringFailures(history, failedTests) >= 3;

  if (sameTestRedThreeTimes) {
    return { diagnosis: 'spec-bug', confidence: 'high', reason: 'same test red for >=3 rounds' };
  }
  if (compileErrInTests && !compileErrInMain) {
    return { diagnosis: 'test-bug', confidence: 'high', reason: 'compile error in tests/' };
  }
  if (compileErrInMain) {
    return { diagnosis: 'code-bug', confidence: 'high', reason: 'compile error in main/internal/cmd' };
  }
  if (runtimeFail) {
    // test compiles, code compiles, test red → code bug (default)
    return { diagnosis: 'code-bug', confidence: 'medium', reason: 'runtime test failure, code defaults' };
  }

  // nothing matched → can't classify
  return { diagnosis: 'unknown', confidence: 'low', reason: 'no recognizable pattern in stderr_tail' };
}

function countRecurringFailures(history, current) {
  if (!Array.isArray(history) || history.length < 2) return 0;
  const key = (t) => t.split(/\s/).slice(-1)[0]; // last token = test name
  const currentSet = new Set((current || []).map(key));
  let streak = 0;
  for (const round of history.slice().reverse()) {
    const prev = new Set((round.failedTests || []).map(key));
    const overlap = [...currentSet].some(k => prev.has(k));
    if (overlap) streak += 1; else break;
  }
  return streak + 1; // +1 for current round
}

// Parse the `## CI Result` block that ci-runner writes into issue description.
// Returns null if block missing or malformed.
export function parseCiResultBlock(issueDescription = '') {
  const m = String(issueDescription).match(/##\s*CI Result\s*([\s\S]+?)(?=\n##\s|\n\s*$|$)/);
  if (!m) return null;
  const body = m[1];

  const get = (k) => {
    const rx = new RegExp(`^\\s*${k}\\s*:\\s*(.*?)\\s*$`, 'm');
    const hit = body.match(rx);
    return hit ? hit[1] : null;
  };

  const target     = get('target');
  const branch     = get('branch');
  const commit     = get('commit');
  const exitCode   = get('exit_code');
  const durationMs = get('duration_ms');
  const coverage   = get('coverage');

  const failedTests = [];
  const ftMatch = body.match(/failed_tests\s*:\s*\n([\s\S]*?)(?=\n[a-z_]+:|$)/);
  if (ftMatch) {
    for (const line of ftMatch[1].split('\n')) {
      const m = line.match(/^\s*-\s+(.+?)\s*$/);
      if (m) failedTests.push(m[1]);
    }
  }

  const tailMatch = body.match(/stderr_tail\s*:\s*\|\s*\n([\s\S]+)$/);
  const stderrTail = tailMatch ? tailMatch[1].replace(/^\s{0,4}/gm, '') : '';

  return {
    target,
    branch,
    commit,
    exitCode: exitCode === null ? null : parseInt(exitCode, 10),
    durationMs: durationMs === null ? null : parseInt(durationMs, 10),
    coverage,
    failedTests,
    stderrTail,
  };
}
