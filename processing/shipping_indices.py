"""processing/shipping_indices.py — Global shipping index tracking and comparison.

Tracks and contextualizes key global shipping indices. BDI and PPI are pulled
from FRED via macro_data; FBX container rate indices are pulled from freight_data.
All other indices use synthetic proxies derived from these sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger


# ── Index metadata registry ───────────────────────────────────────────────────

INDEX_METADATA: dict[str, dict] = {
    "BDI": {
        "name": "Baltic Dry Index",
        "full_name": "Baltic Dry Index (Capesize/Panamax/Supramax)",
        "description": (
            "Benchmark for dry bulk shipping rates. "
            "Leading indicator for global trade volumes."
        ),
        "source": "FRED (BSXRLM)",
        "fred_series": "BSXRLM",
    },
    "FBX_GLOBAL": {
        "name": "Freightos Global",
        "full_name": "Freightos Baltic Index Global (FBX)",
        "description": "Composite container freight rate across all major lanes.",
        "source": "Freightos FBX (scraped)",
        "fred_series": None,
    },
    "FBX01": {
        "name": "Trans-Pacific EB",
        "full_name": "FBX01 Trans-Pacific Eastbound (Asia to US West Coast)",
        "description": (
            "Key benchmark for Asia to US container rates. "
            "Most-watched lane by US importers."
        ),
        "source": "Freightos FBX",
        "fred_series": None,
    },
    "FBX03": {
        "name": "Asia-Europe",
        "full_name": "FBX03 Asia-Europe (Shanghai to Rotterdam)",
        "description": (
            "Main Asia-Europe container rate. "
            "Highly sensitive to Suez Canal disruptions."
        ),
        "source": "Freightos FBX",
        "fred_series": None,
    },
    "FBX11": {
        "name": "Transatlantic EB",
        "full_name": "FBX11 Transatlantic Eastbound",
        "description": (
            "North Atlantic container rates. "
            "Reflects US export competitiveness."
        ),
        "source": "Freightos FBX",
        "fred_series": None,
    },
    "PPIACO": {
        "name": "PPI All Commodities",
        "full_name": "Producer Price Index — All Commodities",
        "description": (
            "Broad input cost indicator for manufacturing and shipping demand."
        ),
        "source": "FRED",
        "fred_series": "PPIACO",
    },
}

# Map FBX index codes to freight_data route_id keys
_FBX_ROUTE_MAP: dict[str, list[str]] = {
    "FBX_GLOBAL": ["global", "transpacific_eb", "asia_europe", "transatlantic"],
    "FBX01": ["transpacific_eb"],
    "FBX03": ["asia_europe"],
    "FBX11": ["transatlantic"],
}


# ── ShippingIndex dataclass ───────────────────────────────────────────────────

@dataclass
class ShippingIndex:
    index_id: str
    name: str
    full_name: str
    description: str
    current_value: float
    change_1d: float       # percent
    change_7d: float       # percent
    change_30d: float      # percent
    change_ytd: float      # percent
    yoy_52w_high: float
    yoy_52w_low: float
    pct_from_52w_high: float  # negative means below high
    trend: str             # "BULL" | "BEAR" | "SIDEWAYS"
    source: str
    last_updated: str


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pct_change(current: float, ref: float) -> float:
    """Safe percent change from ref to current."""
    if ref == 0:
        return 0.0
    return (current - ref) / abs(ref) * 100.0


def _classify_trend(change_30d: float) -> str:
    if change_30d > 5.0:
        return "BULL"
    if change_30d < -5.0:
        return "BEAR"
    return "SIDEWAYS"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_series_from_macro(macro_data: dict, series_key: str) -> pd.Series | None:
    """Pull a sorted, cleaned value Series from macro_data dict entry."""
    df = macro_data.get(series_key)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    df = df.copy()
    if "date" in df.columns:
        df = df.sort_values("date")
    if "value" not in df.columns:
        return None
    s = df["value"].dropna()
    if s.empty:
        return None
    s.index = pd.to_datetime(df.loc[s.index, "date"]) if "date" in df.columns else s.index
    return s


def _build_fred_index(
    index_id: str,
    meta: dict,
    series: pd.Series,
) -> ShippingIndex:
    """Construct a ShippingIndex from a macro FRED time series."""
    now = series.index.max()
    current = float(series.iloc[-1])

    # 1-day change
    val_1d = float(series.iloc[-2]) if len(series) >= 2 else current
    change_1d = _pct_change(current, val_1d)

    # 7-day change (use 7 obs back, FRED is often daily/weekly)
    val_7d = float(series.iloc[-8]) if len(series) >= 8 else float(series.iloc[0])
    change_7d = _pct_change(current, val_7d)

    # 30-day change
    val_30d = float(series.iloc[-31]) if len(series) >= 31 else float(series.iloc[0])
    change_30d = _pct_change(current, val_30d)

    # YTD change — find first observation in current year
    try:
        year_start = pd.Timestamp(now.year, 1, 1)
        ytd_mask = series.index >= year_start
        ytd_series = series[ytd_mask]
        val_ytd = float(ytd_series.iloc[0]) if not ytd_series.empty else current
    except Exception:
        val_ytd = current
    change_ytd = _pct_change(current, val_ytd)

    # 52-week high/low
    cutoff_52w = now - pd.Timedelta(weeks=52)
    mask_52w = series.index >= cutoff_52w
    s52 = series[mask_52w] if mask_52w.any() else series
    high_52w = float(s52.max())
    low_52w = float(s52.min())
    pct_from_high = _pct_change(current, high_52w)

    return ShippingIndex(
        index_id=index_id,
        name=meta["name"],
        full_name=meta["full_name"],
        description=meta["description"],
        current_value=current,
        change_1d=change_1d,
        change_7d=change_7d,
        change_30d=change_30d,
        change_ytd=change_ytd,
        yoy_52w_high=high_52w,
        yoy_52w_low=low_52w,
        pct_from_52w_high=pct_from_high,
        trend=_classify_trend(change_30d),
        source=meta["source"],
        last_updated=_now_iso(),
    )


def _build_fbx_index(
    index_id: str,
    meta: dict,
    freight_data: dict,
) -> ShippingIndex | None:
    """Construct a ShippingIndex from freight_data route DataFrames."""
    route_ids = _FBX_ROUTE_MAP.get(index_id, [])

    # Collect rates from all matching routes
    all_series: list[pd.Series] = []
    for route_id in route_ids:
        df = freight_data.get(route_id)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            continue
        if "rate_usd_per_feu" not in df.columns:
            continue
        df = df.copy().sort_values("date") if "date" in df.columns else df.copy()
        rates = df["rate_usd_per_feu"].dropna()
        if rates.empty:
            continue
        dates = pd.to_datetime(df.loc[rates.index, "date"]) if "date" in df.columns else rates.index
        s = pd.Series(rates.values, index=dates)
        all_series.append(s)

    if not all_series:
        return None

    # For FBX_GLOBAL: average across routes; for specific lanes: use directly
    if len(all_series) == 1:
        combined = all_series[0]
    else:
        # Align by date and average
        merged = pd.concat(all_series, axis=1)
        merged = merged.sort_index()
        combined = merged.mean(axis=1).dropna()

    if combined.empty:
        return None

    combined = combined.sort_index()
    now = combined.index.max()
    current = float(combined.iloc[-1])

    val_1d = float(combined.iloc[-2]) if len(combined) >= 2 else current
    change_1d = _pct_change(current, val_1d)

    val_7d = float(combined.iloc[-8]) if len(combined) >= 8 else float(combined.iloc[0])
    change_7d = _pct_change(current, val_7d)

    val_30d = float(combined.iloc[-31]) if len(combined) >= 31 else float(combined.iloc[0])
    change_30d = _pct_change(current, val_30d)

    try:
        year_start = pd.Timestamp(now.year, 1, 1)
        ytd_mask = combined.index >= year_start
        ytd_s = combined[ytd_mask]
        val_ytd = float(ytd_s.iloc[0]) if not ytd_s.empty else current
    except Exception:
        val_ytd = current
    change_ytd = _pct_change(current, val_ytd)

    cutoff_52w = now - pd.Timedelta(weeks=52)
    mask_52w = combined.index >= cutoff_52w
    s52 = combined[mask_52w] if mask_52w.any() else combined
    high_52w = float(s52.max())
    low_52w = float(s52.min())
    pct_from_high = _pct_change(current, high_52w)

    return ShippingIndex(
        index_id=index_id,
        name=meta["name"],
        full_name=meta["full_name"],
        description=meta["description"],
        current_value=current,
        change_1d=change_1d,
        change_7d=change_7d,
        change_30d=change_30d,
        change_ytd=change_ytd,
        yoy_52w_high=high_52w,
        yoy_52w_low=low_52w,
        pct_from_52w_high=pct_from_high,
        trend=_classify_trend(change_30d),
        source=meta["source"],
        last_updated=_now_iso(),
    )


def _placeholder_index(index_id: str, meta: dict) -> ShippingIndex:
    """Return a zero-value placeholder when no real data is available."""
    return ShippingIndex(
        index_id=index_id,
        name=meta["name"],
        full_name=meta["full_name"],
        description=meta["description"],
        current_value=0.0,
        change_1d=0.0,
        change_7d=0.0,
        change_30d=0.0,
        change_ytd=0.0,
        yoy_52w_high=0.0,
        yoy_52w_low=0.0,
        pct_from_52w_high=0.0,
        trend="SIDEWAYS",
        source=meta["source"],
        last_updated=_now_iso(),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def build_indices(
    macro_data: dict,
    freight_data: dict,
) -> list[ShippingIndex]:
    """Build ShippingIndex objects for all tracked indices.

    Args:
        macro_data: dict mapping FRED series_id -> normalized DataFrame
                    (columns: date, series_id, series_name, value, source).
        freight_data: dict mapping route_id -> normalized DataFrame
                      (columns: date, route_id, rate_usd_per_feu, index_name, source).

    Returns:
        List of ShippingIndex dataclasses in INDEX_METADATA order.
    """
    results: list[ShippingIndex] = []

    for index_id, meta in INDEX_METADATA.items():
        try:
            fred_key = meta.get("fred_series")

            if fred_key is not None:
                # FRED-backed index (BDI, PPIACO)
                series = _extract_series_from_macro(macro_data, fred_key)
                if series is not None and not series.empty:
                    idx = _build_fred_index(index_id, meta, series)
                else:
                    logger.debug(
                        "No FRED data for %s (%s) — using placeholder",
                        index_id,
                        fred_key,
                    )
                    idx = _placeholder_index(index_id, meta)
            else:
                # FBX-backed index (container freight rates)
                idx = _build_fbx_index(index_id, meta, freight_data)
                if idx is None:
                    logger.debug(
                        "No freight data for %s — using placeholder", index_id
                    )
                    idx = _placeholder_index(index_id, meta)

            results.append(idx)

        except Exception as exc:
            logger.warning("Failed to build index %s: %s", index_id, exc)
            results.append(_placeholder_index(index_id, meta))

    return results


def _get_index_time_series(
    index_id: str,
    macro_data: dict,
    freight_data: dict,
) -> pd.Series | None:
    """Return a date-indexed float Series for a given index_id."""
    meta = INDEX_METADATA.get(index_id)
    if meta is None:
        return None

    fred_key = meta.get("fred_series")
    if fred_key is not None:
        return _extract_series_from_macro(macro_data, fred_key)

    # FBX: aggregate routes
    route_ids = _FBX_ROUTE_MAP.get(index_id, [])
    all_series: list[pd.Series] = []
    for route_id in route_ids:
        df = freight_data.get(route_id)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            continue
        if "rate_usd_per_feu" not in df.columns:
            continue
        df = df.copy().sort_values("date") if "date" in df.columns else df.copy()
        rates = df["rate_usd_per_feu"].dropna()
        if rates.empty:
            continue
        dates = pd.to_datetime(df.loc[rates.index, "date"]) if "date" in df.columns else rates.index
        all_series.append(pd.Series(rates.values, index=dates))

    if not all_series:
        return None
    if len(all_series) == 1:
        return all_series[0].sort_index()
    merged = pd.concat(all_series, axis=1).mean(axis=1).dropna()
    return merged.sort_index()


def get_index_correlation_matrix(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
) -> pd.DataFrame:
    """Compute a pairwise correlation matrix of all index time series.

    Aligns series on common dates (inner join) before computing correlations.

    Args:
        indices: list of ShippingIndex objects (from build_indices).
        macro_data: FRED macro data dict.
        freight_data: freight data dict.

    Returns:
        Symmetric DataFrame of Pearson correlations (index = columns = index_id).
        Returns empty DataFrame if fewer than 2 series have data.
    """
    series_dict: dict[str, pd.Series] = {}
    for idx in indices:
        s = _get_index_time_series(idx.index_id, macro_data, freight_data)
        if s is not None and len(s) >= 5:
            series_dict[idx.index_id] = s

    if len(series_dict) < 2:
        logger.debug("Fewer than 2 index series available for correlation matrix")
        return pd.DataFrame()

    # Align on common dates
    combined = pd.DataFrame(series_dict)
    combined = combined.dropna(how="all")

    # Use numpy corrcoef for robustness
    valid_cols = [c for c in combined.columns if combined[c].notna().sum() >= 5]
    if len(valid_cols) < 2:
        return pd.DataFrame()

    sub = combined[valid_cols].dropna()
    if sub.shape[0] < 5:
        # If inner join gives too few rows, use pairwise
        corr_matrix = combined[valid_cols].corr(method="pearson")
    else:
        arr = sub.values.T  # shape: (n_indices, n_dates)
        corr_arr = np.corrcoef(arr)
        corr_matrix = pd.DataFrame(corr_arr, index=valid_cols, columns=valid_cols)

    logger.debug(
        "Correlation matrix built: %d x %d from %d date points",
        corr_matrix.shape[0],
        corr_matrix.shape[1],
        len(sub) if sub is not None else 0,
    )
    return corr_matrix
