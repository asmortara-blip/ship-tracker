"""Honest forward intervals on StressForecast — band + sigma (rec R023)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from processing.disruption_forecast import (
    _FORECAST_SIGMA_MAX,
    _FORECAST_SIGMA_MIN,
    StressForecast,
    forecast_route_stress,
)


def _route_id() -> str:
    from routes.route_registry import ROUTES_BY_ID
    return next(iter(ROUTES_BY_ID))


def _freight(route_id, *, vol=0.04, n=120, seed=0):
    """Synthetic freight history for one route with a tunable volatility.

    Uses the real ``rate_usd_per_feu`` column the volatility reader expects so
    the calm-vs-volatile comparison is genuine (a wrong column would silently
    floor both)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    rate = 2000.0 * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    return {route_id: pd.DataFrame({"rate_usd_per_feu": rate}, index=idx)}


def test_dataclass_has_interval_fields() -> None:
    f = StressForecast(route_id="x", route_name="X", current_stress=0.4,
                       stress_7d=0.4, stress_30d=0.4, trend="Stable",
                       rate_forecast_pct=0.0, mc_p90_upside=0.0, narrative="")
    assert f.stress_7d_band == (0.0, 0.0)
    assert f.stress_30d_band == (0.0, 0.0)
    assert f.forecast_sigma == 0.0


def test_bands_bracket_the_point_and_clamp_to_unit() -> None:
    rid = _route_id()
    f = forecast_route_stress(rid, _freight(rid), {}, [], current_stress=0.5)
    lo7, hi7 = f.stress_7d_band
    lo30, hi30 = f.stress_30d_band
    assert lo7 <= f.stress_7d <= hi7
    assert lo30 <= f.stress_30d <= hi30
    for v in (lo7, hi7, lo30, hi30):
        assert 0.0 <= v <= 1.0


def test_sigma_within_bounds() -> None:
    rid = _route_id()
    f = forecast_route_stress(rid, _freight(rid), {}, [], current_stress=0.5)
    assert _FORECAST_SIGMA_MIN <= f.forecast_sigma <= _FORECAST_SIGMA_MAX


def test_7d_band_is_narrower_than_30d() -> None:
    rid = _route_id()
    f = forecast_route_stress(rid, _freight(rid), {}, [], current_stress=0.5)
    width7 = f.stress_7d_band[1] - f.stress_7d_band[0]
    width30 = f.stress_30d_band[1] - f.stress_30d_band[0]
    # Equal only at the clamp boundary; otherwise 7d (sqrt-time scaled) < 30d.
    assert width7 <= width30


def test_volatile_route_gets_a_wider_band_than_a_calm_one() -> None:
    rid = _route_id()
    calm = forecast_route_stress(rid, _freight(rid, vol=0.01, seed=1), {}, [],
                                 current_stress=0.5)
    swingy = forecast_route_stress(rid, _freight(rid, vol=0.20, seed=2), {}, [],
                                   current_stress=0.5)
    assert swingy.forecast_sigma >= calm.forecast_sigma


def test_neutral_defaults_still_ship_a_floor_band() -> None:
    # Even with no freight history, a forward forecast is never certain -> the
    # sigma floor keeps the band non-degenerate.
    rid = _route_id()
    f = forecast_route_stress(rid, {}, {}, [], current_stress=0.5)
    assert f.forecast_sigma >= _FORECAST_SIGMA_MIN
    assert f.stress_30d_band[1] > f.stress_30d_band[0]
