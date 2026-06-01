"""anomaly_detect.py — time-series anomaly detection for tracked metrics.

Static alert rules (``BDI > 5% spike``) catch sharp moves but miss the
subtler drift that drains optionality before any threshold trips: a
2%/day creep over ten sessions, freight gradually diverging from its
30d baseline, bunker quietly walking up by one standard deviation a
week. This module is the read-only consumer that classifies those
patterns against per-metric configs and fires an ``ANOMALY``
ShippingAlert when one trips.

Design
------
Companion to ``engine.alert_engine_v2`` (which handles threshold-based
fires + persistence) and ``engine.perf_budgets`` (which carries the
same shape for a different metric family). This module never mutates
the source feeds — it just lazy-loads them, runs three statistical
checks, and routes the hits through the existing alert pipeline.

1. :class:`AnomalyConfig` — one row in the metric-config table:
   metric_id, lookback window, z-threshold, detection method, min
   sample count, enabled flag.

2. :class:`AnomalyResult` — emitted by :func:`detect_anomaly` for every
   evaluated series. Carries the observed value, the baseline window
   stats, the z-score, the drift %, a severity classification, and a
   one-line message ready for the alert body.

3. :func:`detect_anomaly` — pure function. Input is a date-indexed
   numeric ``pd.Series`` + a config. Three methods:

     * ``'zscore'`` — mean / std on the lookback window; the test
       statistic is ``|x - mu| / sigma`` on the most recent value.
       The classic "is the latest tick a tail event" check.

     * ``'pct_drift'`` — mean on the lookback window; the test statistic
       is the % deviation of the most recent value vs the mean
       (``|x - mu| / |mu| * 100``). ``z_threshold`` is re-interpreted
       as a percentage. Useful for series with non-stationary variance
       where a z-score is too volatile (oil prices during a regime
       shift).

     * ``'rolling_mean_deviation'`` — compares a short rolling mean (the
       last 7 observations) against the lookback mean. Catches gradual
       drift that a single-observation z-score misses — the drift IS
       the smoothed series moving steadily away from baseline.

   Severity bands (closed at the lower end):
     * ``|z| in [z_threshold, 2*z_threshold)``     → MEDIUM
     * ``|z| in [2*z_threshold, 3*z_threshold)``   → HIGH
     * ``|z| >= 3*z_threshold``                    → CRITICAL

4. :func:`get_metric_series` — lazy import of the source modules. A
   miss (unknown metric, fetch failed, empty data) returns ``None``.
   NEVER raises.

5. :func:`detect_all_anomalies` — runs the whole config list and returns
   the subset of results with ``detected=True``. Per-metric try/except.

6. :func:`check_and_alert_anomalies` — end-to-end orchestrator. For
   each detected anomaly: cooldown gate → build ShippingAlert →
   ``save_alerts`` → stamp cooldown. Returns a count dict shaped like
   the perf-budget + source-health alerters so the worker can log a
   one-line summary.

7. :func:`get_anomaly_configs` / :func:`save_anomaly_configs` — per-user
   kv_state persistence. Missing row → defaults (the eight built-in
   metrics). Same shape, same posture, as ``engine.perf_budgets``.

Storage
-------
* Configs: kv_state row ``anomaly_configs:<user_id>`` carrying a JSON
  list of config records. Empty / missing row → defaults.
* Cooldown: kv_state row ``anomaly_cooldown:<user_id>:<metric_id>``
  carrying the ISO timestamp of the last successful fire. Default
  cooldown is 24 hours — anomaly drift moves on a multi-day cadence,
  so re-firing the same metric within a day adds no information.

Min-sample threshold
--------------------
``AnomalyConfig.min_samples`` (default 14) guards against cold-start
noise: a series with fewer than ``min_samples`` observations cannot
produce a meaningful baseline and ``detected`` returns False. Tuned per
metric — daily indices (BDI, WTI) need 14d to stabilise; weekly indices
(SCFI) only need a few obs but the default value is safe for both.

What this module is NOT
-----------------------
* It does NOT mutate the source feeds (data.fred_feed,
  data.freight_scraper, etc) — read-only consumer.
* It does NOT bypass the alert dedup machinery — every alert flows
  through ``save_alerts`` with the standard dedup shape.
* It does NOT bump SCHEMA_VERSION. Configs + cooldowns ride the
  existing ``kv_state`` table.
* It does NOT fire when the per-metric cooldown is active.
* It does NOT fire for metrics with fewer than ``min_samples``
  observations (cold-start noise).
* It does NOT log raw metric values that may be commercially sensitive
  (alert bodies carry summary statistics, not the proprietary point
  values themselves; the z-score + drift % are the load-bearing
  numbers operators need to triage).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from loguru import logger


# ─── Storage keys ─────────────────────────────────────────────────────────

_CONFIG_KEY_PREFIX = "anomaly_configs:"
_COOLDOWN_KEY_PREFIX = "anomaly_cooldown:"

# Default cooldown window in hours. Anomaly drift moves on a multi-day
# cadence — re-firing the same metric within 24h adds no information
# and risks carpeting the alert table. Tests monkeypatch this via
# ``monkeypatch.setattr(anomaly_detect, "_COOLDOWN_HOURS", N)`` to
# exercise the boundary.
_COOLDOWN_HOURS: int = 24


# ─── Method constants ─────────────────────────────────────────────────────

METHOD_ZSCORE = "zscore"
METHOD_PCT_DRIFT = "pct_drift"
METHOD_ROLLING_MEAN_DEVIATION = "rolling_mean_deviation"

_VALID_METHODS = (METHOD_ZSCORE, METHOD_PCT_DRIFT, METHOD_ROLLING_MEAN_DEVIATION)

# Rolling-mean window size used by ``method='rolling_mean_deviation'``.
# Seven days is the right ballpark: long enough to smooth out single-day
# noise, short enough that a sustained drift moves the rolling mean
# inside the lookback window.
_ROLLING_WINDOW = 7


# ─── Dataclasses ──────────────────────────────────────────────────────────

@dataclass
class AnomalyConfig:
    """Per-metric anomaly-detection configuration.

    Attributes
    ----------
    metric_id:
        The metric this config applies to. Keys into the built-in
        :data:`_METRIC_LOADERS` registry (e.g. ``'bdi'``, ``'wti'``,
        ``'fbx_transpacific_eb'``). NOT free-form — a typo here means
        the loader misses the metric and the result is treated as
        "no data".
    lookback_days:
        Size of the baseline window (in observations, not calendar
        days — daily indices and weekly indices both interpret this as
        "the last N rows"). Default 30, the typical month-of-data
        baseline that smooths weekly seasonality without losing
        responsiveness to regime shifts.
    z_threshold:
        For ``method='zscore'`` and ``'rolling_mean_deviation'``: the
        absolute z-score that trips the detector. Default 2.5 — a
        2.5-sigma move is roughly the 1-in-80 tail of a normal
        distribution. For ``method='pct_drift'``: re-interpreted as a
        percentage — a 2.5% deviation from baseline trips the detector.
    method:
        Which statistical check to run. See module docstring for the
        three methods. Default ``'zscore'`` — the broadest-purpose check.
    min_samples:
        Skip detection when the series has fewer observations than
        this. Default 14 — guards against cold-start noise on a metric
        we just started tracking.
    enabled:
        When False, ``detect_anomaly`` short-circuits to
        ``detected=False`` without computing anything. Lets operators
        silence a noisy metric without deleting the config row.
    """
    metric_id: str
    lookback_days: int = 30
    z_threshold: float = 2.5
    method: str = METHOD_ZSCORE
    min_samples: int = 14
    enabled: bool = True


@dataclass
class AnomalyResult:
    """One metric's evaluated anomaly status, ready to render or alert.

    Attributes
    ----------
    metric_id:
        Mirrors :attr:`AnomalyConfig.metric_id`.
    detected:
        ``True`` iff the test statistic crossed ``z_threshold`` AND
        the series had >= ``min_samples`` observations AND the config
        was enabled. ``False`` for any "no data / not enough data /
        disabled / within tolerance" reason — callers can rely on this
        single boolean.
    severity:
        ``'CRITICAL'`` | ``'HIGH'`` | ``'MEDIUM'`` when ``detected=True``,
        empty string otherwise. See module docstring for the
        z-threshold-multiple → severity bands.
    observed_value:
        Most recent value in the series.
    baseline_mean:
        Mean over the lookback window. ``0.0`` when the series was
        too short.
    baseline_std:
        Population (``ddof=0``) standard deviation over the lookback
        window. ``0.0`` when the series was too short OR constant —
        zero-std baselines cannot produce a meaningful z-score, so
        :func:`detect_anomaly` short-circuits in that case.
    z_score:
        For ``zscore`` / ``rolling_mean_deviation``: the signed z-score
        of the most recent value. For ``pct_drift``: the % drift from
        baseline mean (re-using the field so consumers don't have to
        branch on method).
    drift_pct:
        Signed % drift of the observed value relative to baseline mean.
        Always populated regardless of method — operators read both
        the z-score AND the % drift on the panel.
    message:
        One-line human-readable summary. Used as the alert body.
    checked_at:
        ISO-8601 UTC timestamp when this result was produced.
    """
    metric_id: str
    detected: bool
    severity: str
    observed_value: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    drift_pct: float
    message: str
    checked_at: str


# ─── User-id resolution ────────────────────────────────────────────────────

def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the user_id to scope configs + cooldown to.

    Mirrors the resolution rule used by ``engine.perf_budgets`` and
    ``engine.source_health_alerts``: explicit non-None ``user_id`` wins
    (so tests can pin a known id), otherwise consult the Streamlit
    session via ``current_user_id`` (returns ``""`` outside Streamlit —
    the legacy global bucket). Never raises.
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


def _config_key(user_id: str) -> str:
    """kv_state row key for the configs list blob."""
    return f"{_CONFIG_KEY_PREFIX}{user_id}"


def _cooldown_key(user_id: str, metric_id: str) -> str:
    """kv_state row key for a (user, metric) cooldown timestamp."""
    return f"{_COOLDOWN_KEY_PREFIX}{user_id}:{metric_id}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


# ─── Built-in metric defaults ─────────────────────────────────────────────

# The list shipped with the module. Tuned by metric cadence and
# volatility:
#   - daily indices (BDI, WTI, bunker) get a 30d window + 2.5σ
#   - weekly indices (SCFI) get a 12-week window + 2.0σ — the lower
#     threshold compensates for the fact that one weekly print carries
#     more signal than one daily print
#   - port-wait + freight rates use pct_drift instead of z-score —
#     non-stationary variance makes the z-score too noisy
_DEFAULT_CONFIG_SPECS: list[tuple[str, dict[str, Any]]] = [
    ("bdi", {}),
    ("wti", {}),
    ("bunker", {}),
    ("scfi", {"lookback_days": 12, "z_threshold": 2.0}),
    ("fbx_transpacific_eb", {"method": METHOD_PCT_DRIFT, "z_threshold": 10.0}),
    ("fbx_global", {"method": METHOD_PCT_DRIFT, "z_threshold": 8.0}),
    ("port_wait_la", {"method": METHOD_PCT_DRIFT, "z_threshold": 25.0}),
    ("transpacific_delay_days", {"method": METHOD_ROLLING_MEAN_DEVIATION}),
]


def get_default_configs() -> list[AnomalyConfig]:
    """Return the shipped default config list.

    Used by :func:`get_anomaly_configs` when no customised row exists,
    and by the UI/CLI reset paths. Always returns a fresh list so
    callers can mutate the result without poisoning the next call.
    """
    out: list[AnomalyConfig] = []
    for metric_id, overrides in _DEFAULT_CONFIG_SPECS:
        out.append(AnomalyConfig(metric_id=metric_id, **overrides))
    return out


# ─── Config load / save ───────────────────────────────────────────────────

def _coerce_int(value: Any, default: int) -> int:
    """Best-effort int coercion. Bad input → default."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    """Best-effort float coercion. Bad input → default."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _record_to_config(record: Any) -> Optional[AnomalyConfig]:
    """Map one JSON-decoded record to an :class:`AnomalyConfig`.

    Malformed (wrong type, missing required field, invalid method)
    → None so the caller can skip without raising on one bad row.
    """
    if not isinstance(record, dict):
        return None
    metric_id = record.get("metric_id")
    if not isinstance(metric_id, str) or not metric_id:
        return None
    method = record.get("method", METHOD_ZSCORE)
    if method not in _VALID_METHODS:
        method = METHOD_ZSCORE
    lookback_days = _coerce_int(record.get("lookback_days"), 30)
    if lookback_days <= 0:
        lookback_days = 30
    z_threshold = _coerce_float(record.get("z_threshold"), 2.5)
    if z_threshold <= 0:
        z_threshold = 2.5
    min_samples = _coerce_int(record.get("min_samples"), 14)
    if min_samples <= 0:
        min_samples = 14
    enabled = bool(record.get("enabled", True))
    return AnomalyConfig(
        metric_id=metric_id,
        lookback_days=lookback_days,
        z_threshold=z_threshold,
        method=method,
        min_samples=min_samples,
        enabled=enabled,
    )


def get_anomaly_configs(*, user_id: Optional[str] = None) -> list[AnomalyConfig]:
    """Return the user's saved configs, or defaults when none are saved.

    Missing row → defaults. Empty list saved by the user → defaults
    (a saved empty list is treated as "I haven't customised; give me
    defaults" — the explicit reset path uses ``save_anomaly_configs([])``
    for that purpose). Malformed JSON / DB read failure → defaults.
    NEVER raises.
    """
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_config_key(uid),)
        ).fetchone()
        if row is None:
            return get_default_configs()
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return get_default_configs()
        parsed = json.loads(raw)
    except Exception as exc:
        logger.debug(
            f"anomaly_detect.get_anomaly_configs: read failed for "
            f"user_id={uid!r}: {exc}"
        )
        return get_default_configs()

    if not isinstance(parsed, list) or not parsed:
        return get_default_configs()

    configs: list[AnomalyConfig] = []
    for record in parsed:
        c = _record_to_config(record)
        if c is not None:
            configs.append(c)
    return configs if configs else get_default_configs()


def save_anomaly_configs(
    configs: list[AnomalyConfig], *, user_id: Optional[str] = None
) -> bool:
    """Persist the config list to kv_state. Returns success.

    Passing an empty list deliberately writes an empty JSON array —
    the next call to :func:`get_anomaly_configs` treats that as "reset
    to defaults" (the row exists but is empty). NEVER raises — write
    errors are logged at debug and the return value (False) tells the
    caller (typically the UI Save button) to surface a "couldn't save"
    message.
    """
    uid = _resolve_user_id(user_id)
    try:
        # Filter out non-AnomalyConfig inputs defensively so a caller
        # that passes a mixed list does not corrupt the blob.
        records = [
            asdict(c) for c in configs if isinstance(c, AnomalyConfig)
        ]
        payload = json.dumps(records)
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_config_key(uid), payload, _now_iso()),
            )
        return True
    except Exception as exc:
        logger.debug(
            f"anomaly_detect.save_anomaly_configs: write failed for "
            f"user_id={uid!r}: {exc}"
        )
        return False


# ─── Cooldown helpers ─────────────────────────────────────────────────────

def _get_cooldown(user_id: str, metric_id: str) -> Optional[str]:
    """Return the ISO timestamp of the last fire for (user, metric) or None."""
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_cooldown_key(user_id, metric_id),),
        ).fetchone()
        if row is None:
            return None
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return None
        return str(raw)
    except Exception as exc:
        logger.debug(
            f"anomaly_detect._get_cooldown: read failed for "
            f"user_id={user_id!r} metric={metric_id!r}: {exc}"
        )
        return None


def _set_cooldown(user_id: str, metric_id: str) -> None:
    """Stamp now-ISO into the cooldown row for (user, metric). Best-effort."""
    try:
        from state.db import get_connection

        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_cooldown_key(user_id, metric_id), now, now),
            )
    except Exception as exc:
        logger.debug(
            f"anomaly_detect._set_cooldown: write failed for "
            f"user_id={user_id!r} metric={metric_id!r}: {exc}"
        )


def _within_cooldown(
    user_id: str,
    metric_id: str,
    *,
    window_hours: int = _COOLDOWN_HOURS,
    now: Optional[datetime] = None,
) -> bool:
    """True iff a prior fire for (user, metric) was within ``window_hours``."""
    if window_hours <= 0:
        return False
    last = _get_cooldown(user_id, metric_id)
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


# ─── Pure detection ───────────────────────────────────────────────────────

def _empty_result(
    metric_id: str, *, message: str = ""
) -> AnomalyResult:
    """Build a not-detected result. Used by every early-return path."""
    return AnomalyResult(
        metric_id=metric_id,
        detected=False,
        severity="",
        observed_value=0.0,
        baseline_mean=0.0,
        baseline_std=0.0,
        z_score=0.0,
        drift_pct=0.0,
        message=message,
        checked_at=_now_iso(),
    )


def _classify_severity(abs_test: float, z_threshold: float) -> str:
    """Map an absolute test statistic to a severity label.

    Boundary rules (closed at the lower end):
      * abs_test <  z_threshold              → '' (caller should not call us)
      * abs_test in [z_threshold, 2x)        → 'MEDIUM'
      * abs_test in [2x, 3x)                 → 'HIGH'
      * abs_test >= 3x                       → 'CRITICAL'
    """
    if z_threshold <= 0:
        return "MEDIUM"
    ratio = abs_test / z_threshold
    if ratio >= 3.0:
        return "CRITICAL"
    if ratio >= 2.0:
        return "HIGH"
    if ratio >= 1.0:
        return "MEDIUM"
    return ""


def detect_anomaly(series, config: AnomalyConfig) -> AnomalyResult:
    """Pure function. Check ``series`` against ``config`` and return a result.

    Steps:
      1. Disabled config → not detected.
      2. Drop NaN values + verify >= ``min_samples`` observations remain.
      3. Take the last ``lookback_days`` observations as the baseline.
      4. Run the configured statistical method.
      5. Classify severity if the test statistic crossed threshold.

    NEVER raises. Any unexpected error falls through to "not detected"
    so the orchestrator loop continues.

    Returns an :class:`AnomalyResult` regardless of whether the detection
    fired — callers branch on the ``.detected`` boolean.
    """
    metric_id = getattr(config, "metric_id", "")
    try:
        if not getattr(config, "enabled", True):
            return _empty_result(metric_id, message="config disabled")

        # Lazy pandas import — keeps the module importable without pandas
        # for the rare caller that only needs the dataclasses.
        import pandas as pd  # noqa: F401

        if series is None:
            return _empty_result(metric_id, message="no series")

        clean = series.dropna() if hasattr(series, "dropna") else None
        if clean is None or len(clean) == 0:
            return _empty_result(metric_id, message="empty series")

        min_samples = max(1, int(getattr(config, "min_samples", 14) or 14))
        if len(clean) < min_samples:
            return _empty_result(
                metric_id,
                message=f"insufficient data ({len(clean)} < {min_samples})",
            )

        lookback = max(1, int(getattr(config, "lookback_days", 30) or 30))
        z_threshold = float(getattr(config, "z_threshold", 2.5) or 2.5)
        method = str(getattr(config, "method", METHOD_ZSCORE) or METHOD_ZSCORE)
        if method not in _VALID_METHODS:
            method = METHOD_ZSCORE

        # Use the last `lookback` observations as the baseline. When the
        # series is shorter than `lookback` we silently fall back to
        # "everything we have" — the min_samples gate above already
        # ensured we have at least min_samples points.
        baseline = clean.tail(lookback).astype(float)
        observed_value = float(clean.iloc[-1])

        import numpy as np

        baseline_arr = baseline.to_numpy(dtype=float)
        baseline_mean = float(np.mean(baseline_arr))
        baseline_std = float(np.std(baseline_arr, ddof=0))

        # Always-populated drift %. Zero baseline mean → drift is
        # undefined; report 0 so the field has a stable type.
        if baseline_mean != 0:
            drift_pct = (observed_value - baseline_mean) / abs(baseline_mean) * 100.0
        else:
            drift_pct = 0.0

        # Method dispatch.
        if method == METHOD_ZSCORE:
            if baseline_std <= 0:
                return AnomalyResult(
                    metric_id=metric_id,
                    detected=False,
                    severity="",
                    observed_value=observed_value,
                    baseline_mean=baseline_mean,
                    baseline_std=baseline_std,
                    z_score=0.0,
                    drift_pct=round(drift_pct, 4),
                    message="zero-variance baseline",
                    checked_at=_now_iso(),
                )
            z_score = (observed_value - baseline_mean) / baseline_std
            test_statistic = abs(z_score)
            detected = test_statistic >= z_threshold
            severity = _classify_severity(test_statistic, z_threshold) if detected else ""
            if detected:
                message = (
                    f"{metric_id} z-score {z_score:+.2f} (threshold "
                    f"{z_threshold:.2f}, drift {drift_pct:+.2f}% from "
                    f"{lookback}d mean over {len(baseline)} obs)"
                )
            else:
                message = (
                    f"within tolerance (z={z_score:+.2f}, threshold "
                    f"{z_threshold:.2f})"
                )
            return AnomalyResult(
                metric_id=metric_id,
                detected=detected,
                severity=severity,
                observed_value=observed_value,
                baseline_mean=baseline_mean,
                baseline_std=baseline_std,
                z_score=round(z_score, 4),
                drift_pct=round(drift_pct, 4),
                message=message,
                checked_at=_now_iso(),
            )

        if method == METHOD_PCT_DRIFT:
            # z_threshold is re-interpreted as a percentage.
            test_statistic = abs(drift_pct)
            detected = test_statistic >= z_threshold
            severity = _classify_severity(test_statistic, z_threshold) if detected else ""
            if detected:
                message = (
                    f"{metric_id} drift {drift_pct:+.2f}% from "
                    f"{lookback}d mean (threshold {z_threshold:.2f}%, "
                    f"{len(baseline)} obs)"
                )
            else:
                message = (
                    f"within tolerance (drift {drift_pct:+.2f}%, threshold "
                    f"{z_threshold:.2f}%)"
                )
            return AnomalyResult(
                metric_id=metric_id,
                detected=detected,
                severity=severity,
                observed_value=observed_value,
                baseline_mean=baseline_mean,
                baseline_std=baseline_std,
                z_score=round(drift_pct, 4),
                drift_pct=round(drift_pct, 4),
                message=message,
                checked_at=_now_iso(),
            )

        # METHOD_ROLLING_MEAN_DEVIATION — compare the rolling 7-obs mean
        # against the lookback mean. Tail-end rolling means walk steadily
        # away from baseline when a gradual drift is in progress. The
        # baseline is recomputed here to EXCLUDE the rolling tail so a
        # drift that fills the lookback window does not inflate baseline
        # mean + std and silence its own detection.
        window = min(_ROLLING_WINDOW, len(clean))
        rolling = clean.tail(window).astype(float).to_numpy(dtype=float)
        rolling_mean = float(np.mean(rolling))

        # Recompute baseline as the lookback window IMMEDIATELY PRECEDING
        # the rolling tail. When the series is too short to cleanly
        # separate the two we fall back to the original baseline.
        if len(clean) > window:
            pre_tail = clean.iloc[: -window]
            baseline_for_rmd = pre_tail.tail(lookback).astype(float).to_numpy(dtype=float)
            if len(baseline_for_rmd) > 0:
                baseline_mean = float(np.mean(baseline_for_rmd))
                baseline_std = float(np.std(baseline_for_rmd, ddof=0))
                if baseline_mean != 0:
                    drift_pct = (
                        (rolling_mean - baseline_mean) / abs(baseline_mean) * 100.0
                    )

        if baseline_std <= 0:
            return AnomalyResult(
                metric_id=metric_id,
                detected=False,
                severity="",
                observed_value=observed_value,
                baseline_mean=baseline_mean,
                baseline_std=baseline_std,
                z_score=0.0,
                drift_pct=round(drift_pct, 4),
                message="zero-variance baseline",
                checked_at=_now_iso(),
            )
        z_score = (rolling_mean - baseline_mean) / baseline_std
        test_statistic = abs(z_score)
        detected = test_statistic >= z_threshold
        severity = _classify_severity(test_statistic, z_threshold) if detected else ""
        if detected:
            message = (
                f"{metric_id} {window}-obs rolling mean drifted "
                f"{z_score:+.2f}σ from {lookback}d baseline (threshold "
                f"{z_threshold:.2f}, drift {drift_pct:+.2f}%)"
            )
        else:
            message = (
                f"within tolerance (rolling-mean z={z_score:+.2f}, threshold "
                f"{z_threshold:.2f})"
            )
        return AnomalyResult(
            metric_id=metric_id,
            detected=detected,
            severity=severity,
            observed_value=observed_value,
            baseline_mean=baseline_mean,
            baseline_std=baseline_std,
            z_score=round(z_score, 4),
            drift_pct=round(drift_pct, 4),
            message=message,
            checked_at=_now_iso(),
        )
    except Exception as exc:
        logger.debug(
            f"anomaly_detect.detect_anomaly: metric={metric_id!r} failed: {exc}"
        )
        return _empty_result(metric_id, message="error during detection")


# ─── Metric source loaders ────────────────────────────────────────────────
#
# Each loader returns a pandas Series indexed by date (DatetimeIndex)
# with numeric values, or None when the source is unavailable.
# Loaders NEVER raise — every exception is swallowed and None is returned.
# Sources are lazy-imported inside each loader so the anomaly module
# stays importable in a test environment that does not have, e.g., the
# fredapi backend installed.

def _safe_macro_series(series_id: str):
    """Load a single FRED series as a date-indexed pandas Series. None on miss."""
    try:
        from data.fred_feed import fetch_macro_series

        macro_data = fetch_macro_series()
        if not isinstance(macro_data, dict):
            return None
        df = macro_data.get(series_id)
        if df is None or len(df) == 0:
            return None
        if "date" not in df.columns or "value" not in df.columns:
            return None
        import pandas as pd

        out = pd.Series(
            df["value"].astype(float).values,
            index=pd.to_datetime(df["date"]),
            name=series_id,
        )
        return out.dropna()
    except Exception as exc:
        logger.debug(
            f"anomaly_detect._safe_macro_series: {series_id} failed: {exc}"
        )
        return None


def _load_bdi():
    """Baltic Dry Index. Tries the Baltic Exchange scraper first (daily,
    higher fidelity), falls back to the FRED weekly series."""
    try:
        from data.freight_scraper import fetch_baltic_daily

        ds = fetch_baltic_daily(family="BDI")
        if ds is not None and getattr(ds, "data", None) is not None:
            series = ds.data
            if len(series) > 0:
                return series.dropna().astype(float)
    except Exception as exc:
        logger.debug(f"anomaly_detect._load_bdi: baltic scraper failed: {exc}")
    return _safe_macro_series("BSXRLM")


def _load_wti():
    """WTI Crude Oil Price (FRED DCOILWTICO)."""
    return _safe_macro_series("DCOILWTICO")


def _load_bunker():
    """Bunker / diesel proxy — PPI Diesel Fuel (FRED WPU0561)."""
    return _safe_macro_series("WPU0561")


def _load_scfi():
    """SCFI proxy — PPI Deep Sea Freight (FRED PCU4841484148). The
    proprietary SCFI prints are not directly on FRED; the PPI Deep Sea
    Freight series tracks the same global container-rate level."""
    return _safe_macro_series("PCU4841484148")


def _load_fbx(route_id: str):
    """One FBX route as a date-indexed Series of rate-per-FEU."""
    try:
        from data.freight_scraper import fetch_fbx_rates

        rates = fetch_fbx_rates()
        if not isinstance(rates, dict):
            return None
        df = rates.get(route_id)
        if df is None or len(df) == 0:
            return None
        # Normalised FBX DataFrames carry a `rate_usd_per_feu` column;
        # fall back to whichever numeric column the normaliser produced.
        col = None
        for candidate in ("rate_usd_per_feu", "rate", "value"):
            if candidate in df.columns:
                col = candidate
                break
        if col is None:
            # last resort: first non-date numeric column
            for c in df.columns:
                if c == "date":
                    continue
                try:
                    df[c].astype(float)
                    col = c
                    break
                except (TypeError, ValueError):
                    continue
        if col is None or "date" not in df.columns:
            return None
        import pandas as pd

        out = pd.Series(
            df[col].astype(float).values,
            index=pd.to_datetime(df["date"]),
            name=route_id,
        )
        return out.dropna()
    except Exception as exc:
        logger.debug(
            f"anomaly_detect._load_fbx: route={route_id!r} failed: {exc}"
        )
        return None


def _load_fbx_transpacific_eb():
    return _load_fbx("transpacific_eb")


def _load_fbx_global():
    return _load_fbx("global")


def _load_port_wait_la():
    """Port-wait days for LA / Long Beach. Lazy-load ports.port_data and
    pull the LALB row's wait_days column. None when the source is empty
    or the wait_days history is unavailable."""
    try:
        # The port-wait history lives in the port_loader output. We try
        # the wait-history helper first and fall back to a flat "current
        # wait days" Series when the history is unavailable.
        from ports.port_loader import load_ports

        ports = load_ports()
        if not ports:
            return None
        target = None
        for p in ports:
            locode = getattr(p, "locode", "") or getattr(p, "port_locode", "")
            if locode in ("USLAX", "USLGB"):
                target = p
                break
        if target is None:
            return None
        history = getattr(target, "wait_history", None)
        if history is None:
            return None
        import pandas as pd

        if hasattr(history, "to_pydatetime") or hasattr(history, "index"):
            # Already a Series — coerce numeric.
            return history.astype(float).dropna()
        if isinstance(history, dict):
            return pd.Series(
                list(history.values()),
                index=pd.to_datetime(list(history.keys())),
                name="port_wait_la",
            ).astype(float).dropna()
        return None
    except Exception as exc:
        logger.debug(f"anomaly_detect._load_port_wait_la: failed: {exc}")
        return None


def _load_transpacific_delay_days():
    """Transpacific delay-days proxy. Built from the same FBX series so
    the loader degrades to None gracefully when the proprietary delay
    feed is offline (which is the default in test environments)."""
    try:
        # Prefer the dedicated processor when available.
        from processing.congestion_predictor import get_recent_delay_series  # type: ignore[attr-defined]

        s = get_recent_delay_series()
        if s is not None and len(s) > 0:
            return s.astype(float).dropna()
    except Exception:
        pass
    # No proprietary feed in test envs — return None so the orchestrator
    # treats this as "no data".
    return None


# Built-in registry. ``_METRIC_LOADERS`` maps metric_id → zero-arg loader.
# Tests can override via ``register_metric_loader`` for hermetic detection
# without touching the source backends.
_METRIC_LOADERS: dict[str, Callable[[], Any]] = {
    "bdi": _load_bdi,
    "wti": _load_wti,
    "bunker": _load_bunker,
    "scfi": _load_scfi,
    "fbx_transpacific_eb": _load_fbx_transpacific_eb,
    "fbx_global": _load_fbx_global,
    "port_wait_la": _load_port_wait_la,
    "transpacific_delay_days": _load_transpacific_delay_days,
}


def register_metric_loader(metric_id: str, loader: Callable[[], Any]) -> None:
    """Register / override a loader. Used by tests + extensions.

    The override replaces the built-in entry for the duration of the
    process. Tests that want isolation should save the prior loader and
    restore it on teardown.
    """
    _METRIC_LOADERS[str(metric_id)] = loader


def get_metric_series(metric_id: str):
    """Lazy-load the named metric's time series.

    Returns a date-indexed numeric ``pandas.Series`` on success, or
    ``None`` on any miss (unknown metric, loader returned None, loader
    raised). NEVER raises.
    """
    try:
        loader = _METRIC_LOADERS.get(str(metric_id))
        if loader is None:
            return None
        result = loader()
        if result is None:
            return None
        # Defensive coerce: only return something that quacks like a Series.
        if not hasattr(result, "dropna") or not hasattr(result, "iloc"):
            return None
        cleaned = result.dropna()
        if len(cleaned) == 0:
            return None
        return cleaned
    except Exception as exc:
        logger.debug(
            f"anomaly_detect.get_metric_series: metric={metric_id!r} failed: {exc}"
        )
        return None


# ─── Orchestration ────────────────────────────────────────────────────────

def detect_all_anomalies(
    *,
    user_id: Optional[str] = None,
    configs: Optional[list[AnomalyConfig]] = None,
) -> list[AnomalyResult]:
    """Run :func:`detect_anomaly` over every config and return the hits.

    ``configs`` defaults to the user's saved configs (or built-in
    defaults). Per-metric try/except — one broken loader or one
    detection failure must NOT block the rest of the loop. Returns
    only results with ``detected=True``.

    NEVER raises at the top level.
    """
    try:
        if configs is None:
            configs = get_anomaly_configs(user_id=user_id)
        results: list[AnomalyResult] = []
        for cfg in configs:
            try:
                if not isinstance(cfg, AnomalyConfig):
                    continue
                if not cfg.enabled:
                    continue
                series = get_metric_series(cfg.metric_id)
                if series is None:
                    continue
                result = detect_anomaly(series, cfg)
                if result.detected:
                    results.append(result)
            except Exception as exc:
                logger.debug(
                    f"anomaly_detect.detect_all_anomalies: metric="
                    f"{getattr(cfg, 'metric_id', '?')!r} failed: {exc}"
                )
                continue
        return results
    except Exception as exc:
        logger.debug(f"anomaly_detect.detect_all_anomalies: top-level failed: {exc}")
        return []


def _build_alert(result: AnomalyResult):
    """Build a ShippingAlert from an :class:`AnomalyResult`.

    The alert body carries the summary statistics — z-score, drift %,
    baseline mean — but NOT the raw observed value verbatim, to avoid
    leaking commercially-sensitive levels for proprietary metrics.
    metric_id rides in ``port_locode`` as the entity key so the existing
    dedup machinery collapses two near-simultaneous fires for the same
    metric (same pattern ``engine.perf_budgets`` uses for tab_module).
    """
    from engine.alert_engine_v2 import ShippingAlert, _new_id, _now_iso as alerts_now_iso

    title = f"Anomaly on {result.metric_id}"
    body = (
        f"{result.message}. baseline mean {result.baseline_mean:.4g}, "
        f"std {result.baseline_std:.4g}."
    )
    return ShippingAlert(
        alert_id=_new_id(),
        created_at=alerts_now_iso(),
        alert_type="ANOMALY",
        severity=result.severity or "MEDIUM",
        title=title,
        body=body,
        ticker="",
        route_id="",
        # metric_id piggybacks on port_locode as the dedup entity key —
        # truncated to the column width like the source-health alerter
        # and the perf-budget alerter do.
        port_locode=str(result.metric_id or "")[:32],
        value=float(result.z_score),
        threshold=0.0,
        change_pct=float(result.drift_pct),
        acknowledged=False,
    )


def check_and_alert_anomalies(*, user_id: Optional[str] = None) -> dict:
    """End-to-end: detect every metric, fire alerts for hits.

    Returns a count dict shaped like the perf-budget + source-health
    alerters so the worker can log a one-line summary:

    .. code-block:: python

        {"checked": N, "detected": N, "alerted": N, "skipped_cooldown": N}

    Per-metric try/except inside the loop — a save_alerts failure for
    one metric must NOT prevent the rest from firing. Cooldown is
    stamped ONLY after a successful save (a save that raises does NOT
    mark the cooldown — the next pass retries the alert).

    NEVER raises at the top level.
    """
    counts = {"checked": 0, "detected": 0, "alerted": 0, "skipped_cooldown": 0}
    uid = _resolve_user_id(user_id)

    try:
        configs = get_anomaly_configs(user_id=uid)
        counts["checked"] = len(configs)
    except Exception as exc:
        logger.debug(
            f"anomaly_detect.check_and_alert_anomalies: get_anomaly_configs "
            f"failed: {exc}"
        )
        return counts

    try:
        results = detect_all_anomalies(user_id=uid, configs=configs)
    except Exception as exc:
        logger.debug(
            f"anomaly_detect.check_and_alert_anomalies: detect_all_anomalies "
            f"failed: {exc}"
        )
        return counts

    counts["detected"] = len(results)
    if not results:
        return counts

    for result in results:
        try:
            if _within_cooldown(uid, result.metric_id):
                counts["skipped_cooldown"] += 1
                continue

            alert = _build_alert(result)

            # Lazy import save_alerts so a broken alert_engine_v2 does
            # not kill the WHOLE loop — the per-metric try/except
            # catches and counts it.
            from engine.alert_engine_v2 import save_alerts

            save_alerts([alert], user_id=uid)

            # Only stamp cooldown AFTER a successful save. If save
            # raised, the next pass will retry.
            _set_cooldown(uid, result.metric_id)
            counts["alerted"] += 1
        except Exception as exc:
            logger.debug(
                f"anomaly_detect.check_and_alert_anomalies: per-metric "
                f"metric={getattr(result, 'metric_id', '?')!r} failed: {exc}"
            )
            continue

    return counts
