"""Alpha Vantage data feed — fundamentals, quotes, and income statements.

Fetches company overview, real-time quotes, and income statement data for
shipping stocks from the Alpha Vantage API.  All responses are cached via
CacheManager to respect the free-tier limits (25 requests/day, 5 req/min).

Rate limiting:
    _rate_limited_get() enforces a 12.5-second gap between requests (~4.8/min).

API key:
    Reads ALPHA_VANTAGE_KEY from st.secrets or environment.  All public
    functions silently return None / empty dict when the key is absent.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from loguru import logger

from data.cache_manager import CacheManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.alphavantage.co/query"
_DEFAULT_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

# ---------------------------------------------------------------------------
# Rate-limit state
# ---------------------------------------------------------------------------

_last_request_time: float = 0.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class StockFundamentals:
    ticker: str
    name: str
    sector: str
    industry: str
    market_cap: float           # in billions USD
    pe_ratio: float
    forward_pe: float
    eps: float
    dividend_yield_pct: float
    beta: float
    book_value: float
    pb_ratio: float
    ev_to_ebitda: float
    profit_margin_pct: float
    roe_pct: float              # return on equity
    revenue_growth_yoy_pct: float
    analyst_target_price: float
    week_52_high: float
    week_52_low: float
    description: str            # company description
    fetched_at: str             # ISO timestamp


@dataclass
class StockQuote:
    ticker: str
    price: float
    open: float
    high: float
    low: float
    volume: int
    prev_close: float
    change_pct: float
    fetched_at: str


@dataclass
class CompanyIncome:
    ticker: str
    latest_revenue: float           # most recent quarter (annualized)
    revenue_qoq_growth_pct: float
    gross_margin_pct: float
    operating_margin_pct: float
    net_margin_pct: float
    ebitda: float
    fetched_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    """Return the Alpha Vantage API key from secrets or environment."""
    try:
        key = st.secrets.get("ALPHA_VANTAGE_KEY", "")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("ALPHA_VANTAGE_KEY", "")


def _safe_float(val: object) -> float:
    """Parse a float from API response values, returning 0.0 on missing/invalid."""
    if val in (None, "None", "N/A", "-", ""):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _rate_limited_get(url: str, params: dict) -> dict:
    """GET *url* with *params*, honoring the 5 req/min rate limit.

    Sleeps if fewer than 12.5 seconds have elapsed since the last request.
    Returns an empty dict on rate-limit notices, bad-key errors, or HTTP
    failures.
    """
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 12.5:
        time.sleep(12.5 - elapsed)

    try:
        resp = requests.get(url, params=params, timeout=20)
        _last_request_time = time.time()
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _last_request_time = time.time()
        logger.error("Alpha Vantage request failed: %s", exc)
        return {}

    if "Note" in data:
        logger.warning("Alpha Vantage rate limit hit: %s", data["Note"])
        return {}
    if "Information" in data:
        logger.error("Alpha Vantage error: %s", data["Information"])
        return {}

    return data


def _dataclass_to_df(obj: object) -> pd.DataFrame:
    """Serialize a dataclass instance to a single-row DataFrame for caching."""
    import dataclasses
    return pd.DataFrame([dataclasses.asdict(obj)])


def _df_to_fundamentals(df: pd.DataFrame) -> StockFundamentals | None:
    """Deserialize a single-row DataFrame back to StockFundamentals."""
    if df is None or df.empty:
        return None
    try:
        row = df.iloc[0]
        return StockFundamentals(
            ticker=str(row["ticker"]),
            name=str(row["name"]),
            sector=str(row["sector"]),
            industry=str(row["industry"]),
            market_cap=float(row["market_cap"]),
            pe_ratio=float(row["pe_ratio"]),
            forward_pe=float(row["forward_pe"]),
            eps=float(row["eps"]),
            dividend_yield_pct=float(row["dividend_yield_pct"]),
            beta=float(row["beta"]),
            book_value=float(row["book_value"]),
            pb_ratio=float(row["pb_ratio"]),
            ev_to_ebitda=float(row["ev_to_ebitda"]),
            profit_margin_pct=float(row["profit_margin_pct"]),
            roe_pct=float(row["roe_pct"]),
            revenue_growth_yoy_pct=float(row["revenue_growth_yoy_pct"]),
            analyst_target_price=float(row["analyst_target_price"]),
            week_52_high=float(row["week_52_high"]),
            week_52_low=float(row["week_52_low"]),
            description=str(row["description"]),
            fetched_at=str(row["fetched_at"]),
        )
    except Exception as exc:
        logger.warning("Failed to deserialize StockFundamentals from cache: %s", exc)
        return None


def _df_to_quote(df: pd.DataFrame) -> StockQuote | None:
    """Deserialize a single-row DataFrame back to StockQuote."""
    if df is None or df.empty:
        return None
    try:
        row = df.iloc[0]
        return StockQuote(
            ticker=str(row["ticker"]),
            price=float(row["price"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            volume=int(row["volume"]),
            prev_close=float(row["prev_close"]),
            change_pct=float(row["change_pct"]),
            fetched_at=str(row["fetched_at"]),
        )
    except Exception as exc:
        logger.warning("Failed to deserialize StockQuote from cache: %s", exc)
        return None


def _df_to_income(df: pd.DataFrame) -> CompanyIncome | None:
    """Deserialize a single-row DataFrame back to CompanyIncome."""
    if df is None or df.empty:
        return None
    try:
        row = df.iloc[0]
        return CompanyIncome(
            ticker=str(row["ticker"]),
            latest_revenue=float(row["latest_revenue"]),
            revenue_qoq_growth_pct=float(row["revenue_qoq_growth_pct"]),
            gross_margin_pct=float(row["gross_margin_pct"]),
            operating_margin_pct=float(row["operating_margin_pct"]),
            net_margin_pct=float(row["net_margin_pct"]),
            ebitda=float(row["ebitda"]),
            fetched_at=str(row["fetched_at"]),
        )
    except Exception as exc:
        logger.warning("Failed to deserialize CompanyIncome from cache: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Private fetch helpers (raw API → dataclass)
# ---------------------------------------------------------------------------

def _fetch_fundamentals_raw(ticker: str, api_key: str) -> StockFundamentals | None:
    """Call OVERVIEW endpoint and return a StockFundamentals or None on failure."""
    logger.debug("Alpha Vantage OVERVIEW fetch: %s", ticker)
    params = {"function": "OVERVIEW", "symbol": ticker, "apikey": api_key}
    data = _rate_limited_get(_BASE_URL, params)
    if not data or "Symbol" not in data:
        logger.warning("Alpha Vantage OVERVIEW returned no data for %s", ticker)
        return None

    try:
        market_cap_raw = _safe_float(data.get("MarketCapitalization", 0))
        return StockFundamentals(
            ticker=ticker,
            name=str(data.get("Name", ticker)),
            sector=str(data.get("Sector", "")),
            industry=str(data.get("Industry", "")),
            market_cap=market_cap_raw / 1e9,  # convert to billions
            pe_ratio=_safe_float(data.get("PERatio")),
            forward_pe=_safe_float(data.get("ForwardPE")),
            eps=_safe_float(data.get("EPS")),
            dividend_yield_pct=_safe_float(data.get("DividendYield")) * 100,
            beta=_safe_float(data.get("Beta")),
            book_value=_safe_float(data.get("BookValue")),
            pb_ratio=_safe_float(data.get("PriceToBookRatio")),
            ev_to_ebitda=_safe_float(data.get("EVToEBITDA")),
            profit_margin_pct=_safe_float(data.get("ProfitMargin")) * 100,
            roe_pct=_safe_float(data.get("ReturnOnEquityTTM")) * 100,
            revenue_growth_yoy_pct=_safe_float(data.get("QuarterlyEarningsGrowthYOY")) * 100,
            analyst_target_price=_safe_float(data.get("AnalystTargetPrice")),
            week_52_high=_safe_float(data.get("52WeekHigh")),
            week_52_low=_safe_float(data.get("52WeekLow")),
            description=str(data.get("Description", "")),
            fetched_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("Alpha Vantage OVERVIEW parse error for %s: %s", ticker, exc)
        return None


def _fetch_quote_raw(ticker: str, api_key: str) -> StockQuote | None:
    """Call GLOBAL_QUOTE endpoint and return a StockQuote or None on failure."""
    logger.debug("Alpha Vantage GLOBAL_QUOTE fetch: %s", ticker)
    params = {"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": api_key}
    data = _rate_limited_get(_BASE_URL, params)
    quote = data.get("Global Quote", {})
    if not quote:
        logger.warning("Alpha Vantage GLOBAL_QUOTE returned no data for %s", ticker)
        return None

    try:
        change_pct_str = str(quote.get("10. change percent", "0%")).replace("%", "")
        return StockQuote(
            ticker=ticker,
            price=_safe_float(quote.get("05. price")),
            open=_safe_float(quote.get("02. open")),
            high=_safe_float(quote.get("03. high")),
            low=_safe_float(quote.get("04. low")),
            volume=int(_safe_float(quote.get("06. volume"))),
            prev_close=_safe_float(quote.get("08. previous close")),
            change_pct=_safe_float(change_pct_str),
            fetched_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("Alpha Vantage GLOBAL_QUOTE parse error for %s: %s", ticker, exc)
        return None


def _fetch_income_raw(ticker: str, api_key: str) -> CompanyIncome | None:
    """Call INCOME_STATEMENT endpoint and return a CompanyIncome or None on failure."""
    logger.debug("Alpha Vantage INCOME_STATEMENT fetch: %s", ticker)
    params = {"function": "INCOME_STATEMENT", "symbol": ticker, "apikey": api_key}
    data = _rate_limited_get(_BASE_URL, params)

    quarterly = data.get("quarterlyReports", [])
    if not quarterly:
        logger.warning("Alpha Vantage INCOME_STATEMENT returned no quarterly data for %s", ticker)
        return None

    try:
        q0 = quarterly[0]  # most recent quarter
        q1 = quarterly[1] if len(quarterly) > 1 else {}

        rev0 = _safe_float(q0.get("totalRevenue"))
        rev1 = _safe_float(q1.get("totalRevenue")) if q1 else 0.0
        gross0 = _safe_float(q0.get("grossProfit"))
        op_income0 = _safe_float(q0.get("operatingIncome"))
        net_income0 = _safe_float(q0.get("netIncome"))
        ebitda0 = _safe_float(q0.get("ebitda"))

        # Annualize the most recent quarter
        latest_revenue_annualized = rev0 * 4

        # QoQ revenue growth
        if rev1 != 0.0:
            rev_qoq = (rev0 - rev1) / abs(rev1) * 100
        else:
            rev_qoq = 0.0

        # Margin percentages (guard against zero revenue)
        gross_margin = (gross0 / rev0 * 100) if rev0 != 0.0 else 0.0
        op_margin = (op_income0 / rev0 * 100) if rev0 != 0.0 else 0.0
        net_margin = (net_income0 / rev0 * 100) if rev0 != 0.0 else 0.0

        return CompanyIncome(
            ticker=ticker,
            latest_revenue=latest_revenue_annualized,
            revenue_qoq_growth_pct=rev_qoq,
            gross_margin_pct=gross_margin,
            operating_margin_pct=op_margin,
            net_margin_pct=net_margin,
            ebitda=ebitda0,
            fetched_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("Alpha Vantage INCOME_STATEMENT parse error for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def alphavantage_available() -> bool:
    """Return True if an Alpha Vantage API key is configured."""
    return bool(_get_api_key())


def fetch_fundamentals(
    ticker: str,
    cache_ttl_hours: float = 24.0,
    cache: CacheManager | None = None,
) -> StockFundamentals | None:
    """Fetch company overview/fundamentals for one ticker.

    Returns None when the API key is absent or the request fails.
    Results are cached for *cache_ttl_hours* hours.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("ALPHA_VANTAGE_KEY not set — skipping fundamentals for %s", ticker)
        return None

    cache = cache or CacheManager()
    cache_key = f"alphavantage_{ticker}_OVERVIEW"

    try:
        df = cache.get_or_fetch(
            key=cache_key,
            fetch_fn=lambda: _dataclass_to_df(_fetch_fundamentals_raw(ticker, api_key)),
            ttl_hours=cache_ttl_hours,
            source="alphavantage",
        )
        return _df_to_fundamentals(df)
    except Exception as exc:
        logger.error("fetch_fundamentals failed for %s: %s", ticker, exc)
        return None


def fetch_quote(
    ticker: str,
    cache_ttl_hours: float = 0.25,
    cache: CacheManager | None = None,
) -> StockQuote | None:
    """Fetch real-time quote for one ticker.

    Default TTL is 15 minutes (0.25 h).
    Returns None when the API key is absent or the request fails.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("ALPHA_VANTAGE_KEY not set — skipping quote for %s", ticker)
        return None

    cache = cache or CacheManager()
    cache_key = f"alphavantage_{ticker}_GLOBAL_QUOTE"

    try:
        df = cache.get_or_fetch(
            key=cache_key,
            fetch_fn=lambda: _dataclass_to_df(_fetch_quote_raw(ticker, api_key)),
            ttl_hours=cache_ttl_hours,
            source="alphavantage",
        )
        return _df_to_quote(df)
    except Exception as exc:
        logger.error("fetch_quote failed for %s: %s", ticker, exc)
        return None


def fetch_income(
    ticker: str,
    cache_ttl_hours: float = 24.0,
    cache: CacheManager | None = None,
) -> CompanyIncome | None:
    """Fetch income statement for one ticker.

    Returns None when the API key is absent or the request fails.
    Results are cached for *cache_ttl_hours* hours.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("ALPHA_VANTAGE_KEY not set — skipping income for %s", ticker)
        return None

    cache = cache or CacheManager()
    cache_key = f"alphavantage_{ticker}_INCOME_STATEMENT"

    try:
        df = cache.get_or_fetch(
            key=cache_key,
            fetch_fn=lambda: _dataclass_to_df(_fetch_income_raw(ticker, api_key)),
            ttl_hours=cache_ttl_hours,
            source="alphavantage",
        )
        return _df_to_income(df)
    except Exception as exc:
        logger.error("fetch_income failed for %s: %s", ticker, exc)
        return None


def fetch_all_shipping_fundamentals(
    tickers: list[str] | None = None,
    cache_ttl_hours: float = 24.0,
    cache: CacheManager | None = None,
) -> dict[str, StockFundamentals]:
    """Fetch fundamentals for all shipping tickers.

    *tickers* defaults to ["ZIM", "MATX", "SBLK", "DAC", "CMRE"].
    Respects the 5 req/min rate limit via _rate_limited_get (12.5 s gap).
    Returns a dict mapping ticker -> StockFundamentals; failed tickers are
    silently skipped.
    """
    if tickers is None:
        tickers = _DEFAULT_TICKERS

    api_key = _get_api_key()
    if not api_key:
        logger.warning("ALPHA_VANTAGE_KEY not set — returning empty fundamentals dict")
        return {}

    cache = cache or CacheManager()
    results: dict[str, StockFundamentals] = {}

    for ticker in tickers:
        try:
            fund = fetch_fundamentals(ticker, cache_ttl_hours=cache_ttl_hours, cache=cache)
            if fund is not None:
                results[ticker] = fund
                logger.info("Alpha Vantage fundamentals loaded: %s", ticker)
            else:
                logger.warning("Alpha Vantage fundamentals unavailable for %s — skipped", ticker)
        except Exception as exc:
            logger.warning("Alpha Vantage fundamentals error for %s (skipping): %s", ticker, exc)
            continue

    logger.info("Alpha Vantage fundamentals fetched for: %s", list(results.keys()))
    return results


# ---------------------------------------------------------------------------
# HTML report helper
# ---------------------------------------------------------------------------

def build_fundamentals_table_html(fundamentals: dict[str, StockFundamentals]) -> str:
    """Return an HTML table of key fundamentals for the investor report.

    Columns: Ticker | Company | Mkt Cap | P/E | Fwd P/E | EPS | Beta |
             Div Yield | ROE | Analyst Target

    The analyst target cell is color-coded green when it is above the
    52-week high (implying upside even vs recent peak) and red when it is
    below the current 52-week low (extreme pessimism).  Otherwise it is
    neutral amber.  (We use 52W High as a rough proxy for current price
    when a live quote is not separately passed in.)
    """
    if not fundamentals:
        return "<p><em>No Alpha Vantage fundamentals available.</em></p>"

    rows_html: list[str] = []
    for ticker, f in fundamentals.items():
        # Color-code analyst target vs 52-week midpoint as a price proxy
        mid_52w = (f.week_52_high + f.week_52_low) / 2 if (f.week_52_high + f.week_52_low) else 0
        if mid_52w > 0 and f.analyst_target_price > 0:
            if f.analyst_target_price >= f.week_52_high:
                target_color = "#27ae60"   # green — above recent high
            elif f.analyst_target_price <= f.week_52_low:
                target_color = "#e74c3c"   # red — below recent low
            else:
                target_color = "#f39c12"   # amber — within 52w range
        else:
            target_color = "#888888"       # grey — no data

        target_str = (
            f'<span style="color:{target_color};font-weight:600;">'
            f'${f.analyst_target_price:,.2f}</span>'
            if f.analyst_target_price > 0
            else '<span style="color:#888;">N/A</span>'
        )

        mkt_cap_str = f"${f.market_cap:.2f}B" if f.market_cap > 0 else "N/A"
        pe_str = f"{f.pe_ratio:.1f}x" if f.pe_ratio > 0 else "N/A"
        fwd_pe_str = f"{f.forward_pe:.1f}x" if f.forward_pe > 0 else "N/A"
        eps_str = f"${f.eps:.2f}" if f.eps != 0 else "N/A"
        beta_str = f"{f.beta:.2f}" if f.beta != 0 else "N/A"
        div_str = f"{f.dividend_yield_pct:.2f}%" if f.dividend_yield_pct > 0 else "—"
        roe_str = f"{f.roe_pct:.1f}%" if f.roe_pct != 0 else "N/A"

        rows_html.append(
            f"<tr>"
            f"<td><strong>{ticker}</strong></td>"
            f"<td>{f.name}</td>"
            f"<td>{mkt_cap_str}</td>"
            f"<td>{pe_str}</td>"
            f"<td>{fwd_pe_str}</td>"
            f"<td>{eps_str}</td>"
            f"<td>{beta_str}</td>"
            f"<td>{div_str}</td>"
            f"<td>{roe_str}</td>"
            f"<td>{target_str}</td>"
            f"</tr>"
        )

    header = (
        "<tr style='background:#1a1a2e;color:#e0e0e0;'>"
        "<th>Ticker</th><th>Company</th><th>Mkt Cap</th>"
        "<th>P/E</th><th>Fwd P/E</th><th>EPS</th>"
        "<th>Beta</th><th>Div Yield</th><th>ROE</th>"
        "<th>Analyst Target</th>"
        "</tr>"
    )

    table_style = (
        "style='width:100%;border-collapse:collapse;"
        "font-size:0.85rem;font-family:monospace;'"
    )
    row_style_tag = (
        "<style>"
        "table.av-fund td, table.av-fund th "
        "{padding:6px 10px;border-bottom:1px solid #2a2a3e;text-align:right;}"
        "table.av-fund td:nth-child(1),"
        "table.av-fund td:nth-child(2),"
        "table.av-fund th:nth-child(1),"
        "table.av-fund th:nth-child(2) {text-align:left;}"
        "table.av-fund tr:hover td {background:rgba(255,255,255,0.04);}"
        "</style>"
    )

    return (
        row_style_tag
        + f"<table class='av-fund' {table_style}>"
        + "<thead>" + header + "</thead>"
        + "<tbody>" + "".join(rows_html) + "</tbody>"
        + "</table>"
    )
