"""processing/forecast_accuracy_tracker.py — predicted-vs-actual tracker.

The platform's disruption forecast (``processing/disruption_forecast.py``)
emits 7- and 30-day SSI projections per route, but nothing logs *what we
predicted* alongside *what actually happened* so the operator can see how
fast accuracy decays over horizon (a 30-day forecast should be worse than
a 7-day one) or over time (the model may be drifting).

This module is the persistence + pairing layer:

  * ``log_forecast`` appends one ``ForecastRecord`` to a JSONL file at
    ``cache/forecast_log/<lane_id>.jsonl``. Append-only — no rewrites, no
    locks, safe to call from a scheduled job that may overlap with reads.
  * ``log_actual`` mirrors that for realized values, written to a
    sibling file ``cache/forecast_log/actuals/<lane_id>.jsonl``.
  * ``match_forecasts_to_actuals`` walks both stores and produces
    ``AccuracyRow`` entries — one per (forecast → actual) pair where the
    target date (forecast_date + horizon_days) matches an actual.
  * ``summarize_accuracy`` rolls those rows into operator-readable
    aggregates: overall MAE, MAE by horizon (7d/14d/30d), sign-agreement,
    n_pairs.

Storage is plain JSONL — one record per line, UTF-8, no compression.
That deliberately keeps the format trivially diffable and trivially
readable from any other tool (jq, sqlite import, pandas read_json) and
sidesteps the migration story a SQLite table would impose.

Scheduler-side helpers (``should_log_forecast_today`` and
``run_forecast_log_job``) are pure functions that the worker can wire in
later as a one-line follow-up. They are intentionally *not* wired into
``worker/scheduler.py`` here — other agents are mid-edit on that file.

The module has no third-party dependencies beyond the stdlib.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


__all__ = [
    "FORECAST_LOG_ROOT",
    "ForecastRecord",
    "ActualRecord",
    "AccuracyRow",
    "log_forecast",
    "log_actual",
    "read_forecasts",
    "read_actuals",
    "match_forecasts_to_actuals",
    "summarize_accuracy",
    "summarize_calibration",
    "crps_gaussian",
    "pit_value",
    "pit_histogram",
    "coverage_rate",
    "brier_score",
    "reliability_curve",
    "should_log_forecast_today",
    "run_forecast_log_job",
]


# Default root for the forecast log. Lives under the project's existing
# cache/ tree so the daily backup picks it up alongside everything else.
FORECAST_LOG_ROOT: Path = (
    Path(__file__).parent.parent / "cache" / "forecast_log"
)

# Horizon buckets the summary breaks MAE out by. Anything outside these
# horizons still contributes to the overall MAE — it just doesn't get its
# own dedicated line in the per-horizon breakdown.
_SUMMARY_HORIZONS: tuple[int, ...] = (7, 14, 30)


# ---------------------------------------------------------------------------
# Record dataclasses — plain dicts on the wire, dataclasses in memory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastRecord:
    """One forecast emitted at a point in time.

    ``forecast_date_iso`` is the date the forecast was *made*, NOT the
    target date. The target date is derived at pairing time via
    ``forecast_date + horizon_days``. ``lane_id`` is the route key (e.g.
    ``"transpacific_eb"``) or the literal string ``"fleet"`` for a
    fleet-wide aggregate forecast.
    """

    forecast_date_iso: str
    horizon_days: int
    predicted_value: float
    lane_id: str
    # ── Interval + skill baseline (rec R029) — default 0 so old logs load ────
    predicted_sigma: float = 0.0   # forecast dispersion (e.g. StressForecast.forecast_sigma)
    baseline_value: float = 0.0    # the level at forecast time (persistence baseline)


@dataclass(frozen=True)
class ActualRecord:
    """A realized observation on a given date for a given lane."""

    actual_date_iso: str
    actual_value: float
    lane_id: str


@dataclass(frozen=True)
class AccuracyRow:
    """One scored (forecast → actual) pair.

    ``error`` and ``signed_error`` are both ``predicted - actual`` — kept
    separate so the field name reads naturally in the two contexts the
    summary uses them in. ``abs_error`` is ``|predicted - actual|``.
    """

    forecast_date_iso: str
    target_date_iso: str
    horizon_days: int
    lane_id: str
    predicted: float
    actual: float
    error: float
    abs_error: float
    signed_error: float
    predicted_sigma: float = 0.0   # carried from the forecast (rec R029)
    baseline: float = 0.0          # the level at forecast time (skill baseline)


# ---------------------------------------------------------------------------
# Path helpers — pure, easy to test
# ---------------------------------------------------------------------------


def _forecast_root(root: Path | str | None) -> Path:
    """Resolve the forecast-log root, honouring a per-test override."""
    return Path(root) if root is not None else FORECAST_LOG_ROOT


def _forecast_file(lane_id: str, root: Path | str | None) -> Path:
    """Per-lane forecast JSONL — one file per lane keeps reads cheap."""
    return _forecast_root(root) / f"{lane_id}.jsonl"


def _actual_file(lane_id: str, root: Path | str | None) -> Path:
    """Per-lane actuals JSONL — sibling tree of the forecasts dir."""
    return _forecast_root(root) / "actuals" / f"{lane_id}.jsonl"


def _ensure_parent(path: Path) -> None:
    """Create the parent dir for ``path`` if it does not already exist."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _add_days(iso_date: str, days: int) -> str:
    """Add ``days`` to an ISO date string. Pure helper used at pair time."""
    d = date.fromisoformat(iso_date)
    return (d + timedelta(days=int(days))).isoformat()


# ---------------------------------------------------------------------------
# Writers — append-only JSONL
# ---------------------------------------------------------------------------


def log_forecast(
    record: ForecastRecord,
    *,
    root: Path | str | None = None,
) -> Path:
    """Append a ``ForecastRecord`` to the lane's forecast JSONL.

    Creates the parent directory if missing. Returns the path written
    to. Calling repeatedly with the same record will write duplicates —
    that is intentional; the pairing layer dedupes on its side via the
    (lane, forecast_date, horizon) triple so a re-run never doubles-up
    the accuracy summary.
    """
    path = _forecast_file(record.lane_id, root)
    _ensure_parent(path)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(asdict(record), sort_keys=True))
        fp.write("\n")
    return path


def log_actual(
    record: ActualRecord,
    *,
    root: Path | str | None = None,
) -> Path:
    """Append an ``ActualRecord`` to the lane's actuals JSONL.

    Same contract as ``log_forecast`` — append-only, creates parent dirs,
    returns the path. The pair layer dedupes actuals by
    (lane, actual_date) so multiple writes for the same day are harmless.
    """
    path = _actual_file(record.lane_id, root)
    _ensure_parent(path)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(asdict(record), sort_keys=True))
        fp.write("\n")
    return path


# ---------------------------------------------------------------------------
# Readers — used internally by the pairer + also exposed for callers that
# want to inspect the raw store (e.g. a debugging UI).
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterable[dict]:
    """Yield decoded JSON objects from a JSONL file, one per line.

    Skips blank lines and malformed lines (defensive — a partially-flushed
    write should not abort the read). The caller surfaces those skips via
    counts if it cares; the pair layer does not.
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def read_forecasts(
    lane_id: str,
    *,
    root: Path | str | None = None,
) -> list[ForecastRecord]:
    """Read every ``ForecastRecord`` ever logged for ``lane_id``.

    Returns ``[]`` if the lane's JSONL doesn't exist yet. The list is in
    on-disk order; callers that need a stable comparison sort it.
    """
    out: list[ForecastRecord] = []
    for blob in _iter_jsonl(_forecast_file(lane_id, root)):
        try:
            out.append(ForecastRecord(
                forecast_date_iso=str(blob["forecast_date_iso"]),
                horizon_days=int(blob["horizon_days"]),
                predicted_value=float(blob["predicted_value"]),
                lane_id=str(blob["lane_id"]),
                predicted_sigma=float(blob.get("predicted_sigma", 0.0) or 0.0),
                baseline_value=float(blob.get("baseline_value", 0.0) or 0.0),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def read_actuals(
    lane_id: str,
    *,
    root: Path | str | None = None,
) -> list[ActualRecord]:
    """Read every ``ActualRecord`` ever logged for ``lane_id``.

    Returns ``[]`` if the lane's actuals JSONL doesn't exist yet.
    """
    out: list[ActualRecord] = []
    for blob in _iter_jsonl(_actual_file(lane_id, root)):
        try:
            out.append(ActualRecord(
                actual_date_iso=str(blob["actual_date_iso"]),
                actual_value=float(blob["actual_value"]),
                lane_id=str(blob["lane_id"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _list_lane_ids(root: Path | str | None) -> list[str]:
    """All lane ids that have a forecast or actuals file on disk."""
    base = _forecast_root(root)
    lanes: set[str] = set()
    if base.exists():
        for child in base.glob("*.jsonl"):
            lanes.add(child.stem)
    actuals_dir = base / "actuals"
    if actuals_dir.exists():
        for child in actuals_dir.glob("*.jsonl"):
            lanes.add(child.stem)
    return sorted(lanes)


# ---------------------------------------------------------------------------
# Pairing — the core join
# ---------------------------------------------------------------------------


def match_forecasts_to_actuals(
    *,
    window_days: int = 60,
    root: Path | str | None = None,
    today: date | None = None,
) -> list[AccuracyRow]:
    """Pair every logged forecast with the actual on its target date.

    A forecast made on day N with ``horizon_days=H`` pairs with the
    actual recorded on day N+H. Forecasts whose target date is in the
    future, or whose corresponding actual was not (yet) logged, are left
    un-paired and not emitted — the next call after the actual arrives
    will pick them up.

    Parameters
    ----------
    window_days:
        Only consider forecasts whose target date is within
        ``window_days`` of ``today``. Defaults to 60 — enough to cover the
        longest standard horizon (30d) with plenty of slack. Set very
        large to score the entire history.
    root:
        Override the on-disk root. Tests pass ``tmp_path``.
    today:
        Anchor date for the rolling window. Defaults to today UTC.

    Returns
    -------
    list[AccuracyRow]
        One row per matched (forecast, actual) pair. Sorted by
        target_date_iso then by lane_id so the order is stable across
        runs. Empty list when nothing pairs.
    """
    anchor = today or datetime.now(timezone.utc).date()
    cutoff_back = anchor - timedelta(days=int(window_days))
    cutoff_fwd = anchor

    rows: list[AccuracyRow] = []
    for lane_id in _list_lane_ids(root):
        forecasts = read_forecasts(lane_id, root=root)
        actuals = read_actuals(lane_id, root=root)
        if not forecasts or not actuals:
            continue

        # Build a (date -> actual_value) index so the inner loop is O(1).
        # When a lane has multiple actuals for the same day, last-write
        # wins — that matches the spirit of "one realized value per day".
        actual_by_date: dict[str, float] = {}
        for a in actuals:
            actual_by_date[a.actual_date_iso] = a.actual_value

        # Dedupe forecasts on (forecast_date, horizon) — re-runs of the
        # log job for the same day must not double-count.
        seen: set[tuple[str, int]] = set()
        for f in forecasts:
            key = (f.forecast_date_iso, f.horizon_days)
            if key in seen:
                continue
            seen.add(key)

            try:
                target = _add_days(f.forecast_date_iso, f.horizon_days)
            except ValueError:
                continue

            # Only score targets inside the rolling window. The forward
            # cutoff drops forecasts whose target is still in the future
            # (no actual could possibly exist yet); the backward cutoff
            # drops ancient pairs that bloat the summary.
            try:
                target_date = date.fromisoformat(target)
            except ValueError:
                continue
            if target_date > cutoff_fwd or target_date < cutoff_back:
                continue

            actual_val = actual_by_date.get(target)
            if actual_val is None:
                # Un-paired — actual hasn't been logged yet. Keep the
                # forecast on disk so the next call (after the actual
                # arrives) can score it.
                continue

            error = float(f.predicted_value) - float(actual_val)
            rows.append(AccuracyRow(
                forecast_date_iso=f.forecast_date_iso,
                target_date_iso=target,
                horizon_days=int(f.horizon_days),
                lane_id=f.lane_id,
                predicted=float(f.predicted_value),
                actual=float(actual_val),
                error=error,
                abs_error=abs(error),
                signed_error=error,
                predicted_sigma=float(getattr(f, "predicted_sigma", 0.0) or 0.0),
                baseline=float(getattr(f, "baseline_value", 0.0) or 0.0),
            ))

    rows.sort(key=lambda r: (r.target_date_iso, r.lane_id, r.horizon_days))
    return rows


# ---------------------------------------------------------------------------
# Summarisation — operator-facing roll-up
# ---------------------------------------------------------------------------


def _empty_summary() -> dict[str, Any]:
    """Defensible empty-summary shape. Keeps the schema stable for the UI."""
    return {
        "n_pairs":          0,
        "mae":              0.0,
        "mae_by_horizon":   {f"{h}d": None for h in _SUMMARY_HORIZONS},
        "mean_signed_error": 0.0,
    }


def summarize_accuracy(rows: list[AccuracyRow]) -> dict[str, Any]:
    """Roll up a list of ``AccuracyRow`` into operator-facing aggregates.

    Returns a dict with:

      * ``n_pairs`` — number of scored (forecast, actual) pairs
      * ``mae`` — mean absolute error across every pair
      * ``mae_by_horizon`` — dict ``{"7d": float|None, "14d": ..., "30d": ...}``;
        ``None`` for any horizon that had no observations in the input
      * ``mean_signed_error`` — average of ``predicted - actual`` — a
        positive bias means the model is consistently optimistic; a
        negative bias means it is consistently pessimistic

    Empty input returns the defensible empty-summary shape so the UI can
    rely on the schema being present regardless of data state.

    NOTE: this summary intentionally carries NO directional ("sign
    agreement") metric. The forecasts it scores are stress LEVELS in
    [0, 1] (always non-negative), so a sign-of-level comparison is
    degenerate — it trivially "agrees" ~100% of the time. Meaningful
    directional accuracy needs the baseline level at forecast time; the
    correct form already lives in
    ``disruption_forecast_backtest._sign_agreement_against`` (sign of
    forecast-minus-baseline vs actual-minus-baseline), wired into the
    disruption-radar UI. If this tracker is ever wired to a producer, add
    a baseline to ``ForecastRecord`` and reuse that form rather than
    reviving a sign-of-level check.
    """
    if not rows:
        return _empty_summary()

    n = len(rows)
    mae_all = sum(r.abs_error for r in rows) / n
    mean_signed = sum(r.signed_error for r in rows) / n

    mae_by_h: dict[str, float | None] = {}
    for horizon in _SUMMARY_HORIZONS:
        bucket = [r.abs_error for r in rows if r.horizon_days == horizon]
        mae_by_h[f"{horizon}d"] = (
            sum(bucket) / len(bucket) if bucket else None
        )

    return {
        "n_pairs":           n,
        "mae":               mae_all,
        "mae_by_horizon":    mae_by_h,
        "mean_signed_error": mean_signed,
    }


# ---------------------------------------------------------------------------
# Calibration scoring (rec R029) — does a quoted interval cover reality?
#
# ``summarize_accuracy`` ships only MAE — it cannot tell whether a forecast's
# stated dispersion (``predicted_sigma``, e.g. ``StressForecast.forecast_sigma``)
# is honest. These pure functions add the proper probabilistic scores:
#   * CRPS — a strictly proper score for the FULL predictive distribution,
#     rewarding sharp AND calibrated forecasts (lower is better; degrades to
#     |error| for a point forecast, sigma=0).
#   * PIT — the probability-integral transform; a calibrated forecast yields
#     PIT ~ Uniform(0,1), so a non-flat PIT histogram exposes mis-calibration.
#   * Coverage — the fraction of actuals inside +-z*sigma (should track the
#     nominal, e.g. ~68% at z=1, ~95% at z=1.96).
#   * Brier + reliability — for a binarised event (threshold exceedance).
# ---------------------------------------------------------------------------

_INV_SQRT_PI = 1.0 / math.sqrt(math.pi)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def _std_norm_cdf(x: float) -> float:
    """Standard-normal CDF via erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _std_norm_pdf(x: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def crps_gaussian(mu: float, sigma: float, y: float) -> float:
    """Closed-form CRPS of a Gaussian predictive ``N(mu, sigma^2)`` vs actual ``y``.

    ``CRPS = sigma * [ z*(2*Phi(z) - 1) + 2*phi(z) - 1/sqrt(pi) ]`` with
    ``z = (y - mu)/sigma``. Strictly proper, non-negative. As ``sigma -> 0`` the
    limit is ``|y - mu|`` (a point forecast scored as absolute error), which we
    return directly to avoid a divide-by-zero.
    """
    mu, sigma, y = float(mu), float(sigma), float(y)
    if sigma <= 0.0:
        return abs(y - mu)
    z = (y - mu) / sigma
    return sigma * (z * (2.0 * _std_norm_cdf(z) - 1.0)
                    + 2.0 * _std_norm_pdf(z) - _INV_SQRT_PI)


def pit_value(mu: float, sigma: float, y: float) -> Optional[float]:
    """PIT = ``Phi((y - mu)/sigma)`` in [0, 1]; ``None`` for a point forecast."""
    sigma = float(sigma)
    if sigma <= 0.0:
        return None
    return _std_norm_cdf((float(y) - float(mu)) / sigma)


def pit_histogram(rows: list[AccuracyRow], *, n_bins: int = 10) -> list[int]:
    """Counts of PIT values per equal-width [0,1] bin (point forecasts skipped).

    A calibrated forecast set is ~flat. A U-shape => intervals too narrow
    (over-confident); a hump => too wide.
    """
    nb = max(1, int(n_bins))
    counts = [0] * nb
    for r in rows or []:
        p = pit_value(r.predicted, r.predicted_sigma, r.actual)
        if p is None:
            continue
        idx = min(nb - 1, int(p * nb))
        counts[idx] += 1
    return counts


def coverage_rate(rows: list[AccuracyRow], *, z: float = 1.0) -> Optional[float]:
    """Fraction of actuals within ``predicted +- z*sigma`` (interval rows only)."""
    interval = [r for r in (rows or []) if r.predicted_sigma > 0.0]
    if not interval:
        return None
    hit = sum(1 for r in interval
              if abs(r.actual - r.predicted) <= z * r.predicted_sigma)
    return hit / len(interval)


def brier_score(probs: list[float], outcomes: list[float]) -> float:
    """Mean squared error of probabilistic event forecasts: ``mean((p - o)^2)``.

    ``outcomes`` are 0/1. Lower is better; 0.25 is the always-0.5 baseline.
    """
    pairs = list(zip(probs or [], outcomes or []))
    if not pairs:
        return 0.0
    return sum((float(p) - (1.0 if o else 0.0)) ** 2 for p, o in pairs) / len(pairs)


def reliability_curve(
    probs: list[float], outcomes: list[float], *, n_bins: int = 10,
) -> list[dict]:
    """Binned reliability: per probability bin, mean forecast prob vs observed freq.

    A well-calibrated model sits on the diagonal (mean_prob ~ observed_freq).
    Returns one dict per non-empty bin: ``{bin_lo, bin_hi, n, mean_prob,
    observed_freq}``.
    """
    nb = max(1, int(n_bins))
    pairs = list(zip(probs or [], outcomes or []))
    out: list[dict] = []
    for i in range(nb):
        lo, hi = i / nb, (i + 1) / nb
        last = i == nb - 1
        bucket = [(float(p), 1.0 if o else 0.0) for p, o in pairs
                  if p >= lo and (p < hi or (last and p <= hi))]
        if not bucket:
            continue
        bn = len(bucket)
        out.append({
            "bin_lo": round(lo, 4), "bin_hi": round(hi, 4), "n": bn,
            "mean_prob": round(sum(p for p, _ in bucket) / bn, 4),
            "observed_freq": round(sum(o for _, o in bucket) / bn, 4),
        })
    return out


def summarize_calibration(
    rows: list[AccuracyRow], *, threshold: Optional[float] = None,
) -> dict[str, Any]:
    """Aggregate calibration of interval forecasts: CRPS, PIT, coverage, skill.

    ``mean_crps`` is over all rows (point forecasts scored as |error|).
    ``crps_skill`` compares the model CRPS to a persistence baseline (the
    forecast-time ``baseline`` level scored as a point forecast); ``> 0`` means
    the model beats persistence. ``coverage_68/95`` track the nominal interval
    coverage. When ``threshold`` is given, a Brier score + reliability curve are
    added for the event ``actual > threshold`` using ``P(exceed) = 1 -
    Phi((threshold - predicted)/sigma)``. Empty input returns a stable schema.
    """
    rows = rows or []
    n = len(rows)
    if n == 0:
        return {"n_pairs": 0, "mean_crps": 0.0, "crps_skill": None,
                "coverage_68": None, "coverage_95": None, "mean_pit": None,
                "pit_histogram": [], "n_interval": 0}

    crps_vals = [crps_gaussian(r.predicted, r.predicted_sigma, r.actual) for r in rows]
    mean_crps = sum(crps_vals) / n

    # Persistence baseline: the forecast-time level held flat, scored as a point
    # forecast. crps_skill is only meaningful when baselines were actually logged.
    base_rows = [r for r in rows if r.baseline != 0.0]
    crps_skill: Optional[float] = None
    if base_rows:
        base_crps = sum(abs(r.actual - r.baseline) for r in base_rows) / len(base_rows)
        model_crps_on_base = sum(
            crps_gaussian(r.predicted, r.predicted_sigma, r.actual) for r in base_rows
        ) / len(base_rows)
        if base_crps > 0:
            crps_skill = round(1.0 - model_crps_on_base / base_crps, 4)

    pits = [pit_value(r.predicted, r.predicted_sigma, r.actual) for r in rows]
    pits = [p for p in pits if p is not None]
    out: dict[str, Any] = {
        "n_pairs": n,
        "n_interval": len(pits),
        "mean_crps": round(mean_crps, 6),
        "crps_skill": crps_skill,
        "coverage_68": (round(c, 4) if (c := coverage_rate(rows, z=1.0)) is not None else None),
        "coverage_95": (round(c, 4) if (c := coverage_rate(rows, z=1.96)) is not None else None),
        "mean_pit": (round(sum(pits) / len(pits), 4) if pits else None),
        "pit_histogram": pit_histogram(rows, n_bins=10),
    }

    if threshold is not None:
        probs, outcomes = [], []
        for r in rows:
            if r.predicted_sigma <= 0.0:
                continue
            p_exceed = 1.0 - _std_norm_cdf((threshold - r.predicted) / r.predicted_sigma)
            probs.append(p_exceed)
            outcomes.append(1.0 if r.actual > threshold else 0.0)
        out["brier_score"] = round(brier_score(probs, outcomes), 6)
        out["reliability_curve"] = reliability_curve(probs, outcomes, n_bins=5)
        out["event_threshold"] = float(threshold)

    return out


# ---------------------------------------------------------------------------
# Scheduler-side helpers — pure functions, not wired into worker yet
# ---------------------------------------------------------------------------


def should_log_forecast_today(today: date | None = None) -> bool:
    """Whether the daily forecast-log job should run today.

    Currently always returns ``True`` — the log job is cheap, idempotent
    on a per-(lane, date, horizon) basis, and a missed day cannot be
    recovered after the fact (we'd be logging a "forecast" we didn't
    actually emit). Accepting ``today`` keeps the door open for future
    schedules (weekday-only, every-Nth-day, etc.) without changing the
    call site.
    """
    _ = today  # reserved for future schedule predicates
    return True


def run_forecast_log_job(
    *,
    root: Path | str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Pair the standing log against actuals + return a summary dict.

    This is the SIDE the scheduler should call after the daily forecast
    run has appended its records via ``log_forecast``. It does NOT itself
    invoke ``processing.disruption_forecast`` — that orchestration belongs
    to ``worker/scheduler.py`` and is intentionally left as a one-line
    follow-up wiring step (other agents are mid-edit on that file).

    Returns
    -------
    dict
        ``{"ok": bool, "today": iso, "n_pairs": int, "summary": dict, "error": str}``
        where ``summary`` is the output of :func:`summarize_accuracy`. On
        any failure, ``ok`` is False and ``error`` carries the message —
        mirrors the worker-job contract used elsewhere in the platform.
    """
    anchor = today or datetime.now(timezone.utc).date()
    payload: dict[str, Any] = {
        "ok":      False,
        "today":   anchor.isoformat(),
        "n_pairs": 0,
        "summary": _empty_summary(),
        "error":   "",
    }
    try:
        rows = match_forecasts_to_actuals(root=root, today=anchor)
        summary = summarize_accuracy(rows)
        payload["ok"] = True
        payload["n_pairs"] = summary["n_pairs"]
        payload["summary"] = summary
    except Exception as exc:  # pragma: no cover - defensive
        payload["error"] = f"{exc.__class__.__name__}: {exc}"
    return payload
