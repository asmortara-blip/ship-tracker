"""Sustainability tab — carbon emissions and ESG analytics for shipping routes.

Wire-up instructions (do NOT add this block to app.py without review):
---------------------------------------------------------------------------
# In app.py, inside the st.tabs([...]) block, add a "Sustainability" tab:
#
#   from processing.carbon_calculator import calculate_all_routes
#   import ui.tab_sustainability as tab_sustainability
#
#   tab_labels = [..., "Sustainability"]  # append to existing list
#   ...
#   with tabs[-1]:                        # or whichever index
#       route_emissions = calculate_all_routes()
#       tab_sustainability.render(route_emissions)
---------------------------------------------------------------------------
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from processing.carbon_calculator import (
    RouteEmissions,
    calculate_all_routes,
    compare_to_alternatives,
)
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    _hex_to_rgba,
    apply_dark_layout,
    section_header,
)

# ── Grade colour mapping ──────────────────────────────────────────────────────
_GRADE_COLORS: dict[str, str] = {
    "A": C_HIGH,    # green
    "B": "#34d399",  # light green
    "C": C_MOD,     # amber
    "D": C_LOW,     # red
}


def _grade_badge_html(grade: str) -> str:
    color = _GRADE_COLORS.get(grade, C_TEXT2)
    bg = _hex_to_rgba(color, 0.18)
    border = _hex_to_rgba(color, 0.35)
    return (
        f'<span style="display:inline-block; padding:2px 10px; border-radius:999px;'
        f' font-size:0.75rem; font-weight:700; letter-spacing:0.06em;'
        f' background:{bg}; color:{color}; border:1px solid {border}">'
        f'{grade}</span>'
    )


def _poseidon_badge_html(compliant: bool) -> str:
    if compliant:
        return '<span title="Poseidon Principles 2050 compliant">&#x2705; Poseidon</span>'
    return '<span title="Above Poseidon Principles 2050 threshold" style="opacity:0.65;">&#x274C; Poseidon</span>'


def _bar_html(fraction: float) -> str:
    """Mini horizontal progress bar coloured green-to-red by emissions fraction."""
    pct = min(100, max(0, fraction * 100))
    if pct < 33:
        bar_color = C_HIGH
    elif pct < 66:
        bar_color = C_MOD
    else:
        bar_color = C_LOW
    return (
        f'<div style="background:rgba(255,255,255,0.07); border-radius:4px;'
        f' height:6px; width:100%; margin-top:6px;">'
        f'<div style="width:{pct:.1f}%; height:6px; border-radius:4px;'
        f' background:{bar_color};"></div></div>'
    )


def render(route_results: list[RouteEmissions] | None = None) -> None:
    """Render the full Sustainability tab.

    Parameters
    ----------
    route_results:
        Pre-computed list from calculate_all_routes(). If None, computed on the fly
        (useful during development).
    """
    if route_results is None:
        route_results = calculate_all_routes()

    # ── Hero section ─────────────────────────────────────────────────────────
    avg_co2_per_teu = sum(r.co2_per_teu_mt for r in route_results) / len(route_results)
    total_routes = len(route_results)
    poseidon_count = sum(1 for r in route_results if r.poseidon_compliant)
    grade_a_count = sum(1 for r in route_results if r.sustainability_grade == "A")

    st.markdown(
        f"""
        <div style="background:linear-gradient(135deg,rgba(16,185,129,0.12),rgba(26,34,53,0.95));
                    border:1px solid rgba(16,185,129,0.25); border-radius:14px;
                    padding:28px 32px; margin-bottom:24px;
                    box-shadow:0 0 40px rgba(16,185,129,0.08);">
          <div style="display:flex; align-items:center; gap:12px; margin-bottom:6px;">
            <span style="font-size:2rem;">&#x1F343;</span>
            <span style="font-size:0.72rem; font-weight:700; letter-spacing:0.1em;
                         color:#10b981; text-transform:uppercase;">ESG &amp; Carbon Analytics</span>
          </div>
          <div style="font-size:2.6rem; font-weight:800; color:#f1f5f9; line-height:1.1;
                      margin-bottom:4px;">
            {avg_co2_per_teu:.3f} MT CO2 / TEU
          </div>
          <div style="color:{C_TEXT2}; font-size:0.9rem;">
            Average carbon intensity across all {total_routes} tracked routes &nbsp;|&nbsp;
            <span style="color:#10b981; font-weight:600;">{poseidon_count}/{total_routes}</span>
            Poseidon-compliant &nbsp;|&nbsp;
            <span style="color:#10b981; font-weight:600;">{grade_a_count}</span> Grade-A routes
          </div>
          <div style="margin-top:14px; font-size:0.82rem; color:{C_TEXT3};">
            Sea freight emits ~98% less CO2 per tonne-km than air freight &mdash;
            the greenest long-distance transport mode available.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Section 1: Emissions leaderboard ─────────────────────────────────────
    section_header(
        "Emissions Leaderboard",
        "Routes ranked cleanest to most carbon-intensive — CO2 per TEU (metric tons)",
    )

    max_co2 = max(r.co2_per_teu_mt for r in route_results)

    cols_per_row = 2
    for row_start in range(0, len(route_results), cols_per_row):
        row_routes = route_results[row_start : row_start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, (rank_offset, route) in zip(cols, enumerate(row_routes)):
            rank = row_start + rank_offset + 1
            fraction = route.co2_per_teu_mt / max_co2 if max_co2 > 0 else 0
            grade_color = _GRADE_COLORS.get(route.sustainability_grade, C_TEXT2)
            with col:
                st.markdown(
                    f"""
                    <div class="ship-card" style="border-left:3px solid {grade_color};">
                      <div style="display:flex; justify-content:space-between;
                                  align-items:flex-start; margin-bottom:4px;">
                        <div>
                          <span style="color:{C_TEXT3}; font-size:0.72rem;
                                       font-weight:700;">#{rank}</span>
                          <span style="color:{C_TEXT}; font-size:0.88rem;
                                       font-weight:600; margin-left:8px;">
                            {route.route_name}
                          </span>
                        </div>
                        <div style="display:flex; gap:6px; align-items:center;">
                          {_grade_badge_html(route.sustainability_grade)}
                        </div>
                      </div>
                      <div style="display:flex; gap:20px; font-size:0.8rem;
                                  color:{C_TEXT2}; margin-bottom:2px;">
                        <span>&#x1F6A2; {route.distance_nm:,.0f} nm</span>
                        <span>&#x23F1; {route.transit_days}d</span>
                        <span style="color:{grade_color}; font-weight:700;">
                          {route.co2_per_teu_mt:.4f} MT CO2/TEU
                        </span>
                      </div>
                      {_bar_html(fraction)}
                      <div style="margin-top:8px; font-size:0.75rem; color:{C_TEXT3};">
                        {_poseidon_badge_html(route.poseidon_compliant)}
                        &nbsp;&nbsp;Carbon cost: ${route.carbon_cost_usd:,.0f}
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Section 2: Interactive route comparison ───────────────────────────────
    section_header(
        "Interactive Route Comparison",
        "Select routes to compare CO2/TEU intensity — vs air freight reference line",
    )

    route_names = [r.route_name for r in route_results]
    default_names = route_names[:6]

    selected_names = st.multiselect(
        "Choose routes to compare",
        options=route_names,
        default=default_names,
        key="sustainability_route_select",
    )

    if selected_names:
        selected = [r for r in route_results if r.route_name in selected_names]
        # Sort by CO2/TEU for the chart
        selected.sort(key=lambda r: r.co2_per_teu_mt)

        bar_colors = [_GRADE_COLORS.get(r.sustainability_grade, C_ACCENT) for r in selected]
        x_labels = [r.route_name for r in selected]
        y_values = [r.co2_per_teu_mt for r in selected]

        worst_selected = max(y_values) if y_values else 0.1
        air_freight_ref = worst_selected * 5.0  # air is ~50x total; show 5x within chart scale

        fig = go.Figure()

        # Route bars
        fig.add_trace(
            go.Bar(
                x=x_labels,
                y=y_values,
                marker_color=bar_colors,
                name="CO2 / TEU (MT)",
                text=[f"{v:.4f}" for v in y_values],
                textposition="outside",
                textfont={"color": C_TEXT2, "size": 10},
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "CO2/TEU: %{y:.4f} MT<br>"
                    "<extra></extra>"
                ),
            )
        )

        # Air freight reference bar (single grouped bar)
        fig.add_trace(
            go.Bar(
                x=["vs Air Freight (ref)"],
                y=[air_freight_ref],
                marker_color="rgba(239,68,68,0.4)",
                marker_line_color=C_LOW,
                marker_line_width=1.5,
                name="Air Freight reference (5x worst)",
                text=[f"{air_freight_ref:.4f}"],
                textposition="outside",
                textfont={"color": C_LOW, "size": 10},
                hovertemplate=(
                    "<b>Air Freight (reference)</b><br>"
                    "~5x highest selected route<br>"
                    "Sea freight emits ~50x less CO2<br>"
                    "<extra></extra>"
                ),
            )
        )

        apply_dark_layout(
            fig,
            title="CO2 per TEU Comparison (MT) — lower is greener",
            height=420,
        )
        fig.update_layout(
            barmode="group",
            xaxis_tickangle=-30,
            yaxis_title="MT CO2 per TEU",
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Select at least one route above to render the comparison chart.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Section 3: Carbon cost calculator ────────────────────────────────────
    section_header(
        "Carbon Cost Calculator",
        "Estimate total carbon footprint and offset costs for your TEU volume",
    )

    calc_col, pad_col = st.columns([1, 1])
    with calc_col:
        teu_volume = st.number_input(
            "Your TEU volume",
            min_value=1,
            max_value=100_000,
            value=100,
            step=50,
            key="sustainability_teu_volume",
            help="Number of TEUs you wish to calculate carbon cost for.",
        )

        route_options = [r.route_name for r in route_results]
        selected_calc_route_name = st.selectbox(
            "Select route",
            options=route_options,
            key="sustainability_calc_route",
        )

    selected_calc_route = next(
        (r for r in route_results if r.route_name == selected_calc_route_name),
        route_results[0],
    )
    alts = compare_to_alternatives(selected_calc_route)

    total_co2_mt = selected_calc_route.co2_per_teu_mt * teu_volume
    total_carbon_cost = total_co2_mt * 80.0  # EU ETS
    cost_per_teu = total_carbon_cost / max(teu_volume, 1)
    trees_needed = int(alts["trees_to_offset"] * teu_volume / max(selected_calc_route.teu_capacity * 0.85, 1))
    offset_cost = alts["carbon_offset_cost_usd"] * teu_volume / max(selected_calc_route.teu_capacity * 0.85, 1)

    grade = selected_calc_route.sustainability_grade
    grade_color = _GRADE_COLORS.get(grade, C_TEXT2)

    st.markdown(
        f"""
        <div style="background:#0d1526; border:1px solid rgba(16,185,129,0.2);
                    border-radius:14px; padding:24px 28px; margin-top:12px;
                    font-family:'Courier New', monospace;
                    box-shadow: 0 0 30px rgba(0,0,0,0.4);">
          <div style="color:#10b981; font-size:0.72rem; font-weight:700;
                      letter-spacing:0.1em; margin-bottom:16px;">
            &#x1F4CA; CARBON CALCULATOR &mdash; {selected_calc_route.route_name.upper()}
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
            <div style="background:rgba(16,185,129,0.06); border:1px solid rgba(16,185,129,0.12);
                        border-radius:8px; padding:14px;">
              <div style="color:{C_TEXT3}; font-size:0.7rem; text-transform:uppercase;
                           letter-spacing:0.06em;">Total CO2 Emitted</div>
              <div style="color:#10b981; font-size:1.6rem; font-weight:800;
                          margin-top:4px;">{total_co2_mt:.2f} MT</div>
              <div style="color:{C_TEXT3}; font-size:0.72rem;">for {teu_volume:,} TEU</div>
            </div>
            <div style="background:rgba(59,130,246,0.06); border:1px solid rgba(59,130,246,0.12);
                        border-radius:8px; padding:14px;">
              <div style="color:{C_TEXT3}; font-size:0.7rem; text-transform:uppercase;
                           letter-spacing:0.06em;">EU ETS Carbon Cost</div>
              <div style="color:#3b82f6; font-size:1.6rem; font-weight:800;
                          margin-top:4px;">${total_carbon_cost:,.0f}</div>
              <div style="color:{C_TEXT3}; font-size:0.72rem;">${cost_per_teu:.2f} / TEU</div>
            </div>
            <div style="background:rgba(245,158,11,0.06); border:1px solid rgba(245,158,11,0.12);
                        border-radius:8px; padding:14px;">
              <div style="color:{C_TEXT3}; font-size:0.7rem; text-transform:uppercase;
                           letter-spacing:0.06em;">Trees to Offset</div>
              <div style="color:#f59e0b; font-size:1.6rem; font-weight:800;
                          margin-top:4px;">&#x1F333; {trees_needed:,}</div>
              <div style="color:{C_TEXT3}; font-size:0.72rem;">over 20-year growth period</div>
            </div>
            <div style="background:rgba(139,92,246,0.06); border:1px solid rgba(139,92,246,0.12);
                        border-radius:8px; padding:14px;">
              <div style="color:{C_TEXT3}; font-size:0.7rem; text-transform:uppercase;
                           letter-spacing:0.06em;">Voluntary Offset Cost</div>
              <div style="color:#8b5cf6; font-size:1.6rem; font-weight:800;
                          margin-top:4px;">${offset_cost:,.0f}</div>
              <div style="color:{C_TEXT3}; font-size:0.72rem;">at $15/tonne (VCM)</div>
            </div>
          </div>
          <div style="margin-top:16px; display:flex; align-items:center; gap:12px;
                      font-size:0.8rem; color:{C_TEXT2};">
            <span>Sustainability grade:</span>
            {_grade_badge_html(grade)}
            <span style="color:{C_TEXT3};">
              EEDI score: {selected_calc_route.eedi_score:.1f}/100
            </span>
            <span style="color:{C_TEXT3};">
              {_poseidon_badge_html(selected_calc_route.poseidon_compliant)}
            </span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Section 4: Sustainability scatter plot ────────────────────────────────
    section_header(
        "Sustainability Rankings — Efficiency Frontier",
        "X = transit days  |  Y = CO2 per TEU  |  Bubble size = distance  |  Color = grade"
        "  — short + clean routes appear in the top-left corner",
    )

    scatter_x = [r.transit_days for r in route_results]
    scatter_y = [r.co2_per_teu_mt for r in route_results]
    bubble_size = [max(8, r.distance_nm / 200) for r in route_results]
    bubble_colors = [_GRADE_COLORS.get(r.sustainability_grade, C_ACCENT) for r in route_results]
    hover_texts = [
        (
            f"<b>{r.route_name}</b><br>"
            f"Grade: {r.sustainability_grade}<br>"
            f"CO2/TEU: {r.co2_per_teu_mt:.4f} MT<br>"
            f"Transit: {r.transit_days} days<br>"
            f"Distance: {r.distance_nm:,.0f} nm<br>"
            f"EEDI: {r.eedi_score:.1f}/100<br>"
            f"Poseidon: {'Yes' if r.poseidon_compliant else 'No'}"
        )
        for r in route_results
    ]

    scatter_fig = go.Figure()

    # Plot each grade as a separate trace for a useful legend
    for grade_label in ["A", "B", "C", "D"]:
        grade_routes = [r for r in route_results if r.sustainability_grade == grade_label]
        if not grade_routes:
            continue
        scatter_fig.add_trace(
            go.Scatter(
                x=[r.transit_days for r in grade_routes],
                y=[r.co2_per_teu_mt for r in grade_routes],
                mode="markers+text",
                name=f"Grade {grade_label}",
                text=[r.route_name.split(" ")[0] for r in grade_routes],
                textposition="top center",
                textfont={"size": 9, "color": C_TEXT3},
                marker=dict(
                    size=[max(10, r.distance_nm / 200) for r in grade_routes],
                    color=_GRADE_COLORS[grade_label],
                    opacity=0.82,
                    line=dict(width=1, color="rgba(255,255,255,0.15)"),
                ),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=[
                    (
                        f"<b>{r.route_name}</b><br>"
                        f"Grade: {r.sustainability_grade}<br>"
                        f"CO2/TEU: {r.co2_per_teu_mt:.4f} MT<br>"
                        f"Transit: {r.transit_days} days<br>"
                        f"Distance: {r.distance_nm:,.0f} nm<br>"
                        f"EEDI: {r.eedi_score:.1f}/100<br>"
                        f"Poseidon: {'Yes' if r.poseidon_compliant else 'No'}"
                    )
                    for r in grade_routes
                ],
            )
        )

    # Poseidon threshold line
    max_days = max(r.transit_days for r in route_results) + 2
    scatter_fig.add_shape(
        type="line",
        x0=0, x1=max_days,
        y0=0.12, y1=0.12,
        line=dict(color=C_MOD, dash="dot", width=1.5),
    )
    scatter_fig.add_annotation(
        x=max_days * 0.98,
        y=0.125,
        text="Poseidon 2050 limit (0.12)",
        showarrow=False,
        font=dict(color=C_MOD, size=10),
        xanchor="right",
    )

    apply_dark_layout(
        scatter_fig,
        title="Sustainability Efficiency Frontier — all routes",
        height=520,
    )
    scatter_fig.update_layout(
        xaxis_title="Transit Days",
        yaxis_title="CO2 per TEU (MT)",
    )

    st.plotly_chart(scatter_fig, use_container_width=True)

    # ── Footer insight ────────────────────────────────────────────────────────
    best = route_results[0]  # already sorted cleanest first
    worst = route_results[-1]
    st.markdown(
        f"""
        <div style="background:{_hex_to_rgba(C_HIGH, 0.06)}; border:1px solid {_hex_to_rgba(C_HIGH, 0.2)};
                    border-radius:10px; padding:14px 18px; margin-top:8px;
                    font-size:0.83rem; color:{C_TEXT2};">
          &#x1F4A1; <b style="color:{C_TEXT}">Key insight:</b>
          The cleanest route is <b style="color:{C_HIGH}">{best.route_name}</b>
          at <b>{best.co2_per_teu_mt:.4f} MT CO2/TEU</b> (Grade {best.sustainability_grade}),
          while the most carbon-intensive is
          <b style="color:{C_LOW}">{worst.route_name}</b>
          at <b>{worst.co2_per_teu_mt:.4f} MT CO2/TEU</b> (Grade {worst.sustainability_grade}).
          Even the highest-emitting sea route produces ~98% less CO2 per tonne-km than equivalent air freight.
        </div>
        """,
        unsafe_allow_html=True,
    )
