"""ui/tab_indices.py — Bloomberg-style Shipping Indices Dashboard.

Sections:
  1. Index Dashboard   — KPI cards for all major shipping indices
  2. Multi-Index Chart — Normalized overlay (up to 5 indices), time-range selector
  3. BDI Deep Dive     — Component breakdown, historical context, BDI vs S&P500
  4. Spread Analysis   — Key spreads with historical percentile
  5. Cointegration     — Engle-Granger pair-wise on log-levels
  6. Forward Curve     — FFA-implied BDI forward curve (live or mock)
  7. Cross-Asset       — Indices vs macro (2×2 Plotly subplots)
  8. Methodology       — Reference table of index definitions

Function signature: render(freight_data=None, macro_data=None, stock_data=None)
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger
from plotly.subplots import make_subplots

from data.freight_scraper import fetch_baltic_daily
from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)


# Baltic index families served by fetch_baltic_daily()
_BALTIC_FAMILIES: tuple[str, ...] = ("BDI", "BCI", "BPI", "BSI", "BHSI")


# ── Index catalogue ────────────────────────────────────────────────────────────
_INDICES: list[dict] = [
    # Dry Bulk
    dict(id="BDI",   label="Baltic Dry",       group="Dry Bulk",    base=1000,  scale=1,    unit="pts",   routes=23),
    dict(id="BCI",   label="Baltic Capesize",   group="Dry Bulk",    base=1800,  scale=1,    unit="pts",   routes=5),
    dict(id="BPI",   label="Baltic Panamax",    group="Dry Bulk",    base=1200,  scale=1,    unit="pts",   routes=4),
    dict(id="BSI",   label="Baltic Supramax",   group="Dry Bulk",    base=900,   scale=1,    unit="pts",   routes=10),
    dict(id="BHSI",  label="Baltic Handysize",  group="Dry Bulk",    base=600,   scale=1,    unit="pts",   routes=7),
    # Container
    dict(id="WCI",   label="World Container",   group="Container",   base=3200,  scale=1,    unit="$/FEU", routes=8),
    dict(id="SCFI",  label="Shanghai SCFI",     group="Container",   base=2800,  scale=1,    unit="pts",   routes=15),
    dict(id="CCFI",  label="China CCFI",        group="Container",   base=1100,  scale=1,    unit="pts",   routes=12),
    dict(id="FBX",   label="Freightos FBX",     group="Container",   base=2600,  scale=1,    unit="$/FEU", routes=12),
    dict(id="HARPEX",label="Harpex",            group="Container",   base=950,   scale=1,    unit="pts",   routes=6),
    # Tanker
    dict(id="BDTI",  label="Baltic Dirty Tnkr", group="Tanker",      base=800,   scale=1,    unit="pts",   routes=12),
    dict(id="BCTI",  label="Baltic Clean Tnkr", group="Tanker",      base=700,   scale=1,    unit="pts",   routes=9),
    dict(id="BLNG",  label="Baltic LNG",        group="Tanker",      base=55000, scale=1,    unit="$/day", routes=4),
    dict(id="BLPG",  label="Baltic LPG",        group="Tanker",      base=45000, scale=1,    unit="$/day", routes=3),
]

_INDEX_COLORS: dict[str, str] = {
    "BDI": C_ACCENT, "BCI": "#4a90a4", "BPI": "#7c6eaf", "BSI": "#c9962b", "BHSI": "#f97316",
    "WCI": C_HIGH,   "SCFI": "#34d399","CCFI": "#a7f3d0","FBX": "#fbbf24", "HARPEX": "#fb923c",
    "BDTI": C_LOW,   "BCTI": "#f87171","BLNG": "#c084fc","BLPG": "#e879f9",
}

_METHODOLOGY: list[dict] = [
    dict(index="BDI",    method="Weighted avg of BCI/BPI/BSI/BHSI rates",  freq="Daily",  routes=23, publisher="Baltic Exchange"),
    dict(index="BCI",    method="TC avg of 5 Capesize routes (170k DWT)",   freq="Daily",  routes=5,  publisher="Baltic Exchange"),
    dict(index="BPI",    method="TC avg of 4 Panamax routes (74k DWT)",     freq="Daily",  routes=4,  publisher="Baltic Exchange"),
    dict(index="BSI",    method="TC avg of 10 Supramax routes (58k DWT)",   freq="Daily",  routes=10, publisher="Baltic Exchange"),
    dict(index="BHSI",   method="TC avg of 7 Handysize routes (38k DWT)",   freq="Daily",  routes=7,  publisher="Baltic Exchange"),
    dict(index="WCI",    method="Avg spot rate 8 global trade lanes",        freq="Weekly", routes=8,  publisher="Drewry"),
    dict(index="SCFI",   method="Spot rates ex-Shanghai 15 routes",          freq="Weekly", routes=15, publisher="Shanghai Shipping Exchange"),
    dict(index="CCFI",   method="Long-term & spot rates ex-China 12 routes", freq="Weekly", routes=12, publisher="Shanghai Shipping Exchange"),
    dict(index="FBX",    method="AI-aggregated spot market rates 12 lanes",  freq="Weekly", routes=12, publisher="Freightos"),
    dict(index="HARPEX", method="Charter rates 6 container vessel classes",  freq="Weekly", routes=6,  publisher="Harper Petersen"),
    dict(index="BDTI",   method="Time charter equiv dirty tanker 12 routes", freq="Daily",  routes=12, publisher="Baltic Exchange"),
    dict(index="BCTI",   method="Time charter equiv clean tanker 9 routes",  freq="Daily",  routes=9,  publisher="Baltic Exchange"),
    dict(index="BLNG",   method="LNG carrier spot rate 4 benchmark routes",  freq="Weekly", routes=4,  publisher="Baltic Exchange"),
    dict(index="BLPG",   method="LPG VLGC spot rate 3 benchmark routes",     freq="Weekly", routes=3,  publisher="Baltic Exchange"),
]


# ── Data helpers ───────────────────────────────────────────────────────────────

def _seed_from_id(idx_id: str) -> int:
    return sum(ord(c) for c in idx_id) % 9999


def _mock_series(idx: dict, days: int = 365 * 5) -> pd.Series:
    """Generate realistic mock price history for an index."""
    rng = np.random.default_rng(_seed_from_id(idx["id"]))
    mu = 0.0001
    sigma = 0.012 + rng.random() * 0.008
    log_returns = rng.normal(mu, sigma, days)
    prices = idx["base"] * np.exp(np.cumsum(log_returns))
    for i in range(1, len(prices)):
        prices[i] += 0.002 * (idx["base"] - prices[i - 1])
    end = dt.date.today()
    dates = pd.date_range(end=end, periods=days, freq="B")
    return pd.Series(prices[: len(dates)], index=dates, name=idx["id"])


def _try_yfinance(ticker: str, period: str = "5y") -> Optional[pd.Series]:
    try:
        import yfinance as yf  # noqa: PLC0415
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return None
        close = df["Close"]
        if hasattr(close, "squeeze"):
            close = close.squeeze()
        return close.dropna()
    except Exception as exc:
        logger.debug("yfinance fetch failed for {}: {}", ticker, exc)
        return None


def _get_series(idx: dict, days: int = 365 * 5) -> pd.Series:
    """Return the price history for an index.

    * Baltic families (BDI/BCI/BPI/BSI/BHSI) now resolve through the
      ``fetch_baltic_daily()`` feed (live-with-fixture-fallback).
    * Everything else still falls back to the seeded mock walk until a
      dedicated feed lands for it.
    """
    if idx["id"] in _BALTIC_FAMILIES:
        try:
            ds = fetch_baltic_daily(idx["id"])
            series = ds.data
            if isinstance(series, pd.Series) and len(series) > 0:
                return series.rename(idx["id"])
        except Exception as exc:
            logger.warning("Baltic feed failed for {}: {}", idx["id"], exc)
    return _mock_series(idx, days)


def _build_all_series(days: int = 365 * 5) -> dict[str, pd.Series]:
    result: dict[str, pd.Series] = {}
    for idx in _INDICES:
        try:
            result[idx["id"]] = _get_series(idx, days)
        except Exception as exc:
            logger.warning("Failed to build series for {}: {}", idx["id"], exc)
            result[idx["id"]] = _mock_series(idx, days)
    return result


def _build_baltic_sources() -> dict[str, DataSource]:
    """Return the DataSource for each Baltic family (one HTTP call via cache)."""
    out: dict[str, DataSource] = {}
    for fam in _BALTIC_FAMILIES:
        try:
            out[fam] = fetch_baltic_daily(fam).source
        except Exception as exc:
            logger.debug("Baltic source fetch failed for {}: {}", fam, exc)
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_series() -> dict[str, pd.Series]:
    return _build_all_series()


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_baltic_sources() -> dict[str, DataSource]:
    return _build_baltic_sources()


def _pct(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / abs(old) * 100


def _get_stats(series: pd.Series) -> dict:
    try:
        s = series.dropna()
        if len(s) < 2:
            return {}
        now = float(s.iloc[-1])
        prev_day = float(s.iloc[-2]) if len(s) >= 2 else now
        prev_week = float(s.iloc[-6]) if len(s) >= 6 else prev_day
        prev_month = float(s.iloc[-22]) if len(s) >= 22 else prev_day
        prev_year = float(s.iloc[-252]) if len(s) >= 252 else prev_day
        avg_5y = float(s.mean())
        return dict(
            now=now,
            day_chg=now - prev_day,
            day_pct=_pct(now, prev_day),
            wow_pct=_pct(now, prev_week),
            mom_pct=_pct(now, prev_month),
            yoy_pct=_pct(now, prev_year),
            avg_5y=avg_5y,
            above_avg_pct=_pct(now, avg_5y),
        )
    except Exception as exc:
        logger.debug("Stats error: {}", exc)
        return {}


# ── Cell formatters ───────────────────────────────────────────────────────────

def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ── Card builders ──────────────────────────────────────────────────────────────

def _pct_arrow(pct: float) -> tuple[str, str]:
    """Return (arrow_glyph, color) for a percent change."""
    if pct > 0:
        return "▲", C_HIGH
    if pct < 0:
        return "▼", C_LOW
    return "—", C_TEXT3


def _kpi_card_dict(idx: dict, stats: dict) -> dict:
    """Build a metric_card_row dict for one shipping index."""
    accent = _INDEX_COLORS.get(idx["id"], C_ACCENT)
    label = f'{idx["group"]} · {idx["label"]}'
    if not stats:
        return {
            "label": label,
            "value": "—",
            "accent": accent,
            "sublabel": "No data",
        }
    now = stats["now"]
    val_str = f'{now:,.0f}' if now >= 100 else f'{now:,.1f}'
    value_html = (
        f'{val_str}<span style="color:{C_TEXT3};font-size:0.7rem;'
        f'margin-left:4px;font-family:var(--sans);font-weight:500;">{idx["unit"]}</span>'
    )
    arrow, day_color = _pct_arrow(stats["day_pct"])
    delta_html = (
        f'{arrow} {abs(stats["day_chg"]):.0f} '
        f'({abs(stats["day_pct"]):.1f}%)'
    )
    wow_arrow, wow_c = _pct_arrow(stats["wow_pct"])
    mom_arrow, mom_c = _pct_arrow(stats["mom_pct"])
    yoy_arrow, yoy_c = _pct_arrow(stats["yoy_pct"])
    above_color = C_HIGH if stats["above_avg_pct"] > 0 else C_LOW
    sub_html = (
        f'<span style="color:{wow_c};">WoW {wow_arrow} {abs(stats["wow_pct"]):.1f}%</span> · '
        f'<span style="color:{mom_c};">MoM {mom_arrow} {abs(stats["mom_pct"]):.1f}%</span> · '
        f'<span style="color:{yoy_c};">YoY {yoy_arrow} {abs(stats["yoy_pct"]):.1f}%</span>'
        f'<br><span style="color:{above_color};">'
        f'{stats["above_avg_pct"]:+.1f}% vs 5Y avg</span>'
    )
    return {
        "label": label,
        "value": value_html,
        "accent": accent,
        "delta": delta_html,
        "delta_color": day_color,
        "sublabel": sub_html,
    }


# ── Section 1: Index Dashboard ─────────────────────────────────────────────────

def _baltic_sources_list(
    baltic_sources: dict[str, DataSource] | None,
    families: tuple[str, ...] = ("BDI",),
) -> list[DataSource]:
    """Return DataSource objects for the requested Baltic families, in order."""
    if not baltic_sources:
        return []
    out: list[DataSource] = []
    for fam in families:
        src = baltic_sources.get(fam)
        if src is not None:
            out.append(src)
    return out


def _render_index_dashboard(
    all_series: dict[str, pd.Series],
    baltic_sources: dict[str, DataSource] | None = None,
) -> None:
    section_header("Index Dashboard", "Live snapshot of major shipping benchmarks")
    groups = [
        ("Dry Bulk",  [m for m in _INDICES if m["group"] == "Dry Bulk"]),
        ("Container", [m for m in _INDICES if m["group"] == "Container"]),
        ("Tanker",    [m for m in _INDICES if m["group"] == "Tanker"]),
    ]
    for _group_name, row_indices in groups:
        cards: list[dict] = []
        for idx in row_indices:
            try:
                series = all_series.get(idx["id"], pd.Series(dtype=float))
                stats = _get_stats(series)
                cards.append(_kpi_card_dict(idx, stats))
            except Exception as exc:
                logger.warning("Card render error {}: {}", idx["id"], exc)
                cards.append({
                    "label": idx["label"],
                    "value": "—",
                    "accent": C_LOW,
                    "sublabel": "Error",
                })
        metric_card_row(cards, columns=len(cards))
    sources = _baltic_sources_list(baltic_sources, _BALTIC_FAMILIES)
    if sources:
        st.markdown(source_footer(sources), unsafe_allow_html=True)


# ── Section 2: Multi-Index Chart ───────────────────────────────────────────────

def _render_multi_index_chart(
    all_series: dict[str, pd.Series],
    baltic_sources: dict[str, DataSource] | None = None,
) -> None:
    section_header("Multi-Index Comparison",
                   "Normalized to 100 at start date — overlay up to 5 indices")
    all_ids = [idx["id"] for idx in _INDICES]
    default_sel = ["BDI", "WCI", "BDTI", "SCFI", "BCI"]
    ca, cb = st.columns([3, 1])
    with ca:
        selected = st.multiselect(
            "Select indices (max 5)",
            options=all_ids,
            default=default_sel,
            max_selections=5,
            key="mi_select",
        )
    with cb:
        time_range = st.selectbox("Range", ["1M", "3M", "6M", "1Y", "2Y", "5Y"],
                                  index=3, key="mi_range")
    range_days = {"1M": 22, "3M": 66, "6M": 132, "1Y": 252, "2Y": 504, "5Y": 1260}
    ndays = range_days.get(time_range, 252)
    if not selected:
        st.info("Select at least one index.")
        return
    try:
        fig = go.Figure()
        colors_list = [_INDEX_COLORS.get(s, C_ACCENT) for s in selected]
        for sid, color in zip(selected, colors_list):
            series = all_series.get(sid, pd.Series(dtype=float)).dropna()
            series = series.iloc[-ndays:]
            if len(series) < 2:
                continue
            norm = series / series.iloc[0] * 100
            label = next((m["label"] for m in _INDICES if m["id"] == sid), sid)
            fig.add_trace(go.Scatter(
                x=norm.index, y=norm.values, name=label,
                line=dict(color=color, width=2),
                hovertemplate=f"<b>{label}</b><br>%{{x|%b %d, %Y}}<br>"
                              f"Normalized: %{{y:.1f}}<extra></extra>",
            ))
        fig.add_hline(y=100, line_dash="dot", line_color=C_TEXT3, line_width=1)
        apply_dark_layout(
            fig,
            title=f"Normalized Index Performance — {time_range}",
            height=380,
            hovermode="x unified",
            yaxis=dict(title="Index (base = 100)"),
        )
        st.plotly_chart(fig, use_container_width=True)
        sources = _baltic_sources_list(baltic_sources, ("BDI",))
        if sources:
            st.markdown(source_footer(sources), unsafe_allow_html=True)
    except Exception as exc:
        logger.error("Multi-index chart error: {}", exc)
        st.error(f"Chart error: {exc}")


# ── Section 3: BDI Deep Dive ───────────────────────────────────────────────────

def _render_bdi_deep_dive(
    all_series: dict[str, pd.Series],
    baltic_sources: dict[str, DataSource] | None = None,
) -> None:
    section_header("BDI Deep Dive",
                   "Component breakdown, historical context, and macro correlation")
    bdi = all_series.get("BDI", pd.Series(dtype=float)).dropna()
    bci = all_series.get("BCI", pd.Series(dtype=float)).dropna()
    bpi = all_series.get("BPI", pd.Series(dtype=float)).dropna()
    bsi = all_series.get("BSI", pd.Series(dtype=float)).dropna()
    bhsi = all_series.get("BHSI", pd.Series(dtype=float)).dropna()

    components = [
        dict(name="Capesize (BCI)",   weight="40%", value=bci.iloc[-1] if len(bci) else 1800,  color=C_ACCENT),
        dict(name="Panamax (BPI)",    weight="30%", value=bpi.iloc[-1] if len(bpi) else 1200,  color="#7c6eaf"),
        dict(name="Supramax (BSI)",   weight="15%", value=bsi.iloc[-1] if len(bsi) else 900,   color=C_MOD),
        dict(name="Handysize (BHSI)", weight="15%", value=bhsi.iloc[-1] if len(bhsi) else 600, color="#f97316"),
    ]
    metric_card_row(
        [
            {
                "label": comp["name"],
                "value": f'{comp["value"]:,.0f}',
                "accent": comp["color"],
                "sublabel": f'Weight {comp["weight"]}',
            }
            for comp in components
        ],
        columns=4,
    )

    try:
        avg_5y = float(bdi.mean()) if len(bdi) > 0 else 1000
        current = float(bdi.iloc[-1]) if len(bdi) > 0 else 1000
        pct_vs_avg = _pct(current, avg_5y)
        direction = "above" if pct_vs_avg >= 0 else "below"
        metric_card_row(
            [
                {
                    "label": "BDI vs 5Y Average",
                    "value": f"{pct_vs_avg:+.1f}%",
                    "accent": C_HIGH if pct_vs_avg > 0 else C_LOW,
                    "delta": f"Currently {direction} 5-year mean",
                    "delta_color": C_HIGH if pct_vs_avg > 0 else C_LOW,
                    "sublabel": f"5Y avg: {avg_5y:,.0f} pts · spot: {current:,.0f} pts",
                },
            ],
            columns=1,
        )
    except Exception as exc:
        logger.debug("BDI context error: {}", exc)

    col1, col2 = st.columns([3, 2])
    with col1:
        try:
            if len(bdi) > 10:
                fig = go.Figure()
                s = bdi.iloc[-504:]
                avg_line = [float(bdi.mean())] * len(s)
                fig.add_trace(go.Scatter(
                    x=s.index, y=s.values, name="BDI",
                    line=dict(color=C_ACCENT, width=2),
                    fill="tozeroy", fillcolor="rgba(53,114,176,0.08)",
                ))
                fig.add_trace(go.Scatter(
                    x=s.index, y=avg_line, name="5Y Average",
                    line=dict(color=C_MOD, width=1, dash="dash"),
                ))
                apply_dark_layout(
                    fig, height=280, title="BDI — 2-Year History",
                    yaxis=dict(title="BDI Points"),
                )
                st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            logger.error("BDI history chart error: {}", exc)

    with col2:
        try:
            sp500 = _try_yfinance("^GSPC", "2y")
            if sp500 is None or len(sp500) < 20:
                rng = np.random.default_rng(42)
                sp500 = pd.Series(
                    4500 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 504))),
                    index=pd.date_range(end=dt.date.today(), periods=504, freq="B"),
                )
            common = bdi.index.intersection(sp500.index)
            if len(common) > 10:
                x_vals = sp500.loc[common].values
                y_vals = bdi.loc[common].values
                corr = float(np.corrcoef(x_vals, y_vals)[0, 1])
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    x=x_vals, y=y_vals, mode="markers",
                    marker=dict(color=C_ACCENT, size=4, opacity=0.5),
                    name="BDI vs S&P 500",
                    hovertemplate="S&P: %{x:,.0f}<br>BDI: %{y:,.0f}<extra></extra>",
                ))
                apply_dark_layout(
                    fig2, height=280,
                    title=f"BDI vs S&P 500 (corr: {corr:.2f})",
                    xaxis=dict(title="S&P 500"),
                    yaxis=dict(title="BDI"),
                )
                st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info("Insufficient overlapping data for correlation chart.")
        except Exception as exc:
            logger.error("BDI/SP500 scatter error: {}", exc)
            st.info("Correlation chart unavailable.")

    sources = _baltic_sources_list(baltic_sources, ("BDI", "BCI", "BPI", "BSI", "BHSI"))
    if sources:
        st.markdown(source_footer(sources), unsafe_allow_html=True)


# ── Section 4: Spread Analysis ─────────────────────────────────────────────────

def _render_spread_analysis(
    all_series: dict[str, pd.Series],
    baltic_sources: dict[str, DataSource] | None = None,
) -> None:
    section_header("Index Spread Analysis",
                   "Key spreads with historical percentile ranking")
    spread_defs = [
        dict(name="BCI – BPI Spread",   a="BCI",  b="BPI",  desc="Capesize premium over Panamax"),
        dict(name="BSI – BHSI Spread",  a="BSI",  b="BHSI", desc="Supramax premium over Handysize"),
        dict(name="BDTI – BCTI Spread", a="BDTI", b="BCTI", desc="Dirty vs Clean tanker premium"),
        dict(name="WCI – SCFI Spread",  a="WCI",  b="SCFI", desc="Global vs Shanghai container rates"),
        dict(name="BDI – BCI Spread",   a="BDI",  b="BCI",  desc="Composite vs Capesize benchmark"),
        dict(name="SCFI – CCFI Spread", a="SCFI", b="CCFI", desc="Spot vs long-term container spread"),
    ]
    rows = []
    for sd in spread_defs:
        try:
            sa = all_series.get(sd["a"], pd.Series(dtype=float)).dropna()
            sb = all_series.get(sd["b"], pd.Series(dtype=float)).dropna()
            common = sa.index.intersection(sb.index)
            if len(common) < 20:
                continue
            spread = sa.loc[common] - sb.loc[common]
            current = float(spread.iloc[-1])
            pctile = float((spread < current).mean() * 100)
            avg = float(spread.mean())
            rows.append(dict(
                Spread=sd["name"],
                Description=sd["desc"],
                Current=current,
                Avg=avg,
                Percentile=pctile,
            ))
        except Exception as exc:
            logger.debug("Spread error {}/{}: {}", sd["a"], sd["b"], exc)
    if not rows:
        st.info("Spread data unavailable.")
        return

    def _pctile_color(p: float) -> str:
        if p >= 80:
            return C_LOW
        if p >= 60:
            return C_MOD
        if p <= 20:
            return C_HIGH
        return C_TEXT2

    headers = ["Spread", "Description", "Current", "5Y Avg", "Percentile"]
    table_rows = []
    for row in rows:
        p = row["Percentile"]
        pc = _pctile_color(p)
        bar_w = int(p)
        pctile_cell = (
            f'<div class="progress-bar-custom">'
            f'<div class="progress-bar-fill" style="width:{bar_w}%;background:{pc};"></div>'
            f'</div>'
            f'{_mono(f"{p:.0f}th", color=pc)}'
        )
        table_rows.append([
            _sans(row["Spread"], color=C_TEXT, weight=600),
            _sans(row["Description"], color=C_TEXT2),
            _mono(f"{row['Current']:+,.0f}", color=C_TEXT),
            _mono(f"{row['Avg']:+,.0f}", color=C_TEXT3),
            pctile_cell,
        ])
    wsj_market_table(headers, table_rows)
    sources = _baltic_sources_list(baltic_sources, ("BDI", "BCI", "BPI", "BSI", "BHSI"))
    if sources:
        st.markdown(source_footer(sources), unsafe_allow_html=True)


# ── Section 4b: Cross-Index Cointegration ─────────────────────────────────────

_COINT_PAIRS: list[tuple[str, str]] = [
    ("BDI", "WCI"),
    ("BDI", "SCFI"),
    ("WCI", "SCFI"),
    ("WCI", "FBX"),
    ("SCFI", "FBX"),
    ("SCFI", "CCFI"),
    ("FBX", "HARPEX"),
    ("BCI", "BPI"),
    ("BDTI", "BCTI"),
]


def _render_cointegration(
    all_series: dict[str, pd.Series],
    baltic_sources: dict[str, DataSource] | None = None,
) -> None:
    """Engle-Granger cointegration across shipping index pairs."""
    section_header(
        "Cross-Index Cointegration",
        "Pair-wise Engle-Granger on log-levels — surfaces mean-reversion opportunities",
    )
    try:
        from engine.cointegration import pair_report  # noqa: PLC0415
    except Exception as exc:
        logger.warning("cointegration unavailable: {}", exc)
        st.info("Cointegration engine unavailable (install statsmodels).")
        return

    reports = []
    for a, b in _COINT_PAIRS:
        try:
            sa = all_series.get(a, pd.Series(dtype=float)).dropna()
            sb = all_series.get(b, pd.Series(dtype=float)).dropna()
            common = sa.index.intersection(sb.index)
            if len(common) < 150:
                continue
            y = np.log(sa.loc[common].astype(float).clip(lower=1e-6))
            x = np.log(sb.loc[common].astype(float).clip(lower=1e-6))
            y.name, x.name = a, b
            reports.append(pair_report(y, x, min_obs=120))
        except Exception as exc:
            logger.debug("coint pair {}/{} skipped: {}", a, b, exc)

    if not reports:
        st.info("Insufficient overlapping history to run cointegration.")
        return

    reports.sort(key=lambda r: r.engle_granger.coint_pvalue)

    headers = ["Pair", "β̂", "EG p-value", "λ (ECM)", "Half-life (days)", "Signal"]
    table_rows = []
    for r in reports:
        eg = r.engle_granger
        ecm = r.ecm
        p = eg.coint_pvalue
        p_color = C_HIGH if p < 0.05 else (C_MOD if p < 0.10 else C_TEXT3)
        hl = ecm.half_life_days
        hl_txt = f"{hl:,.1f}" if np.isfinite(hl) else "∞"
        hl_color = C_HIGH if np.isfinite(hl) and hl < 60 else C_TEXT2
        lam_color = C_HIGH if ecm.lambda_y < 0 and ecm.lambda_y_tstat < -2 else C_TEXT2
        z = r.spread_zscore
        if abs(z) > 2 and eg.is_cointegrated:
            sig = (
                f"SHORT {eg.y_name}/{eg.x_name}" if z > 0
                else f"LONG {eg.y_name}/{eg.x_name}"
            )
            sig_color = C_LOW if z > 0 else C_HIGH
        elif eg.is_cointegrated:
            sig = f"Watch · z={z:+.2f}"
            sig_color = C_MOD
        else:
            sig = "No cointegration"
            sig_color = C_TEXT3

        pair_cell = (
            _sans(f"{eg.y_name} – {eg.x_name}", color=C_TEXT, weight=600)
            + _sans(f" n={r.n_obs}", color=C_TEXT3, weight=400)
        )
        table_rows.append([
            pair_cell,
            _mono(f"{eg.beta:.3f}", color=C_TEXT),
            _mono(f"{p:.4f}", color=p_color),
            _mono(f"{ecm.lambda_y:+.3f}", color=lam_color),
            _mono(hl_txt, color=hl_color),
            _sans(sig, color=sig_color, weight=700),
        ])
    wsj_market_table(headers, table_rows)

    top = reports[0]
    if top.engle_granger.is_cointegrated:
        # Spread-strategy walk-forward backtest — gross vs NET of an assumed
        # turnover cost (R103). Trading the spread means trading both legs each
        # entry/exit; this shows whether the mean-reversion edge survives that.
        try:
            from engine.cointegration import walk_forward_backtest  # noqa: PLC0415
            sa = all_series.get(top.y, pd.Series(dtype=float)).dropna()
            sb = all_series.get(top.x, pd.Series(dtype=float)).dropna()
            common = sa.index.intersection(sb.index)
            if len(common) >= 160:
                yv = sa.loc[common].astype(float); yv.name = top.y
                xv = sb.loc[common].astype(float); xv.name = top.x
                bt = walk_forward_backtest(yv, xv)
                metric_card_row([
                    {"label": "Spread Sharpe (gross)",
                     "value": f"{bt.sharpe:+.2f}",
                     "accent": C_HIGH if bt.sharpe > 0 else C_LOW,
                     "sublabel": f"{bt.n_trades} trades · walk-forward"},
                    {"label": "Spread Sharpe (net of cost)",
                     "value": f"{bt.net_sharpe:+.2f}",
                     "accent": C_HIGH if bt.net_sharpe > 0 else C_LOW,
                     "sublabel": "after assumed turnover cost (both legs)"},
                    {"label": "Info Ratio vs B&H",
                     "value": f"{bt.information_ratio:+.2f}",
                     "accent": C_HIGH if bt.information_ratio > 0 else C_LOW,
                     "sublabel": f"{top.y} – {top.x}"},
                ], columns=3)
        except Exception as exc:
            logger.debug("coint backtest spotlight skipped: {}", exc)
        try:
            spread = top.spread
            mu = float(spread.mean())
            sd = float(spread.std(ddof=0))
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=spread.index, y=spread.values, mode="lines",
                name=f"{top.y} – β·{top.x}",
                line={"color": C_ACCENT, "width": 1.5},
            ))
            fig.add_hline(y=mu, line_dash="dash", line_color=C_TEXT3)
            fig.add_hline(y=mu + 2 * sd, line_dash="dot", line_color=C_LOW)
            fig.add_hline(y=mu - 2 * sd, line_dash="dot", line_color=C_HIGH)
            apply_dark_layout(
                fig,
                height=260,
                title=(
                    f"Cointegrating spread: {top.y} – β·{top.x}  "
                    f"(β={top.engle_granger.beta:.3f}, "
                    f"half-life {top.ecm.half_life_days:.0f}d)"
                ),
            )
            st.plotly_chart(fig, use_container_width=True, key="coint_spread_spotlight")
        except Exception as exc:
            logger.debug("coint spotlight error: {}", exc)

    sources = _baltic_sources_list(baltic_sources, ("BDI",))
    if sources:
        st.markdown(source_footer(sources), unsafe_allow_html=True)


# ── Section 5: Forward Curve ───────────────────────────────────────────────────

def _render_forward_curve(
    all_series: dict[str, pd.Series],
    baltic_sources: dict[str, DataSource] | None = None,
) -> None:
    section_header("BDI Forward Curve",
                   "FFA-implied curve — 12-month outlook (modeled when live unavailable)")
    try:
        bdi = all_series.get("BDI", pd.Series(dtype=float)).dropna()
        spot = float(bdi.iloc[-1]) if len(bdi) else 1200
        months = list(range(1, 13))
        labels = [(dt.date.today() + dt.timedelta(days=30 * m)).strftime("%b %Y") for m in months]
        rng = np.random.default_rng(_seed_from_id("FFA_BDI"))
        scenario = rng.choice(["contango", "backwardation", "flat"])
        if scenario == "contango":
            curve = [spot * (1 + 0.012 * m + rng.normal(0, 0.005)) for m in months]
            scenario_label = "Mild Contango"
            scenario_sub = "Market expects higher rates"
        elif scenario == "backwardation":
            curve = [spot * (1 - 0.008 * m + rng.normal(0, 0.005)) for m in months]
            scenario_label = "Backwardation"
            scenario_sub = "Market expects rate softening"
        else:
            curve = [spot * (1 + rng.normal(0, 0.007)) for _ in months]
            scenario_label = "Flat Curve"
            scenario_sub = "Market neutral"
        scenario_color = (
            C_MOD if scenario == "contango"
            else (C_LOW if scenario == "backwardation" else C_TEXT2)
        )

        metric_card_row(
            [
                {
                    "label": "FFA Scenario",
                    "value": scenario_label,
                    "accent": scenario_color,
                    "delta": scenario_sub,
                    "delta_color": scenario_color,
                    "sublabel": f"Spot: {spot:,.0f} pts",
                },
            ],
            columns=1,
        )
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=labels, y=[spot] * len(labels), name="Spot",
            line=dict(color=C_TEXT3, width=1, dash="dot"),
        ))
        fig.add_trace(go.Bar(
            x=labels, y=curve, name="FFA Implied",
            marker_color=[C_HIGH if v >= spot else C_LOW for v in curve],
            opacity=0.7,
        ))
        fig.add_trace(go.Scatter(
            x=labels, y=curve, name="Curve",
            line=dict(color=C_ACCENT, width=2),
            mode="lines+markers",
            marker=dict(size=6, color=C_ACCENT),
        ))
        apply_dark_layout(
            fig, height=320,
            title="BDI FFA Forward Curve — 12 Months",
            yaxis=dict(title="BDI Points"),
            barmode="overlay",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Source: Modeled FFA curve based on current spot. "
            "Live FFA data requires subscription API."
        )
        sources = _baltic_sources_list(baltic_sources, ("BDI",))
        if sources:
            st.markdown(source_footer(sources), unsafe_allow_html=True)
    except Exception as exc:
        logger.error("Forward curve error: {}", exc)
        st.error(f"Forward curve error: {exc}")


# ── Section 6: Cross-Asset Dashboard ──────────────────────────────────────────

def _mock_macro_series(label: str, base: float, days: int = 504) -> pd.Series:
    rng = np.random.default_rng(_seed_from_id(label))
    returns = rng.normal(0.0002, 0.01, days)
    prices = base * np.exp(np.cumsum(returns))
    return pd.Series(prices,
                     index=pd.date_range(end=dt.date.today(), periods=days, freq="B"),
                     name=label)


def _render_cross_asset(
    all_series: dict[str, pd.Series],
    baltic_sources: dict[str, DataSource] | None = None,
) -> None:
    section_header("Cross-Asset Dashboard", "Shipping indices vs macro drivers — 2Y history")
    try:
        bdi = all_series.get("BDI", _mock_macro_series("BDI", 1200)).dropna().iloc[-504:]
        wci = all_series.get("WCI", _mock_macro_series("WCI", 3200)).dropna().iloc[-504:]
        bdti = all_series.get("BDTI", _mock_macro_series("BDTI", 800)).dropna().iloc[-504:]
        scfi = all_series.get("SCFI", _mock_macro_series("SCFI", 2800)).dropna().iloc[-504:]

        _iron_ore = _try_yfinance("SCCO", "2y")
        iron_ore = _iron_ore if _iron_ore is not None else _mock_macro_series("IronOre", 120)
        _oil = _try_yfinance("CL=F", "2y")
        oil = _oil if _oil is not None else _mock_macro_series("Oil", 75)
        retail = _mock_macro_series("US_Retail", 700000)
        cn_exports = _mock_macro_series("CN_Exports", 300000)

        pairs = [
            dict(title="BDI vs Iron Ore Price",  idx=bdi,  macro=iron_ore,   idx_name="BDI",  macro_name="Iron Ore Proxy"),
            dict(title="WCI vs US Retail Sales", idx=wci,  macro=retail,     idx_name="WCI",  macro_name="US Retail Sales"),
            dict(title="BDTI vs Oil Price (WTI)",idx=bdti, macro=oil,        idx_name="BDTI", macro_name="WTI Crude"),
            dict(title="SCFI vs China Exports",  idx=scfi, macro=cn_exports, idx_name="SCFI", macro_name="China Exports"),
        ]
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=[p["title"] for p in pairs],
            specs=[[{"secondary_y": True}, {"secondary_y": True}],
                   [{"secondary_y": True}, {"secondary_y": True}]],
        )
        positions = [(1, 1), (1, 2), (2, 1), (2, 2)]
        for pair, (row, col) in zip(pairs, positions):
            try:
                idx_s = pair["idx"].dropna()
                mac_s = pair["macro"].dropna()
                common = idx_s.index.intersection(mac_s.index)
                if len(common) < 5:
                    continue
                fig.add_trace(
                    go.Scatter(x=common, y=idx_s.loc[common], name=pair["idx_name"],
                               line=dict(color=C_ACCENT, width=1.5),
                               showlegend=(row == 1 and col == 1)),
                    row=row, col=col, secondary_y=False,
                )
                fig.add_trace(
                    go.Scatter(x=common, y=mac_s.loc[common], name=pair["macro_name"],
                               line=dict(color=C_MOD, width=1.5, dash="dash"),
                               showlegend=(row == 1 and col == 1)),
                    row=row, col=col, secondary_y=True,
                )
            except Exception as exc:
                logger.debug("Cross-asset subplot error: {}", exc)
        apply_dark_layout(
            fig,
            height=520,
            margin=dict(l=50, r=50, t=60, b=40),
            hovermode="x unified",
        )
        fig.update_annotations(font_color=C_TEXT2, font_size=11)
        for axis in fig.layout:
            if axis.startswith("xaxis") or axis.startswith("yaxis"):
                fig.layout[axis].update(
                    gridcolor="rgba(232,230,225,0.04)",
                    zerolinecolor="rgba(232,230,225,0.06)",
                )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Blue = shipping index (left axis)  |  Amber dashed = macro indicator (right axis)"
        )
        sources = _baltic_sources_list(baltic_sources, ("BDI",))
        if sources:
            st.markdown(source_footer(sources), unsafe_allow_html=True)
    except Exception as exc:
        logger.error("Cross-asset dashboard error: {}", exc)
        st.error(f"Cross-asset error: {exc}")


# ── Section 7: Methodology ────────────────────────────────────────────────────

def _render_methodology() -> None:
    section_header("Index Methodology Reference", "Calculation methods, coverage, and publishers")
    try:
        group_colors = {"Dry Bulk": C_ACCENT, "Container": C_HIGH, "Tanker": C_LOW}
        headers = ["Index", "Method", "Freq", "Routes", "Publisher"]
        table_rows = []
        for entry in _METHODOLOGY:
            accent = _INDEX_COLORS.get(entry["index"], C_TEXT3)
            group = next((m["group"] for m in _INDICES if m["id"] == entry["index"]), "")
            gc = group_colors.get(group, C_TEXT3)
            table_rows.append([
                _sans(entry["index"], color=accent, weight=700),
                _sans(entry["method"], color=C_TEXT2),
                badge(entry["freq"], color=C_TEXT3),
                _mono(str(entry["routes"]), color=C_TEXT3),
                _sans(entry["publisher"], color=gc),
            ])
        wsj_market_table(headers, table_rows)
    except Exception as exc:
        logger.error("Methodology section error: {}", exc)
        st.error(f"Methodology error: {exc}")


# ── Main entry point ──────────────────────────────────────────────────────────

def render(freight_data=None, macro_data=None, stock_data=None, *args, **kwargs) -> None:
    """Render Bloomberg-style shipping indices dashboard."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('indices'):
        page_header(
            title="Shipping Indices",
            subtitle="Baltic Exchange · Drewry · Freightos · Shanghai Shipping Exchange",
            badge_text="INDICES",
            badge_color=C_ACCENT,
        )

        try:
            with st.spinner("Loading index data..."):
                all_series = _cached_series()
        except Exception as exc:
            logger.error("Failed to load series: {}", exc)
            all_series = {}
            for idx in _INDICES:
                try:
                    all_series[idx["id"]] = _mock_series(idx)
                except Exception:
                    pass

        try:
            baltic_sources = _cached_baltic_sources()
        except Exception as exc:
            logger.debug("Baltic sources unavailable: {}", exc)
            baltic_sources = {}

        # Each section carries the editorial label shown on the divider that
        # *precedes* the next section, so the page reads as a sequence of named
        # passages rather than an unbroken scroll.
        sections = [
            ("Index dashboard",  _render_index_dashboard,  "Multi-Index Comparison"),
            ("Multi-index",      _render_multi_index_chart, "BDI Deep Dive"),
            ("BDI deep dive",    _render_bdi_deep_dive,     "Spread Analysis"),
            ("Spread analysis",  _render_spread_analysis,   "Cointegration"),
            ("Cointegration",    _render_cointegration,     "Forward Curve"),
            ("Forward curve",    _render_forward_curve,     "Cross-Asset"),
            ("Cross-asset",      _render_cross_asset,       "Methodology"),
        ]
        for i, (name, fn, next_label) in enumerate(sections):
            try:
                fn(all_series, baltic_sources)
            except Exception as exc:
                logger.error("{} error: {}", name, exc)
                st.error(f"{name} error: {exc}")
            section_divider(next_label)

        try:
            _render_methodology()
        except Exception as exc:
            logger.error("Methodology error: {}", exc)
            st.error(f"Methodology error: {exc}")
