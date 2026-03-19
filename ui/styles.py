"""Global CSS design system for Ship Tracker."""
from __future__ import annotations
import streamlit as st


# ── Shared color constants (also used inline in Python) ──────────────────
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


def inject_global_css() -> None:
    """Inject the global CSS design system. Call once at app startup."""
    st.markdown(f"""
    <style>
    /* ── Google Font ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* ── Root variables ── */
    :root {{
        --bg:        {C_BG};
        --surface:   {C_SURFACE};
        --card:      {C_CARD};
        --border:    {C_BORDER};
        --high:      {C_HIGH};
        --mod:       {C_MOD};
        --low:       {C_LOW};
        --accent:    {C_ACCENT};
        --conv:      {C_CONV};
        --macro:     {C_MACRO};
        --text:      {C_TEXT};
        --text2:     {C_TEXT2};
        --text3:     {C_TEXT3};
        --radius:    8px;
        --radius-lg: 12px;
    }}

    /* ── Base ── */
    html, body, [class*="css"] {{
        font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
    }}
    .main .block-container {{
        padding-top: 1.5rem;
        padding-bottom: 2rem;
        max-width: 1400px;
    }}

    /* ── Hide default Streamlit chrome ── */
    #MainMenu, footer, header {{ visibility: hidden; }}
    .stDeployButton {{ display: none; }}

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        background: var(--surface);
        border-radius: var(--radius);
        padding: 4px;
        border: 1px solid var(--border);
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 6px;
        padding: 6px 16px;
        font-weight: 500;
        font-size: 0.88rem;
        color: var(--text2);
        background: transparent;
        border: none;
    }}
    .stTabs [aria-selected="true"] {{
        background: var(--accent) !important;
        color: white !important;
    }}

    /* ── Sidebar ── */
    section[data-testid="stSidebar"] {{
        background: var(--surface) !important;
        border-right: 1px solid var(--border);
    }}
    section[data-testid="stSidebar"] .stMarkdown p {{
        font-size: 0.82rem;
    }}

    /* ── Metric cards (override default) ── */
    [data-testid="stMetric"] {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 12px 16px !important;
    }}
    [data-testid="stMetricLabel"] {{
        font-size: 0.75rem !important;
        color: var(--text2) !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }}
    [data-testid="stMetricValue"] {{
        font-size: 1.6rem !important;
        font-weight: 700 !important;
        color: var(--text) !important;
    }}
    [data-testid="stMetricDelta"] {{
        font-size: 0.8rem !important;
    }}

    /* ── DataFrames ── */
    [data-testid="stDataFrame"] {{
        border: 1px solid var(--border);
        border-radius: var(--radius);
        overflow: hidden;
    }}

    /* ── Buttons ── */
    .stButton > button {{
        background: var(--accent);
        color: white;
        border: none;
        border-radius: var(--radius);
        font-weight: 500;
        font-size: 0.85rem;
        transition: all 0.15s ease;
    }}
    .stButton > button:hover {{
        background: #2563eb;
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(59,130,246,0.3);
    }}

    /* ── Expanders ── */
    .streamlit-expanderHeader {{
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        color: var(--text2) !important;
    }}

    /* ── Selectboxes / dropdowns ── */
    .stSelectbox > div > div {{
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        color: var(--text) !important;
    }}

    /* ── Sliders ── */
    .stSlider > div > div > div > div {{
        background: var(--accent) !important;
    }}

    /* ── Progress bars ── */
    .stProgress > div > div > div {{
        background: var(--accent) !important;
    }}

    /* ── Alerts ── */
    .stInfo    {{ background: rgba(59,130,246,0.1);  border-color: {C_ACCENT}; border-radius: var(--radius); }}
    .stSuccess {{ background: rgba(16,185,129,0.1);  border-color: {C_HIGH};   border-radius: var(--radius); }}
    .stWarning {{ background: rgba(245,158,11,0.1);  border-color: {C_MOD};    border-radius: var(--radius); }}
    .stError   {{ background: rgba(239,68,68,0.1);   border-color: {C_LOW};    border-radius: var(--radius); }}

    /* ── Custom card components ── */
    .ship-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 16px 20px;
        margin-bottom: 10px;
        transition: border-color 0.15s ease;
    }}
    .ship-card:hover {{
        border-color: rgba(255,255,255,0.18);
    }}
    .ship-card-accent {{
        border-left: 3px solid var(--accent);
    }}

    .kpi-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 18px 20px;
        text-align: center;
        height: 100%;
    }}
    .kpi-value {{
        font-size: 2rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.1;
        margin: 4px 0;
    }}
    .kpi-label {{
        font-size: 0.72rem;
        font-weight: 600;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }}
    .kpi-delta {{
        font-size: 0.82rem;
        margin-top: 4px;
    }}

    .badge {{
        display: inline-block;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}

    .insight-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 16px 20px;
        margin-bottom: 8px;
        transition: all 0.15s ease;
    }}
    .insight-card:hover {{
        border-color: rgba(255,255,255,0.15);
        transform: translateY(-1px);
    }}

    .section-label {{
        font-size: 0.72rem;
        font-weight: 700;
        color: var(--text3);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 8px;
    }}

    /* ── Divider ── */
    hr {{
        border-color: var(--border) !important;
        margin: 1.5rem 0 !important;
    }}

    /* ── Keyframe animations ── */
    @keyframes pulse-glow {{
        0%, 100% {{ box-shadow: 0 0 8px rgba(16,185,129,0.3); }}
        50% {{ box-shadow: 0 0 20px rgba(16,185,129,0.7), 0 0 40px rgba(16,185,129,0.3); }}
    }}
    @keyframes pulse-dot {{
        0%, 100% {{ opacity: 1; transform: scale(1); }}
        50% {{ opacity: 0.6; transform: scale(1.3); }}
    }}
    @keyframes slide-in-up {{
        from {{ opacity: 0; transform: translateY(16px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes fade-in {{
        from {{ opacity: 0; }}
        to {{ opacity: 1; }}
    }}
    @keyframes rotate-border {{
        0% {{ background-position: 0% 50%; }}
        50% {{ background-position: 100% 50%; }}
        100% {{ background-position: 0% 50%; }}
    }}
    @keyframes shimmer {{
        0% {{ background-position: -200% 0; }}
        100% {{ background-position: 200% 0; }}
    }}
    @keyframes ticker-scroll {{
        0% {{ transform: translateX(0); }}
        100% {{ transform: translateX(-50%); }}
    }}

    /* ── Utility classes ── */
    .pulse-green {{
        animation: pulse-glow 2s ease-in-out infinite;
    }}
    .pulse-dot {{
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        background: #10b981;
        animation: pulse-dot 1.5s ease-in-out infinite;
    }}
    .slide-in {{
        animation: slide-in-up 0.4s ease-out both;
    }}
    .fade-in {{
        animation: fade-in 0.6s ease-out both;
    }}
    .glass-card {{
        background: rgba(26, 34, 53, 0.7) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 12px !important;
    }}
    .glow-green {{
        box-shadow: 0 0 20px rgba(16,185,129,0.25), 0 0 60px rgba(16,185,129,0.1);
    }}
    .glow-blue {{
        box-shadow: 0 0 20px rgba(59,130,246,0.25), 0 0 60px rgba(59,130,246,0.1);
    }}
    .glow-red {{
        box-shadow: 0 0 20px rgba(239,68,68,0.25), 0 0 60px rgba(239,68,68,0.1);
    }}
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
    .shimmer-loading {{
        background: linear-gradient(90deg, #1a2235 25%, #243050 50%, #1a2235 75%);
        background-size: 200% 100%;
        animation: shimmer 1.5s infinite;
        border-radius: 6px;
    }}
    .insight-card {{
        animation: slide-in-up 0.35s ease-out both;
    }}
    .insight-card:nth-child(2) {{ animation-delay: 0.05s; }}
    .insight-card:nth-child(3) {{ animation-delay: 0.10s; }}
    .insight-card:nth-child(4) {{ animation-delay: 0.15s; }}
    .insight-card:nth-child(5) {{ animation-delay: 0.20s; }}
    .rotating-border {{
        background: linear-gradient(270deg, #3b82f6, #10b981, #8b5cf6, #3b82f6);
        background-size: 400% 400%;
        animation: rotate-border 4s ease infinite;
        padding: 2px;
        border-radius: 13px;
    }}

    /* ── Tab styling enhancement ── */
    .stTabs [data-baseweb="tab"] {{
        transition: all 0.2s ease !important;
    }}
    .stTabs [data-baseweb="tab"]:hover {{
        color: #f1f5f9 !important;
        transform: translateY(-1px);
    }}

    /* ── Metric value glow on high values ── */
    [data-testid="stMetricValue"] {{
        text-shadow: 0 0 20px rgba(16,185,129,0.3);
    }}

    /* ── Expander animation ── */
    [data-testid="stExpander"] {{
        transition: all 0.2s ease;
        border-radius: 10px !important;
    }}

    /* ── Button glow on hover ── */
    .stButton button:hover {{
        box-shadow: 0 0 20px rgba(59,130,246,0.4) !important;
        transform: translateY(-1px) !important;
        transition: all 0.2s ease !important;
    }}

    /* ── DataFrame styling ── */
    [data-testid="stDataFrame"] {{
        border-radius: 10px !important;
        overflow: hidden !important;
    }}

    /* ── Selectbox glow on focus ── */
    [data-baseweb="select"]:focus-within {{
        box-shadow: 0 0 0 2px rgba(59,130,246,0.4) !important;
    }}
    </style>
    """, unsafe_allow_html=True)


def kpi_card(label: str, value: str, delta: str = "", delta_color: str = C_TEXT2, accent_color: str = C_ACCENT) -> str:
    """Return HTML for a styled KPI card."""
    delta_html = f'<div class="kpi-delta" style="color:{delta_color}">{delta}</div>' if delta else ""
    return f"""
    <div class="kpi-card" style="border-top: 3px solid {accent_color}">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        {delta_html}
    </div>
    """


def badge(text: str, color: str = C_ACCENT) -> str:
    """Return HTML for a colored status badge."""
    from ui.styles import _hex_to_rgba
    bg = _hex_to_rgba(color, 0.15)
    return f'<span class="badge" style="background:{bg}; color:{color}; border: 1px solid {_hex_to_rgba(color, 0.3)}">{text}</span>'


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert hex color to rgba string."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def section_header(title: str, subtitle: str = "") -> None:
    """Render a styled section header."""
    sub_html = f'<div style="color:var(--text2); font-size:0.85rem; margin-top:2px">{subtitle}</div>' if subtitle else ""
    st.markdown(f"""
    <div style="margin-bottom:16px">
        <div style="font-size:1.1rem; font-weight:700; color:var(--text)">{title}</div>
        {sub_html}
    </div>
    """, unsafe_allow_html=True)


def render_kpi_row(metrics: list[dict]) -> None:
    """Render a row of KPI cards. Each dict: {label, value, delta?, delta_color?, accent?}"""
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            st.markdown(kpi_card(
                label=m["label"],
                value=m["value"],
                delta=m.get("delta", ""),
                delta_color=m.get("delta_color", C_TEXT2),
                accent_color=m.get("accent", C_ACCENT),
            ), unsafe_allow_html=True)


def ticker_tape_html(items: list[dict]) -> str:
    """Generate a scrolling ticker tape HTML string.

    items: list of {"label": str, "value": str, "change": float, "unit": str}
    """
    def _item_html(item: dict) -> str:
        change = item.get("change", 0.0)
        arrow = "▲" if change >= 0 else "▼"
        change_color = "#10b981" if change >= 0 else "#ef4444"
        unit = item.get("unit", "")
        return (
            f'<span style="display:inline-flex; align-items:center; gap:8px; '
            f'padding:0 24px; white-space:nowrap; font-size:0.82rem;">'
            f'<span style="color:#64748b; font-weight:600; text-transform:uppercase; '
            f'letter-spacing:0.05em;">{item.get("label", "")}</span>'
            f'<span style="color:#f1f5f9; font-weight:700;">'
            f'{item.get("value", "")}{unit}</span>'
            f'<span style="color:{change_color}; font-size:0.75rem;">'
            f'{arrow} {abs(change):.2f}</span>'
            f'<span style="color:#334155;">|</span>'
            f'</span>'
        )

    items_html = "".join(_item_html(i) for i in items)
    # Duplicate for seamless looping
    ticker_content = items_html * 2
    duration = max(10, len(items) * 4)
    return (
        f'<div style="overflow:hidden; background:rgba(17,24,39,0.8); '
        f'border:1px solid rgba(255,255,255,0.06); border-radius:8px; '
        f'padding:8px 0; width:100%;">'
        f'<div style="display:inline-flex; animation:ticker-scroll {duration}s linear infinite;">'
        f'{ticker_content}'
        f'</div></div>'
    )


def live_badge(text: str = "LIVE") -> str:
    """Return HTML for a pulsing LIVE badge."""
    return (
        f'<span style="display:inline-flex; align-items:center; gap:5px; '
        f'background:rgba(16,185,129,0.12); color:#10b981; '
        f'border:1px solid rgba(16,185,129,0.3); '
        f'padding:3px 10px; border-radius:999px; font-size:0.72rem; font-weight:700">'
        f'<span class="pulse-dot"></span>{text}</span>'
    )


def gradient_card(content_html: str, border_color: str = "#3b82f6", glow: bool = True) -> str:
    """Wrap content in a glassmorphism gradient card."""
    glow_style = "box-shadow: 0 0 30px rgba(59,130,246,0.15);" if glow else ""
    return (
        f'<div style="background:linear-gradient(135deg,rgba(26,34,53,0.9),rgba(15,29,53,0.9));'
        f'border:1px solid {border_color}33; border-radius:14px; padding:20px;'
        f'backdrop-filter:blur(10px); {glow_style}">'
        f'{content_html}</div>'
    )
