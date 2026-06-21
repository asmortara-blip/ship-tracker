"""The daily forecast-logging scheduler job (rec R046)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _forecast(rid, *, cur=0.5, s7=0.55, s30=0.6, sigma=0.08):
    return SimpleNamespace(
        route_id=rid, route_name=rid, current_stress=cur,
        stress_7d=s7, stress_30d=s30, forecast_sigma=sigma,
        stress_7d_band=(s7 - sigma, s7 + sigma),
        stress_30d_band=(s30 - sigma, s30 + sigma),
    )


def test_logs_forecasts_and_actuals(monkeypatch, tmp_path) -> None:
    import worker.scheduler as sch
    from processing import forecast_accuracy_tracker as fat

    # Redirect the forecast-log root to a tmp dir, and stub the heavy pipeline.
    monkeypatch.setattr(
        "processing.shipping_stress_index.compute_shipping_stress",
        lambda *a, **k: object())
    monkeypatch.setattr(
        "processing.disruption_forecast.forecast_all_stress",
        lambda *a, **k: [_forecast("transpacific_eb"), _forecast("asia_europe")])

    res = sch.run_forecast_logging_job({"stock_data": {}}, root=tmp_path)
    assert res["ok"] is True
    assert res["forecasts"] == 4          # 2 routes x (7d + 30d)
    assert res["actuals"] == 2

    # The forecast records carry the R023 sigma + the persistence baseline.
    recs = fat.read_forecasts("transpacific_eb", root=tmp_path)
    assert len(recs) == 2
    h30 = next(r for r in recs if r.horizon_days == 30)
    assert h30.predicted_value == pytest.approx(0.6)
    assert h30.predicted_sigma == pytest.approx(0.08)
    assert h30.baseline_value == pytest.approx(0.5)
    # 7d sigma is the 30d sigma shrunk by sqrt(7/30).
    h7 = next(r for r in recs if r.horizon_days == 7)
    assert h7.predicted_sigma == pytest.approx(0.08 * (7.0 / 30.0) ** 0.5)


def test_actual_pairs_with_a_prior_forecast(monkeypatch, tmp_path) -> None:
    # A forecast logged 30 days ago (targeting today) must pair with today's
    # actual once the job logs it -> n_pairs > 0. Uses the real "today" on both
    # sides so it stays date-independent.
    import worker.scheduler as sch
    from datetime import datetime, timedelta, timezone
    from processing import forecast_accuracy_tracker as fat

    today = datetime.now(timezone.utc).date()
    thirty_ago = (today - timedelta(days=30)).isoformat()
    fat.log_forecast(fat.ForecastRecord(
        forecast_date_iso=thirty_ago, horizon_days=30, predicted_value=0.62,
        lane_id="transpacific_eb", predicted_sigma=0.08, baseline_value=0.5,
    ), root=tmp_path)

    monkeypatch.setattr(
        "processing.shipping_stress_index.compute_shipping_stress",
        lambda *a, **k: object())
    monkeypatch.setattr(
        "processing.disruption_forecast.forecast_all_stress",
        lambda *a, **k: [_forecast("transpacific_eb", cur=0.58)])

    res = sch.run_forecast_logging_job({}, root=tmp_path)
    assert res["ok"] and res["n_pairs"] >= 1


def test_never_raises_on_bad_bundle(monkeypatch, tmp_path) -> None:
    import worker.scheduler as sch
    monkeypatch.setattr(
        "processing.disruption_forecast.forecast_all_stress",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    res = sch.run_forecast_logging_job(None, root=tmp_path)
    assert res["ok"] is False and res["forecasts"] == 0
