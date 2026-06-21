"""worker_deadman.py — auto-fire ShippingAlerts when scheduler jobs go dark (R117).

The worker scheduler runs ~25 background jobs (briefing, freezes, prunes,
snapshots, self-monitors). Each invocation is recorded to
``state.worker_runs`` — but until now NOTHING watched those records. A
critical daily job that silently stops running (cron died, a job started
raising on every pass, the machine was down at the briefing hour) produced
no signal: the operator only found out when a downstream artifact was
missing.

This module is the **deadman switch** for the worker itself. It reads the
per-job run telemetry that ``state.worker_runs.summarize_jobs`` already
produces and fires a ``ShippingAlert`` when a *critical* job is either:

  * **stale** — it has never run, or its last run is older than the
    expected cadence window (default ~25h, so a job that ran today is
    fine and a job that missed its daily slot screams). → CRITICAL.
  * **failing** — its last run raised, or its 24h success rate dropped
    below a floor while it was actually running. → HIGH.

Design (mirrors ``engine.source_health_alerts`` deliberately)
-------------------------------------------------------------
1. :func:`assess_worker_health` is **pure** — it takes the summary list,
   a fixed ``now_iso``, and the critical-job set, and returns a list of
   :class:`DeadmanFinding`. No DB, no clock, no I/O. Fully unit-testable.

2. :func:`check_worker_health_and_fire` is the orchestrator. It calls
   ``summarize_jobs()``, runs the pure assessment, and for each finding
   builds a ``ShippingAlert`` and persists it via
   ``alert_engine_v2.save_alerts`` — the exact construction +
   per-(user,job) ``kv_state`` cooldown idiom used by
   ``source_health_alerts``. Cooldown is stamped ONLY after a
   successful fire. Returns ``{"fired", "skipped_cooldown", "errored"}``.

3. Per-finding try/except inside the loop. One bad finding (DB write
   blip) must NOT block the rest. The orchestrator itself NEVER raises —
   a deadman that crashes the worker is worse than useless.

4. No SCHEMA_VERSION bump. The cooldown rides the existing ``kv_state``
   table under the ``worker_deadman_cooldown:`` key prefix, exactly as
   source-health alerting rides ``source_alert_cooldown:``.

Critical-job default
--------------------
``_DEFAULT_CRITICAL_JOBS`` is a small, deliberately-conservative set of
the daily jobs whose silent disappearance actually hurts:

  * ``run_daily_briefing_job``       — the headline daily build.
  * ``run_signal_ledger_freeze_job`` — the point-in-time signal freeze
    (R004); a gap here corrupts the track record permanently.
  * ``run_health_ping_job``          — the source-health probe; if it
    stops, EVERY other health alert goes blind (it's the data feed
    behind ``source_health_alerts``).
  * ``run_source_health_alert_job``  — the source-health self-monitor.

We intentionally do NOT make every prune/GC job critical: a missed prune
is cosmetic (it reruns tomorrow), and alerting on it would be noise. The
caller can pass its own ``critical_jobs`` to widen or narrow the set.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Critical-job default + thresholds
# ─────────────────────────────────────────────────────────────────────────────

# The small set of jobs whose silent disappearance actually hurts. See the
# module docstring for the rationale behind each entry. All are daily- or
# faster-cadence jobs, so the default ~25h staleness window is right.
_DEFAULT_CRITICAL_JOBS: frozenset[str] = frozenset({
    "run_daily_briefing_job",
    "run_signal_ledger_freeze_job",
    "run_health_ping_job",
    "run_source_health_alert_job",
})

# Default staleness window in minutes. 1500 min == 25h — a daily-cadence
# job that ran any time today is fine; one that missed its slot (or has
# never run) is stale. Source-health uses a much tighter window because its
# cadence is 5-minutely; the deadman watches DAILY jobs, hence the wider
# default.
_DEFAULT_STALENESS_MINUTES: int = 1500

# A job actually running in-window but succeeding less than half the time
# is failing, not merely flaky.
_DEFAULT_MIN_SUCCESS_RATE: float = 0.5

# kv_state cooldown key prefix (mirrors source_health_alerts'
# ``source_alert_cooldown:``). Per-(user, job) so a chronically-stale job
# doesn't carpet-bomb the alert table.
_COOLDOWN_KEY_PREFIX: str = "worker_deadman_cooldown:"

# Default cooldown between two fires for the same job for the same user.
# 6h: long enough that a job stuck stale across several worker passes
# produces at most a handful of alerts a day, short enough that the
# operator gets a fresh nudge each quarter-day until it's fixed.
_DEFAULT_COOLDOWN_MINUTES: int = 360


# ─────────────────────────────────────────────────────────────────────────────
#  Finding dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeadmanFinding:
    """One thing wrong with a critical worker job.

    Attributes
    ----------
    job_name:
        The ``run_*_job`` whose telemetry tripped the check.
    reason:
        Short machine-ish tag — ``'stale'`` or ``'failing'``.
    detail:
        Human-readable one-liner for the alert body (e.g.
        "last run 2026-06-04T03:00:00+00:00 is 51.0h old (> 25.0h)").
    severity:
        ``'CRITICAL'`` (stale — the job may have stopped entirely) or
        ``'HIGH'`` (failing — the job is running but erroring).
    """
    job_name: str
    reason: str
    detail: str
    severity: str


# ─────────────────────────────────────────────────────────────────────────────
#  Pure assessment
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; treat tz-naive as UTC. None on failure."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def assess_worker_health(
    summaries: Iterable[dict],
    *,
    now_iso: str,
    critical_jobs: Iterable[str],
    staleness_minutes: int = _DEFAULT_STALENESS_MINUTES,
    min_success_rate: float = _DEFAULT_MIN_SUCCESS_RATE,
) -> list[DeadmanFinding]:
    """Decide which critical jobs are stale or failing. PURE — no DB, no clock.

    Parameters
    ----------
    summaries:
        The output of ``state.worker_runs.summarize_jobs`` — one dict per
        job with keys ``job_name``, ``last_status`` ('ok'|'error'|'NEVER'),
        ``last_run_at`` (ISO str or ''), ``runs_in_window`` (24h count),
        ``success_rate_24h`` (float [0,1]; 1.0 with runs_in_window=0 means
        "no data").
    now_iso:
        The reference "now" as an ISO-8601 string. Passed in (not read
        from the clock) so the function is deterministic + unit-testable.
    critical_jobs:
        The set of job_names to monitor. Jobs not in this set yield no
        finding regardless of their state.
    staleness_minutes:
        A job whose ``last_run_at`` is older than this (or 'NEVER', or
        unparseable) is STALE → CRITICAL. Default ~25h so a daily job
        that ran today passes.
    min_success_rate:
        A job that ran in-window (``runs_in_window > 0``) but whose
        ``success_rate_24h`` is below this is FAILING → HIGH.

    Returns
    -------
    list[DeadmanFinding]
        One finding per tripped critical job, in the iteration order of
        ``summaries``. A job that is BOTH stale and failing yields a
        single STALE finding (stale is the strictly-worse condition — the
        job may have stopped entirely; we don't need to also say it was
        failing on the way out). Empty list when nothing is wrong.

    NEVER raises — a malformed summary entry is skipped, an unparseable
    ``now_iso`` makes every timestamp comparison fall back to "stale"
    (fail-loud rather than fail-silent: a broken clock should surface the
    jobs, not hide them).
    """
    critical = set(str(j) for j in (critical_jobs or ()))
    if not critical:
        return []

    now_dt = _parse_iso(now_iso)
    # If now_iso itself is unparseable we cannot do age math. Rather than
    # silently passing every job, treat staleness as unknown→stale below
    # (a broken reference clock is itself an operational problem worth
    # surfacing). last_status/'NEVER'/success-rate checks still work.
    threshold = max(0, int(staleness_minutes))

    findings: list[DeadmanFinding] = []
    for row in summaries or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("job_name", "") or "")
        if name not in critical:
            continue

        last_status = str(row.get("last_status", "") or "").upper()
        last_run_at = row.get("last_run_at", "")

        # ── STALE check (CRITICAL) — strictly worse, evaluated first ──
        # 'NEVER' → stale. Else compare last_run_at age to the window. An
        # unparseable last_run_at (or unparseable now_iso) is treated as
        # stale: we'd rather alert on a job we can't prove is alive than
        # stay silent.
        stale = False
        detail = ""
        if last_status == "NEVER":
            stale = True
            detail = "job has NEVER run"
        else:
            last_dt = _parse_iso(last_run_at if isinstance(last_run_at, str) else "")
            if last_dt is None or now_dt is None:
                stale = True
                detail = f"last run timestamp unusable ({last_run_at!r})"
            else:
                age_min = (now_dt - last_dt).total_seconds() / 60.0
                if age_min >= threshold:
                    stale = True
                    detail = (
                        f"last run {last_run_at} is {age_min / 60.0:.1f}h old "
                        f"(> {threshold / 60.0:.1f}h cadence window)"
                    )

        if stale:
            findings.append(DeadmanFinding(
                job_name=name,
                reason="stale",
                detail=detail,
                severity="CRITICAL",
            ))
            continue

        # ── FAILING check (HIGH) — only reached when NOT stale ──
        # last_status == 'error' OR (it ran in-window but the success rate
        # cratered). The runs_in_window>0 guard is essential: a quiet job
        # reports success_rate_24h=1.0 with runs_in_window=0 ("no data"),
        # which must NOT read as failing.
        try:
            runs_in_window = int(row.get("runs_in_window", 0) or 0)
        except (TypeError, ValueError):
            runs_in_window = 0
        try:
            success_rate = float(row.get("success_rate_24h", 1.0))
        except (TypeError, ValueError):
            success_rate = 1.0

        failing = False
        if last_status == "ERROR":
            failing = True
            detail = "most recent run ended in error"
        elif runs_in_window > 0 and success_rate < float(min_success_rate):
            failing = True
            detail = (
                f"24h success rate {success_rate:.0%} < "
                f"{float(min_success_rate):.0%} over {runs_in_window} run(s)"
            )

        if failing:
            findings.append(DeadmanFinding(
                job_name=name,
                reason="failing",
                detail=detail,
                severity="HIGH",
            ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
#  User-id resolution + cooldown helpers (mirror source_health_alerts)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the user_id to scope the cooldown to.

    Mirrors ``source_health_alerts._resolve_user_id``: explicit non-None
    ``user_id`` wins (tests pin a known id), otherwise consult the
    Streamlit session via ``current_user_id`` (returns ``""`` outside
    Streamlit — the legacy global bucket). Never raises.
    """
    if user_id is None:
        try:
            from state.user_scope import current_user_id
            return current_user_id()
        except Exception:
            return ""
    if not isinstance(user_id, str):
        return ""
    return user_id


def _cooldown_key(user_id: str, job_name: str) -> str:
    """kv_state row key for a (user, job) cooldown timestamp."""
    return f"{_COOLDOWN_KEY_PREFIX}{user_id}:{job_name}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def get_deadman_cooldown(
    job_name: str, *, user_id: Optional[str] = None
) -> Optional[str]:
    """Return the ISO timestamp of the last deadman fire for ``(user, job)``,
    or ``None`` if no prior fire exists. Never raises — a DB read failure
    returns ``None`` (the safe default that lets the loop re-fire, so
    missing cooldown data never silences a real outage)."""
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_cooldown_key(uid, str(job_name or "")),),
        ).fetchone()
        if row is None:
            return None
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return None
        return str(raw)
    except Exception as exc:
        logger.debug(
            f"worker_deadman.get_deadman_cooldown: read failed for "
            f"user_id={uid!r} job={job_name!r}: {exc}"
        )
        return None


def set_deadman_cooldown(
    job_name: str, *, user_id: Optional[str] = None
) -> None:
    """Mark ``(user, job)`` as having just fired. Best-effort — a write
    failure is logged at debug; the next pass simply re-fires (worst
    case: one duplicate alert)."""
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_cooldown_key(uid, str(job_name or "")), now, now),
            )
    except Exception as exc:
        logger.debug(
            f"worker_deadman.set_deadman_cooldown: write failed for "
            f"user_id={uid!r} job={job_name!r}: {exc}"
        )


def _within_cooldown(
    job_name: str, cooldown_minutes: int, *, user_id: Optional[str] = None
) -> bool:
    """True iff a prior deadman fire for ``(user, job)`` was within the window."""
    if cooldown_minutes <= 0:
        return False
    last = get_deadman_cooldown(job_name, user_id=user_id)
    if last is None:
        return False
    last_dt = _parse_iso(last)
    if last_dt is None:
        return False
    return (_now_utc() - last_dt) < timedelta(minutes=cooldown_minutes)


# ─────────────────────────────────────────────────────────────────────────────
#  Alert construction (mirrors source_health_alerts._build_alert)
# ─────────────────────────────────────────────────────────────────────────────

def _build_alert(finding: DeadmanFinding):
    """Build a ShippingAlert for a deadman finding.

    Lazy import of ``ShippingAlert`` keeps this module light for callers
    that only want the pure assessment. The job_name rides in
    ``port_locode`` as the entity key so the standard
    (alert_type, severity, port_locode) dedup collapses repeat fires for
    the same job — exactly as source-health rides the source_id there.
    """
    from engine.alert_engine_v2 import (
        ShippingAlert,
        _new_id,
        _now_iso as alerts_now_iso,
    )

    verb = "stale" if finding.reason == "stale" else "failing"
    title = f"Worker job {finding.job_name} is {verb}"
    body = (
        f"Scheduler job '{finding.job_name}' is {verb}: {finding.detail}. "
        f"Auto-alert tier: {finding.severity}. Investigate the worker "
        f"(cron alive? job raising every pass?) — this is the deadman "
        f"self-monitor (R117)."
    )
    return ShippingAlert(
        alert_id=_new_id(),
        created_at=alerts_now_iso(),
        alert_type="WORKER_DEADMAN",
        severity=finding.severity,
        title=title,
        body=body,
        ticker="",
        route_id="",
        # job_name as the dedup entity key (truncated like source_health).
        port_locode=str(finding.job_name or "")[:32],
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def check_worker_health_and_fire(
    *,
    now: Optional[datetime] = None,
    critical_jobs: Optional[Iterable[str]] = None,
    staleness_minutes: int = _DEFAULT_STALENESS_MINUTES,
    min_success_rate: float = _DEFAULT_MIN_SUCCESS_RATE,
    cooldown_minutes: int = _DEFAULT_COOLDOWN_MINUTES,
    user_id: Optional[str] = None,
) -> dict:
    """Read worker run telemetry and fire alerts for stale / failing jobs.

    Steps:
      1. Resolve ``user_id`` (explicit > current Streamlit user > legacy
         empty bucket).
      2. Call ``state.worker_runs.summarize_jobs()`` for the per-job
         telemetry.
      3. Run the pure ``assess_worker_health`` against ``now`` (default:
         wall clock) and the critical-job set (default:
         ``_DEFAULT_CRITICAL_JOBS``).
      4. For each finding:
         * Skip if within cooldown (counts as ``skipped_cooldown``).
         * Build a ShippingAlert and persist via ``save_alerts``. Mark the
           cooldown ONLY after a successful save.
         * Any per-finding exception increments ``errored`` and does NOT
           break the loop.
      5. Return ``{"fired": N, "skipped_cooldown": N, "errored": N}``.

    NEVER raises at the top level — every exception path collapses to a
    populated count dict. A ``summarize_jobs`` that raises returns
    ``{"fired": 0, "skipped_cooldown": 0, "errored": 1}`` so the caller
    sees the failure in the counters.
    """
    counts = {"fired": 0, "skipped_cooldown": 0, "errored": 0}
    uid = _resolve_user_id(user_id)
    crit = critical_jobs if critical_jobs is not None else _DEFAULT_CRITICAL_JOBS
    now_dt = now or _now_utc()
    try:
        now_iso = now_dt.isoformat()
    except Exception:
        now_iso = _now_iso()

    try:
        from state.worker_runs import summarize_jobs
        summaries = summarize_jobs()
    except Exception as exc:
        logger.debug(
            f"worker_deadman.check_worker_health_and_fire: "
            f"summarize_jobs failed: {exc}"
        )
        counts["errored"] += 1
        return counts

    try:
        findings = assess_worker_health(
            summaries,
            now_iso=now_iso,
            critical_jobs=crit,
            staleness_minutes=staleness_minutes,
            min_success_rate=min_success_rate,
        )
    except Exception as exc:  # assess is non-raising by contract; belt-and-braces
        logger.debug(
            f"worker_deadman.check_worker_health_and_fire: "
            f"assess_worker_health failed: {exc}"
        )
        counts["errored"] += 1
        return counts

    for finding in findings:
        try:
            if _within_cooldown(
                finding.job_name, cooldown_minutes, user_id=uid
            ):
                counts["skipped_cooldown"] += 1
                continue

            alert = _build_alert(finding)

            # Lazy import save_alerts so a broken engine.alert_engine_v2
            # import doesn't kill the WHOLE loop — the per-finding
            # try/except catches and counts it.
            from engine.alert_engine_v2 import save_alerts

            save_alerts([alert], user_id=uid)

            # Only stamp cooldown AFTER a successful save. If save_alerts
            # raised, the next pass retries — the operator is not left blind.
            set_deadman_cooldown(finding.job_name, user_id=uid)
            counts["fired"] += 1
        except Exception as exc:
            logger.debug(
                f"worker_deadman.check_worker_health_and_fire: "
                f"finding={finding.job_name!r} failed: {exc}"
            )
            counts["errored"] += 1
            continue

    return counts


__all__ = [
    "DeadmanFinding",
    "assess_worker_health",
    "check_worker_health_and_fire",
    "get_deadman_cooldown",
    "set_deadman_cooldown",
]
