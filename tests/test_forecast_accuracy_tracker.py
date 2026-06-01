"""Defining-property tests for processing/forecast_accuracy_tracker.py."""
from __future__ import annotations

from datetime import date

import pytest

from processing.forecast_accuracy_tracker import (
    ActualRecord,
    ForecastRecord,
    log_actual,
    log_forecast,
    match_forecasts_to_actuals,
    read_actuals,
    read_forecasts,
    summarize_accuracy,
)


# ── 1. Append-only writers + readers ─────────────────────────────────────


def test_log_forecast_then_read_round_trips(tmp_path) -> None:
    r = ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.42, lane_id="transpacific_eb",
    )
    log_forecast(r, root=tmp_path)
    out = read_forecasts("transpacific_eb", root=tmp_path)
    assert len(out) == 1
    assert out[0].predicted_value == pytest.approx(0.42)


def test_log_actual_then_read_round_trips(tmp_path) -> None:
    a = ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.51, lane_id="fleet",
    )
    log_actual(a, root=tmp_path)
    out = read_actuals("fleet", root=tmp_path)
    assert len(out) == 1
    assert out[0].actual_value == pytest.approx(0.51)


def test_read_forecasts_returns_empty_when_no_file(tmp_path) -> None:
    assert read_forecasts("does_not_exist", root=tmp_path) == []


def test_read_actuals_returns_empty_when_no_file(tmp_path) -> None:
    assert read_actuals("does_not_exist", root=tmp_path) == []


# ── 2. Pairing — the core join ───────────────────────────────────────────


def test_pair_exact_match_produces_zero_error(tmp_path) -> None:
    """Forecast=actual on the target date → abs_error = 0."""
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.50, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.50, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 27), window_days=60,
    )
    assert len(rows) == 1
    assert rows[0].abs_error == pytest.approx(0.0)
    assert rows[0].target_date_iso == "2026-05-27"
    assert rows[0].horizon_days == 7


def test_pair_unmatched_forecast_returns_empty(tmp_path) -> None:
    """Forecast logged but no actual on target date → not paired."""
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 27),
    )
    assert rows == []


def test_pair_horizon_must_match_exactly(tmp_path) -> None:
    """Horizon=7 pairs with target+7d, not +6 or +8."""
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    # Actual on wrong day → no pair
    log_actual(ActualRecord(
        actual_date_iso="2026-05-26", actual_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 30),
    )
    assert rows == []


def test_pair_future_target_excluded(tmp_path) -> None:
    """Forecast whose target_date is past today → excluded (can't have actual yet)."""
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-25", horizon_days=30,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-06-24", actual_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 26),   # target 2026-06-24 is still future
    )
    assert rows == []


def test_pair_window_days_clamps_old_history(tmp_path) -> None:
    """A forecast paired-but-old (target before today-window) excluded."""
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-01-01", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-01-08", actual_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 27), window_days=30,
    )
    assert rows == []   # paired but outside the 30-day window


def test_pair_deduplicates_duplicate_forecasts(tmp_path) -> None:
    """Logging the same forecast twice → only one row emitted."""
    rec = ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    )
    log_forecast(rec, root=tmp_path)
    log_forecast(rec, root=tmp_path)   # re-run
    log_actual(ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 27),
    )
    assert len(rows) == 1


# ── 3. Summarisation ─────────────────────────────────────────────────────


def test_summarize_empty_returns_zero_pairs() -> None:
    """Empty input → defensible empty shape."""
    s = summarize_accuracy([])
    assert s["n_pairs"] == 0
    assert s["mae"] == 0.0


def test_summarize_one_perfect_pair_returns_zero_mae(tmp_path) -> None:
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 27),
    )
    s = summarize_accuracy(rows)
    assert s["mae"] == pytest.approx(0.0)
    assert s["n_pairs"] == 1


def test_summarize_aggregates_mae_correctly(tmp_path) -> None:
    """Hand-computed MAE: (|0.5-0.4| + |0.5-0.6|) / 2 = 0.1"""
    # Pair 1 — error +0.1
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.4, lane_id="fleet",
    ), root=tmp_path)
    # Pair 2 — error -0.1
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-21", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-05-28", actual_value=0.6, lane_id="fleet",
    ), root=tmp_path)
    rows = match_forecasts_to_actuals(
        root=tmp_path, today=date(2026, 5, 30),
    )
    s = summarize_accuracy(rows)
    assert s["n_pairs"] == 2
    assert s["mae"] == pytest.approx(0.1)


def test_summary_omits_degenerate_sign_agreement() -> None:
    """#9: the degenerate sign-of-level 'sign_agreement' metric is removed from
    the summary schema — the forecasts it scores are non-negative stress LEVELS
    in [0, 1], so a sign comparison trivially "agreed" ~100% of the time. The
    real directional metric lives in disruption_forecast_backtest
    (_sign_agreement_against, baseline-relative)."""
    keys = set(summarize_accuracy([]).keys())
    assert "sign_agreement" not in keys
    assert keys == {"n_pairs", "mae", "mae_by_horizon", "mean_signed_error"}
