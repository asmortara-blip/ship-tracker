"""Global CSS design system for Ship Tracker — professional financial intelligence platform."""
from __future__ import annotations
import streamlit as st


# ── Shared color constants (also used inline in Python) ──────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"   # green  — High demand / bullish
C_MOD     = "#f59e0b"   # amber  — Moderate / neutral
C_LOW     = "#ef4444"   # red    — Low demand / bearish
C_ACCENT  = "#3b82f6"   # blue   — Primary accent
C_CONV    = "#8b5cf6"   # purple — Convergence signals
C_MACRO   = "#06b6d4"   # cyan   — Macro signals
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

CATEGORY_COLORS = {
    "CONVERGENCE": C_CONV,
    "ROUTE":       C_ACCENT,
    "PORT_DEMAND": C_HIGH,
    "MACRO":       C_MACRO,
}

DEMAND_COLORS = {
    "High":     C_HIGH,
    "Moderate": C_MOD,
    "Low":      C_LOW,
}

ACTION_COLORS = {
    "Prioritize": C_HIGH,
    "Monitor":    C_ACCENT,
    "Watch":      C_TEXT2,
    "Caution":    C_MOD,
    "Avoid":      C_LOW,
}

RISK_COLORS = {
    "LOW":      C_HIGH,
    "MODERATE": C_MOD,
    "HIGH":     C_LOW,
    "CRITICAL": "#b91c1c",
}


# ─────────────────────────────────────────────────────────────────────────────
#  PLOTLY LAYOUT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def dark_layout(
    *,
    title: str = "",
    height: int = 400,
    margin: dict | None = None,
    showlegend: bool = True,
    legend_orientation: str = "h",
) -> dict:
    """Return a Plotly layout dict with consistent dark ship-tracker theme."""
    if margin is None:
        margin = {"l": 20, "r": 20, "t": 40 if title else 20, "b": 20}
    return {
        "template": "plotly_dark",
        "paper_bgcolor": "#0a0f1a",
        "plot_bgcolor": "#111827",
        "font": {"color": "#f1f5f9", "family": "Inter, sans-serif", "size": 12},
        "title": {"text": title, "font": {"size": 14, "color": "#f1f5f9"}, "x": 0.01} if title else {},
        "height": height,
        "margin": margin,
        "showlegend": showlegend,
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "rgba(255,255,255,0.1)",
            "font": {"color": "#94a3b8", "size": 11},
            "orientation": legend_orientation,
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
        "xaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.1)",
            "tickfont": {"color": "#64748b", "size": 11},
            "linecolor": "rgba(255,255,255,0.1)",
        },
        "yaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.1)",
            "tickfont": {"color": "#64748b", "size": 11},
            "linecolor": "rgba(255,255,255,0.1)",
        },
        "hoverlabel": {
            "bgcolor": "#1a2235",
            "bordercolor": "rgba(255,255,255,0.15)",
            "font": {"color": "#f1f5f9", "size": 12},
        },
    }


def apply_dark_layout(fig, **kwargs) -> None:
    """Apply dark_layout() to a Plotly figure in-place."""
    fig.update_layout(**dark_layout(**kwargs))


# ─────────────────────────────────────────────────────────────────────────────
#  GLOBAL CSS INJECTION
# ─────────────────────────────────────────────────────────────────────────────

def inject_global_css() -> None:
    """Inject the global CSS design system. Call once at app startup."""
    st.markdown(f"""
    <style>
    /* ── Google Fonts: Inter + JetBrains Mono ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

    /* ── CSS Custom Properties ── */
    :root {{
        --bg:           {C_BG};
        --surface:      {C_SURFACE};
        --card:         {C_CARD};
        --card-hover:   #1e2d47;
        --border:       {C_BORDER};
        --border-hover: rgba(255,255,255,0.16);
        --high:         {C_HIGH};
        --mod:          {C_MOD};
        --low:          {C_LOW};
        --accent:       {C_ACCENT};
        --accent-dark:  #2563eb;
        --accent-glow:  rgba(59,130,246,0.35);
        --conv:         {C_CONV};
        --macro:        {C_MACRO};
        --text:         {C_TEXT};
        --text2:        {C_TEXT2};
        --text3:        {C_TEXT3};
        --radius:       8px;
        --radius-lg:    12px;
        --radius-xl:    16px;
        --mono:         'JetBrains Mono', 'Fira Code', monospace;
        --sans:         'Inter', system-ui, -apple-system, sans-serif;
        --transition:   all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
        --shadow-card:  0 4px 24px rgba(0,0,0,0.4), 0 1px 4px rgba(0,0,0,0.2);
        --shadow-lift:  0 8px 32px rgba(0,0,0,0.5), 0 2px 8px rgba(0,0,0,0.3);
        --shadow-glow:  0 0 0 1px rgba(59,130,246,0.3), 0 4px 20px rgba(59,130,246,0.2);
    }}

    /* ── Base Reset ── */
    html, body, [class*="css"] {{
        font-family: var(--sans) !important;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }}

    /* ── Main content area ── */
    .main .block-container {{
        padding-top: 1.25rem;
        padding-bottom: 3rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
        max-width: 1440px;
    }}

    /* ── Hide only footer and deploy button, keep header ── */
    footer {{ visibility: hidden; }}
    .stDeployButton {{ display: none !important; }}
    #MainMenu {{ visibility: hidden; }}

    /* ════════════════════════════════════════════════
       SCROLLBARS — thin, dark, blue thumb
    ════════════════════════════════════════════════ */
    ::-webkit-scrollbar {{
        width: 6px;
        height: 6px;
    }}
    ::-webkit-scrollbar-track {{
        background: var(--surface);
    }}
    ::-webkit-scrollbar-thumb {{
        background: rgba(59,130,246,0.45);
        border-radius: 3px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
        background: rgba(59,130,246,0.75);
    }}
    ::-webkit-scrollbar-corner {{
        background: var(--surface);
    }}

    /* ════════════════════════════════════════════════
       SIDEBAR
    ════════════════════════════════════════════════ */
    section[data-testid="stSidebar"] {{
        background: var(--surface) !important;
        border-right: 1px solid var(--border);
    }}
    section[data-testid="stSidebar"] .stMarkdown p {{
        font-size: 0.82rem;
        color: var(--text2);
    }}
    section[data-testid="stSidebar"] > div {{
        padding-top: 0.75rem;
    }}

    /* Sidebar nav items — custom HTML classes */
    .nav-item {{
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 9px 14px;
        border-radius: var(--radius);
        margin: 2px 0;
        color: var(--text2);
        font-size: 0.86rem;
        font-weight: 500;
        cursor: pointer;
        transition: var(--transition);
        border-left: 3px solid transparent;
        text-decoration: none;
    }}
    .nav-item:hover {{
        background: rgba(59,130,246,0.08);
        color: var(--text);
        border-left-color: rgba(59,130,246,0.4);
    }}
    .nav-item-active {{
        background: rgba(59,130,246,0.14) !important;
        color: var(--text) !important;
        border-left-color: var(--accent) !important;
        font-weight: 600 !important;
    }}

    /* Section navigation */
    .section-nav {{
        display: flex;
        flex-direction: column;
        gap: 2px;
        padding: 6px 0;
    }}
    .section-nav-item {{
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 16px;
        border-radius: var(--radius);
        color: var(--text2);
        font-size: 0.85rem;
        font-weight: 500;
        cursor: pointer;
        transition: var(--transition);
        border-left: 3px solid transparent;
        background: transparent;
        user-select: none;
    }}
    .section-nav-item .nav-icon {{
        font-size: 1rem;
        width: 20px;
        text-align: center;
        flex-shrink: 0;
    }}
    .section-nav-item .nav-label {{
        flex: 1;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .section-nav-item:hover {{
        background: rgba(255,255,255,0.05);
        color: var(--text);
        border-left-color: rgba(59,130,246,0.4);
    }}
    .section-nav-item.active {{
        background: rgba(59,130,246,0.12);
        color: #93c5fd;
        border-left-color: var(--accent);
        font-weight: 600;
    }}

    /* ════════════════════════════════════════════════
       TABS
    ════════════════════════════════════════════════ */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        background: var(--surface);
        border-radius: var(--radius);
        padding: 4px;
        border: 1px solid var(--border);
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 6px;
        padding: 7px 18px;
        font-weight: 500;
        font-size: 0.86rem;
        color: var(--text2);
        background: transparent;
        border: none;
        transition: var(--transition);
    }}
    .stTabs [data-baseweb="tab"]:hover {{
        color: var(--text) !important;
        background: rgba(255,255,255,0.05) !important;
    }}
    .stTabs [aria-selected="true"] {{
        background: var(--accent) !important;
        color: white !important;
        box-shadow: 0 2px 8px rgba(59,130,246,0.4) !important;
    }}
    .stTabs [data-baseweb="tab-panel"] {{
        padding-top: 1.25rem;
        padding-bottom: 0.5rem;
    }}

    /* ════════════════════════════════════════════════
       STREAMLIT METRIC WIDGET OVERRIDES
    ════════════════════════════════════════════════ */
    [data-testid="stMetric"] {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 14px 18px !important;
        transition: var(--transition);
    }}
    [data-testid="stMetric"]:hover {{
        border-color: var(--border-hover);
        box-shadow: var(--shadow-card);
    }}
    [data-testid="stMetricLabel"] {{
        font-size: 0.72rem !important;
        font-weight: 700 !important;
        color: var(--text3) !important;
        text-transform: uppercase;
        letter-spacing: 0.07em;
    }}
    [data-testid="stMetricValue"] {{
        font-size: 1.65rem !important;
        font-weight: 700 !important;
        color: var(--text) !important;
        font-family: var(--mono) !important;
    }}
    [data-testid="stMetricDelta"] {{
        font-size: 0.78rem !important;
        font-family: var(--mono) !important;
    }}

    /* ════════════════════════════════════════════════
       DATAFRAMES
    ════════════════════════════════════════════════ */
    [data-testid="stDataFrame"] {{
        border: 1px solid var(--border);
        border-radius: var(--radius-lg) !important;
        overflow: hidden !important;
        box-shadow: var(--shadow-card);
    }}

    /* ════════════════════════════════════════════════
       BUTTONS
    ════════════════════════════════════════════════ */
    .stButton > button {{
        background: var(--accent);
        color: white;
        border: none;
        border-radius: var(--radius);
        font-weight: 600;
        font-size: 0.85rem;
        letter-spacing: 0.02em;
        transition: var(--transition);
    }}
    .stButton > button:hover {{
        background: var(--accent-dark);
        transform: translateY(-1px);
        box-shadow: 0 4px 16px rgba(59,130,246,0.4);
    }}
    .stButton > button:active {{
        transform: translateY(0px);
    }}

    /* ════════════════════════════════════════════════
       EXPANDERS
    ════════════════════════════════════════════════ */
    [data-testid="stExpander"] {{
        border: 1px solid var(--border) !important;
        border-radius: var(--radius-lg) !important;
        background: var(--card) !important;
        transition: var(--transition);
        overflow: hidden;
    }}
    [data-testid="stExpander"]:hover {{
        border-color: var(--border-hover) !important;
    }}
    .streamlit-expanderHeader {{
        background: transparent !important;
        border: none !important;
        border-radius: var(--radius-lg) !important;
        font-size: 0.86rem !important;
        font-weight: 600 !important;
        color: var(--text2) !important;
        padding: 14px 18px !important;
    }}
    .streamlit-expanderContent {{
        border-top: 1px solid var(--border) !important;
        padding: 14px 18px !important;
    }}

    /* ════════════════════════════════════════════════
       FORM ELEMENTS
    ════════════════════════════════════════════════ */
    .stSelectbox > div > div {{
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        color: var(--text) !important;
        transition: var(--transition);
    }}
    .stSelectbox > div > div:hover {{
        border-color: var(--border-hover) !important;
    }}
    [data-baseweb="select"]:focus-within {{
        box-shadow: 0 0 0 2px rgba(59,130,246,0.35) !important;
        border-color: var(--accent) !important;
    }}
    .stSlider > div > div > div > div {{
        background: var(--accent) !important;
    }}
    .stProgress > div > div > div {{
        background: linear-gradient(90deg, var(--accent), var(--macro)) !important;
        border-radius: 4px;
    }}

    /* ════════════════════════════════════════════════
       ALERTS
    ════════════════════════════════════════════════ */
    .stInfo    {{ background: rgba(59,130,246,0.08);  border-color: {C_ACCENT}; border-radius: var(--radius); border-left-width: 3px; }}
    .stSuccess {{ background: rgba(16,185,129,0.08);  border-color: {C_HIGH};   border-radius: var(--radius); border-left-width: 3px; }}
    .stWarning {{ background: rgba(245,158,11,0.08);  border-color: {C_MOD};    border-radius: var(--radius); border-left-width: 3px; }}
    .stError   {{ background: rgba(239,68,68,0.08);   border-color: {C_LOW};    border-radius: var(--radius); border-left-width: 3px; }}

    /* ════════════════════════════════════════════════
       DIVIDERS
    ════════════════════════════════════════════════ */
    hr {{
        border: none !important;
        height: 1px !important;
        background: var(--border) !important;
        margin: 1.75rem 0 !important;
    }}

    /* ════════════════════════════════════════════════
       KEYFRAME ANIMATIONS
    ════════════════════════════════════════════════ */
    @keyframes pulse-glow {{
        0%, 100% {{ box-shadow: 0 0 8px rgba(16,185,129,0.3); }}
        50%       {{ box-shadow: 0 0 24px rgba(16,185,129,0.7), 0 0 48px rgba(16,185,129,0.25); }}
    }}
    @keyframes pulse-glow-red {{
        0%, 100% {{ box-shadow: 0 0 8px rgba(239,68,68,0.4), 0 0 0 0 rgba(239,68,68,0.15); }}
        50%       {{ box-shadow: 0 0 24px rgba(239,68,68,0.8), 0 0 48px rgba(239,68,68,0.3); }}
    }}
    @keyframes pulse-dot {{
        0%, 100% {{ opacity: 1;   transform: scale(1);   }}
        50%       {{ opacity: 0.5; transform: scale(1.35); }}
    }}
    @keyframes slide-in-up {{
        from {{ opacity: 0; transform: translateY(20px); }}
        to   {{ opacity: 1; transform: translateY(0);    }}
    }}
    @keyframes slide-in-left {{
        from {{ opacity: 0; transform: translateX(-16px); }}
        to   {{ opacity: 1; transform: translateX(0);     }}
    }}
    @keyframes fade-in {{
        from {{ opacity: 0; }}
        to   {{ opacity: 1; }}
    }}
    @keyframes page-enter {{
        from {{ opacity: 0; transform: translateY(10px) scale(0.995); }}
        to   {{ opacity: 1; transform: translateY(0)    scale(1);     }}
    }}
    @keyframes rotate-border {{
        0%   {{ background-position:   0% 50%; }}
        50%  {{ background-position: 100% 50%; }}
        100% {{ background-position:   0% 50%; }}
    }}
    @keyframes shimmer {{
        0%   {{ background-position: -300% 0; }}
        100% {{ background-position:  300% 0; }}
    }}
    @keyframes ticker-scroll {{
        0%   {{ transform: translateX(0);    }}
        100% {{ transform: translateX(-50%); }}
    }}
    @keyframes hero-drift {{
        0%   {{ background-position:   0% 50%; }}
        50%  {{ background-position: 100% 50%; }}
        100% {{ background-position:   0% 50%; }}
    }}
    @keyframes neon-flicker {{
        0%, 95%, 100% {{ opacity: 1; }}
        96%            {{ opacity: 0.85; }}
        97%            {{ opacity: 1; }}
        98%            {{ opacity: 0.9; }}
    }}
    @keyframes underline-grow {{
        from {{ transform: scaleX(0); }}
        to   {{ transform: scaleX(1); }}
    }}

    /* ════════════════════════════════════════════════
       SHIP CARD (base component)
    ════════════════════════════════════════════════ */
    .ship-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 16px 20px;
        margin-bottom: 10px;
        transition: var(--transition);
        box-shadow: var(--shadow-card);
    }}
    .ship-card:hover {{
        border-color: var(--border-hover);
        transform: translateY(-2px);
        box-shadow: var(--shadow-lift);
    }}
    .ship-card-accent {{
        border-left: 3px solid var(--accent);
    }}

    /* ════════════════════════════════════════════════
       KPI CARDS — enhanced
    ════════════════════════════════════════════════ */
    .kpi-card {{
        background: linear-gradient(135deg, var(--card) 0%, rgba(26,34,53,0.7) 100%);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 20px 22px;
        text-align: center;
        height: 100%;
        transition: var(--transition);
        box-shadow: var(--shadow-card);
        position: relative;
        overflow: hidden;
    }}
    .kpi-card::before {{
        content: '';
        position: absolute;
        inset: 0;
        background: linear-gradient(135deg, rgba(255,255,255,0.03) 0%, transparent 60%);
        pointer-events: none;
    }}
    .kpi-card:hover {{
        border-color: var(--border-hover);
        transform: translateY(-3px);
        box-shadow: var(--shadow-lift);
    }}
    .kpi-value {{
        font-size: 2.1rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.1;
        margin: 6px 0;
        font-family: var(--mono);
        letter-spacing: -0.02em;
    }}
    .kpi-label {{
        font-size: 0.7rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }}
    .kpi-delta {{
        font-size: 0.8rem;
        margin-top: 5px;
        font-family: var(--mono);
        font-weight: 500;
    }}

    /* ════════════════════════════════════════════════
       HERO STAT — big number with neon glow
    ════════════════════════════════════════════════ */
    .hero-stat {{
        font-family: var(--mono);
        font-size: 3.5rem;
        font-weight: 700;
        letter-spacing: -0.03em;
        line-height: 1;
        color: var(--text);
        text-shadow:
            0 0 20px rgba(59,130,246,0.5),
            0 0 60px rgba(59,130,246,0.2),
            0 0 100px rgba(59,130,246,0.1);
    }}
    .hero-stat-green {{
        color: var(--high);
        text-shadow:
            0 0 20px rgba(16,185,129,0.5),
            0 0 60px rgba(16,185,129,0.2),
            0 0 100px rgba(16,185,129,0.1);
    }}

    /* ════════════════════════════════════════════════
       HERO BACKGROUND — animated gradient
    ════════════════════════════════════════════════ */
    .hero-bg {{
        background: linear-gradient(
            135deg,
            rgba(59,130,246,0.07) 0%,
            rgba(139,92,246,0.05) 35%,
            rgba(6,182,212,0.05) 65%,
            rgba(16,185,129,0.04) 100%
        );
        background-size: 400% 400%;
        animation: hero-drift 12s ease infinite;
        border-radius: var(--radius-xl);
        border: 1px solid var(--border);
        padding: 28px 32px;
    }}

    /* ════════════════════════════════════════════════
       SECTION BANNER — gradient text + animated underline
    ════════════════════════════════════════════════ */
    .section-banner {{
        margin-bottom: 20px;
        padding-bottom: 14px;
        position: relative;
    }}
    .section-banner-title {{
        font-size: 1.35rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        background: linear-gradient(135deg, var(--text) 0%, rgba(148,163,184,0.9) 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        display: inline-block;
    }}
    .section-banner-subtitle {{
        font-size: 0.84rem;
        color: var(--text3);
        margin-top: 3px;
    }}
    .section-banner-underline {{
        position: absolute;
        bottom: 0;
        left: 0;
        height: 2px;
        width: 48px;
        background: linear-gradient(90deg, var(--accent), var(--macro));
        border-radius: 1px;
        animation: underline-grow 0.5s cubic-bezier(0.4, 0, 0.2, 1) both;
        transform-origin: left;
    }}

    /* Sub-section header (within a page section) */
    .sub-section-header {{
        font-size: 0.82rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.1em;
        padding: 4px 0;
        border-bottom: 1px solid var(--border);
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .sub-section-header::before {{
        content: '';
        display: inline-block;
        width: 3px;
        height: 12px;
        background: var(--accent);
        border-radius: 2px;
    }}

    /* ════════════════════════════════════════════════
       SECTION LABEL (small utility)
    ════════════════════════════════════════════════ */
    .section-label {{
        font-size: 0.7rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.09em;
        margin-bottom: 8px;
    }}

    /* ════════════════════════════════════════════════
       BADGES
    ════════════════════════════════════════════════ */
    .badge {{
        display: inline-flex;
        align-items: center;
        padding: 3px 11px;
        border-radius: 999px;
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        transition: var(--transition);
        box-shadow: 0 0 8px var(--badge-glow, transparent);
    }}
    .badge:hover {{
        box-shadow: 0 0 16px var(--badge-glow, rgba(59,130,246,0.3));
        transform: translateY(-1px);
    }}

    /* ════════════════════════════════════════════════
       STATUS CHIP
    ════════════════════════════════════════════════ */
    .status-chip {{
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 4px 12px;
        border-radius: var(--radius);
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}
    .status-chip-dot {{
        width: 6px;
        height: 6px;
        border-radius: 50%;
        flex-shrink: 0;
    }}

    /* ════════════════════════════════════════════════
       INSIGHT CARDS — animated entry + hover lift
    ════════════════════════════════════════════════ */
    .insight-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 16px 20px;
        margin-bottom: 10px;
        transition: var(--transition);
        box-shadow: var(--shadow-card);
        animation: slide-in-up 0.4s cubic-bezier(0.4, 0, 0.2, 1) both;
    }}
    .insight-card:hover {{
        border-color: var(--border-hover);
        transform: translateY(-3px);
        box-shadow: var(--shadow-lift);
    }}
    .insight-card:nth-child(1) {{ animation-delay: 0.00s; }}
    .insight-card:nth-child(2) {{ animation-delay: 0.05s; }}
    .insight-card:nth-child(3) {{ animation-delay: 0.10s; }}
    .insight-card:nth-child(4) {{ animation-delay: 0.15s; }}
    .insight-card:nth-child(5) {{ animation-delay: 0.20s; }}
    .insight-card:nth-child(6) {{ animation-delay: 0.25s; }}

    /* ════════════════════════════════════════════════
       METRIC GRID
    ════════════════════════════════════════════════ */
    .metric-grid {{
        display: grid;
        gap: 14px;
        margin-bottom: 14px;
    }}

    /* Grid layout utilities */
    .grid-2 {{
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 14px;
    }}
    .grid-3 {{
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 14px;
    }}
    .grid-4 {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 14px;
    }}
    @media (max-width: 1024px) {{
        .grid-4 {{ grid-template-columns: repeat(2, 1fr); }}
        .grid-3 {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media (max-width: 640px) {{
        .grid-4, .grid-3, .grid-2 {{ grid-template-columns: 1fr; }}
        .hero-stat {{ font-size: 2.2rem; }}
        .kpi-value {{ font-size: 1.6rem; }}
    }}

    /* ════════════════════════════════════════════════
       DATA TABLE — custom beautiful styling
    ════════════════════════════════════════════════ */
    .data-table {{
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        overflow: hidden;
        font-size: 0.84rem;
        box-shadow: var(--shadow-card);
    }}
    .data-table thead tr {{
        background: rgba(255,255,255,0.04);
    }}
    .data-table thead th {{
        padding: 11px 16px;
        text-align: left;
        font-size: 0.7rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        border-bottom: 1px solid var(--border);
        white-space: nowrap;
    }}
    .data-table tbody tr {{
        transition: var(--transition);
    }}
    .data-table tbody tr:hover {{
        background: rgba(59,130,246,0.05);
    }}
    .data-table tbody tr:not(:last-child) td {{
        border-bottom: 1px solid rgba(255,255,255,0.04);
    }}
    .data-table tbody td {{
        padding: 10px 16px;
        color: var(--text2);
        vertical-align: middle;
    }}
    .data-table .mono {{
        font-family: var(--mono);
        font-size: 0.82rem;
        color: var(--text);
    }}

    /* ════════════════════════════════════════════════
       ROUTE CARD
    ════════════════════════════════════════════════ */
    .route-card {{
        background: linear-gradient(135deg, var(--card), rgba(26,34,53,0.6));
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 18px 22px;
        transition: var(--transition);
        box-shadow: var(--shadow-card);
        position: relative;
        overflow: hidden;
    }}
    .route-card::after {{
        content: '';
        position: absolute;
        top: 0; right: 0;
        width: 80px; height: 80px;
        background: radial-gradient(circle at top right, rgba(59,130,246,0.08), transparent 70%);
        pointer-events: none;
    }}
    .route-card:hover {{
        border-color: rgba(59,130,246,0.35);
        transform: translateY(-2px);
        box-shadow: 0 8px 32px rgba(59,130,246,0.12), var(--shadow-lift);
    }}
    .route-label {{
        font-size: 0.68rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--text3);
    }}
    .route-name {{
        font-size: 1.05rem;
        font-weight: 700;
        color: var(--text);
        margin: 4px 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .route-arrow {{
        color: var(--accent);
        font-size: 0.9rem;
    }}

    /* ════════════════════════════════════════════════
       PORT CARD
    ════════════════════════════════════════════════ */
    .port-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 16px 20px;
        transition: var(--transition);
        box-shadow: var(--shadow-card);
    }}
    .port-card:hover {{
        border-color: rgba(6,182,212,0.3);
        box-shadow: 0 0 24px rgba(6,182,212,0.1), var(--shadow-lift);
        transform: translateY(-2px);
    }}
    .port-name {{
        font-size: 1rem;
        font-weight: 700;
        color: var(--text);
        margin-bottom: 6px;
    }}
    .port-detail {{
        font-size: 0.8rem;
        color: var(--text2);
    }}

    /* ════════════════════════════════════════════════
       GLASS PANEL — strong glassmorphism
    ════════════════════════════════════════════════ */
    .glass-panel {{
        background: rgba(26, 34, 53, 0.65) !important;
        backdrop-filter: blur(20px) saturate(1.5) !important;
        -webkit-backdrop-filter: blur(20px) saturate(1.5) !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: var(--radius-xl) !important;
        box-shadow:
            0 8px 32px rgba(0,0,0,0.4),
            inset 0 1px 0 rgba(255,255,255,0.06) !important;
    }}
    .glass-card {{
        background: rgba(26, 34, 53, 0.7) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 12px !important;
    }}

    /* ════════════════════════════════════════════════
       NEON TEXT GLOW CLASSES
    ════════════════════════════════════════════════ */
    .neon-green {{
        color: var(--high) !important;
        text-shadow:
            0 0 10px rgba(16,185,129,0.7),
            0 0 30px rgba(16,185,129,0.35),
            0 0 60px rgba(16,185,129,0.15);
        animation: neon-flicker 8s infinite;
    }}
    .neon-blue {{
        color: #60a5fa !important;
        text-shadow:
            0 0 10px rgba(96,165,250,0.7),
            0 0 30px rgba(96,165,250,0.35),
            0 0 60px rgba(96,165,250,0.15);
    }}
    .neon-amber {{
        color: var(--mod) !important;
        text-shadow:
            0 0 10px rgba(245,158,11,0.7),
            0 0 30px rgba(245,158,11,0.35),
            0 0 60px rgba(245,158,11,0.15);
    }}

    /* ════════════════════════════════════════════════
       GLOW UTILITY CLASSES
    ════════════════════════════════════════════════ */
    .pulse-green   {{ animation: pulse-glow 2.5s ease-in-out infinite; }}
    .pulse-dot     {{
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        background: #10b981;
        animation: pulse-dot 1.5s ease-in-out infinite;
        flex-shrink: 0;
    }}
    .slide-in      {{ animation: slide-in-up 0.4s cubic-bezier(0.4,0,0.2,1) both; }}
    .fade-in       {{ animation: fade-in 0.6s ease-out both; }}
    .page-enter    {{ animation: page-enter 0.45s cubic-bezier(0.4,0,0.2,1) both; }}
    .glow-green    {{ box-shadow: 0 0 20px rgba(16,185,129,0.25), 0 0 60px rgba(16,185,129,0.08); }}
    .glow-blue     {{ box-shadow: 0 0 20px rgba(59,130,246,0.25), 0 0 60px rgba(59,130,246,0.08); }}
    .glow-red      {{ box-shadow: 0 0 20px rgba(239,68,68,0.25), 0 0 60px rgba(239,68,68,0.08); }}

    /* ════════════════════════════════════════════════
       GRADIENT TEXT
    ════════════════════════════════════════════════ */
    .gradient-text-green {{
        background: linear-gradient(135deg, #10b981, #34d399);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }}
    .gradient-text-blue {{
        background: linear-gradient(135deg, #3b82f6, #60a5fa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }}
    .gradient-text-purple {{
        background: linear-gradient(135deg, #8b5cf6, #a78bfa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }}

    /* ════════════════════════════════════════════════
       SHIMMER LOADING SKELETON
    ════════════════════════════════════════════════ */
    .shimmer {{
        background: linear-gradient(
            105deg,
            rgba(26,34,53,0) 0%,
            rgba(26,34,53,0) 30%,
            rgba(46,64,97,0.6) 50%,
            rgba(26,34,53,0) 70%,
            rgba(26,34,53,0) 100%
        ), var(--card);
        background-size: 300% 100%;
        animation: shimmer 1.8s infinite linear;
        border-radius: var(--radius);
    }}
    .shimmer-loading {{
        background: linear-gradient(90deg, var(--card) 25%, #243050 50%, var(--card) 75%);
        background-size: 200% 100%;
        animation: shimmer 1.5s infinite;
        border-radius: 6px;
    }}

    /* ════════════════════════════════════════════════
       ROTATING BORDER
    ════════════════════════════════════════════════ */
    .rotating-border {{
        background: linear-gradient(270deg, #3b82f6, #10b981, #8b5cf6, #3b82f6);
        background-size: 400% 400%;
        animation: rotate-border 4s ease infinite;
        padding: 2px;
        border-radius: 13px;
    }}

    /* ════════════════════════════════════════════════
       TICKER BAR
    ════════════════════════════════════════════════ */
    .ticker-bar {{
        overflow: hidden;
        background: linear-gradient(
            90deg,
            rgba(17,24,39,0.95) 0%,
            rgba(26,34,53,0.9) 50%,
            rgba(17,24,39,0.95) 100%
        );
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: var(--radius);
        padding: 9px 0;
        width: 100%;
        position: relative;
    }}
    .ticker-bar::before,
    .ticker-bar::after {{
        content: '';
        position: absolute;
        top: 0; bottom: 0;
        width: 40px;
        z-index: 1;
        pointer-events: none;
    }}
    .ticker-bar::before {{
        left: 0;
        background: linear-gradient(90deg, rgba(10,15,26,0.9), transparent);
    }}
    .ticker-bar::after {{
        right: 0;
        background: linear-gradient(90deg, transparent, rgba(10,15,26,0.9));
    }}

    /* ════════════════════════════════════════════════
       ALERT — CRITICAL with pulsing red glow
    ════════════════════════════════════════════════ */
    .alert-critical {{
        background: rgba(185,28,28,0.1);
        border: 1px solid rgba(239,68,68,0.45);
        border-left: 4px solid #ef4444;
        border-radius: var(--radius-lg);
        padding: 16px 20px;
        animation: pulse-glow-red 2.5s ease-in-out infinite;
        display: flex;
        align-items: flex-start;
        gap: 12px;
    }}
    .alert-critical-icon {{
        font-size: 1.1rem;
        flex-shrink: 0;
        margin-top: 1px;
    }}
    .alert-critical-title {{
        font-size: 0.88rem;
        font-weight: 700;
        color: #fca5a5;
        margin-bottom: 3px;
    }}
    .alert-critical-body {{
        font-size: 0.82rem;
        color: #f87171;
        line-height: 1.5;
    }}

    /* ════════════════════════════════════════════════
       PROGRESS BAR CUSTOM
    ════════════════════════════════════════════════ */
    .progress-bar-custom {{
        width: 100%;
        height: 6px;
        background: rgba(255,255,255,0.08);
        border-radius: 3px;
        overflow: hidden;
        position: relative;
    }}
    .progress-bar-fill {{
        height: 100%;
        border-radius: 3px;
        transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
    }}
    .progress-bar-fill::after {{
        content: '';
        position: absolute;
        inset: 0;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
        animation: shimmer 2s infinite;
    }}

    /* ════════════════════════════════════════════════
       METRIC VALUE GLOW
    ════════════════════════════════════════════════ */
    [data-testid="stMetricValue"] {{
        text-shadow: 0 0 24px rgba(59,130,246,0.2);
    }}

    /* ════════════════════════════════════════════════
       INPUT WIDGETS
    ════════════════════════════════════════════════ */
    [data-baseweb="input"] {{
        background: var(--card) !important;
        border-color: var(--border) !important;
        border-radius: var(--radius) !important;
    }}
    [data-baseweb="input"]:focus-within {{
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 2px rgba(59,130,246,0.25) !important;
    }}

    </style>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert hex color to rgba string."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ─────────────────────────────────────────────────────────────────────────────
#  HTML COMPONENT BUILDERS (return HTML strings)
# ─────────────────────────────────────────────────────────────────────────────

def kpi_card(
    label: str,
    value: str,
    delta: str = "",
    delta_color: str = C_TEXT2,
    accent_color: str = C_ACCENT,
) -> str:
    """Return HTML for a styled KPI card with gradient background and glow on hover."""
    delta_html = (
        f'<div class="kpi-delta" style="color:{delta_color}">{delta}</div>'
        if delta else ""
    )
    glow_rgba = _hex_to_rgba(accent_color, 0.18)
    return f"""
    <div class="kpi-card" style="border-top:3px solid {accent_color}; --kpi-glow:{glow_rgba};">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        {delta_html}
    </div>
    """


def badge(text: str, color: str = C_ACCENT) -> str:
    """Return HTML for a colored status badge with border glow."""
    bg    = _hex_to_rgba(color, 0.14)
    bord  = _hex_to_rgba(color, 0.35)
    glow  = _hex_to_rgba(color, 0.25)
    return (
        f'<span class="badge" '
        f'style="background:{bg}; color:{color}; border:1px solid {bord}; '
        f'--badge-glow:{glow}">'
        f'{text}</span>'
    )


def section_header(title: str, subtitle: str = "") -> None:
    """Render a styled section header with animated underline."""
    sub_html = (
        f'<div class="section-banner-subtitle">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(f"""
    <div class="section-banner">
        <div class="section-banner-title">{title}</div>
        {sub_html}
        <div class="section-banner-underline"></div>
    </div>
    """, unsafe_allow_html=True)


def render_kpi_row(metrics: list[dict]) -> None:
    """Render a row of KPI cards. Each dict: {{label, value, delta?, delta_color?, accent?}}"""
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            st.markdown(
                kpi_card(
                    label=m["label"],
                    value=m["value"],
                    delta=m.get("delta", ""),
                    delta_color=m.get("delta_color", C_TEXT2),
                    accent_color=m.get("accent", C_ACCENT),
                ),
                unsafe_allow_html=True,
            )


def ticker_tape_html(items: list[dict]) -> str:
    """Generate a scrolling ticker tape HTML string.

    items: list of {{"label": str, "value": str, "change": float, "unit": str}}
    """
    def _item_html(item: dict) -> str:
        change = item.get("change", 0.0)
        arrow  = "▲" if change >= 0 else "▼"
        color  = "#10b981" if change >= 0 else "#ef4444"
        unit   = item.get("unit", "")
        return (
            f'<span style="display:inline-flex;align-items:center;gap:8px;'
            f'padding:0 28px;white-space:nowrap;font-size:0.81rem;">'
            f'<span style="color:#64748b;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.06em;">{item.get("label","")}</span>'
            f'<span style="color:#f1f5f9;font-weight:700;font-family:\'JetBrains Mono\',monospace;">'
            f'{item.get("value","")}{unit}</span>'
            f'<span style="color:{color};font-size:0.73rem;font-family:\'JetBrains Mono\',monospace;">'
            f'{arrow} {abs(change):.2f}</span>'
            f'<span style="color:#1e293b;padding:0 4px;">|</span>'
            f'</span>'
        )

    items_html     = "".join(_item_html(i) for i in items)
    ticker_content = items_html * 2  # duplicate for seamless loop
    duration       = max(12, len(items) * 4)
    return (
        f'<div class="ticker-bar">'
        f'<div style="display:inline-flex;animation:ticker-scroll {duration}s linear infinite;">'
        f'{ticker_content}'
        f'</div></div>'
    )


def live_badge(text: str = "LIVE") -> str:
    """Return HTML for a pulsing LIVE badge."""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;'
        f'background:rgba(16,185,129,0.1);color:#10b981;'
        f'border:1px solid rgba(16,185,129,0.3);'
        f'padding:4px 11px;border-radius:999px;font-size:0.71rem;font-weight:700;'
        f'letter-spacing:0.05em;">'
        f'<span class="pulse-dot"></span>{text}</span>'
    )


def gradient_card(content_html: str, border_color: str = "#3b82f6", glow: bool = True) -> str:
    """Wrap content in a glassmorphism gradient card."""
    glow_style = f"box-shadow:0 0 32px rgba(59,130,246,0.14),0 4px 24px rgba(0,0,0,0.35);" if glow else ""
    rgba_border = _hex_to_rgba(border_color, 0.25)
    return (
        f'<div style="background:linear-gradient(135deg,rgba(26,34,53,0.92),rgba(15,23,42,0.88));'
        f'border:1px solid {rgba_border};border-radius:14px;padding:22px;'
        f'backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);{glow_style}">'
        f'{content_html}</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
#  NEW HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def nav_section_button(icon: str, label: str, active: bool = False, key: str = "") -> bool:
    """Render a styled navigation section button. Returns True if clicked."""
    active_class = "active" if active else ""
    btn_html = f"""
    <div class="section-nav-item {active_class}" id="nav-{key or label.lower().replace(' ', '-')}">
        <span class="nav-icon">{icon}</span>
        <span class="nav-label">{label}</span>
        {'<span style="margin-left:auto;width:6px;height:6px;border-radius:50%;background:var(--accent);opacity:0.8;flex-shrink:0"></span>' if active else ''}
    </div>
    """
    st.markdown(btn_html, unsafe_allow_html=True)
    btn_key = key if key else f"nav_{label.lower().replace(' ', '_')}"
    return st.button(label, key=btn_key, use_container_width=True)


def page_header(
    title: str,
    subtitle: str = "",
    icon: str = "",
    badge_text: str = "",
    badge_color: str = "",
) -> None:
    """Render a beautiful page section header with optional badge."""
    icon_html = (
        f'<span style="font-size:1.6rem;line-height:1;filter:drop-shadow(0 0 8px rgba(59,130,246,0.4));">'
        f'{icon}</span>'
        if icon else ""
    )
    badge_html = ""
    if badge_text:
        bc     = badge_color or C_ACCENT
        bg     = _hex_to_rgba(bc, 0.14)
        bord   = _hex_to_rgba(bc, 0.35)
        badge_html = (
            f'<span style="display:inline-flex;align-items:center;padding:3px 12px;'
            f'background:{bg};color:{bc};border:1px solid {bord};'
            f'border-radius:999px;font-size:0.71rem;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.05em;">{badge_text}</span>'
        )
    sub_html = (
        f'<div style="color:var(--text3);font-size:0.85rem;margin-top:4px;font-weight:400;">'
        f'{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(f"""
    <div class="page-enter" style="margin-bottom:24px;">
        <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
            {icon_html}
            <div>
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                    <span style="font-size:1.5rem;font-weight:800;letter-spacing:-0.025em;
                        background:linear-gradient(135deg,{C_TEXT} 0%,rgba(148,163,184,0.85) 100%);
                        -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                        background-clip:text;">{title}</span>
                    {badge_html}
                </div>
                {sub_html}
            </div>
        </div>
        <div style="height:2px;margin-top:14px;
            background:linear-gradient(90deg,{C_ACCENT},{C_MACRO},transparent);
            border-radius:1px;opacity:0.6;"></div>
    </div>
    """, unsafe_allow_html=True)


def metric_card_row(metrics: list[dict], columns: int = 4) -> None:
    """Render a responsive row of metric cards.

    Each dict supports:
        label (str), value (str), delta (str, optional),
        delta_color (str, optional), accent (str, optional),
        icon (str, optional), sublabel (str, optional)
    """
    n    = min(columns, len(metrics))
    cols = st.columns(n)
    for i, (col, m) in enumerate(zip(cols, metrics)):
        with col:
            accent  = m.get("accent", C_ACCENT)
            icon    = m.get("icon", "")
            delta   = m.get("delta", "")
            d_color = m.get("delta_color", C_TEXT2)
            sub     = m.get("sublabel", "")
            icon_html  = (
                f'<div style="font-size:1.4rem;margin-bottom:6px;'
                f'filter:drop-shadow(0 0 6px {_hex_to_rgba(accent, 0.5)});">{icon}</div>'
                if icon else ""
            )
            delta_html = (
                f'<div style="font-size:0.78rem;color:{d_color};'
                f'font-family:\'JetBrains Mono\',monospace;font-weight:500;margin-top:4px;">'
                f'{delta}</div>'
                if delta else ""
            )
            sub_html = (
                f'<div style="font-size:0.72rem;color:var(--text3);margin-top:2px;">{sub}</div>'
                if sub else ""
            )
            delay = f"animation-delay:{i * 0.05:.2f}s"
            st.markdown(f"""
            <div class="kpi-card slide-in" style="border-top:3px solid {accent};{delay}">
                {icon_html}
                <div class="kpi-label">{m.get("label","")}</div>
                <div class="kpi-value">{m.get("value","")}</div>
                {delta_html}
                {sub_html}
            </div>
            """, unsafe_allow_html=True)


def insight_card_html(
    title: str,
    score: float,
    action: str,
    rationale: str = "",
    category: str = "",
) -> str:
    """Generate HTML for a styled insight card.

    score: 0.0 – 1.0 confidence / strength score
    action: Prioritize | Monitor | Watch | Caution | Avoid
    category: CONVERGENCE | ROUTE | PORT_DEMAND | MACRO
    """
    action_color  = ACTION_COLORS.get(action, C_TEXT2)
    cat_color     = CATEGORY_COLORS.get(category.upper(), C_TEXT3)
    score_pct     = int(score * 100)
    score_color   = C_HIGH if score >= 0.7 else (C_MOD if score >= 0.4 else C_LOW)
    bar_fill      = _hex_to_rgba(score_color, 0.85)
    action_bg     = _hex_to_rgba(action_color, 0.12)
    action_bord   = _hex_to_rgba(action_color, 0.35)
    cat_html      = (
        f'<span style="font-size:0.68rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.07em;color:{cat_color};margin-right:6px;">{category}</span>'
        if category else ""
    )
    rationale_html = (
        f'<p style="font-size:0.8rem;color:var(--text3);margin:8px 0 0;line-height:1.55;">'
        f'{rationale}</p>'
        if rationale else ""
    )
    return f"""
    <div class="insight-card">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px;">
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
                    {cat_html}
                </div>
                <div style="font-size:0.92rem;font-weight:700;color:var(--text);
                    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{title}</div>
                {rationale_html}
            </div>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex-shrink:0;">
                <span style="background:{action_bg};color:{action_color};border:1px solid {action_bord};
                    padding:3px 10px;border-radius:999px;font-size:0.7rem;font-weight:700;
                    text-transform:uppercase;letter-spacing:0.04em;white-space:nowrap;">{action}</span>
                <span style="font-family:'JetBrains Mono',monospace;font-size:0.82rem;
                    font-weight:700;color:{score_color};">{score_pct}%</span>
            </div>
        </div>
        <div class="progress-bar-custom">
            <div class="progress-bar-fill" style="width:{score_pct}%;background:{bar_fill};"></div>
        </div>
    </div>
    """


def status_badge(text: str, status: str = "info") -> str:
    """Generate HTML for a status badge.

    status: info | success | warning | danger | neutral
    """
    palette = {
        "info":    (C_ACCENT,  0.13, 0.35),
        "success": (C_HIGH,    0.13, 0.35),
        "warning": (C_MOD,     0.13, 0.35),
        "danger":  (C_LOW,     0.13, 0.35),
        "neutral": (C_TEXT3,   0.10, 0.20),
    }
    color, bg_a, bord_a = palette.get(status, palette["info"])
    bg   = _hex_to_rgba(color, bg_a)
    bord = _hex_to_rgba(color, bord_a)
    dot_colors = {
        "info":    C_ACCENT,
        "success": C_HIGH,
        "warning": C_MOD,
        "danger":  C_LOW,
        "neutral": C_TEXT3,
    }
    dot_color = dot_colors.get(status, C_ACCENT)
    pulse = ' animation:pulse-dot 1.5s ease-in-out infinite;' if status == "danger" else ""
    return (
        f'<span class="status-chip" style="background:{bg};color:{color};border:1px solid {bord};">'
        f'<span class="status-chip-dot" style="background:{dot_color};{pulse}"></span>'
        f'{text}</span>'
    )
