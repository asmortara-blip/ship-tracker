"""Pure-function tests for processing.disruption_forecast.

The forecaster projects shipping stress forward over 7- and 30-day horizons.
These tests pin the [0, 1] bounds on every projected stress number, the
graceful-degradation contract on empty/None inputs, the blend-weight
invariant, the trend classification, and the per-route ordering of the
batch API. No Streamlit, no live feed.
"""
from __future__ import annotations

import pytest

from processing.disruption_forecast import (
    StressForecast,
    forecast_all_stress,
    forecast_route_stress,
)
from processing.shipping_stress_index import compute_shipping_stress


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def all_forecasts() -> list[StressForecast]:
    """The full per-route forecast batch from empty market inputs."""
    return forecast_all_stress({}, {}, [])


# ── Weight invariant ────────────────────────────────────────────────────────


def test_blend_weights_sum_to_one() -> None:
    """The forward-projection blend weights must sum to 1.0."""
    from processing.disruption_forecast import _W_CONGESTION, _W_CURRENT, _W_RATE

    assert abs(_W_CURRENT + _W_CONGESTION + _W_RATE - 1.0) < 1e-9


# ── forecast_route_stress: shape & bounds ───────────────────────────────────


def test_single_route_forecast_shape() -> None:
    fc = forecast_route_stress("transpacific_eb", {}, {}, [])
    assert isinstance(fc, StressForecast)
    assert fc.route_id == "transpacific_eb"
    assert isinstance(fc.route_name, str) and fc.route_name
    assert fc.trend in {"Improving", "Stable", "Worsening"}
    assert isinstance(fc.drivers, list) and fc.drivers


def test_single_route_forecast_bounds() -> None:
    """current/7d/30d stress are all floats in [0, 1]."""
    fc = forecast_route_stress("asia_europe", {}, {}, [])
    for value in (fc.current_stress, fc.stress_7d, fc.stress_30d):
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0


def test_explicit_current_stress_is_respected() -> None:
    """A supplied current_stress is clamped into [0, 1] and used as the anchor."""
    high = forecast_route_stress("transpacific_eb", {}, {}, [], current_stress=0.9)
    low = forecast_route_stress("transpacific_eb", {}, {}, [], current_stress=0.1)
    assert high.current_stress == pytest.approx(0.9, abs=1e-9)
    assert low.current_stress == pytest.approx(0.1, abs=1e-9)
    assert 0.0 <= high.stress_7d <= 1.0
    assert 0.0 <= low.stress_7d <= 1.0


def test_out_of_range_current_stress_is_clamped() -> None:
    """current_stress beyond [0, 1] is clamped, not propagated."""
    fc = forecast_route_stress("transpacific_eb", {}, {}, [], current_stress=5.0)
    assert 0.0 <= fc.current_stress <= 1.0
    assert 0.0 <= fc.stress_7d <= 1.0
    assert 0.0 <= fc.stress_30d <= 1.0


def test_none_current_stress_uses_neutral_baseline() -> None:
    """A ``None`` current_stress falls back to the neutral baseline (~0.4)."""
    from processing.disruption_forecast import _NEUTRAL_STRESS

    fc = forecast_route_stress("transpacific_eb", {}, {}, [], current_stress=None)
    assert fc.current_stress == pytest.approx(_NEUTRAL_STRESS, abs=1e-9)


def test_unknown_route_id_does_not_raise() -> None:
    """An unknown route still yields a valid neutral forecast."""
    fc = forecast_route_stress("not_a_real_route", {}, {}, [])
    assert isinstance(fc, StressForecast)
    assert 0.0 <= fc.stress_7d <= 1.0
    assert 0.0 <= fc.stress_30d <= 1.0


def test_trend_matches_30d_delta() -> None:
    """The trend label is consistent with the 30-day delta vs. current."""
    fc = forecast_route_stress("transpacific_eb", {}, {}, [], current_stress=0.5)
    delta = fc.stress_30d - fc.current_stress
    if fc.trend == "Worsening":
        assert delta > 0.0
    elif fc.trend == "Improving":
        assert delta < 0.0


# ── forecast_all_stress: shape, bounds, ordering ────────────────────────────


def test_forecast_all_returns_one_per_route(
    all_forecasts: list[StressForecast],
) -> None:
    assert isinstance(all_forecasts, list)
    assert len(all_forecasts) > 0
    assert all(isinstance(f, StressForecast) for f in all_forecasts)


def test_forecast_all_bounds(all_forecasts: list[StressForecast]) -> None:
    for fc in all_forecasts:
        assert 0.0 <= fc.current_stress <= 1.0, fc.route_id
        assert 0.0 <= fc.stress_7d <= 1.0, fc.route_id
        assert 0.0 <= fc.stress_30d <= 1.0, fc.route_id


def test_forecast_all_sorted_by_stress_30d_descending(
    all_forecasts: list[StressForecast],
) -> None:
    scores = [fc.stress_30d for fc in all_forecasts]
    assert scores == sorted(scores, reverse=True)


def test_forecast_all_route_ids_unique(
    all_forecasts: list[StressForecast],
) -> None:
    ids = [fc.route_id for fc in all_forecasts]
    assert len(ids) == len(set(ids))


# ── Graceful degradation ────────────────────────────────────────────────────


def test_forecast_all_none_inputs_do_not_raise() -> None:
    """``None`` for every collection argument still yields a valid batch."""
    forecasts = forecast_all_stress(None, None, None)
    assert isinstance(forecasts, list)
    assert len(forecasts) > 0
    for fc in forecasts:
        assert 0.0 <= fc.stress_7d <= 1.0
        assert 0.0 <= fc.stress_30d <= 1.0


def test_forecast_route_none_inputs_do_not_raise() -> None:
    fc = forecast_route_stress("transpacific_eb", None, None, None)
    assert isinstance(fc, StressForecast)
    assert 0.0 <= fc.stress_7d <= 1.0


def test_forecast_all_seeds_from_stress_report() -> None:
    """A supplied ShippingStressReport seeds current_stress without crashing."""
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    forecasts = forecast_all_stress({}, {}, [], stress_report=report)
    assert len(forecasts) > 0
    for fc in forecasts:
        assert 0.0 <= fc.current_stress <= 1.0
        assert 0.0 <= fc.stress_7d <= 1.0
        assert 0.0 <= fc.stress_30d <= 1.0


def test_forecast_all_tolerates_bogus_stress_report() -> None:
    """A duck-typed object lacking ``route_stress`` is handled, not fatal."""

    class _Bogus:
        pass

    forecasts = forecast_all_stress({}, {}, [], stress_report=_Bogus())
    assert len(forecasts) > 0
    assert all(0.0 <= f.stress_30d <= 1.0 for f in forecasts)


# ── Determinism ─────────────────────────────────────────────────────────────


def test_forecast_all_is_repeatable() -> None:
    a = forecast_all_stress({}, {}, [])
    b = forecast_all_stress({}, {}, [])
    assert [f.route_id for f in a] == [f.route_id for f in b]
    assert [f.stress_30d for f in a] == [f.stress_30d for f in b]


# ── Refinements: volatility scaling, symmetric tails, cross-signal ──────────


def _rate_history(rid: str, start: float, end: float, noise_sd: float, *, seed: int = 7):
    """A synthetic ``rate_usd_per_feu`` series: linear trend + Gaussian noise.

    Seeded with a fixed integer (not ``hash(rid)`` — Python's hash is salted per
    process, which made earlier versions of these tests flaky).
    """
    import numpy as np
    import pandas as pd

    dates = pd.date_range("2025-01-01", periods=240, freq="D")
    rng = np.random.default_rng(seed)
    rates = np.linspace(start, end, 240) + rng.normal(0.0, noise_sd, 240)
    return pd.DataFrame({"date": dates, "rate_usd_per_feu": rates.clip(min=1.0)})


def _rate_history_split(historical_vol: float, *, seed: int = 7):
    """Two-segment synthetic rate history with engineered volatility.

    Days 0–149 hold the route's "characteristic" past with the supplied noise σ
    (this is where the historical rate-volatility estimator lives — std of monthly
    log-returns over the full series). Days 150–239 are an identical, noise-free
    linear ramp from 1900 → 2150 on every lane, so the ML rate forecaster reads
    the same momentum / z-score features regardless of how swingy the early
    history was.

    Net effect: same recent shock on every lane, but ``_route_rate_volatility``
    sees a very different dispersion. This is what makes
    ``test_rate_signal_is_volatility_scaled`` deterministic.
    """
    import numpy as np
    import pandas as pd

    dates = pd.date_range("2025-01-01", periods=240, freq="D")
    rng = np.random.default_rng(seed)

    historical = 1900.0 + rng.normal(0.0, historical_vol, size=150)
    recent = np.linspace(1900.0, 2150.0, 90)  # +13.2% over 90 days, no noise
    rates = np.concatenate([historical, recent])
    return pd.DataFrame({"date": dates, "rate_usd_per_feu": rates.clip(min=1.0)})


def test_added_fields_present_and_bounded() -> None:
    """The new StressForecast fields exist and carry sane values on empty input."""
    fc = forecast_route_stress("transpacific_eb", {}, {}, [])
    assert hasattr(fc, "mc_p10_downside")
    assert hasattr(fc, "rate_volatility")
    assert hasattr(fc, "rate_signal_z")
    assert isinstance(fc.structural_oversupply, bool)
    # The P10 downside is one-signed: it never adds upside.
    assert fc.mc_p10_downside <= 0.0
    assert fc.mc_p90_upside >= 0.0
    assert fc.rate_volatility >= 0.0


def test_rate_signal_is_volatility_scaled() -> None:
    """An equal % move counts as a larger z-score on a calm lane than a swingy one.

    Constructed deterministically: both lanes share an identical, noise-free
    recent 90-day ramp (so the rate forecaster reads the same momentum signal),
    only the *early* history differs in volatility. The model should therefore
    scale the same recent move into a larger |z| on the lane with the calmer
    past, and the historical-vol estimate should rank them correctly.
    """
    calm = {"transpacific_eb": _rate_history_split(historical_vol=25.0)}
    swingy = {"asia_europe": _rate_history_split(historical_vol=350.0)}

    fc_calm = forecast_route_stress("transpacific_eb", calm, {}, [], current_stress=0.5)
    fc_swingy = forecast_route_stress("asia_europe", swingy, {}, [], current_stress=0.5)

    assert fc_swingy.rate_volatility > fc_calm.rate_volatility
    assert abs(fc_calm.rate_signal_z) > abs(fc_swingy.rate_signal_z)


def test_symmetric_mc_tails_p10_downside_is_one_signed() -> None:
    """Symmetric MC tails: the P10 downside contribution is non-positive.

    The pre-refactor disruption_forecast only ever added an *upside* tail
    contribution; the symmetric-tails design exposes a one-signed downside
    tail (≤ 0) alongside the one-signed upside tail (≥ 0).

    Note: whether the net stress_30d ends up above or below current depends
    on the OU mean-reversion target estimated from history *and* the other
    blend components. A rate that has fallen far below its trailing mean
    pulls *up* under OU reversion (the rate signal correctly forecasts a
    rebound) — so a falling-rate input does not necessarily produce
    stress_30d < current. What is guaranteed is that the downside *tail*
    itself never contributes upward push, which is what we pin here.
    """
    falling = {"transpacific_eb": _rate_history("transpacific_eb", 2400, 1000, 25.0)}
    fc = forecast_route_stress("transpacific_eb", falling, {}, [], current_stress=0.6)
    # Downside tail contribution: never adds upward push.
    assert fc.mc_p10_downside <= 0.0
    # Upside tail contribution: never adds downward push (the symmetric pair).
    assert fc.mc_p90_upside >= 0.0


def test_structural_oversupply_thresholds_are_sane() -> None:
    """The cross-signal trigger reads as 'high congestion AND falling rates'."""
    from processing.disruption_forecast import (
        _OVERSUPPLY_CONGESTION_MIN,
        _OVERSUPPLY_RATE_MAX,
    )

    # High congestion: clearly above neutral. Falling rates: clearly negative.
    assert 0.5 < _OVERSUPPLY_CONGESTION_MIN < 1.0
    assert _OVERSUPPLY_RATE_MAX < 0.0


def test_structural_oversupply_surfaces_in_narrative() -> None:
    """When the flag is set, the narrative explicitly calls out structural oversupply."""
    from processing.disruption_forecast import _build_narrative

    narrative = _build_narrative(
        "Trans-Pacific Eastbound",
        current=0.70, stress_7d=0.68, stress_30d=0.66,
        trend="Improving", rate_pct=-0.08, mc_p90=0.05,
        mc_p10=-0.04, structural_oversupply=True,
    )
    assert "oversupply" in narrative.lower()
    # Without the flag, the oversupply clause is absent.
    plain = _build_narrative(
        "Trans-Pacific Eastbound",
        current=0.70, stress_7d=0.68, stress_30d=0.66,
        trend="Improving", rate_pct=-0.08, mc_p90=0.05,
        mc_p10=-0.04, structural_oversupply=False,
    )
    assert "oversupply" not in plain.lower()


def test_structural_oversupply_flag_is_bool_in_batch() -> None:
    """Every forecast in a batch exposes a boolean structural_oversupply flag."""
    batch = forecast_all_stress({}, {}, [])
    assert all(isinstance(f.structural_oversupply, bool) for f in batch)


# ── BDI macro-key resolution (BSXRLM canonical, BDIY fallback) ──────────────


def _rising_bdi_frame() -> "pd.DataFrame":  # type: ignore[name-defined]
    """Build a strictly rising 60-row FRED frame so BDI_rising → True."""
    import pandas as pd  # local import keeps the module top tidy

    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    return pd.DataFrame({
        "date": dates,
        "value": [1_500.0 + 5.0 * i for i in range(60)],
    })


def test_macro_for_congestion_resolves_bdi_via_bsxrlm() -> None:
    """The canonical FRED key ``BSXRLM`` must populate BDI_rising."""
    from processing.disruption_forecast import _macro_for_congestion

    adapted = _macro_for_congestion({"BSXRLM": _rising_bdi_frame()})
    assert adapted.get("BDI_rising") is True


def test_macro_for_congestion_resolves_bdi_via_bdiy_fallback() -> None:
    """Legacy callers keyed by ``BDIY`` must continue to resolve as the fallback."""
    from processing.disruption_forecast import _macro_for_congestion

    adapted = _macro_for_congestion({"BDIY": _rising_bdi_frame()})
    assert adapted.get("BDI_rising") is True
