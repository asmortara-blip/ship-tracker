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
            list(cache_dir.rglob(cfg_src["pattern"] + ".parquet"))
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

# ── Logging — rotated file sink + stderr ─────────────────────────────────
# Idempotent across Streamlit hot-reloads. Writes to <project_root>/logs/
# which the Dockerfile mounts via the cache/logs volume.
try:
    from utils.logging_setup import configure_logging
    configure_logging(level="INFO", max_size_mb=10, retention=5)
except Exception as _exc:
    # Don't let a logging setup failure take down the whole app.
    logger.warning(f"Could not configure rotated logging: {_exc}")

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


@st.cache_data(ttl=21600, show_spinner=False)  # 6h TTL
def get_rate_forecasts(freight_data, macro_data):
    try:
        from processing.rate_forecaster import forecast_all_routes
        return forecast_all_routes(freight_data, macro_data)
    except Exception as e:
        logger.warning("Rate forecasting failed: %s", e)
        return {}


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
    # WSJ Brand header
    st.markdown("""
    <div style="padding:10px 0 14px 0;border-bottom:2px solid rgba(232,230,225,0.1);margin-bottom:12px">
        <div style="font-family:'Libre Baskerville','Georgia',serif;font-size:1.2rem;
                    font-weight:700;color:#e8e6e1;letter-spacing:-0.01em;line-height:1.1">
            The Ship Tracker
        </div>
        <div style="font-family:'Libre Franklin',sans-serif;font-size:0.62rem;color:#6b6760;
                    margin-top:4px;text-transform:uppercase;letter-spacing:0.12em;font-weight:600">
            Shipping Intelligence
        </div>
    </div>
    """, unsafe_allow_html=True)

    lookback = st.slider("Lookback period (days)", 30, 180, 90, step=15)

    if st.button("🔄 Refresh All Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # ── What-if scenario selector ──────────────────────────────────────────
    # Wired to state/scenarios.py via active_scenario / set_active_scenario.
    # Any tab can read the active scenario through `overlay_value(...)` etc.
    try:
        from state.scenarios import (
            SCENARIO_CATALOG,
            active_scenario,
            list_scenarios,
            set_active_scenario,
        )

        st.caption("**What-If Scenario**")
        _scenario_options = ["— No scenario —"] + [
            f"{s.name}" for s in list_scenarios()
        ]
        _scenario_ids = [None] + [s.id for s in list_scenarios()]
        _current = active_scenario()
        _default_idx = (
            _scenario_ids.index(_current.id) if _current and _current.id in _scenario_ids else 0
        )
        _picked = st.selectbox(
            "Active scenario",
            options=_scenario_options,
            index=_default_idx,
            label_visibility="collapsed",
            key="scenario_selector",
        )
        _picked_id = _scenario_ids[_scenario_options.index(_picked)]
        if _picked_id != (_current.id if _current else None):
            set_active_scenario(_picked_id)
            st.rerun()

        if _current is not None:
            st.markdown(
                f'<div style="font-size:0.7rem;line-height:1.35;color:#a8a39a;'
                f'background:rgba(53,114,176,0.08);border-left:2px solid #3572b0;'
                f'padding:6px 8px;margin-top:4px;border-radius:2px">'
                f'<b style="color:#e8e6e1">{_current.category}</b> · '
                f'{len(_current.shocks)} shock{"s" if len(_current.shocks)!=1 else ""}<br>'
                f'<span style="color:#7a756c">{_current.summary[:140]}…</span></div>',
                unsafe_allow_html=True,
            )

        st.divider()
    except Exception as _exc:
        logger.debug(f"Scenario selector unavailable: {_exc}")

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
        _dot_color = "#2e9e6e" if _info["status"] == "fresh" else ("#c9962b" if _info["status"] == "stale" else "#c0392b")
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:4px 0;border-bottom:1px dotted rgba(232,230,225,0.06)">'
            f'<span style="display:flex;align-items:center;gap:6px;font-size:0.73rem;color:#e8e6e1;'
            f'font-family:Libre Franklin,sans-serif">'
            f'<span style="width:5px;height:5px;border-radius:50%;background:{_dot_color};flex-shrink:0"></span>'
            f'{_src_name}</span>'
            f'<span style="font-size:0.68rem;color:#6b6760;font-family:JetBrains Mono,monospace">{_detail}</span></div>', unsafe_allow_html=True)

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

# ML rate forecasts (GBR + Ridge) — preferred for the Routes tab when available
ml_forecasts: dict = get_rate_forecasts(freight_data, macro_data)

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

# Alert engine v2 — load persisted unread count for sidebar badge
try:
    from engine.alert_engine_v2 import get_unread_count as _get_unread_count
    _unread_alert_count = _get_unread_count()
except Exception:
    _unread_alert_count = 0

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

# Trade bellwether indicators (new WSJ-style enrichment)
try:
    from processing.trade_bellwethers import compute_bellwether_score, compute_earnings_calendar, compute_yield_curve_analysis
    bellwether_score = compute_bellwether_score(macro_data)
    earnings_calendar = compute_earnings_calendar()
    yield_curve_analysis = compute_yield_curve_analysis(macro_data)
except Exception as exc:
    logger.warning(f"Trade bellwethers unavailable: {exc}")
    bellwether_score = {}
    earnings_calendar = []
    yield_curve_analysis = {}

# Supply chain composite risk score
try:
    from processing.supply_chain_risk import compute_supply_chain_risk_score
    sc_risk_data = compute_supply_chain_risk_score(port_results, freight_data, macro_data, route_results)
except Exception as exc:
    logger.warning(f"Supply chain risk unavailable: {exc}")
    sc_risk_data = {}

# Rate regime analytics
try:
    from processing.rate_analytics import compute_rate_regime
    rate_regime_data = compute_rate_regime(freight_data)
except Exception as exc:
    logger.warning(f"Rate regime unavailable: {exc}")
    rate_regime_data = {}

# Company profiles
try:
    from processing.company_profiler import compute_company_profiles
    company_profiles = compute_company_profiles(stock_data)
except Exception as exc:
    logger.warning(f"Company profiler unavailable: {exc}")
    company_profiles = []

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
    # WSJ-style Signal Pulse
    st.markdown('<div style="font-family:Libre Baskerville,Georgia,serif;font-size:0.82rem;'
                'font-weight:700;color:#e8e6e1;margin-bottom:8px">Signal Pulse</div>', unsafe_allow_html=True)
    if insights and go is not None:
        avg_score = sum(i.score for i in insights) / len(insights)
        gauge_color = "#2e9e6e" if avg_score >= 0.70 else ("#c9962b" if avg_score >= 0.55 else "#c0392b")
        # Compact health bar
        st.markdown(f"""
        <div style="margin-bottom:8px">
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
                <span style="font-size:0.68rem;font-weight:600;color:#6b6760;text-transform:uppercase;
                             letter-spacing:0.06em;font-family:Libre Franklin,sans-serif">Market Health</span>
                <span style="font-family:JetBrains Mono,monospace;font-size:0.88rem;
                             font-weight:700;color:{gauge_color}">{avg_score:.0%}</span>
            </div>
            <div style="height:3px;background:rgba(232,230,225,0.06);border-radius:2px;overflow:hidden">
                <div style="height:100%;width:{avg_score*100:.0f}%;background:{gauge_color};
                            border-radius:2px;transition:width 0.6s ease"></div>
            </div>
        </div>""", unsafe_allow_html=True)
        # Top insights - WSJ editorial style
        for ins in insights[:3]:
            sc = "#2e9e6e" if ins.score >= 0.70 else ("#c9962b" if ins.score >= 0.55 else "#9a968e")
            title_short = ins.title[:44] + ("..." if len(ins.title) > 44 else "")
            st.markdown(f"""
            <div style="padding:6px 0;border-bottom:1px dotted rgba(232,230,225,0.06)">
                <div style="font-family:Libre Franklin,sans-serif;font-size:0.74rem;
                            font-weight:600;color:#e8e6e1;line-height:1.3">{title_short}</div>
                <div style="font-family:JetBrains Mono,monospace;font-size:0.66rem;
                            color:{sc};margin-top:2px;font-weight:600">{ins.score:.0%} · {ins.action}</div>
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
    # WSJ-style Watchlist
    st.markdown('<div style="font-family:Libre Baskerville,Georgia,serif;font-size:0.82rem;'
                'font-weight:700;color:#e8e6e1;margin-bottom:6px">Watchlist</div>', unsafe_allow_html=True)
    shipping_tickers = cfg.get("shipping_stocks", [])[:5]
    if stock_data:
        # Table header
        st.markdown("""
        <div style="display:flex;justify-content:space-between;padding:2px 0 4px;
                    border-bottom:1px solid rgba(232,230,225,0.1)">
            <span style="font-size:0.62rem;font-weight:700;color:#6b6760;text-transform:uppercase;
                         letter-spacing:0.08em;font-family:Libre Franklin,sans-serif">Ticker</span>
            <div style="display:flex;gap:16px">
                <span style="font-size:0.62rem;font-weight:700;color:#6b6760;text-transform:uppercase;
                             letter-spacing:0.08em;font-family:Libre Franklin,sans-serif;width:50px;text-align:right">Price</span>
                <span style="font-size:0.62rem;font-weight:700;color:#6b6760;text-transform:uppercase;
                             letter-spacing:0.08em;font-family:Libre Franklin,sans-serif;width:50px;text-align:right">Chg</span>
            </div>
        </div>""", unsafe_allow_html=True)
        for ticker in shipping_tickers:
            df = stock_data.get(ticker)
            if df is not None and not df.empty and "close" in df.columns:
                close = df["close"]
                current = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) > 1 else current
                chg_pct = (current - prev) / prev if prev != 0 else 0
                sign = "+" if chg_pct >= 0 else ""
                color = "#2e9e6e" if chg_pct > 0 else ("#c0392b" if chg_pct < 0 else "#9a968e")
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;align-items:center;
                            padding:4px 0;border-bottom:1px dotted rgba(232,230,225,0.06)">
                    <span style="font-size:0.76rem;font-weight:600;color:#e8e6e1;
                                 font-family:Libre Franklin,sans-serif">{ticker}</span>
                    <div style="display:flex;gap:16px">
                        <span style="font-size:0.76rem;color:#e8e6e1;font-family:JetBrains Mono,monospace;
                                     width:50px;text-align:right">{current:.2f}</span>
                        <span style="font-size:0.76rem;color:{color};font-weight:600;
                                     font-family:JetBrains Mono,monospace;width:50px;text-align:right">
                            {sign}{chg_pct*100:.1f}%</span>
                    </div>
                </div>""", unsafe_allow_html=True)
    else:
        for ticker in shipping_tickers:
            st.caption(f"-- {ticker}")

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


# ── WSJ Editorial Masthead ────────────────────────────────────────────────
import datetime as _dt
_edition_date = _dt.datetime.now(_dt.timezone.utc).strftime("%A, %B %-d, %Y")
_edition_vol = _dt.datetime.now(_dt.timezone.utc).strftime("Vol. %j")

st.markdown(f"""
<style>
@keyframes pulse-live {{ 0%,100%{{opacity:1}} 50%{{opacity:.35}} }}
.wsj-live {{ animation: pulse-live 2.5s ease-in-out infinite; }}
</style>

<div style="padding:12px 0 0 0; margin-bottom:0">
    <!-- WSJ Masthead -->
    <div style="text-align:center;padding-bottom:14px;border-bottom:3px double rgba(232,230,225,0.12)">
        <div style="font-family:'Libre Franklin',system-ui,sans-serif;font-size:0.62rem;
                    font-weight:700;color:#6b6760;text-transform:uppercase;letter-spacing:0.2em;
                    margin-bottom:6px">
            Global Shipping Intelligence
        </div>
        <div style="font-family:'Libre Baskerville','Georgia',serif;font-size:2.3rem;
                    font-weight:700;color:#e8e6e1;letter-spacing:-0.02em;line-height:1.05">
            The Ship Tracker
        </div>
        <div style="display:flex;justify-content:center;align-items:center;gap:16px;margin-top:8px">
            <span style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;
                         color:#6b6760;letter-spacing:0.04em">{_edition_date}</span>
            <span style="color:rgba(232,230,225,0.15)">|</span>
            <span style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;
                         color:#6b6760">{_edition_vol}</span>
            <span style="color:rgba(232,230,225,0.15)">|</span>
            <span class="wsj-live" style="display:inline-flex;align-items:center;gap:4px;
                         font-size:0.66rem;font-weight:700;color:#2e9e6e;letter-spacing:0.06em">
                <span style="display:inline-block;width:5px;height:5px;border-radius:50%;
                             background:#2e9e6e"></span>
                {'LIVE' if stock_data else 'OFFLINE'}
            </span>
        </div>
    </div>

    <!-- Summary line -->
    <div style="display:flex;justify-content:space-between;align-items:center;
                padding:10px 0;border-bottom:1px solid rgba(232,230,225,0.08);
                margin-bottom:4px;flex-wrap:wrap;gap:8px">
        <div style="display:flex;gap:20px;flex-wrap:wrap">
            <span style="font-family:JetBrains Mono,monospace;font-size:0.75rem;color:#9a968e">
                <span style="font-weight:600;color:#e8e6e1">{port_count}</span> Ports
            </span>
            <span style="font-family:JetBrains Mono,monospace;font-size:0.75rem;color:#9a968e">
                <span style="font-weight:600;color:#e8e6e1">{route_count}</span> Routes
            </span>
            <span style="font-family:JetBrains Mono,monospace;font-size:0.75rem;color:#9a968e">
                <span style="font-weight:600;color:#e8e6e1">{insight_count}</span> Signals
            </span>
            <span style="font-family:JetBrains Mono,monospace;font-size:0.75rem;
                         color:{'#2e9e6e' if high_count > 0 else '#9a968e'}">
                <span style="font-weight:600">{high_count}</span> High-Conviction
            </span>
        </div>
        <div style="display:flex;gap:12px;flex-wrap:wrap">
            <span style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                         font-weight:600;color:{'#2e9e6e' if top_score >= 0.70 else ('#c9962b' if top_score >= 0.55 else '#c0392b')}">
                Top Signal {top_score:.0%}
            </span>
            <span style="color:rgba(232,230,225,0.12)">|</span>
            <span style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                         font-weight:600;color:{'#2e9e6e' if freight_trend == 'Rising' else ('#c9962b' if freight_trend == 'Stable' else '#c0392b')}">
                Freight {freight_trend}
            </span>
            <span style="color:rgba(232,230,225,0.12)">|</span>
            <span style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                         font-weight:600;color:{'#2e9e6e' if sc_health == 'Healthy' else '#c0392b'}">
                SC {sc_health}
            </span>
            {'<span style="color:rgba(232,230,225,0.12)">|</span><span style="font-family:Libre Franklin,sans-serif;font-size:0.72rem;font-weight:600;color:#c0392b">' + str(alert_cnt) + ' Alerts</span>' if alert_cnt else ''}
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ── WSJ Market Ticker Tape ────────────────────────────────────────────────
ticker_items = []
if stock_data:
    for ticker, df in stock_data.items():
        if df is not None and not df.empty and "close" in df.columns:
            close = df["close"]
            cur = float(close.iloc[-1])
            prv = float(close.iloc[-2]) if len(close) > 1 else cur
            chg = (cur - prv) / prv * 100 if prv != 0 else 0
            sign = "+" if chg >= 0 else ""
            clr = "#2e9e6e" if chg > 0 else "#c0392b"
            ticker_items.append(
                f'<span style="display:inline-flex;align-items:center;gap:5px;padding:0 16px;'
                f'white-space:nowrap;font-size:0.78rem">'
                f'<span style="color:#6b6760;font-weight:600;font-size:0.7rem;'
                f'text-transform:uppercase;letter-spacing:0.03em">{ticker}</span>'
                f'<span style="color:#e8e6e1;font-weight:600;font-family:JetBrains Mono,monospace;'
                f'font-size:0.8rem">${cur:.2f}</span>'
                f'<span style="color:{clr};font-family:JetBrains Mono,monospace;'
                f'font-size:0.72rem">{sign}{chg:.1f}%</span>'
                f'</span>'
            )
if freight_data:
    try:
        import pandas as pd
        for route_name, df in freight_data.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                vals = df[df.columns[0]].dropna()
                if len(vals) >= 2:
                    chg = (float(vals.iloc[-1]) - float(vals.iloc[-2])) / float(vals.iloc[-2]) * 100 if float(vals.iloc[-2]) != 0 else 0
                    sign = "+" if chg >= 0 else ""
                    clr = "#2e9e6e" if chg > 0 else "#c0392b"
                    ticker_items.append(
                        f'<span style="display:inline-flex;align-items:center;gap:5px;padding:0 16px;'
                        f'white-space:nowrap;font-size:0.78rem">'
                        f'<span style="color:#6b6760;font-weight:600;font-size:0.7rem;'
                        f'letter-spacing:0.03em">{str(route_name)[:14]}</span>'
                        f'<span style="color:{clr};font-family:JetBrains Mono,monospace;'
                        f'font-size:0.72rem">{sign}{chg:.1f}%</span>'
                        f'</span>'
                    )
    except Exception:
        pass

_ticker_inner = "".join(ticker_items) if ticker_items else '<span style="color:#6b6760">Loading market data...</span>'
_ticker_duration = max(16, len(ticker_items) * 3)
components.html(f"""
<div style="overflow:hidden;background:#12151e;border-top:1px solid rgba(232,230,225,0.06);
            border-bottom:1px solid rgba(232,230,225,0.06);padding:6px 0;white-space:nowrap;
            font-family:'Libre Franklin','Inter',system-ui,sans-serif;position:relative">
    <div style="position:absolute;left:0;top:0;bottom:0;width:40px;
                background:linear-gradient(90deg,#0c0e14,transparent);z-index:1"></div>
    <div style="position:absolute;right:0;top:0;bottom:0;width:40px;
                background:linear-gradient(90deg,transparent,#0c0e14);z-index:1"></div>
    <div style="display:inline-flex;animation:ticker {_ticker_duration}s linear infinite">
        {_ticker_inner}{_ticker_inner}
    </div>
</div>
<style>
@keyframes ticker {{ 0%{{transform:translateX(0)}} 100%{{transform:translateX(-50%)}} }}
</style>
""", height=34)


# ── Section navigation helpers ────────────────────────────────────────────
SECTIONS = [
    ("dashboard",    "🏠", "Dashboard",          "Overview, scorecard & live data"),
    ("markets",      "📈", "Markets & Signals",   "Alpha, correlations & derivatives"),
    ("disruption_alpha", "📡", "Disruption Alpha", "Voyage tracking → disruption → equity ideas"),
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
    "disruption_alpha": "#3572b0",
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

# Inject WSJ section nav CSS
st.markdown("""<style>
/* ── Sidebar masthead ──────────────────────────────────────────── */
.nav-brand { padding: 4px 4px 12px; margin-bottom: 2px; }
.nav-brand-title {
    font-family: 'Libre Baskerville','Georgia',serif;
    font-size: 1.18rem; font-weight: 700; color: #e8e6e1;
    letter-spacing: -0.01em; line-height: 1.15;
    display: flex; align-items: center; gap: 8px;
}
.nav-brand-dot {
    width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    background: #2e9e6e; box-shadow: 0 0 8px rgba(46,158,110,0.7);
    animation: nav-pulse 2.6s ease-in-out infinite;
}
@keyframes nav-pulse { 0%,100%{ opacity:1 } 50%{ opacity:0.35 } }
.nav-brand-sub {
    font-family: 'Libre Franklin',sans-serif; font-size: 0.6rem;
    font-weight: 600; color: #6b6760; letter-spacing: 0.16em;
    text-transform: uppercase; margin-top: 6px;
}
.nav-cluster-label {
    font-family: 'Libre Franklin',sans-serif; font-size: 0.58rem;
    font-weight: 700; color: #6b6760; letter-spacing: 0.14em;
    text-transform: uppercase; padding: 15px 8px 5px;
}
/* ── Sidebar nav buttons ───────────────────────────────────────── */
.sec-nav-btn > div > button, .sec-nav-active > div > button {
    border: none !important;
    border-left: 2px solid transparent !important;
    border-radius: 6px !important;
    font-size: 0.83rem !important;
    font-family: 'Libre Franklin', sans-serif !important;
    text-align: left !important;
    padding: 9px 12px !important;
    margin-bottom: 1px !important;
    transition: all 0.18s cubic-bezier(0.4,0,0.2,1) !important;
}
.sec-nav-btn > div > button {
    background: transparent !important;
    color: #9a968e !important;
    font-weight: 500 !important;
}
.sec-nav-btn > div > button:hover {
    background: rgba(53,114,176,0.05) !important;
    border-left-color: rgba(53,114,176,0.35) !important;
    color: #e8e6e1 !important;
}
.sec-nav-active > div > button {
    background: var(--sec-bg, rgba(53,114,176,0.13)) !important;
    border-left: 3px solid var(--sec-accent, #3572b0) !important;
    color: #e8e6e1 !important;
    font-weight: 600 !important;
}
</style>""", unsafe_allow_html=True)

# Render nav in sidebar
_NAV_CLUSTER2 = "trade_macro"  # second visual group starts here
with st.sidebar:
    st.markdown(
        '<div class="nav-brand">'
        '<div class="nav-brand-title"><span class="nav-brand-dot"></span>ShipTracker</div>'
        '<div class="nav-brand-sub">Shipping Intelligence Platform</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="nav-cluster-label">Core</div>', unsafe_allow_html=True)
    for sec_key, sec_icon, sec_label, sec_desc in SECTIONS:
        if sec_key == _NAV_CLUSTER2:
            st.markdown('<div class="nav-cluster-label">Analysis &amp; Risk</div>',
                        unsafe_allow_html=True)
        active = st.session_state["nav_section"] == sec_key
        if active:
            _ac = SECTION_COLORS.get(sec_key, "#3572b0").lstrip("#")
            _rgb = f"{int(_ac[0:2], 16)},{int(_ac[2:4], 16)},{int(_ac[4:6], 16)}"
            st.markdown(
                f'<div class="sec-nav-active" '
                f'style="--sec-accent:#{_ac};--sec-bg:rgba({_rgb},0.13);">',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div class="sec-nav-btn">', unsafe_allow_html=True)
        btn_label = f"{sec_icon}  {sec_label}"
        if alerts and sec_key == "risk":
            btn_label += f"  ({len(alerts)})"
        if sec_key == "intelligence" and _unread_alert_count > 0:
            btn_label += f"  🔔 {_unread_alert_count}"
        if st.button(btn_label, key=f"nav_{sec_key}", use_container_width=True):
            st.session_state["nav_section"] = sec_key
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

active_section = st.session_state.get("nav_section", "dashboard")

# WSJ Section breadcrumb
sec_info = next((s for s in SECTIONS if s[0] == active_section), SECTIONS[0])
sec_color = SECTION_COLORS.get(active_section, "#3572b0")
st.markdown(f"""
<div style="border-top:2px solid #e8e6e1;padding-top:10px;margin-bottom:18px">
    <div style="display:flex;align-items:baseline;gap:10px">
        <span style="font-size:1.1rem">{sec_info[1]}</span>
        <span style="font-family:'Libre Baskerville',Georgia,serif;font-size:1.05rem;
                     font-weight:700;color:#e8e6e1">{sec_info[2]}</span>
    </div>
    <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;color:#6b6760;
                margin-top:3px">{sec_info[3]}</div>
</div>
""", unsafe_allow_html=True)


# ── Cross-tab filter bar ─────────────────────────────────────────────────
# Rendered once above the section tabs. State persists via state/session.py.
try:
    from ui.filter_bar import render_filter_bar
    _active_filters = render_filter_bar()
except Exception as _exc:
    logger.debug(f"Filter bar unavailable: {_exc}")
    _active_filters = None


# ── Section routing ───────────────────────────────────────────────────────

# ── 1. Dashboard ──────────────────────────────────────────────────────────
if active_section == "dashboard":
    t0, t_brief, t1, t2, t3, t4 = st.tabs([
        "Overview", "Daily Briefing", "Market Commentary",
        "Scorecard", "Live Feed", "Data Health",
    ])
    with t0:
        try:
            from ui.tab_overview import render as _r
            _r(port_results, route_results, insights,
               freight_data=freight_data, macro_data=macro_data,
               stock_data=stock_data, alerts=alerts)
        except Exception as e:
            st.error(f"Overview error: {e}")
    with t_brief:
        try:
            from ui.tab_briefing import render as _r
            _r(
                port_results=port_results, route_results=route_results,
                insights=insights, freight_data=freight_data,
                macro_data=macro_data, stock_data=stock_data,
            )
        except Exception as e:
            st.error(f"Daily Briefing error: {e}")
    with t1:
        try:
            from ui.tab_commentary import render as _r
            _r(stock_data=stock_data, freight_data=freight_data, macro_data=macro_data,
               port_results=port_results, insights=insights)
        except Exception as e:
            st.error(f"Market Commentary error: {e}")
    with t2:
        try:
            from ui.tab_scorecard import render as _r
            _r(port_results, route_results, insights, freight_data, macro_data, stock_data)
        except Exception as e:
            st.error(f"Scorecard error: {e}")
    with t3:
        try:
            from ui.tab_live_feed import render as _r
            _r(port_results, route_results, insights, freight_data, stock_data, macro_data)
        except Exception as e:
            st.error(f"Live Feed error: {e}")
    with t4:
        try:
            from ui.tab_data_health import render as _r
            _r(port_results, route_results, freight_data, macro_data, stock_data, trade_data, ais_data)
        except Exception as e:
            st.error(f"Data Health error: {e}")

# ── 2. Markets & Signals ──────────────────────────────────────────────────
elif active_section == "markets":
    t0, t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11 = st.tabs([
        "Markets", "Sector Dashboard", "Alpha Signals", "Results",
        "Indices", "Derivatives", "Scenarios", "Monte Carlo",
        "Backtesting", "Portfolio", "Options & Flow", "Convergence",
    ])
    with t0:
        try:
            from ui.tab_markets import render as _r
            _r(correlation_results, stock_data, lookback)
        except Exception as e:
            st.error(f"Markets error: {e}")
    with t1:
        try:
            from ui.tab_sector import render as _r
            _r(stock_data=stock_data, freight_data=freight_data, port_results=port_results)
        except Exception as e:
            st.error(f"Sector Dashboard error: {e}")
    with t2:
        try:
            from ui.tab_alpha import render as _r
            _r(route_results, port_results, freight_data, macro_data, stock_data, insights)
        except Exception as e:
            st.error(f"Alpha error: {e}")
    with t3:
        try:
            from ui.tab_results import render as _r
            _r(insights)
        except Exception as e:
            st.error(f"Results error: {e}")
    with t4:
        try:
            from ui.tab_indices import render as _r
            _r(macro_data=macro_data, freight_data=freight_data, stock_data=stock_data, lookback_days=lookback)
        except Exception as e:
            st.error(f"Indices error: {e}")
    with t5:
        try:
            from ui.tab_derivatives import render as _r
            _r(route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Derivatives error: {e}")
    with t6:
        try:
            from ui.tab_scenarios import render as _r
            _r(port_results, route_results, macro_data)
        except Exception as e:
            st.error(f"Scenarios error: {e}")
    with t7:
        try:
            from ui.tab_monte_carlo import render as _r
            _r(freight_data, route_results)
        except Exception as e:
            st.error(f"Monte Carlo error: {e}")
    with t8:
        try:
            from ui import tab_backtest
            tab_backtest.render(stock_data, macro_data, insights)
        except Exception as e:
            st.error(f"Tab error: {e}")
    with t9:
        try:
            from ui import tab_portfolio
            tab_portfolio.render(stock_data, macro_data, insights)
        except Exception as e:
            st.error(f"Tab error: {e}")
    with t10:
        try:
            from ui import tab_options
            tab_options.render(stock_data, insights, signals=insights)
        except Exception as e:
            st.error(f"Options & Flow error: {e}")
    with t11:
        try:
            from ui.tab_convergence import render as _r
            _r(
                port_results=port_results, route_results=route_results,
                insights=insights, freight_data=freight_data,
                macro_data=macro_data, stock_data=stock_data,
            )
        except Exception as e:
            st.error(f"Convergence error: {e}")

# ── 3. Disruption Alpha ───────────────────────────────────────────────────
elif active_section == "disruption_alpha":
    t0, t1, t2, t3, t4, t5 = st.tabs([
        "Voyage Tracker", "Disruption Radar", "Macro Projection",
        "Supply Linkage", "Equity Signals", "Idea Engine",
    ])
    with t0:
        try:
            from ui.tab_voyage_tracker import render as _r
            _r(freight_data, route_results)
        except Exception as e:
            st.error(f"Voyage Tracker error: {e}")
    with t1:
        try:
            from ui.tab_disruption_radar import render as _r
            _r(freight_data, macro_data, port_results, route_results)
        except Exception as e:
            st.error(f"Disruption Radar error: {e}")
    with t2:
        try:
            from ui.tab_macro_projection import render as _r
            _r(port_results, freight_data, macro_data, route_results)
        except Exception as e:
            st.error(f"Macro Projection error: {e}")
    with t3:
        try:
            from ui.tab_supply_linkage import render as _r
            _r(stock_data, freight_data, macro_data, port_results, route_results, trade_data)
        except Exception as e:
            st.error(f"Supply Linkage error: {e}")
    with t4:
        try:
            from ui.tab_equity_signals import render as _r
            _r(stock_data, freight_data, macro_data, port_results, route_results, insights)
        except Exception as e:
            st.error(f"Equity Signals error: {e}")
    with t5:
        try:
            from ui.tab_idea_engine import render as _r
            _r(
                port_results=port_results,
                route_results=route_results,
                insights=insights,
                freight_data=freight_data,
                macro_data=macro_data,
                stock_data=stock_data,
            )
        except Exception as e:
            st.error(f"Idea Engine error: {e}")

# ── 4. Ports & Routes ─────────────────────────────────────────────────────
elif active_section == "ports_routes":
    t0, t1, t2, t3, t4, t5, t6, t7 = st.tabs([
        "Port Demand", "Port Monitor", "Routes", "Rate Analytics",
        "ETA Predictor", "Congestion", "Emerging Routes",
        "Vessel Map",
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
            _r(route_results, freight_data, ml_forecasts or forecasts)
        except Exception as e:
            st.error(f"Routes error: {e}")
    with t3:
        try:
            from ui.tab_rate_analytics import render as _r
            _r(freight_data=freight_data, route_results=route_results)
        except Exception as e:
            st.error(f"Rate Analytics error: {e}")
    with t4:
        try:
            from ui.tab_eta import render as _r
            _r(port_results, route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"ETA Predictor error: {e}")
    with t5:
        try:
            from ui.tab_congestion import render as _r
            _r(port_results, ais_data, freight_data, macro_data)
        except Exception as e:
            st.error(f"Congestion error: {e}")
    with t6:
        try:
            from ui.tab_emerging_routes import render as _r
            _r(route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Emerging Routes error: {e}")
    with t7:
        try:
            from ui.tab_vessel_map import render as _r
            _r(port_results, route_results, freight_data)
        except Exception as e:
            st.error(f"Vessel Map error: {e}")

# ── 5. Carriers & Ops ────────────────────────────────────────────────────
elif active_section == "carriers":
    t0, t1, t2, t3, t4, t5 = st.tabs([
        "Carriers", "Fleet", "Equipment",
        "Cargo", "Booking", "Bunker Fuel",
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

# ── 6. Trade & Macro ─────────────────────────────────────────────────────
elif active_section == "trade_macro":
    t0, t1, t2, t3, t4, t5, t6, t7, t8 = st.tabs([
        "Macro", "Bellwethers", "Trade Flows", "Trade War", "Geopolitical",
        "Chokepoints", "Trade Finance", "E-Commerce", "Nowcast",
    ])
    with t0:
        try:
            from ui.tab_macro import render as _r
            _r(macro_data, freight_data, stock_data)
        except Exception as e:
            st.error(f"Macro error: {e}")
    with t1:
        try:
            from ui.tab_bellwethers import render as _r
            _r(macro_data=macro_data)
        except Exception as e:
            st.error(f"Bellwethers error: {e}")
    with t2:
        try:
            from ui.tab_trade_flows import render as _r
            _r(trade_data=trade_data, route_results=route_results,
               port_results=port_results, freight_data=freight_data, macro_data=macro_data)
        except Exception as e:
            st.error(f"Trade Flows error: {e}")
    with t3:
        try:
            from ui.tab_trade_war import render as _r
            _r(route_results, port_results, freight_data, macro_data, trade_data)
        except Exception as e:
            st.error(f"Trade War error: {e}")
    with t4:
        try:
            from ui.tab_geopolitical import render as _r
            _r(route_results, port_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Geopolitical error: {e}")
    with t5:
        try:
            from ui.tab_chokepoints import render as _r
            _r(route_results, freight_data, macro_data)
        except Exception as e:
            st.error(f"Chokepoints error: {e}")
    with t6:
        try:
            from ui.tab_finance import render as _r
            _r(freight_data, macro_data, route_results, stock_data)
        except Exception as e:
            st.error(f"Trade Finance error: {e}")
    with t7:
        try:
            from ui.tab_ecommerce import render as _r
            _r(trade_data, freight_data, macro_data, route_results)
        except Exception as e:
            st.error(f"E-Commerce error: {e}")
    with t8:
        try:
            from ui.tab_nowcast import render as _r
            _r(
                port_results=port_results, route_results=route_results,
                insights=insights, freight_data=freight_data,
                macro_data=macro_data, stock_data=stock_data,
            )
        except Exception as e:
            st.error(f"Nowcast error: {e}")

# ── 7. Supply Chain ───────────────────────────────────────────────────────
elif active_section == "supply_chain":
    t0, t1, t2, t3, t4 = st.tabs([
        "Supply Chain", "Visibility", "Intermodal",
        "Network", "Attribution",
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

# ── 8. Risk & Compliance ──────────────────────────────────────────────────
elif active_section == "risk":
    t0, t1, t2, t3, t4, t5 = st.tabs([
        "Risk Matrix", "Risk Lab", "Weather", "Compliance",
        "Market Cycle", "Fundamentals",
    ])
    with t0:
        try:
            from ui.tab_risk_matrix import render as _r
            _r(route_results, port_results, macro_data)
        except Exception as e:
            st.error(f"Risk Matrix error: {e}")
    with t1:
        try:
            from ui.tab_risk_lab import render as _r
            _r(
                port_results=port_results, route_results=route_results,
                insights=insights, freight_data=freight_data,
                macro_data=macro_data, stock_data=stock_data,
            )
        except Exception as e:
            st.error(f"Risk Lab error: {e}")
    with t2:
        try:
            from ui.tab_weather import render as _r
            _r(port_results, route_results, freight_data)
        except Exception as e:
            st.error(f"Weather error: {e}")
    with t3:
        try:
            from ui.tab_compliance import render as _r
            _r(route_results, port_results, macro_data)
        except Exception as e:
            st.error(f"Compliance error: {e}")
    with t4:
        try:
            from ui.tab_cycle import render as _r
            _r(freight_data, macro_data, stock_data, route_results)
        except Exception as e:
            st.error(f"Market Cycle error: {e}")
    with t5:
        try:
            from ui.tab_fundamentals import render as _r
            _r(stock_data, freight_data, macro_data)
        except Exception as e:
            st.error(f"Fundamentals error: {e}")

# ── 9. Intelligence ───────────────────────────────────────────────────────
elif active_section == "intelligence":
    t0, t1, t2, t3, t4 = st.tabs([
        "News & Sentiment", "Deep Dive",
        "AI Assistant", "Sustainability",
        "Alerts",
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
    with t4:
        try:
            from ui import tab_alerts
            tab_alerts.render(port_results, route_results, insights, freight_data, macro_data, stock_data)
        except Exception as e:
            st.error(f"Tab error: {e}")


# ── 10. Reports ───────────────────────────────────────────────────────────
elif active_section == "reports":
    from ui import tab_report
    fundamentals_data = get_fundamentals_data()
    (t0,) = st.tabs(["Investor Report"])
    with t0:
        try:
            tab_report.render(port_results, route_results, insights, freight_data, macro_data, stock_data, fundamentals_data=fundamentals_data)
        except Exception as e:
            st.error(f"Investor Report error: {e}")


# ── WSJ Footer ────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:48px;padding:16px 0 8px 0;border-top:2px solid rgba(232,230,225,0.1)">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <div style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;color:#6b6760">
            <span style="font-family:'Libre Baskerville',Georgia,serif;font-weight:700;
                         color:#9a968e">The Ship Tracker</span>
            &nbsp;&middot;&nbsp; Data: UN Comtrade &middot; FRED &middot; World Bank &middot;
            yfinance &middot; Freightos FBX
        </div>
        <div style="font-family:'Libre Franklin',sans-serif;font-size:0.66rem;color:#6b6760">
            Free public APIs only &nbsp;&middot;&nbsp; Not financial advice
        </div>
    </div>
    <div style="height:1px;background:rgba(232,230,225,0.06);margin-top:12px"></div>
</div>
""", unsafe_allow_html=True)
