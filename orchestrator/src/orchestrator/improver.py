"""improver-autopilot: autonomous improvement loop (REQ-improver-autopilot).

Runs on a configurable interval and evaluates 4 whitelist rule types against
live metrics from the main DB. When a signal fires it either:

  - detect-only mode  (improver_bkd_project_id = "") — writes an improver_runs
    row with status='pending' and logs the finding for human follow-up.
  - autopilot mode    (improver_bkd_project_id set)  — additionally creates a
    BKD intent:analyze issue so the analyze-agent can implement the config
    change and open a PR.

4 whitelist rule types (only numeric config params, never prompt content):

  1. latency-guard   — stage P95 duration approaches watchdog threshold
                       → adjust watchdog_stuck_threshold_sec
  2. loop-cap        — fixer round hits cap too often (or never reaches it)
                       → adjust fixer_round_cap
  3. flake-tolerance — infra flake rate too high or too low
                       → adjust checker_infra_flake_retry_max
  4. throughput      — inflight-cap escalations too frequent
                       → adjust inflight_req_cap

Budget cap enforced per rule:
  - global: at most `improver_budget_per_window` non-skipped runs per 7-day window
  - per-rule: at least `improver_cooldown_per_rule_days` days between same-rule triggers
  - data guard: skip when sample count < `improver_min_sample_count`

Design constraints:
  - Only numeric config fields, never prompt text — "不抢 AI 决定权"
  - Each param has hard min/max bounds and moves by at most one step per trigger
  - New deployments with insufficient data are protected by min_sample_count
"""
from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from .bkd import BKDClient
from .config import settings
from .store import db, improver_runs

log = structlog.get_logger(__name__)

# ─── Rule-specific thresholds — hard constraints, not in config ───────────────
# latency-guard
_LATENCY_TRIGGER_RATIO = 0.75    # P95 / watchdog_stuck_threshold_sec >= this → trigger
_LATENCY_BUMP_RATIO = 1.25       # increase threshold by 25% per trigger
_LATENCY_MAX_SEC = 14400         # absolute cap: 4 h

# loop-cap
_LOOP_CAP_HIT_RATE_UP = 0.30     # fraction of fixable REQs hitting cap → raise cap
_LOOP_CAP_MAX_ROUNDS_DOWN = 2    # if max observed rounds < (cap - this) → lower cap
_LOOP_CAP_MIN = 3
_LOOP_CAP_MAX = 10

# flake-tolerance
_FLAKE_RATE_UP = 0.25            # 7-day infra-flake rate → raise retry_max
_FLAKE_RATE_DOWN = 0.03          # 14-day infra-flake rate → lower retry_max
_FLAKE_RETRY_MIN = 0
_FLAKE_RETRY_MAX = 3

# throughput
_THROUGHPUT_ESCALATION_UP = 3    # inflight-cap escalations in 7d → raise cap
_INFLIGHT_CAP_MIN = 5
_INFLIGHT_CAP_MAX = 20


@dataclass
class _Signal:
    """One detected signal from a rule evaluation."""
    rule_type: str
    signal_data: dict
    proposed_change: dict       # {param, from_value, to_value}
    bkd_description: str        # human-readable prompt for BKD issue (autopilot mode)


# ─── Rule implementations ─────────────────────────────────────────────────────

async def _eval_latency_guard(pool) -> _Signal | None:
    """Rule 1: stage P95 duration is too close to watchdog threshold."""
    threshold = settings.watchdog_stuck_threshold_sec
    min_samples = settings.improver_min_sample_count
    rows = await pool.fetch(
        """
        SELECT stage,
               COUNT(*) AS n,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_sec) AS p95
        FROM stage_runs
        WHERE started_at > NOW() - INTERVAL '7 days'
          AND duration_sec IS NOT NULL
          AND outcome IS NOT NULL
        GROUP BY stage
        HAVING COUNT(*) >= $1
        """,
        min_samples,
    )
    if not rows:
        return None
    worst_stage = max(rows, key=lambda r: float(r["p95"]))
    p95 = float(worst_stage["p95"])
    ratio = p95 / threshold
    if ratio < _LATENCY_TRIGGER_RATIO:
        return None
    new_threshold = min(int(threshold * _LATENCY_BUMP_RATIO), _LATENCY_MAX_SEC)
    if new_threshold <= threshold:
        return None
    return _Signal(
        rule_type="latency-guard",
        signal_data={
            "worst_stage": worst_stage["stage"],
            "p95_sec": round(p95, 1),
            "current_threshold_sec": threshold,
            "ratio": round(ratio, 3),
            "sample_count": int(worst_stage["n"]),
        },
        proposed_change={
            "param": "watchdog_stuck_threshold_sec",
            "from_value": threshold,
            "to_value": new_threshold,
        },
        bkd_description=textwrap.dedent(f"""\
            ## improver-autopilot: latency-guard

            Stage `{worst_stage["stage"]}` P95 duration is **{p95:.0f}s**
            ({ratio:.0%} of watchdog_stuck_threshold_sec={threshold}s).
            The watchdog would false-escalate ~5% of legitimate runs.

            **Proposed change**: bump `watchdog_stuck_threshold_sec`
            from `{threshold}` → `{new_threshold}` in
            `orchestrator/src/orchestrator/config.py`.

            Update the default value and add a changelog entry.
            Run `make ci-unit-test` to verify tests pass.
        """),
    )


async def _eval_loop_cap(pool) -> _Signal | None:
    """Rule 2: fixer_round_cap is too tight (or too loose)."""
    cap = settings.fixer_round_cap
    min_samples = settings.improver_min_sample_count
    # Count REQs that escalated due to fixer-round-cap in last 14d
    cap_hit_row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM req_state
        WHERE state = 'escalated'
          AND context->>'escalated_reason' = 'fixer-round-cap'
          AND updated_at > NOW() - INTERVAL '14 days'
        """,
    )
    cap_hit_count = int(cap_hit_row["cnt"]) if cap_hit_row else 0

    # Count REQs that had any fixer action in last 14d
    fixable_row = await pool.fetchrow(
        """
        SELECT COUNT(DISTINCT req_id) AS cnt FROM verifier_decisions
        WHERE decision_action = 'fix'
          AND made_at > NOW() - INTERVAL '14 days'
        """,
    )
    fixable_count = int(fixable_row["cnt"]) if fixable_row else 0

    if fixable_count < min_samples:
        return None

    cap_hit_rate = cap_hit_count / fixable_count

    if cap_hit_rate >= _LOOP_CAP_HIT_RATE_UP and cap < _LOOP_CAP_MAX:
        new_cap = cap + 1
        return _Signal(
            rule_type="loop-cap",
            signal_data={
                "cap_hit_count": cap_hit_count,
                "fixable_req_count": fixable_count,
                "cap_hit_rate": round(cap_hit_rate, 3),
                "current_fixer_round_cap": cap,
            },
            proposed_change={
                "param": "fixer_round_cap",
                "from_value": cap,
                "to_value": new_cap,
            },
            bkd_description=textwrap.dedent(f"""\
                ## improver-autopilot: loop-cap (raise)

                {cap_hit_count}/{fixable_count} fixable REQs ({cap_hit_rate:.0%})
                escalated with `fixer-round-cap` in the last 14 days.
                The cap is cutting off fixers that might have succeeded.

                **Proposed change**: raise `fixer_round_cap`
                from `{cap}` → `{new_cap}` in
                `orchestrator/src/orchestrator/config.py`.

                Update the default value and add a changelog entry.
                Run `make ci-unit-test` to verify tests pass.
            """),
        )

    # Check if the cap could be lowered (max observed rounds much lower than cap)
    max_rounds_row = await pool.fetchrow(
        """
        SELECT COALESCE(MAX(fix_count), 0) AS max_rounds
        FROM (
            SELECT req_id, COUNT(*) AS fix_count
            FROM verifier_decisions
            WHERE decision_action = 'fix'
              AND made_at > NOW() - INTERVAL '14 days'
            GROUP BY req_id
        ) t
        """,
    )
    max_rounds = int(max_rounds_row["max_rounds"]) if max_rounds_row else 0

    if max_rounds < (cap - _LOOP_CAP_MAX_ROUNDS_DOWN) and cap > _LOOP_CAP_MIN:
        new_cap = cap - 1
        return _Signal(
            rule_type="loop-cap",
            signal_data={
                "max_observed_rounds": max_rounds,
                "current_fixer_round_cap": cap,
                "cap_hit_rate": round(cap_hit_rate, 3),
                "fixable_req_count": fixable_count,
            },
            proposed_change={
                "param": "fixer_round_cap",
                "from_value": cap,
                "to_value": new_cap,
            },
            bkd_description=textwrap.dedent(f"""\
                ## improver-autopilot: loop-cap (lower)

                Max observed fixer rounds in last 14 days is {max_rounds},
                well below `fixer_round_cap={cap}`. The cap is higher than
                needed, allowing extra compute to be spent on unproductive retries.

                **Proposed change**: lower `fixer_round_cap`
                from `{cap}` → `{new_cap}` in
                `orchestrator/src/orchestrator/config.py`.

                Update the default value and add a changelog entry.
                Run `make ci-unit-test` to verify tests pass.
            """),
        )
    return None


async def _eval_flake_tolerance(pool) -> _Signal | None:
    """Rule 3: infra flake rate is too high (raise retry_max) or too low (lower it)."""
    retry_max = settings.checker_infra_flake_retry_max
    min_samples = settings.improver_min_sample_count

    # 7-day flake rate for raise signal
    row7 = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE flake_reason IS NOT NULL) AS flake_count,
            COUNT(*) AS total_count
        FROM artifact_checks
        WHERE checked_at > NOW() - INTERVAL '7 days'
        """,
    )
    total7 = int(row7["total_count"]) if row7 else 0
    if total7 < min_samples:
        return None

    flake7 = int(row7["flake_count"]) if row7 else 0
    flake_rate7 = flake7 / total7 if total7 > 0 else 0.0

    if flake_rate7 >= _FLAKE_RATE_UP and retry_max < _FLAKE_RETRY_MAX:
        new_max = retry_max + 1
        return _Signal(
            rule_type="flake-tolerance",
            signal_data={
                "flake_count_7d": flake7,
                "total_count_7d": total7,
                "flake_rate_7d": round(flake_rate7, 4),
                "current_retry_max": retry_max,
            },
            proposed_change={
                "param": "checker_infra_flake_retry_max",
                "from_value": retry_max,
                "to_value": new_max,
            },
            bkd_description=textwrap.dedent(f"""\
                ## improver-autopilot: flake-tolerance (raise)

                Infra flake rate over the last 7 days is **{flake_rate7:.1%}**
                ({flake7}/{total7} checks). Increasing retry_max reduces
                unnecessary escalations from transient infra noise.

                **Proposed change**: raise `checker_infra_flake_retry_max`
                from `{retry_max}` → `{new_max}` in
                `orchestrator/src/orchestrator/config.py`.

                Update the default value and add a changelog entry.
                Run `make ci-unit-test` to verify tests pass.
            """),
        )

    # 14-day flake rate for lower signal
    row14 = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE flake_reason IS NOT NULL) AS flake_count,
            COUNT(*) AS total_count
        FROM artifact_checks
        WHERE checked_at > NOW() - INTERVAL '14 days'
        """,
    )
    total14 = int(row14["total_count"]) if row14 else 0
    if total14 < min_samples:
        return None

    flake14 = int(row14["flake_count"]) if row14 else 0
    flake_rate14 = flake14 / total14 if total14 > 0 else 0.0

    if flake_rate14 < _FLAKE_RATE_DOWN and retry_max > _FLAKE_RETRY_MIN:
        new_max = retry_max - 1
        return _Signal(
            rule_type="flake-tolerance",
            signal_data={
                "flake_count_14d": flake14,
                "total_count_14d": total14,
                "flake_rate_14d": round(flake_rate14, 4),
                "current_retry_max": retry_max,
            },
            proposed_change={
                "param": "checker_infra_flake_retry_max",
                "from_value": retry_max,
                "to_value": new_max,
            },
            bkd_description=textwrap.dedent(f"""\
                ## improver-autopilot: flake-tolerance (lower)

                Infra flake rate over the last 14 days is only **{flake_rate14:.1%}**
                ({flake14}/{total14} checks). Lowering retry_max reduces
                latency overhead from retries that almost never help.

                **Proposed change**: lower `checker_infra_flake_retry_max`
                from `{retry_max}` → `{new_max}` in
                `orchestrator/src/orchestrator/config.py`.

                Update the default value and add a changelog entry.
                Run `make ci-unit-test` to verify tests pass.
            """),
        )
    return None


async def _eval_throughput(pool) -> _Signal | None:
    """Rule 4: inflight-cap is causing too many escalations."""
    cap = settings.inflight_req_cap
    min_samples = settings.improver_min_sample_count

    row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM req_state
        WHERE state = 'escalated'
          AND context->>'escalated_reason' LIKE 'inflight-cap-exceeded:%'
          AND updated_at > NOW() - INTERVAL '7 days'
        """,
    )
    cap_escalations = int(row["cnt"]) if row else 0

    # Check if we have enough data (total REQs in window as proxy)
    total_row = await pool.fetchrow(
        "SELECT COUNT(*) AS cnt FROM req_state WHERE updated_at > NOW() - INTERVAL '7 days'",
    )
    total_reqs = int(total_row["cnt"]) if total_row else 0
    if total_reqs < min_samples:
        return None

    if cap_escalations >= _THROUGHPUT_ESCALATION_UP and cap < _INFLIGHT_CAP_MAX:
        new_cap = min(cap + 2, _INFLIGHT_CAP_MAX)
        return _Signal(
            rule_type="throughput",
            signal_data={
                "cap_escalation_count_7d": cap_escalations,
                "total_reqs_7d": total_reqs,
                "current_inflight_req_cap": cap,
            },
            proposed_change={
                "param": "inflight_req_cap",
                "from_value": cap,
                "to_value": new_cap,
            },
            bkd_description=textwrap.dedent(f"""\
                ## improver-autopilot: throughput

                {cap_escalations} REQs were rejected by `inflight-cap` in the
                last 7 days (current cap={cap}). Raising the cap allows more
                concurrent work without overwhelming the scheduler.

                **Proposed change**: raise `inflight_req_cap`
                from `{cap}` → `{new_cap}` in
                `orchestrator/src/orchestrator/config.py`.

                Update the default value and add a changelog entry.
                Run `make ci-unit-test` to verify tests pass.
            """),
        )
    return None


# ─── Budget & cooldown checks ─────────────────────────────────────────────────

async def _check_budget_and_cooldown(
    pool,
    signal: _Signal,
    now: datetime,
) -> str | None:
    """Return skip_reason string if the signal should be suppressed, else None."""
    window = now.date() - timedelta(days=now.weekday())
    used = await improver_runs.count_in_budget_window(pool, window)
    if used >= settings.improver_budget_per_window:
        return "budget"

    last = await improver_runs.last_non_skipped_at(pool, signal.rule_type)
    if last is not None:
        cooldown = timedelta(days=settings.improver_cooldown_per_rule_days)
        if (now - last.replace(tzinfo=UTC if last.tzinfo is None else last.tzinfo)) < cooldown:
            return "cooldown"

    return None


# ─── BKD issue creation (autopilot mode) ─────────────────────────────────────

async def _submit_to_bkd(signal: _Signal) -> str | None:
    """Create a BKD intent:analyze issue; return the issue id or None on error."""
    project_id = settings.improver_bkd_project_id
    if not project_id:
        return None
    change = signal.proposed_change
    title = (
        f"[improver] auto-tune {signal.rule_type}: "
        f"{change['param']} {change['from_value']} → {change['to_value']}"
    )
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            issue = await bkd.create_issue(
                project_id,
                title=title,
                tags=["sisyphus", "intent:analyze", "improver-autopilot"],
                description=signal.bkd_description,
                status_id="todo",
            )
        return issue.id
    except Exception as e:
        log.warning("improver.bkd_create_failed", rule_type=signal.rule_type, error=str(e))
        return None


# ─── Rule evaluators list ────────────────────────────────────────────────────

_RULES = [
    _eval_latency_guard,
    _eval_loop_cap,
    _eval_flake_tolerance,
    _eval_throughput,
]


# ─── Main tick ───────────────────────────────────────────────────────────────

async def _tick() -> dict:
    """Single scan: evaluate all rules, apply budget/cooldown, write improver_runs."""
    pool = db.get_pool()
    now = datetime.now(UTC)
    submitted = 0
    pending = 0
    skipped = 0

    for eval_fn in _RULES:
        try:
            signal = await eval_fn(pool)
        except Exception as e:
            log.warning("improver.rule_eval_failed", rule=eval_fn.__name__, error=str(e))
            continue

        if signal is None:
            continue

        skip_reason = await _check_budget_and_cooldown(pool, signal, now)

        if skip_reason:
            await improver_runs.insert_run(
                pool,
                signal.rule_type,
                signal.signal_data,
                signal.proposed_change,
                status="skipped",
                skip_reason=skip_reason,
                triggered_at=now,
            )
            skipped += 1
            log.info(
                "improver.signal_skipped",
                rule_type=signal.rule_type,
                skip_reason=skip_reason,
                proposed=signal.proposed_change,
            )
            continue

        # Budget OK — write pending row first, then try BKD
        run_id = await improver_runs.insert_run(
            pool,
            signal.rule_type,
            signal.signal_data,
            signal.proposed_change,
            status="pending",
            triggered_at=now,
        )

        if settings.improver_bkd_project_id:
            bkd_issue_id = await _submit_to_bkd(signal)
            if bkd_issue_id:
                await improver_runs.update_status(
                    pool,
                    run_id,
                    status="submitted",
                    bkd_issue_id=bkd_issue_id,
                    bkd_project_id=settings.improver_bkd_project_id,
                )
                submitted += 1
                log.warning(
                    "improver.signal_submitted",
                    rule_type=signal.rule_type,
                    proposed=signal.proposed_change,
                    bkd_issue_id=bkd_issue_id,
                )
            else:
                # BKD unavailable — keep as pending for human follow-up
                pending += 1
                log.warning(
                    "improver.signal_pending_bkd_failed",
                    rule_type=signal.rule_type,
                    proposed=signal.proposed_change,
                    run_id=run_id,
                )
        else:
            # detect-only mode
            pending += 1
            log.warning(
                "improver.signal_detected",
                rule_type=signal.rule_type,
                proposed=signal.proposed_change,
                hint="set SISYPHUS_IMPROVER_BKD_PROJECT_ID to enable autopilot",
            )

    return {"submitted": submitted, "pending": pending, "skipped": skipped}


async def run_loop() -> None:
    """Background task started by main.py on startup."""
    if not settings.improver_enabled:
        log.info("improver.disabled")
        return
    interval = settings.improver_interval_sec
    log.info("improver.loop.started", interval_sec=interval)
    while True:
        try:
            result = await _tick()
            if result["submitted"] or result["pending"]:
                log.warning("improver.tick", **result)
            else:
                log.debug("improver.tick", **result)
        except asyncio.CancelledError:
            log.info("improver.loop.stopped")
            raise
        except Exception as e:
            log.exception("improver.loop.error", error=str(e))
        await asyncio.sleep(interval)
