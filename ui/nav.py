"""Sidebar navigation system for the Ship Tracker intelligence dashboard.

Replaces the old 43-flat-tab system with 8 grouped sections, each rendered
as st.tabs() in the main content area.
"""
from __future__ import annotations

import streamlit as st

# ── Color constants (mirrors ui/styles.py) ───────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

# ── Section definitions ───────────────────────────────────────────────────────
SECTIONS: list[dict] = [
    {
        "key": "dashboard",
        "icon": "🏠",
        "label": "Dashboard",
        "description": "Overview & live data",
        "color": "#3b82f6",
        "sub_pages": ["Overview", "Scorecard", "Live Feed", "Data Health"],
    },
    {
        "key": "markets",
        "icon": "📈",
        "label": "Markets & Signals",
        "description": "Signals, alpha & correlations",
        "color": "#10b981",
        "sub_pages": ["Markets", "Alpha Signals", "Results", "Indices", "Derivatives", "Scenarios", "Monte Carlo", "Backtesting", "Portfolio", "Options & Flow"],
    },
    {
        "key": "ports_routes",
        "icon": "🚢",
        "label": "Ports & Routes",
        "description": "Port demand, routes & congestion",
        "color": "#06b6d4",
        "sub_pages": ["Port Demand", "Port Monitor", "Routes", "ETA Predictor", "Congestion", "Emerging Routes", "Vessel Map"],
    },
    {
        "key": "carriers",
        "icon": "🏢",
        "label": "Carriers & Ops",
        "description": "Fleet, cargo & operations",
        "color": "#8b5cf6",
        "sub_pages": ["Carriers", "Fleet", "Equipment", "Cargo", "Booking", "Bunker Fuel"],
    },
    {
        "key": "trade_macro",
        "icon": "🌍",
        "label": "Trade & Macro",
        "description": "Macro, trade wars & geopolitics",
        "color": "#f59e0b",
        "sub_pages": ["Macro", "Trade War", "Geopolitical", "Chokepoints", "Trade Finance", "E-Commerce"],
    },
    {
        "key": "supply_chain",
        "icon": "🔗",
        "label": "Supply Chain",
        "description": "Visibility, network & intermodal",
        "color": "#ec4899",
        "sub_pages": ["Supply Chain", "Visibility", "Intermodal", "Network", "Attribution"],
    },
    {
        "key": "risk",
        "icon": "⚠️",
        "label": "Risk & Compliance",
        "description": "Risk, weather & regulatory",
        "color": "#ef4444",
        "sub_pages": ["Risk Matrix", "Weather Risk", "Compliance", "Market Cycle", "Fundamentals"],
    },
    {
        "key": "intelligence",
        "icon": "🤖",
        "label": "Intelligence",
        "description": "News, AI assistant & insights",
        "color": "#a78bfa",
        "sub_pages": ["News & Sentiment", "Deep Dive", "AI Assistant", "Sustainability", "Alerts"],
    },
    {
        "key": "reports",
        "icon": "📋",
        "label": "Reports",
        "description": "Investor & summary reports",
        "color": "#64748b",
        "sub_pages": ["Investor Report"],
    },
]

# ── Session-state default ─────────────────────────────────────────────────────
_NAV_KEY = "nav_section"
_DEFAULT_SECTION = "dashboard"


def _ensure_state() -> None:
    """Guarantee nav_section exists in session_state."""
    if _NAV_KEY not in st.session_state:
        st.session_state[_NAV_KEY] = _DEFAULT_SECTION


# ── Public helpers ────────────────────────────────────────────────────────────

def get_sections() -> list[dict]:
    """Return the full ordered list of navigation sections."""
    return SECTIONS


def get_active_section() -> dict:
    """Return the section dict corresponding to the currently active section."""
    _ensure_state()
    active_key = st.session_state[_NAV_KEY]
    for section in SECTIONS:
        if section["key"] == active_key:
            return section
    # Fallback to dashboard if key is somehow stale.
    return SECTIONS[0]


def get_section_color() -> str:
    """Return the accent color for the currently active section."""
    return get_active_section()["color"]


# ── CSS injection ─────────────────────────────────────────────────────────────

def _inject_nav_css() -> None:
    """Inject sidebar-scoped CSS once per session."""
    st.markdown(
        """
        <style>
        /* ── Sidebar chrome ─────────────────────────────────────────────── */
        section[data-testid="stSidebar"] {
            background: #0a0f1a !important;
            border-right: 1px solid rgba(255,255,255,0.07) !important;
        }
        section[data-testid="stSidebar"] > div:first-child {
            padding-top: 0 !important;
        }

        /* ── Brand block ─────────────────────────────────────────────────── */
        .nav-brand {
            padding: 1.1rem 0.8rem 0.7rem;
            border-bottom: 1px solid rgba(255,255,255,0.07);
            margin-bottom: 0.5rem;
        }
        .nav-brand-title {
            font-size: 1.2rem;
            font-weight: 700;
            color: #f1f5f9;
            letter-spacing: 0.04em;
            line-height: 1.2;
        }
        .nav-brand-sub {
            font-size: 0.68rem;
            color: #64748b;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-top: 0.15rem;
        }
        .nav-brand-dot {
            display: inline-block;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #10b981;
            margin-right: 5px;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50%       { opacity: 0.4; }
        }

        /* ── Section group label ─────────────────────────────────────────── */
        .nav-section-label {
            font-size: 0.6rem;
            font-weight: 600;
            color: #475569;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            padding: 0.9rem 0.9rem 0.2rem;
        }

        /* ── Nav button wrappers ─────────────────────────────────────────── */
        .nav-btn-wrap {
            padding: 0 0.45rem 0.15rem;
        }

        /* Active nav button */
        .nav-btn-active > div > button {
            background: var(--nav-btn-bg, rgba(59,130,246,0.15)) !important;
            border: 1px solid var(--nav-btn-border, rgba(59,130,246,0.45)) !important;
            border-left: 3px solid var(--nav-btn-accent, #3b82f6) !important;
            color: #f1f5f9 !important;
            text-align: left !important;
            border-radius: 6px !important;
            font-weight: 600 !important;
        }

        /* Inactive nav button */
        .nav-btn-inactive > div > button {
            background: transparent !important;
            border: 1px solid transparent !important;
            border-left: 3px solid transparent !important;
            color: #94a3b8 !important;
            text-align: left !important;
            border-radius: 6px !important;
            font-weight: 400 !important;
        }
        .nav-btn-inactive > div > button:hover {
            background: rgba(255,255,255,0.04) !important;
            border-left-color: rgba(255,255,255,0.2) !important;
            color: #f1f5f9 !important;
        }

        /* Shared button chrome */
        section[data-testid="stSidebar"] button {
            width: 100% !important;
            justify-content: flex-start !important;
            padding: 0.42rem 0.65rem !important;
            font-size: 0.82rem !important;
            transition: all 0.15s ease !important;
        }

        /* ── Alert badge ─────────────────────────────────────────────────── */
        .nav-alert-badge {
            display: inline-block;
            background: #ef4444;
            color: #fff;
            font-size: 0.6rem;
            font-weight: 700;
            padding: 1px 5px;
            border-radius: 8px;
            margin-left: 6px;
            vertical-align: middle;
            line-height: 1.5;
        }

        /* ── Mini stats bar ──────────────────────────────────────────────── */
        .mini-stats {
            display: flex;
            justify-content: space-around;
            padding: 0.55rem 0.5rem;
            margin: 0.4rem 0.45rem 0;
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 8px;
        }
        .mini-stat-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 1px;
        }
        .mini-stat-value {
            font-size: 0.8rem;
            font-weight: 700;
            color: #f1f5f9;
            line-height: 1;
        }
        .mini-stat-label {
            font-size: 0.55rem;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }
        .mini-stat-high {
            color: #10b981 !important;
        }

        /* ── Health indicator ────────────────────────────────────────────── */
        .nav-health {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 0.45rem 0.9rem;
            font-size: 0.68rem;
            color: #64748b;
            border-top: 1px solid rgba(255,255,255,0.06);
            margin-top: 0.5rem;
        }
        .health-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            flex-shrink: 0;
        }
        .health-ok   { background: #10b981; }
        .health-warn { background: #f59e0b; }
        .health-err  { background: #ef4444; }

        /* ── Breadcrumb header ───────────────────────────────────────────── */
        .nav-breadcrumb {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.6rem 0;
            margin-bottom: 0.5rem;
            border-bottom: 1px solid rgba(255,255,255,0.07);
            flex-wrap: wrap;
        }
        .nav-bc-icon {
            font-size: 1.3rem;
            line-height: 1;
        }
        .nav-bc-section {
            font-size: 1.05rem;
            font-weight: 700;
            color: #f1f5f9;
        }
        .nav-bc-sep {
            color: #475569;
            font-size: 0.85rem;
        }
        .nav-bc-sub {
            font-size: 0.85rem;
            color: #94a3b8;
        }
        .nav-bc-desc {
            font-size: 0.72rem;
            color: #64748b;
            margin-left: auto;
            font-style: italic;
        }
        .nav-bc-bar {
            height: 3px;
            border-radius: 2px;
            width: 100%;
            margin-top: 0.3rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _alert_count(alerts) -> int:
    """Safely count the number of alerts."""
    if alerts is None:
        return 0
    try:
        return len(alerts)
    except Exception:
        return 0


def _health_status(insights, stock_data) -> tuple[str, str]:
    """Return (css_class, label) for the mini health indicator."""
    has_insights = insights is not None and len(insights) > 0 if insights is not None else False
    has_stocks   = stock_data is not None and len(stock_data) > 0 if stock_data is not None else False
    if has_insights and has_stocks:
        return "health-ok", "All systems operational"
    if has_insights or has_stocks:
        return "health-warn", "Partial data available"
    return "health-err", "No live data"


# ── Main render functions ─────────────────────────────────────────────────────

def render_sidebar_nav(
    insights=None,
    stock_data=None,
    alerts=None,
    unread_alert_count: int = 0,
) -> str:
    """Render the full sidebar navigation and return the active section key.

    Parameters
    ----------
    insights:            Insight records (list/DataFrame) used for health indicator.
    stock_data:          Stock/market data (list/DataFrame) used for health indicator.
    alerts:              Alert records; count shown as a badge on the Risk section.
    unread_alert_count:  Unacknowledged alert count shown as a badge on Intelligence section.

    Returns
    -------
    str: The active section key (e.g. ``"dashboard"``).
    """
    _ensure_state()
    _inject_nav_css()

    n_alerts = _alert_count(alerts)
    active_key = st.session_state[_NAV_KEY]

    # ── Brand / logo block ────────────────────────────────────────────────────
    st.sidebar.markdown(
        """
        <div class="nav-brand">
            <div class="nav-brand-title">
                <span class="nav-brand-dot"></span>ShipTracker
            </div>
            <div class="nav-brand-sub">Shipping Intelligence Platform</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Navigation section buttons ────────────────────────────────────────────
    # Group into two visual clusters: data sections and analytical sections.
    cluster_1_keys = {"dashboard", "markets", "ports_routes", "carriers"}
    cluster_2_keys = {"trade_macro", "supply_chain", "risk", "intelligence"}

    # Label for first cluster
    st.sidebar.markdown(
        '<div class="nav-section-label">Core</div>',
        unsafe_allow_html=True,
    )

    for section in SECTIONS:
        key   = section["key"]
        icon  = section["icon"]
        label = section["label"]
        color = section["color"]

        # Insert second cluster label before trade_macro
        if key == "trade_macro":
            st.sidebar.markdown(
                '<div class="nav-section-label">Analysis</div>',
                unsafe_allow_html=True,
            )

        is_active = key == active_key

        # Badge markup — risk section uses legacy alert count; intelligence uses unread v2 count
        badge_html = ""
        if key == "risk" and n_alerts > 0:
            badge_html = f'<span class="nav-alert-badge">{n_alerts}</span>'
        elif key == "intelligence" and unread_alert_count > 0:
            badge_html = f'<span class="nav-alert-badge">{unread_alert_count}</span>'

        # Determine CSS wrapper class and inline CSS variables for active color
        wrapper_class = "nav-btn-active" if is_active else "nav-btn-inactive"
        if is_active:
            bg_rgba     = _hex_to_rgba(color, 0.13)
            border_rgba = _hex_to_rgba(color, 0.45)
            inline_style = (
                f'style="--nav-btn-bg:{bg_rgba};'
                f'--nav-btn-border:{border_rgba};'
                f'--nav-btn-accent:{color};"'
            )
        else:
            inline_style = ""

        # Wrap button in a styled div; the st.button provides real interactivity
        st.sidebar.markdown(
            f'<div class="nav-btn-wrap"><div class="{wrapper_class}" {inline_style}>',
            unsafe_allow_html=True,
        )

        btn_label = f"{icon}  {label}{badge_html}"
        _btn_text = f"{icon}  {label}"
        if key == "risk" and n_alerts > 0:
            _btn_text += f"  🔴 {n_alerts}"
        elif key == "intelligence" and unread_alert_count > 0:
            _btn_text += f"  🔔 {unread_alert_count}"
        if st.sidebar.button(
            _btn_text,
            key=f"nav_btn_{key}",
            use_container_width=True,
            help=section["description"],
        ):
            st.session_state[_NAV_KEY] = key
            st.rerun()

        st.sidebar.markdown("</div></div>", unsafe_allow_html=True)

    # ── Mini health indicator ─────────────────────────────────────────────────
    health_class, health_label = _health_status(insights, stock_data)
    st.sidebar.markdown(
        f"""
        <div class="nav-health">
            <span class="health-dot {health_class}"></span>
            <span>{health_label}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    return st.session_state[_NAV_KEY]


def render_mini_stats(
    port_count: int,
    route_count: int,
    insight_count: int,
    high_count: int,
) -> None:
    """Render a compact stats strip in the sidebar.

    Parameters
    ----------
    port_count:    Number of ports tracked.
    route_count:   Number of active routes.
    insight_count: Total insight records loaded.
    high_count:    Count of high-priority / high-demand signals.
    """
    st.sidebar.markdown(
        f"""
        <div class="mini-stats">
            <div class="mini-stat-item">
                <span class="mini-stat-value">{port_count}</span>
                <span class="mini-stat-label">Ports</span>
            </div>
            <div class="mini-stat-item">
                <span class="mini-stat-value">{route_count}</span>
                <span class="mini-stat-label">Routes</span>
            </div>
            <div class="mini-stat-item">
                <span class="mini-stat-value">{insight_count}</span>
                <span class="mini-stat-label">Signals</span>
            </div>
            <div class="mini-stat-item">
                <span class="mini-stat-value mini-stat-high">{high_count}</span>
                <span class="mini-stat-label">High</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_breadcrumb(section: dict, sub_page: str = "") -> None:
    """Render a breadcrumb / section header in the main content area.

    Parameters
    ----------
    section:  A section dict from SECTIONS (or from get_active_section()).
    sub_page: The currently visible sub-page name, e.g. ``"Overview"``.
    """
    icon  = section.get("icon", "")
    label = section.get("label", "")
    desc  = section.get("description", "")
    color = section.get("color", C_ACCENT)

    sub_html = ""
    if sub_page:
        sub_html = (
            f'<span class="nav-bc-sep">›</span>'
            f'<span class="nav-bc-sub">{sub_page}</span>'
        )

    desc_html = f'<span class="nav-bc-desc">{desc}</span>' if desc else ""

    gradient = (
        f"linear-gradient(90deg, {color} 0%, {_hex_to_rgba(color, 0.0)} 100%)"
    )

    st.markdown(
        f"""
        <div class="nav-breadcrumb">
            <span class="nav-bc-icon">{icon}</span>
            <span class="nav-bc-section" style="color:{color};">{label}</span>
            {sub_html}
            {desc_html}
            <div class="nav-bc-bar" style="background:{gradient};"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
