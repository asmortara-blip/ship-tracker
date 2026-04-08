"""Global CSS design system for Ship Tracker — Wall Street Journal editorial style.

Dark-mode adaptation of WSJ's signature editorial aesthetic: serif headlines,
clean data presentation, thin rule lines, generous whitespace, and muted
professional color palette.
"""
from __future__ import annotations
import streamlit as st


# ── Shared color constants ───────────────────────────────────────────────────
# WSJ-inspired dark palette: warm neutrals, restrained accent colors
C_BG      = "#0c0e14"       # deep ink
C_SURFACE = "#12151e"       # slightly lifted
C_CARD    = "#181c28"       # card background
C_BORDER  = "rgba(232,230,225,0.05)"
C_HIGH    = "#2e9e6e"       # muted green (WSJ market green)
C_MOD     = "#c9962b"       # warm amber/gold
C_LOW     = "#c0392b"       # WSJ market red
C_ACCENT  = "#3572b0"       # steel blue
C_CONV    = "#7c6eaf"       # muted purple
C_MACRO   = "#4a90a4"       # teal
C_TEXT    = "#e8e6e1"        # warm off-white (WSJ paper color)
C_TEXT2   = "#9a968e"        # warm mid gray
C_TEXT3   = "#6b6760"        # warm dark gray
C_RULE    = "rgba(232,230,225,0.12)"  # thin rule lines

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
    "CRITICAL": "#8b1a1a",
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
    """Return a Plotly layout dict with WSJ-inspired dark theme."""
    if margin is None:
        margin = {"l": 24, "r": 24, "t": 44 if title else 24, "b": 24}
    return {
        "template": "plotly_dark",
        "paper_bgcolor": C_BG,
        "plot_bgcolor": C_SURFACE,
        "font": {"color": C_TEXT2, "family": "'Libre Franklin', 'Inter', system-ui, sans-serif", "size": 12},
        "title": {
            "text": title,
            "font": {"size": 15, "color": C_TEXT, "family": "'Libre Baskerville', 'Georgia', serif", "weight": 700},
            "x": 0.01,
        } if title else {},
        "height": height,
        "margin": margin,
        "showlegend": showlegend,
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "rgba(232,230,225,0.05)",
            "font": {"color": C_TEXT2, "size": 11},
            "orientation": legend_orientation,
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
        "xaxis": {
            "gridcolor": "rgba(232,230,225,0.04)",
            "zerolinecolor": "rgba(232,230,225,0.06)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(232,230,225,0.06)",
        },
        "yaxis": {
            "gridcolor": "rgba(232,230,225,0.04)",
            "zerolinecolor": "rgba(232,230,225,0.06)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(232,230,225,0.06)",
        },
        "hoverlabel": {
            "bgcolor": C_CARD,
            "bordercolor": "rgba(255,255,255,0.1)",
            "font": {"color": C_TEXT, "size": 12},
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
    /* ── Google Fonts: Libre Baskerville (WSJ serif) + Libre Franklin (WSJ sans) + JetBrains Mono ── */
    @import url('https://fonts.googleapis.com/css2?family=Libre+Baskerville:ital,wght@0,400;0,700;1,400&family=Libre+Franklin:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

    /* ── CSS Custom Properties ── */
    :root {{
        --bg:           {C_BG};
        --surface:      {C_SURFACE};
        --card:         {C_CARD};
        --card-hover:   #1e2330;
        --border:       {C_BORDER};
        --border-hover: rgba(255,255,255,0.12);
        --rule:         {C_RULE};
        --high:         {C_HIGH};
        --mod:          {C_MOD};
        --low:          {C_LOW};
        --accent:       {C_ACCENT};
        --accent-dark:  #2a5a8c;
        --conv:         {C_CONV};
        --macro:        {C_MACRO};
        --text:         {C_TEXT};
        --text2:        {C_TEXT2};
        --text3:        {C_TEXT3};
        --radius:       6px;
        --radius-lg:    10px;
        --radius-xl:    14px;
        --mono:         'JetBrains Mono', 'Fira Code', monospace;
        --sans:         'Libre Franklin', 'Inter', system-ui, -apple-system, sans-serif;
        --serif:        'Libre Baskerville', 'Georgia', 'Times New Roman', serif;
        --transition:   all 0.2s ease;
        --shadow-card:  0 2px 12px rgba(0,0,0,0.3);
        --shadow-lift:  0 4px 20px rgba(0,0,0,0.4);
    }}

    /* ── Base Reset ── */
    html, body, [class*="css"] {{
        font-family: var(--sans) !important;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }}

    /* ── Main content area ── */
    .main .block-container {{
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        padding-left: 2rem;
        padding-right: 2rem;
        max-width: 1400px;
    }}

    /* ── Hide chrome ── */
    footer {{ visibility: hidden; }}
    .stDeployButton {{ display: none !important; }}
    #MainMenu {{ visibility: hidden; }}

    /* ════════════════════════════════════════════════
       SCROLLBARS — thin, subtle
    ════════════════════════════════════════════════ */
    ::-webkit-scrollbar {{
        width: 5px;
        height: 5px;
    }}
    ::-webkit-scrollbar-track {{
        background: var(--surface);
    }}
    ::-webkit-scrollbar-thumb {{
        background: rgba(154,150,142,0.3);
        border-radius: 3px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
        background: rgba(154,150,142,0.5);
    }}
    ::-webkit-scrollbar-corner {{
        background: var(--surface);
    }}

    /* ════════════════════════════════════════════════
       SIDEBAR
    ════════════════════════════════════════════════ */
    section[data-testid="stSidebar"] {{
        background: var(--surface) !important;
        border-right: 1px solid var(--rule);
    }}
    section[data-testid="stSidebar"] .stMarkdown p {{
        font-size: 0.82rem;
        color: var(--text2);
    }}
    section[data-testid="stSidebar"] > div {{
        padding-top: 0.75rem;
    }}

    /* Sidebar nav items */
    .nav-item {{
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 9px 14px;
        border-radius: var(--radius);
        margin: 2px 0;
        color: var(--text2);
        font-size: 0.84rem;
        font-weight: 500;
        cursor: pointer;
        transition: var(--transition);
        border-left: 2px solid transparent;
        text-decoration: none;
    }}
    .nav-item:hover {{
        background: rgba(53,114,176,0.06);
        color: var(--text);
        border-left-color: rgba(53,114,176,0.3);
    }}
    .nav-item-active {{
        background: rgba(53,114,176,0.1) !important;
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
        font-size: 0.84rem;
        font-weight: 500;
        cursor: pointer;
        transition: var(--transition);
        border-left: 2px solid transparent;
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
        background: rgba(232,230,225,0.03);
        color: var(--text);
        border-left-color: rgba(53,114,176,0.3);
    }}
    .section-nav-item.active {{
        background: rgba(53,114,176,0.08);
        color: var(--text);
        border-left-color: var(--accent);
        font-weight: 600;
    }}

    /* ════════════════════════════════════════════════
       TABS — WSJ understated style
    ════════════════════════════════════════════════ */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 0px;
        background: transparent;
        border-radius: 0;
        padding: 0;
        border-bottom: 2px solid var(--rule);
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 0;
        padding: 10px 20px;
        font-weight: 500;
        font-size: 0.84rem;
        color: var(--text3);
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        margin-bottom: -2px;
        transition: var(--transition);
        font-family: var(--sans);
        letter-spacing: 0.01em;
    }}
    .stTabs [data-baseweb="tab"]:hover {{
        color: var(--text) !important;
        background: transparent !important;
        border-bottom-color: rgba(53,114,176,0.3) !important;
    }}
    .stTabs [aria-selected="true"] {{
        background: transparent !important;
        color: var(--text) !important;
        border-bottom: 2px solid var(--accent) !important;
        box-shadow: none !important;
        font-weight: 600 !important;
    }}
    .stTabs [data-baseweb="tab-panel"] {{
        padding-top: 1.5rem;
        padding-bottom: 0.5rem;
    }}

    /* ════════════════════════════════════════════════
       STREAMLIT METRIC WIDGET OVERRIDES
    ════════════════════════════════════════════════ */
    [data-testid="stMetric"] {{
        background: var(--card);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        padding: 16px 20px !important;
        transition: var(--transition);
    }}
    [data-testid="stMetric"]:hover {{
        border-color: var(--border-hover);
    }}
    [data-testid="stMetricLabel"] {{
        font-size: 0.7rem !important;
        font-weight: 600 !important;
        color: var(--text3) !important;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-family: var(--sans) !important;
    }}
    [data-testid="stMetricValue"] {{
        font-size: 1.6rem !important;
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
        border: 1px solid var(--rule);
        border-radius: var(--radius) !important;
        overflow: hidden !important;
    }}

    /* ════════════════════════════════════════════════
       BUTTONS — understated WSJ
    ════════════════════════════════════════════════ */
    .stButton > button {{
        background: var(--card);
        color: var(--text);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        font-weight: 600;
        font-size: 0.84rem;
        font-family: var(--sans);
        letter-spacing: 0.01em;
        transition: var(--transition);
    }}
    .stButton > button:hover {{
        background: rgba(53,114,176,0.1);
        border-color: rgba(53,114,176,0.3);
        color: var(--text);
    }}
    .stButton > button:active {{
        background: rgba(53,114,176,0.15);
    }}

    /* ════════════════════════════════════════════════
       EXPANDERS
    ════════════════════════════════════════════════ */
    [data-testid="stExpander"] {{
        border: 1px solid var(--rule) !important;
        border-radius: var(--radius) !important;
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
        border-radius: var(--radius) !important;
        font-size: 0.86rem !important;
        font-weight: 600 !important;
        color: var(--text2) !important;
        padding: 14px 18px !important;
    }}
    .streamlit-expanderContent {{
        border-top: 1px solid var(--rule) !important;
        padding: 14px 18px !important;
    }}

    /* ════════════════════════════════════════════════
       FORM ELEMENTS
    ════════════════════════════════════════════════ */
    .stSelectbox > div > div {{
        background: var(--card) !important;
        border: 1px solid var(--rule) !important;
        border-radius: var(--radius) !important;
        color: var(--text) !important;
        transition: var(--transition);
    }}
    .stSelectbox > div > div:hover {{
        border-color: var(--border-hover) !important;
    }}
    [data-baseweb="select"]:focus-within {{
        box-shadow: 0 0 0 1px rgba(53,114,176,0.3) !important;
        border-color: var(--accent) !important;
    }}
    .stSlider > div > div > div > div {{
        background: var(--accent) !important;
    }}
    .stProgress > div > div > div {{
        background: linear-gradient(90deg, var(--accent), var(--macro)) !important;
        border-radius: 3px;
    }}

    /* ════════════════════════════════════════════════
       ALERTS
    ════════════════════════════════════════════════ */
    .stInfo    {{ background: rgba(53,114,176,0.06);  border-color: {C_ACCENT}; border-radius: var(--radius); border-left-width: 2px; }}
    .stSuccess {{ background: rgba(46,158,110,0.06);  border-color: {C_HIGH};   border-radius: var(--radius); border-left-width: 2px; }}
    .stWarning {{ background: rgba(201,150,43,0.06);  border-color: {C_MOD};    border-radius: var(--radius); border-left-width: 2px; }}
    .stError   {{ background: rgba(192,57,43,0.06);   border-color: {C_LOW};    border-radius: var(--radius); border-left-width: 2px; }}

    /* ════════════════════════════════════════════════
       DIVIDERS — WSJ thin rules
    ════════════════════════════════════════════════ */
    hr {{
        border: none !important;
        height: 1px !important;
        background: var(--rule) !important;
        margin: 1.75rem 0 !important;
    }}

    /* ════════════════════════════════════════════════
       KEYFRAME ANIMATIONS — restrained
    ════════════════════════════════════════════════ */
    @keyframes pulse-dot {{
        0%, 100% {{ opacity: 1;   }}
        50%       {{ opacity: 0.4; }}
    }}
    @keyframes slide-in-up {{
        from {{ opacity: 0; transform: translateY(12px); }}
        to   {{ opacity: 1; transform: translateY(0);    }}
    }}
    @keyframes fade-in {{
        from {{ opacity: 0; }}
        to   {{ opacity: 1; }}
    }}
    @keyframes page-enter {{
        from {{ opacity: 0; transform: translateY(6px); }}
        to   {{ opacity: 1; transform: translateY(0);   }}
    }}
    @keyframes ticker-scroll {{
        0%   {{ transform: translateX(0);    }}
        100% {{ transform: translateX(-50%); }}
    }}

    /* ════════════════════════════════════════════════
       WSJ EDITORIAL COMPONENTS
    ════════════════════════════════════════════════ */

    /* ── Masthead ── */
    .wsj-masthead {{
        text-align: center;
        padding: 20px 0 16px;
        border-bottom: 3px double var(--rule);
        margin-bottom: 20px;
    }}
    .wsj-masthead-title {{
        font-family: var(--serif);
        font-size: 2.2rem;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.02em;
        line-height: 1.1;
    }}
    .wsj-masthead-subtitle {{
        font-family: var(--sans);
        font-size: 0.72rem;
        font-weight: 600;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.15em;
        margin-top: 6px;
    }}
    .wsj-masthead-date {{
        font-family: var(--sans);
        font-size: 0.7rem;
        color: var(--text3);
        margin-top: 8px;
        letter-spacing: 0.04em;
    }}

    /* ── Section Header (WSJ style) ── */
    .wsj-section-header {{
        border-top: 2px solid var(--text);
        padding-top: 10px;
        margin-bottom: 16px;
        margin-top: 24px;
    }}
    .wsj-section-title {{
        font-family: var(--serif);
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.01em;
    }}
    .wsj-section-subtitle {{
        font-family: var(--sans);
        font-size: 0.75rem;
        color: var(--text3);
        margin-top: 3px;
    }}

    /* ── Article Headline ── */
    .wsj-headline {{
        font-family: var(--serif);
        font-size: 1.45rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.25;
        letter-spacing: -0.02em;
        margin-bottom: 6px;
    }}
    .wsj-headline-sm {{
        font-family: var(--serif);
        font-size: 1.05rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.3;
        letter-spacing: -0.01em;
        margin-bottom: 4px;
    }}
    .wsj-subhead {{
        font-family: var(--sans);
        font-size: 0.88rem;
        color: var(--text2);
        line-height: 1.5;
        margin-bottom: 10px;
    }}
    .wsj-byline {{
        font-family: var(--sans);
        font-size: 0.72rem;
        font-weight: 600;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }}
    .wsj-body {{
        font-family: var(--sans);
        font-size: 0.88rem;
        color: var(--text2);
        line-height: 1.65;
    }}

    /* ── Thin Rule Variants ── */
    .wsj-rule {{
        height: 1px;
        background: var(--rule);
        margin: 16px 0;
    }}
    .wsj-rule-heavy {{
        height: 2px;
        background: var(--text);
        margin: 16px 0;
    }}
    .wsj-rule-double {{
        height: 0;
        border-top: 3px double var(--rule);
        margin: 16px 0;
    }}
    .wsj-rule-dotted {{
        height: 0;
        border-top: 1px dotted rgba(232,230,225,0.2);
        margin: 12px 0;
    }}

    /* ── Data Card (WSJ style) ── */
    .wsj-card {{
        background: var(--card);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        padding: 18px 22px;
        margin-bottom: 12px;
        transition: var(--transition);
    }}
    .wsj-card:hover {{
        border-color: var(--border-hover);
    }}
    .wsj-card-accent {{
        border-top: 2px solid var(--accent);
    }}

    /* ── KPI Cards — WSJ refined ── */
    .kpi-card {{
        background: var(--card);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        padding: 18px 20px;
        text-align: center;
        height: 100%;
        transition: var(--transition);
        position: relative;
    }}
    .kpi-card:hover {{
        border-color: var(--border-hover);
    }}
    .kpi-value {{
        font-size: 1.8rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.1;
        margin: 6px 0;
        font-family: var(--mono);
        letter-spacing: -0.02em;
    }}
    .kpi-label {{
        font-size: 0.68rem;
        font-weight: 600;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-family: var(--sans);
    }}
    .kpi-delta {{
        font-size: 0.78rem;
        margin-top: 4px;
        font-family: var(--mono);
        font-weight: 500;
    }}

    /* ── Section Banner ── */
    .section-banner {{
        margin-bottom: 20px;
        padding-bottom: 12px;
        border-bottom: 1px solid var(--rule);
    }}
    .section-banner-title {{
        font-family: var(--serif);
        font-size: 1.25rem;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.01em;
    }}
    .section-banner-subtitle {{
        font-family: var(--sans);
        font-size: 0.82rem;
        color: var(--text3);
        margin-top: 3px;
    }}
    .section-banner-underline {{
        display: none; /* replaced by border-bottom above */
    }}

    /* ── Sub-section header ── */
    .sub-section-header {{
        font-family: var(--sans);
        font-size: 0.72rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.1em;
        padding: 8px 0 6px;
        border-bottom: 1px solid var(--rule);
        margin-bottom: 12px;
    }}
    .sub-section-header::before {{
        content: none;
    }}

    /* ── Section Label (utility) ── */
    .section-label {{
        font-size: 0.68rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-bottom: 8px;
        font-family: var(--sans);
    }}

    /* ════════════════════════════════════════════════
       BADGES — muted, professional
    ════════════════════════════════════════════════ */
    .badge {{
        display: inline-flex;
        align-items: center;
        padding: 3px 10px;
        border-radius: 3px;
        font-size: 0.68rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        transition: var(--transition);
        font-family: var(--sans);
    }}

    /* ── Status Chip ── */
    .status-chip {{
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 4px 10px;
        border-radius: 3px;
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-family: var(--sans);
    }}
    .status-chip-dot {{
        width: 5px;
        height: 5px;
        border-radius: 50%;
        flex-shrink: 0;
    }}

    /* ════════════════════════════════════════════════
       INSIGHT CARDS
    ════════════════════════════════════════════════ */
    .insight-card {{
        background: var(--card);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        padding: 16px 20px;
        margin-bottom: 10px;
        transition: var(--transition);
        animation: slide-in-up 0.35s ease both;
    }}
    .insight-card:hover {{
        border-color: var(--border-hover);
    }}
    .insight-card:nth-child(1) {{ animation-delay: 0.00s; }}
    .insight-card:nth-child(2) {{ animation-delay: 0.04s; }}
    .insight-card:nth-child(3) {{ animation-delay: 0.08s; }}
    .insight-card:nth-child(4) {{ animation-delay: 0.12s; }}
    .insight-card:nth-child(5) {{ animation-delay: 0.16s; }}
    .insight-card:nth-child(6) {{ animation-delay: 0.20s; }}

    /* ════════════════════════════════════════════════
       GRID LAYOUTS
    ════════════════════════════════════════════════ */
    .metric-grid {{
        display: grid;
        gap: 14px;
        margin-bottom: 14px;
    }}
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
        .kpi-value {{ font-size: 1.4rem; }}
    }}

    /* ════════════════════════════════════════════════
       DATA TABLE — WSJ editorial style
    ════════════════════════════════════════════════ */
    .data-table {{
        width: 100%;
        border-collapse: collapse;
        border-spacing: 0;
        font-size: 0.84rem;
        font-family: var(--sans);
    }}
    .data-table thead tr {{
        border-bottom: 2px solid var(--text);
    }}
    .data-table thead th {{
        padding: 8px 14px;
        text-align: left;
        font-size: 0.68rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        white-space: nowrap;
        font-family: var(--sans);
    }}
    .data-table tbody tr {{
        border-bottom: 1px solid var(--rule);
        transition: var(--transition);
    }}
    .data-table tbody tr:hover {{
        background: rgba(53,114,176,0.04);
    }}
    .data-table tbody td {{
        padding: 10px 14px;
        color: var(--text2);
        vertical-align: middle;
    }}
    .data-table .mono {{
        font-family: var(--mono);
        font-size: 0.82rem;
        color: var(--text);
    }}

    /* ════════════════════════════════════════════════
       WSJ MARKET DATA TABLE
    ════════════════════════════════════════════════ */
    .wsj-market-table {{
        width: 100%;
        border-collapse: collapse;
        font-family: var(--sans);
    }}
    .wsj-market-table thead th {{
        font-size: 0.66rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        padding: 6px 12px;
        border-bottom: 2px solid var(--text);
        text-align: right;
    }}
    .wsj-market-table thead th:first-child {{
        text-align: left;
    }}
    .wsj-market-table tbody td {{
        padding: 8px 12px;
        font-size: 0.84rem;
        color: var(--text2);
        border-bottom: 1px solid var(--rule);
        text-align: right;
        font-variant-numeric: tabular-nums;
    }}
    .wsj-market-table tbody td:first-child {{
        text-align: left;
        color: var(--text);
        font-weight: 600;
    }}
    .wsj-market-table tbody td.mono {{
        font-family: var(--mono);
        font-size: 0.82rem;
    }}
    .wsj-market-table tbody tr:hover {{
        background: rgba(53,114,176,0.03);
    }}

    /* ── WSJ What's News sidebar ── */
    .wsj-whats-news {{
        border-top: 2px solid var(--text);
        padding-top: 10px;
    }}
    .wsj-whats-news-title {{
        font-family: var(--serif);
        font-size: 0.92rem;
        font-weight: 700;
        color: var(--text);
        margin-bottom: 10px;
    }}
    .wsj-news-item {{
        padding: 8px 0;
        border-bottom: 1px dotted rgba(232,230,225,0.15);
    }}
    .wsj-news-item:last-child {{
        border-bottom: none;
    }}
    .wsj-news-bullet {{
        display: inline-block;
        width: 4px;
        height: 4px;
        background: var(--accent);
        border-radius: 50%;
        margin-right: 8px;
        vertical-align: middle;
    }}
    .wsj-news-text {{
        font-family: var(--sans);
        font-size: 0.82rem;
        color: var(--text2);
        line-height: 1.5;
    }}

    /* ── WSJ Pull Quote ── */
    .wsj-pullquote {{
        border-top: 2px solid var(--text);
        border-bottom: 1px solid var(--rule);
        padding: 16px 0;
        margin: 20px 0;
    }}
    .wsj-pullquote-text {{
        font-family: var(--serif);
        font-size: 1.15rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.4;
        font-style: italic;
    }}
    .wsj-pullquote-attr {{
        font-family: var(--sans);
        font-size: 0.72rem;
        color: var(--text3);
        margin-top: 6px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }}

    /* ── WSJ Stat Box ── */
    .wsj-stat-box {{
        border: 1px solid var(--rule);
        border-top: 2px solid var(--accent);
        border-radius: 0 0 var(--radius) var(--radius);
        padding: 16px 18px;
        background: var(--card);
    }}
    .wsj-stat-number {{
        font-family: var(--mono);
        font-size: 2rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1;
    }}
    .wsj-stat-label {{
        font-family: var(--sans);
        font-size: 0.72rem;
        font-weight: 600;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-top: 4px;
    }}
    .wsj-stat-context {{
        font-family: var(--sans);
        font-size: 0.8rem;
        color: var(--text2);
        margin-top: 6px;
        line-height: 1.4;
    }}

    /* ── WSJ Ticker Bar ── */
    .wsj-ticker {{
        overflow: hidden;
        background: var(--surface);
        border-top: 1px solid var(--rule);
        border-bottom: 1px solid var(--rule);
        padding: 7px 0;
        width: 100%;
        position: relative;
        margin-bottom: 20px;
    }}
    .wsj-ticker::before,
    .wsj-ticker::after {{
        content: '';
        position: absolute;
        top: 0; bottom: 0;
        width: 50px;
        z-index: 1;
        pointer-events: none;
    }}
    .wsj-ticker::before {{
        left: 0;
        background: linear-gradient(90deg, var(--bg), transparent);
    }}
    .wsj-ticker::after {{
        right: 0;
        background: linear-gradient(90deg, transparent, var(--bg));
    }}

    /* ════════════════════════════════════════════════
       ROUTE CARD
    ════════════════════════════════════════════════ */
    .route-card {{
        background: var(--card);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        padding: 16px 20px;
        transition: var(--transition);
    }}
    .route-card:hover {{
        border-color: var(--border-hover);
    }}
    .route-label {{
        font-size: 0.66rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--text3);
        font-family: var(--sans);
    }}
    .route-name {{
        font-family: var(--serif);
        font-size: 1rem;
        font-weight: 700;
        color: var(--text);
        margin: 4px 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .route-arrow {{
        color: var(--text3);
        font-size: 0.85rem;
    }}

    /* ── Port Card ── */
    .port-card {{
        background: var(--card);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        padding: 16px 20px;
        transition: var(--transition);
    }}
    .port-card:hover {{
        border-color: var(--border-hover);
    }}
    .port-name {{
        font-family: var(--serif);
        font-size: 0.95rem;
        font-weight: 700;
        color: var(--text);
        margin-bottom: 4px;
    }}
    .port-detail {{
        font-size: 0.8rem;
        color: var(--text2);
    }}

    /* ════════════════════════════════════════════════
       PROGRESS BAR
    ════════════════════════════════════════════════ */
    .progress-bar-custom {{
        width: 100%;
        height: 4px;
        background: rgba(232,230,225,0.05);
        border-radius: 2px;
        overflow: hidden;
    }}
    .progress-bar-fill {{
        height: 100%;
        border-radius: 2px;
        transition: width 0.6s ease;
    }}

    /* ════════════════════════════════════════════════
       ALERT CRITICAL
    ════════════════════════════════════════════════ */
    .alert-critical {{
        background: rgba(192,57,43,0.06);
        border: 1px solid rgba(192,57,43,0.2);
        border-left: 3px solid {C_LOW};
        border-radius: var(--radius);
        padding: 14px 18px;
        display: flex;
        align-items: flex-start;
        gap: 10px;
    }}
    .alert-critical-icon {{
        font-size: 1rem;
        flex-shrink: 0;
    }}
    .alert-critical-title {{
        font-family: var(--serif);
        font-size: 0.88rem;
        font-weight: 700;
        color: #e08080;
        margin-bottom: 3px;
    }}
    .alert-critical-body {{
        font-size: 0.82rem;
        color: var(--text2);
        line-height: 1.5;
    }}

    /* ════════════════════════════════════════════════
       UTILITY CLASSES
    ════════════════════════════════════════════════ */
    .pulse-dot {{
        display: inline-block;
        width: 6px; height: 6px;
        border-radius: 50%;
        background: {C_HIGH};
        animation: pulse-dot 2s ease-in-out infinite;
        flex-shrink: 0;
    }}
    .slide-in      {{ animation: slide-in-up 0.35s ease both; }}
    .fade-in       {{ animation: fade-in 0.4s ease both; }}
    .page-enter    {{ animation: page-enter 0.3s ease both; }}

    /* ════════════════════════════════════════════════
       INPUT WIDGETS
    ════════════════════════════════════════════════ */
    [data-baseweb="input"] {{
        background: var(--card) !important;
        border-color: var(--rule) !important;
        border-radius: var(--radius) !important;
    }}
    [data-baseweb="input"]:focus-within {{
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 1px rgba(53,114,176,0.25) !important;
    }}

    /* ════════════════════════════════════════════════
       SHIMMER LOADING
    ════════════════════════════════════════════════ */
    @keyframes shimmer {{
        0%   {{ background-position: -300% 0; }}
        100% {{ background-position:  300% 0; }}
    }}
    .shimmer {{
        background: linear-gradient(
            105deg,
            var(--card) 0%,
            var(--card) 30%,
            rgba(46,64,97,0.4) 50%,
            var(--card) 70%,
            var(--card) 100%
        ), var(--card);
        background-size: 300% 100%;
        animation: shimmer 2s infinite linear;
        border-radius: var(--radius);
    }}

    </style>
    """, )


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
    """Return HTML for a styled KPI card."""
    delta_html = (
        f'<div class="kpi-delta" style="color:{delta_color}">{delta}</div>'
        if delta else ""
    )
    return f"""
    <div class="kpi-card" style="border-top:2px solid {accent_color};">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        {delta_html}
    </div>
    """


def badge(text: str, color: str = C_ACCENT) -> str:
    """Return HTML for a colored status badge."""
    bg    = _hex_to_rgba(color, 0.1)
    bord  = _hex_to_rgba(color, 0.25)
    return (
        f'<span class="badge" '
        f'style="background:{bg}; color:{color}; border:1px solid {bord};">'
        f'{text}</span>'
    )


def section_header(title: str, subtitle: str = "") -> None:
    """Render a WSJ-style section header with top rule."""
    sub_html = (
        f'<div class="wsj-section-subtitle">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(f"""
    <div class="wsj-section-header">
        <div class="wsj-section-title">{title}</div>
        {sub_html}
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
                ), unsafe_allow_html=True)


def ticker_tape_html(items: list[dict]) -> str:
    """Generate a WSJ-style scrolling ticker tape HTML string."""
    def _item_html(item: dict) -> str:
        change = item.get("change", 0.0)
        arrow  = "+" if change >= 0 else ""
        color  = C_HIGH if change >= 0 else C_LOW
        unit   = item.get("unit", "")
        return (
            f'<span style="display:inline-flex;align-items:center;gap:6px;'
            f'padding:0 20px;white-space:nowrap;font-size:0.8rem;font-family:var(--sans);">'
            f'<span style="color:{C_TEXT3};font-weight:600;font-size:0.72rem;'
            f'text-transform:uppercase;letter-spacing:0.04em;">{item.get("label","")}</span>'
            f'<span style="color:{C_TEXT};font-weight:600;font-family:var(--mono);font-size:0.82rem;">'
            f'{item.get("value","")}{unit}</span>'
            f'<span style="color:{color};font-size:0.75rem;font-family:var(--mono);">'
            f'{arrow}{change:.2f}%</span>'
            f'<span style="color:{C_TEXT3};padding:0 4px;opacity:0.3;">|</span>'
            f'</span>'
        )

    items_html     = "".join(_item_html(i) for i in items)
    ticker_content = items_html * 2
    duration       = max(14, len(items) * 3)
    return (
        f'<div class="wsj-ticker">'
        f'<div style="display:inline-flex;animation:ticker-scroll {duration}s linear infinite;">'
        f'{ticker_content}'
        f'</div></div>'
    )


def live_badge(text: str = "LIVE") -> str:
    """Return HTML for a subtle LIVE badge."""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:5px;'
        f'background:rgba(46,158,110,0.08);color:{C_HIGH};'
        f'border:1px solid rgba(46,158,110,0.2);'
        f'padding:3px 10px;border-radius:3px;font-size:0.7rem;font-weight:700;'
        f'letter-spacing:0.06em;font-family:var(--sans);">'
        f'<span class="pulse-dot"></span>{text}</span>'
    )


def gradient_card(content_html: str, border_color: str = C_ACCENT, glow: bool = False) -> str:
    """Wrap content in a WSJ-style card."""
    rgba_border = _hex_to_rgba(border_color, 0.2)
    return (
        f'<div style="background:var(--card);'
        f'border:1px solid {rgba_border};border-radius:var(--radius);padding:20px;'
        f'border-top:2px solid {border_color};">'
        f'{content_html}</div>'
    )


def nav_section_button(icon: str, label: str, active: bool = False, key: str = "") -> bool:
    """Render a styled navigation section button."""
    active_class = "active" if active else ""
    btn_html = f"""
    <div class="section-nav-item {active_class}" id="nav-{key or label.lower().replace(' ', '-')}">
        <span class="nav-icon">{icon}</span>
        <span class="nav-label">{label}</span>
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
    """Render a WSJ-style page section header."""
    badge_html = ""
    if badge_text:
        bc = badge_color or C_ACCENT
        bg = _hex_to_rgba(bc, 0.1)
        bord = _hex_to_rgba(bc, 0.2)
        badge_html = (
            f'<span style="display:inline-flex;align-items:center;padding:2px 10px;'
            f'background:{bg};color:{bc};border:1px solid {bord};'
            f'border-radius:3px;font-size:0.68rem;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.05em;font-family:var(--sans);'
            f'margin-left:10px;">{badge_text}</span>'
        )
    sub_html = (
        f'<div style="color:var(--text3);font-size:0.84rem;margin-top:4px;'
        f'font-family:var(--sans);">{subtitle}</div>'
        if subtitle else ""
    )
    icon_html = f'<span style="margin-right:8px;">{icon}</span>' if icon else ""
    st.markdown(f"""
    <div class="page-enter" style="margin-bottom:24px;">
        <div style="border-top:2px solid var(--text);padding-top:12px;">
            <div style="display:flex;align-items:baseline;flex-wrap:wrap;">
                {icon_html}
                <span class="wsj-headline" style="margin-bottom:0;">{title}</span>
                {badge_html}
            </div>
            {sub_html}
        </div>
    </div>
    """, unsafe_allow_html=True)


def metric_card_row(metrics: list[dict], columns: int = 4) -> None:
    """Render a responsive row of metric cards."""
    n    = min(columns, len(metrics))
    cols = st.columns(n)
    for i, (col, m) in enumerate(zip(cols, metrics)):
        with col:
            accent  = m.get("accent", C_ACCENT)
            delta   = m.get("delta", "")
            d_color = m.get("delta_color", C_TEXT2)
            sub     = m.get("sublabel", "")
            delta_html = (
                f'<div style="font-size:0.78rem;color:{d_color};'
                f'font-family:var(--mono);font-weight:500;margin-top:4px;">'
                f'{delta}</div>'
                if delta else ""
            )
            sub_html = (
                f'<div style="font-size:0.72rem;color:var(--text3);margin-top:2px;">{sub}</div>'
                if sub else ""
            )
            st.markdown(f"""
            <div class="kpi-card slide-in" style="border-top:2px solid {accent};">
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
    """Generate HTML for a styled insight card."""
    action_color  = ACTION_COLORS.get(action, C_TEXT2)
    cat_color     = CATEGORY_COLORS.get(category.upper(), C_TEXT3)
    score_pct     = int(score * 100)
    score_color   = C_HIGH if score >= 0.7 else (C_MOD if score >= 0.4 else C_LOW)
    bar_fill      = _hex_to_rgba(score_color, 0.7)
    action_bg     = _hex_to_rgba(action_color, 0.08)
    action_bord   = _hex_to_rgba(action_color, 0.2)
    cat_html      = (
        f'<span style="font-size:0.66rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.07em;color:{cat_color};margin-right:6px;'
        f'font-family:var(--sans);">{category}</span>'
        if category else ""
    )
    rationale_html = (
        f'<p style="font-size:0.8rem;color:var(--text3);margin:8px 0 0;'
        f'line-height:1.55;font-family:var(--sans);">{rationale}</p>'
        if rationale else ""
    )
    return f"""
    <div class="insight-card">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px;">
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
                    {cat_html}
                </div>
                <div style="font-family:var(--serif);font-size:0.92rem;font-weight:700;color:var(--text);
                    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{title}</div>
                {rationale_html}
            </div>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex-shrink:0;">
                <span style="background:{action_bg};color:{action_color};border:1px solid {action_bord};
                    padding:2px 9px;border-radius:3px;font-size:0.68rem;font-weight:700;
                    text-transform:uppercase;letter-spacing:0.04em;white-space:nowrap;
                    font-family:var(--sans);">{action}</span>
                <span style="font-family:var(--mono);font-size:0.82rem;
                    font-weight:700;color:{score_color};">{score_pct}%</span>
            </div>
        </div>
        <div class="progress-bar-custom">
            <div class="progress-bar-fill" style="width:{score_pct}%;background:{bar_fill};"></div>
        </div>
    </div>
    """


def status_badge(text: str, status: str = "info") -> str:
    """Generate HTML for a status badge."""
    palette = {
        "info":    (C_ACCENT,  0.08, 0.2),
        "success": (C_HIGH,    0.08, 0.2),
        "warning": (C_MOD,     0.08, 0.2),
        "danger":  (C_LOW,     0.08, 0.2),
        "neutral": (C_TEXT3,   0.06, 0.12),
    }
    color, bg_a, bord_a = palette.get(status, palette["info"])
    bg   = _hex_to_rgba(color, bg_a)
    bord = _hex_to_rgba(color, bord_a)
    dot_color = color
    pulse = ' animation:pulse-dot 2s ease-in-out infinite;' if status == "danger" else ""
    return (
        f'<span class="status-chip" style="background:{bg};color:{color};border:1px solid {bord};">'
        f'<span class="status-chip-dot" style="background:{dot_color};{pulse}"></span>'
        f'{text}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────────
#  WSJ EDITORIAL HTML BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def wsj_section(title: str, subtitle: str = "") -> None:
    """Render a WSJ section header with heavy top rule."""
    sub = f'<div class="wsj-section-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(f"""
    <div class="wsj-section-header">
        <div class="wsj-section-title">{title}</div>
        {sub}
    </div>
    """, unsafe_allow_html=True)


def wsj_headline(text: str, subhead: str = "", byline: str = "") -> None:
    """Render a WSJ article headline."""
    sub = f'<div class="wsj-subhead">{subhead}</div>' if subhead else ""
    by = f'<div class="wsj-byline">{byline}</div>' if byline else ""
    st.markdown(f"""
    <div style="margin-bottom:14px;">
        <div class="wsj-headline">{text}</div>
        {sub}
        {by}
    </div>
    """, unsafe_allow_html=True)


def wsj_pullquote(text: str, attribution: str = "") -> None:
    """Render a WSJ-style pull quote."""
    attr = f'<div class="wsj-pullquote-attr">-- {attribution}</div>' if attribution else ""
    st.markdown(f"""
    <div class="wsj-pullquote">
        <div class="wsj-pullquote-text">{text}</div>
        {attr}
    </div>
    """, unsafe_allow_html=True)


def wsj_stat_box(number: str, label: str, context: str = "", accent: str = C_ACCENT) -> str:
    """Return HTML for a WSJ stat box."""
    ctx = f'<div class="wsj-stat-context">{context}</div>' if context else ""
    return f"""
    <div class="wsj-stat-box" style="border-top-color:{accent};">
        <div class="wsj-stat-number">{number}</div>
        <div class="wsj-stat-label">{label}</div>
        {ctx}
    </div>
    """


def wsj_news_list(items: list[str]) -> None:
    """Render a WSJ What's News-style bullet list."""
    items_html = ""
    for item in items:
        items_html += f"""
        <div class="wsj-news-item">
            <span class="wsj-news-bullet"></span>
            <span class="wsj-news-text">{item}</span>
        </div>
        """
    st.markdown(f"""
    <div class="wsj-whats-news">
        <div class="wsj-whats-news-title">What's News</div>
        {items_html}
    </div>
    """, unsafe_allow_html=True)


def wsj_market_table(headers: list[str], rows: list[list[str]]) -> None:
    """Render a WSJ-style market data table."""
    thead = "".join(f"<th>{h}</th>" for h in headers)
    tbody = ""
    for row in rows:
        cells = "".join(f"<td>{c}</td>" for c in row)
        tbody += f"<tr>{cells}</tr>"
    st.markdown(f"""
    <table class="wsj-market-table">
        <thead><tr>{thead}</tr></thead>
        <tbody>{tbody}</tbody>
    </table>
    """, unsafe_allow_html=True)
