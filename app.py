from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import yaml
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ── Page config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Cargo Ship Container Tracker",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Config ────────────────────────────────────────────────────────────────
@st.cache_resource
def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


cfg = load_config()

# Inject global CSS design system
from ui.styles import inject_global_css
inject_global_css()


# ── Data loading (cached) ──────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="Loading stock data...")
def get_stock_data(lookback_days: int):
    from data.cache_manager import CacheManager
    from data.stock_feed import fetch_all_stocks
    tickers = cfg.get("shipping_stocks", []) + cfg.get("sector_etfs", [])
    cache = CacheManager()
    return fetch_all_stocks(tickers, lookback_days, cache, ttl_hours=cfg["cache"]["stocks_ttl_hours"])


@st.cache_data(ttl=86400, show_spinner="Loading macro data...")
def get_macro_data(lookback_days: int):
    from data.cache_manager import CacheManager
    from data.fred_feed import fetch_macro_series
    cache = CacheManager()
    return fetch_macro_series(lookback_days, cache, ttl_hours=cfg["cache"]["fred_ttl_hours"])


@st.cache_data(ttl=604800, show_spinner="Loading World Bank data...")
def get_wb_data():
    from data.cache_manager import CacheManager
    from data.worldbank_feed import fetch_port_throughput
    cache = CacheManager()
    return fetch_port_throughput(cache, ttl_hours=cfg["cache"]["worldbank_ttl_hours"])


@st.cache_data(ttl=21600, show_spinner="Loading vessel positions...")
def get_ais_data():
    from data.cache_manager import CacheManager
    from data.ais_feed import fetch_vessel_counts
    cache = CacheManager()
    return fetch_vessel_counts(cache, ttl_hours=cfg["cache"]["ais_ttl_hours"])


@st.cache_data(ttl=604800, show_spinner="Loading trade flow data...")
def get_trade_data(lookback_months: int = 3):
    from data.cache_manager import CacheManager
    from data.comtrade_feed import fetch_all_ports
    cache = CacheManager()
    return fetch_all_ports(lookback_months, cache, ttl_hours=cfg["cache"]["comtrade_ttl_hours"])


@st.cache_data(ttl=86400, show_spinner="Loading freight rates...")
def get_freight_data(lookback_days: int):
    from data.cache_manager import CacheManager
    from data.freight_scraper import fetch_fbx_rates
    cache = CacheManager()
    return fetch_fbx_rates(lookback_days, cache, ttl_hours=cfg["cache"]["freight_ttl_hours"])


# ── Sidebar top controls (before data load) ────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="padding: 4px 0 12px 0">
        <div style="font-size:1.3rem; font-weight:800; color:#f1f5f9; letter-spacing:-0.02em">
            🚢 Ship Tracker
        </div>
        <div style="font-size:0.75rem; color:#64748b; margin-top:2px; text-transform:uppercase; letter-spacing:0.06em">
            Cargo Container Intelligence
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    lookback = st.slider("Lookback period (days)", 30, 180, 90, step=15)

    st.divider()

    if st.button("🔄 Refresh All Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # Credential status
    st.caption("**API Status**")
    checks = {
        "FRED": bool(os.getenv("FRED_API_KEY")),
        "Comtrade": bool(os.getenv("COMTRADE_API_KEY")),
        "AISHub": bool(os.getenv("AISHUB_USERNAME")),
        "yfinance": True,  # No key needed
    }
    for name, ok in checks.items():
        icon = "🟢" if ok else "🔴"
        st.caption(f"{icon} {name}")

    st.divider()
    st.caption("Free data sources only")
    st.caption("All times UTC")

    # Placeholders for dynamic sidebar sections (filled after data loads)
    sidebar_signal_placeholder = st.empty()
    sidebar_watchlist_placeholder = st.empty()
    sidebar_bottom_placeholder = st.empty()


# ── Default values for new variables ──────────────────────────────────────
momentum_ranks = []
convergence_events = []
volatility_reports = {}
alerts = []
fx_rates = {}
fleet_data = None

# ── Load all data ─────────────────────────────────────────────────────────
with st.spinner("Loading data..."):
    try:
        stock_data = get_stock_data(lookback)
        macro_data = get_macro_data(lookback + 90)  # Extra history for correlation
        wb_data = get_wb_data()
        ais_data = get_ais_data()
        trade_data = get_trade_data()
        freight_data = get_freight_data(lookback + 30)
    except Exception as exc:
        st.error(f"Data loading error: {exc}")
        stock_data = {}
        macro_data = {}
        wb_data = {}
        ais_data = {}
        trade_data = {}
        freight_data = {}

    try:
        from data.currency_feed import fetch_fx_rates, fetch_fx_history
        fx_rates = fetch_fx_rates()
    except Exception as exc:
        logger.warning(f"FX rates unavailable: {exc}")

    try:
        from processing.fleet_tracker import get_fleet_data
        fleet_data = get_fleet_data()
    except Exception as exc:
        logger.warning(f"Fleet data unavailable: {exc}")

# ── Run analysis ──────────────────────────────────────────────────────────
try:
    with st.spinner("Running analysis..."):
        from ports.demand_analyzer import analyze_all_ports
        from routes.optimizer import optimize_all_routes
        port_results = analyze_all_ports(trade_data, ais_data, wb_data)
        route_results = optimize_all_routes(port_results, freight_data, macro_data)

    with st.spinner("Generating insights..."):
        from engine.scorer import InsightScorer
        insights = InsightScorer(cfg).score_all(port_results, route_results, macro_data, stock_data)

    with st.spinner("Analyzing correlations..."):
        from engine.correlator import ShippingStockCorrelator
        corr_cfg = cfg.get("engine", {}).get("correlation", {})
        correlation_results = ShippingStockCorrelator(
            min_window=corr_cfg.get("min_window_days", 60),
            min_abs_r=corr_cfg.get("min_abs_correlation", 0.40),
            lags_to_test=corr_cfg.get("lag_days_to_test", [0, 7, 14, 21, 30]),
        ).analyze(stock_data, macro_data, freight_data)
except Exception as exc:
    logger.error(f"Analysis pipeline error: {exc}")
    port_results, route_results, insights, correlation_results = [], [], [], []
    st.error(f"Analysis error: {exc}")

try:
    from processing.forecaster import forecast_all_routes
    from processing.seasonal import get_seasonal_adjustment
    from routes.route_registry import get_all_route_ids
    seasonal_adjs = {rid: get_seasonal_adjustment(rid) for rid in get_all_route_ids()}
    forecasts = forecast_all_routes(freight_data, seasonal_adjs)
except Exception as exc:
    logger.error(f"Forecast error: {exc}")
    forecasts = []

try:
    from processing.freight_volatility import analyze_all_routes_volatility
    volatility_reports = analyze_all_routes_volatility(freight_data)
except Exception as exc:
    logger.warning(f"Volatility analysis unavailable: {exc}")

try:
    from engine.momentum_ranker import rank_all_momentum
    momentum_ranks = rank_all_momentum(route_results, port_results, stock_data, freight_data)
except Exception as exc:
    logger.warning(f"Momentum ranking unavailable: {exc}")

try:
    from engine.convergence_tracker import detect_convergence
    convergence_events = detect_convergence(port_results, route_results, macro_data, freight_data)
except Exception as exc:
    logger.warning(f"Convergence tracking unavailable: {exc}")

try:
    from engine.alert_engine import generate_alerts
    alerts = generate_alerts(port_results, route_results, freight_data, macro_data, insights)
except Exception as exc:
    logger.warning(f"Alert engine unavailable: {exc}")


# ── Dynamic sidebar sections (filled after data + analysis) ───────────────
import plotly.graph_objects as go

with sidebar_signal_placeholder.container():
    st.divider()
    st.caption("**Signal Pulse**")

    if insights:
        # Mini donut gauge for overall health score
        avg_score = sum(i.score for i in insights) / len(insights)
        gauge_color = "#10b981" if avg_score >= 0.70 else ("#f59e0b" if avg_score >= 0.55 else "#ef4444")
        fig_gauge = go.Figure(go.Pie(
            values=[avg_score, 1 - avg_score],
            hole=0.7,
            marker_colors=[gauge_color, "rgba(255,255,255,0.05)"],
            textinfo="none",
            hoverinfo="skip",
        ))
        fig_gauge.update_layout(
            showlegend=False,
            margin=dict(l=0, r=0, t=0, b=0),
            height=120,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            annotations=[dict(
                text=f"<b>{avg_score:.0%}</b><br><span style='font-size:9px'>Health</span>",
                x=0.5, y=0.5,
                font=dict(size=16, color=gauge_color),
                showarrow=False,
            )],
        )
        st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar": False})

        # Sparkline of insight score distribution
        scores_sorted = sorted([i.score for i in insights], reverse=True)
        fig_spark = go.Figure(go.Scatter(
            y=scores_sorted,
            mode="lines",
            line=dict(color="#3b82f6", width=2),
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.12)",
        ))
        fig_spark.update_layout(
            height=80,
            margin=dict(l=0, r=0, t=4, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False, range=[0, 1]),
            showlegend=False,
        )
        st.plotly_chart(fig_spark, use_container_width=True, config={"displayModeBar": False})

        # Top 3 insight cards
        for ins in insights[:3]:
            score_color = "#10b981" if ins.score >= 0.70 else ("#f59e0b" if ins.score >= 0.55 else "#94a3b8")
            title_short = ins.title[:45] + ("..." if len(ins.title) > 45 else "")
            st.markdown(f"""
        <div style="background:#1a2235; border-left:3px solid {score_color};
                    border-radius:6px; padding:7px 10px; margin-bottom:6px">
            <div style="font-size:0.75rem; font-weight:600; color:#f1f5f9; line-height:1.3">
                {title_short}</div>
            <div style="font-size:0.68rem; color:{score_color}; margin-top:2px; font-weight:700">
                {ins.score:.0%} · {ins.action}</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.caption("No active signals")

with sidebar_watchlist_placeholder.container():
    st.divider()
    st.caption("**Watchlist**")
    shipping_tickers = cfg.get("shipping_stocks", [])[:5]  # ZIM, MATX, SBLK, DAC, CMRE
    if stock_data:
        for ticker in shipping_tickers:
            df = stock_data.get(ticker)
            if df is not None and not df.empty and "close" in df.columns:
                close = df["close"]
                current = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) > 1 else current
                chg_pct = (current - prev) / prev if prev != 0 else 0
                arrow = "▲" if chg_pct > 0 else ("▼" if chg_pct < 0 else "—")
                color = "#10b981" if chg_pct > 0 else ("#ef4444" if chg_pct < 0 else "#94a3b8")
                st.markdown(f"""
            <div style="display:flex; justify-content:space-between; align-items:center;
                        padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.04)">
                <span style="font-size:0.75rem; font-weight:600; color:#f1f5f9; font-family:monospace">{ticker}</span>
                <span style="font-size:0.75rem; color:{color}; font-weight:700">{arrow} {chg_pct:+.1%}</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        for ticker in shipping_tickers:
            st.caption(f"— {ticker}")

with sidebar_bottom_placeholder.container():
    st.divider()
    cache_size = sum(f.stat().st_size for f in Path("cache").rglob("*.parquet")) if Path("cache").exists() else 0
    cache_mb = cache_size / (1024 * 1024)
    from utils.helpers import now_iso
    st.caption(f"📦 Cache: {cache_mb:.1f} MB")
    st.caption(f"🕐 {now_iso()}")


# ── Page header ───────────────────────────────────────────────────────────
from utils.helpers import now_iso, trend_label

port_count = len(port_results) if port_results else 25
route_count = len(route_results) if route_results else 17
insight_count = len(insights) if insights else 0
high_count = sum(1 for i in insights if i.score >= 0.70) if insights else 0

# Compute quick-stat values
top_score = max((i.score for i in insights), default=0.0) if insights else 0.0
avg_demand = (
    sum(getattr(p, "demand_score", 0.5) for p in port_results) / len(port_results)
    if port_results else 0.5
)

# Freight trend
freight_trend = "Stable"
if freight_data:
    try:
        import pandas as pd
        all_vals = []
        for v in freight_data.values():
            if isinstance(v, pd.DataFrame) and not v.empty:
                col = v.columns[0]
                all_vals.extend(v[col].dropna().tolist())
        if len(all_vals) >= 10:
            half = len(all_vals) // 2
            first_half_avg = sum(all_vals[:half]) / half
            second_half_avg = sum(all_vals[half:]) / (len(all_vals) - half)
            pct_chg = (second_half_avg - first_half_avg) / first_half_avg if first_half_avg != 0 else 0
            freight_trend = trend_label(pct_chg)
    except Exception:
        freight_trend = "Stable"

# SC health
sc_health = "Healthy" if insight_count > 0 and high_count >= (insight_count // 3) else "Stressed"
if insight_count == 0:
    sc_health = "Stressed"

# Color helpers
def score_pill_color(score: float) -> str:
    if score >= 0.70:
        return "#10b981"
    if score >= 0.55:
        return "#f59e0b"
    return "#ef4444"

def demand_pill_color(d: float) -> str:
    if d >= 0.65:
        return "#10b981"
    if d >= 0.45:
        return "#f59e0b"
    return "#ef4444"

freight_pill_color = "#10b981" if freight_trend == "Rising" else ("#f59e0b" if freight_trend == "Stable" else "#ef4444")
sc_pill_color = "#10b981" if sc_health == "Healthy" else "#ef4444"
top_color = score_pill_color(top_score)
avg_color = demand_pill_color(avg_demand)

live_color = "#10b981" if stock_data else "#ef4444"
live_label = "● LIVE" if stock_data else "● OFFLINE"
refresh_ts = now_iso()[:19].replace("T", " ") + " UTC"

st.markdown(f"""
<style>
@keyframes pulse-live {{
    0%   {{ opacity: 1; }}
    50%  {{ opacity: 0.4; }}
    100% {{ opacity: 1; }}
}}
.live-badge {{
    animation: pulse-live 2s ease-in-out infinite;
}}
</style>
<div style="padding: 20px 0 24px 0; border-bottom: 1px solid rgba(255,255,255,0.06); margin-bottom: 24px">
    <!-- Top row: title + live status + refresh timestamp -->
    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:12px">
        <div>
            <div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.15em; color:#475569; margin-bottom:6px">
                🚢 GLOBAL SHIPPING INTELLIGENCE PLATFORM
            </div>
            <div style="font-size:1.8rem; font-weight:900; color:#f1f5f9; letter-spacing:-0.04em; line-height:1">
                Container Market Intelligence
            </div>
            <div style="font-size:0.82rem; color:#64748b; margin-top:5px">
                {port_count} ports · {route_count} routes · {insight_count} active signals · {high_count} high-conviction
            </div>
        </div>
        <div style="display:flex; flex-direction:column; align-items:flex-end; gap:8px">
            <span class="live-badge" style="background:rgba({('16,185,129' if stock_data else '239,68,68')},0.15);
                         color:{live_color}; border:1px solid rgba({('16,185,129' if stock_data else '239,68,68')},0.3);
                         padding:4px 14px; border-radius:999px; font-size:0.75rem; font-weight:700;
                         letter-spacing:0.05em">
                {live_label}
            </span>
            <span style="font-size:0.68rem; color:#475569; font-family:monospace">
                {refresh_ts}
            </span>
        </div>
    </div>
    <!-- Second row: quick metric pills -->
    <div style="display:flex; gap:10px; flex-wrap:wrap">
        <span style="background:rgba({('16,185,129' if top_score >= 0.70 else ('245,158,11' if top_score >= 0.55 else '239,68,68'))},0.1);
                     color:{top_color}; border:1px solid rgba({('16,185,129' if top_score >= 0.70 else ('245,158,11' if top_score >= 0.55 else '239,68,68'))},0.2);
                     padding:4px 12px; border-radius:999px; font-size:0.72rem; font-weight:600">
            Top Signal: {top_score:.0%}
        </span>
        <span style="background:rgba({('16,185,129' if avg_demand >= 0.65 else ('245,158,11' if avg_demand >= 0.45 else '239,68,68'))},0.1);
                     color:{avg_color}; border:1px solid rgba({('16,185,129' if avg_demand >= 0.65 else ('245,158,11' if avg_demand >= 0.45 else '239,68,68'))},0.2);
                     padding:4px 12px; border-radius:999px; font-size:0.72rem; font-weight:600">
            Avg Port Demand: {avg_demand:.0%}
        </span>
        <span style="background:rgba({('16,185,129' if freight_trend == 'Rising' else ('245,158,11' if freight_trend == 'Stable' else '239,68,68'))},0.1);
                     color:{freight_pill_color}; border:1px solid rgba({('16,185,129' if freight_trend == 'Rising' else ('245,158,11' if freight_trend == 'Stable' else '239,68,68'))},0.2);
                     padding:4px 12px; border-radius:999px; font-size:0.72rem; font-weight:600">
            Freight Trend: {freight_trend}
        </span>
        <span style="background:rgba({('16,185,129' if sc_health == 'Healthy' else '239,68,68')},0.1);
                     color:{sc_pill_color}; border:1px solid rgba({('16,185,129' if sc_health == 'Healthy' else '239,68,68')},0.2);
                     padding:4px 12px; border-radius:999px; font-size:0.72rem; font-weight:600">
            SC Health: {sc_health}
        </span>
    </div>
</div>
""", unsafe_allow_html=True)


# ── Ticker tape ───────────────────────────────────────────────────────────
ticker_items = []

if stock_data:
    for ticker, df in stock_data.items():
        if df is not None and not df.empty and "close" in df.columns:
            close = df["close"]
            current = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) > 1 else current
            chg = (current - prev) / prev if prev != 0 else 0
            arrow = "▲" if chg > 0 else "▼"
            color = "#10b981" if chg > 0 else "#ef4444"
            ticker_items.append(
                f'<span style="color:{color}; margin:0 6px">{ticker} {arrow} {chg:+.1%}</span>'
            )

if freight_data:
    try:
        import pandas as pd
        for route_name, df in freight_data.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                col = df.columns[0]
                vals = df[col].dropna()
                if len(vals) >= 2:
                    cur_val = float(vals.iloc[-1])
                    prv_val = float(vals.iloc[-2])
                    chg = (cur_val - prv_val) / prv_val if prv_val != 0 else 0
                    arrow = "▲" if chg > 0 else "▼"
                    color = "#10b981" if chg > 0 else "#ef4444"
                    label = str(route_name)[:12]
                    ticker_items.append(
                        f'<span style="color:{color}; margin:0 6px">{label} {arrow} {chg:+.1%}</span>'
                    )
    except Exception:
        pass

ticker_html = "  ·  ".join(ticker_items) if ticker_items else "Loading market data..."

components.html(f"""
<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.06);
            border-radius:8px; padding:8px 16px; overflow:hidden; white-space:nowrap;
            font-family: 'SF Mono', monospace; font-size:12px; margin-bottom:16px">
    <span style="color:#475569; margin-right:12px; font-weight:700">MARKET</span>
    {ticker_html}
</div>
""", height=38)


# ── Tabs ──────────────────────────────────────────────────────────────────
tab0, tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12, tab13, tab14 = st.tabs([
    "🌍  Overview",
    "🏗️  Port Demand",
    "🚢  Routes",
    "🔥  Results",
    "📈  Markets",
    "🏥  Supply Chain",
    "🎭  Scenarios",
    "🔮  Monte Carlo",
    "🌿  Sustainability",
    "📡  Live Feed",
    "⚠️  Risk Matrix",
    "⚓  Fleet",
    "📦  Cargo",
    "📊  Indices",
    "🔬  Deep Dive",
])

with tab0:
    from ui.tab_overview import render as render_overview
    render_overview(port_results, route_results, insights)

with tab1:
    from ui.tab_port_demand import render as render_ports
    render_ports(port_results)

with tab2:
    from ui.tab_routes import render as render_routes
    render_routes(route_results, freight_data, forecasts)

with tab3:
    from ui.tab_results import render as render_results
    render_results(insights)

with tab4:
    from ui.tab_markets import render as render_markets
    render_markets(correlation_results, stock_data, lookback)

with tab5:
    from ui.tab_supply_chain import render as render_supply_chain
    render_supply_chain(port_results, route_results, freight_data, macro_data, insights)

with tab6:
    from ui.tab_scenarios import render as render_scenarios
    render_scenarios(port_results, route_results, macro_data)

with tab7:
    try:
        from ui.tab_monte_carlo import render as render_monte_carlo
        render_monte_carlo(freight_data, route_results)
    except Exception as exc:
        st.error(f"Monte Carlo tab error: {exc}")

with tab8:
    try:
        from ui.tab_sustainability import render as render_sustainability
        render_sustainability()
    except Exception as exc:
        st.error(f"Sustainability tab error: {exc}")

with tab9:
    try:
        from ui.tab_live_feed import render as render_live_feed
        render_live_feed(port_results, route_results, insights, freight_data, stock_data, macro_data)
    except Exception as exc:
        st.error(f"Live Feed tab error: {exc}")

with tab10:
    try:
        from ui.tab_risk_matrix import render as render_risk_matrix
        render_risk_matrix(route_results, port_results, macro_data)
    except Exception as exc:
        st.error(f"Risk Matrix tab error: {exc}")

with tab11:
    try:
        from ui.tab_fleet import render as render_fleet
        render_fleet(freight_data=freight_data, macro_data=macro_data)
    except Exception as exc:
        st.error(f"Fleet tab error: {exc}")

with tab12:
    try:
        from ui.tab_cargo import render as render_cargo
        render_cargo(trade_data=trade_data, wb_data=wb_data, route_results=route_results)
    except Exception as exc:
        st.error(f"Cargo tab error: {exc}")

with tab13:
    try:
        from ui.tab_indices import render as render_indices
        render_indices(macro_data=macro_data, freight_data=freight_data, stock_data=stock_data, lookback_days=lookback)
    except Exception as exc:
        st.error(f"Indices tab error: {exc}")

with tab14:
    try:
        from ui.tab_deep_dive import render as render_deep_dive
        render_deep_dive(
            route_results=route_results,
            freight_data=freight_data,
            port_results=port_results,
            macro_data=macro_data,
            stock_data=stock_data,
            forecasts=forecasts,
            insights=insights,
        )
    except Exception as exc:
        st.error(f"Deep Dive tab error: {exc}")
