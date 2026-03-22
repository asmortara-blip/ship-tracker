from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import yaml
from dotenv import load_dotenv
from loguru import logger

load_dotenv()


def _get_api_health() -> dict:
    """Check health of each data source by looking at cache files."""
    import time
    health = {}
    cache_dir = Path("cache")

    sources = {
        "yfinance":      {"pattern": "*stock*",     "ttl_hours": 1},
        "FRED":          {"pattern": "*fred*",      "ttl_hours": 24},
        "WorldBank":     {"pattern": "*worldbank*", "ttl_hours": 168},
        "Trade/WITS":    {"pattern": "*wits*",      "ttl_hours": 168},
        "Freight/FBX":   {"pattern": "*fbx*",       "ttl_hours": 24},
        "AIS/Synthetic": {"pattern": "*ais*",       "ttl_hours": 6},
    }

    for source, cfg_src in sources.items():
        files = (
            list(cache_dir.glob(cfg_src["pattern"] + ".parquet"))
            if cache_dir.exists()
            else []
        )
        if files:
            newest = max(files, key=lambda f: f.stat().st_mtime)
            age_hours = (time.time() - newest.stat().st_mtime) / 3600
            fresh = age_hours < cfg_src["ttl_hours"]
            health[source] = {
                "status": "fresh" if fresh else "stale",
                "age_hours": round(age_hours, 1),
                "icon": "🟢" if fresh else "🟡",
            }
        else:
            health[source] = {"status": "no_cache", "age_hours": None, "icon": "🔴"}
    return health


def _age_label(age_hours: float | None) -> str:
    if age_hours is None:
        return "No cache"
    if age_hours < 0.1:
        return "Fresh"
    if age_hours < 1.0:
        return f"{int(age_hours * 60)}m ago"
    if age_hours < 24.0:
        return f"{round(age_hours, 1)}h ago"
    return f"{round(age_hours / 24, 1)}d ago"


# ── Page config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Ship Tracker — Global Shipping Intelligence",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Config ────────────────────────────────────────────────────────────────
@st.cache_resource
def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        logger.error(f"config.yaml not found at {config_path}")
        return {}
    except yaml.YAMLError as exc:
        logger.error(f"config.yaml malformed: {exc}")
        return {}


cfg = load_config()

# ── Inject CSS ────────────────────────────────────────────────────────────
try:
    from ui.styles import inject_global_css
    inject_global_css()
except Exception as _css_err:
    logger.warning(f"CSS load error: {_css_err}")


# ── Data loading (cached) ─────────────────────────────────────────────────
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


@st.cache_data(ttl=86400, show_spinner=False)  # 24h TTL
def get_fundamentals_data() -> dict:
    """Fetch Alpha Vantage fundamentals if key is configured."""
    try:
        from data.alphavantage_feed import fetch_all_shipping_fundamentals, alphavantage_available
        if not alphavantage_available():
            return {}
        return fetch_all_shipping_fundamentals()
    except Exception as e:
        logger.warning("Fundamentals data unavailable: %s", e)
        return {}


# ── Sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    # Brand header
    st.markdown("""
    <div style="padding:12px 0 16px 0; border-bottom:1px solid rgba(255,255,255,0.07); margin-bottom:12px">
        <div style="font-size:1.45rem; font-weight:900; color:#f1f5f9; letter-spacing:-0.03em; line-height:1">
            🚢 Ship Tracker
        </div>
        <div style="font-size:0.7rem; color:#475569; margin-top:4px; text-transform:uppercase; letter-spacing:0.1em">
            Global Shipping Intelligence
        </div>
    </div>
    """, unsafe_allow_html=True)

    lookback = st.slider("Lookback period (days)", 30, 180, 90, step=15)

    if st.button("🔄 Refresh All Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # API health
    st.caption("**Data Sources**")
    _health = _get_api_health()
    try:
        _fred_key = st.secrets.get("FRED_API_KEY", os.getenv("FRED_API_KEY", ""))
    except Exception:
        _fred_key = os.getenv("FRED_API_KEY", "")
    if not _fred_key:
        _health["FRED"] = {"status": "no_key", "age_hours": None, "icon": "🔴"}
    for _src_name, _info in _health.items():
        _detail = (
            "No API key" if _info["status"] == "no_key"
            else "Not loaded" if _info["status"] == "no_cache"
            else ("Stale: " + _age_label(_info.get("age_hours"))) if _info["status"] == "stale"
            else _age_label(_info.get("age_hours"))
        )
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04)">'
            f'<span style="font-size:0.73rem;color:#f1f5f9">{_info["icon"]} {_src_name}</span>'
            f'<span style="font-size:0.68rem;color:#64748b">{_detail}</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()
    st.caption("Free data sources only · All times UTC")

    # Placeholders filled after analysis
    sidebar_signal_placeholder = st.empty()
    sidebar_watchlist_placeholder = st.empty()
    sidebar_bottom_placeholder = st.empty()


# ── Default values ────────────────────────────────────────────────────────
momentum_ranks = []
convergence_events = []
volatility_reports = {}
alerts = []
fx_rates = {}
fleet_data = None

# ── Load all data ─────────────────────────────────────────────────────────
with st.spinner("Loading data..."):
    try:
        stock_data  = get_stock_data(lookback)
        macro_data  = get_macro_data(lookback + 90)
        wb_data     = get_wb_data()
        ais_data    = get_ais_data()
        trade_data  = get_trade_data()
        freight_data = get_freight_data(lookback + 30)
    except Exception as exc:
        st.error(f"Data loading error: {exc}")
        stock_data = macro_data = wb_data = ais_data = trade_data = freight_data = {}

    try:
        from data.currency_feed import fetch_fx_rates
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
        port_results  = analyze_all_ports(trade_data, ais_data, wb_data)
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
    port_results = route_results = insights = correlation_results = []
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

try:
    from processing.news_sentiment import fetch_all_news
    from data.cache_manager import CacheManager as _CacheManager
    news_articles = fetch_all_news(_CacheManager())
except Exception as exc:
    logger.warning(f"News sentiment unavailable: {exc}")
    news_articles = []

try:
    from processing.leading_indicators import compute_leading_indicator_score
    leading_score = compute_leading_indicator_score(macro_data)
except Exception as exc:
    logger.warning(f"Leading indicators unavailable: {exc}")
    leading_score = {}

try:
    from processing.eta_predictor import predict_all_routes as predict_all_etas
    etas = predict_all_etas(port_results, freight_data, macro_data)
except Exception as exc:
    logger.warning(f"ETA predictor unavailable: {exc}")
    etas = []

try:
    from engine.narration_engine import NarrationEngine
    narration = NarrationEngine().build_weekly_digest(port_results, route_results, insights, macro_data, freight_data)
except Exception as exc:
    logger.warning(f"Narration engine unavailable: {exc}")
    narration = {}


# ── Dynamic sidebar (signal pulse + watchlist) ────────────────────────────
try:
    import plotly.graph_objects as go
except Exception:
    go = None

with sidebar_signal_placeholder.container():
    st.divider()
    st.caption("**Signal Pulse**")
    if insights and go is not None:
        avg_score = sum(i.score for i in insights) / len(insights)
        gauge_color = "#10b981" if avg_score >= 0.70 else ("#f59e0b" if avg_score >= 0.55 else "#ef4444")
        fig_gauge = go.Figure(go.Pie(
            values=[avg_score, 1 - avg_score], hole=0.7,
            marker_colors=[gauge_color, "rgba(255,255,255,0.05)"],
            textinfo="none", hoverinfo="skip",
        ))
        fig_gauge.update_layout(
            showlegend=False, margin=dict(l=0, r=0, t=0, b=0), height=110,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            annotations=[dict(
                text=f"<b>{avg_score:.0%}</b><br><span style='font-size:9px'>Health</span>",
                x=0.5, y=0.5, font=dict(size=15, color=gauge_color), showarrow=False,
            )],
        )
        st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar": False})
        scores_sorted = sorted([i.score for i in insights], reverse=True)
        fig_spark = go.Figure(go.Scatter(
            y=scores_sorted, mode="lines",
            line=dict(color="#3b82f6", width=2),
            fill="tozeroy", fillcolor="rgba(59,130,246,0.12)",
        ))
        fig_spark.update_layout(
            height=70, margin=dict(l=0, r=0, t=4, b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(visible=False), yaxis=dict(visible=False, range=[0, 1]),
            showlegend=False,
        )
        st.plotly_chart(fig_spark, use_container_width=True, config={"displayModeBar": False})
        for ins in insights[:3]:
            sc = "#10b981" if ins.score >= 0.70 else ("#f59e0b" if ins.score >= 0.55 else "#94a3b8")
            title_short = ins.title[:44] + ("…" if len(ins.title) > 44 else "")
            st.markdown(f"""
            <div style="background:#1a2235;border-left:3px solid {sc};
                        border-radius:6px;padding:7px 10px;margin-bottom:6px">
                <div style="font-size:0.74rem;font-weight:600;color:#f1f5f9;line-height:1.3">{title_short}</div>
                <div style="font-size:0.67rem;color:{sc};margin-top:2px;font-weight:700">{ins.score:.0%} · {ins.action}</div>
            </div>""", unsafe_allow_html=True)
    elif insights:
        avg_score = sum(i.score for i in insights) / len(insights)
        st.caption(f"Health: {avg_score:.0%}")
        for ins in insights[:3]:
            st.caption(f"{ins.score:.0%} · {ins.title[:44]}")
    else:
        st.caption("No active signals")

with sidebar_watchlist_placeholder.container():
    st.divider()
    st.caption("**Watchlist**")
    shipping_tickers = cfg.get("shipping_stocks", [])[:5]
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
                <div style="display:flex;justify-content:space-between;align-items:center;
                            padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04)">
                    <span style="font-size:0.75rem;font-weight:600;color:#f1f5f9;font-family:monospace">{ticker}</span>
                    <span style="font-size:0.75rem;color:{color};font-weight:700">{arrow} {chg_pct:+.1%}</span>
                </div>""", unsafe_allow_html=True)
    else:
        for ticker in shipping_tickers:
            st.caption(f"— {ticker}")

with sidebar_bottom_placeholder.container():
    st.divider()
    cache_size = sum(f.stat().st_size for f in Path("cache").rglob("*.parquet")) if Path("cache").exists() else 0
    cache_mb = cache_size / (1024 * 1024)
    try:
        from utils.helpers import now_iso
        st.caption(f"📦 Cache: {cache_mb:.1f} MB")
        st.caption(f"🕐 {now_iso()}")
    except Exception:
        st.caption(f"📦 Cache: {cache_mb:.1f} MB")


# ── Quick-stat helpers ────────────────────────────────────────────────────
try:
    from utils.helpers import now_iso, trend_label
except Exception:
    import datetime
    def now_iso() -> str:
        return datetime.datetime.utcnow().isoformat()
    def trend_label(pct: float) -> str:
        return "Rising" if pct > 0.01 else ("Falling" if pct < -0.01 else "Stable")

port_count   = len(port_results)  if port_results  else 25
route_count  = len(route_results) if route_results else 17
insight_count = len(insights)     if insights      else 0
high_count   = sum(1 for i in insights if i.score >= 0.70) if insights else 0

top_score  = max((i.score for i in insights), default=0.0)
avg_demand = (sum(getattr(p, "demand_score", 0.5) for p in port_results) / len(port_results)
              if port_results else 0.5)

freight_trend = "Stable"
if freight_data:
    try:
        import pandas as pd
        all_vals = []
        for v in freight_data.values():
            if isinstance(v, pd.DataFrame) and not v.empty:
                all_vals.extend(v[v.columns[0]].dropna().tolist())
        if len(all_vals) >= 10:
            half = len(all_vals) // 2
            fha = sum(all_vals[:half]) / half
            sha = sum(all_vals[half:]) / (len(all_vals) - half)
            freight_trend = trend_label((sha - fha) / fha if fha != 0 else 0)
    except Exception:
        pass

sc_health = "Healthy" if insight_count > 0 and high_count >= (insight_count // 3) else "Stressed"
if insight_count == 0:
    sc_health = "Stressed"

def _pill_rgb(val, high_thr, low_thr=None):
    if low_thr and val < low_thr:
        return "239,68,68"
    return "16,185,129" if val >= high_thr else "245,158,11"

top_rgb   = _pill_rgb(top_score, 0.70, 0.55)
dem_rgb   = _pill_rgb(avg_demand, 0.65, 0.45)
frt_rgb   = "16,185,129" if freight_trend == "Rising" else ("245,158,11" if freight_trend == "Stable" else "239,68,68")
sc_rgb    = "16,185,129" if sc_health == "Healthy" else "239,68,68"
live_rgb  = "16,185,129" if stock_data else "239,68,68"
live_lbl  = "● LIVE" if stock_data else "● OFFLINE"
alert_cnt = len(alerts) if alerts else 0
refresh_ts = now_iso()[:19].replace("T", " ") + " UTC"


# ── Hero header ───────────────────────────────────────────────────────────
st.markdown(f"""
<style>
@keyframes pulse-live {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
@keyframes hero-line {{ 0%{{background-position:0% 50%}} 50%{{background-position:100% 50%}} 100%{{background-position:0% 50%}} }}
.live-badge {{ animation: pulse-live 2s ease-in-out infinite; }}
.hero-rule {{ height:2px; border-radius:2px;
    background: linear-gradient(90deg,#3b82f6,#10b981,#8b5cf6,#3b82f6);
    background-size:300% 300%;
    animation: hero-line 5s ease infinite; }}
</style>

<div style="padding:20px 0 20px 0; margin-bottom:4px">
    <!-- Title row -->
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
        <div>
            <div style="font-size:0.65rem;text-transform:uppercase;letter-spacing:0.18em;
                        color:#475569;margin-bottom:6px">
                🚢 GLOBAL SHIPPING INTELLIGENCE PLATFORM
            </div>
            <div style="font-size:2.1rem;font-weight:900;color:#f1f5f9;
                        letter-spacing:-0.045em;line-height:1.05">
                Container Market<br>
                <span style="background:linear-gradient(135deg,#3b82f6,#10b981);
                             -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                             background-clip:text;">Intelligence</span>
            </div>
            <div style="font-size:0.8rem;color:#64748b;margin-top:6px">
                {port_count} ports &nbsp;·&nbsp; {route_count} routes &nbsp;·&nbsp;
                {insight_count} active signals &nbsp;·&nbsp; {high_count} high-conviction
            </div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">
            <span class="live-badge" style="
                background:rgba({live_rgb},0.14);color:rgb({live_rgb});
                border:1px solid rgba({live_rgb},0.35);
                padding:4px 14px;border-radius:999px;font-size:0.73rem;font-weight:700;
                letter-spacing:0.06em">{live_lbl}</span>
            <span style="font-size:0.66rem;color:#475569;font-family:monospace">{refresh_ts}</span>
        </div>
    </div>
    <!-- Animated rule -->
    <div class="hero-rule" style="margin-bottom:12px"></div>
    <!-- Stat pills -->
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <span style="background:rgba({top_rgb},0.1);color:rgb({top_rgb});
                     border:1px solid rgba({top_rgb},0.25);
                     padding:4px 13px;border-radius:999px;font-size:0.71rem;font-weight:600">
            Top Signal: {top_score:.0%}
        </span>
        <span style="background:rgba({dem_rgb},0.1);color:rgb({dem_rgb});
                     border:1px solid rgba({dem_rgb},0.25);
                     padding:4px 13px;border-radius:999px;font-size:0.71rem;font-weight:600">
            Avg Port Demand: {avg_demand:.0%}
        </span>
        <span style="background:rgba({frt_rgb},0.1);color:rgb({frt_rgb});
                     border:1px solid rgba({frt_rgb},0.25);
                     padding:4px 13px;border-radius:999px;font-size:0.71rem;font-weight:600">
            Freight Trend: {freight_trend}
        </span>
        <span style="background:rgba({sc_rgb},0.1);color:rgb({sc_rgb});
                     border:1px solid rgba({sc_rgb},0.25);
                     padding:4px 13px;border-radius:999px;font-size:0.71rem;font-weight:600">
            SC Health: {sc_health}
        </span>
        {'<span style="background:rgba(239,68,68,0.12);color:#ef4444;border:1px solid rgba(239,68,68,0.3);padding:4px 13px;border-radius:999px;font-size:0.71rem;font-weight:700">⚠ ' + str(alert_cnt) + ' Alerts</span>' if alert_cnt else ''}
    </div>
</div>
""", unsafe_allow_html=True)


# ── Market ticker tape ────────────────────────────────────────────────────
ticker_items = []
if stock_data:
    for ticker, df in stock_data.items():
        if df is not None and not df.empty and "close" in df.columns:
            close = df["close"]
            cur = float(close.iloc[-1])
            prv = float(close.iloc[-2]) if len(close) > 1 else cur
            chg = (cur - prv) / prv if prv != 0 else 0
            arrow = "▲" if chg > 0 else "▼"
            clr = "#10b981" if chg > 0 else "#ef4444"
            ticker_items.append(f'<span style="color:{clr};margin:0 8px">{ticker} {arrow} {chg:+.1%}</span>')
if freight_data:
    try:
        import pandas as pd
        for route_name, df in freight_data.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                vals = df[df.columns[0]].dropna()
                if len(vals) >= 2:
                    chg = (float(vals.iloc[-1]) - float(vals.iloc[-2])) / float(vals.iloc[-2]) if float(vals.iloc[-2]) != 0 else 0
                    arrow = "▲" if chg > 0 else "▼"
                    clr = "#10b981" if chg > 0 else "#ef4444"
                    ticker_items.append(f'<span style="color:{clr};margin:0 8px">{str(route_name)[:12]} {arrow} {chg:+.1%}</span>')
    except Exception:
        pass

ticker_html = "  ·  ".join(ticker_items) if ticker_items else "Loading market data..."
components.html(f"""
<div style="background:#0d1117;border:1px solid rgba(255,255,255,0.07);
            border-radius:8px;padding:8px 16px;overflow:hidden;white-space:nowrap;
            font-family:'SF Mono',monospace;font-size:11.5px;margin-bottom:2px">
    <span style="color:#334155;margin-right:12px;font-weight:700;font-size:10px;
                 text-transform:uppercase;letter-spacing:0.1em">MARKET</span>
    {ticker_html}
</div>
""", height=36)


# ── Section navigation helpers ────────────────────────────────────────────
SECTIONS = [
    ("dashboard",    "🏠", "Dashboard",          "Overview, scorecard & live data"),
    ("markets",      "📈", "Markets & Signals",   "Alpha, correlations & derivatives"),
    ("ports_routes", "🚢", "Ports & Routes",      "Demand, congestion & ETA"),
    ("carriers",     "🏢", "Carriers & Ops",      "Fleet, cargo & booking"),
    ("trade_macro",  "🌍", "Trade & Macro",       "Geopolitics, tariffs & macro"),
    ("supply_chain", "🔗", "Supply Chain",        "Visibility, network & intermodal"),
    ("risk",         "⚠️", "Risk & Compliance",   "Weather, regulatory & market cycle"),
    ("intelligence", "🤖", "Intelligence",        "News, AI assistant & sustainability"),
    ("reports",      "📋", "Reports",             "Investor & summary reports"),
]

SECTION_COLORS = {
    "dashboard":    "#3b82f6",
    "markets":      "#10b981",
    "ports_routes": "#06b6d4",
    "carriers":     "#8b5cf6",
    "trade_macro":  "#f59e0b",
    "supply_chain": "#ec4899",
    "risk":         "#ef4444",
    "intelligence": "#a78bfa",
    "reports":      "#64748b",
}

if "nav_section" not in st.session_state:
    st.session_state["nav_section"] = "dashboard"

# Inject section nav CSS
st.markdown("""<style>
.sec-nav-btn > div > button {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 8px !important;
    color: #94a3b8 !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    text-align: left !important;
    transition: all 0.15s ease !important;
    padding: 8px 12px !important;
    margin-bottom: 3px !important;
}
.sec-nav-btn > div > button:hover {
    background: rgba(255,255,255,0.07) !important;
    color: #f1f5f9 !important;
    border-color: rgba(255,255,255,0.14) !important;
}
.sec-nav-active > div > button {
    background: rgba(59,130,246,0.15) !important;
    border-color: rgba(59,130,246,0.4) !important;
    border-left: 3px solid #3b82f6 !important;
    color: #f1f5f9 !important;
    font-weight: 600 !important;
}
</style>""", unsafe_allow_html=True)

# Render nav in sidebar
with st.sidebar:
    st.divider()
    st.caption("**Navigation**")
    for sec_key, sec_icon, sec_label, sec_desc in SECTIONS:
        active = st.session_state["nav_section"] == sec_key
        css_class = "sec-nav-active" if active else "sec-nav-btn"
        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
        btn_label = f"{sec_icon}  {sec_label}"
        if alerts and sec_key == "risk":
            btn_label += f"  ({len(alerts)})"
        if st.button(btn_label, key=f"nav_{sec_key}", use_container_width=True):
            st.session_state["nav_section"] = sec_key
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

active_section = st.session_state.get("nav_section", "dashboard")

# Section breadcrumb
sec_info = next((s for s in SECTIONS if s[0] == active_section), SECTIONS[0])
sec_color = SECTION_COLORS.get(active_section, "#3b82f6")
st.markdown(f"""
<div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;
            padding:10px 16px;background:rgba(26,34,53,0.6);
            border:1px solid rgba(255,255,255,0.07);border-radius:10px;
            border-left:3px solid {sec_color}">
    <span style="font-size:1.3rem">{sec_info[1]}</span>
    <div>
        <div style="font-size:0.88rem;font-weight:700;color:#f1f5f9">{sec_info[2]}</div>
        <div style="font-size:0.72rem;color:#64748b">{sec_info[3]}</div>
    </div>
</div>
""", unsafe_allow_html=True)


# ── Section routing ───────────────────────────────────────────────────────

# ── 1. Dashboard ──────────────────────────────────────────────────────────
if active_section == "dashboard":
    t0, t1, t2, t3 = st.tabs(["🌍 Overview", "📊 Scorecard", "📡 Live Feed", "🩺 Data Health"])
    with t0:
        try:
            from ui.tab_overview import render as _r
            _r(port_results, route_results, insights)
        except Exception as e:
            st.error(f"Overview error: {e}")
    with t1:
        try:
            from ui.tab_scorecard import render as _r
            _r(port_results, route_results, insights, freight_data, macro_data, stock_data)
        except Exception as e:
            st.error(f"Scorecard error: {e}")
    with t2:
        try:
            from ui.tab_live_feed import render as _r
            _r(port_results, route_results, insights, freight_data, stock_data, macro_data)
        except Exception as e:
            st.error(f"Live Feed error: {e}")
    with t3:
        try:
            from ui.tab_data_health import render as _r
            _r(port_results, route_results, freight_data, macro_data, stock_data, trade_data, ais_data)
        except Exception as e:
            st.error(f"Data Health error: {e}")

# ── 2. Markets & Signals ──────────────────────────────────────────────────
elif active_section == "markets":
    t0, t1, t2, t3, t4, t5, t6 = st.tabs([
        "📈 Markets", "⚡ Alpha", "🔥 Results",
        "📊 Indices", "📜 Derivatives", "🎭 Scenarios", "🔮 Monte Carlo",
    ])
    with t0:
        try:
            from ui.tab_markets import render as _r
            _r(correlation_results, stock_data, lookback)
        except Exception as e:
            st.error(f"Markets error: {e}")
    with t1:
        try:
            from ui.tab_alpha import render as _r
            _r(route_results, port_results, freight_data, macro_data, stock_data, insights)
        except Exception as e:
            st.error(f"Alpha error: {e}")
    with t2:
        try:
            from ui.tab_results import render as _r
            _r(insights)
        except Exception as e:
            st.error(f"Results error: {e}")
    with t3:
        try:
            from ui.tab_indices import render as _r
            _r(macro_data=macro_data, freight_data=freight_data, stock_data=stock_data, lookback_days=lookback)
        except Exception as e:
            st.error(f"Indices error: {e}")
    with t4:
        try:
            from ui.tab_derivatives import render as _r
            _r(route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Derivatives error: {e}")
    with t5:
        try:
            from ui.tab_scenarios import render as _r
            _r(port_results, route_results, macro_data)
        except Exception as e:
            st.error(f"Scenarios error: {e}")
    with t6:
        try:
            from ui.tab_monte_carlo import render as _r
            _r(freight_data, route_results)
        except Exception as e:
            st.error(f"Monte Carlo error: {e}")

# ── 3. Ports & Routes ─────────────────────────────────────────────────────
elif active_section == "ports_routes":
    t0, t1, t2, t3, t4, t5 = st.tabs([
        "🏗️ Port Demand", "🏭 Port Monitor", "🚢 Routes",
        "⏱️ ETA Predictor", "🚧 Congestion", "🛤️ Emerging Routes",
    ])
    with t0:
        try:
            from ui.tab_port_demand import render as _r
            _r(port_results)
        except Exception as e:
            st.error(f"Port Demand error: {e}")
    with t1:
        try:
            from ui.tab_port_monitor import render as _r
            _r(port_results, ais_data, freight_data)
        except Exception as e:
            st.error(f"Port Monitor error: {e}")
    with t2:
        try:
            from ui.tab_routes import render as _r
            _r(route_results, freight_data, forecasts)
        except Exception as e:
            st.error(f"Routes error: {e}")
    with t3:
        try:
            from ui.tab_eta import render as _r
            _r(port_results, route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"ETA Predictor error: {e}")
    with t4:
        try:
            from ui.tab_congestion import render as _r
            _r(port_results, ais_data, freight_data, macro_data)
        except Exception as e:
            st.error(f"Congestion error: {e}")
    with t5:
        try:
            from ui.tab_emerging_routes import render as _r
            _r(route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Emerging Routes error: {e}")

# ── 4. Carriers & Ops ────────────────────────────────────────────────────
elif active_section == "carriers":
    t0, t1, t2, t3, t4, t5 = st.tabs([
        "🏢 Carriers", "⚓ Fleet", "📦 Equipment",
        "📦 Cargo", "📋 Booking", "⛽ Bunker Fuel",
    ])
    with t0:
        try:
            from ui.tab_carriers import render as _r
            _r(route_results, freight_data, stock_data)
        except Exception as e:
            st.error(f"Carriers error: {e}")
    with t1:
        try:
            from ui.tab_fleet import render as _r
            _r(freight_data=freight_data, macro_data=macro_data)
        except Exception as e:
            st.error(f"Fleet error: {e}")
    with t2:
        try:
            from ui.tab_equipment import render as _r
            _r(route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Equipment error: {e}")
    with t3:
        try:
            from ui.tab_cargo import render as _r
            _r(trade_data=trade_data, wb_data=wb_data, route_results=route_results)
        except Exception as e:
            st.error(f"Cargo error: {e}")
    with t4:
        try:
            from ui.tab_booking import render as _r
            _r(port_results, route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Booking error: {e}")
    with t5:
        try:
            from ui.tab_bunker import render as _r
            _r(freight_data, macro_data, route_results)
        except Exception as e:
            st.error(f"Bunker Fuel error: {e}")

# ── 5. Trade & Macro ─────────────────────────────────────────────────────
elif active_section == "trade_macro":
    t0, t1, t2, t3, t4, t5 = st.tabs([
        "📉 Macro", "⚔️ Trade War", "🌍 Geopolitical",
        "🚧 Chokepoints", "💰 Trade Finance", "🛒 E-Commerce",
    ])
    with t0:
        try:
            from ui.tab_macro import render as _r
            _r(macro_data, freight_data, stock_data)
        except Exception as e:
            st.error(f"Macro error: {e}")
    with t1:
        try:
            from ui.tab_trade_war import render as _r
            _r(route_results, port_results, freight_data, macro_data, trade_data)
        except Exception as e:
            st.error(f"Trade War error: {e}")
    with t2:
        try:
            from ui.tab_geopolitical import render as _r
            _r(route_results, port_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Geopolitical error: {e}")
    with t3:
        try:
            from ui.tab_chokepoints import render as _r
            _r(route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Chokepoints error: {e}")
    with t4:
        try:
            from ui.tab_finance import render as _r
            _r(freight_data, macro_data, route_results, stock_data)
        except Exception as e:
            st.error(f"Trade Finance error: {e}")
    with t5:
        try:
            from ui.tab_ecommerce import render as _r
            _r(trade_data, freight_data, macro_data, route_results)
        except Exception as e:
            st.error(f"E-Commerce error: {e}")

# ── 6. Supply Chain ───────────────────────────────────────────────────────
elif active_section == "supply_chain":
    t0, t1, t2, t3, t4 = st.tabs([
        "🏥 Supply Chain", "👁️ Visibility", "🔄 Intermodal",
        "🌐 Network", "🧩 Attribution",
    ])
    with t0:
        try:
            from ui.tab_supply_chain import render as _r
            _r(port_results, route_results, freight_data, macro_data, insights)
        except Exception as e:
            st.error(f"Supply Chain error: {e}")
    with t1:
        try:
            from ui.tab_visibility import render as _r
            _r(port_results, route_results, trade_data, freight_data)
        except Exception as e:
            st.error(f"Visibility error: {e}")
    with t2:
        try:
            from ui.tab_intermodal import render as _r
            _r(route_results, freight_data, macro_data, port_results)
        except Exception as e:
            st.error(f"Intermodal error: {e}")
    with t3:
        try:
            from ui.tab_network import render as _r
            _r(port_results, route_results, freight_data, trade_data)
        except Exception as e:
            st.error(f"Network error: {e}")
    with t4:
        try:
            from ui.tab_attribution import render as _r
            _r(stock_data, freight_data, macro_data, route_results)
        except Exception as e:
            st.error(f"Attribution error: {e}")

# ── 7. Risk & Compliance ──────────────────────────────────────────────────
elif active_section == "risk":
    t0, t1, t2, t3, t4 = st.tabs([
        "⚠️ Risk Matrix", "🌩️ Weather", "🛡️ Compliance",
        "🔄 Market Cycle", "📋 Fundamentals",
    ])
    with t0:
        try:
            from ui.tab_risk_matrix import render as _r
            _r(route_results, port_results, macro_data)
        except Exception as e:
            st.error(f"Risk Matrix error: {e}")
    with t1:
        try:
            from ui.tab_weather import render as _r
            _r(port_results, route_results, freight_data)
        except Exception as e:
            st.error(f"Weather error: {e}")
    with t2:
        try:
            from ui.tab_compliance import render as _r
            _r(route_results, port_results, macro_data)
        except Exception as e:
            st.error(f"Compliance error: {e}")
    with t3:
        try:
            from ui.tab_cycle import render as _r
            _r(freight_data, macro_data, stock_data, route_results)
        except Exception as e:
            st.error(f"Market Cycle error: {e}")
    with t4:
        try:
            from ui.tab_fundamentals import render as _r
            _r(stock_data, freight_data, macro_data)
        except Exception as e:
            st.error(f"Fundamentals error: {e}")

# ── 8. Intelligence ───────────────────────────────────────────────────────
elif active_section == "intelligence":
    t0, t1, t2, t3 = st.tabs([
        "📰 News & Sentiment", "🔬 Deep Dive",
        "🤖 AI Assistant", "🌿 Sustainability",
    ])
    with t0:
        try:
            from ui.tab_news import render as _r
            _r(news_articles=news_articles, port_results=port_results,
               route_results=route_results, insights=insights)
        except Exception as e:
            st.error(f"News error: {e}")
    with t1:
        try:
            from ui.tab_deep_dive import render as _r
            _r(route_results=route_results, freight_data=freight_data,
               port_results=port_results, macro_data=macro_data,
               stock_data=stock_data, forecasts=forecasts, insights=insights)
        except Exception as e:
            st.error(f"Deep Dive error: {e}")
    with t2:
        try:
            from ui.tab_assistant import render as _r
            _r(port_results, route_results, insights, freight_data, macro_data, stock_data)
        except Exception as e:
            st.error(f"AI Assistant error: {e}")
    with t3:
        try:
            from ui.tab_sustainability import render as _r
            _r()
        except Exception as e:
            st.error(f"Sustainability error: {e}")


# ── 9. Reports ────────────────────────────────────────────────────────────
elif active_section == "reports":
    from ui import tab_report
    fundamentals_data = get_fundamentals_data()
    (t0,) = st.tabs(["📋 Investor Report"])
    with t0:
        try:
            tab_report.render(port_results, route_results, insights, freight_data, macro_data, stock_data, fundamentals_data=fundamentals_data)
        except Exception as e:
            st.error(f"Investor Report error: {e}")


# ── Footer ────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:40px;padding:20px 0 8px 0;
            border-top:1px solid rgba(255,255,255,0.06);
            display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
    <div style="font-size:0.68rem;color:#334155">
        <span style="font-weight:700;color:#475569">🚢 Ship Tracker</span>
        &nbsp;·&nbsp; Data: UN Comtrade · FRED · World Bank · yfinance · Freightos FBX
    </div>
    <div style="font-size:0.66rem;color:#334155">
        Built with Streamlit &nbsp;·&nbsp; Free public APIs only &nbsp;·&nbsp; Not financial advice
    </div>
</div>
""", unsafe_allow_html=True)
