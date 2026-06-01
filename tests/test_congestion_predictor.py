"""Pure-function tests for processing.congestion_predictor.

The predictor produces 7-/14-/30-day port-congestion outlooks. These tests pin
the three documented refinements over the original stateless smoother:

  * confidence bands — present, [0, 1]-bounded, and widening with the horizon;
  * port-specific mean-reversion baselines — busy hubs revert higher than quiet
    feeder ports;
  * magnitude-aware macro pressure — pressure scales with the size of the BDI
    move and the PMI distance from neutral, not a yes/no threshold.

No Streamlit, no live feed.
"""
from __future__ import annotations

from processing.congestion_predictor import (
    CongestionForecast,
    _macro_pressure,
    _port_baseline,
    predict_all_ports,
    predict_congestion,
)


# ── Shape & backward compatibility ──────────────────────────────────────────


def test_forecast_shape_and_bounds() -> None:
    """All point forecasts are [0, 1] floats and the original fields survive."""
    fc = predict_congestion("CNSHA", 0.6, {"PMI": 53.0})
    assert isinstance(fc, CongestionForecast)
    for value in (fc.predicted_7d, fc.predicted_14d, fc.predicted_30d):
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0
    assert fc.trend in {"WORSENING", "STABLE", "IMPROVING"}
    assert isinstance(fc.driving_factors, list) and fc.driving_factors


# ── Confidence bands ────────────────────────────────────────────────────────


def test_confidence_bands_bracket_point_and_are_bounded() -> None:
    """Each band is a [0, 1] (low, high) tuple bracketing its point forecast."""
    fc = predict_congestion("USLAX", 0.55, {})
    for point, band in (
        (fc.predicted_7d, fc.band_7d),
        (fc.predicted_14d, fc.band_14d),
        (fc.predicted_30d, fc.band_30d),
    ):
        low, high = band
        assert 0.0 <= low <= high <= 1.0
        assert low <= point <= high


def test_band_widens_with_horizon() -> None:
    """The 30-day band is wider than the 7-day band (random-walk fan-out)."""
    fc = predict_congestion("NLRTM", 0.5, {})
    width_7d = fc.band_7d[1] - fc.band_7d[0]
    width_30d = fc.band_30d[1] - fc.band_30d[0]
    assert width_30d > width_7d


def test_observed_history_drives_band_volatility() -> None:
    """A jumpy congestion history yields a wider band than a calm one."""
    calm = predict_congestion(
        "CNSHA", 0.6, {}, congestion_history=[0.60, 0.61, 0.60, 0.59, 0.60, 0.61]
    )
    wild = predict_congestion(
        "CNSHA", 0.6, {}, congestion_history=[0.30, 0.80, 0.40, 0.90, 0.20, 0.70]
    )
    assert wild.congestion_volatility > calm.congestion_volatility
    assert (wild.band_30d[1] - wild.band_30d[0]) > (calm.band_30d[1] - calm.band_30d[0])


# ── Port-specific mean-reversion baseline ───────────────────────────────────


def test_busy_hub_baseline_exceeds_quiet_port() -> None:
    """A mega-hub reverts toward a higher baseline than a quiet feeder port."""
    assert _port_baseline("CNSHA") > _port_baseline("MATNM")


def test_unknown_port_uses_default_baseline() -> None:
    """An unrecognised LOCODE falls back to the documented default baseline."""
    from processing.congestion_predictor import _DEFAULT_BASELINE

    assert _port_baseline("ZZZZZ") == _DEFAULT_BASELINE
    assert _port_baseline("") == _DEFAULT_BASELINE


def test_30d_forecast_reverts_toward_port_baseline() -> None:
    """From the same low seed, a busy hub's 30d forecast lands above a quiet port's."""
    hub = predict_congestion("CNSHA", 0.2, {})
    quiet = predict_congestion("MATNM", 0.2, {})
    assert hub.predicted_30d > quiet.predicted_30d
    # The forecast exposes which baseline it used.
    assert hub.reversion_baseline == _port_baseline("CNSHA")


# ── Magnitude-aware macro pressure ──────────────────────────────────────────


def test_macro_pressure_scales_with_bdi_move_size() -> None:
    """A larger BDI %% change produces strictly more pressure (continuous, not binary)."""
    small = _macro_pressure({"BDI_change_pct": 0.02})
    large = _macro_pressure({"BDI_change_pct": 0.25})
    assert 0.0 < small < large


def test_macro_pressure_ignores_falling_bdi() -> None:
    """A falling BDI does not add congestion pressure (one-signed channel)."""
    assert _macro_pressure({"BDI_change_pct": -0.20}) == 0.0


def test_macro_pressure_legacy_bool_still_honoured() -> None:
    """The legacy ``BDI_rising`` flag still yields a sensible (sub-maximal) pressure."""
    legacy = _macro_pressure({"BDI_rising": True})
    full = _macro_pressure({"BDI_change_pct": 0.50})
    assert 0.0 < legacy < full


def test_macro_pressure_scales_with_pmi_distance() -> None:
    """PMI pressure scales with distance above neutral; contraction adds nothing."""
    near = _macro_pressure({"PMI": 50.5})
    far = _macro_pressure({"PMI": 58.0})
    assert 0.0 < near < far
    assert _macro_pressure({"PMI": 46.0}) == 0.0


def test_empty_macro_data_is_neutral() -> None:
    """No macro data => zero pressure."""
    assert _macro_pressure(None) == 0.0
    assert _macro_pressure({}) == 0.0


# ── Batch API ───────────────────────────────────────────────────────────────


def test_predict_all_ports_passes_history_through() -> None:
    """``predict_all_ports`` forwards a per-port ``congestion_history`` to the band."""
    port_results = [
        {"port_locode": "CNSHA", "current_congestion": 0.6,
         "congestion_history": [0.30, 0.80, 0.40, 0.90, 0.20, 0.70]},
        {"port_locode": "MATNM", "current_congestion": 0.4},
    ]
    out = predict_all_ports(port_results, {})
    assert set(out) == {"CNSHA", "MATNM"}
    # The port with a jumpy history carries the higher measured volatility.
    assert out["CNSHA"].congestion_volatility > 0.0
