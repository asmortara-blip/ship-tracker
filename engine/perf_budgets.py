"""perf_budgets.py — per-tab render-latency budgets + breach detection.

Operators set a maximum p95 render time per tab (e.g. ``2.0s`` for the
alerts tab). When a tab exceeds that budget over the look-back window,
this module fires a ``PERF_BUDGET`` ShippingAlert so the operator sees
the regression without manually scrolling the perf panel.

Design
------
The companion to ``engine.perf_telemetry``: that module records
per-tab render durations; this module is the read-only consumer that
classifies them against operator-chosen budgets.

1. :class:`PerfBudget` — one row in the budget table: tab module,
   max p95 (seconds), optional max mean (seconds), and the window
   the operator wants the check to evaluate over.

2. :class:`BudgetBreach` — emitted by :func:`check_budgets` for every
   tab whose observed p95 exceeded its budget. Carries the budget,
   the observation, the sample count, and a severity classification:
     * ``'warn'``     when budget < observed_p95 <= 2x budget
     * ``'critical'`` when observed_p95 > 2x budget

3. :func:`get_default_budgets` — sensible defaults shipped with the
   module so a fresh install has coverage from minute one without
   the operator having to write a config.

4. :func:`load_budgets` / :func:`save_budgets` — per-user persistence
   via the existing ``kv_state`` table under ``perf_budgets:<user_id>``.
   No schema bump — same pattern as ``state.user_filters`` and
   ``engine.source_health_alerts``.

5. :func:`check_budgets` — single pass: call ``get_perf_summary`` for
   the configured window, compare each tab's observed p95 against its
   budget, emit a ``BudgetBreach`` for every miss. Tabs with fewer than
   ``_MIN_SAMPLES`` observations are skipped — small samples are noise
   and the operator should not get woken up because one slow render
   blew out the p95 of a 3-sample bucket.

6. :func:`check_and_alert` — calls ``check_budgets``, fires a
   ``PERF_BUDGET`` ShippingAlert for each breach (subject to a per-tab
   cooldown so a chronically-slow tab does not carpet-bomb the alert
   table). Severity maps:
     * ``critical`` breach → ``HIGH`` ShippingAlert (the loudest tier
       still gated by the cooldown)
     * ``warn``     breach → ``MEDIUM`` ShippingAlert

7. Per-tab try/except inside the loop. One bad budget row (malformed
   blob from a future schema, missing tab name) must NOT block the
   rest of the loop. Errored tabs increment ``errored`` and the loop
   continues. The orchestrator itself never raises.

Storage
-------
* Budgets: kv_state row ``perf_budgets:<user_id>`` carrying a JSON
  list of budget records. Empty / missing row → defaults.
* Cooldown: kv_state row
  ``perf_budget_cooldown:<user_id>:<tab_module>`` carrying the ISO
  timestamp of the last successful fire. Suppression window is the
  budget's ``window_hours`` — preventing two fires for the same tab
  within the same evaluation window.

Min-sample threshold
--------------------
``_MIN_SAMPLES = 5``. The p95 of a 3-sample bucket is degenerate —
one slow render dominates and fires a spurious breach. Five samples is
the smallest number where the p95 is starting to mean something for a
tab that renders a few times an hour. Documented at the constant for
future tuners.

What this module is NOT
-----------------------
* It does NOT mutate ``engine.perf_telemetry`` — read-only consumer.
* It does NOT bypass the alert dedup machinery — every alert flows
  through ``save_alerts`` with the standard dedup shape.
* It does NOT bump SCHEMA_VERSION. Budgets + cooldowns ride the
  existing ``kv_state`` table.
* It does NOT fire when the per-tab cooldown is active — the operator
  has already been told this tab is slow this window.
* It does NOT fire for tabs with < ``_MIN_SAMPLES`` observations.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


# ─── Storage keys ──────────────────────────────────────────────────────────

_BUDGET_KEY_PREFIX = "perf_budgets:"
_COOLDOWN_KEY_PREFIX = "perf_budget_cooldown:"

# Minimum number of render-event samples a tab needs before a breach
# can fire. Below this, the p95 is statistical noise — one slow render
# in a 3-sample bucket fires a spurious 'critical'. Five is the smallest
# number where the p95 is starting to mean something for a tab that
# renders a few times an hour.
_MIN_SAMPLES = 5


# ─── Dataclasses ──────────────────────────────────────────────────────────

@dataclass
class PerfBudget:
    """Per-tab render-latency budget.

    Attributes
    ----------
    tab_module:
        The dotted module path of the tab (e.g. ``'ui.tab_alerts'``).
        Matches the ``tab_name`` arg passed to ``track_render`` — the
        check loop compares this string verbatim against the keys of
        ``get_perf_summary(...)['by_tab']``. NOT a free-form label —
        a typo here means the budget never matches.
    max_p95_seconds:
        Hard ceiling on the observed p95 in the window. When
        ``observed_p95 > max_p95_seconds`` the tab is in breach.
    max_mean_seconds:
        Optional secondary check on the observed mean. When provided
        AND the mean exceeds it, the breach severity is bumped one
        tier (warn → critical, critical stays critical). When None
        (default) the mean is reported but not used to classify.
    window_hours:
        Look-back window in hours. Default 24. Smaller windows catch
        regressions faster; larger windows smooth out noisy reloads.
    """
    tab_module: str
    max_p95_seconds: float
    max_mean_seconds: Optional[float] = None
    window_hours: int = 24


@dataclass
class BudgetBreach:
    """One tab's budget violation, ready to render or fire as an alert.

    Attributes
    ----------
    tab_module:
        The tab that breached. Mirrors ``PerfBudget.tab_module``.
    observed_p95:
        The actual p95 in seconds from ``get_perf_summary``.
    observed_mean:
        The actual mean in seconds. Always populated — used by the UI
        and the alert body even when ``max_mean_seconds`` is None.
    budget_p95:
        The ceiling the tab exceeded.
    budget_mean:
        The secondary ceiling, or ``None`` when no mean check was
        configured for this tab.
    sample_count:
        Number of render events in the window. Always >= ``_MIN_SAMPLES``
        by construction (the check loop skips tabs with fewer samples).
    window_hours:
        Look-back window the check was evaluated over.
    severity:
        Classification string:
          * ``'warn'``     — budget < observed_p95 <= 2x budget
          * ``'critical'`` — observed_p95 > 2x budget
    """
    tab_module: str
    observed_p95: float
    observed_mean: float
    budget_p95: float
    budget_mean: Optional[float]
    sample_count: int
    window_hours: int
    severity: str


# ─── User-id resolution ────────────────────────────────────────────────────

def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the user_id to scope budgets + cooldown to.

    Mirrors the resolution rule in ``source_health_alerts._resolve_user_id``:
    explicit non-None ``user_id`` wins (so tests can pin a known id),
    otherwise consult the Streamlit session via ``current_user_id``
    (returns ``""`` outside Streamlit — the legacy global bucket).
    Never raises.
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


def _budget_key(user_id: str) -> str:
    """kv_state row key for the budget list blob."""
    return f"{_BUDGET_KEY_PREFIX}{user_id}"


def _cooldown_key(user_id: str, tab_module: str) -> str:
    """kv_state row key for a (user, tab) cooldown timestamp."""
    return f"{_COOLDOWN_KEY_PREFIX}{user_id}:{tab_module}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


# ─── Defaults ──────────────────────────────────────────────────────────────

# Per-tab defaults. Tabs not listed here fall back to the catch-all
# 2.5s budget (see ``get_default_budgets``). Tuned by tab weight:
#   * lightweight aggregators (overview, status) get 1.5s
#   * standard report / alert tabs get 2.0s
#   * heavy chart / overview pages get 3-4s
_DEFAULT_BUDGETS: list[tuple[str, float]] = [
    ("ui.tab_overview",          1.5),
    ("ui.tab_alerts",            2.0),
    ("ui.tab_reports",           2.0),
    ("ui.tab_deep_dive",         4.0),
    ("ui.tab_operator_overview", 3.0),
    ("ui.tab_data_health",       2.5),
    ("ui.tab_briefing",          2.0),
    ("ui.tab_portfolio",         2.5),
    ("ui.tab_scorecard",         2.0),
    ("ui.tab_backtest",          3.0),
]

# Catch-all default for any tab not listed above. Anything slower than
# this on a tab the operator never customised is worth flagging.
_CATCH_ALL_P95_SECONDS = 2.5


def get_default_budgets() -> list[PerfBudget]:
    """Return the shipped default budget list.

    Used by :func:`load_budgets` when no customised row exists for the
    user, and by the UI/CLI ``reset`` paths. Always returns a fresh
    list so callers can mutate the result without poisoning the next
    call.
    """
    return [
        PerfBudget(
            tab_module=tab,
            max_p95_seconds=p95,
            max_mean_seconds=None,
            window_hours=24,
        )
        for tab, p95 in _DEFAULT_BUDGETS
    ]


# ─── Budget load / save ───────────────────────────────────────────────────

def _record_to_budget(record: Any) -> Optional[PerfBudget]:
    """Map one JSON-decoded record to a :class:`PerfBudget`.

    Malformed (wrong type, missing required field, non-numeric ceiling)
    → None so the caller can skip it without raising on one bad row.
    """
    if not isinstance(record, dict):
        return None
    tab = record.get("tab_module")
    p95 = record.get("max_p95_seconds")
    mean = record.get("max_mean_seconds")
    window = record.get("window_hours", 24)
    if not isinstance(tab, str) or not tab:
        return None
    try:
        p95_f = float(p95)
    except (TypeError, ValueError):
        return None
    if p95_f <= 0:
        return None
    try:
        mean_f: Optional[float] = float(mean) if mean is not None else None
    except (TypeError, ValueError):
        mean_f = None
    if mean_f is not None and mean_f <= 0:
        mean_f = None
    try:
        window_i = int(window)
    except (TypeError, ValueError):
        window_i = 24
    if window_i <= 0:
        window_i = 24
    return PerfBudget(
        tab_module=tab,
        max_p95_seconds=p95_f,
        max_mean_seconds=mean_f,
        window_hours=window_i,
    )


def load_budgets(*, user_id: Optional[str] = None) -> list[PerfBudget]:
    """Return the user's saved budgets, or defaults when none are saved.

    Missing row → defaults. Empty list saved by the user → defaults
    (a saved empty list is treated as "I haven't customised; give me
    defaults" — the explicit reset path uses ``save_budgets([])`` for
    that purpose). Malformed JSON / DB read failure → defaults.
    NEVER raises.
    """
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_budget_key(uid),)
        ).fetchone()
        if row is None:
            return get_default_budgets()
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return get_default_budgets()
        parsed = json.loads(raw)
    except Exception as exc:
        logger.debug(
            f"perf_budgets.load_budgets: read failed for user_id={uid!r}: {exc}"
        )
        return get_default_budgets()

    if not isinstance(parsed, list) or not parsed:
        return get_default_budgets()

    budgets: list[PerfBudget] = []
    for record in parsed:
        b = _record_to_budget(record)
        if b is not None:
            budgets.append(b)
    return budgets if budgets else get_default_budgets()


def save_budgets(
    budgets: list[PerfBudget], *, user_id: Optional[str] = None
) -> bool:
    """Persist the budget list to kv_state. Returns success.

    Passing an empty list deliberately writes an empty JSON array —
    next call to :func:`load_budgets` treats that as "reset to defaults"
    (the row exists but is empty). NEVER raises — write errors are
    logged at debug and the return value (False) tells the caller
    (typically the UI Save button) to surface a "couldn't save" message.
    """
    uid = _resolve_user_id(user_id)
    try:
        # Filter out non-PerfBudget inputs defensively so a caller
        # that passes a mixed list does not corrupt the blob.
        records = [
            asdict(b) for b in budgets if isinstance(b, PerfBudget)
        ]
        payload = json.dumps(records)
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_budget_key(uid), payload, _now_iso()),
            )
        return True
    except Exception as exc:
        logger.debug(
            f"perf_budgets.save_budgets: write failed for user_id={uid!r}: {exc}"
        )
        return False


# ─── Cooldown helpers ─────────────────────────────────────────────────────

def _get_cooldown(user_id: str, tab_module: str) -> Optional[str]:
    """Return the ISO timestamp of the last fire for (user, tab) or None."""
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_cooldown_key(user_id, tab_module),),
        ).fetchone()
        if row is None:
            return None
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return None
        return str(raw)
    except Exception as exc:
        logger.debug(
            f"perf_budgets._get_cooldown: read failed for "
            f"user_id={user_id!r} tab={tab_module!r}: {exc}"
        )
        return None


def _set_cooldown(user_id: str, tab_module: str) -> None:
    """Stamp now-ISO into the cooldown row for (user, tab). Best-effort."""
    try:
        from state.db import get_connection

        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_cooldown_key(user_id, tab_module), now, now),
            )
    except Exception as exc:
        logger.debug(
            f"perf_budgets._set_cooldown: write failed for "
            f"user_id={user_id!r} tab={tab_module!r}: {exc}"
        )


def _within_cooldown(
    user_id: str,
    tab_module: str,
    window_hours: int,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True iff a prior fire for (user, tab) was within ``window_hours``.

    The cooldown window is exactly the budget's window — the operator
    has already been told this tab is slow over the current evaluation
    window, so re-firing inside it adds no information.
    """
    if window_hours <= 0:
        return False
    last = _get_cooldown(user_id, tab_module)
    if last is None:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    current = now if now is not None else _now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - last_dt) < timedelta(hours=window_hours)


# ─── Classification + check loop ──────────────────────────────────────────

def _classify_severity(observed_p95: float, budget_p95: float) -> str:
    """Map an observation against its budget to a severity label.

    Boundary rules (closed at the lower end):
      * observed_p95 <= budget_p95              → caller should NOT call us
        (no breach); we return ``'warn'`` as the safe default but the
        loop short-circuits before reaching here.
      * budget_p95 < observed_p95 <= 2x budget  → 'warn'
      * observed_p95 > 2x budget                → 'critical'
    """
    if budget_p95 <= 0:
        return "warn"
    ratio = observed_p95 / budget_p95
    if ratio > 2.0:
        return "critical"
    return "warn"


def check_budgets(
    *, user_id: Optional[str] = None, now: Optional[datetime] = None
) -> list[BudgetBreach]:
    """Compare every budget against the current perf summary and return
    a list of breaches.

    Steps:
      1. Resolve user_id (explicit > Streamlit session > legacy bucket).
      2. Load budgets (custom if saved, defaults otherwise).
      3. Group budgets by window_hours so we hit ``get_perf_summary``
         once per unique window — small saving but real when an
         operator has 30 budgets all sharing the same 24h window.
      4. For each budget, look up the matching tab in the summary.
         Skip if the tab has no observations (no data, no breach).
         Skip if sample_count < ``_MIN_SAMPLES`` (noise).
         Skip if observed_p95 <= budget_p95 (no breach).
         Otherwise classify and emit a BudgetBreach.

    NEVER raises. Any per-budget exception is swallowed and the loop
    continues with the next budget. Returns ``[]`` on any top-level
    exception (e.g. a broken get_perf_summary).
    """
    try:
        uid = _resolve_user_id(user_id)
        budgets = load_budgets(user_id=uid)
        if not budgets:
            return []

        # Group by window so we don't call get_perf_summary N times.
        # Pre-load every unique window once and cache the results.
        windows = sorted({int(b.window_hours) for b in budgets})
        summaries: dict[int, dict] = {}
        from engine.perf_telemetry import get_perf_summary

        for w in windows:
            try:
                summaries[w] = get_perf_summary(window_hours=w) or {}
            except Exception as exc:
                logger.debug(
                    f"perf_budgets.check_budgets: get_perf_summary({w}) "
                    f"failed: {exc}"
                )
                summaries[w] = {}

        breaches: list[BudgetBreach] = []
        for budget in budgets:
            try:
                summary = summaries.get(int(budget.window_hours), {})
                by_tab = summary.get("by_tab") if isinstance(summary, dict) else None
                if not isinstance(by_tab, dict):
                    continue
                stats = by_tab.get(budget.tab_module)
                if not isinstance(stats, dict):
                    continue
                count = int(stats.get("count", 0) or 0)
                if count < _MIN_SAMPLES:
                    continue
                observed_p95_ms = int(stats.get("p95_ms", 0) or 0)
                observed_p95_s = observed_p95_ms / 1000.0
                # Use the real mean (get_perf_summary now emits mean_ms). This
                # field + the max_mean_seconds bump below are named for the
                # mean; they previously read median_ms, so the alert body
                # mislabeled the median as the "mean" and the severity bump
                # compared a mean budget against the median (under-firing for
                # right-skewed render latency).
                observed_mean_ms = int(stats.get("mean_ms", 0) or 0)
                observed_mean_s = observed_mean_ms / 1000.0

                if observed_p95_s <= budget.max_p95_seconds:
                    continue

                severity = _classify_severity(
                    observed_p95_s, budget.max_p95_seconds
                )
                # Secondary check — if the operator set a mean budget
                # AND the mean exceeds it, bump severity one tier.
                if (
                    budget.max_mean_seconds is not None
                    and observed_mean_s > budget.max_mean_seconds
                    and severity == "warn"
                ):
                    severity = "critical"

                breaches.append(
                    BudgetBreach(
                        tab_module=budget.tab_module,
                        observed_p95=round(observed_p95_s, 4),
                        observed_mean=round(observed_mean_s, 4),
                        budget_p95=float(budget.max_p95_seconds),
                        budget_mean=(
                            float(budget.max_mean_seconds)
                            if budget.max_mean_seconds is not None
                            else None
                        ),
                        sample_count=count,
                        window_hours=int(budget.window_hours),
                        severity=severity,
                    )
                )
            except Exception as exc:
                logger.debug(
                    f"perf_budgets.check_budgets: per-budget tab="
                    f"{getattr(budget, 'tab_module', '?')!r} failed: {exc}"
                )
                continue
        return breaches
    except Exception as exc:
        logger.debug(f"perf_budgets.check_budgets: top-level failed: {exc}")
        return []


# ─── Alert firing ─────────────────────────────────────────────────────────

def _build_alert(breach: BudgetBreach):
    """Build a ShippingAlert from a BudgetBreach.

    Severity mapping:
      * critical breach → HIGH ShippingAlert (the loudest tier we still
        cooldown-gate; CRITICAL is reserved for source-health red).
      * warn     breach → MEDIUM ShippingAlert

    The body carries every number the operator needs to triage:
    observed p95 + mean, the budget, the sample count, the window,
    and the severity classification. tab_module rides in ``port_locode``
    as the entity key so the existing dedup machinery collapses two
    near-simultaneous fires for the same tab — same pattern
    ``source_health_alerts`` uses for the source id.
    """
    from engine.alert_engine_v2 import ShippingAlert, _new_id, _now_iso as alerts_now_iso

    alert_severity = "HIGH" if breach.severity == "critical" else "MEDIUM"
    title = f"Tab {breach.tab_module} exceeded perf budget"
    mean_str = (
        f" / mean {breach.observed_mean:.2f}s"
        if breach.budget_mean is None
        else f" / mean {breach.observed_mean:.2f}s (budget {breach.budget_mean:.2f}s)"
    )
    body = (
        f"Tab '{breach.tab_module}' p95 was {breach.observed_p95:.2f}s "
        f"(budget {breach.budget_p95:.2f}s){mean_str} "
        f"over {breach.window_hours}h window across {breach.sample_count} "
        f"renders. Classification: {breach.severity}."
    )
    return ShippingAlert(
        alert_id=_new_id(),
        created_at=alerts_now_iso(),
        alert_type="PERF_BUDGET",
        severity=alert_severity,
        title=title,
        body=body,
        ticker="",
        route_id="",
        # tab_module piggybacks on port_locode as the dedup entity key —
        # truncated to the column width like the source-health alerter does.
        port_locode=str(breach.tab_module or "")[:32],
        value=float(breach.observed_p95),
        threshold=float(breach.budget_p95),
        change_pct=0.0,
        acknowledged=False,
    )


def check_and_alert(*, user_id: Optional[str] = None) -> dict:
    """Run :func:`check_budgets` and fire alerts for each breach.

    Returns a count dict shaped like the source-health alerter so the
    CLI and the worker can log a one-line summary:

    .. code-block:: python

        {"checked": N, "breached": N, "alerted": N, "skipped_cooldown": N}

    Per-tab try/except inside the loop — a save_alerts failure for one
    tab must not prevent the rest from firing. Cooldown is stamped ONLY
    after a successful save (a save that raises does NOT mark the
    cooldown — the next pass retries the alert).

    NEVER raises at the top level.
    """
    counts = {"checked": 0, "breached": 0, "alerted": 0, "skipped_cooldown": 0}
    uid = _resolve_user_id(user_id)

    try:
        budgets = load_budgets(user_id=uid)
        counts["checked"] = len(budgets)
    except Exception as exc:
        logger.debug(
            f"perf_budgets.check_and_alert: load_budgets failed: {exc}"
        )
        return counts

    try:
        breaches = check_budgets(user_id=uid)
    except Exception as exc:
        logger.debug(
            f"perf_budgets.check_and_alert: check_budgets failed: {exc}"
        )
        return counts

    counts["breached"] = len(breaches)

    if not breaches:
        return counts

    for breach in breaches:
        try:
            # Cooldown gate — same evaluation window means the operator
            # already knows about this tab. Suppress re-fire.
            if _within_cooldown(
                uid, breach.tab_module, int(breach.window_hours)
            ):
                counts["skipped_cooldown"] += 1
                continue

            alert = _build_alert(breach)

            # Lazy import save_alerts so a broken alert_engine_v2
            # doesn't kill the WHOLE loop — the per-breach try/except
            # catches and counts it.
            from engine.alert_engine_v2 import save_alerts

            save_alerts([alert], user_id=uid)

            # Only stamp cooldown AFTER a successful save. If save
            # raised, the next pass will retry.
            _set_cooldown(uid, breach.tab_module)
            counts["alerted"] += 1
        except Exception as exc:
            logger.debug(
                f"perf_budgets.check_and_alert: per-breach tab="
                f"{getattr(breach, 'tab_module', '?')!r} failed: {exc}"
            )
            continue

    return counts
