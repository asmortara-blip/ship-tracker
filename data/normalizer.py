from __future__ import annotations

import pandas as pd
from loguru import logger


# ------------------------------------------------------------------
# Column name constants (single source of truth)
# ------------------------------------------------------------------

TRADE_COLS = ["date", "port_locode", "country_iso3", "hs_code", "flow",
              "value_usd", "net_weight_kg", "source"]

FREIGHT_COLS = ["date", "route_id", "rate_usd_per_feu", "index_name", "source"]

AIS_COLS = ["date", "port_locode", "vessel_count", "vessel_type", "source"]

MACRO_COLS = ["date", "series_id", "series_name", "value", "source"]

STOCK_COLS = ["date", "symbol", "open", "high", "low", "close", "volume"]

THROUGHPUT_COLS = ["year", "port_locode", "country_iso3", "teu_millions",
                   "connectivity_index", "source"]


# ------------------------------------------------------------------
# Normalizers
# ------------------------------------------------------------------

def normalize_trade_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw UN Comtrade response to standard schema.

    Expected input columns (subset): period, reporterISO, cmdCode, flowCode,
    primaryValue, netWgt.

    Returns DataFrame with TRADE_COLS columns.
    """
    if df is None or df.empty:
        return _empty(TRADE_COLS)

    try:
        out = pd.DataFrame()

        # Date: Comtrade uses YYYYMM integer period
        if "period" in df.columns:
            out["date"] = pd.to_datetime(df["period"].astype(str), format="%Y%m", errors="coerce")
        elif "refPeriodId" in df.columns:
            out["date"] = pd.to_datetime(df["refPeriodId"].astype(str), format="%Y%m", errors="coerce")
        else:
            out["date"] = pd.NaT

        out["port_locode"] = df.get("port_locode", "UNKNOWN")
        out["country_iso3"] = df.get("reporterISO", df.get("reporterCode", "UNK"))
        out["hs_code"] = df.get("cmdCode", df.get("cmdDesc", "")).astype(str).str[:4]
        out["flow"] = df.get("flowCode", df.get("flowDesc", "")).str.upper().str[:1]  # "X" or "M"
        out["value_usd"] = pd.to_numeric(df.get("primaryValue", 0), errors="coerce").fillna(0)
        out["net_weight_kg"] = pd.to_numeric(df.get("netWgt", 0), errors="coerce").fillna(0)
        out["source"] = "comtrade"

        out = out.dropna(subset=["date"])
        out = out.sort_values("date").reset_index(drop=True)
        logger.debug(f"normalize_trade_df: {len(out)} rows")
        return out[TRADE_COLS]

    except Exception as exc:
        logger.error(f"normalize_trade_df failed: {exc}")
        return _empty(TRADE_COLS)


def normalize_freight_df(
    df: pd.DataFrame,
    route_id: str = "unknown",
    index_name: str = "FBX",
) -> pd.DataFrame:
    """Normalize scraped or fetched freight rate data to standard schema."""
    if df is None or df.empty:
        return _empty(FREIGHT_COLS)

    try:
        out = pd.DataFrame()
        out["date"] = pd.to_datetime(df.get("date", df.index), errors="coerce")
        out["route_id"] = df.get("route_id", route_id)
        out["rate_usd_per_feu"] = pd.to_numeric(
            df.get("rate_usd_per_feu", df.get("value", df.get("close", 0))),
            errors="coerce",
        ).fillna(0)
        out["index_name"] = df.get("index_name", index_name)
        out["source"] = df.get("source", "freight_scraper")

        out = out.dropna(subset=["date"])
        out = out[out["rate_usd_per_feu"] > 0]
        out = out.sort_values("date").reset_index(drop=True)
        logger.debug(f"normalize_freight_df [{route_id}]: {len(out)} rows")
        return out[FREIGHT_COLS]

    except Exception as exc:
        logger.error(f"normalize_freight_df failed: {exc}")
        return _empty(FREIGHT_COLS)


def normalize_ais_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize AISHub vessel position data to port congestion summary."""
    if df is None or df.empty:
        return _empty(AIS_COLS)

    try:
        out = pd.DataFrame()
        out["date"] = pd.to_datetime(df.get("date", df.get("timestamp", pd.Timestamp.now())), errors="coerce")
        out["port_locode"] = df.get("port_locode", "UNKNOWN")
        out["vessel_count"] = pd.to_numeric(df.get("vessel_count", df.get("count", 0)), errors="coerce").fillna(0)
        out["vessel_type"] = df.get("vessel_type", "cargo")
        out["source"] = "aishub"

        out = out.dropna(subset=["date"])
        out = out.sort_values("date").reset_index(drop=True)
        logger.debug(f"normalize_ais_df: {len(out)} rows")
        return out[AIS_COLS]

    except Exception as exc:
        logger.error(f"normalize_ais_df failed: {exc}")
        return _empty(AIS_COLS)


def normalize_macro_df(df: pd.DataFrame, series_id: str = "", series_name: str = "") -> pd.DataFrame:
    """Normalize FRED or World Bank macro time series to standard schema."""
    if df is None or df.empty:
        return _empty(MACRO_COLS)

    try:
        out = pd.DataFrame()

        # Handle both Series and DataFrame inputs
        if isinstance(df, pd.Series):
            df = df.to_frame(name="value").reset_index()
            df.columns = ["date", "value"]

        out["date"] = pd.to_datetime(df.get("date", df.iloc[:, 0]), errors="coerce")
        out["series_id"] = df.get("series_id", series_id)
        out["series_name"] = df.get("series_name", series_name)
        out["value"] = pd.to_numeric(df.get("value", df.iloc[:, -1]), errors="coerce")
        out["source"] = df.get("source", "fred")

        out = out.dropna(subset=["date", "value"])
        out = out.sort_values("date").reset_index(drop=True)
        logger.debug(f"normalize_macro_df [{series_id}]: {len(out)} rows")
        return out[MACRO_COLS]

    except Exception as exc:
        logger.error(f"normalize_macro_df failed: {exc}")
        return _empty(MACRO_COLS)


def normalize_stock_df(df: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    """Normalize yfinance OHLCV DataFrame to standard schema."""
    if df is None or df.empty:
        return _empty(STOCK_COLS)

    try:
        out = pd.DataFrame()

        # yfinance returns DatetimeIndex
        if isinstance(df.index, pd.DatetimeIndex):
            out["date"] = df.index.tz_localize(None) if df.index.tz else df.index
        else:
            out["date"] = pd.to_datetime(df.get("Date", df.index), errors="coerce")

        # yfinance column names may vary (capitalized or multi-level)
        cols = {c.lower(): c for c in df.columns}
        out["symbol"] = symbol
        out["open"] = pd.to_numeric(df[cols.get("open", "Open")], errors="coerce")
        out["high"] = pd.to_numeric(df[cols.get("high", "High")], errors="coerce")
        out["low"] = pd.to_numeric(df[cols.get("low", "Low")], errors="coerce")
        out["close"] = pd.to_numeric(df[cols.get("close", "Close")], errors="coerce")
        out["volume"] = pd.to_numeric(df[cols.get("volume", "Volume")], errors="coerce").fillna(0)

        out = out.dropna(subset=["date", "close"])
        out = out.sort_values("date").reset_index(drop=True)
        logger.debug(f"normalize_stock_df [{symbol}]: {len(out)} rows")
        return out[STOCK_COLS]

    except Exception as exc:
        logger.error(f"normalize_stock_df [{symbol}] failed: {exc}")
        return _empty(STOCK_COLS)


def normalize_throughput_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize World Bank port throughput data."""
    if df is None or df.empty:
        return _empty(THROUGHPUT_COLS)

    try:
        out = pd.DataFrame()
        out["year"] = pd.to_numeric(df.get("year", df.get("date", 0)), errors="coerce").fillna(0).astype(int)
        out["port_locode"] = df.get("port_locode", "UNKNOWN")
        out["country_iso3"] = df.get("country_iso3", df.get("countryiso3code", "UNK"))
        out["teu_millions"] = pd.to_numeric(df.get("teu_millions", df.get("value", 0)), errors="coerce").fillna(0)
        out["connectivity_index"] = pd.to_numeric(df.get("connectivity_index", 0), errors="coerce").fillna(0)
        out["source"] = "worldbank"

        out = out[out["year"] > 0]
        out = out.sort_values("year").reset_index(drop=True)
        logger.debug(f"normalize_throughput_df: {len(out)} rows")
        return out[THROUGHPUT_COLS]

    except Exception as exc:
        logger.error(f"normalize_throughput_df failed: {exc}")
        return _empty(THROUGHPUT_COLS)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _empty(columns: list[str]) -> pd.DataFrame:
    """Return an empty DataFrame with the given columns."""
    return pd.DataFrame(columns=columns)
