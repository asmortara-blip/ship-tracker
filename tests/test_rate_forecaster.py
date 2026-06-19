"""Tests for processing.rate_forecaster — ML-based freight rate forecaster.

Covers:
  - RateForecast dataclass shape, defaults, field types
  - _data_fingerprint: None / missing-column → "empty"; identical data →
    identical hash; different data → different hash; 16-hex output
  - clear_forecast_cache: empties the module-level cache dict
  - _pct_change_safe: insufficient length → 0.0; zero/NaN old → 0.0; NaN new
    → 0.0; correct sign/magnitude on normal input
  - _latest_macro_value: missing key / empty df / all-NaN → NaN;
    'value' column path + numeric-fallback path both return last scalar
  - _macro_pct_change: missing key / empty / short df → 0.0; zero old → 0.0;
    correct pct change on a normal series
  - _build_features: empty series → ValueError; on a normal series, returns
    a 1-row DataFrame with all 15 expected features; defaults applied for
    missing macro (BDI=0, WTI=75, PMI=100); IPMAN falls back to NAPMPI;
    capacity_proxy = 1 + mom_30d; mean_rev_z = 0 when std90 = 0
  - _build_training_dataset: < min_required rows → ValueError; happy path
    returns aligned (X, y) with same row count, target lifted by horizon
  - _train_forecast_model: horizon ≤ 7 → Ridge; horizon > 7 → GBR; returns
    (model, scaler, r2 clipped to [-1, 1])
  - _gbr_confidence_interval: staged-prediction path returns (low, high)
    with low ≤ high; fallback ±alpha path used when staged unavailable
  - _extract_key_drivers: GBR (feature_importances_) and Ridge (coef_) both
    yield exactly 3 human-readable labels from _FEATURE_LABELS; unknown
    model type → []
  - forecast_route: missing 'date'/'rate_usd_per_feu' cols → None;
    all-fallback source → None; < 30 rows → None; happy path → RateForecast
    with current_rate from last row, direction ∈ {Rising, Falling, Stable},
    direction_confidence ∈ [0,1], CI low ≤ high, forecasts clamped to
    [0.30×, 3.0×] of current_rate, key_drivers length 0–3, cache populated;
    second call with same data hits the cache
  - forecast_all_routes: empty dict → {}; None/empty DataFrames skipped;
    routes with sufficient history produce RateForecast entries keyed by id
"""
from __future__ import annotations

import datetime
from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from processing.rate_forecaster import (
    _FEATURE_LABELS,
    _FORECAST_CACHE,
    RateForecast,
    _build_features,
    _build_training_dataset,
    _data_fingerprint,
    _extract_key_drivers,
    _gbr_confidence_interval,
    _latest_macro_value,
    _macro_pct_change,
    _pct_change_safe,
    _train_forecast_model,
    clear_forecast_cache,
    forecast_all_routes,
    forecast_route,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _rate_df(rates: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(rates), freq="D"),
        "rate_usd_per_feu": rates,
    })


def _noisy_rates(n: int, base: float = 2000.0, slope: float = 5.0,
                 seed: int = 42) -> list[float]:
    """Linear trend + small noise. Perfectly linear inputs produce zero
    residual std which collapses confidence bands; the noise keeps the
    feature matrix non-degenerate for sklearn fits."""
    rng = np.random.default_rng(seed)
    return [base + slope * i + rng.normal(0, 15.0) for i in range(n)]


def _macro_df(values: list[float], start: str = "2024-01-01",
              col: str = "value") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(values), freq="D"),
        col: values,
    })


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with a clean module-level cache."""
    clear_forecast_cache()
    yield
    clear_forecast_cache()


# ─── RateForecast dataclass ─────────────────────────────────────────────────

def test_rate_forecast_required_fields() -> None:
    fc = RateForecast(
        route_id="r",
        route_name="R",
        current_rate=2000.0,
        forecast_7d=2010.0,
        forecast_30d=2100.0,
        forecast_90d=2200.0,
        confidence_interval_30d=(1900.0, 2300.0),
        direction="Rising",
        direction_confidence=0.7,
    )
    # Defaults
    assert fc.key_drivers == []
    assert fc.model_r2 == 0.0
    assert fc.last_updated == ""
    assert isinstance(fc.confidence_interval_30d, tuple)


# ─── _data_fingerprint ──────────────────────────────────────────────────────

def test_data_fingerprint_none_returns_empty() -> None:
    assert _data_fingerprint(None) == "empty"


def test_data_fingerprint_missing_column_returns_empty() -> None:
    df = pd.DataFrame({"other_col": [1.0, 2.0]})
    assert _data_fingerprint(df) == "empty"


def test_data_fingerprint_deterministic_for_same_data() -> None:
    df1 = _rate_df([1000.0, 1100.0, 1200.0])
    df2 = _rate_df([1000.0, 1100.0, 1200.0])
    assert _data_fingerprint(df1) == _data_fingerprint(df2)


def test_data_fingerprint_differs_for_different_data() -> None:
    df1 = _rate_df([1000.0, 1100.0, 1200.0])
    df2 = _rate_df([1000.0, 1100.0, 1200.1])
    assert _data_fingerprint(df1) != _data_fingerprint(df2)


def test_data_fingerprint_returns_16_hex_chars() -> None:
    """blake2b with digest_size=8 → 16 hex chars."""
    fp = _data_fingerprint(_rate_df([1.0, 2.0, 3.0]))
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)


# ─── clear_forecast_cache ──────────────────────────────────────────────────

def test_clear_forecast_cache_empties_dict() -> None:
    # Stuff a synthetic entry into the cache, then clear.
    fake = RateForecast(
        route_id="x", route_name="X", current_rate=1.0,
        forecast_7d=1.0, forecast_30d=1.0, forecast_90d=1.0,
        confidence_interval_30d=(0.9, 1.1),
        direction="Stable", direction_confidence=0.5,
    )
    _FORECAST_CACHE[("x", "abc")] = (fake, datetime.datetime.now(timezone.utc))
    assert _FORECAST_CACHE  # non-empty
    clear_forecast_cache()
    assert _FORECAST_CACHE == {}


# ─── _pct_change_safe ───────────────────────────────────────────────────────

def test_pct_change_safe_insufficient_length_returns_zero() -> None:
    s = pd.Series([100.0, 110.0])
    assert _pct_change_safe(s, periods=5) == 0.0


def test_pct_change_safe_zero_old_returns_zero() -> None:
    s = pd.Series([0.0, 0.0, 100.0])
    assert _pct_change_safe(s, periods=2) == 0.0


def test_pct_change_safe_nan_returns_zero() -> None:
    s = pd.Series([float("nan"), 100.0, 110.0])
    assert _pct_change_safe(s, periods=2) == 0.0


def test_pct_change_safe_normal_input_correct() -> None:
    s = pd.Series([100.0, 105.0, 110.0, 121.0])
    # periods=3 → (121 - 100) / 100 = 0.21
    assert _pct_change_safe(s, periods=3) == pytest.approx(0.21, abs=1e-9)


def test_pct_change_safe_negative_old_uses_abs() -> None:
    s = pd.Series([-50.0, -25.0, 0.0])
    # (0 - (-50)) / abs(-50) = 1.0
    assert _pct_change_safe(s, periods=2) == pytest.approx(1.0, abs=1e-9)


# ─── _latest_macro_value ────────────────────────────────────────────────────

def test_latest_macro_value_missing_key_returns_nan() -> None:
    assert np.isnan(_latest_macro_value({}, "BDIY"))


def test_latest_macro_value_empty_df_returns_nan() -> None:
    assert np.isnan(_latest_macro_value({"BDIY": pd.DataFrame()}, "BDIY"))


def test_latest_macro_value_all_nan_returns_nan() -> None:
    df = _macro_df([float("nan"), float("nan"), float("nan")])
    assert np.isnan(_latest_macro_value({"BDIY": df}, "BDIY"))


def test_latest_macro_value_value_column_returns_last() -> None:
    df = _macro_df([1.0, 2.0, 3.0])
    assert _latest_macro_value({"BDIY": df}, "BDIY") == 3.0


def test_latest_macro_value_numeric_fallback_when_no_value_column() -> None:
    df = _macro_df([10.0, 20.0, 30.0], col="level")
    assert _latest_macro_value({"BDIY": df}, "BDIY") == 30.0


# ─── _macro_pct_change ──────────────────────────────────────────────────────

def test_macro_pct_change_missing_returns_zero() -> None:
    assert _macro_pct_change({}, "BDIY", 30) == 0.0


def test_macro_pct_change_too_short_returns_zero() -> None:
    df = _macro_df([1.0, 2.0, 3.0])
    assert _macro_pct_change({"BDIY": df}, "BDIY", 30) == 0.0


def test_macro_pct_change_zero_old_returns_zero() -> None:
    values = [0.0] + [1.0] * 40
    df = _macro_df(values)
    assert _macro_pct_change({"BDIY": df}, "BDIY", 30) == 0.0


def test_macro_pct_change_normal_input() -> None:
    values = list(np.linspace(100.0, 200.0, 50))
    df = _macro_df(values)
    out = _macro_pct_change({"BDIY": df}, "BDIY", 30)
    # 30-row lookback on linspace 100→200 over 50 points
    expected = (values[-1] - values[-31]) / abs(values[-31])
    assert out == pytest.approx(expected, abs=1e-9)


# ─── _build_features ────────────────────────────────────────────────────────

def test_build_features_empty_series_raises() -> None:
    with pytest.raises(ValueError):
        _build_features(pd.Series([], dtype=float), {})


def test_build_features_returns_single_row_with_all_columns() -> None:
    rates = _noisy_rates(100)
    s = pd.Series(rates)
    feat = _build_features(s, {})
    assert isinstance(feat, pd.DataFrame)
    assert len(feat) == 1
    # All 15 expected features present
    for col in _FEATURE_LABELS:
        assert col in feat.columns


def test_build_features_macro_defaults_when_missing() -> None:
    """bdi_level → 0, wti_price → 75, pmi_level → 100 when not provided."""
    s = pd.Series(_noisy_rates(100))
    feat = _build_features(s, {})
    assert feat["bdi_level"].iloc[0] == 0.0
    assert feat["wti_price"].iloc[0] == 75.0
    assert feat["pmi_level"].iloc[0] == 100.0


def test_build_features_bdi_resolves_via_bsxrlm_macro_key() -> None:
    """The BDI level must be sourced from the canonical FRED ``BSXRLM`` key."""
    s = pd.Series(_noisy_rates(100))
    bsx = _macro_df([1_200.0] * 100)
    feat = _build_features(s, {"BSXRLM": bsx})
    assert feat["bdi_level"].iloc[0] == 1_200.0


def test_build_features_bdi_resolves_via_bdiy_fallback_macro_key() -> None:
    """Legacy callers keyed by ``BDIY`` continue to resolve as the fallback."""
    s = pd.Series(_noisy_rates(100))
    bdi = _macro_df([1_300.0] * 100)
    feat = _build_features(s, {"BDIY": bdi})
    assert feat["bdi_level"].iloc[0] == 1_300.0


def test_build_features_bsxrlm_preferred_over_bdiy_when_both_present() -> None:
    """When both keys are present, the canonical BSXRLM wins."""
    s = pd.Series(_noisy_rates(100))
    bsx = _macro_df([1_111.0] * 100)
    bdi = _macro_df([2_222.0] * 100)
    feat = _build_features(s, {"BSXRLM": bsx, "BDIY": bdi})
    assert feat["bdi_level"].iloc[0] == 1_111.0


def test_build_features_uses_napmpi_when_ipman_missing() -> None:
    """If IPMAN missing, falls back to NAPMPI; PMI default not used."""
    s = pd.Series(_noisy_rates(100))
    napmpi = _macro_df([50.0] * 100)
    feat = _build_features(s, {"NAPMPI": napmpi})
    assert feat["pmi_level"].iloc[0] == 50.0


def test_build_features_capacity_proxy_equals_one_plus_mom30() -> None:
    rates = _noisy_rates(100)
    s = pd.Series(rates)
    feat = _build_features(s, {})
    mom30 = feat["mom_30d"].iloc[0]
    assert feat["capacity_proxy"].iloc[0] == pytest.approx(1.0 + mom30, abs=1e-9)


def test_build_features_mean_rev_z_zero_when_no_variance() -> None:
    """Constant series → std90 = 0 → mean_rev_z = 0 (avoids div-by-zero)."""
    s = pd.Series([1500.0] * 100)
    feat = _build_features(s, {})
    assert feat["mean_rev_z"].iloc[0] == 0.0


def test_build_features_seasonal_flags_are_binary() -> None:
    s = pd.Series(_noisy_rates(100))
    feat = _build_features(s, {})
    assert feat["is_peak_season"].iloc[0] in (0.0, 1.0)
    assert feat["is_cny"].iloc[0] in (0.0, 1.0)


def test_build_features_momentum_zero_when_series_too_short() -> None:
    """When the series has fewer rows than the lookback, mom = 0."""
    s = pd.Series(_noisy_rates(5))  # < 7, < 14, < 30, < 60
    feat = _build_features(s, {})
    assert feat["mom_7d"].iloc[0] == 0.0
    assert feat["mom_14d"].iloc[0] == 0.0
    assert feat["mom_30d"].iloc[0] == 0.0
    assert feat["mom_60d"].iloc[0] == 0.0


# ─── _build_training_dataset ───────────────────────────────────────────────

def test_build_training_dataset_too_short_raises() -> None:
    s = pd.Series(_noisy_rates(20))
    with pytest.raises(ValueError):
        _build_training_dataset(s, {}, horizon=7)


def test_build_training_dataset_returns_aligned_xy() -> None:
    s = pd.Series(_noisy_rates(120))
    X, y = _build_training_dataset(s, {}, horizon=7)
    assert len(X) == len(y)
    assert len(X) >= 1
    # X is a DataFrame with the expected feature columns
    for col in _FEATURE_LABELS:
        assert col in X.columns
    # y is a Series of floats
    assert y.dtype.kind == "f"


def test_build_training_dataset_horizon_30() -> None:
    """Larger horizon still works given a long-enough series."""
    s = pd.Series(_noisy_rates(150))
    X, y = _build_training_dataset(s, {}, horizon=30)
    assert len(X) == len(y) >= 1


# ─── _train_forecast_model ─────────────────────────────────────────────────

def test_train_forecast_model_horizon_7_returns_ridge() -> None:
    from sklearn.linear_model import Ridge
    s = pd.Series(_noisy_rates(120))
    X, y = _build_training_dataset(s, {}, horizon=7)
    model, scaler, r2 = _train_forecast_model(X, y, horizon=7)
    assert isinstance(model, Ridge)
    assert -1.0 <= r2 <= 1.0


def test_train_forecast_model_horizon_30_returns_gbr() -> None:
    from sklearn.ensemble import GradientBoostingRegressor
    s = pd.Series(_noisy_rates(150))
    X, y = _build_training_dataset(s, {}, horizon=30)
    model, scaler, r2 = _train_forecast_model(X, y, horizon=30)
    assert isinstance(model, GradientBoostingRegressor)
    assert -1.0 <= r2 <= 1.0
    # Trained model can predict on transformed features
    pred = model.predict(scaler.transform(X.head(1)))
    assert pred.shape == (1,)


def test_oos_r2_reports_no_fake_skill_on_noise() -> None:
    # Overlapping rolling-window features + a target INDEPENDENT of them. The
    # leak-free walk-forward CV (scaler-in-fold + embargo) must not fabricate
    # skill — the exact inflation the old full-scaler/plain-KFold path produced.
    import numpy as np
    rng = np.random.default_rng(0)
    raw = rng.normal(0.0, 1.0, 260)
    X = pd.DataFrame({f"lag{k}": raw[k:k + 200] for k in range(5)})  # overlapping
    y = pd.Series(rng.normal(0.0, 1.0, 200))                        # independent
    _, _, r2 = _train_forecast_model(X, y, horizon=30)
    assert -1.0 <= r2 <= 0.10


def test_oos_r2_recovers_genuine_signal() -> None:
    # A clean, learnable, stationary relationship → the leak-free CV still
    # reports real skill (the fix removes inflation, not legitimate signal).
    import numpy as np
    rng = np.random.default_rng(1)
    n = 400
    x = rng.normal(0.0, 1.0, n)
    X = pd.DataFrame({"f": x})
    y = pd.Series(2.0 * x + rng.normal(0.0, 0.05, n))
    _, _, r2 = _train_forecast_model(X, y, horizon=7)   # Ridge, embargo=7
    assert r2 > 0.5


# ─── _gbr_confidence_interval ──────────────────────────────────────────────

def test_gbr_confidence_interval_returns_lo_le_hi() -> None:
    s = pd.Series(_noisy_rates(150))
    X, y = _build_training_dataset(s, {}, horizon=30)
    model, scaler, _ = _train_forecast_model(X, y, horizon=30)
    lo, hi = _gbr_confidence_interval(model, scaler, X.head(1))
    assert lo <= hi


def test_gbr_confidence_interval_fallback_when_no_staged() -> None:
    """A Ridge model has no staged_predict → fallback ±alpha band fires."""
    s = pd.Series(_noisy_rates(120))
    X, y = _build_training_dataset(s, {}, horizon=7)
    model, scaler, _ = _train_forecast_model(X, y, horizon=7)  # Ridge
    lo, hi = _gbr_confidence_interval(model, scaler, X.head(1), alpha=0.20)
    center = float(model.predict(scaler.transform(X.head(1)))[0])
    # Fallback symmetric around the central prediction (sign-dependent).
    if center >= 0:
        assert lo == pytest.approx(center * 0.80, abs=1e-6)
        assert hi == pytest.approx(center * 1.20, abs=1e-6)
    else:
        # Negative center: (1 - alpha) gives the higher value
        assert lo == pytest.approx(center * 1.20, abs=1e-6)
        assert hi == pytest.approx(center * 0.80, abs=1e-6)


# ─── _extract_key_drivers ──────────────────────────────────────────────────

def test_extract_key_drivers_from_gbr() -> None:
    s = pd.Series(_noisy_rates(150))
    X, y = _build_training_dataset(s, {}, horizon=30)
    model, _, _ = _train_forecast_model(X, y, horizon=30)
    drivers = _extract_key_drivers(model, list(X.columns))
    assert len(drivers) == 3
    # All labels are non-empty strings
    assert all(isinstance(d, str) and d for d in drivers)


def test_extract_key_drivers_from_ridge_uses_coef() -> None:
    s = pd.Series(_noisy_rates(120))
    X, y = _build_training_dataset(s, {}, horizon=7)
    model, _, _ = _train_forecast_model(X, y, horizon=7)
    drivers = _extract_key_drivers(model, list(X.columns))
    assert len(drivers) == 3


def test_extract_key_drivers_unknown_model_returns_empty() -> None:
    class _Bogus:
        pass
    assert _extract_key_drivers(_Bogus(), ["a", "b", "c"]) == []


def test_extract_key_drivers_uses_human_labels_when_available() -> None:
    """Drivers should be mapped to _FEATURE_LABELS values, not raw feature
    names, whenever a label exists for the top features."""
    s = pd.Series(_noisy_rates(150))
    X, y = _build_training_dataset(s, {}, horizon=30)
    model, _, _ = _train_forecast_model(X, y, horizon=30)
    drivers = _extract_key_drivers(model, list(X.columns))
    label_values = set(_FEATURE_LABELS.values())
    # At least one driver should be a known human label (top 3 are all in the
    # registered feature set, since _build_features only emits those 15).
    assert any(d in label_values for d in drivers)


# ─── forecast_route — input guards ─────────────────────────────────────────

def test_forecast_route_returns_none_when_date_column_missing() -> None:
    df = pd.DataFrame({"rate_usd_per_feu": _noisy_rates(60)})
    assert forecast_route("r", "R", df, {}) is None


def test_forecast_route_returns_none_when_rate_column_missing() -> None:
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=60)})
    assert forecast_route("r", "R", df, {}) is None


def test_forecast_route_returns_none_when_all_rows_fallback() -> None:
    df = _rate_df(_noisy_rates(60))
    df["source"] = "fallback"
    assert forecast_route("r", "R", df, {}) is None


def test_forecast_route_returns_none_when_under_30_rows() -> None:
    df = _rate_df(_noisy_rates(20))
    assert forecast_route("r", "R", df, {}) is None


def test_forecast_route_filters_non_positive_rates() -> None:
    """Rows with rate <= 0 are dropped; if too few remain, return None."""
    rates = _noisy_rates(60)
    # Wipe most of them to zero, leaving fewer than 30 valid rows
    for i in range(0, 50):
        rates[i] = 0.0
    df = _rate_df(rates)
    assert forecast_route("r", "R", df, {}) is None


# ─── forecast_route — happy path ───────────────────────────────────────────

def test_forecast_route_happy_path_returns_well_formed_dataclass() -> None:
    df = _rate_df(_noisy_rates(150))
    fc = forecast_route("r1", "Route 1", df, {})
    assert fc is not None
    assert isinstance(fc, RateForecast)
    assert fc.route_id == "r1"
    assert fc.route_name == "Route 1"
    # current_rate matches the last positive observation
    assert fc.current_rate == pytest.approx(df["rate_usd_per_feu"].iloc[-1], abs=1e-9)
    # Direction enum
    assert fc.direction in {"Rising", "Falling", "Stable"}
    # Confidence in [0, 1]
    assert 0.0 <= fc.direction_confidence <= 1.0
    # R² in [0, 1]
    assert 0.0 <= fc.model_r2 <= 1.0
    # CI ordering
    lo, hi = fc.confidence_interval_30d
    assert lo <= hi
    # last_updated is ISO-ish
    assert "UTC" in fc.last_updated


def test_forecast_route_forecasts_within_clamp_bounds() -> None:
    """All three forecasts are clamped to [0.30×, 3.0×] of current_rate."""
    df = _rate_df(_noisy_rates(150))
    fc = forecast_route("r2", "R2", df, {})
    assert fc is not None
    lo, hi = fc.current_rate * 0.30, fc.current_rate * 3.0
    assert lo <= fc.forecast_7d <= hi
    assert lo <= fc.forecast_30d <= hi
    assert lo <= fc.forecast_90d <= hi


def test_forecast_route_direction_consistent_with_pct_change() -> None:
    """direction is derived from (forecast_30d - current_rate) / current_rate
    with thresholds ±3%. Pin the exact relationship."""
    df = _rate_df(_noisy_rates(150))
    fc = forecast_route("r_dir", "R Dir", df, {})
    assert fc is not None
    pct = (fc.forecast_30d - fc.current_rate) / fc.current_rate
    if pct > 0.03:
        assert fc.direction == "Rising"
    elif pct < -0.03:
        assert fc.direction == "Falling"
    else:
        assert fc.direction == "Stable"


def test_forecast_route_ci_low_nonnegative() -> None:
    """ci_low is forced to max(0, …); no negative confidence floor."""
    df = _rate_df(_noisy_rates(150))
    fc = forecast_route("r3", "R3", df, {})
    assert fc is not None
    assert fc.confidence_interval_30d[0] >= 0.0


def test_forecast_route_key_drivers_length_at_most_3() -> None:
    df = _rate_df(_noisy_rates(150))
    fc = forecast_route("r4", "R4", df, {})
    assert fc is not None
    assert 0 <= len(fc.key_drivers) <= 3


def test_forecast_route_cache_hit_returns_same_object() -> None:
    """Second call with identical data hits the cache and returns the same
    RateForecast instance (within TTL)."""
    df = _rate_df(_noisy_rates(150))
    fc1 = forecast_route("rcache", "R Cache", df, {})
    fc2 = forecast_route("rcache", "R Cache", df, {})
    assert fc1 is not None and fc2 is not None
    assert fc1 is fc2  # cached object identity


def test_forecast_route_cache_miss_when_data_changes() -> None:
    """Different fingerprint → cache miss → fresh forecast (different identity)."""
    df1 = _rate_df(_noisy_rates(150, seed=1))
    df2 = _rate_df(_noisy_rates(150, seed=2))
    fc1 = forecast_route("rfp", "R FP", df1, {})
    fc2 = forecast_route("rfp", "R FP", df2, {})
    assert fc1 is not None and fc2 is not None
    assert fc1 is not fc2


def test_forecast_route_cached_entry_keyed_by_id_and_fingerprint() -> None:
    df = _rate_df(_noisy_rates(150))
    forecast_route("r_key", "R Key", df, {})
    fp = _data_fingerprint(df)
    assert ("r_key", fp) in _FORECAST_CACHE


# ─── forecast_all_routes ───────────────────────────────────────────────────

def test_forecast_all_routes_empty_returns_empty_dict() -> None:
    assert forecast_all_routes({}, {}) == {}


def test_forecast_all_routes_skips_none_and_empty() -> None:
    from routes.route_registry import ROUTES
    freight = {
        ROUTES[0].id: None,
        ROUTES[1].id: pd.DataFrame(),
    }
    out = forecast_all_routes(freight, {})
    assert out == {}


def test_forecast_all_routes_returns_forecasts_keyed_by_route_id() -> None:
    from routes.route_registry import ROUTES
    freight = {
        r.id: _rate_df(_noisy_rates(150, seed=i + 1))
        for i, r in enumerate(ROUTES[:3])
    }
    out = forecast_all_routes(freight, {})
    assert isinstance(out, dict)
    assert len(out) == 3
    for rid, fc in out.items():
        assert rid in freight
        assert isinstance(fc, RateForecast)
        assert fc.route_id == rid


def test_forecast_all_routes_uses_route_registry_names() -> None:
    """Names come from ROUTES registry, not from the raw route_id slug."""
    from routes.route_registry import ROUTES
    target = ROUTES[0]
    freight = {target.id: _rate_df(_noisy_rates(150))}
    out = forecast_all_routes(freight, {})
    assert target.id in out
    assert out[target.id].route_name == target.name


def test_forecast_all_routes_unknown_route_id_falls_back_to_titlecase() -> None:
    """If a route_id isn't in the registry, the name is derived from the id."""
    freight = {"my_custom_lane": _rate_df(_noisy_rates(150))}
    out = forecast_all_routes(freight, {})
    assert "my_custom_lane" in out
    assert out["my_custom_lane"].route_name == "My Custom Lane"
