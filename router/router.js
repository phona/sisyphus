// Sisyphus n8n router — pure functions, embeddable into n8n Code node.
// Contract: `routeEvent(webhookBody, opts?) -> { action, params, reason? }`
// Actions are executed by n8n Switch + HTTP nodes downstream.

export const STAGE_PRIORITY = [
  'ci',
  'test-fix',
  'bugfix',
  'reviewer',
  'verify',
  'accept',
  'dev',
  'analyze',
];

export const SPEC_TAGS = new Set([
  'dev-spec',
  'contract-spec',
  'accept-spec',
  'ui-spec',
  'migration-spec',
]);

export const CB_THRESHOLD = 3; // bugfix round >= N -> escalate

// MVP: projectId → git repo URL mapping. Extend as more repos onboard.
// The real source should be BKD project metadata (list-projects returns repositoryUrl),
// but Router is a pure function — n8n can pass these in via opts.projectRepoMap.
export const DEFAULT_PROJECT_REPO_MAP = {
  '77k9z58j': 'https://github.com/phona/ubox-crosser.git', // workflowtest
};

// Work directory on the debug machine (vm-node04). One per branch, so different
// branches are physically isolated. Same branch across rounds reuses the dir
// via `git fetch && git reset --hard` to keep disk usage bounded.
export function workdirFor(branch) {
  if (!branch) return null;
  const safe = String(branch).replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '');
  return `/var/sisyphus-ci/${safe}`;
}

export function parseTags(tags = []) {
  const t = new Set(tags);

  // routeKey: stage priority
  let routeKey = 'unknown';
  for (const p of STAGE_PRIORITY) {
    if (t.has(p)) {
      routeKey = p;
      break;
    }
  }
  if (routeKey === 'unknown') {
    for (const s of SPEC_TAGS) {
      if (t.has(s)) {
        routeKey = 'spec';
        break;
      }
    }
  }

  // resultKey: how the stage ended
  let resultKey = '';
  if (t.has('ci:pass')) resultKey = 'ci-pass';
  else if (t.has('ci:fail')) resultKey = 'ci-fail';
  else if (t.has('result:pass')) resultKey = 'pass';
  else if (t.has('result:fail')) resultKey = 'fail';
  else if (t.has('diagnosis:spec-bug')) resultKey = 'spec-bug';
  else if (t.has('diagnosis:test-bug')) resultKey = 'test-bug';
  else if (t.has('decision:unsupported')) resultKey = 'unsupported';
  else if (t.has('decision:needs-clarify')) resultKey = 'needs-clarify';

  // side channels
  const reqId = [...t].find(x => /^REQ-[\w-]+$/.test(x)) || null;
  const roundTag = [...t].find(x => /^round-\d+$/.test(x));
  const round = roundTag ? parseInt(roundTag.slice(6), 10) : 0;
  const targetTag = [...t].find(x => /^target:(lint|unit|integration|acceptance)$/.test(x));
  const target = targetTag ? targetTag.split(':')[1] : null;
  const layers = [...t]
    .filter(x => /^layer:/.test(x))
    .map(x => x.split(':')[1]);
  const specStage = [...t].find(x => SPEC_TAGS.has(x)) || null;
  const parentIdTag = [...t].find(x => /^parent-id:/.test(x));
  const parentIssueId = parentIdTag ? parentIdTag.slice('parent-id:'.length) : null;
  const parentStageTag = [...t].find(x => /^parent:/.test(x) && !/^parent-id:/.test(x));
  const parentStage = parentStageTag ? parentStageTag.slice('parent:'.length) : null;

  return { routeKey, resultKey, reqId, round, target, layers, specStage, parentIssueId, parentStage };
}

export function routeEvent(webhookBody = {}, opts = {}) {
  const { event, issueId, priorStatusId, projectId, originalTitle, issueNumber } = webhookBody;
  const tags = Array.isArray(webhookBody.tags) ? webhookBody.tags : [];
  const ctx = parseTags(tags);
  ctx._projectId = projectId || webhookBody.projectId || '';
  ctx._repoMap = opts.projectRepoMap || DEFAULT_PROJECT_REPO_MAP;

  // L-1: dedup gate (set by upstream Ctx node based on workflowStaticData)
  if (webhookBody._dedupSkip === true) {
    return { action: 'skip', reason: 'duplicate event (dedup gate)', params: { issueId, dedupKey: webhookBody._dedupKey } };
  }

  // L0: intent:analyze entry — BKD UI tag trigger
  if (event === 'issue.updated' && tags.includes('intent:analyze') && !tags.includes('analyze')) {
    const reqId = ctx.reqId || (issueNumber ? `REQ-${issueNumber}` : null);
    if (!reqId) {
      return { action: 'escalate', reason: 'intent:analyze without issueNumber/reqId', params: { issueId, tags } };
    }
    return {
      action: 'start_analyze',
      params: {
        issueId,
        reqId,
        originalTitle: originalTitle || webhookBody.title || '',
        repoUrl: ctx._repoMap[ctx._projectId] || null,
      },
    };
  }

  // L1: session crash
  if (event === 'session.failed') {
    return {
      action: 'escalate',
      reason: 'session.failed',
      params: { issueId, reqId: ctx.reqId, tags },
    };
  }

  // L2: idempotency — already done
  if (priorStatusId === 'done') {
    return { action: 'skip', reason: 'already done', params: { issueId } };
  }

  // L2.5: analyze-layer fallback — if agent overwrote tags leaving only layer:*,
  // we can still infer analyze completion from layer tags + a reqId. reqId can come
  // from either existing REQ-xxx tag or the issueNumber fallback.
  if (ctx.routeKey === 'unknown' && ctx.layers.length > 0) {
    const reqId = ctx.reqId || (issueNumber ? `REQ-${issueNumber}` : null);
    if (reqId) {
      ctx.reqId = reqId;
      return routeAnalyze(ctx, issueId);
    }
  }

  // L3: dispatch by routeKey
  switch (ctx.routeKey) {
    case 'analyze':  return routeAnalyze(ctx, issueId);
    case 'spec':     return routeSpecDone(ctx, issueId, opts);
    case 'dev':      return routeDevDone(ctx, issueId);
    case 'ci':       return routeCiDone(ctx, issueId, webhookBody);
    case 'verify':   return routeVerifyDone(ctx, issueId); // kept for transition
    case 'accept':   return routeAcceptDone(ctx, issueId);
    case 'bugfix':   return routeBugfixDone(ctx, issueId);
    case 'test-fix': return { action: 'create_reviewer', params: { reqId: ctx.reqId, round: ctx.round || 1, sourceIssueId: issueId } };
    case 'reviewer': return routeReviewerDone(ctx, issueId);
    default:         return { action: 'escalate', reason: 'unknown route', params: { issueId, tags } };
  }
}

function routeAnalyze(ctx, issueId) {
  if (ctx.resultKey === 'unsupported' || ctx.resultKey === 'needs-clarify') {
    return { action: 'escalate', reason: `analyze:${ctx.resultKey}`, params: { issueId, reqId: ctx.reqId } };
  }
  if (!ctx.layers.length) {
    return { action: 'escalate', reason: 'analyze missing layers tag', params: { issueId, reqId: ctx.reqId } };
  }
  return {
    action: 'fanout_specs',
    params: { reqId: ctx.reqId, layers: ctx.layers, specs: expectedSpecsFor(ctx.layers) },
  };
}

export function expectedSpecsFor(layers = []) {
  const specs = ['dev-spec', 'accept-spec'];
  if (layers.includes('backend'))  specs.push('contract-spec');
  if (layers.includes('frontend')) specs.push('ui-spec');
  if (layers.includes('data'))     specs.push('migration-spec');
  return specs;
}

function routeSpecDone(ctx, issueId, opts) {
  if (!ctx.specStage) {
    return { action: 'skip', reason: 'spec without specStage tag', params: { issueId } };
  }
  // TEMP (integration-testing mode): bypass ci-lint at spec stage and let SPG gate open.
  // Reason: ubox-crosser baseline lint + go vet not BASE_REV-scoped + spec→CI feedback loop
  // keep blocking downstream stages (dev / ci-unit / ci-integration / accept). Skipping
  // ci-lint lets the rest of the chain surface its own bugs in a single run.
  return {
    action: 'mark_spec_reviewed',
    params: {
      reqId: ctx.reqId,
      specStage: ctx.specStage,
      parentIssueId: issueId,
    },
  };
}

function routeDevDone(ctx, issueId) {
  const branch = `stage/${ctx.reqId}-dev`;
  return {
    action: 'create_ci_runner',
    params: {
      reqId: ctx.reqId,
      target: 'unit',
      branch,
      workdir: workdirFor(branch),
      repoUrl: ctx._repoMap[ctx._projectId] || null,
      parentIssueId: issueId,
      parentStage: 'dev',
    },
  };
}

function ciRunnerAction(ctx, target, parentStage, parentIssueId, branchOverride) {
  const branch = branchOverride || `stage/${ctx.reqId}-${parentStage}`;
  return {
    action: 'create_ci_runner',
    params: {
      reqId: ctx.reqId,
      target,
      branch,
      workdir: workdirFor(branch),
      repoUrl: ctx._repoMap[ctx._projectId] || null,
      parentIssueId,
      parentStage,
    },
  };
}

function routeCiDone(ctx, issueId, webhookBody) {
  // Parent info now comes from tags (`parent:xxx` + `parent-id:xxx`), not webhook metadata.
  // BKD webhooks don't ship metadata; tags travel with the issue and survive round-trips.
  const parentStage = ctx.parentStage || webhookBody?.metadata?.parentStage || null;
  const parentIssueId = ctx.parentIssueId || webhookBody?.metadata?.parentIssueId || null;

  if (ctx.resultKey === 'ci-pass') {
    // dev unit pass → run integration CI on feat branch
    if (parentStage === 'dev') {
      return ciRunnerAction(ctx, 'integration', 'verify', issueId, `feat/${ctx.reqId}`);
    }
    // integration pass → kick off accept (AI-QA) stage
    if (parentStage === 'verify' || ctx.target === 'integration') {
      return {
        action: 'create_accept',
        params: {
          reqId: ctx.reqId,
          sourceIssueId: issueId,
          branch: `feat/${ctx.reqId}`,
          workdir: workdirFor(`feat/${ctx.reqId}`),
          repoUrl: ctx._repoMap[ctx._projectId] || null,
        },
      };
    }
    // spec lint pass → gate: check if all required specs done, create dev if so
    if (parentStage && /-spec$/.test(parentStage)) {
      return { action: 'mark_spec_reviewed', params: { reqId: ctx.reqId, specStage: parentStage, parentIssueId } };
    }
    return { action: 'skip', reason: 'ci:pass with unknown parent', params: { issueId } };
  }

  if (ctx.resultKey === 'ci-fail') {
    // lint / unit failures are lightweight — bounce back to the originating issue
    if (ctx.target === 'lint' || ctx.target === 'unit') {
      return {
        action: 'comment_back',
        params: {
          targetIssueId: parentIssueId,
          reqId: ctx.reqId,
          reason: `ci:${ctx.target} fail`,
          ciIssueId: issueId,
        },
      };
    }
    // integration failure = real bug → open a bugfix issue (round N)
    if (ctx.target === 'integration') {
      return {
        action: 'create_bugfix',
        params: {
          reqId: ctx.reqId,
          round: 1,
          sourceIssueId: issueId,
          reason: 'ci:integration fail',
          branch: `feat/${ctx.reqId}`,
        },
      };
    }
    return { action: 'escalate', reason: `ci:fail unknown target ${ctx.target}`, params: { issueId } };
  }

  return { action: 'escalate', reason: 'ci without ci:pass/ci:fail tag', params: { issueId } };
}

function routeVerifyDone(ctx, issueId) {
  // legacy verify-agent path; ci-runner eventually supersedes.
  if (ctx.resultKey === 'pass') {
    return { action: 'escalate', reason: 'legacy verify pass; accept stage not yet implemented', params: { reqId: ctx.reqId, issueId } };
  }
  if (ctx.resultKey === 'fail') {
    return {
      action: 'create_bugfix',
      params: { reqId: ctx.reqId, round: Math.max(ctx.round, 1), sourceIssueId: issueId, reason: 'verify fail', branch: `feat/${ctx.reqId}` },
    };
  }
  return { action: 'escalate', reason: 'verify without result tag', params: { issueId } };
}

function routeAcceptDone(ctx, issueId) {
  if (ctx.resultKey === 'pass') {
    // accept pass → archive: openspec apply + gh pr create + mark parent done
    return {
      action: 'done_archive',
      params: {
        reqId: ctx.reqId,
        acceptIssueId: issueId,
        branch: `feat/${ctx.reqId}`,
        workdir: workdirFor(`feat/${ctx.reqId}`),
        repoUrl: ctx._repoMap[ctx._projectId] || null,
      },
    };
  }
  if (ctx.resultKey === 'fail') {
    if (ctx.round >= CB_THRESHOLD) {
      return { action: 'escalate', reason: `circuit-breaker round>=${CB_THRESHOLD}`, params: { reqId: ctx.reqId, round: ctx.round } };
    }
    return {
      action: 'create_bugfix',
      params: { reqId: ctx.reqId, round: ctx.round + 1, sourceIssueId: issueId, reason: 'accept fail', branch: `feat/${ctx.reqId}` },
    };
  }
  return { action: 'escalate', reason: 'accept without result tag', params: { issueId } };
}

function routeBugfixDone(ctx, issueId) {
  if (ctx.resultKey === 'spec-bug') {
    return { action: 'escalate', reason: 'diagnosis:spec-bug', params: { issueId, reqId: ctx.reqId, round: ctx.round } };
  }
  // dev-fix normal path → hand to test-fix for adversarial review
  return { action: 'create_test_fix', params: { reqId: ctx.reqId, round: ctx.round || 1, sourceIssueId: issueId } };
}

function routeReviewerDone(ctx, issueId) {
  if (ctx.resultKey === 'pass') {
    // reviewer merged → re-run integration CI on feat branch
    return ciRunnerAction(ctx, 'integration', 'verify', issueId, `feat/${ctx.reqId}`);
  }
  return { action: 'escalate', reason: 'reviewer failed to pick a winner', params: { issueId, reqId: ctx.reqId } };
}
