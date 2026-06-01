from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from data.cache_manager import CacheManager
from data.normalizer import normalize_freight_df
from data.quality import DataSeries, DataSource
from routes.route_registry import ROUTES, get_all_fbx_indices
from utils.helpers import stable_hash


# FBX index metadata (used for scraping and labeling)
FBX_INDICES = {
    "FBX01":  {"name": "Trans-Pacific Eastbound",        "route_id": "transpacific_eb"},
    "FBX02":  {"name": "Trans-Pacific Westbound",        "route_id": "transpacific_wb"},
    "FBX03":  {"name": "Asia-Europe",                    "route_id": "asia_europe"},
    "FBX04":  {"name": "Europe-Asia",                    "route_id": "med_hub_to_asia"},
    "FBX11":  {"name": "Transatlantic EB",               "route_id": "transatlantic"},
    "FBX12":  {"name": "Transatlantic WB",               "route_id": "transatlantic"},
    "FBX21":  {"name": "Asia-S. America West Coast",     "route_id": "china_south_america"},
    "FBX22":  {"name": "S. America West Coast-Asia",     "route_id": "china_south_america"},
    "FBX31":  {"name": "Europe-S. America East Coast",   "route_id": "europe_south_america"},
    "FBX32":  {"name": "S. America East Coast-Europe",   "route_id": "europe_south_america"},
    "FBXGLO": {"name": "Global Container Index",         "route_id": "global"},
}

# Public Freightos data endpoints (these are publicly accessible)
_FBX_API_URL = "https://fbx.freightos.com/api/v1/indices"
_FBX_PAGE_URL = "https://fbx.freightos.com/"


@st.cache_data(ttl=86400, hash_funcs={CacheManager: lambda _: None})
def fetch_fbx_rates(
    lookback_days: int = 120,
    cache: CacheManager | None = None,
    ttl_hours: float = 24.0,
) -> dict[str, pd.DataFrame]:
    """Fetch Freightos Baltic Index rates for all tracked routes.

    Attempts multiple strategies:
    1. FBX API (public JSON endpoint)
    2. FBX page scrape (HTML/embedded JSON)
    3. Synthetic flat-rate fallback (neutral signal)

    Returns:
        dict mapping route_id → normalized DataFrame with FREIGHT_COLS columns.
    """
    cache = cache or CacheManager()
    results: dict[str, pd.DataFrame] = {}

    key = f"fbx_all_{lookback_days}d"
    raw_df = cache.get_or_fetch(
        key=key,
        fetch_fn=lambda lb=lookback_days: _fetch_fbx_all(lb),
        ttl_hours=ttl_hours,
        source="freight",
    )

    if raw_df is None or raw_df.empty:
        logger.warning("FBX fetch failed; using synthetic fallback rates")
        return _synthetic_fallback()

    for route in ROUTES:
        route_df = raw_df[raw_df["route_id"] == route.id]
        if not route_df.empty:
            results[route.id] = route_df
        else:
            logger.debug(f"No FBX data for {route.id}; using fallback")
            results[route.id] = _single_fallback(route.id, route.fbx_index)

    return results


# ── DataSeries variant (Phase 1 rollout) ─────────────────────────────────────

def fetch_fbx_rates_wrapped(
    lookback_days: int = 120,
    cache: CacheManager | None = None,
    ttl_hours: float = 24.0,
):
    """Wrapped-variant of ``fetch_fbx_rates`` returning a ``DataSeries``.

    The FBX scraper is fragile — we label it ``scraped`` + ``unofficial`` so
    the UI surfaces the caveat. When the fallback kicks in, we return a DEMO
    ``DataSource`` so charts don't silently plot synthetic rates.
    """
    from data.quality import DataSeries, DataSource, DataKind, DataQuality

    # We call the underlying function once and inspect whether the cache path
    # fell back to ``_synthetic_fallback`` by checking for synthetic markers.
    data = fetch_fbx_rates(
        lookback_days=lookback_days, cache=cache, ttl_hours=ttl_hours
    )
    is_synthetic = _looks_synthetic(data)

    if not data:
        source = DataSource.demo("FBX — no data")
    elif is_synthetic:
        source = DataSource(
            name="FBX (synthetic fallback)",
            kind=DataKind.DEMO,
            quality=DataQuality.DEMO,
            url="https://fbx.freightos.com",
            notes="All strategies failed; returning synthetic flat-rate baseline",
        )
    else:
        source = DataSource(
            name="Freightos Baltic Index",
            kind=DataKind.SCRAPED,
            url="https://fbx.freightos.com",
            quality=DataQuality.UNOFFICIAL,
            sla_hours=ttl_hours,
            notes=f"{len(data)} routes, {lookback_days}d lookback",
        )
    return DataSeries(data=data, source=source,
                      meta={"lookback_days": lookback_days})


def _looks_synthetic(data: dict) -> bool:
    """Return True if the payload looks like the ``_synthetic_fallback`` output.

    The synthetic fallback in this module emits DataFrames with a ``source``
    column set to ``"synthetic"``. We detect that marker.
    """
    if not data:
        return False
    for df in data.values():
        if df is None or df.empty:
            continue
        if "source" in df.columns:
            values = df["source"].dropna().astype(str).str.lower().unique()
            if any(v in {"synthetic", "mock", "fallback"} for v in values):
                return True
    return False


def _fetch_fbx_all(lookback_days: int) -> pd.DataFrame:
    """Try multiple strategies to get FBX data."""
    # Strategy 1: Try the public API
    df = _try_fbx_api(lookback_days)
    if df is not None and not df.empty:
        logger.info(f"FBX: loaded {len(df)} rate records from API")
        return df

    # Strategy 2: Scrape the page for embedded JSON
    df = _try_fbx_scrape()
    if df is not None and not df.empty:
        logger.info(f"FBX: loaded {len(df)} rate records from scrape")
        return df

    logger.warning("All FBX fetch strategies failed")
    return pd.DataFrame()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=10))
def _try_fbx_api(lookback_days: int) -> pd.DataFrame | None:
    """Attempt to fetch from Freightos public API endpoint."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ShipTracker/1.0)",
            "Accept": "application/json",
        }
        resp = requests.get(_FBX_API_URL, headers=headers, timeout=20)

        if resp.status_code != 200:
            return None

        data = resp.json()
        return _parse_fbx_json(data, lookback_days)

    except Exception as exc:
        logger.debug(f"FBX API attempt failed: {exc}")
        return None


def _try_fbx_scrape() -> pd.DataFrame | None:
    """Scrape FBX page for embedded rate data."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ShipTracker/1.0)"}
        resp = requests.get(_FBX_PAGE_URL, headers=headers, timeout=20)

        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Look for JSON data embedded in script tags
        for script in soup.find_all("script"):
            text = script.string or ""
            if "FBX" in text and ("rate" in text.lower() or "index" in text.lower()):
                # Try to extract JSON objects
                json_matches = re.findall(r'\{[^{}]{100,}\}', text)
                for match in json_matches:
                    try:
                        import json
                        obj = json.loads(match)
                        df = _parse_fbx_json(obj, 120)
                        if df is not None and not df.empty:
                            return df
                    except Exception:
                        continue

        return None

    except Exception as exc:
        logger.debug(f"FBX scrape failed: {exc}")
        return None


def _parse_fbx_json(data: dict | list, lookback_days: int) -> pd.DataFrame | None:
    """Parse various FBX JSON response formats into a unified DataFrame."""
    rows = []
    cutoff = datetime.now() - timedelta(days=lookback_days)

    # Handle list of index objects
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                rows.extend(_extract_fbx_rows(item, cutoff))
    elif isinstance(data, dict):
        # Handle nested structures
        for key in ["indices", "data", "rates", "results"]:
            if key in data:
                sub = data[key]
                if isinstance(sub, list):
                    for item in sub:
                        rows.extend(_extract_fbx_rows(item, cutoff))
                break
        else:
            rows.extend(_extract_fbx_rows(data, cutoff))

    if not rows:
        return None

    df = pd.DataFrame(rows)
    return df


def _extract_fbx_rows(item: dict, cutoff: datetime) -> list[dict]:
    """Extract rate rows from a single FBX JSON object."""
    rows = []
    index_code = item.get("index", item.get("code", item.get("id", "")))

    if index_code not in FBX_INDICES:
        return rows

    meta = FBX_INDICES[index_code]
    route_id = meta["route_id"]

    # Handle different date/rate field names
    date_str = item.get("date", item.get("timestamp", item.get("period", "")))
    rate = item.get("rate", item.get("value", item.get("price", item.get("close", 0))))

    try:
        date = pd.to_datetime(date_str)
        if date < cutoff:
            return rows
        rows.append({
            "date": date,
            "route_id": route_id,
            "rate_usd_per_feu": float(rate),
            "index_name": index_code,
            "source": "freightos_fbx",
        })
    except Exception:
        pass

    # Also check for series/history arrays
    for hist_key in ["history", "series", "data", "values"]:
        if hist_key in item and isinstance(item[hist_key], list):
            for point in item[hist_key]:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    try:
                        date = pd.to_datetime(point[0])
                        if date >= cutoff:
                            rows.append({
                                "date": date,
                                "route_id": route_id,
                                "rate_usd_per_feu": float(point[1]),
                                "index_name": index_code,
                                "source": "freightos_fbx",
                            })
                    except Exception:
                        continue

    return rows


# Rough long-term average rate per FBX index (USD/FEU). These anchor the
# synthetic baseline; everything else is modeled noise around them.
_DEFAULT_RATES: dict[str, float] = {
    "FBX01":  2500.0,  # Trans-Pacific Eastbound
    "FBX02":  800.0,   # Trans-Pacific Westbound
    "FBX03":  1800.0,  # Asia-Europe
    "FBX04":  1600.0,  # Europe-Asia
    "FBX11":  1200.0,  # Transatlantic EB
    "FBX12":  1100.0,  # Transatlantic WB
    "FBX21":  2200.0,  # Asia-S. America West Coast
    "FBX22":  900.0,   # S. America West Coast-Asia
    "FBX31":  2400.0,  # Europe-S. America East Coast
    "FBX32":  1000.0,  # S. America East Coast-Europe
    "FBXGLO": 1700.0,  # Global Container Index
}
_DEFAULT_RATE_FALLBACK = 1500.0

# Synthetic-series length and shape parameters. The fallback is a
# mean-reverting random walk (Ornstein-Uhlenbeck-style) so forecasters have
# real history to fit, with a seasonal sinusoid layered on top.
_SYNTH_HISTORY_DAYS = 120     # rows of daily history per route
_SYNTH_REVERSION = 0.06       # pull-back strength toward the baseline / day
_SYNTH_VOL_FRAC = 0.018       # daily shock size as a fraction of the baseline
_SYNTH_SEASONAL_AMP = 0.12    # peak/off-peak swing as a fraction of baseline


def _synthetic_fallback() -> dict[str, pd.DataFrame]:
    """Generate synthetic fallback data when all real fetches fail.

    Each route gets a full ``_SYNTH_HISTORY_DAYS``-row daily time series — a
    mean-reverting random walk around the route's long-term baseline with a
    seasonal peak/off-peak modulation — so downstream forecasters
    (``forecaster.py`` / ``rate_forecaster.py`` / ``monte_carlo.py``) have
    genuine history to fit instead of a single point-in-time rate.

    The returned dict keys, DataFrame columns and row ordering match the real
    scrape path: every frame is run through ``normalize_freight_df`` and so
    carries the canonical ``FREIGHT_COLS`` schema.
    """
    results: dict[str, pd.DataFrame] = {}
    for route in ROUTES:
        results[route.id] = _synthetic_series(route.id, route.fbx_index)
    return results


def _synthetic_series(route_id: str, fbx_index: str) -> pd.DataFrame:
    """Build a ~120-day mean-reverting synthetic rate series for one route.

    The walk is seeded deterministically from ``route_id`` so the same route
    yields the same history across processes (the platform relies on this
    stability for caching and UI consistency). Schema matches the real scrape
    path.
    """
    import numpy as np

    baseline = _DEFAULT_RATES.get(fbx_index, _DEFAULT_RATE_FALLBACK)

    # Deterministic per-route RNG — stable across processes.
    rng = np.random.default_rng(stable_hash(route_id))

    n = _SYNTH_HISTORY_DAYS
    end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    dates = [end - timedelta(days=n - 1 - i) for i in range(n)]

    # Seasonal modulation: a full-year sinusoid keyed off day-of-year, so
    # peak season reads high and off-peak reads low regardless of where the
    # 120-day window lands. ``day_of_year / 365`` -> phase in [0, 1].
    day_of_year = np.array([d.timetuple().tm_yday for d in dates], dtype=float)
    seasonal = _SYNTH_SEASONAL_AMP * np.sin(2.0 * np.pi * day_of_year / 365.0)

    # Mean-reverting random walk (discrete Ornstein-Uhlenbeck) expressed as a
    # multiplicative deviation around the seasonally-modulated baseline.
    shock_sd = _SYNTH_VOL_FRAC
    deviation = 0.0
    rates = np.empty(n, dtype=float)
    for i in range(n):
        # Pull the deviation back toward zero, then apply a Gaussian shock.
        deviation += -_SYNTH_REVERSION * deviation + rng.normal(0.0, shock_sd)
        target = baseline * (1.0 + seasonal[i])
        rate = target * (1.0 + deviation)
        # Floor at a small positive value so normalize_freight_df keeps the row.
        rates[i] = max(50.0, rate)

    df = pd.DataFrame({
        "date": dates,
        "route_id": route_id,
        "rate_usd_per_feu": np.round(rates, 2),
        "index_name": fbx_index,
        "source": "synthetic",
    })
    return normalize_freight_df(df, route_id=route_id, index_name=fbx_index)


def _single_fallback(route_id: str, fbx_index: str) -> pd.DataFrame:
    """Single-route fallback used to fill a gap when the real scrape returns
    no rows for a specific route.

    Delegates to :func:`_synthetic_series` so a gap-filled route still gets a
    full time series rather than a lone point-in-time rate — forecasters need
    history. The ``source`` column stays ``"synthetic"`` so provenance
    detection (``_looks_synthetic``) and DEMO labelling behave correctly.
    """
    return _synthetic_series(route_id, fbx_index)


def get_current_rate(route_id: str, freight_data: dict[str, pd.DataFrame]) -> float:
    """Return most recent rate for a route in USD/FEU."""
    df = freight_data.get(route_id)
    if df is None or df.empty:
        return 0.0
    return float(df["rate_usd_per_feu"].iloc[-1])


def get_rate_trend(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
    lookback_days: int = 90,
) -> tuple[float, str]:
    """Return (pct_change_30d, trend_label) for a route.

    trend_label is one of: "Rising", "Stable", "Falling"
    """
    from utils.helpers import trend_label

    df = freight_data.get(route_id)
    if df is None or len(df) < 2:
        return 0.0, "Stable"

    df = df.sort_values("date")
    recent = df.tail(31)

    if len(recent) < 2:
        return 0.0, "Stable"

    start_rate = recent["rate_usd_per_feu"].iloc[0]
    end_rate = recent["rate_usd_per_feu"].iloc[-1]

    if start_rate == 0:
        return 0.0, "Stable"

    pct = (end_rate - start_rate) / start_rate
    label = trend_label(pct)
    return pct, label


# ── Baltic Exchange daily feed ───────────────────────────────────────────────

_BALTIC_FAMILIES: tuple[str, ...] = ("BDI", "BCI", "BPI", "BSI", "BHSI")
_BALTIC_URL = "https://www.balticexchange.com/en/data-services/market-information0/indices.html"
_BALTIC_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "baltic_backfill.csv"


def _load_baltic_fixture() -> pd.DataFrame:
    """Load the static Baltic backfill CSV shipped with the repo."""
    df = pd.read_csv(_BALTIC_FIXTURE_PATH)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def _try_fetch_baltic_csv(url: str, timeout: float = 10.0) -> pd.DataFrame | None:
    """Attempt a live HTTP fetch of the Baltic Exchange daily CSV.

    The real Baltic CSV sits behind a paid/login gate, so this will almost
    always fall through. We keep the request path so a future free endpoint
    can be swapped in without refactoring the caller.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ShipTracker/1.0)",
            "Accept": "text/csv,application/json",
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200 or not resp.text.strip():
            return None
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        if "date" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        # Require all five family columns
        for fam in _BALTIC_FAMILIES:
            if fam not in df.columns:
                return None
        return df
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        logger.debug("Baltic live fetch failed: {}", exc)
        return None


@st.cache_data(ttl=18 * 3600, hash_funcs={CacheManager: lambda _: None}, show_spinner=False)
def _cached_baltic_frame(force_fixture: bool) -> tuple[pd.DataFrame, bool]:
    """Return (frame, is_fixture). Cached for 18h.

    ``is_fixture`` is True when we fell back to the bundled backfill CSV.
    """
    if not force_fixture:
        live = _try_fetch_baltic_csv(_BALTIC_URL)
        if live is not None and not live.empty:
            logger.info("Baltic live fetch succeeded: {} rows", len(live))
            return live, False

    logger.debug("Baltic: falling back to fixture CSV")
    return _load_baltic_fixture(), True


def fetch_baltic_daily(family: str = "BDI", *, force_fixture: bool = False) -> DataSeries:
    """Fetch the Baltic Exchange daily index for the given family.

    Family ∈ {"BDI","BCI","BPI","BSI","BHSI"}. Returns a DataSeries with the
    series indexed by date. On HTTP error or force_fixture=True, falls back to
    ``data/fixtures/baltic_backfill.csv`` with ``quality="stale"``.
    """
    if family not in _BALTIC_FAMILIES:
        raise ValueError(
            f"Unknown Baltic family {family!r}; expected one of {_BALTIC_FAMILIES}"
        )

    frame, is_fixture = _cached_baltic_frame(force_fixture)
    series = frame[family].astype(float).rename(family)
    last_ts = series.index.max()
    if pd.notna(last_ts):
        as_of = pd.Timestamp(last_ts).to_pydatetime().replace(tzinfo=timezone.utc)
    else:
        as_of = datetime.now(timezone.utc)

    if is_fixture:
        # Hand-rolled: DataSource doesn't ship a .stale() classmethod but the
        # string constants are stable, so constructing directly is idiomatic.
        source = DataSource(
            name="Baltic Exchange (fixture)",
            kind="cached",
            url=_BALTIC_URL,
            as_of=as_of,
            quality="stale",
            notes="HTTP unreachable; using cached backfill",
        )
    else:
        source = DataSource.scraped(
            name="Baltic Exchange",
            url=_BALTIC_URL,
            notes=f"{len(series)} rows, last {as_of:%Y-%m-%d}",
        )

    return DataSeries(
        data=series,
        source=source,
        meta={"family": family, "is_fixture": is_fixture},
    )
