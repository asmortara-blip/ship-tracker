from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from routes.optimizer import RouteOpportunity
from utils.helpers import format_usd


# ── Color constants (mirrors ui/styles.py) ────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_HIGH    = "#10b981"
_C_MOD     = "#f59e0b"
_C_LOW     = "#ef4444"
_C_ACCENT  = "#3b82f6"
_C_CONV    = "#8b5cf6"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider(label: str) -> None:
    """Render a dramatic styled section divider with a centred label."""
    st.markdown(
        f'<div style="display:flex; align-items:center; gap:12px; margin:28px 0">'
        f'<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        f'<span style="font-size:0.65rem; color:#475569; text-transform:uppercase;'
        f' letter-spacing:0.12em">{label}</span>'
        f'<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _score_color(score: float) -> str:
    if score >= 0.65:
        return _C_HIGH
    if score >= 0.45:
        return _C_MOD
    return _C_LOW


# ── Section 1 – Transit Gantt ─────────────────────────────────────────────────

def _render_transit_gantt(route_results: list[RouteOpportunity]) -> None:
    """Horizontal Gantt-style chart showing all routes as transit timeline bars."""
    sorted_routes = sorted(route_results, key=lambda r: r.transit_days)

    bar_colors = [_score_color(r.opportunity_score) for r in sorted_routes]
    bar_texts  = [str(r.transit_days) + "d" for r in sorted_routes]
    route_names = [r.route_name for r in sorted_routes]
    transit_vals = [r.transit_days for r in sorted_routes]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        orientation="h",
        x=transit_vals,
        y=route_names,
        marker_color=bar_colors,
        text=bar_texts,
        textposition="inside",
        textfont=dict(color="#f1f5f9", size=12, family="monospace"),
        hovertemplate="<b>%{y}</b><br>Transit: %{x} days<extra></extra>",
    ))

    max_days = max(transit_vals) if transit_vals else 60

    fig.add_vline(
        x=14,
        line_dash="dot",
        line_color="rgba(148,163,184,0.4)",
        annotation_text="2 weeks",
        annotation_position="top",
        annotation_font=dict(color=_C_TEXT3, size=10),
    )
    fig.add_vline(
        x=28,
        line_dash="dot",
        line_color="rgba(148,163,184,0.4)",
        annotation_text="4 weeks",
        annotation_position="top",
        annotation_font=dict(color=_C_TEXT3, size=10),
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=420,
        margin=dict(t=24, b=24, l=180, r=24),
        xaxis=dict(
            title="Transit Days",
            range=[0, max(max_days + 5, 35)],
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=11),
        ),
        hoverlabel=dict(
            bgcolor=_C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section 2 – Freight Rate Heatmap Calendar ─────────────────────────────────

def _render_rate_calendar(freight_data: dict, route_id: str) -> None:
    """12-week calendar heatmap for freight rates (GitHub contribution style)."""
    df = freight_data.get(route_id)

    if df is None or df.empty or len(df) < 30:
        st.markdown(
            f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER}; border-radius:10px;'
            f' padding:28px; text-align:center; color:{_C_TEXT3}; font-size:0.88rem">'
            f'Insufficient rate history — need at least 30 days of data for calendar view.</div>',
            unsafe_allow_html=True,
        )
        return

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    # Keep last 84 days (12 weeks)
    cutoff = df["date"].max() - pd.Timedelta(days=83)
    df = df[df["date"] >= cutoff].copy()

    if df.empty or len(df) < 7:
        st.markdown(
            f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER}; border-radius:10px;'
            f' padding:28px; text-align:center; color:{_C_TEXT3}; font-size:0.88rem">'
            f'Insufficient recent rate data for calendar view.</div>',
            unsafe_allow_html=True,
        )
        return

    period_avg = df["rate_usd_per_feu"].mean()

    df["weekday"] = df["date"].dt.weekday          # 0=Mon … 6=Sun
    df["week_num"] = (df["date"] - df["date"].min()).dt.days // 7

    n_weeks = int(df["week_num"].max()) + 1
    grid = pd.DataFrame(index=range(7), columns=range(n_weeks), dtype=float)

    for _, row in df.iterrows():
        wd = int(row["weekday"])
        wk = int(row["week_num"])
        if wk < n_weeks:
            rel = (row["rate_usd_per_feu"] - period_avg) / (period_avg + 1e-9)
            grid.loc[wd, wk] = float(rel)

    z = grid.values.tolist()
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    week_labels = [f"W{i+1}" for i in range(n_weeks)]

    # Build hover text — relative change %
    hover = []
    for wd in range(7):
        row_hover = []
        for wk in range(n_weeks):
            val = grid.loc[wd, wk]
            if pd.isna(val):
                row_hover.append("No data")
            else:
                sign = "+" if val >= 0 else ""
                row_hover.append(f"{sign}{val*100:.1f}% vs period avg")
        hover.append(row_hover)

    fig = go.Figure(go.Heatmap(
        z=z,
        x=week_labels,
        y=day_labels,
        text=hover,
        hovertemplate="<b>%{y} %{x}</b><br>%{text}<extra></extra>",
        colorscale=[[0, _C_HIGH], [0.5, _C_CARD], [1.0, _C_LOW]],
        zmid=0,
        showscale=True,
        colorbar=dict(
            title=dict(text="vs avg", font=dict(color=_C_TEXT2, size=10)),
            tickformat=".0%",
            tickfont=dict(color=_C_TEXT2, size=9),
            len=0.8,
        ),
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=220,
        margin=dict(t=12, b=12, l=48, r=60),
        xaxis=dict(
            side="top",
            tickfont=dict(size=10, color=_C_TEXT3),
            gridcolor="rgba(0,0,0,0)",
        ),
        yaxis=dict(
            tickfont=dict(size=10, color=_C_TEXT3),
            autorange="reversed",
            gridcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor=_C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"Green = below average (cheap), Red = above average (expensive). "
        f"Period average: ${period_avg:,.0f}/FEU"
    )


# ── Section 3 – Opportunity Score Leaderboard ─────────────────────────────────

def _render_leaderboard(route_results: list[RouteOpportunity]) -> None:
    """Render a dramatic card-based leaderboard for all routes."""

    def _trend_badge(trend: str) -> str:
        if trend == "Rising":
            return '<span style="background:rgba(16,185,129,0.15); color:#10b981; ' \
                   'border:1px solid #10b981; border-radius:999px; ' \
                   'padding:2px 10px; font-size:0.72rem; font-weight:700">&#11044; Rising</span>'
        if trend == "Falling":
            return '<span style="background:rgba(239,68,68,0.15); color:#ef4444; ' \
                   'border:1px solid #ef4444; border-radius:999px; ' \
                   'padding:2px 10px; font-size:0.72rem; font-weight:700">&#11044; Falling</span>'
        return '<span style="background:rgba(245,158,11,0.15); color:#f59e0b; ' \
               'border:1px solid #f59e0b; border-radius:999px; ' \
               'padding:2px 10px; font-size:0.72rem; font-weight:700">&#11044; Stable</span>'

    def _mini_bar(label: str, val: float, color: str) -> str:
        pct = val * 100
        return (
            f'<div style="margin-bottom:5px">'
            f'<div style="display:flex; justify-content:space-between; margin-bottom:2px">'
            f'<span style="font-size:0.67rem; color:{_C_TEXT3}">{label}</span>'
            f'<span style="font-size:0.67rem; color:{color}; font-weight:600">{pct:.0f}%</span>'
            f'</div>'
            f'<div style="background:rgba(255,255,255,0.06); border-radius:3px; height:4px; overflow:hidden">'
            f'<div style="background:{color}; width:{pct:.0f}%; height:100%; border-radius:3px"></div>'
            f'</div>'
            f'</div>'
        )

    def _card_html(rank: int, r: RouteOpportunity) -> str:
        border_color = _score_color(r.opportunity_score)
        score_pct    = f"{r.opportunity_score * 100:.0f}%"
        rate_str     = f"${r.current_rate_usd_feu:,.0f}/FEU" if r.current_rate_usd_feu > 0 else "—"
        badge        = _trend_badge(r.rate_trend)

        sub_scores = (
            _mini_bar("Rate Momentum",   r.rate_momentum_component,          _C_ACCENT)
            + _mini_bar("Demand Imbalance",  r.demand_imbalance_component,   _C_HIGH)
            + _mini_bar("Congestion Clear",  r.congestion_clearance_component, _C_MOD)
            + _mini_bar("Macro Tailwind",    r.macro_tailwind_component,     _C_CONV)
        )

        return (
            f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
            f' border-left:4px solid {border_color}; border-radius:12px;'
            f' padding:18px 20px; margin-bottom:16px; position:relative">'

            # rank badge
            f'<div style="position:absolute; top:14px; left:18px;'
            f' font-size:2rem; font-weight:900; color:rgba(255,255,255,0.07);'
            f' line-height:1; user-select:none">#{rank}</div>'

            # header row
            f'<div style="display:flex; justify-content:space-between; align-items:flex-start;'
            f' margin-bottom:10px; padding-left:36px">'
            f'  <div>'
            f'    <div style="font-size:0.95rem; font-weight:700; color:{_C_TEXT}">{r.route_name}</div>'
            f'    <div style="font-size:0.8rem; color:{_C_TEXT3}; margin-top:2px">'
            f'      {r.origin_locode} &rarr; {r.dest_locode}'
            f'    </div>'
            f'  </div>'
            f'  <div style="text-align:right">'
            f'    <div style="font-size:2.2rem; font-weight:900; color:{border_color};'
            f' line-height:1">{score_pct}</div>'
            f'    <div style="font-size:0.68rem; color:{_C_TEXT3}; margin-top:2px">opportunity</div>'
            f'  </div>'
            f'</div>'

            # sub-score bars
            f'<div style="padding-left:36px; margin-bottom:10px">'
            f'{sub_scores}'
            f'</div>'

            # footer row
            f'<div style="display:flex; justify-content:space-between; align-items:center;'
            f' padding-left:36px">'
            f'  <span style="font-size:0.82rem; font-weight:600; color:{_C_TEXT2}">{rate_str}</span>'
            f'  {badge}'
            f'</div>'
            f'</div>'
        )

    # Render in 2-column grid
    n = len(route_results)
    rows = (n + 1) // 2
    for row_i in range(rows):
        col_left, col_right = st.columns(2)
        left_idx  = row_i * 2
        right_idx = row_i * 2 + 1

        with col_left:
            r = route_results[left_idx]
            st.markdown(_card_html(left_idx + 1, r), unsafe_allow_html=True)

        if right_idx < n:
            with col_right:
                r = route_results[right_idx]
                st.markdown(_card_html(right_idx + 1, r), unsafe_allow_html=True)


# ── Existing helpers (rate alerts + comparison) ───────────────────────────────

def _render_rate_alerts(route_results: list[RouteOpportunity]) -> None:
    """Render the Rate Alerts section with configurable thresholds."""
    st.markdown(
        f'<div style="font-size:0.72rem; font-weight:700; color:{_C_TEXT3}; '
        f'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:10px">'
        f'Rate Alerts</div>',
        unsafe_allow_html=True,
    )

    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        high_thresh = st.slider(
            "Alert if rate exceeds (USD/FEU)",
            min_value=500,
            max_value=8000,
            value=st.session_state.get("alert_high_thresh", 3000),
            step=100,
            key="alert_high_thresh",
        )
    with sc2:
        low_thresh = st.slider(
            "Alert if rate drops below (USD/FEU)",
            min_value=100,
            max_value=3000,
            value=st.session_state.get("alert_low_thresh", 800),
            step=100,
            key="alert_low_thresh",
        )
    with sc3:
        pct_thresh = st.slider(
            "Alert if 30d change exceeds (\u00b1%)",
            min_value=5,
            max_value=100,
            value=st.session_state.get("alert_pct_thresh", 20),
            step=5,
            key="alert_pct_thresh",
        )

    alerts: list[tuple[RouteOpportunity, list[str]]] = []
    for r in route_results:
        reasons: list[str] = []
        rate = r.current_rate_usd_feu
        pct_change = r.rate_pct_change_30d * 100

        if rate > 0 and rate > high_thresh:
            reasons.append(f"Rate ${rate:,.0f}/FEU exceeds high threshold ${high_thresh:,}")
        if rate > 0 and rate < low_thresh:
            reasons.append(f"Rate ${rate:,.0f}/FEU below low threshold ${low_thresh:,}")
        if rate > 0 and abs(pct_change) > pct_thresh:
            sign = "+" if pct_change >= 0 else ""
            reasons.append(f"30d change {sign}{pct_change:.1f}% exceeds {pct_thresh}% threshold")
        if reasons:
            alerts.append((r, reasons))

    if alerts:
        for route, reasons in alerts:
            has_high = any("exceeds high" in rsn for rsn in reasons)
            border_color = _C_LOW if has_high else _C_MOD
            bg_color = "rgba(239,68,68,0.08)" if has_high else "rgba(245,158,11,0.08)"
            reasons_html = "".join(
                f'<li style="margin:2px 0; color:{_C_TEXT2}">{rsn}</li>'
                for rsn in reasons
            )
            st.markdown(
                f'<div style="background:{bg_color}; border:1px solid {border_color}; '
                f'border-left:4px solid {border_color}; border-radius:8px; '
                f'padding:10px 14px; margin-bottom:8px">'
                f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:4px">'
                f'<span style="font-size:1rem">\u26a0\ufe0f</span>'
                f'<span style="font-weight:700; color:{_C_TEXT}; font-size:0.88rem">'
                f'{route.route_name}</span>'
                f'<span style="font-size:0.78rem; color:{_C_TEXT3}; margin-left:auto">'
                f'${route.current_rate_usd_feu:,.0f}/FEU</span>'
                f'</div>'
                f'<ul style="margin:0; padding-left:18px; font-size:0.78rem">'
                f'{reasons_html}</ul>'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            f'<div style="background:rgba(16,185,129,0.08); border:1px solid {_C_HIGH}; '
            f'border-radius:8px; padding:10px 14px; color:{_C_HIGH}; font-size:0.88rem; '
            f'font-weight:600">'
            f'\u2705 All rates within normal range</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-bottom:16px'></div>", unsafe_allow_html=True)


def _render_route_comparison(route_results: list[RouteOpportunity]) -> None:
    """Render the Route vs Route comparison view."""
    route_names = [r.route_name for r in route_results]
    default_selection = route_names[:2] if len(route_names) >= 2 else route_names[:1]

    selected_names = st.multiselect(
        "Select 2\u20134 routes to compare",
        options=route_names,
        default=default_selection,
        key="route_comparison_select",
    )

    if len(selected_names) < 2:
        st.info("Select at least 2 routes to compare.")
        return
    if len(selected_names) > 4:
        st.warning("Please select at most 4 routes. Only the first 4 will be shown.")
        selected_names = selected_names[:4]

    selected_routes = [r for r in route_results if r.route_name in selected_names]
    selected_routes.sort(key=lambda r: selected_names.index(r.route_name))

    metric_labels = ["Rate Momentum", "Demand Imbalance", "Congestion Clearance", "Macro Tailwind"]
    metric_fields = [
        "rate_momentum_component",
        "demand_imbalance_component",
        "congestion_clearance_component",
        "macro_tailwind_component",
    ]
    metric_colors = [_C_ACCENT, _C_HIGH, _C_MOD, _C_CONV]

    comp_fig = go.Figure()
    x_labels = [r.route_name for r in selected_routes]

    for label, field, color in zip(metric_labels, metric_fields, metric_colors):
        y_vals = [getattr(r, field) for r in selected_routes]
        comp_fig.add_trace(go.Bar(
            name=label,
            x=x_labels,
            y=y_vals,
            marker_color=color,
            text=[f"{v:.0%}" for v in y_vals],
            textposition="outside",
            textfont=dict(size=10, color=_C_TEXT2),
        ))

    comp_fig.update_layout(
        template="plotly_dark",
        barmode="group",
        height=380,
        yaxis_title="Component Score",
        yaxis=dict(
            range=[0, 1.15],
            tickformat=".0%",
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11),
        ),
        margin=dict(t=60, b=40),
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        hoverlabel=dict(
            bgcolor=_C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(comp_fig, use_container_width=True)

    st.markdown(
        f'<div style="font-size:0.72rem; font-weight:700; color:{_C_TEXT3}; '
        f'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">'
        f'Comparison Summary</div>',
        unsafe_allow_html=True,
    )

    table_rows = []
    for r in selected_routes:
        pct = r.rate_pct_change_30d * 100
        sign = "+" if pct >= 0 else ""
        table_rows.append({
            "Route": r.route_name,
            "Score": round(r.opportunity_score, 3),
            "Rate ($/FEU)": f"${r.current_rate_usd_feu:,.0f}" if r.current_rate_usd_feu > 0 else "\u2014",
            "30d Change": f"{sign}{pct:.1f}%" if r.current_rate_usd_feu > 0 else "\u2014",
            "Trend": r.rate_trend,
            "Transit (days)": r.transit_days,
        })

    def _color_route_score(val):
        try:
            v = float(str(val).replace("%", "")) / 100
        except (ValueError, TypeError):
            return ""
        if v >= 0.55:
            return "background-color: rgba(16,185,129,0.25); color:#10b981"
        if v >= 0.35:
            return "background-color: rgba(245,158,11,0.20); color:#f59e0b"
        return "background-color: rgba(239,68,68,0.18); color:#ef4444"

    st.dataframe(
        pd.DataFrame(table_rows).style.applymap(_color_route_score, subset=["Score"]),
        use_container_width=True,
        hide_index=True,
    )


# ── Main render ───────────────────────────────────────────────────────────────

def render(route_results: list[RouteOpportunity], freight_data: dict, forecasts: list | None = None) -> None:
    """Render the Routes tab."""
    st.header("Route Opportunity Analysis")

    if not route_results:
        st.info("No route data available. Check API credentials and click Refresh.")
        return

    # ── Local color aliases (kept for inline f-string use below) ─────────────
    C_BG      = _C_BG
    C_SURFACE = _C_SURFACE
    C_CARD    = _C_CARD
    C_BORDER  = _C_BORDER
    C_HIGH    = _C_HIGH
    C_MOD     = _C_MOD
    C_LOW     = _C_LOW
    C_ACCENT  = _C_ACCENT
    C_CONV    = _C_CONV
    C_TEXT    = _C_TEXT
    C_TEXT2   = _C_TEXT2
    C_TEXT3   = _C_TEXT3

    top_route    = route_results[0]
    strong_count = sum(1 for r in route_results if r.opportunity_score >= 0.60)
    avg_rate     = sum(r.current_rate_usd_feu for r in route_results if r.current_rate_usd_feu > 0)
    n_rates      = sum(1 for r in route_results if r.current_rate_usd_feu > 0)
    top_rate_pct = top_route.rate_pct_change_30d

    c1, c2, c3, c4 = st.columns(4)

    def kpi(label, value, sub="", color=C_ACCENT):
        sub_html = "" if not sub else f'<div style="font-size:0.78rem; color:{C_TEXT2}">{sub}</div>'
        return (
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-top:3px solid {color};'
            f' border-radius:10px; padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.68rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;'
            f' letter-spacing:0.07em">{label}</div>'
            f'<div style="font-size:1.7rem; font-weight:800; color:{C_TEXT}; line-height:1.1;'
            f' margin:5px 0">{value}</div>'
            f'{sub_html}'
            f'</div>'
        )

    top_name_short = top_route.route_name.split(" ")[0] + "..."
    top_score_pct  = f"{top_route.opportunity_score:.0%} opportunity"
    top_kpi_color  = C_HIGH if top_route.opportunity_score >= 0.65 else C_MOD

    c1.markdown(kpi("Top Route", top_name_short, top_score_pct, top_kpi_color), unsafe_allow_html=True)
    c2.markdown(kpi("Strong Opportunities", str(strong_count), f"of {len(route_results)} routes", C_HIGH), unsafe_allow_html=True)
    if n_rates > 0:
        c3.markdown(kpi("Avg Freight Rate", f"${avg_rate / n_rates:,.0f}", "USD per FEU", C_ACCENT), unsafe_allow_html=True)
    top_rate_color = C_HIGH if top_rate_pct > 0 else C_LOW
    c4.markdown(kpi("Top Route Rate", f"{top_rate_pct * 100:+.1f}%", "30-day change", top_rate_color), unsafe_allow_html=True)

    # ── Transit Gantt ─────────────────────────────────────────────────────────
    _divider("Route Transit Times")
    _render_transit_gantt(route_results)

    # ── Rate Alerts ───────────────────────────────────────────────────────────
    _divider("Rate Alerts")
    _render_rate_alerts(route_results)

    # ── Opportunity scatter ───────────────────────────────────────────────────
    _divider("Opportunity Landscape")
    st.subheader("Rate Change vs Demand Imbalance")

    scatter_colors = {
        "Strong":   "#2ecc71",
        "Moderate": "#f39c12",
        "Weak":     "#e74c3c",
    }

    fig = go.Figure()
    for r in route_results:
        fig.add_trace(go.Scatter(
            x=[r.rate_pct_change_30d * 100],
            y=[r.demand_imbalance],
            mode="markers+text",
            marker=dict(
                size=r.opportunity_score * 60 + 10,
                color=scatter_colors.get(r.opportunity_label, "#7f8c8d"),
                opacity=0.85,
                line=dict(color="white", width=1),
            ),
            text=[r.route_name.split(" ")[0] + "..."],
            textposition="top center",
            name=r.route_name,
            hovertemplate=(
                f"<b>{r.route_name}</b><br>"
                f"Rate change: {r.rate_pct_change_30d * 100:+.1f}%<br>"
                f"Demand imbalance: {r.demand_imbalance:+.2f}<br>"
                f"Opportunity score: {r.opportunity_score:.0%}<br>"
                f"<extra></extra>"
            ),
        ))

    fig.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.5)
    fig.add_vline(x=0, line_dash="dot", line_color="gray", opacity=0.5)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        height=380,
        xaxis=dict(
            title="Rate Change 30d (%)",
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        yaxis=dict(
            title="Demand Imbalance (dest - origin)",
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        showlegend=False,
        margin=dict(t=20),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Route detail ──────────────────────────────────────────────────────────
    _divider("Route Detail")
    st.subheader("Route Detail")
    col_a, col_b = st.columns([1, 2])

    with col_a:
        selected_name = st.selectbox(
            "Select route",
            [r.route_name for r in route_results],
            key="route_select",
        )

    selected = next((r for r in route_results if r.route_name == selected_name), None)

    if selected:
        with col_a:
            opp_color = {"Strong": C_HIGH, "Moderate": C_MOD, "Weak": C_LOW}.get(
                selected.opportunity_label, C_ACCENT
            )
            rate_html = (
                ""
                if not selected.current_rate_usd_feu
                else (
                    f'<div>'
                    f'<div style="font-size:0.66rem; color:{C_TEXT3}; text-transform:uppercase;'
                    f' letter-spacing:0.06em">Current Rate</div>'
                    f'<div style="font-size:0.85rem; color:{C_TEXT2}; margin-top:2px">'
                    f'${selected.current_rate_usd_feu:,.0f}/FEU</div>'
                    f'</div>'
                )
            )
            st.markdown(
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
                f' border-radius:10px; padding:16px 18px; margin-bottom:12px">'
                f'<div style="display:flex; justify-content:space-between; align-items:flex-start;'
                f' margin-bottom:12px">'
                f'<div style="font-size:1rem; font-weight:700; color:{C_TEXT}">{selected.route_name}</div>'
                f'<span style="background:rgba(255,255,255,0.06); color:{opp_color};'
                f' padding:3px 12px; border-radius:999px; font-size:0.75rem; font-weight:700">'
                f'{selected.opportunity_label}</span>'
                f'</div>'
                f'<div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">'
                f'<div><div style="font-size:0.66rem; color:{C_TEXT3}; text-transform:uppercase;'
                f' letter-spacing:0.06em">Route</div>'
                f'<div style="font-size:0.85rem; color:{C_TEXT2}; margin-top:2px">'
                f'{selected.origin_locode} \u2192 {selected.dest_locode}</div></div>'
                f'<div><div style="font-size:0.66rem; color:{C_TEXT3}; text-transform:uppercase;'
                f' letter-spacing:0.06em">Transit</div>'
                f'<div style="font-size:0.85rem; color:{C_TEXT2}; margin-top:2px">'
                f'{selected.transit_days} days</div></div>'
                f'<div><div style="font-size:0.66rem; color:{C_TEXT3}; text-transform:uppercase;'
                f' letter-spacing:0.06em">FBX Index</div>'
                f'<div style="font-size:0.85rem; color:{C_ACCENT}; margin-top:2px; font-weight:600">'
                f'{selected.fbx_index}</div></div>'
                f'{rate_html}'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            st.markdown(
                f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3};'
                f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">'
                f'Score Breakdown</div>',
                unsafe_allow_html=True,
            )
            for name, val, wt in [
                ("Rate Momentum",   selected.rate_momentum_component,          0.35),
                ("Demand Imbalance", selected.demand_imbalance_component,      0.30),
                ("Congestion Clear", selected.congestion_clearance_component,  0.20),
                ("Macro Tailwind",   selected.macro_tailwind_component,        0.15),
            ]:
                bar_color = C_HIGH if val > 0.6 else (C_LOW if val < 0.35 else C_MOD)
                st.markdown(
                    f'<div style="margin-bottom:8px">'
                    f'<div style="display:flex; justify-content:space-between; margin-bottom:3px">'
                    f'<span style="font-size:0.78rem; color:{C_TEXT2}">{name}'
                    f' <span style="color:{C_TEXT3}">({wt:.0%})</span></span>'
                    f'<span style="font-size:0.78rem; font-weight:600; color:{bar_color}">{val:.0%}</span>'
                    f'</div>'
                    f'<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:6px; overflow:hidden">'
                    f'<div style="background:{bar_color}; width:{val * 100:.0f}%; height:100%; border-radius:4px"></div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        with col_b:
            st.info(selected.rationale)

            # Freight rate time series
            df = freight_data.get(selected.route_id)
            if df is not None and not df.empty and len(df) > 1:
                st.markdown("**Freight Rate History**")
                rate_fig = go.Figure(go.Scatter(
                    x=df["date"],
                    y=df["rate_usd_per_feu"],
                    mode="lines",
                    line=dict(color="#4A90D9", width=2),
                    fill="tozeroy",
                    fillcolor="rgba(74,144,217,0.15)",
                    hovertemplate="%{x|%Y-%m-%d}: $%{y:,.0f}/FEU<extra></extra>",
                ))
                rate_fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor=C_BG,
                    plot_bgcolor=C_SURFACE,
                    height=250,
                    yaxis=dict(
                        title="Rate (USD/FEU)",
                        gridcolor="rgba(255,255,255,0.05)",
                        zerolinecolor="rgba(255,255,255,0.1)",
                    ),
                    xaxis=dict(
                        gridcolor="rgba(255,255,255,0.05)",
                        zerolinecolor="rgba(255,255,255,0.1)",
                    ),
                    margin=dict(t=10, b=10),
                    hoverlabel=dict(
                        bgcolor=C_CARD,
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12),
                    ),
                )
                st.plotly_chart(rate_fig, use_container_width=True)

            # Rate Calendar Heatmap
            _divider("Rate Calendar — 12-Week View")
            st.markdown("**Freight Rate Calendar**")
            _render_rate_calendar(freight_data, selected.route_id)

    # ── Rate Forecasts ────────────────────────────────────────────────────────
    if forecasts:
        _divider("Rate Forecasts")
        st.subheader("30/60/90-Day Rate Forecasts")
        st.caption("Linear trend extrapolation with seasonal adjustment. Low confidence forecasts shown for reference only.")

        for fc in forecasts[:6]:
            pct_30 = (fc.forecast_30d - fc.current_rate) / fc.current_rate * 100 if fc.current_rate > 0 else 0
            arrow = "\u2191" if pct_30 > 1 else ("\u2193" if pct_30 < -1 else "\u2192")

            with st.container(border=True):
                fc1, fc2, fc3, fc4 = st.columns([2, 1, 1, 1])
                fc1.markdown(f"**{fc.route_name}** {arrow}")
                fc2.metric("Current", f"${fc.current_rate:,.0f}")
                fc3.metric("30d forecast", f"${fc.forecast_30d:,.0f}", f"{pct_30:+.1f}%")
                fc4.metric("90d forecast", f"${fc.forecast_90d:,.0f}")

                with st.expander("Forecast detail"):
                    days  = [0, 30, 60, 90]
                    rates = [fc.current_rate, fc.forecast_30d, fc.forecast_60d, fc.forecast_90d]
                    upper = [fc.current_rate, fc.upper_30d, fc.upper_30d + (fc.forecast_60d - fc.forecast_30d), fc.upper_30d + (fc.forecast_90d - fc.forecast_30d)]
                    lower = [fc.current_rate, fc.lower_30d, fc.lower_30d + (fc.forecast_60d - fc.forecast_30d), fc.lower_30d + (fc.forecast_90d - fc.forecast_30d)]

                    ffig = go.Figure()
                    ffig.add_trace(go.Scatter(
                        x=days + days[::-1],
                        y=upper + lower[::-1],
                        fill="toself",
                        fillcolor="rgba(74,144,217,0.15)",
                        line=dict(color="rgba(255,255,255,0)"),
                        showlegend=False,
                        name="confidence range",
                    ))
                    ffig.add_trace(go.Scatter(
                        x=days,
                        y=rates,
                        mode="lines+markers",
                        line=dict(color="#4A90D9", width=2),
                        marker=dict(size=6),
                        name="Forecast",
                        hovertemplate="Day %{x}: $%{y:,.0f}/FEU<extra></extra>",
                    ))
                    ffig.update_layout(
                        template="plotly_dark",
                        paper_bgcolor=C_BG,
                        plot_bgcolor=C_SURFACE,
                        height=180,
                        xaxis=dict(
                            title="Days from today",
                            gridcolor="rgba(255,255,255,0.05)",
                            zerolinecolor="rgba(255,255,255,0.1)",
                        ),
                        yaxis=dict(
                            title="Rate (USD/FEU)",
                            gridcolor="rgba(255,255,255,0.05)",
                            zerolinecolor="rgba(255,255,255,0.1)",
                        ),
                        margin=dict(t=5, b=5),
                        showlegend=False,
                        hoverlabel=dict(
                            bgcolor=C_CARD,
                            bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=C_TEXT, size=12),
                        ),
                    )
                    st.plotly_chart(ffig, use_container_width=True, key=f"routes_forecast_{fc.route_name}")
                    st.caption(fc.methodology)
                    st.caption(
                        f"Confidence: **{fc.confidence}** \u00b7 "
                        f"R\u00b2={fc.r_squared:.2f} \u00b7 {fc.data_points} data points"
                    )

    # ── Opportunity Score Leaderboard ─────────────────────────────────────────
    _divider("Opportunity Leaderboard")
    st.subheader("All Routes \u2014 Ranked")
    _render_leaderboard(route_results)

    # ── Route Comparison ──────────────────────────────────────────────────────
    _divider("Route Comparison")
    with st.expander("Route Comparison", expanded=False):
        _render_route_comparison(route_results)
