from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHIPPING_TICKERS: list[str] = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

# Composite score weights
_W_MOMENTUM = 0.35
_W_VALUE = 0.25
_W_BETA = 0.25
_W_RS = 0.15

# Signal thresholds
_SIGNAL_THRESHOLDS: list[tuple[float, str]] = [
    (0.75, "STRONG BUY"),
    (0.60, "BUY"),
    (0.40, "HOLD"),
    (0.25, "SELL"),
]

# Badge colors keyed by signal label
_SIGNAL_COLORS: dict[str, str] = {
    "STRONG BUY": "#00c853",
    "BUY": "#69f0ae",
    "HOLD": "#ffd740",
    "SELL": "#ff6d00",
    "STRONG SELL": "#d50000",
}

# Score bar color thresholds
_BAR_COLOR_HIGH = "#00c853"
_BAR_COLOR_MID = "#ffd740"
_BAR_COLOR_LOW = "#d50000"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class StockScreenResult:
    """Multi-factor screen result for a single shipping stock."""

    ticker: str
    momentum_score: float     # [0, 1] price momentum vs peers
    value_score: float        # [0, 1] proxy: inverse of recent run-up (contrarian)
    shipping_beta: float      # [0, 1] sensitivity to BDI
    relative_strength: float  # [0, 1] vs XLI as market proxy
    composite_score: float    # weighted average of above factors
    signal: str               # "STRONG BUY" | "BUY" | "HOLD" | "SELL" | "STRONG SELL"
    reasoning: str            # 2-sentence explanation


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_close_series(stock_data: dict[str, pd.DataFrame], ticker: str) -> pd.Series | None:
    """Return a date-indexed close price Series, or None if unavailable."""
    df = stock_data.get(ticker)
    if df is None or df.empty:
        return None
    try:
        return df.set_index("date")["close"].sort_index()
    except KeyError:
        return None


def _return_over_n_days(series: pd.Series, n: int) -> float | None:
    """Compute the n-day simple return (latest vs n days ago).  Returns None on failure."""
    if series is None or len(series) < n + 1:
        return None
    try:
        return float(series.iloc[-1] / series.iloc[-(n + 1)] - 1.0)
    except Exception:
        return None


def _normalize_across(values: dict[str, float]) -> dict[str, float]:
    """Min-max normalize a dict of floats to [0, 1].  Handles ties gracefully."""
    if not values:
        return {}
    vals = list(values.values())
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _composite_signal(score: float) -> str:
    """Map composite [0, 1] score to a signal label."""
    for threshold, label in _SIGNAL_THRESHOLDS:
        if score >= threshold:
            return label
    return "STRONG SELL"


def _build_bdi_series(macro_data: dict[str, pd.DataFrame]) -> pd.Series | None:
    """Extract BDI as a date-indexed Series from macro_data."""
    bdi_df = macro_data.get("BDIY")
    if bdi_df is None or bdi_df.empty:
        return None
    try:
        return bdi_df.set_index("date")["value"].sort_index()
    except KeyError:
        return None


def _shipping_beta_for(
    returns: pd.Series,
    bdi: pd.Series | None,
) -> float:
    """Compute shipping beta [0, 1] as correlation with BDI returns scaled by 1.5.

    Falls back to 0.5 if BDI unavailable or insufficient overlap.
    """
    if bdi is None or len(bdi) < 20:
        return 0.5

    bdi_ret = bdi.pct_change().dropna()
    combined = pd.concat([returns, bdi_ret], axis=1, keys=["stock", "bdi"]).dropna()

    if len(combined) < 20:
        return 0.5

    try:
        corr = float(combined["stock"].corr(combined["bdi"]))
        if np.isnan(corr):
            return 0.5
        raw_beta = corr * 1.5
        # Normalize to [0, 1]: raw_beta ranges theoretically from -1.5 to 1.5
        normalized = (raw_beta + 1.5) / 3.0
        return float(np.clip(normalized, 0.0, 1.0))
    except Exception:
        return 0.5


def _build_reasoning(
    ticker: str,
    momentum_score: float,
    value_score: float,
    shipping_beta: float,
    relative_strength: float,
    composite_score: float,
    signal: str,
) -> str:
    """Generate a 2-sentence qualitative reasoning string."""
    # Sentence 1: dominant factor
    factors = {
        "momentum": momentum_score,
        "value": value_score,
        "shipping beta": shipping_beta,
        "relative strength": relative_strength,
    }
    dominant = max(factors, key=lambda k: factors[k])
    weakest = min(factors, key=lambda k: factors[k])

    s1 = (
        f"{ticker} scores highest on {dominant} ({factors[dominant]:.2f}) "
        f"and weakest on {weakest} ({factors[weakest]:.2f}), "
        f"yielding a composite of {composite_score:.2f}."
    )

    # Sentence 2: actionable takeaway
    if signal in ("STRONG BUY", "BUY"):
        s2 = f"The multi-factor model favors a {signal.lower()} stance given broad factor alignment."
    elif signal == "HOLD":
        s2 = "Mixed signals across factors support a neutral, wait-and-see posture."
    else:
        s2 = f"Weak factor scores support a {signal.lower()} posture until conditions improve."

    return f"{s1} {s2}"


# ---------------------------------------------------------------------------
# Main screener
# ---------------------------------------------------------------------------

def screen_shipping_stocks(
    stock_data: dict[str, pd.DataFrame],
    macro_data: dict[str, pd.DataFrame],
    freight_data: dict[str, pd.DataFrame] | None = None,
) -> list[StockScreenResult]:
    """Run the multi-factor shipping stock screen.

    Args:
        stock_data: Mapping ticker -> DataFrame[date, close, ...].
        macro_data: Mapping signal-key -> DataFrame[date, value].
        freight_data: Optional freight rate DataFrames (not used directly here).

    Returns:
        List of StockScreenResult sorted by composite_score descending.
    """
    bdi = _build_bdi_series(macro_data)
    xli_series = _get_close_series(stock_data, "XLI")

    # -- Step 1: gather raw factor values ----------------------------------
    raw_momentum_30d: dict[str, float] = {}    # 30d price return
    raw_runup_90d: dict[str, float] = {}       # 90d price return (for value proxy)
    raw_rs: dict[str, float] = {}              # relative strength vs XLI

    stock_returns: dict[str, pd.Series] = {}   # daily returns keyed by ticker

    for ticker in SHIPPING_TICKERS:
        series = _get_close_series(stock_data, ticker)
        if series is None:
            logger.debug(f"Screener: no close data for {ticker}, skipping")
            continue

        ret30 = _return_over_n_days(series, 30)
        ret90 = _return_over_n_days(series, 90)

        if ret30 is None:
            logger.debug(f"Screener: insufficient history for 30d return on {ticker}")
            continue

        raw_momentum_30d[ticker] = ret30
        raw_runup_90d[ticker] = ret90 if ret90 is not None else ret30

        # Relative strength: excess return over XLI
        xli_ret = _return_over_n_days(xli_series, 30) if xli_series is not None else None
        if xli_ret is not None:
            raw_rs[ticker] = ret30 - xli_ret
        else:
            raw_rs[ticker] = ret30  # no benchmark available; use absolute

        # Store daily returns for beta calculation
        daily_ret = series.pct_change().dropna()
        stock_returns[ticker] = daily_ret

    tickers_with_data = list(raw_momentum_30d.keys())
    if not tickers_with_data:
        logger.warning("Screener: no shipping stocks had sufficient data")
        return []

    # -- Step 2: normalize factors across peers ---------------------------
    norm_momentum = _normalize_across(raw_momentum_30d)

    # Value = inverse of 90d run-up (stocks that ran least = more value)
    inverse_runup = {t: -v for t, v in raw_runup_90d.items() if t in tickers_with_data}
    norm_value = _normalize_across(inverse_runup)

    norm_rs = _normalize_across({t: v for t, v in raw_rs.items() if t in tickers_with_data})

    # -- Step 3: build results --------------------------------------------
    results: list[StockScreenResult] = []

    for ticker in tickers_with_data:
        momentum = norm_momentum.get(ticker, 0.5)
        value = norm_value.get(ticker, 0.5)
        rs = norm_rs.get(ticker, 0.5)

        # Shipping beta via BDI correlation
        daily_ret = stock_returns.get(ticker, pd.Series(dtype=float))
        beta = _shipping_beta_for(daily_ret, bdi)

        composite = (
            _W_MOMENTUM * momentum
            + _W_VALUE * value
            + _W_BETA * beta
            + _W_RS * rs
        )
        composite = float(np.clip(composite, 0.0, 1.0))

        signal = _composite_signal(composite)
        reasoning = _build_reasoning(ticker, momentum, value, beta, rs, composite, signal)

        results.append(
            StockScreenResult(
                ticker=ticker,
                momentum_score=round(momentum, 4),
                value_score=round(value, 4),
                shipping_beta=round(beta, 4),
                relative_strength=round(rs, 4),
                composite_score=round(composite, 4),
                signal=signal,
                reasoning=reasoning,
            )
        )

    results.sort(key=lambda r: r.composite_score, reverse=True)
    logger.info(
        f"Screener: evaluated {len(results)} shipping stocks; "
        f"top pick: {results[0].ticker} ({results[0].signal})" if results else "no results"
    )

    return results


# ---------------------------------------------------------------------------
# UI renderer
# ---------------------------------------------------------------------------

def render_stock_screener_panel(results: list[StockScreenResult]) -> None:
    """Render the stock screener results as a styled Streamlit panel.

    Displays a table of shipping stocks with colored signal badges, composite
    score bars, and one-line reasoning text for each stock.
    """
    try:
        import streamlit as st
    except ImportError:
        logger.error("streamlit not available; cannot render screener panel")
        return

    if not results:
        st.info("No screener results available. Check data availability for shipping stocks.")
        return

    st.markdown("#### Multi-Factor Shipping Stock Screen")

    # Build HTML table rows
    rows_html: list[str] = []
    for r in results:
        color = _SIGNAL_COLORS.get(r.signal, "#9e9e9e")

        badge_html = (
            f'<span style="'
            f'background-color:{color};color:#000;font-weight:600;'
            f'padding:2px 8px;border-radius:4px;font-size:0.78rem;">'
            f'{r.signal}</span>'
        )

        # Score bar (0–100%)
        pct = int(r.composite_score * 100)
        if pct >= 60:
            bar_color = _BAR_COLOR_HIGH
        elif pct >= 40:
            bar_color = _BAR_COLOR_MID
        else:
            bar_color = _BAR_COLOR_LOW

        bar_html = (
            f'<div style="background:#333;border-radius:4px;height:10px;width:100%;">'
            f'<div style="background:{bar_color};width:{pct}%;height:10px;border-radius:4px;"></div>'
            f'</div>'
            f'<span style="font-size:0.75rem;color:#aaa;">{pct}%</span>'
        )

        row = (
            f"<tr>"
            f'<td style="padding:6px 10px;font-weight:700;font-size:1rem;">{r.ticker}</td>'
            f'<td style="padding:6px 10px;">{badge_html}</td>'
            f'<td style="padding:6px 10px;min-width:140px;">{bar_html}</td>'
            f'<td style="padding:6px 10px;font-size:0.83rem;color:#ccc;">{r.reasoning.split(".")[0]}.</td>'
            f"</tr>"
        )
        rows_html.append(row)

    header_style = 'style="padding:6px 10px;text-align:left;color:#888;font-size:0.78rem;border-bottom:1px solid #444;"'
    table_html = (
        '<table style="width:100%;border-collapse:collapse;">'
        f"<thead><tr>"
        f"<th {header_style}>TICKER</th>"
        f"<th {header_style}>SIGNAL</th>"
        f"<th {header_style}>COMPOSITE</th>"
        f"<th {header_style}>REASONING</th>"
        f"</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        f"</table>"
    )

    st.markdown(table_html, unsafe_allow_html=True)

    # Factor detail expander
    with st.expander("Factor breakdown", expanded=False):
        factor_rows: list[dict] = [
            {
                "Ticker": r.ticker,
                "Signal": r.signal,
                "Momentum": f"{r.momentum_score:.2f}",
                "Value": f"{r.value_score:.2f}",
                "Ship Beta": f"{r.shipping_beta:.2f}",
                "Rel. Strength": f"{r.relative_strength:.2f}",
                "Composite": f"{r.composite_score:.2f}",
            }
            for r in results
        ]
        st.dataframe(pd.DataFrame(factor_rows).set_index("Ticker"), use_container_width=True)
