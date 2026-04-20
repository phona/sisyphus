// Sisyphus n8n router — pure functions, embeddable into n8n Code node.
// Contract: `routeEvent(webhookBody, opts?) -> { action, params, reason? }`
// Actions are executed by n8n Switch + HTTP nodes downstream.
//
// Note: ci-diagnose.js is bundled alongside router.js in the n8n Code node,
// so `diagnoseCiFailure` is in scope at runtime. For unit tests we import it.
import { diagnoseCiFailure } from './ci-diagnose.js';

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
  // repos: analyze emits repo:* for multi-repo REQs (future fan-out hook).
  const repos = [...t]
    .filter(x => /^repo:/.test(x))
    .map(x => x.split(':')[1]);
  const specStage = [...t].find(x => SPEC_TAGS.has(x)) || null;
  const parentIdTag = [...t].find(x => /^parent-id:/.test(x));
  const parentIssueId = parentIdTag ? parentIdTag.slice('parent-id:'.length) : null;
  const parentStageTag = [...t].find(x => /^parent:/.test(x) && !/^parent-id:/.test(x));
  const parentStage = parentStageTag ? parentStageTag.slice('parent:'.length) : null;

  return { routeKey, resultKey, reqId, round, target, repos, specStage, parentIssueId, parentStage };
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

  // L2.4: terminal states — done-archive and github-incident issues are already "terminal",
  // their session.completed should NOT re-dispatch through the action switch.
  if (tags.includes('done-archive') || tags.includes('github-incident')) {
    return { action: 'skip', reason: `terminal stage: ${tags.find(t => ['done-archive', 'github-incident'].includes(t))}`, params: { issueId } };
  }

  // L2.5: analyze fallback — if agent dropped the `analyze` tag but still has reqId
  // (we can recover from REQ-xxx tag or issueNumber), treat as analyze completion.
  // Previously this checked layer:* presence, but layers are now deprecated.
  if (ctx.routeKey === 'unknown' && (ctx.reqId || issueNumber)) {
    // Only fall through to analyze if the issue doesn't look like a terminal/ci/spec stage.
    const stageMarkers = ['ci', 'bugfix', 'test-fix', 'reviewer', 'verify', 'accept', 'dev'];
    const hasOtherStage = stageMarkers.some(s => tags.includes(s)) || [...SPEC_TAGS].some(s => tags.includes(s));
    if (!hasOtherStage) {
      const reqId = ctx.reqId || `REQ-${issueNumber}`;
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
  if (!ctx.reqId) {
    return { action: 'escalate', reason: 'analyze missing reqId', params: { issueId } };
  }
  // Fan-out 3 固定 spec：契约 + 验收 = LOCKED 边界；dev-spec = 实现计划。
  // 不再按 layer 选择——layer 是历史拍脑袋维度，实际所有 REQ 都需要这三件套。
  // 未来真的有 UI-only / migration-only 需求再独立加 stage，不塞进 fan-out。
  return {
    action: 'fanout_specs',
    params: {
      reqId: ctx.reqId,
      repos: ctx.repos,  // forward for future multi-repo fan-out; not used by Router today
      specs: ['dev-spec', 'contract-spec', 'accept-spec'],
    },
  };
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
    // integration failure = contract/acceptance assertions broke.
    // User policy: 契约/验收是 LOCKED 边界，任何 integration fail 一律让人工介入 (GitHub issue)，
    // 不再区分 code-bug/spec-bug。AI 不会自主去改跟"产品需求与代码契合度"相关的错误。
    // diagnosis 依然跑一次只作为 hint 附到 GitHub issue 上下文。
    if (ctx.target === 'integration') {
      const ciResult = webhookBody._ciResult || {};
      const diag = diagnoseCiFailure({ target: 'integration', stderrTail: ciResult.stderrTail, failedTests: ciResult.failedTests });
      return {
        action: 'open_github_issue',
        params: {
          reqId: ctx.reqId,
          sourceIssueId: issueId,
          kind: 'ci-integration-fail',
          diagnosis: diag.diagnosis,
          diagnosisReason: diag.reason,
          branch: `feat/${ctx.reqId}`,
          repoUrl: ctx._repoMap[ctx._projectId] || null,
          workdir: workdirFor(`feat/${ctx.reqId}`),
          stderrTail: ciResult.stderrTail || '',
          failedTests: ciResult.failedTests || [],
          // Dedup key: same REQ + same kind → agent should comment on existing open issue
          // instead of opening a new one each CI round.
          incidentKey: `${ctx.reqId}:ci-integration-fail`,
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
    // Acceptance fail = human must decide (spec ambiguity vs code bug).
    // Accept tests are the contract-with-user layer; auto-bugfix here risks diverging
    // from the real intent. Route to GitHub issue for repo owner review.
    return {
      action: 'open_github_issue',
      params: {
        reqId: ctx.reqId,
        sourceIssueId: issueId,
        kind: 'accept-fail',
        round: ctx.round,
        branch: `feat/${ctx.reqId}`,
        repoUrl: ctx._repoMap[ctx._projectId] || null,
        workdir: workdirFor(`feat/${ctx.reqId}`),
        incidentKey: `${ctx.reqId}:accept-fail`,
      },
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
