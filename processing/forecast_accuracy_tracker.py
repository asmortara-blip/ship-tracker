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
