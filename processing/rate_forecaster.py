"""
ML-based Freight Rate Forecaster
=================================
Uses scikit-learn (GradientBoostingRegressor for 30d, Ridge for 7d) with a rich
feature set drawn from rate history and macro indicators.

Intentionally interpretable — feature importances are mapped to human labels so
users can understand *why* a forecast was generated, not just trust a number.
"""
from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

# ── Feature name → human-readable label mapping ──────────────────────────────
_FEATURE_LABELS: dict[str, str] = {
    "mom_7d":         "7-day rate momentum",
    "mom_14d":        "14-day rate momentum",
    "mom_30d":        "30-day rate momentum",
    "mom_60d":        "60-day rate momentum",
    "month":          "Seasonal month",
    "week_of_year":   "Week of year",
    "is_peak_season": "Peak season (Jul–Oct)",
    "is_cny":         "Chinese New Year period (Jan–Feb)",
    "bdi_level":      "Baltic Dry Index level",
    "bdi_chg_30d":    "BDI 30-day change",
    "bdi_chg_90d":    "BDI 90-day change",
    "wti_price":      "WTI crude oil (fuel proxy)",
    "pmi_level":      "Manufacturing PMI (demand proxy)",
    "mean_rev_z":     "Mean reversion z-score vs 90-day avg",
    "capacity_proxy": "Capacity proxy (inverse rate decline)",
}


@dataclass
class RateForecast:
    route_id: str
    route_name: str
    current_rate: float
    forecast_7d: float
    forecast_30d: float
    forecast_90d: float
    confidence_interval_30d: tuple          # (low, high)
    direction: str                          # "Rising" | "Falling" | "Stable"
    direction_confidence: float             # 0–1
    key_drivers: list[str] = field(default_factory=list)   # top 3 factors
    model_r2: float = 0.0                   # GBR out-of-sample R²
    last_updated: str = ""


# ── Simple in-process cache (avoids re-training within one session) ──────────
_FORECAST_CACHE: dict[str, tuple[RateForecast, datetime.datetime]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Feature engineering
# ─────────────────────────────────────────────────────────────────────────────

def _pct_change_safe(series: pd.Series, periods: int) -> float:
    """Return percentage change over `periods` rows, or 0 if insufficient data."""
    if len(series) <= periods:
        return 0.0
    old = series.iloc[-(periods + 1)]
    new = series.iloc[-1]
    if old == 0 or pd.isna(old) or pd.isna(new):
        return 0.0
    return float((new - old) / abs(old))


def _latest_macro_value(macro_data: dict, series_id: str) -> float:
    """Extract the most-recent scalar from a macro_data series DataFrame."""
    df = macro_data.get(series_id)
    if df is None or df.empty:
        return float("nan")
    # FRED DataFrames typically have a 'value' column; fall back to first numeric col
    val_col = "value" if "value" in df.columns else df.select_dtypes(include="number").columns[0]
    s = df[val_col].dropna()
    if s.empty:
        return float("nan")
    return float(s.iloc[-1])


def _macro_pct_change(macro_data: dict, series_id: str, lookback_rows: int) -> float:
    """Percentage change over the last `lookback_rows` observations for a macro series."""
    df = macro_data.get(series_id)
    if df is None or df.empty or len(df) < lookback_rows + 1:
        return 0.0
    val_col = "value" if "value" in df.columns else df.select_dtypes(include="number").columns[0]
    s = df[val_col].dropna()
    if len(s) < lookback_rows + 1:
        return 0.0
    old, new = float(s.iloc[-(lookback_rows + 1)]), float(s.iloc[-1])
    if old == 0 or pd.isna(old) or pd.isna(new):
        return 0.0
    return (new - old) / abs(old)


def _build_features(rate_series: pd.Series, macro_data: dict) -> pd.DataFrame:
    """Build a single-row feature matrix from the tail of `rate_series` + macro data.

    Returns a DataFrame with one row (the "current" observation) ready for
    model.predict().  The same column ordering is used during training so that
    column names align automatically via pandas.
    """
    if rate_series.empty:
        raise ValueError("rate_series is empty")

    rates = rate_series.dropna().values.astype(float)
    n = len(rates)

    # ── Freight momentum ──────────────────────────────────────────────────────
    def _mom(periods: int) -> float:
        if n <= periods:
            return 0.0
        old = rates[-(periods + 1)] if periods < n else rates[0]
        new = rates[-1]
        return (new - old) / abs(old) if old != 0 else 0.0

    mom_7d  = _mom(7)
    mom_14d = _mom(14)
    mom_30d = _mom(30)
    mom_60d = _mom(60)

    # ── Seasonality ───────────────────────────────────────────────────────────
    today = datetime.date.today()
    month        = today.month
    week_of_year = today.isocalendar()[1]
    is_peak      = 1 if month in (7, 8, 9, 10) else 0
    is_cny       = 1 if month in (1, 2) else 0

    # ── BDI ───────────────────────────────────────────────────────────────────
    bdi_level  = _latest_macro_value(macro_data, "BDIY")
    bdi_chg30  = _macro_pct_change(macro_data, "BDIY", 30)
    bdi_chg90  = _macro_pct_change(macro_data, "BDIY", 90)

    # ── WTI (bunker fuel proxy) ───────────────────────────────────────────────
    wti = _latest_macro_value(macro_data, "DCOILWTICO")

    # ── PMI (manufacturing demand proxy) ─────────────────────────────────────
    pmi = _latest_macro_value(macro_data, "IPMAN")
    if np.isnan(pmi):
        pmi = _latest_macro_value(macro_data, "NAPMPI")

    # ── Mean reversion: z-score of current rate vs 90-day window ─────────────
    window = rates[-90:] if n >= 90 else rates
    mean90 = float(np.mean(window))
    std90  = float(np.std(window))
    mean_rev_z = (rates[-1] - mean90) / std90 if std90 > 0 else 0.0

    # ── Capacity proxy: inverse of recent rate-decline speed ──────────────────
    # Positive momentum → capacity tighter → proxy > 1; sharp declines → < 1
    capacity_proxy = 1.0 + mom_30d  # simple, bounded interpretation

    feat = {
        "mom_7d":         mom_7d,
        "mom_14d":        mom_14d,
        "mom_30d":        mom_30d,
        "mom_60d":        mom_60d,
        "month":          float(month),
        "week_of_year":   float(week_of_year),
        "is_peak_season": float(is_peak),
        "is_cny":         float(is_cny),
        "bdi_level":      bdi_level if not np.isnan(bdi_level) else 0.0,
        "bdi_chg_30d":    bdi_chg30,
        "bdi_chg_90d":    bdi_chg90,
        "wti_price":      wti if not np.isnan(wti) else 75.0,   # reasonable default
        "pmi_level":      pmi if not np.isnan(pmi) else 100.0,  # index ≈100 neutral
        "mean_rev_z":     mean_rev_z,
        "capacity_proxy": capacity_proxy,
    }
    return pd.DataFrame([feat])


def _build_training_dataset(
    rate_series: pd.Series,
    macro_data: dict,
    horizon: int,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build X, y for a rolling-window supervised training set.

    Each row i uses features computed from rates[:i] to predict
    the rate `horizon` periods later.  We need at least horizon + 30 rows.
    """
    rates = rate_series.dropna().reset_index(drop=True)
    n = len(rates)
    min_required = max(horizon + 30, 60)
    if n < min_required:
        raise ValueError(
            f"Need ≥{min_required} data points for {horizon}d horizon, got {n}"
        )

    rows = []
    targets = []

    # Build one training example per available window (step every 7 days for speed)
    step = max(1, (n - min_required) // 40)   # at most ~40 training rows
    indices = list(range(min_required - horizon, n - horizon, step))
    if not indices:
        raise ValueError("No training indices could be constructed")

    # We don't want to retrain heavy macro lookups per row, so we use a static
    # snapshot of macro values (macro data doesn't change per in-sample row).
    static_macro = macro_data  # same macro for all training rows

    for i in indices:
        sub = rates.iloc[: i + 1]
        try:
            row_df = _build_features(sub, static_macro)
            rows.append(row_df)
            targets.append(float(rates.iloc[i + horizon]))
        except Exception:
            continue

    if not rows:
        raise ValueError("Feature building produced no training rows")

    X = pd.concat(rows, ignore_index=True)
    y = pd.Series(targets, name="target")
    return X, y


# ─────────────────────────────────────────────────────────────────────────────
# Model training
# ─────────────────────────────────────────────────────────────────────────────

def _train_forecast_model(
    X: pd.DataFrame,
    y: pd.Series,
    horizon: int,
) -> tuple:
    """Train model appropriate for horizon.  Returns (model, scaler, r2_score).

    - 7d  → Ridge (stable, avoids overfit on short horizons)
    - 30d → GradientBoostingRegressor (captures non-linear macro interactions)
    """
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import cross_val_score
    import numpy as _np

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    if horizon <= 7:
        model = Ridge(alpha=10.0)
    else:
        model = GradientBoostingRegressor(
            n_estimators=80,
            max_depth=3,
            learning_rate=0.08,
            subsample=0.8,
            min_samples_leaf=2,
            random_state=42,
        )

    n = len(X)
    if n >= 10:
        cv_folds = min(5, n // 4)
        try:
            scores = cross_val_score(
                model, X_scaled, y,
                cv=cv_folds,
                scoring="r2",
                n_jobs=1,
            )
            r2 = float(_np.clip(_np.nanmean(scores), -1.0, 1.0))
        except Exception:
            r2 = 0.0
    else:
        r2 = 0.0

    model.fit(X_scaled, y)
    return model, scaler, r2


# ─────────────────────────────────────────────────────────────────────────────
# Confidence interval via GBR staged predictions
# ─────────────────────────────────────────────────────────────────────────────

def _gbr_confidence_interval(
    gbr_model,
    scaler,
    X_pred: pd.DataFrame,
    alpha: float = 0.15,
) -> tuple[float, float]:
    """Estimate a confidence interval from GBR staged predictions.

    Uses the spread of ensemble predictions across early vs. late stages.
    Falls back to a ±15% band if staged predictions are unavailable.
    """
    try:
        from sklearn.ensemble import GradientBoostingRegressor
        X_s = scaler.transform(X_pred)
        stage_preds = [p for p in gbr_model.staged_predict(X_s)]
        if len(stage_preds) >= 10:
            # Use spread of last-25% stages as uncertainty proxy
            tail = stage_preds[int(len(stage_preds) * 0.75):]
            tail_arr = np.array([p[0] for p in tail])
            spread = float(np.std(tail_arr))
            center = float(stage_preds[-1][0])
            return (center - 2 * spread, center + 2 * spread)
    except Exception:
        pass

    # Fallback: ±alpha band on the central prediction
    X_s = scaler.transform(X_pred)
    center = float(gbr_model.predict(X_s)[0])
    return (center * (1 - alpha), center * (1 + alpha))


# ─────────────────────────────────────────────────────────────────────────────
# Key driver extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_key_drivers(model, feature_names: list[str]) -> list[str]:
    """Return top-3 human-readable feature drivers from a trained GBR/Ridge model."""
    try:
        if hasattr(model, "feature_importances_"):
            importances = model.feature_importances_
        elif hasattr(model, "coef_"):
            importances = np.abs(model.coef_)
        else:
            return []

        sorted_idx = np.argsort(importances)[::-1]
        drivers = []
        for idx in sorted_idx[:3]:
            feat = feature_names[idx]
            label = _FEATURE_LABELS.get(feat, feat.replace("_", " ").title())
            drivers.append(label)
        return drivers
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Per-route forecast
# ─────────────────────────────────────────────────────────────────────────────

def forecast_route(
    route_id: str,
    route_name: str,
    rate_df: pd.DataFrame,
    macro_data: dict,
    cache_ttl_hours: float = 6.0,
) -> Optional[RateForecast]:
    """Generate an ML forecast for one route.

    Args:
        route_id:        Unique route identifier (used for cache key).
        route_name:      Human-readable name shown in UI.
        rate_df:         DataFrame with at least columns ``date`` and
                         ``rate_usd_per_feu``.
        macro_data:      dict[series_id → DataFrame] from fred_feed.
        cache_ttl_hours: How long to re-use a cached forecast (default 6 h).

    Returns:
        RateForecast or None if data is insufficient.
    """
    global _FORECAST_CACHE

    # ── Cache check ───────────────────────────────────────────────────────────
    cache_key = route_id
    if cache_key in _FORECAST_CACHE:
        cached_fc, cached_at = _FORECAST_CACHE[cache_key]
        age_h = (datetime.datetime.utcnow() - cached_at).total_seconds() / 3600
        if age_h < cache_ttl_hours:
            return cached_fc

    try:
        # ── Prepare rate series ────────────────────────────────────────────────
        df = rate_df.copy()
        if "date" not in df.columns or "rate_usd_per_feu" not in df.columns:
            logger.debug(f"rate_forecaster: missing columns for {route_id}")
            return None

        df = df.sort_values("date").dropna(subset=["rate_usd_per_feu"])
        df = df[df["rate_usd_per_feu"] > 0]

        # Skip routes built entirely from fallback data
        if "source" in df.columns and (df["source"] == "fallback").all():
            return None

        rate_series = df["rate_usd_per_feu"].reset_index(drop=True)
        current_rate = float(rate_series.iloc[-1])

        if len(rate_series) < 30:
            logger.debug(f"rate_forecaster: insufficient history for {route_id} ({len(rate_series)} rows)")
            return None

        # ── Build current-state feature row for prediction ──────────────────
        X_now = _build_features(rate_series, macro_data)
        feature_names = list(X_now.columns)

        # ── 7-day model (Ridge) ───────────────────────────────────────────────
        forecast_7d = current_rate  # fallback
        try:
            X_7, y_7 = _build_training_dataset(rate_series, macro_data, horizon=7)
            model_7, scaler_7, _ = _train_forecast_model(X_7, y_7, horizon=7)
            X_now_7 = X_now[feature_names]
            forecast_7d = float(model_7.predict(scaler_7.transform(X_now_7))[0])
        except Exception as e:
            logger.debug(f"rate_forecaster: 7d model failed for {route_id}: {e}")

        # ── 30-day model (GBR) ───────────────────────────────────────────────
        forecast_30d = current_rate  # fallback
        ci_low = current_rate * 0.85
        ci_high = current_rate * 1.15
        model_r2 = 0.0
        gbr_model = None
        gbr_scaler = None

        try:
            X_30, y_30 = _build_training_dataset(rate_series, macro_data, horizon=30)
            gbr_model, gbr_scaler, model_r2 = _train_forecast_model(X_30, y_30, horizon=30)
            X_now_30 = X_now[feature_names]
            forecast_30d = float(gbr_model.predict(gbr_scaler.transform(X_now_30))[0])
            ci_low, ci_high = _gbr_confidence_interval(gbr_model, gbr_scaler, X_now_30)
        except Exception as e:
            logger.debug(f"rate_forecaster: 30d model failed for {route_id}: {e}")

        # ── 90-day model (GBR, larger horizon) ───────────────────────────────
        forecast_90d = current_rate  # fallback
        try:
            X_90, y_90 = _build_training_dataset(rate_series, macro_data, horizon=90)
            model_90, scaler_90, _ = _train_forecast_model(X_90, y_90, horizon=30)
            X_now_90 = X_now[feature_names]
            forecast_90d = float(model_90.predict(scaler_90.transform(X_now_90))[0])
        except Exception as e:
            logger.debug(f"rate_forecaster: 90d model failed for {route_id}: {e}")

        # ── Cap forecasts to reasonable bounds ────────────────────────────────
        lo, hi = current_rate * 0.30, current_rate * 3.0
        forecast_7d  = float(np.clip(forecast_7d,  lo, hi))
        forecast_30d = float(np.clip(forecast_30d, lo, hi))
        forecast_90d = float(np.clip(forecast_90d, lo, hi))
        ci_low  = float(max(0.0, ci_low))
        ci_high = float(np.clip(ci_high, lo, hi * 1.2))

        # ── Direction + confidence ────────────────────────────────────────────
        pct_30 = (forecast_30d - current_rate) / current_rate if current_rate > 0 else 0.0
        if pct_30 > 0.03:
            direction = "Rising"
        elif pct_30 < -0.03:
            direction = "Falling"
        else:
            direction = "Stable"

        # Direction confidence: higher R², stronger signal → more confident
        r2_contribution   = max(0.0, model_r2)
        signal_strength   = min(abs(pct_30) / 0.10, 1.0)   # saturates at ±10%
        direction_confidence = float(np.clip(
            0.5 * r2_contribution + 0.5 * signal_strength, 0.0, 1.0
        ))

        # ── Key drivers ───────────────────────────────────────────────────────
        key_drivers: list[str] = []
        if gbr_model is not None:
            key_drivers = _extract_key_drivers(gbr_model, feature_names)

        fc = RateForecast(
            route_id=route_id,
            route_name=route_name,
            current_rate=current_rate,
            forecast_7d=forecast_7d,
            forecast_30d=forecast_30d,
            forecast_90d=forecast_90d,
            confidence_interval_30d=(ci_low, ci_high),
            direction=direction,
            direction_confidence=direction_confidence,
            key_drivers=key_drivers,
            model_r2=float(np.clip(model_r2, 0.0, 1.0)),
            last_updated=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )

        _FORECAST_CACHE[cache_key] = (fc, datetime.datetime.utcnow())
        return fc

    except Exception as exc:
        logger.error(f"rate_forecaster: forecast_route failed for {route_id}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Batch forecast
# ─────────────────────────────────────────────────────────────────────────────

def forecast_all_routes(
    freight_data: dict,
    macro_data: dict,
) -> dict[str, RateForecast]:
    """Forecast all routes in `freight_data`.

    Args:
        freight_data: dict[route_id → DataFrame] as returned by freight_scraper.
        macro_data:   dict[series_id → DataFrame] from fred_feed.

    Returns:
        dict[route_id → RateForecast]  — only routes with sufficient history.
    """
    from routes.route_registry import ROUTES

    results: dict[str, RateForecast] = {}
    route_map = {r.id: r.name for r in ROUTES}

    for route_id, rate_df in freight_data.items():
        if rate_df is None or rate_df.empty:
            continue

        route_name = route_map.get(route_id, route_id.replace("_", " ").title())
        fc = forecast_route(
            route_id=route_id,
            route_name=route_name,
            rate_df=rate_df,
            macro_data=macro_data,
        )
        if fc is not None:
            results[route_id] = fc

    logger.info(f"rate_forecaster: {len(results)} ML forecasts generated")
    return results
