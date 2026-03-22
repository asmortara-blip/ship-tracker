"""ui/tab_indices.py — Bloomberg-style Shipping Indices Dashboard.

Sections:
  1. Index Dashboard   — KPI cards for all major shipping indices
  2. Multi-Index Chart — Normalized overlay (up to 5 indices), time-range selector
  3. BDI Deep Dive     — Component breakdown, historical context, BDI vs S&P500
  4. Spread Analysis   — Key spreads with historical percentile
  5. Forward Curve     — FFA-implied BDI forward curve (live or mock)
  6. Cross-Asset       — Indices vs macro (2×2 Plotly subplots)
  7. Methodology       — Reference table of index definitions

Function signature: render(freight_data=None, macro_data=None, stock_data=None)
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger
from plotly.subplots import make_subplots

# ── Design tokens ──────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_PLOT_LAYOUT = dict(
    paper_bgcolor=C_CARD,
    plot_bgcolor=C_SURFACE,
    font=dict(color=C_TEXT, size=12),
    margin=dict(l=50, r=20, t=40, b=40),
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=C_BORDER, borderwidth=1),
    xaxis=dict(gridcolor=C_BORDER, zerolinecolor=C_BORDER),
    yaxis=dict(gridcolor=C_BORDER, zerolinecolor=C_BORDER),
)

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
    "BDI": C_ACCENT, "BCI": "#06b6d4", "BPI": "#8b5cf6", "BSI": "#f59e0b", "BHSI": "#f97316",
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
    # Add a slow mean-reversion pull
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
    """Attempt live fetch for BDI; fall back to mock for all others."""
    if idx["id"] == "BDI":
        live = _try_yfinance("^BALT", "5y") or _try_yfinance("BDI.L", "5y")
        if live is not None and len(live) > 20:
            return live.rename("BDI")
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


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_series() -> dict[str, pd.Series]:
    return _build_all_series()


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


# ── Card renderer ──────────────────────────────────────────────────────────────

def _pct_badge(pct: float) -> str:
    color = C_HIGH if pct > 0 else (C_LOW if pct < 0 else C_TEXT3)
    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "—")
    return f'<span style="color:{color};font-size:11px">{arrow} {abs(pct):.1f}%</span>'


def _kpi_card_html(idx: dict, stats: dict) -> str:
    if not stats:
        return (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:14px;min-height:130px;">'
            f'<div style="color:{C_TEXT2};font-size:11px;text-transform:uppercase;letter-spacing:1px">'
            f'{idx["label"]}</div>'
            f'<div style="color:{C_TEXT3};margin-top:12px;font-size:13px">No data</div></div>'
        )
    now = stats["now"]
    day_pct = stats["day_pct"]
    color = C_HIGH if day_pct > 0 else (C_LOW if day_pct < 0 else C_TEXT3)
    arrow = "▲" if day_pct > 0 else ("▼" if day_pct < 0 else "—")
    accent = _INDEX_COLORS.get(idx["id"], C_ACCENT)
    unit = idx["unit"]
    val_str = f"{now:,.0f}" if now >= 100 else f"{now:,.1f}"
    day_str = f'{arrow} {abs(stats["day_chg"]):.0f} ({abs(day_pct):.1f}%)'
    wow  = _pct_badge(stats["wow_pct"])
    mom  = _pct_badge(stats["mom_pct"])
    yoy  = _pct_badge(stats["yoy_pct"])
    above_str = f'{stats["above_avg_pct"]:+.1f}% vs 5Y avg'
    above_color = C_HIGH if stats["above_avg_pct"] > 0 else C_LOW
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:14px 16px;border-top:3px solid {accent}">'
        f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:4px">'
        f'{idx["group"]}</div>'
        f'<div style="color:{C_TEXT};font-size:12px;font-weight:600;margin-bottom:8px">{idx["label"]}</div>'
        f'<div style="color:{C_TEXT};font-size:22px;font-weight:700;line-height:1">{val_str}'
        f'<span style="color:{C_TEXT3};font-size:11px;margin-left:4px">{unit}</span></div>'
        f'<div style="color:{color};font-size:11px;margin-top:4px">{day_str}</div>'
        f'<div style="display:flex;gap:10px;margin-top:8px;flex-wrap:wrap">'
        f'<span style="color:{C_TEXT3};font-size:10px">WoW {wow}</span>'
        f'<span style="color:{C_TEXT3};font-size:10px">MoM {mom}</span>'
        f'<span style="color:{C_TEXT3};font-size:10px">YoY {yoy}</span>'
        f'</div>'
        f'<div style="color:{above_color};font-size:10px;margin-top:6px">{above_str}</div>'
        f'</div>'
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = f'<div style="color:{C_TEXT3};font-size:12px;margin-top:2px">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div style="margin:28px 0 12px">'
        f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;letter-spacing:0.3px">{title}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


# ── Section 1: Index Dashboard ─────────────────────────────────────────────────

def _render_index_dashboard(all_series: dict[str, pd.Series]) -> None:
    _section_header("Index Dashboard", "Live snapshot of major shipping benchmarks")
    rows = [
        [m for m in _INDICES if m["group"] == "Dry Bulk"],
        [m for m in _INDICES if m["group"] == "Container"],
        [m for m in _INDICES if m["group"] == "Tanker"],
    ]
    for row_indices in rows:
        cols = st.columns(len(row_indices))
        for col, idx in zip(cols, row_indices):
            try:
                series = all_series.get(idx["id"], pd.Series(dtype=float))
                stats = _get_stats(series)
                col.markdown(_kpi_card_html(idx, stats), unsafe_allow_html=True)
            except Exception as exc:
                logger.warning("Card render error {}: {}", idx["id"], exc)
                col.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px">'
                    f'<div style="color:{C_TEXT2}">{idx["label"]}</div>'
                    f'<div style="color:{C_LOW};font-size:11px">Error</div></div>',
                    unsafe_allow_html=True,
                )


# ── Section 2: Multi-Index Chart ───────────────────────────────────────────────

def _render_multi_index_chart(all_series: dict[str, pd.Series]) -> None:
    _section_header("Multi-Index Comparison", "Normalized to 100 at start date — overlay up to 5 indices")
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
        time_range = st.selectbox("Range", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3, key="mi_range")
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
                hovertemplate=f"<b>{label}</b><br>%{{x|%b %d, %Y}}<br>Normalized: %{{y:.1f}}<extra></extra>",
            ))
        fig.add_hline(y=100, line_dash="dot", line_color=C_TEXT3, line_width=1)
        fig.update_layout(
            **_PLOT_LAYOUT,
            title=dict(text=f"Normalized Index Performance — {time_range}", font=dict(size=13, color=C_TEXT2)),
            height=380,
            hovermode="x unified",
            yaxis_title="Index (base = 100)",
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        logger.error("Multi-index chart error: {}", exc)
        st.error(f"Chart error: {exc}")


# ── Section 3: BDI Deep Dive ───────────────────────────────────────────────────

def _render_bdi_deep_dive(all_series: dict[str, pd.Series]) -> None:
    _section_header("BDI Deep Dive", "Component breakdown, historical context, and macro correlation")
    bdi = all_series.get("BDI", pd.Series(dtype=float)).dropna()
    bci = all_series.get("BCI", pd.Series(dtype=float)).dropna()
    bpi = all_series.get("BPI", pd.Series(dtype=float)).dropna()
    bsi = all_series.get("BSI", pd.Series(dtype=float)).dropna()
    bhsi = all_series.get("BHSI", pd.Series(dtype=float)).dropna()

    # Component rates card row
    components = [
        dict(name="Capesize (BCI)", weight="40%", value=bci.iloc[-1] if len(bci) else 1800, color=C_ACCENT),
        dict(name="Panamax (BPI)", weight="30%", value=bpi.iloc[-1] if len(bpi) else 1200, color="#8b5cf6"),
        dict(name="Supramax (BSI)", weight="15%", value=bsi.iloc[-1] if len(bsi) else 900, color=C_MOD),
        dict(name="Handysize (BHSI)", weight="15%", value=bhsi.iloc[-1] if len(bhsi) else 600, color="#f97316"),
    ]
    cols = st.columns(4)
    for col, comp in zip(cols, components):
        col.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:12px 14px;'
            f'border-left:4px solid {comp["color"]}">'
            f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase">{comp["name"]}</div>'
            f'<div style="color:{C_TEXT};font-size:20px;font-weight:700;margin-top:4px">{comp["value"]:,.0f}</div>'
            f'<div style="color:{C_TEXT3};font-size:10px">Weight: {comp["weight"]}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)

    # BDI historical context
    try:
        avg_5y = float(bdi.mean()) if len(bdi) > 0 else 1000
        current = float(bdi.iloc[-1]) if len(bdi) > 0 else 1000
        pct_vs_avg = _pct(current, avg_5y)
        avg_color = C_HIGH if pct_vs_avg > 0 else C_LOW
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:12px 16px;'
            f'margin-bottom:16px;display:flex;align-items:center;gap:16px">'
            f'<div><span style="color:{C_TEXT2};font-size:12px">BDI Historical Context: </span>'
            f'<span style="color:{avg_color};font-size:13px;font-weight:600">'
            f'Currently {pct_vs_avg:+.1f}% {"above" if pct_vs_avg >= 0 else "below"} 5-year average</span>'
            f' &nbsp;<span style="color:{C_TEXT3};font-size:11px">(5Y avg: {avg_5y:,.0f} pts)</span></div></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.debug("BDI context error: {}", exc)

    # BDI chart + S&P500 scatter side by side
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
                    fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                ))
                fig.add_trace(go.Scatter(
                    x=s.index, y=avg_line, name="5Y Average",
                    line=dict(color=C_MOD, width=1, dash="dash"),
                ))
                fig.update_layout(**_PLOT_LAYOUT, height=280, title="BDI — 2-Year History", yaxis_title="BDI Points")
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
                fig2.update_layout(
                    **_PLOT_LAYOUT, height=280,
                    title=f"BDI vs S&P 500 (corr: {corr:.2f})",
                    xaxis_title="S&P 500", yaxis_title="BDI",
                )
                st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info("Insufficient overlapping data for correlation chart.")
        except Exception as exc:
            logger.error("BDI/SP500 scatter error: {}", exc)
            st.info("Correlation chart unavailable.")


# ── Section 4: Spread Analysis ─────────────────────────────────────────────────

def _render_spread_analysis(all_series: dict[str, pd.Series]) -> None:
    _section_header("Index Spread Analysis", "Key spreads with historical percentile ranking")
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
                Current=f"{current:+,.0f}",
                Avg=f"{avg:+,.0f}",
                Percentile=pctile,
            ))
        except Exception as exc:
            logger.debug("Spread error {}/{}: {}", sd["a"], sd["b"], exc)
    if not rows:
        st.info("Spread data unavailable.")
        return
    df = pd.DataFrame(rows)
    # Color code the percentile
    def _pctile_color(p: float) -> str:
        if p >= 80:
            return C_LOW
        if p >= 60:
            return C_MOD
        if p <= 20:
            return C_HIGH
        return C_TEXT2

    header_cols = st.columns([2, 2.5, 1, 1, 1.5])
    for col, h in zip(header_cols, ["Spread", "Description", "Current", "5Y Avg", "Percentile"]):
        col.markdown(f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;font-weight:600">{h}</div>', unsafe_allow_html=True)
    st.markdown(f'<hr style="border-color:{C_BORDER};margin:4px 0 8px">', unsafe_allow_html=True)
    for row in rows:
        try:
            p = row["Percentile"]
            pc = _pctile_color(p)
            bar_w = int(p)
            rcols = st.columns([2, 2.5, 1, 1, 1.5])
            rcols[0].markdown(f'<div style="color:{C_TEXT};font-size:12px;font-weight:600">{row["Spread"]}</div>', unsafe_allow_html=True)
            rcols[1].markdown(f'<div style="color:{C_TEXT2};font-size:11px">{row["Description"]}</div>', unsafe_allow_html=True)
            rcols[2].markdown(f'<div style="color:{C_TEXT};font-size:12px">{row["Current"]}</div>', unsafe_allow_html=True)
            rcols[3].markdown(f'<div style="color:{C_TEXT3};font-size:12px">{row["Avg"]}</div>', unsafe_allow_html=True)
            rcols[4].markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<div style="flex:1;background:{C_SURFACE};border-radius:3px;height:6px">'
                f'<div style="width:{bar_w}%;background:{pc};border-radius:3px;height:6px"></div></div>'
                f'<span style="color:{pc};font-size:11px;min-width:32px">{p:.0f}th</span></div>',
                unsafe_allow_html=True,
            )
        except Exception as exc:
            logger.debug("Spread row error: {}", exc)


# ── Section 5: Forward Curve ───────────────────────────────────────────────────

def _render_forward_curve(all_series: dict[str, pd.Series]) -> None:
    _section_header("BDI Forward Curve", "FFA-implied curve — 12-month outlook (mock if live unavailable)")
    try:
        bdi = all_series.get("BDI", pd.Series(dtype=float)).dropna()
        spot = float(bdi.iloc[-1]) if len(bdi) else 1200
        # Mock FFA curve with realistic shape
        months = list(range(1, 13))
        labels = [(dt.date.today() + dt.timedelta(days=30 * m)).strftime("%b %Y") for m in months]
        rng = np.random.default_rng(_seed_from_id("FFA_BDI"))
        # Randomly pick contango or backwardation for the mock
        scenario = rng.choice(["contango", "backwardation", "flat"])
        if scenario == "contango":
            curve = [spot * (1 + 0.012 * m + rng.normal(0, 0.005)) for m in months]
            scenario_label = "Mild Contango (market expects higher rates)"
        elif scenario == "backwardation":
            curve = [spot * (1 - 0.008 * m + rng.normal(0, 0.005)) for m in months]
            scenario_label = "Backwardation (market expects rate softening)"
        else:
            curve = [spot * (1 + rng.normal(0, 0.007)) for _ in months]
            scenario_label = "Flat Curve (market neutral)"
        scenario_color = C_MOD if scenario == "contango" else (C_LOW if scenario == "backwardation" else C_TEXT2)
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;padding:10px 14px;'
            f'margin-bottom:12px;color:{scenario_color};font-size:12px">'
            f'Scenario: <b>{scenario_label}</b> &nbsp; '
            f'<span style="color:{C_TEXT3}">(Spot: {spot:,.0f} pts)</span></div>',
            unsafe_allow_html=True,
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
        fig.update_layout(
            **_PLOT_LAYOUT, height=320,
            title="BDI FFA Forward Curve — 12 Months",
            yaxis_title="BDI Points",
            barmode="overlay",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(
            f'<div style="color:{C_TEXT3};font-size:10px;text-align:right;margin-top:-8px">'
            f'Source: Mock FFA curve based on current spot. Live FFA data requires subscription API.</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error("Forward curve error: {}", exc)
        st.error(f"Forward curve error: {exc}")


# ── Section 6: Cross-Asset Dashboard ──────────────────────────────────────────

def _mock_macro_series(label: str, base: float, days: int = 504) -> pd.Series:
    rng = np.random.default_rng(_seed_from_id(label))
    returns = rng.normal(0.0002, 0.01, days)
    prices = base * np.exp(np.cumsum(returns))
    return pd.Series(prices, index=pd.date_range(end=dt.date.today(), periods=days, freq="B"), name=label)


def _render_cross_asset(all_series: dict[str, pd.Series]) -> None:
    _section_header("Cross-Asset Dashboard", "Shipping indices vs macro drivers — 2Y history")
    try:
        bdi = all_series.get("BDI", _mock_macro_series("BDI", 1200)).dropna().iloc[-504:]
        wci = all_series.get("WCI", _mock_macro_series("WCI", 3200)).dropna().iloc[-504:]
        bdti = all_series.get("BDTI", _mock_macro_series("BDTI", 800)).dropna().iloc[-504:]
        scfi = all_series.get("SCFI", _mock_macro_series("SCFI", 2800)).dropna().iloc[-504:]

        iron_ore = _try_yfinance("SCCO", "2y") or _mock_macro_series("IronOre", 120)
        oil = _try_yfinance("CL=F", "2y") or _mock_macro_series("Oil", 75)
        retail = _mock_macro_series("US_Retail", 700000)
        cn_exports = _mock_macro_series("CN_Exports", 300000)

        pairs = [
            dict(title="BDI vs Iron Ore Price", idx=bdi, macro=iron_ore, idx_name="BDI", macro_name="Iron Ore Proxy"),
            dict(title="WCI vs US Retail Sales", idx=wci, macro=retail, idx_name="WCI", macro_name="US Retail Sales"),
            dict(title="BDTI vs Oil Price (WTI)", idx=bdti, macro=oil, idx_name="BDTI", macro_name="WTI Crude"),
            dict(title="SCFI vs China Exports", idx=scfi, macro=cn_exports, idx_name="SCFI", macro_name="China Exports"),
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
                               line=dict(color=C_ACCENT, width=1.5), showlegend=(row == 1 and col == 1)),
                    row=row, col=col, secondary_y=False,
                )
                fig.add_trace(
                    go.Scatter(x=common, y=mac_s.loc[common], name=pair["macro_name"],
                               line=dict(color=C_MOD, width=1.5, dash="dash"), showlegend=(row == 1 and col == 1)),
                    row=row, col=col, secondary_y=True,
                )
            except Exception as exc:
                logger.debug("Cross-asset subplot error: {}", exc)
        fig.update_layout(
            paper_bgcolor=C_CARD, plot_bgcolor=C_SURFACE,
            font=dict(color=C_TEXT, size=11),
            height=520, margin=dict(l=50, r=50, t=60, b=40),
            hovermode="x unified",
            showlegend=True,
            legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=C_BORDER),
        )
        fig.update_annotations(font_color=C_TEXT2, font_size=11)
        for axis in fig.layout:
            if axis.startswith("xaxis") or axis.startswith("yaxis"):
                fig.layout[axis].update(gridcolor=C_BORDER, zerolinecolor=C_BORDER)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(
            f'<div style="color:{C_TEXT3};font-size:10px;text-align:right;margin-top:-8px">'
            f'Blue = shipping index (left axis) &nbsp;|&nbsp; Amber dashed = macro indicator (right axis)</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error("Cross-asset dashboard error: {}", exc)
        st.error(f"Cross-asset error: {exc}")


# ── Section 7: Methodology ────────────────────────────────────────────────────

def _render_methodology() -> None:
    _section_header("Index Methodology Reference", "Calculation methods, coverage, and publishers")
    try:
        header_cols = st.columns([1, 3, 1, 1, 2])
        for col, h in zip(header_cols, ["Index", "Method", "Freq", "Routes", "Publisher"]):
            col.markdown(
                f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;font-weight:600">{h}</div>',
                unsafe_allow_html=True,
            )
        st.markdown(f'<hr style="border-color:{C_BORDER};margin:4px 0 6px">', unsafe_allow_html=True)
        for entry in _METHODOLOGY:
            try:
                accent = _INDEX_COLORS.get(entry["index"], C_TEXT3)
                group = next((m["group"] for m in _INDICES if m["id"] == entry["index"]), "")
                group_colors = {"Dry Bulk": C_ACCENT, "Container": C_HIGH, "Tanker": C_LOW}
                gc = group_colors.get(group, C_TEXT3)
                mcols = st.columns([1, 3, 1, 1, 2])
                mcols[0].markdown(
                    f'<div style="color:{accent};font-size:12px;font-weight:700">{entry["index"]}</div>',
                    unsafe_allow_html=True,
                )
                mcols[1].markdown(
                    f'<div style="color:{C_TEXT2};font-size:11px">{entry["method"]}</div>',
                    unsafe_allow_html=True,
                )
                mcols[2].markdown(
                    f'<div style="color:{C_TEXT3};font-size:11px">{entry["freq"]}</div>',
                    unsafe_allow_html=True,
                )
                mcols[3].markdown(
                    f'<div style="color:{C_TEXT3};font-size:11px">{entry["routes"]}</div>',
                    unsafe_allow_html=True,
                )
                mcols[4].markdown(
                    f'<div style="color:{gc};font-size:11px">{entry["publisher"]}</div>',
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.debug("Methodology row error: {}", exc)
    except Exception as exc:
        logger.error("Methodology section error: {}", exc)
        st.error(f"Methodology error: {exc}")


# ── Main entry point ──────────────────────────────────────────────────────────

def render(freight_data=None, macro_data=None, stock_data=None) -> None:
    """Render Bloomberg-style shipping indices dashboard."""
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_SURFACE},{C_BG});'
            f'border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px;margin-bottom:20px">'
            f'<div style="display:flex;align-items:center;gap:12px">'
            f'<div style="background:{C_ACCENT};width:4px;height:40px;border-radius:2px"></div>'
            f'<div>'
            f'<div style="color:{C_TEXT};font-size:20px;font-weight:700;letter-spacing:0.5px">Shipping Indices</div>'
            f'<div style="color:{C_TEXT3};font-size:12px;margin-top:2px">'
            f'Baltic Exchange · Drewry · Freightos · Shanghai Shipping Exchange</div>'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.debug("Header render error: {}", exc)

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
        _render_index_dashboard(all_series)
    except Exception as exc:
        logger.error("Index dashboard error: {}", exc)
        st.error(f"Index dashboard error: {exc}")

    st.divider()

    try:
        _render_multi_index_chart(all_series)
    except Exception as exc:
        logger.error("Multi-index chart error: {}", exc)
        st.error(f"Chart error: {exc}")

    st.divider()

    try:
        _render_bdi_deep_dive(all_series)
    except Exception as exc:
        logger.error("BDI deep dive error: {}", exc)
        st.error(f"BDI deep dive error: {exc}")

    st.divider()

    try:
        _render_spread_analysis(all_series)
    except Exception as exc:
        logger.error("Spread analysis error: {}", exc)
        st.error(f"Spread analysis error: {exc}")

    st.divider()

    try:
        _render_forward_curve(all_series)
    except Exception as exc:
        logger.error("Forward curve error: {}", exc)
        st.error(f"Forward curve error: {exc}")

    st.divider()

    try:
        _render_cross_asset(all_series)
    except Exception as exc:
        logger.error("Cross-asset error: {}", exc)
        st.error(f"Cross-asset error: {exc}")

    st.divider()

    try:
        _render_methodology()
    except Exception as exc:
        logger.error("Methodology error: {}", exc)
        st.error(f"Methodology error: {exc}")
